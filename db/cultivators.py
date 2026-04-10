from __future__ import annotations

import logging
from datetime import datetime, timezone

from db.database import execute, fetch_one, fetch_all

log = logging.getLogger("bot.database.cultivators")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_naive() -> datetime:
    """UTC now as a naive datetime (for MySQL storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(dt: datetime) -> datetime:
    """Strip tzinfo for MySQL storage."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


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
            outcome         = VALUES(outcome)
        """,
        (
            discord_id,
            username,
            display_name,
            _naive(joined_at),
            _now_naive(),
            outcome,
            _now_naive(),   # stamp last_updated so accrual starts from registration
        ),
    )
    log.info("Cultivators » Upserted discord_id=%s outcome=%s", discord_id, outcome)


async def get_cultivator(discord_id: int) -> dict | None:
    """Fetch a single cultivator row by Discord ID."""
    return await fetch_one(
        "SELECT * FROM cultivators WHERE discord_id = %s",
        (discord_id,),
    )


async def has_passed(discord_id: int) -> bool:
    """Return True if the user has already passed the trial."""
    row = await fetch_one(
        "SELECT outcome FROM cultivators WHERE discord_id = %s AND outcome = 'pass'",
        (discord_id,),
    )
    return row is not None


# ---------------------------------------------------------------------------
# Affinity
# ---------------------------------------------------------------------------

async def set_affinity(discord_id: int, affinity: str) -> None:
    """
    Set elemental affinity for the first time.
    Works whether the stored value is NULL or 'water' (default).
    Also stamps last_updated so accrual begins from this moment at the
    correct affinity rate — avoids retroactively applying the new multiplier
    to time elapsed before the choice was made.
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
# Qi is NOT stored as a live counter.  The DB holds:
#   qi           — Qi value at the moment of the last flush
#   last_updated — UTC timestamp of that flush
#
# Current Qi at any moment is computed by cultivate.py as:
#   current = min(qi + rate * elapsed_seconds, qi_threshold)
#
# set_qi() is called whenever we need to persist the computed value
# (before any write that changes Qi, rate, or threshold).
# ---------------------------------------------------------------------------

async def set_qi(discord_id: int, qi: int, as_of: datetime) -> dict:
    """
    Persist a flushed Qi value and stamp last_updated.
    Returns the updated row.
    """
    await execute(
        """
        UPDATE cultivators
        SET qi           = %s,
            last_updated = %s
        WHERE discord_id = %s
        """,
        (qi, _naive(as_of), discord_id),
    )
    return await fetch_one("SELECT * FROM cultivators WHERE discord_id = %s", (discord_id,))


async def add_qi(discord_id: int, amount: int) -> dict:
    """
    Add a discrete Qi bonus on top of the already-flushed qi value
    (e.g. meditation burst).  Caps at qi_threshold.
    Stamps last_updated so the next accrual window starts clean.
    Returns the updated row.

    IMPORTANT: call _flush_qi() in the cog before calling this so that
    the stored qi is already current — otherwise the burst is added to a
    stale baseline.
    """
    await execute(
        """
        UPDATE cultivators
        SET qi           = LEAST(qi + %s, qi_threshold),
            last_updated = %s
        WHERE discord_id = %s
        """,
        (amount, _now_naive(), discord_id),
    )
    return await fetch_one("SELECT * FROM cultivators WHERE discord_id = %s", (discord_id,))


# ---------------------------------------------------------------------------
# Closed Cultivation
# ---------------------------------------------------------------------------

async def set_closed_cultivation(discord_id: int, until: datetime) -> None:
    await execute(
        "UPDATE cultivators SET closed_cult_until = %s WHERE discord_id = %s",
        (_naive(until), discord_id),
    )


async def clear_closed_cultivation(discord_id: int) -> None:
    await execute(
        """
        UPDATE cultivators
        SET closed_cult_until = NULL,
            last_updated      = %s
        WHERE discord_id = %s
        """,
        (_now_naive(), discord_id),
    )


# ---------------------------------------------------------------------------
# Tribulation State
# ---------------------------------------------------------------------------

async def enter_tribulation(discord_id: int) -> None:
    """Mark cultivator as in tribulation state."""
    await execute(
        """
        UPDATE cultivators
        SET in_tribulation        = TRUE,
            tribulation_started_at = %s
        WHERE discord_id = %s
        """,
        (_now_naive(), discord_id),
    )


