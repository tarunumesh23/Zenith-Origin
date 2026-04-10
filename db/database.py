from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import aiomysql

log = logging.getLogger("bot.database")

pool: aiomysql.Pool | None = None


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------

async def connect() -> None:
    global pool

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")

    url = urlparse(database_url)
    db_name = url.path.lstrip("/")
    host = url.hostname
    port = url.port or 3306

    if not host or not db_name:
        raise ValueError("DATABASE_URL is malformed — missing host or database name")

    pool = await aiomysql.create_pool(
        host=host,
        port=port,
        user=url.username,
        password=url.password,
        db=db_name,
        autocommit=True,
        minsize=2,
        maxsize=10,
        charset="utf8mb4",
        auth_plugin="mysql_native_password",
        connect_timeout=10,
    )

    log.info("Database    » Connected  (host=%s db=%s)", host, db_name)


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
    if not args:
        return
    async with _get_pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(query, args)