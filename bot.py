import discord
from discord.ext import commands
from dotenv import load_dotenv
from ui.embed import error_embed
import asyncio
import logging
import os
import signal
import sys
import time
import db.database as database
from db.migrations import run_migrations
from db.cultivators import has_passed
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

# Commands that don't require registration to use
UNREGISTERED_ALLOWED = {"start"}

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

_ready      = False
_started    = False
_start_time: float | None = None

# ---------------------------------------------------------------------------
# Global check
# ---------------------------------------------------------------------------
@bot.check
async def global_check(ctx: commands.Context) -> bool:
    global BOT_LOCKED, LOCK_REASON
    BOT_LOCKED = False
    LOCK_REASON = "No reason provided."

    # Owner bypasses everything
    if ctx.author.id == OWNER_ID:
        return True

    # 🔒 Bot lock check
    if BOT_LOCKED:
        await ctx.send(
            embed=discord.Embed(
                title="🔒 Bot Locked",
                description=f"The bot is currently locked by the owner.\n\n**Reason:** {LOCK_REASON}",
                color=discord.Color.red(),
            )
        )
        return False

    # Role gate
    if REQUIRE_ROLE and not any(role.id == REQUIRED_ROLE_ID for role in ctx.author.roles):
        return False

    # Allow unregistered commands freely
    if ctx.command and ctx.command.name in UNREGISTERED_ALLOWED:
        return True

    # Registration gate — must have passed the intro trial
    if not await has_passed(ctx.author.id):
        await ctx.send(
            embed=discord.Embed(
                title="⛩️ The Path is Sealed",
                description=(
                    "You have not yet proven yourself to the Dao.\n\n"
                    "Use `z!start` to begin your trial and earn the right to cultivate."
                ),
                color=discord.Color.dark_red(),
            ),
            ephemeral=True,
        )
        return False

    return True

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
        # Silent fail — the global_check already sent a message if needed
        return

    raise error

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@bot.event
async def on_ready() -> None:
    global _ready, _started, _start_time
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

    _started    = True
    _start_time = time.monotonic()
    await send_status(bot, "start")

# ---------------------------------------------------------------------------
# Shutdown helpers
# ---------------------------------------------------------------------------
async def _shutdown() -> None:
    log.info("Shutting down...")
    try:
        # Send stop status BEFORE closing the bot so the HTTP session is alive
        await send_status(bot, "stop")
    except Exception:
        log.exception("Failed to send shutdown status")
    try:
        await database.disconnect()
    except Exception:
        log.exception("Error during shutdown cleanup")
    await bot.close()

_shutdown_triggered = False

def _handle_signal(signum, _frame) -> None:
    global _shutdown_triggered
    if _shutdown_triggered:
        return  # Prevent double-trigger on rapid signals
    _shutdown_triggered = True
    log.info("Received signal %s", signal.Signals(signum).name)
    asyncio.get_running_loop().create_task(_shutdown())

@bot.command()
@commands.is_owner()
async def sync(ctx):
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()
    await ctx.send("Synced and cleared global commands.")



@bot.command()
@commands.is_owner()
async def lock(ctx, *, reason: str = "No reason provided."):
    global BOT_LOCKED, LOCK_REASON

    BOT_LOCKED = True
    LOCK_REASON = reason

    await ctx.send(
        embed=discord.Embed(
            title="🔒 Bot Locked",
            description=f"Bot has been locked.\n\n**Reason:** {reason}",
            color=discord.Color.red(),
        )
    )


@bot.command()
@commands.is_owner()
async def unlock(ctx):
    global BOT_LOCKED, LOCK_REASON

    BOT_LOCKED = False
    LOCK_REASON = ""

    await ctx.send(
        embed=discord.Embed(
            title="🔓 Bot Unlocked",
            description="Bot is now operational again.",
            color=discord.Color.green(),
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        async with bot:
            await bot.start(TOKEN)
    except Exception:
        log.exception("Bot crashed")
        await send_status(bot, "crash")
        raise

if __name__ == "__main__":
    asyncio.run(main())