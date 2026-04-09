from __future__ import annotations

import logging
import os
import random

import discord
from discord.ext import commands

from db.cultivators import has_passed, set_affinity, upsert_cultivator
from story.introstory import OUTCOMES, SCENES, STORY_BANNER_URL, SceneView, _build_scene_embed
from cultivation.constants import AFFINITIES, AFFINITY_DISPLAY
from ui.embed import build_embed

log = logging.getLogger("bot.cogs.start")

CULTIVATION_LOG_CHANNEL = int(os.getenv("CULTIVATION_LOG_CHANNEL", "0"))


class Start(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_users: set[int] = set()

    # ------------------------------------------------------------------
    # Command
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="start", description="Begin your path of cultivation")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def start(self, ctx: commands.Context) -> None:
        uid = ctx.author.id

        if await has_passed(uid):
            await ctx.send(
                embed=build_embed(
                    ctx,
                    title="⚡ Already Awakened",
                    description="You have already walked the Path. There is no need to prove yourself again.",
                    color=discord.Color.gold(),
                ),
                ephemeral=True,
            )
            return

        if uid in self.active_users:
            await ctx.send(
                embed=build_embed(
                    ctx,
                    title="⏳ Trial In Progress",
                    description="You are already on your trial. Focus.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        self.active_users.add(uid)
        log.info("Story » %s started the intro trial", ctx.author)
        await self._send_scene(ctx, scene_index=0, score=0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_scene(
        self,
        ctx: commands.Context,
        scene_index: int,
        score: int,
    ) -> None:
        scene = SCENES[scene_index]
        embed = _build_scene_embed(ctx, scene_index, scene["description"])
        view = SceneView(self, ctx, scene_index, score)
        await ctx.send(embed=embed, view=view)

    async def send_outcome(
        self,
        ctx: commands.Context,
        score: int,
        interaction: discord.Interaction,
    ) -> None:
        self.active_users.discard(ctx.author.id)

        if score >= 8:
            key = "pass"
        elif score >= 4:
            key = "retry"
        else:
            key = "fail"

        outcome = OUTCOMES[key]

        # Roll affinity once — same value shown in embed and written to DB
        affinity: str | None = random.choice(AFFINITIES) if key == "pass" else None

        affinity_line = (
            f"\n\n**Elemental Affinity Awakened:** {AFFINITY_DISPLAY[affinity]}"
            if affinity else ""
        )

        embed = build_embed(
            ctx,
            title=outcome["title"],
            description=f"{outcome['description']}{affinity_line}\n\n**Final Score:** `{score}/10`",
            color=outcome["color"],
            show_footer=True,
        )
        embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )
        if STORY_BANNER_URL:
            embed.set_image(url=STORY_BANNER_URL)

        # Edit the scene message in place
        await interaction.response.edit_message(embed=embed, view=None)

        # Persist to DB
        try:
            await upsert_cultivator(
                discord_id=ctx.author.id,
                username=str(ctx.author),
                display_name=ctx.author.display_name,
                joined_at=ctx.author.joined_at or ctx.author.created_at,
                outcome=key,
            )
            if affinity:
                await set_affinity(ctx.author.id, affinity)
        except Exception:
            log.exception("Story » Failed to upsert cultivator %s", ctx.author.id)

        log.info("Story » %s finished trial — outcome=%s score=%d affinity=%s", ctx.author, key, score, affinity)

        if key == "pass":
            await self._log_cultivator(ctx, affinity)

    async def _log_cultivator(self, ctx: commands.Context, affinity: str | None = None) -> None:
        channel = self.bot.get_channel(CULTIVATION_LOG_CHANNEL)
        if channel is None:
            log.warning("Story » CULTIVATION_LOG_CHANNEL %d not found", CULTIVATION_LOG_CHANNEL)
            return

        affinity_line = (
            f"\nElemental Affinity: {AFFINITY_DISPLAY[affinity]}"
            if affinity else ""
        )

        embed = build_embed(
            ctx,
            title="⚡ A New Cultivator Has Emerged",
            description=(
                f"{ctx.author.mention} has proven themselves worthy.\n"
                f"The Dao has opened its gates.{affinity_line}"
            ),
            color=discord.Color.gold(),
            thumbnail=ctx.author.display_avatar.url,
            show_footer=True,
        )
        embed.set_footer(text=f"ID: {ctx.author.id}")
        if STORY_BANNER_URL:
            embed.set_image(url=STORY_BANNER_URL)

        await channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Start(bot))