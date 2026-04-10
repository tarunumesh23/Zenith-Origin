from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from db.database import execute, fetch_one, fetch_all

log = logging.getLogger("bot.database.cultivators")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_naive() -> datetime:
    """UTC now as a naive datetime (for MySQL DATETIME storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(dt: datetime) -> datetime:
    """Strip tzinfo for MySQL DATETIME storage."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


async def _refetch(discord_id: int, caller: str) -> dict:
    """
    Re-fetch a cultivator row after a write.  Raises RuntimeError if the row
    has disappeared (should never happen in normal operation, but beats a
    silent None propagating through the call stack).
    """
    row = await fetch_one(
        "SELECT * FROM cultivators WHERE discord_id = %s",
        (discord_id,),
    )
    if row is None:
        raise RuntimeError(f"{caller}: cultivator row vanished for discord_id={discord_id}")
    return row


# ---------------------------------------------------------------------------
# Realm / stage constants
# ---------------------------------------------------------------------------

REALM_ORDER: list[str] = ["mortal", "qi_gathering", "qi_condensation", "qi_refining"]

_REALM_BASE_THRESHOLD: dict[str, int] = {
    "mortal":          100,
    "qi_gathering":    300,
    "qi_condensation": 700,
    "qi_refining":     1500,
}


def _compute_threshold(realm: str, stage: int) -> int:
    """
    Qi threshold needed to trigger tribulation.
    Scales with realm and stage so later stages require more passive farming.
    """
    try:
        base = _REALM_BASE_THRESHOLD[realm]
    except KeyError:
        raise ValueError(f"Unknown realm: {realm!r}")
    return base + (stage - 1) * 50


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def upsert_cultivator(
    discord_id: int,
    username: str,
    display_name: str,
    joined_at: datetime,
    outcome: str,
) -> None:
    """Insert or update a cultivator record on trial completion."""
    now = _now_naive()
    await execute(
        """
        INSERT INTO cultivators
            (discord_id, username, display_name, joined_at, registered_at, outcome,
             last_updated)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            username        = VALUES(username),
            display_name    = VALUES(display_name),
            registered_at   = VALUES(registered_at),
            outcome         = VALUES(outcome),
            last_updated    = VALUES(last_updated)
        """,
        (discord_id, username, display_name, _naive(joined_at), now, outcome, now),
    )
    log.info("Cultivators » upserted discord_id=%s outcome=%s", discord_id, outcome)


async def get_cultivator(discord_id: int) -> dict | None:
    """Fetch a single cultivator row by Discord ID, or None if unregistered."""
    return await fetch_one(
        "SELECT * FROM cultivators WHERE discord_id = %s",
        (discord_id,),
    )


async def has_passed(discord_id: int) -> bool:
    """Return True if the user has already passed the trial."""
    row = await fetch_one(
        "SELECT 1 FROM cultivators WHERE discord_id = %s AND outcome = 'pass'",
        (discord_id,),
    )
    return row is not None


# ---------------------------------------------------------------------------
# Affinity
# ---------------------------------------------------------------------------

async def set_affinity(discord_id: int, affinity: str) -> None:
    """
    Permanently set elemental affinity (only allowed once — no-op if already set
    to something other than NULL or the legacy 'water' default).

    Stamps last_updated so accrual restarts at the correct new multiplier from
    this moment forward, avoiding retroactive application of the new rate.
    """
    await execute(
        """
        UPDATE cultivators
        SET affinity     = %s,
            last_updated = %s
        WHERE discord_id = %s
          AND (affinity IS NULL OR affinity = 'water')
        """,
        (affinity, _now_naive(), discord_id),
    )


# ---------------------------------------------------------------------------
# Qi — real-time accrual model
#
# The DB holds a *snapshot*:
#   qi           — Qi value at the moment of the last flush
#   last_updated — UTC timestamp of that flush (naive, stored as UTC)
#
# Live Qi at any moment T is computed purely in Python:
#   current = min(qi + rate_per_second * (T - last_updated).seconds, qi_threshold)
#
# All write helpers below accept a caller-supplied `now` so that the timestamp
# used in business logic and the timestamp written to the DB are identical —
# no drift between two calls to datetime.now().
# ---------------------------------------------------------------------------

async def set_qi(discord_id: int, qi: int, as_of: datetime) -> dict:
    """
    Persist a flushed Qi value and stamp last_updated.

    `as_of` must be the same `now` used to compute `qi` in the calling code so
    that the stored snapshot is internally consistent.

    Returns the updated row (single round-trip: UPDATE … RETURNING is not
    available in MySQL <8.0, so we do one extra SELECT — unavoidable).
    """
    if qi < 0:
        raise ValueError(f"set_qi: qi must be >= 0, got {qi}")

    await execute(
        """
        UPDATE cultivators
        SET qi           = %s,
            last_updated = %s
        WHERE discord_id = %s
        """,
        (qi, _naive(as_of), discord_id),
    )
    return await _refetch(discord_id, "set_qi")