async def exit_tribulation(discord_id: int) -> None:
    """Clear tribulation state."""
    await execute(
        """
        UPDATE cultivators
        SET in_tribulation         = FALSE,
            tribulation_started_at = NULL
        WHERE discord_id = %s
        """,
        (discord_id,),
    )


async def set_breakthrough_cooldown(discord_id: int, until: datetime) -> None:
    await execute(
        "UPDATE cultivators SET breakthrough_cooldown = %s WHERE discord_id = %s",
        (_naive(until), discord_id),
    )


# ---------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------

REALM_ORDER = ["mortal", "qi_gathering", "qi_condensation", "qi_refining"]


async def advance_stage(discord_id: int, row: dict) -> dict:
    """
    Advance cultivator by one stage. Handles realm transitions.
    Resets qi to 0 and stamps last_updated so accrual restarts cleanly.
    Returns updated row.
    """
    realm = row["realm"]
    stage = row["stage"]

    if stage < 9:
        new_stage = stage + 1
        new_realm = realm
    else:
        current_index = REALM_ORDER.index(realm)
        if current_index + 1 >= len(REALM_ORDER):
            return row
        new_realm = REALM_ORDER[current_index + 1]
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
    return await fetch_one("SELECT * FROM cultivators WHERE discord_id = %s", (discord_id,))


async def apply_qi_loss(discord_id: int, percent: float) -> None:
    """
    Deduct a percentage of current Qi after a failed breakthrough.
    Stamps last_updated so accrual resumes from the reduced value.
    """
    await execute(
        """
        UPDATE cultivators
        SET qi           = GREATEST(FLOOR(qi * %s), 0),
            last_updated = %s
        WHERE discord_id = %s
        """,
        (1.0 - percent, _now_naive(), discord_id),
    )


# ---------------------------------------------------------------------------
# Stabilise
# ---------------------------------------------------------------------------

async def use_stabilise(discord_id: int) -> None:
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
    outcome: str,
    qi_lost: int = 0,
    overflow: bool = False,
) -> None:
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
# ---------------------------------------------------------------------------

async def get_cooldown(discord_id: int, command: str) -> datetime | None:
    """Return cooldown expiry or None if not on cooldown."""
    row = await fetch_one(
        "SELECT expires_at FROM cooldowns WHERE discord_id = %s AND command = %s",
        (discord_id, command),
    )
    if row is None:
        return None
    expires = row["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires


async def set_cooldown(discord_id: int, command: str, until: datetime) -> None:
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
    won: bool,
    rep_change: int,
    fled: bool = False,
) -> None:
    if fled:
        await execute(
            """
            UPDATE cultivators
            SET reputation = reputation + %s, fled_challenges = fled_challenges + 1
            WHERE discord_id = %s
            """,
            (rep_change, discord_id),
        )
    elif won:
        await execute(
            """
            UPDATE cultivators
            SET reputation = reputation + %s, total_wins = total_wins + 1
            WHERE discord_id = %s
            """,
            (rep_change, discord_id),
        )
    else:
        await execute(
            """
            UPDATE cultivators
            SET reputation = reputation + %s, total_losses = total_losses + 1
            WHERE discord_id = %s
            """,
            (rep_change, discord_id),
        )


async def log_rival(
    challenger_id: int,
    target_id: int,
    fight_type: str,
    outcome: str,
    qi_transferred: int = 0,
    vendetta_active: bool = False,
) -> None:
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
    """Check if challenger has an active vendetta against target."""
    return await fetch_one(
        """
        SELECT * FROM rivals
        WHERE challenger_id = %s AND target_id = %s
          AND vendetta_active = TRUE
        ORDER BY fought_at DESC LIMIT 1
        """,
        (challenger_id, target_id),
    )


async def clear_vendetta(challenger_id: int, target_id: int) -> None:
    await execute(
        """
        UPDATE rivals SET vendetta_active = FALSE
        WHERE challenger_id = %s AND target_id = %s
        """,
        (challenger_id, target_id),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_threshold(realm: str, stage: int) -> int:
    """
    Qi threshold needed to trigger tribulation.
    Scales with realm and stage so later stages require more passive farming.
    """
    base = {
        "mortal":          100,
        "qi_gathering":    300,
        "qi_condensation": 700,
        "qi_refining":     1500,
    }
    return base[realm] + (stage - 1) * 50