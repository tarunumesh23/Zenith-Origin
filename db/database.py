import os
import aiomysql
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

pool = None


async def connect():
    global pool

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")

    url = urlparse(database_url)

    pool = await aiomysql.create_pool(
        host=url.hostname,
        port=url.port,
        user=url.username,
        password=url.password,
        db=url.path[1:],  # removes leading '/'
        autocommit=True,
        minsize=1,
        maxsize=10,
    )

    print("  🗄️  Database     » Connected")


async def disconnect():
    global pool
    if pool:
        pool.close()
        await pool.wait_closed()
        print("  🗄️  Database     » Disconnected")


async def fetch_one(query: str, args=None):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, args)
            return await cur.fetchone()


async def fetch_all(query: str, args=None):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, args)
            return await cur.fetchall()


async def execute(query: str, args=None):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, args)