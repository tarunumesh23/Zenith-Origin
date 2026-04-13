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
from ui.interaction_utils import safe_edit

from talent.constants import RARITIES, ONE_PER_SERVER_TALENTS

log = logging.getLogger("bot.cogs.start")

CULTIVATION_LOG_CHANNEL = int(os.getenv("CULTIVATION_LOG_CHANNEL", "0"))


class Start(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_users: set[int] = set()

    # ───────────────────────────────────────────────────────────────
    # /start
    # ───────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="start", description="Begin your path of cultivation")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def start(self, ctx: commands.Context) -> None:
        uid = ctx.author.id

        if await has_passed(uid):
            await ctx.send(
                embed=build_embed(
                    ctx,
                    title="⚡ Already Awakened",
                    description="You have already walked the Path.",
                    color=discord.Color.gold(),
                ),
                ephemeral=True,
            )
            return

        # Soft reset instead of blocking forever
        if uid in self.active_users:
            self.active_users.discard(uid)

        self.active_users.add(uid)
        log.info("Start » %s began trial", ctx.author)

        await self._send_scene(ctx, 0, 0)

    # ───────────────────────────────────────────────────────────────

    async def _send_scene(self, ctx: commands.Context, scene_index: int, score: int) -> None:
        scene = SCENES[scene_index]
        embed = _build_scene_embed(ctx, scene_index, scene["description"])
        view  = SceneView(self, ctx, scene_index, score)
        await ctx.send(embed=embed, view=view)

    # ───────────────────────────────────────────────────────────────

    async def send_outcome(
        self,
        ctx: commands.Context,
        score: int,
        interaction: discord.Interaction,
    ) -> None:
        self.active_users.discard(ctx.author.id)

        # Outcome logic
        if score >= 8:
            key = "pass"
        elif score >= 4:
            key = "retry"
        else:
            key = "fail"

        outcome  = OUTCOMES[key]
        guild_id = ctx.guild.id if ctx.guild else 0
        affinity = random.choice(AFFINITIES) if key == "pass" else None

        # ── Reward blocks ─────────────────────────────
        affinity_text = ""
        talent_text   = ""
        root_text     = ""

        starter_talent = None
        starter_root   = None

        if key == "pass":
            # ── Affinity
            affinity_text = f"🌿 **Affinity:** {AFFINITY_DISPLAY[affinity]}"

            # ── Talent
            try:
                claimed = await talent_db.get_claimed_one_per_server(guild_id)
                starter_talent = roll_starter_talent(claimed)

                rarity = RARITIES.get(starter_talent.rarity, {})
                talent_text = (
                    f"🌟 **Talent:** {rarity.get('emoji','')} **{starter_talent.name}** "
                    f"[{starter_talent.rarity}]\n"
                    f"*{starter_talent.description}*"
                )
            except Exception:
                log.exception("Start » talent roll failed")

            # ── Spirit Root
            try:
                starter_root = roll_root()
                root_text = (
                    f"🌱 **Spirit Root:** {starter_root.emoji} **{starter_root.name}** "
                    f"(Tier {starter_root.value})\n"
                    f"*{starter_root.description}*"
                )
            except Exception:
                log.exception("Start » root roll failed")

        # ── Build embed (clean UI) ───────────────────
        desc_parts = [
            outcome["description"],
            "",
            f"**Final Score:** `{score}/10`",
        ]

        if affinity_text:
            desc_parts.append("\n" + affinity_text)
        if talent_text:
            desc_parts.append("\n" + talent_text)
        if root_text:
            desc_parts.append("\n" + root_text)

        embed = build_embed(
            ctx,
            title=outcome["title"],
            description="\n".join(desc_parts),
            color=outcome["color"],
            show_footer=True,
        )

        embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )

        if STORY_BANNER_URL:
            embed.set_image(url=STORY_BANNER_URL)

        await safe_edit(interaction, embed=embed, view=None)

        # ── Persist player ───────────────────────────
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
            log.exception("Start » cultivator save failed")

        # ── Save talent ──────────────────────────────
        if starter_talent:
            try:
                player = PlayerTalentData(ctx.author.id, guild_id)
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

                if starter_talent.name in ONE_PER_SERVER_TALENTS:
                    await talent_db.claim_one_per_server(
                        guild_id, ctx.author.id, starter_talent.name
                    )

            except Exception:
                log.exception("Start » talent save failed")

        # ── Save root ────────────────────────────────
        if starter_root:
            try:
                existing = await spirit_roots_db.get_spirit_root(ctx.author.id, guild_id)
                if not existing:
                    await spirit_roots_db.create_spirit_root(
                        ctx.author.id, guild_id, starter_root.value
                    )
            except Exception:
                log.exception("Start » root save failed")

        # ── Log ──────────────────────────────────────
        log.info(
            "Start » %s outcome=%s score=%d",
            ctx.author, key, score
        )

        if key == "pass":
            await self._log_cultivator(ctx, affinity, starter_talent, starter_root)

    # ───────────────────────────────────────────────────────────────

    async def _log_cultivator(self, ctx, affinity, talent, root):
        channel = self.bot.get_channel(CULTIVATION_LOG_CHANNEL)
        if not channel:
            return

        parts = [
            f"{ctx.author.mention} has awakened.",
            "",
        ]

        if affinity:
            parts.append(f"🌿 {AFFINITY_DISPLAY[affinity]}")
        if talent:
            parts.append(f"🌟 {talent.name} [{talent.rarity}]")
        if root:
            parts.append(f"🌱 {root.name} (T{root.value})")

        embed = build_embed(
            ctx,
            title="⚡ New Cultivator",
            description="\n".join(parts),
            color=discord.Color.gold(),
            thumbnail=ctx.author.display_avatar.url,
        )

        await channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Start(bot))