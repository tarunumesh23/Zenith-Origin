from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Final, NamedTuple

import discord
import psutil
import pytz

log = logging.getLogger("bot.status")

IST: Final = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Status config
# ---------------------------------------------------------------------------

class _StatusEntry(NamedTuple):
    color: discord.Color
    title: str
    description: str


_STATUS_CONFIG: Final[dict[str, _StatusEntry]] = {
    "start": _StatusEntry(discord.Color.green(),  "🟢 Bot Online",  "Bot has started successfully."),
    "stop":  _StatusEntry(discord.Color.red(),    "🔴 Bot Offline", "Bot has been stopped."),
    "crash": _StatusEntry(discord.Color.orange(), "🟠 Bot Crashed", "Bot has crashed and is restarting."),
}

_DEFAULT_STATUS: Final = _StatusEntry(discord.Color.blurple(), "🔵 Bot Status", "Unknown status event.")

# Prime the CPU sampler at import time so the first real call returns a
# meaningful percentage instead of 0.0 (psutil needs two samples to diff).
psutil.cpu_percent(interval=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ist() -> datetime:
    return datetime.now(IST)


def _system_fields() -> list[tuple[str, str, bool]]:
    """Return ``(name, value, inline)`` tuples for CPU and RAM embed fields."""
    mem = psutil.virtual_memory()
    used_mb  = mem.used  // 1024 ** 2
    total_mb = mem.total // 1024 ** 2
    return [
        ("⚙️ CPU", f"{psutil.cpu_percent(interval=None):.1f}%", True),
        ("🧠 RAM", f"{mem.percent:.1f}%  ({used_mb:,} / {total_mb:,} MB)", True),
    ]


def _resolve_channel_id() -> int | None:
    """
    Read and validate ``STATUS_CHANNEL_ID`` from the environment.
    Returns the integer ID, or ``None`` if absent / malformed.
    """
    raw = os.getenv("STATUS_CHANNEL_ID", "").strip()
    if not raw:
        log.error("STATUS_CHANNEL_ID is not set — skipping status message")
        return None
    try:
        return int(raw)
    except ValueError:
        log.error("STATUS_CHANNEL_ID is not a valid integer: %r", raw)
        return None


def _build_embed(bot: discord.Client, status: str, now: datetime) -> discord.Embed:
    entry = _STATUS_CONFIG.get(status)

    if entry is None:
        log.warning("Unknown status %r — using default entry", status)
        entry = _DEFAULT_STATUS

    embed = discord.Embed(
        title=entry.title,
        description=entry.description,
        color=entry.color,
        timestamp=now,
    )
    embed.add_field(name="🕐 Time (IST)", value=now.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    for name, value, inline in _system_fields():
        embed.add_field(name=name, value=value, inline=inline)

    avatar_url = bot.user.display_avatar.url if bot.user else None
    embed.set_footer(text="ZO Bot", icon_url=avatar_url)
    return embed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def send_status(bot: discord.Client, status: str) -> None:
    """Send a status embed to the configured ``STATUS_CHANNEL_ID`` channel."""
    channel_id = _resolve_channel_id()
    if channel_id is None:
        return

    embed = _build_embed(bot, status, _now_ist())

    try:
        channel = await bot.fetch_channel(channel_id)
    except discord.NotFound:
        log.error("Status channel %d not found", channel_id)
        return
    except discord.Forbidden:
        log.error("No permission to access channel %d", channel_id)
        return
    except discord.HTTPException:
        log.exception("HTTP error fetching channel %d", channel_id)
        return

    if not isinstance(channel, discord.abc.Messageable):
        log.error("Channel %d is not messageable — cannot send status", channel_id)
        return

    try:
        await channel.send(embed=embed)
        log.debug("Status '%s' sent to channel %d", status, channel_id)
    except discord.Forbidden:
        log.error("Missing permissions to send in channel %d", channel_id)
    except discord.HTTPException:
        log.exception("Failed to send status embed to channel %d", channel_id)