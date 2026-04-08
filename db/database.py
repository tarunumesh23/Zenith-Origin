import aiomysql
import os
from dotenv import load_dotenv

load_dotenv()

pool = None


async def connect():
    global pool
    pool = await aiomysql.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
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