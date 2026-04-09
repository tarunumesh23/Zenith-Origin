from __future__ import annotations

import logging
from datetime import datetime, timezone

from db.database import execute, fetch_one

log = logging.getLogger("bot.database.cultivators")


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

async def upsert_cultivator(
    discord_id: int,
    username: str,
    display_name: str,
    joined_at: datetime,
    outcome: str,
) -> None:
    """Insert or update a cultivator record."""
    await execute(
        """
        INSERT INTO cultivators
            (discord_id, username, display_name, joined_at, registered_at, outcome)
        VALUES
            (%s, %s, %s, %s, %s, %s)
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
            joined_at.replace(tzinfo=None),
            datetime.now(timezone.utc).replace(tzinfo=None),
            outcome,
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