from __future__ import annotations

import logging
from datetime import datetime, timezone

from cultivation.constants import REALM_ORDER          # ← was: from db.cultivators import REALM_ORDER
from db.database import execute, fetch_one, fetch_all

log = logging.getLogger("bot.database.pvp")


# ---------------------------------------------------------------------------
# Pending challenges (in-DB so they survive restarts)
# ---------------------------------------------------------------------------

async def create_challenge(challenger_id: int, target_id: int, expires_at: datetime) -> None:
    """Store a pending Dao Challenge."""
    await execute(
        """
        INSERT INTO pending_challenges (challenger_id, target_id, expires_at)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE expires_at = VALUES(expires_at)
        """,
        (challenger_id, target_id, expires_at.replace(tzinfo=None)),
    )


async def get_challenge(challenger_id: int, target_id: int) -> dict | None:
    """Fetch an active (non-expired) pending challenge between two players."""
    return await fetch_one(
        """
        SELECT * FROM pending_challenges
        WHERE challenger_id = %s AND target_id = %s
          AND expires_at > %s AND accepted = FALSE
        """,
        (challenger_id, target_id, datetime.now(timezone.utc).replace(tzinfo=None)),
    )


async def get_incoming_challenge(target_id: int) -> dict | None:
    """Fetch any active challenge targeting this user."""
    return await fetch_one(
        """
        SELECT * FROM pending_challenges
        WHERE target_id = %s AND expires_at > %s AND accepted = FALSE
        ORDER BY issued_at DESC LIMIT 1
        """,
        (target_id, datetime.now(timezone.utc).replace(tzinfo=None)),
    )


async def accept_challenge(challenger_id: int, target_id: int) -> None:
    await execute(
        """
        UPDATE pending_challenges SET accepted = TRUE
        WHERE challenger_id = %s AND target_id = %s
        """,
        (challenger_id, target_id),
    )


async def delete_challenge(challenger_id: int, target_id: int) -> None:
    await execute(
        "DELETE FROM pending_challenges WHERE challenger_id = %s AND target_id = %s",
        (challenger_id, target_id),
    )


async def expire_old_challenges() -> None:
    """Clean up stale challenges. Call periodically."""
    await execute(
        "DELETE FROM pending_challenges WHERE expires_at <= %s AND accepted = FALSE",
        (datetime.now(timezone.utc).replace(tzinfo=None),),
    )


# ---------------------------------------------------------------------------
# Pending duels (both must consent)
# ---------------------------------------------------------------------------

async def create_duel_request(challenger_id: int, target_id: int, expires_at: datetime) -> None:
    await execute(
        """
        INSERT INTO pending_duels (challenger_id, target_id, expires_at)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE expires_at = VALUES(expires_at)
        """,
        (challenger_id, target_id, expires_at.replace(tzinfo=None)),
    )


async def get_duel_request(challenger_id: int, target_id: int) -> dict | None:
    return await fetch_one(
        """
        SELECT * FROM pending_duels
        WHERE challenger_id = %s AND target_id = %s
          AND expires_at > %s AND accepted = FALSE
        """,
        (challenger_id, target_id, datetime.now(timezone.utc).replace(tzinfo=None)),
    )


async def accept_duel(challenger_id: int, target_id: int) -> None:
    await execute(
        "UPDATE pending_duels SET accepted = TRUE WHERE challenger_id = %s AND target_id = %s",
        (challenger_id, target_id),
    )


async def delete_duel_request(challenger_id: int, target_id: int) -> None:
    await execute(
        "DELETE FROM pending_duels WHERE challenger_id = %s AND target_id = %s",
        (challenger_id, target_id),
    )


# ---------------------------------------------------------------------------
# Formation Ward
# ---------------------------------------------------------------------------

async def set_ward(discord_id: int, until: datetime) -> None:
    """Activate a formation ward until the given time."""
    await execute(
        "UPDATE cultivators SET ward_until = %s WHERE discord_id = %s",
        (until.replace(tzinfo=None), discord_id),
    )


async def clear_ward(discord_id: int) -> None:
    await execute(
        "UPDATE cultivators SET ward_until = NULL WHERE discord_id = %s",
        (discord_id,),
    )


