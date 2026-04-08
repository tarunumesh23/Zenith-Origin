import logging
import os
from datetime import datetime

import discord
import psutil
import pytz

log = logging.getLogger("bot.status")

IST = pytz.timezone("Asia/Kolkata")

_STATUS_CONFIG: dict[str, tuple[discord.Color, str, str]] = {
    "start": (discord.Color.green(),  "🟢 Bot Online",  "Bot has started successfully."),
    "stop":  (discord.Color.red(),    "🔴 Bot Offline", "Bot has been stopped."),
    "crash": (discord.Color.orange(), "🟠 Bot Crashed", "Bot has crashed and is restarting."),
}

# Prime the CPU counter at import time so the first real call returns a
# meaningful percentage instead of 0.0 (psutil needs two samples to diff).
psutil.cpu_percent(interval=None)


def _system_fields() -> list[tuple[str, str, bool]]:
    """Return (name, value, inline) tuples for CPU and RAM embed fields."""
    mem = psutil.virtual_memory()
    return [
        ("⚙️ CPU", f"{psutil.cpu_percent(interval=None)}%", True),
        ("🧠 RAM", f"{mem.percent}%  ({mem.used // 1024**2} / {mem.total // 1024**2} MB)", True),
    ]


async def send_status(bot: discord.Client, status: str) -> None:
    """Send a status embed to the configured STATUS_CHANNEL_ID."""
    raw_id = os.getenv("STATUS_CHANNEL_ID")
    if not raw_id:
        log.error("STATUS_CHANNEL_ID is not set — skipping status message")
        return

    try:
        channel_id = int(raw_id)
    except ValueError:
        log.error("STATUS_CHANNEL_ID is not a valid integer: %r", raw_id)
        return

    now = datetime.now(IST)

    color, title, description = _STATUS_CONFIG.get(
        status,
        (discord.Color.blurple(), "🔵 Bot Status", status),
    )

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=now,
    )
    embed.add_field(name="🕐 Time (IST)", value=now.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    for name, value, inline in _system_fields():
        embed.add_field(name=name, value=value, inline=inline)

    avatar_url = bot.user.display_avatar.url if bot.user else None
    embed.set_footer(text="ZO Bot", icon_url=avatar_url)

    try:
        channel = await bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.error("Channel %d is not a text channel — cannot send status", channel_id)
            return
        await channel.send(embed=embed)
    except discord.Forbidden:
        log.error("Missing permissions to send in channel %d", channel_id)
    except discord.NotFound:
        log.error("Status channel %d not found", channel_id)
    except discord.HTTPException:
        log.exception("Failed to send status embed to channel %d", channel_id)