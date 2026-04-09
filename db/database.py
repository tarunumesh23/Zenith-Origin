from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import aiomysql

log = logging.getLogger("bot.database")

pool: aiomysql.Pool | None = None


async def connect() -> None:
    global pool

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")

    url = urlparse(database_url)

    pool = await aiomysql.create_pool(
        host=url.hostname,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        db=url.path.lstrip("/"),
        autocommit=True,
        minsize=1,
        maxsize=10,
        charset="utf8mb4",
        auth_plugin="mysql_native_password",  # ← fix for caching_sha2_password error
    )

    log.info("Database    » Connected  (host=%s db=%s)", url.hostname, url.path.lstrip("/"))


async def disconnect() -> None:
    global pool
    if pool is None:
        return
    pool.close()
    await pool.wait_closed()
    pool = None
    log.info("Database    » Disconnected")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_pool() -> aiomysql.Pool:
    if pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Ensure connect() has been awaited before making queries."
        )
    return pool


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

async def fetch_one(query: str, args: tuple | list | None = None) -> dict | None:
    """Execute *query* and return the first row as a dict, or None."""
    async with _get_pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, args)
            return await cur.fetchone()


async def fetch_all(query: str, args: tuple | list | None = None) -> list[dict]:
    """Execute *query* and return all rows as a list of dicts."""
    async with _get_pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, args)
            return await cur.fetchall()


async def execute(query: str, args: tuple | list | None = None) -> int:
    """Execute a write query and return the number of affected rows."""
    async with _get_pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, args)
            return cur.rowcount


async def execute_many(query: str, args: list[tuple | list]) -> None:
    """Execute a write query for each item in *args* (bulk insert / update)."""
    async with _get_pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(query, args)