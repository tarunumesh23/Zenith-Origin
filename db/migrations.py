import asyncio
from db.database import connect, disconnect, execute

# Add all your table creation queries here
MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGINT PRIMARY KEY,
        username VARCHAR(100),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # Future tables can be added here
    # """
    # CREATE TABLE IF NOT EXISTS economy (
    #     user_id BIGINT PRIMARY KEY,
    #     balance INT DEFAULT 0
    # )
    # """,
]


async def run_migrations():
    # Connect to the database
    await connect()

    # Run each migration
    for query in MIGRATIONS:
        await execute(query)

    print("  🗄️  Migrations   » Done")

    # Disconnect from the database
    await disconnect()


# Run migrations when the script is executed directly
if __name__ == "__main__":
    asyncio.run(run_migrations())