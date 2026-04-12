from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Final

import discord
from discord.ext import commands
from dotenv import load_dotenv

import db.database as database
from db.cultivators import has_passed
from db.migrations import run_migrations
from ui.embed import error_embed
from ui.status import send_status

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bot")

for noisy_logger in ("discord.client", "discord.gateway", "discord.http"):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)
logging.getLogger("discord.player").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
load_dotenv()


def _require_env(key: str) -> str:
    """Return the value of a required environment variable or exit."""
    value = os.getenv(key, "").strip()
    if not value:
        log.critical("Missing required environment variable: %s", key)
        sys.exit(1)
    return value


def _optional_int(key: str, default: int = 0) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("Env var %s='%s' is not a valid integer; using default %d", key, raw, default)
        return default


TOKEN: Final[str]            = _require_env("token")
REQUIRED_ROLE_ID: Final[int] = int(_require_env("REQUIRED_ROLE_ID"))
REQUIRE_ROLE: Final[bool]    = os.getenv("REQUIRE_ROLE", "true").lower() == "true"
OWNER_ID: Final[int]         = _optional_int("OWNER_ID")

EXCLUDED_FOLDERS: Final[frozenset[str]] = frozenset({"ui", "__pycache__"})
UNREGISTERED_ALLOWED: Final[frozenset[str]] = frozenset({"start"})

# ---------------------------------------------------------------------------
# Lock state — encapsulated in a dataclass to avoid scattered globals
# ---------------------------------------------------------------------------
@dataclass
class BotLock:
    locked: bool = False
    reason: str  = "No reason provided."

    def acquire(self, reason: str = "No reason provided.") -> None:
        self.locked = True
        self.reason = reason
        log.warning("Bot locked. Reason: %s", reason)

    def release(self) -> None:
        self.locked = False
        self.reason = ""
        log.info("Bot unlocked.")

    @property
    def embed_locked(self) -> discord.Embed:
        return discord.Embed(
            title="🔒 Bot Locked",
            description=f"The bot is currently locked by the owner.\n\n**Reason:** {self.reason}",
            color=discord.Color.red(),
        )

    @property
    def embed_unlocked(self) -> discord.Embed:
        return discord.Embed(
            title="🔓 Bot Unlocked",
            description="Bot is now operational again.",
            color=discord.Color.green(),
        )


lock_state = BotLock()

# ---------------------------------------------------------------------------
# Bot runtime state — encapsulated to avoid scattered module-level flags
# ---------------------------------------------------------------------------
@dataclass
class BotState:
    ready: bool              = False
    started: bool            = False
    start_time: float | None = None
    shutdown_triggered: bool = False


state = BotState()

# ---------------------------------------------------------------------------
# Intents & bot instance
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=["z!", "Z!"],
    intents=intents,
    help_command=None,
    case_insensitive=True,
)


# ---------------------------------------------------------------------------
# Global check
# ---------------------------------------------------------------------------
@bot.check
async def global_check(ctx: commands.Context) -> bool:
    # Owner bypasses everything
    if ctx.author.id == OWNER_ID:
        return True

    # Bot lock check
    if lock_state.locked:
        await ctx.send(embed=lock_state.embed_locked)
        return False

    # Role gate
    if REQUIRE_ROLE and not any(role.id == REQUIRED_ROLE_ID for role in ctx.author.roles):
        return False

    # Unregistered-allowed commands bypass registration gate
    if ctx.command and ctx.command.name in UNREGISTERED_ALLOWED:
        return True

    # Registration gate
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
    # Unwrap CheckFailure silently
    if isinstance(error, commands.CheckFailure):
        return

    if isinstance(error, commands.CommandOnCooldown):
        embed = error_embed(
            ctx,
            description=f"Slow down! Try again in **{error.retry_after:.1f}s**",
        )
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(3)
        try:
            await msg.delete()
        except discord.HTTPException:
            pass  # Message may have already been deleted
        return

    if isinstance(error, commands.CommandNotFound):
        return  # Ignore unknown commands silently

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            embed=discord.Embed(
                title="❌ Missing Argument",
                description=f"Missing required argument: `{error.param.name}`",
                color=discord.Color.red(),
            )
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(
            embed=discord.Embed(
                title="❌ Bad Argument",
                description=str(error),
                color=discord.Color.red(),
            )
        )
        return

    # Unexpected errors — log with full context
    log.exception(
        "Unhandled command error in '%s' (author=%s guild=%s)",
        ctx.command,
        ctx.author,
        ctx.guild,
        exc_info=error,
    )
    raise error


