"""
db/training.py
~~~~~~~~~~~~~~
All MySQL queries for the Training system.

Tables
------
training_stats    — one row per player, all six stat values + mastery + tiers
training_sessions — audit log + cooldown + streak tracking
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from db.database import execute, fetch_all, fetch_one

log = logging.getLogger("bot.db.training")


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class TrainingStatsRecord:
    discord_id:      int
    guild_id:        int

    atk:             float
    def_:            float
    spe:             float
    eva:             float
    crit_chance:     float
    crit_dmg:        float

    mastery_body:    int
    mastery_flow:    int
    mastery_killing: int

    tier_body:       str
    tier_flow:       str
    tier_killing:    str

    fatigue:         float
    deviation_streak: int
    cascade_lock:    int

    injury_body_remaining:    int
    injury_flow_remaining:    int
    injury_killing_remaining: int

    passive_tags:    list[str]

    last_path_trained:         Optional[str]
    consecutive_path_sessions: int

    created_at:   datetime
    last_updated: datetime


@dataclass
class TrainingSessionRecord:
    id:              int
    discord_id:      int
    guild_id:        int
    path:            str
    tier:            str
    stats_gained:    dict
    mastery_gained:  int
    risk_event_type: Optional[str]
    overtraining:    bool
    trained_at:      datetime


# ---------------------------------------------------------------------------
# Initialise / read
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_training_stats(discord_id: int, guild_id: int) -> TrainingStatsRecord | None:
    row = await fetch_one(
        "SELECT * FROM training_stats WHERE discord_id = %s AND guild_id = %s",
        (discord_id, guild_id),
    )
    return _row_to_stats(row) if row else None


async def create_training_stats(discord_id: int, guild_id: int) -> TrainingStatsRecord:
    """Insert a fresh all-zero stats row for a new player."""
    now = _now()
    await execute(
        """
        INSERT INTO training_stats (
            discord_id, guild_id,
            atk, def_, spe, eva, crit_chance, crit_dmg,
            mastery_body, mastery_flow, mastery_killing,
            tier_body, tier_flow, tier_killing,
            fatigue, deviation_streak, cascade_lock,
            injury_body_remaining, injury_flow_remaining, injury_killing_remaining,
            passive_tags,
            last_path_trained, consecutive_path_sessions,
            created_at, last_updated
        ) VALUES (
            %s, %s,
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            0, 0, 0,
            'beginner', 'beginner', 'beginner',
            0.0, 0, 0,
            0, 0, 0,
            '[]',
            NULL, 0,
            %s, %s
        )
        """,
        (discord_id, guild_id, now, now),
    )
    record = await get_training_stats(discord_id, guild_id)
    assert record is not None
    return record


async def get_or_create_training_stats(discord_id: int, guild_id: int) -> TrainingStatsRecord:
    record = await get_training_stats(discord_id, guild_id)
    if record is None:
        record = await create_training_stats(discord_id, guild_id)
    return record


# ---------------------------------------------------------------------------
# Apply session result
# ---------------------------------------------------------------------------

async def apply_session_result(
    discord_id:  int,
    guild_id:    int,
    path:        str,
    tier:        str,
    stats_delta: dict[str, float],
    mastery_gained: int,
    new_tier:    Optional[str],
    fatigue_after: float,
    risk_event_type: Optional[str],
    path_locked: Optional[str],
    lock_sessions: int,
    deviation_cascade: bool,
    mutation_tag: Optional[str],
    overtraining: bool,
    consecutive: int,
) -> TrainingStatsRecord:
    """
    Atomically apply all changes from a resolved training session.
    Returns the updated record.
    """
    now = _now()

    # Map stat names to column names
    _STAT_COLS: dict[str, str] = {
        "atk":         "atk",
        "def":         "def_",
        "def_":        "def_",
        "spe":         "spe",
        "eva":         "eva",
        "crit_chance": "crit_chance",
        "crit_dmg":    "crit_dmg",
    }

    # Build SET fragments for stat updates (clamped at 0)
    stat_fragments = []
    stat_args = []
    for raw_stat, delta in stats_delta.items():
        col = _STAT_COLS.get(raw_stat)
        if col and delta != 0:
            stat_fragments.append(f"{col} = GREATEST(0, {col} + %s)")
            stat_args.append(delta)

    # Mastery column for this path
    mastery_col = f"mastery_{path.split('_')[0]}"
    # Tier column (only updated if advancing)
    tier_col = f"tier_{path.split('_')[0]}"
    # Injury column for locked path
    injury_col = f"injury_{(path_locked or path).split('_')[0]}_remaining"

    # Cascade lock
    cascade_val = 5 if deviation_cascade else None

    # Build full SET clause
    set_parts = stat_fragments + [
        f"{mastery_col} = {mastery_col} + %s",
        "fatigue = LEAST(10.0, fatigue + %s)",
        "last_path_trained = %s",
        "consecutive_path_sessions = %s",
        "last_updated = %s",
    ]
    set_args = stat_args + [mastery_gained, 1.0, path, consecutive, now]

    if new_tier:
        set_parts.append(f"{tier_col} = %s")
        set_args.append(new_tier)

    if path_locked:
        set_parts.append(f"{injury_col} = %s")
        set_args.append(lock_sessions)

    if cascade_val is not None:
        set_parts.append("cascade_lock = %s")
        set_args.append(cascade_val)
        set_parts.append("deviation_streak = 0")

    elif risk_event_type == "qi_deviation":
        set_parts.append("deviation_streak = deviation_streak + 1")
    else:
        set_parts.append("deviation_streak = 0")

    if mutation_tag:
        # Append tag to JSON array
        set_parts.append("passive_tags = JSON_ARRAY_APPEND(passive_tags, '$', %s)")
        set_args.append(mutation_tag)

    where_args = [discord_id, guild_id]
    query = f"UPDATE training_stats SET {', '.join(set_parts)} WHERE discord_id = %s AND guild_id = %s"
    await execute(query, set_args + where_args)

    # Tick down injury counters (only where > 0, skipping the one we just set)
    for p in ("body_tempering", "flow_arts", "killing_sense"):
        if p == path_locked:
            continue   # just set, don't immediately decrement
        col = f"injury_{p.split('_')[0]}_remaining"
        await execute(
            f"UPDATE training_stats SET {col} = GREATEST(0, {col} - 1) WHERE discord_id = %s AND guild_id = %s AND {col} > 0",
            (discord_id, guild_id),
        )

    # Tick down cascade lock if no new cascade
    if cascade_val is None:
        await execute(
            "UPDATE training_stats SET cascade_lock = GREATEST(0, cascade_lock - 1) WHERE discord_id = %s AND guild_id = %s AND cascade_lock > 0",
            (discord_id, guild_id),
        )

    record = await get_training_stats(discord_id, guild_id)
    assert record is not None
    return record


# ---------------------------------------------------------------------------
# Fatigue decay  (call on every /train invocation before the session)
# ---------------------------------------------------------------------------

async def decay_fatigue(discord_id: int, guild_id: int, hours_elapsed: float) -> None:
    """Reduce fatigue passively based on time elapsed since last session."""
    from training.constants import FATIGUE_DECAY_PER_HOUR
    decay = hours_elapsed * FATIGUE_DECAY_PER_HOUR
    await execute(
        "UPDATE training_stats SET fatigue = GREATEST(0.0, fatigue - %s) WHERE discord_id = %s AND guild_id = %s",
        (decay, discord_id, guild_id),
    )


# ---------------------------------------------------------------------------
# Cooldown helpers  (reuses shared cooldowns table)
# ---------------------------------------------------------------------------

def _cooldown_key(path: str) -> str:
    return f"training_{path}"


async def get_training_cooldown(discord_id: int, path: str) -> datetime | None:
    row = await fetch_one(
        """
        SELECT expires_at FROM cooldowns
        WHERE discord_id = %s AND command = %s AND expires_at > NOW()
        """,
        (discord_id, _cooldown_key(path)),
    )
    return row["expires_at"] if row else None


async def set_training_cooldown(discord_id: int, path: str, seconds: int) -> None:
    await execute(
        """
        INSERT INTO cooldowns (discord_id, command, expires_at)
        VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s SECOND))
        AS nr ON DUPLICATE KEY UPDATE expires_at = nr.expires_at
        """,
        (discord_id, _cooldown_key(path), seconds),
    )


async def clear_training_cooldown(discord_id: int, path: str) -> None:
    await execute(
        "DELETE FROM cooldowns WHERE discord_id = %s AND command = %s",
        (discord_id, _cooldown_key(path)),
    )


# ---------------------------------------------------------------------------
# Session audit log
# ---------------------------------------------------------------------------

async def log_session(
    discord_id:      int,
    guild_id:        int,
    path:            str,
    tier:            str,
    stats_gained:    dict,
    mastery_gained:  int,
    risk_event_type: Optional[str],
    overtraining:    bool,
) -> None:
    await execute(
        """
        INSERT INTO training_sessions
            (discord_id, guild_id, path, tier, stats_gained, mastery_gained,
             risk_event_type, overtraining, trained_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (
            discord_id, guild_id, path, tier,
            json.dumps(stats_gained), mastery_gained,
            risk_event_type, int(overtraining),
        ),
    )


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

