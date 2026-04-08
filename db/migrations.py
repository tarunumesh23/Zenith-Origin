from __future__ import annotations

import asyncio
import logging

import db.database as database
from db.database import connect, disconnect, execute

log = logging.getLogger("bot.migrations")

# ---------------------------------------------------------------------------
# Add CREATE TABLE / ALTER TABLE statements here.
# Every statement must be idempotent (IF NOT EXISTS / IF NOT EXISTS column).
# ---------------------------------------------------------------------------
MIGRATIONS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id         BIGINT PRIMARY KEY,
        username   VARCHAR(100),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # """
    # CREATE TABLE IF NOT EXISTS economy (
    #     user_id BIGINT PRIMARY KEY,
    #     balance INT NOT NULL DEFAULT 0,
    #     FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    # ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    # """,
]


async def run_migrations() -> None:
    """
    Run all pending migrations.

    When called from on_ready the pool is already open — this function
    does NOT call connect() / disconnect() itself.  If run as __main__
    it opens and closes the connection around the migrations.
    """
    for i, query in enumerate(MIGRATIONS, start=1):
        try:
            await execute(query)
            log.debug("Migration %d/%d OK", i, len(MIGRATIONS))
        except Exception:
            log.exception("Migration %d/%d FAILED — query:\n%s", i, len(MIGRATIONS), query.strip())
            raise

    log.info("Migrations  » %d statement(s) applied", len(MIGRATIONS))


async def _run_standalone() -> None:
    """Entry point when the module is executed directly."""
    await connect()
    try:
        await run_migrations()
    finally:
        await disconnect()


if __name__ == "__main__":
    asyncio.run(_run_standalone())