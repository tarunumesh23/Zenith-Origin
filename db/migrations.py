from db.database import execute


# Add all your table creation queries here
# Each migration runs only if the table doesn't exist yet
MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGINT PRIMARY KEY,
        username VARCHAR(100),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # Add new tables here in the future
    # """
    # CREATE TABLE IF NOT EXISTS economy (
    #     user_id BIGINT PRIMARY KEY,
    #     balance INT DEFAULT 0
    # )
    # """,
]


async def run_migrations():
    for query in MIGRATIONS:
        await execute(query)
    print("  🗄️  Migrations   » Done")