async def add_qi(discord_id: int, amount: int, now: datetime | None = None) -> dict:
    """
    Add a discrete Qi bonus (e.g. meditation burst) on top of the already-flushed
    stored value, capped at qi_threshold.

    IMPORTANT: the caller must have called _flush_qi() immediately before this so
    the stored baseline is current; otherwise the bonus is applied to a stale value.

    Stamps last_updated to `now` (defaults to current UTC) so the next accrual
    window starts cleanly from zero elapsed time.

    Returns the updated row.
    """
    if amount < 0:
        raise ValueError(f"add_qi: amount must be >= 0, got {amount}")

    ts = _naive(now) if now else _now_naive()
    await execute(
        """
        UPDATE cultivators
        SET qi           = LEAST(qi + %s, qi_threshold),
            last_updated = %s
        WHERE discord_id = %s
        """,
        (amount, ts, discord_id),
    )
    return await _refetch(discord_id, "add_qi")


# ---------------------------------------------------------------------------
# Closed cultivation
# ---------------------------------------------------------------------------

async def set_closed_cultivation(discord_id: int, until: datetime) -> None:
    """Begin a closed-cultivation window ending at `until`."""
    await execute(
        "UPDATE cultivators SET closed_cult_until = %s WHERE discord_id = %s",
        (_naive(until), discord_id),
    )


async def clear_closed_cultivation(discord_id: int, now: datetime | None = None) -> None:
    """
    Cancel closed cultivation and stamp last_updated so accrual resumes at the
    normal (non-boosted) rate from this exact moment.
    """
    ts = _naive(now) if now else _now_naive()
    await execute(
        """
        UPDATE cultivators
        SET closed_cult_until = NULL,
            last_updated      = %s
        WHERE discord_id = %s
        """,
        (ts, discord_id),
    )


# ---------------------------------------------------------------------------
# Tribulation
# ---------------------------------------------------------------------------

async def enter_tribulation(discord_id: int, now: datetime | None = None) -> None:
    """Mark the cultivator as pending tribulation."""
    ts = _naive(now) if now else _now_naive()
    await execute(
        """
        UPDATE cultivators
        SET in_tribulation         = TRUE,
            tribulation_started_at = %s
        WHERE discord_id = %s
        """,
        (ts, discord_id),
    )


async def exit_tribulation(discord_id: int) -> None:
    """Clear tribulation state after a breakthrough attempt (win or loss)."""
    await execute(
        """
        UPDATE cultivators
        SET in_tribulation         = FALSE,
            tribulation_started_at = NULL
        WHERE discord_id = %s
        """,
        (discord_id,),
    )


# ---------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------

async def advance_stage(discord_id: int, row: dict) -> dict:
    """
    Advance the cultivator by exactly one stage, handling realm transitions.

    - Resets qi to 0 and stamps last_updated so accrual restarts cleanly.
    - If already at the highest realm/stage, returns the row unchanged (no DB write).
    - Returns the updated row.
    """
    realm = row["realm"]
    stage = row["stage"]

    if stage < 9:
        new_stage = stage + 1
        new_realm = realm
    else:
        idx = REALM_ORDER.index(realm)
        if idx + 1 >= len(REALM_ORDER):
            # Already at the ceiling — nothing to do.
            return row
        new_realm = REALM_ORDER[idx + 1]
        new_stage = 1

    new_threshold = _compute_threshold(new_realm, new_stage)

    await execute(
        """
        UPDATE cultivators
        SET realm          = %s,
            stage          = %s,
            qi             = 0,
            qi_threshold   = %s,
            stabilise_used = FALSE,
            last_updated   = %s
        WHERE discord_id = %s
        """,
        (new_realm, new_stage, new_threshold, _now_naive(), discord_id),
    )
    return await _refetch(discord_id, "advance_stage")


async def apply_qi_loss(discord_id: int, percent: float, now: datetime | None = None) -> dict:
    """
    Deduct `percent` of the current stored Qi after a failed breakthrough.
    Stamps last_updated so accrual resumes from the reduced value.

    `percent` is the *fraction to remove* — e.g. 0.25 removes 25 %.

    Returns the updated row (callers need the new qi value to build result embeds).
    """
    if not 0.0 <= percent <= 1.0:
        raise ValueError(f"apply_qi_loss: percent must be in [0, 1], got {percent}")

    ts = _naive(now) if now else _now_naive()
    await execute(
        """
        UPDATE cultivators
        SET qi           = GREATEST(FLOOR(qi * %s), 0),
            last_updated = %s
        WHERE discord_id = %s
        """,
        (1.0 - percent, ts, discord_id),
    )
    return await _refetch(discord_id, "apply_qi_loss")


