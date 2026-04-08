import discord
from discord.ext import commands
from dotenv import load_dotenv
from ui.embed import error_embed
import asyncio
import logging
import os
import signal
import sys
import db.database as database
from db.migrations import run_migrations
from ui.status import send_status

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

# Suppress noisy discord.py internals
logging.getLogger("discord.client").setLevel(logging.WARNING)
logging.getLogger("discord.player").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
load_dotenv()

def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        log.critical("Missing required environment variable: %s", key)
        sys.exit(1)
    return value

TOKEN            = _require_env("token")
REQUIRED_ROLE_ID = int(_require_env("REQUIRED_ROLE_ID"))
REQUIRE_ROLE     = os.getenv("REQUIRE_ROLE", "true").lower() == "true"
OWNER_ID         = int(os.getenv("OWNER_ID", "0"))

EXCLUDED_FOLDERS = {"ui", "__pycache__"}

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=["z!", "Z!"],
    intents=intents,
    help_command=None,
)

_ready = False  # guard against on_ready firing on reconnects

# ---------------------------------------------------------------------------
# Global check
# ---------------------------------------------------------------------------
@bot.check
async def global_check(ctx: commands.Context) -> bool:
    if ctx.author.id == OWNER_ID:
        return True
    if not REQUIRE_ROLE:
        return True
    return any(role.id == REQUIRED_ROLE_ID for role in ctx.author.roles)

# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandOnCooldown):
        embed = error_embed(
            ctx,
            description=f"Slow down! Try again in **{error.retry_after:.1f}s**",
        )
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(3)
        await msg.delete()
        return

    if isinstance(error, commands.CheckFailure):
        msg = await ctx.send("❌ You need the required role to use this bot.")
        await asyncio.sleep(5)
        await msg.delete()
        return

    raise error

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@bot.event
async def on_ready() -> None:
    global _ready
    if _ready:
        log.warning("on_ready fired again (reconnect) — skipping re-initialisation")
        return
    _ready = True

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Mortals chase immortality 👀",
        )
    )

    log.info("=" * 40)
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    log.info("=" * 40)

    # Database
    try:
        await database.connect()
        await run_migrations()
        await send_status(bot, "start")
        log.info("Database    » Connected")
    except Exception:
        log.exception("Database    » Failed to connect")

    # Load cogs
    success, failed, skipped = [], [], []

    for root, dirs, files in os.walk("./cogs"):
        for d in list(dirs):
            if d in EXCLUDED_FOLDERS:
                folder = os.path.join(root, d).replace("./", "").replace(os.sep, "/")
                log.info("Cog         » Skipped  %s", folder)
                skipped.append(d)
        dirs[:] = [d for d in dirs if d not in EXCLUDED_FOLDERS]

        for filename in files:
            if filename.endswith(".py") and filename != "__init__.py":
                path = os.path.relpath(os.path.join(root, filename))
                extension = path.replace(os.sep, ".")[:-3]
                try:
                    await bot.load_extension(extension)
                    log.info("Cog         » Loaded   %s", extension)
                    success.append(extension)
                except Exception:
                    log.exception("Cog         » Failed   %s", extension)
                    failed.append(extension)

    await bot.tree.sync()

    log.info("=" * 40)
    log.info(
        "Cogs        » %d loaded | %d failed | %d skipped",
        len(success), len(failed), len(skipped),
    )
    log.info("Slash cmds  » Synced")
    log.info("=" * 40)

# ---------------------------------------------------------------------------
# Shutdown helpers
# ---------------------------------------------------------------------------
async def _shutdown() -> None:
    log.info("Shutting down...")
    try:
        await send_status(bot, "stop")
        await database.disconnect()
    except Exception:
        log.exception("Error during shutdown cleanup")
    await bot.close()

def _handle_signal(signum, _frame) -> None:
    log.info("Received signal %s", signal.Signals(signum).name)
    asyncio.get_running_loop().create_task(_shutdown())

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())