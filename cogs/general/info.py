from __future__ import annotations

import os
import time
import discord
from discord.ext import commands
from dotenv import load_dotenv
from datetime import timedelta

from ui.embed import build_embed

load_dotenv()

SUPPORT_SERVER = "https://discord.gg/KPezB4UvNq"
OWNER_ID = int(os.getenv("OWNER_ID", "0"))


class InfoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Support",
            url=SUPPORT_SERVER,
            style=discord.ButtonStyle.link,
            emoji="🌐",
        ))


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot       = bot
        self.start_time = time.time()

    @commands.hybrid_command(name="info", description="Shows information about the bot")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def info(self, ctx: commands.Context) -> None:
        bot_user    = self.bot.user
        created_at  = discord.utils.format_dt(bot_user.created_at, style="F")
        guild_count = len(self.bot.guilds)
        cmd_count   = len([c for c in self.bot.commands if not c.hidden])

        uptime_secs = int(time.time() - self.start_time)
        uptime      = discord.utils.format_dt(
            discord.utils.utcnow() - timedelta(seconds=uptime_secs), style="R"
        )
        ping  = round(self.bot.latency * 1000)
        owner = self.bot.get_user(OWNER_ID) or await self.bot.fetch_user(OWNER_ID)

        fields = [
            {
                "name": "📊 Stats",
                "value": (
                    f"**Servers**  : `{guild_count}`\n"
                    f"**Commands** : `{cmd_count}`\n"
                    f"**Ping**     : `{ping}ms`\n"
                    f"**Uptime**   : {uptime}"
                ),
                "inline": False,
            },
            {"name": "👑 Developer", "value": owner.mention,                    "inline": True},
            {"name": "🌐 Support",   "value": f"[Join Server]({SUPPORT_SERVER})", "inline": True},
        ]

        embed = build_embed(
            ctx,
            title=bot_user.name,
            description=(
                "A fast, reliable, and feature-rich Discord bot.\n"
                "Use `/help` to view all available commands."
            ),
            fields=fields,
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=bot_user.display_avatar.url)
        embed.set_author(name=f"{bot_user.name} • Information", icon_url=bot_user.display_avatar.url)
        embed.set_footer(
            text=f"ID: {bot_user.id} • Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )

        await ctx.send(embed=embed, view=InfoView())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))