# ---------------------------------------------------------------------------
# Stabilise
# ---------------------------------------------------------------------------

async def use_stabilise(discord_id: int) -> None:
    """Consume the one-per-realm stabilise bonus."""
    await execute(
        "UPDATE cultivators SET stabilise_used = TRUE WHERE discord_id = %s",
        (discord_id,),
    )


# ---------------------------------------------------------------------------
# Breakthrough log
# ---------------------------------------------------------------------------

async def log_breakthrough(
    discord_id: int,
    realm: str,
    stage: int,
    outcome: Literal["success", "fail"],
    *,
    qi_lost: int = 0,
    overflow: bool = False,
) -> None:
    """
    Append an immutable breakthrough event to the audit log.

    `qi_lost` and `overflow` are keyword-only to prevent accidental
    positional mis-ordering at call sites.
    """
    await execute(
        """
        INSERT INTO breakthrough_log
            (discord_id, realm, stage, outcome, qi_lost, overflow)
        VALUES
            (%s, %s, %s, %s, %s, %s)
        """,
        (discord_id, realm, stage, outcome, qi_lost, overflow),
    )


# ---------------------------------------------------------------------------
# Cooldowns
#
# Single source of truth for all command cooldowns, including breakthroughs.
# The old set_breakthrough_cooldown() helper is removed — callers use
# set_cooldown(discord_id, "breakthrough", until) directly.
# ---------------------------------------------------------------------------

async def get_cooldown(discord_id: int, command: str) -> datetime | None:
    """
    Return the cooldown expiry as a UTC-aware datetime, or None if not on
    cooldown / cooldown has already expired.
    """
    row = await fetch_one(
        "SELECT expires_at FROM cooldowns WHERE discord_id = %s AND command = %s",
        (discord_id, command),
    )
    if row is None:
        return None
    expires: datetime = row["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires


async def set_cooldown(discord_id: int, command: str, until: datetime) -> None:
    """
    Upsert a cooldown record.  Any command name is valid — no separate
    per-command helpers needed.
    """
    await execute(
        """
        INSERT INTO cooldowns (discord_id, command, expires_at)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE expires_at = VALUES(expires_at)
        """,
        (discord_id, command, _naive(until)),
    )


# ---------------------------------------------------------------------------
# PvP
# ---------------------------------------------------------------------------

async def update_pvp_stats(
    discord_id: int,
    *,
    won: bool,
    rep_change: int,
    fled: bool = False,
) -> None:
    """
    Atomically update reputation and win/loss/flee counters.

    All parameters after `discord_id` are keyword-only to prevent silent
    argument transposition at call sites.
    """
    if fled:
        await execute(
            """
            UPDATE cultivators
            SET reputation      = reputation + %s,
                fled_challenges = fled_challenges + 1
            WHERE discord_id = %s
            """,
            (rep_change, discord_id),
        )
    elif won:
        await execute(
            """
            UPDATE cultivators
            SET reputation = reputation + %s,
                total_wins = total_wins + 1
            WHERE discord_id = %s
            """,
            (rep_change, discord_id),
        )
    else:
        await execute(
            """
            UPDATE cultivators
            SET reputation   = reputation + %s,
                total_losses = total_losses + 1
            WHERE discord_id = %s
            """,
            (rep_change, discord_id),
        )


async def log_rival(
    challenger_id: int,
    target_id: int,
    fight_type: str,
    outcome: str,
    *,
    qi_transferred: int = 0,
    vendetta_active: bool = False,
) -> None:
    """
    Append a PvP encounter to the rivals log.

    `qi_transferred` and `vendetta_active` are keyword-only to prevent
    accidental positional mis-ordering.
    """
    await execute(
        """
        INSERT INTO rivals
            (challenger_id, target_id, fight_type, outcome, qi_transferred, vendetta_active)
        VALUES
            (%s, %s, %s, %s, %s, %s)
        """,
        (challenger_id, target_id, fight_type, outcome, qi_transferred, vendetta_active),
    )


async def get_vendetta(challenger_id: int, target_id: int) -> dict | None:
    """Return the most recent active vendetta record, or None."""
    return await fetch_one(
        """
        SELECT * FROM rivals
        WHERE challenger_id = %s
          AND target_id     = %s
          AND vendetta_active = TRUE
        ORDER BY fought_at DESC
        LIMIT 1
        """,
        (challenger_id, target_id),
    )


async def clear_vendetta(challenger_id: int, target_id: int) -> None:
    """Mark all vendettas between these two participants as resolved."""
    await execute(
        """
        UPDATE rivals
        SET vendetta_active = FALSE
        WHERE challenger_id = %s
          AND target_id     = %s
        """,
        (challenger_id, target_id),
    )