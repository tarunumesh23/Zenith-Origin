import discord
import os
import psutil
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")


def get_system_info():
    return {
        "cpu": f"{psutil.cpu_percent()}%",
        "ram": f"{psutil.virtual_memory().percent}%",
    }


async def send_status(bot, status: str):
    channel_id = int(os.getenv("STATUS_CHANNEL_ID"))

    # 🔥 FIX 1: fetch channel instead of get_channel
    channel = await bot.fetch_channel(channel_id)

    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    info = get_system_info()

    if status == "start":
        color = discord.Color.green()
        title = "🟢 Bot Online"
        description = "Bot has started successfully."
    elif status == "stop":
        color = discord.Color.red()
        title = "🔴 Bot Offline"
        description = "Bot has been stopped."
    elif status == "crash":
        color = discord.Color.orange()
        title = "🟠 Bot Crashed"
        description = "Bot has crashed and is restarting."
    else:
        color = discord.Color.blurple()
        title = "🔵 Bot Status"
        description = status

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(IST)
    )

    embed.add_field(name="🕐 Time", value=now, inline=True)
    embed.add_field(name="⚙️ CPU", value=info["cpu"], inline=False)
    embed.add_field(name="🧠 RAM", value=info["ram"], inline=False)

    # 🔥 FIX 2: safe avatar access
    avatar_url = None
    if bot.user and bot.user.display_avatar:
        avatar_url = bot.user.display_avatar.url

    if avatar_url:
        embed.set_footer(text="ZO Bot", icon_url=avatar_url)
    else:
        embed.set_footer(text="ZO Bot")

    await channel.send(embed=embed)