# ---------------------------------------------------------------------------
# Cog loader helper
# ---------------------------------------------------------------------------
async def _load_cogs() -> tuple[list[str], list[str], list[str]]:
    """
    Walk ./cogs, load every .py file as an extension.
    Returns (success, failed, skipped) extension name lists.
    """
    success, failed, skipped = [], [], []
    loaded: set[str] = set()

    for root, dirs, files in os.walk("./cogs"):
        # Filter excluded directories in-place so os.walk skips them
        excluded = [d for d in dirs if d in EXCLUDED_FOLDERS]
        for d in excluded:
            folder = os.path.join(root, d).replace("./", "").replace(os.sep, "/")
            log.info("Cog loader  » Skipped folder  %s", folder)
            skipped.append(d)
        dirs[:] = [d for d in dirs if d not in EXCLUDED_FOLDERS]

        for filename in sorted(files):  # sorted for deterministic load order
            if not filename.endswith(".py") or filename == "__init__.py":
                continue

            path      = os.path.relpath(os.path.join(root, filename))
            extension = path.replace(os.sep, ".")[:-3]

            if extension in loaded:
                log.warning("Cog loader  » Duplicate   %s — skipping", extension)
                continue

            try:
                await bot.load_extension(extension)
                loaded.add(extension)
                log.info("Cog loader  » Loaded     %s", extension)
                success.append(extension)
            except commands.ExtensionAlreadyLoaded:
                log.warning("Cog loader  » Already loaded %s — skipping", extension)
                loaded.add(extension)
            except Exception:
                log.exception("Cog loader  » Failed     %s", extension)
                failed.append(extension)

    return success, failed, skipped


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@bot.event
async def on_ready() -> None:
    if state.ready:
        log.warning("on_ready fired again (reconnect) — skipping re-initialisation")
        return
    state.ready = True

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Mortals chase immortality 👀",
        )
    )

    log.info("=" * 50)
    log.info("Logged in  » %s  (ID: %s)", bot.user, bot.user.id)
    log.info("Guilds     » %d", len(bot.guilds))
    log.info("=" * 50)

    # Database
    try:
        await database.connect()
        await run_migrations()
        log.info("Database   » Connected & migrations applied")
    except Exception:
        log.exception("Database   » Failed to initialise — aborting cog load")
        return

    # Load cogs
    success, failed, skipped = await _load_cogs()

    # Sync slash commands
    try:
        await bot.tree.sync()
        log.info("Slash cmds » Synced (%d commands)", len(bot.tree.get_commands()))
    except discord.HTTPException:
        log.exception("Slash cmds » Sync failed")

    log.info("=" * 50)
    log.info(
        "Cogs       » %d loaded | %d failed | %d skipped",
        len(success), len(failed), len(skipped),
    )
    if failed:
        log.warning("Failed cogs: %s", ", ".join(failed))
    log.info("=" * 50)

    state.started   = True
    state.start_time = time.monotonic()
    await send_status(bot, "start")


# ---------------------------------------------------------------------------
# Owner commands
# ---------------------------------------------------------------------------
@bot.command(hidden=True)
@commands.is_owner()
async def sync(ctx: commands.Context) -> None:
    """Force-sync slash commands and clear the global command tree."""
    bot.tree.clear_commands(guild=None)
    synced = await bot.tree.sync()
    await ctx.send(f"✅ Synced {len(synced)} global command(s).")


@bot.command(name="botlock", hidden=True)
@commands.is_owner()
async def botlock(ctx: commands.Context, *, reason: str = "No reason provided.") -> None:
    """Lock the bot so only the owner can use it."""
    lock_state.acquire(reason)
    await ctx.send(embed=discord.Embed(
        title="🔒 Bot Locked",
        description=f"Bot has been locked.\n\n**Reason:** {reason}",
        color=discord.Color.red(),
    ))


@bot.command(name="botunlock", hidden=True)
@commands.is_owner()
async def botunlock(ctx: commands.Context) -> None:
    """Unlock the bot for all authorised users."""
    lock_state.release()
    await ctx.send(embed=lock_state.embed_unlocked)


@bot.command(name="botrestart", hidden=True)
@commands.is_owner()
async def botrestart(ctx: commands.Context, *, reason: str = "Manual restart by owner.") -> None:
    """Gracefully restart the bot by re-executing the current process."""
    await ctx.send(embed=discord.Embed(
        title="🔄 Restarting Bot",
        description=f"Bot is restarting...\n\n**Reason:** {reason}",
        color=discord.Color.orange(),
    ))
    log.info("Restart requested by owner. Reason: %s", reason)

    try:
        await send_status(bot, "stop")
    except Exception:
        log.exception("Failed to send pre-restart status")
    try:
        await database.disconnect()
        log.info("Database   » Disconnected cleanly before restart")
    except Exception:
        log.exception("Error during database disconnect before restart")

    await bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)


@bot.command(name="botstatus", hidden=True)
@commands.is_owner()
async def botstatus(ctx: commands.Context) -> None:
    """Display current runtime stats."""
    uptime = (
        f"{time.monotonic() - state.start_time:.0f}s"
        if state.start_time else "N/A"
    )
    embed = discord.Embed(title="📊 Bot Status", color=discord.Color.blurple())
    embed.add_field(name="Uptime",   value=uptime,             inline=True)
    embed.add_field(name="Guilds",   value=len(bot.guilds),    inline=True)
    embed.add_field(name="Locked",   value=str(lock_state.locked), inline=True)
    embed.add_field(name="Cogs",     value=len(bot.cogs),      inline=True)
    embed.add_field(name="Commands", value=len(bot.commands),  inline=True)
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Shutdown helpers
# ---------------------------------------------------------------------------
async def _shutdown() -> None:
    log.info("Shutdown sequence initiated...")
    try:
        await send_status(bot, "stop")
    except Exception:
        log.exception("Failed to send shutdown status")
    try:
        await database.disconnect()
        log.info("Database   » Disconnected cleanly")
    except Exception:
        log.exception("Error during database disconnect")
    await bot.close()
    log.info("Bot        » Closed")


def _handle_signal(signum: int, _frame) -> None:
    if state.shutdown_triggered:
        return
    state.shutdown_triggered = True
    log.info("Signal received: %s — initiating graceful shutdown", signal.Signals(signum).name)
    loop = asyncio.get_running_loop()
    loop.create_task(_shutdown())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle_signal)

    try:
        async with bot:
            await bot.start(TOKEN)
    except (KeyboardInterrupt, SystemExit):
        log.info("Interrupted — shutting down")
    except Exception:
        log.exception("Bot crashed unexpectedly")
        try:
            await send_status(bot, "crash")
        except Exception:
            log.exception("Failed to send crash status")
        raise
    finally:
        log.info("Process exiting")


if __name__ == "__main__":
    asyncio.run(main())