async def has_active_ward(discord_id: int) -> bool:
    row = await fetch_one(
        "SELECT ward_until FROM cultivators WHERE discord_id = %s",
        (discord_id,),
    )
    if row is None or row["ward_until"] is None:
        return False
    ward_until = row["ward_until"]
    if ward_until.tzinfo is None:
        ward_until = ward_until.replace(tzinfo=timezone.utc)
    return ward_until > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Qi transfers
# ---------------------------------------------------------------------------

async def transfer_qi(winner_id: int, loser_id: int, amount: int) -> None:
    """Move `amount` Qi from loser to winner, clamped at 0."""
    await execute(
        "UPDATE cultivators SET qi = GREATEST(CAST(qi AS SIGNED) - %s, 0) WHERE discord_id = %s",
        (amount, loser_id),
    )
    await execute(
        "UPDATE cultivators SET qi = LEAST(qi + %s, qi_threshold) WHERE discord_id = %s",
        (amount, winner_id),
    )


async def apply_stage_loss(discord_id: int, row: dict) -> dict | None:
    """
    Regress cultivator by one stage (Life-and-Death Duel loser penalty).
    Returns updated row or None if already at floor (mortal stage 1).
    """
    realm = row["realm"]
    stage = row["stage"]

    if realm == "mortal" and stage <= 1:
        return None  # already at floor

    if stage > 1:
        new_stage = stage - 1
        new_realm = realm
    else:
        idx       = REALM_ORDER.index(realm)
        new_realm = REALM_ORDER[idx - 1]
        new_stage = 9

    from db.cultivators import _compute_threshold
    new_threshold = _compute_threshold(new_realm, new_stage)

    await execute(
        """
        UPDATE cultivators
        SET realm = %s, stage = %s, qi = 0, qi_threshold = %s
        WHERE discord_id = %s
        """,
        (new_realm, new_stage, new_threshold, discord_id),
    )

    from db.database import fetch_one as _fetch_one
    return await _fetch_one(
        "SELECT * FROM cultivators WHERE discord_id = %s", (discord_id,)
    )


async def apply_foundation_bonus(discord_id: int) -> None:
    """Permanent +5 Qi per tick bonus for Life-and-Death Duel winners."""
    await execute(
        "UPDATE cultivators SET foundation_bonus = foundation_bonus + 5 WHERE discord_id = %s",
        (discord_id,),
    )


# ---------------------------------------------------------------------------
# Crippled debuff (failed ambush attacker)
# ---------------------------------------------------------------------------

async def apply_crippled(discord_id: int, until: datetime) -> None:
    await execute(
        "UPDATE cultivators SET crippled_until = %s WHERE discord_id = %s",
        (until.replace(tzinfo=None), discord_id),
    )


async def is_crippled(discord_id: int) -> bool:
    row = await fetch_one(
        "SELECT crippled_until FROM cultivators WHERE discord_id = %s",
        (discord_id,),
    )
    if row is None or row["crippled_until"] is None:
        return False
    t = row["crippled_until"]
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Combat log
# ---------------------------------------------------------------------------

async def log_combat(
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
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (challenger_id, target_id, fight_type, outcome, qi_transferred, vendetta_active),
    )


# ---------------------------------------------------------------------------
# Reputation helpers
# ---------------------------------------------------------------------------

async def add_reputation(discord_id: int, amount: int) -> None:
    await execute(
        "UPDATE cultivators SET reputation = reputation + %s WHERE discord_id = %s",
        (amount, discord_id),
    )


async def record_fled(discord_id: int) -> None:
    await execute(
        """
        UPDATE cultivators
        SET reputation = reputation - 15, fled_challenges = fled_challenges + 1
        WHERE discord_id = %s
        """,
        (discord_id,),
    )


async def record_win(discord_id: int, rep_gain: int) -> None:
    await execute(
        """
        UPDATE cultivators
        SET reputation = reputation + %s, total_wins = total_wins + 1
        WHERE discord_id = %s
        """,
        (rep_gain, discord_id),
    )


async def record_loss(discord_id: int, rep_loss: int) -> None:
    await execute(
        """
        UPDATE cultivators
        SET reputation = reputation + %s, total_losses = total_losses + 1
        WHERE discord_id = %s
        """,
        (rep_loss, discord_id),
    )