"""
db/spirit_roots.py
~~~~~~~~~~~~~~~~~~
All MySQL queries for the Spirit Root system.

Changes in this revision
────────────────────────
• Fixed ``ON DUPLICATE KEY UPDATE ... VALUES(col)`` deprecation warning in
  ``set_spin_cooldown``.  Now uses the row alias pattern.
• Added ``clear_spin_cooldown`` which DELETE-s the row outright (used by
  AdminSpiritRoots.grant_spin instead of the old 1-second TTL hack).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from db.database import execute, fetch_all, fetch_one

log = logging.getLogger("bot.db.spirit_roots")


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class SpiritRootRecord:
    discord_id:    int
    guild_id:      int
    current_value: int               # 1–5
    best_value:    int               # 1–5, never decreases
    pity_counter:  int               # increments on non-improving spins
    total_spins:   int
    acquired_at:   datetime
    last_spin_at:  Optional[datetime]


@dataclass
class SpinLogEntry:
    id:             int
    discord_id:     int
    guild_id:       int
    rolled_value:   int
    pity_triggered: bool
    outcome:        str              # 'improved' | 'equal' | 'protected'
    spun_at:        datetime


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

async def get_spirit_root(
    discord_id: int,
    guild_id:   int,
) -> SpiritRootRecord | None:
    """Return the player's spirit root record, or None if not yet initialised."""
    row = await fetch_one(
        """
        SELECT discord_id, guild_id,
               current_value, best_value,
               pity_counter,  total_spins,
               acquired_at,   last_spin_at
        FROM   spirit_roots
        WHERE  discord_id = %s
          AND  guild_id   = %s
        """,
        (discord_id, guild_id),
    )
    return _row_to_record(row) if row else None


async def get_spin_history(
    discord_id: int,
    guild_id:   int,
    limit:      int = 10,
) -> list[SpinLogEntry]:
    """Return the *limit* most recent spin log entries for a player."""
    rows = await fetch_all(
        """
        SELECT id, discord_id, guild_id,
               rolled_value, pity_triggered, outcome, spun_at
        FROM   spirit_root_spin_log
        WHERE  discord_id = %s AND guild_id = %s
        ORDER  BY spun_at DESC
        LIMIT  %s
        """,
        (discord_id, guild_id, limit),
    )
    return [_row_to_log(r) for r in rows]


async def get_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    """Return top *limit* players ranked by best_value DESC, total_spins ASC."""
    return await fetch_all(
        """
        SELECT sr.discord_id,
               sr.current_value,
               sr.best_value,
               sr.total_spins,
               sr.last_spin_at,
               c.display_name
        FROM   spirit_roots sr
        JOIN   cultivators  c  ON c.discord_id = sr.discord_id
        WHERE  sr.guild_id = %s
        ORDER  BY sr.best_value   DESC,
                  sr.total_spins  ASC
        LIMIT  %s
        """,
        (guild_id, limit),
    )


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

async def create_spirit_root(
    discord_id: int,
    guild_id:   int,
    root_value: int,
) -> SpiritRootRecord:
    """Insert the player's very first spirit root row."""
    await execute(
        """
        INSERT INTO spirit_roots
            (discord_id, guild_id, current_value, best_value,
             pity_counter, total_spins, acquired_at, last_spin_at)
        VALUES (%s, %s, %s, %s, 0, 0, NOW(), NULL)
        """,
        (discord_id, guild_id, root_value, root_value),
    )
    log.debug(
        "Spirit root created  discord_id=%s  guild_id=%s  value=%s",
        discord_id, guild_id, root_value,
    )
    record = await get_spirit_root(discord_id, guild_id)
    assert record is not None, "Row must exist immediately after INSERT"
    return record


async def apply_spin_result(
    discord_id:     int,
    guild_id:       int,
    rolled_value:   int,
    outcome:        str,    # 'improved' | 'equal' | 'protected'
    pity_triggered: bool,
) -> SpiritRootRecord:
    """Atomically update the spirit root after a spin."""
    if outcome == "improved":
        await execute(
            """
            UPDATE spirit_roots
            SET    current_value = %s,
                   best_value    = GREATEST(best_value, %s),
                   pity_counter  = 0,
                   total_spins   = total_spins + 1,
                   last_spin_at  = NOW()
            WHERE  discord_id = %s AND guild_id = %s
            """,
            (rolled_value, rolled_value, discord_id, guild_id),
        )

    elif outcome == "equal":
        await execute(
            """
            UPDATE spirit_roots
            SET    total_spins  = total_spins + 1,
                   last_spin_at = NOW()
            WHERE  discord_id = %s AND guild_id = %s
            """,
            (discord_id, guild_id),
        )

    elif outcome == "protected":
        if pity_triggered:
            await execute(
                """
                UPDATE spirit_roots
                SET    pity_counter  = 0,
                       total_spins   = total_spins + 1,
                       last_spin_at  = NOW()
                WHERE  discord_id = %s AND guild_id = %s
                """,
                (discord_id, guild_id),
            )
        else:
            await execute(
                """
                UPDATE spirit_roots
                SET    pity_counter  = pity_counter + 1,
                       total_spins   = total_spins + 1,
                       last_spin_at  = NOW()
                WHERE  discord_id = %s AND guild_id = %s
                """,
                (discord_id, guild_id),
            )
    else:
        raise ValueError(f"Unknown spin outcome: {outcome!r}")

    record = await get_spirit_root(discord_id, guild_id)
    assert record is not None, "Row disappeared mid-transaction"
    return record


