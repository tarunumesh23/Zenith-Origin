from __future__ import annotations

import logging
import os
import random

import discord
from discord.ext import commands

from db.cultivators import has_passed, set_affinity, upsert_cultivator
from story.introstory import OUTCOMES, SCENES, STORY_BANNER_URL, SceneView, _build_scene_embed
from cultivation.constants import AFFINITIES, AFFINITY_DISPLAY
from talent.engine import roll_starter_talent
from talent.models import PlayerTalentData
from db import talent as talent_db
from db import spirit_roots as spirit_roots_db
from spirit_roots import roll_root
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
        view  = SceneView(self, ctx, scene_index, score)
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

        outcome  = OUTCOMES[key]
        affinity: str | None = random.choice(AFFINITIES) if key == "pass" else None
        guild_id = ctx.guild.id if ctx.guild else 0

        affinity_line = (
            f"\n\n**Elemental Affinity Awakened:** {AFFINITY_DISPLAY[affinity]}"
            if affinity else ""
        )

        # ── Roll starter talent on pass ───────────────────────────────
        starter_talent = None
        talent_line    = ""

        if key == "pass":
            try:
                claimed        = await talent_db.get_claimed_one_per_server(guild_id)
                starter_talent = roll_starter_talent(claimed)

                from talent.constants import RARITIES
                rarity_data = RARITIES.get(starter_talent.rarity, {})
                talent_line = (
                    f"\n\n**Talent Awakened:** "
                    f"{rarity_data.get('emoji', '')} **{starter_talent.name}** "
                    f"[{starter_talent.rarity}]"
                    f"\n*{starter_talent.description}*"
                )
            except Exception:
                log.exception(
                    "Story » Failed to roll starter talent for discord_id=%s", ctx.author.id
                )

        # ── Roll starter spirit root on pass ──────────────────────────
        starter_root = None
        root_line    = ""

        if key == "pass":
            try:
                starter_root = roll_root()
                root_line = (
                    f"\n\n**Spirit Root Awakened:** "
                    f"{starter_root.emoji} **{starter_root.name}** (Tier {starter_root.value})"
                    f"\n*{starter_root.description}*"
                )
            except Exception:
                log.exception(
                    "Story » Failed to roll starter root for discord_id=%s", ctx.author.id
                )

        embed = build_embed(
            ctx,
            title=outcome["title"],
            description=(
                f"{outcome['description']}"
                f"{affinity_line}"
                f"{talent_line}"
                f"{root_line}"
                f"\n\n**Final Score:** `{score}/10`"
            ),
            color=outcome["color"],
            show_footer=True,
        )
        embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )
        if STORY_BANNER_URL:
            embed.set_image(url=STORY_BANNER_URL)

        await interaction.response.edit_message(embed=embed, view=None)

        # ── Persist cultivator row ────────────────────────────────────
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

        # ── Persist starter talent ────────────────────────────────────
        if key == "pass" and starter_talent is not None:
            try:
                player = PlayerTalentData(user_id=ctx.author.id, guild_id=guild_id)
                player.active_talent = starter_talent
                await talent_db.save_player_talent_data(player)

                await talent_db.log_spin(
                    ctx.author.id,
                    guild_id,
                    starter_talent.name,
                    starter_talent.rarity,
                    pity_triggered=False,
                    accepted=True,
                )

                from talent.constants import ONE_PER_SERVER_TALENTS
                if starter_talent.name in ONE_PER_SERVER_TALENTS:
                    await talent_db.claim_one_per_server(
                        guild_id, ctx.author.id, starter_talent.name
                    )

                log.info(
                    "Story » starter talent assigned discord_id=%s talent=%s rarity=%s",
                    ctx.author.id, starter_talent.name, starter_talent.rarity,
                )
            except Exception:
                log.exception(
                    "Story » Failed to save starter talent for discord_id=%s", ctx.author.id
                )

        # ── Persist starter spirit root ───────────────────────────────
        # cultivators row must exist before this INSERT (FK constraint).
        # upsert_cultivator above runs first — safe to insert root now.
        if key == "pass" and starter_root is not None:
            try:
                # Guard against duplicate calls (e.g. retry path edge cases)
                existing = await spirit_roots_db.get_spirit_root(ctx.author.id, guild_id)
                if existing is None:
                    await spirit_roots_db.create_spirit_root(
                        ctx.author.id, guild_id, starter_root.value
                    )
                    log.info(
                        "Story » starter root assigned discord_id=%s root=%s tier=%d",
                        ctx.author.id, starter_root.name, starter_root.value,
                    )
                else:
                    log.warning(
                        "Story » spirit root already exists for discord_id=%s — skipping",
                        ctx.author.id,
                    )
            except Exception:
                log.exception(
                    "Story » Failed to save starter root for discord_id=%s", ctx.author.id
                )

        log.info(
            "Story » %s finished trial — outcome=%s score=%d affinity=%s talent=%s root=%s",
            ctx.author, key, score, affinity,
            starter_talent.name if starter_talent else "none",
            f"{starter_root.name} (T{starter_root.value})" if starter_root else "none",
        )

        if key == "pass":
            await self._log_cultivator(ctx, affinity, starter_talent, starter_root)

    async def _log_cultivator(
        self,
        ctx: commands.Context,
        affinity: str | None = None,
        starter_talent=None,
        starter_root=None,
    ) -> None:
        channel = self.bot.get_channel(CULTIVATION_LOG_CHANNEL)
        if channel is None:
            log.warning("Story » CULTIVATION_LOG_CHANNEL %d not found", CULTIVATION_LOG_CHANNEL)
            return

        affinity_line = (
            f"\nElemental Affinity: {AFFINITY_DISPLAY[affinity]}"
            if affinity else ""
        )

        talent_line = ""
        if starter_talent is not None:
            from talent.constants import RARITIES
            rarity_data = RARITIES.get(starter_talent.rarity, {})
            talent_line = (
                f"\nTalent: {rarity_data.get('emoji', '')} {starter_talent.name} "
                f"[{starter_talent.rarity}]"
            )

        root_line = ""
        if starter_root is not None:
            root_line = (
                f"\nSpirit Root: {starter_root.emoji} {starter_root.name} "
                f"(Tier {starter_root.value})"
            )

        embed = build_embed(
            ctx,
            title="⚡ A New Cultivator Has Emerged",
            description=(
                f"{ctx.author.mention} has proven themselves worthy.\n"
                f"The Dao has opened its gates."
                f"{affinity_line}"
                f"{talent_line}"
                f"{root_line}"
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