import discord
from discord.ext import commands
from ui.embed import build_embed
from db.database import fetch_one, fetch_all, execute


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="help", description="Shows this help message")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def help(self, ctx):
        embed = build_embed(
            ctx,
            title="Help",
            description="Here are the available commands!",
            fields=[
                {"name": "/help", "value": "Shows this help message", "inline": False},
            ],
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(General(bot))