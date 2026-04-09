from __future__ import annotations

import os
import discord
from discord.ext import commands

from story.introstory import SCENES, OUTCOMES, SceneView
from db.cultivators import upsert_cultivator, has_passed

CULTIVATION_LOG_CHANNEL = int(os.getenv("CULTIVATION_LOG_CHANNEL", "0"))


class Start(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_users: set[int] = set()

    @commands.hybrid_command(name="start", description="Begin your path of cultivation")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def start(self, ctx: commands.Context) -> None:
        uid = ctx.author.id

        # ── Block if already passed ──
        if await has_passed(uid):
            await ctx.send(
                embed=discord.Embed(
                    description="⚡ You have already walked the Path. There is no need to prove yourself again.",
                    color=discord.Color.gold()
                ),
                ephemeral=True
            )
            return

        if uid in self.active_users:
            await ctx.send(
                embed=discord.Embed(
                    description="⏳ You are already on your trial. Focus.",
                    color=discord.Color.orange()
                ),
                ephemeral=True
            )
            return

        self.active_users.add(uid)
        await self.send_scene(ctx, scene_index=0, score=0)

    async def send_scene(
        self,
        ctx: commands.Context,
        scene_index: int,
        score: int,
        followup: discord.Interaction | None = None
    ) -> None:
        scene = SCENES[scene_index]

        embed = discord.Embed(
            title=scene["title"],
            description=scene["description"],
            color=discord.Color.dark_teal()
        )
        embed.set_footer(text=f"Scene {scene_index + 1} of {len(SCENES)}")

        view = SceneView(self, ctx, scene_index, score)

        if followup:
            await followup.followup.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed, view=view)

    async def send_outcome(
        self,
        ctx: commands.Context,
        score: int,
        followup: discord.Interaction
    ) -> None:
        self.active_users.discard(ctx.author.id)

        if score >= 8:
            key = "pass"
        elif score >= 4:
            key = "retry"
        else:
            key = "fail"

        outcome = OUTCOMES[key]
        embed = discord.Embed(
            title=outcome["title"],
            description=f"{outcome['description']}\n\n**Final Score:** `{score}/10`",
            color=outcome["color"]
        )
        embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url
        )

        await followup.followup.send(embed=embed)

        # ── Save to database ──
        await upsert_cultivator(
            discord_id=ctx.author.id,
            username=str(ctx.author),
            display_name=ctx.author.display_name,
            joined_at=ctx.author.joined_at or ctx.author.created_at,
            outcome=key,
        )

        # ── Log to cultivation channel if passed ──
        if key == "pass":
            await self._log_cultivator(ctx)

    async def _log_cultivator(self, ctx: commands.Context) -> None:
        channel = self.bot.get_channel(CULTIVATION_LOG_CHANNEL)
        if channel is None:
            return

        embed = discord.Embed(
            title="⚡ A New Cultivator Has Emerged",
            description=(
                f"{ctx.author.mention} has proven themselves worthy.\n"
                f"The Dao has opened its gates."
            ),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"ID: {ctx.author.id}")

        await channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Start(bot))