async def log_spin(
    discord_id:     int,
    guild_id:       int,
    rolled_value:   int,
    pity_triggered: bool,
    outcome:        str,
) -> None:
    """Append an immutable entry to the spin audit log."""
    await execute(
        """
        INSERT INTO spirit_root_spin_log
            (discord_id, guild_id, rolled_value, pity_triggered, outcome, spun_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        """,
        (discord_id, guild_id, rolled_value, int(pity_triggered), outcome),
    )


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------

async def admin_set_root(
    discord_id: int,
    guild_id:   int,
    new_value:  int,
) -> SpiritRootRecord:
    """Admin override: force-set current AND best root."""
    await execute(
        """
        UPDATE spirit_roots
        SET    current_value = %s,
               best_value    = GREATEST(best_value, %s)
        WHERE  discord_id = %s AND guild_id = %s
        """,
        (new_value, new_value, discord_id, guild_id),
    )
    record = await get_spirit_root(discord_id, guild_id)
    if record is None:
        raise LookupError(
            f"No spirit root found for discord_id={discord_id} guild_id={guild_id}"
        )
    return record


async def admin_reset_root(discord_id: int, guild_id: int) -> None:
    """Admin override: delete the player's spirit root row entirely."""
    await execute(
        "DELETE FROM spirit_roots WHERE discord_id = %s AND guild_id = %s",
        (discord_id, guild_id),
    )
    log.warning("Spirit root RESET  discord_id=%s  guild_id=%s", discord_id, guild_id)


async def admin_reset_pity(discord_id: int, guild_id: int) -> SpiritRootRecord:
    """Admin override: zero out pity counter without touching the root itself."""
    await execute(
        """
        UPDATE spirit_roots
        SET    pity_counter = 0
        WHERE  discord_id = %s AND guild_id = %s
        """,
        (discord_id, guild_id),
    )
    record = await get_spirit_root(discord_id, guild_id)
    if record is None:
        raise LookupError(
            f"No spirit root found for discord_id={discord_id} guild_id={guild_id}"
        )
    return record


# ---------------------------------------------------------------------------
# Cooldown helpers  (reuses the shared cooldowns table)
# ---------------------------------------------------------------------------

_SPIN_CMD = "spirit_root_spin"


async def get_spin_cooldown(discord_id: int) -> datetime | None:
    """Return the spin cooldown expiry, or None if the player is free to spin."""
    row = await fetch_one(
        """
        SELECT expires_at
        FROM   cooldowns
        WHERE  discord_id = %s
          AND  command     = %s
          AND  expires_at  > NOW()
        """,
        (discord_id, _SPIN_CMD),
    )
    return row["expires_at"] if row else None


async def set_spin_cooldown(discord_id: int, cooldown_seconds: int) -> None:
    """
    Upsert the spin cooldown.

    Pass ``cooldown_seconds=0`` to clear the cooldown immediately
    (delegates to ``clear_spin_cooldown``).

    FIX: replaced deprecated VALUES(col) with row alias pattern.
    """
    if cooldown_seconds <= 0:
        await clear_spin_cooldown(discord_id)
        return

    await execute(
        """
        INSERT INTO cooldowns (discord_id, command, expires_at)
        VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s SECOND))
        AS new_row
        ON DUPLICATE KEY UPDATE
            expires_at = new_row.expires_at
        """,
        (discord_id, _SPIN_CMD, cooldown_seconds),
    )


async def clear_spin_cooldown(discord_id: int) -> None:
    """
    Remove the spin cooldown row entirely so the player can spin immediately.

    Use this instead of ``set_spin_cooldown(id, 1)`` — that left a 1-second
    TTL row in the table.  This performs a clean DELETE.
    """
    await execute(
        "DELETE FROM cooldowns WHERE discord_id = %s AND command = %s",
        (discord_id, _SPIN_CMD),
    )


# ---------------------------------------------------------------------------
# Internal mappers
# ---------------------------------------------------------------------------

def _row_to_record(row: dict) -> SpiritRootRecord:
    return SpiritRootRecord(
        discord_id=row["discord_id"],
        guild_id=row["guild_id"],
        current_value=row["current_value"],
        best_value=row["best_value"],
        pity_counter=row["pity_counter"],
        total_spins=row["total_spins"],
        acquired_at=row["acquired_at"],
        last_spin_at=row["last_spin_at"],
    )


def _row_to_log(row: dict) -> SpinLogEntry:
    return SpinLogEntry(
        id=row["id"],
        discord_id=row["discord_id"],
        guild_id=row["guild_id"],
        rolled_value=row["rolled_value"],
        pity_triggered=bool(row["pity_triggered"]),
        outcome=row["outcome"],
        spun_at=row["spun_at"],
    )