async def get_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    return await fetch_all(
        """
        SELECT ts.discord_id,
               ts.atk, ts.def_, ts.spe, ts.eva, ts.crit_chance, ts.crit_dmg,
               ts.tier_body, ts.tier_flow, ts.tier_killing,
               c.display_name,
               (ts.atk + ts.def_ + ts.spe + ts.eva + ts.crit_chance + ts.crit_dmg) AS total_power
        FROM   training_stats ts
        JOIN   cultivators c ON c.discord_id = ts.discord_id
        WHERE  ts.guild_id = %s
        ORDER  BY total_power DESC
        LIMIT  %s
        """,
        (guild_id, limit),
    )


# ---------------------------------------------------------------------------
# Internal mappers
# ---------------------------------------------------------------------------

def _row_to_stats(row: dict) -> TrainingStatsRecord:
    tags_raw = row.get("passive_tags") or "[]"
    if isinstance(tags_raw, str):
        try:
            tags = json.loads(tags_raw)
        except Exception:
            tags = []
    else:
        tags = tags_raw or []

    return TrainingStatsRecord(
        discord_id=row["discord_id"],
        guild_id=row["guild_id"],
        atk=float(row["atk"]),
        def_=float(row["def_"]),
        spe=float(row["spe"]),
        eva=float(row["eva"]),
        crit_chance=float(row["crit_chance"]),
        crit_dmg=float(row["crit_dmg"]),
        mastery_body=int(row["mastery_body"]),
        mastery_flow=int(row["mastery_flow"]),
        mastery_killing=int(row["mastery_killing"]),
        tier_body=row["tier_body"],
        tier_flow=row["tier_flow"],
        tier_killing=row["tier_killing"],
        fatigue=float(row["fatigue"]),
        deviation_streak=int(row["deviation_streak"]),
        cascade_lock=int(row["cascade_lock"]),
        injury_body_remaining=int(row["injury_body_remaining"]),
        injury_flow_remaining=int(row["injury_flow_remaining"]),
        injury_killing_remaining=int(row["injury_killing_remaining"]),
        passive_tags=tags,
        last_path_trained=row.get("last_path_trained"),
        consecutive_path_sessions=int(row["consecutive_path_sessions"]),
        created_at=row["created_at"],
        last_updated=row["last_updated"],
    )