from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

from talent.constants import (
    RARITIES,
    RARITY_ORDER,
    ONE_PER_SERVER_TALENTS,
    SPIN_PITY,
    FUSION_PITY,
)
from talent.engine import (
    spin_talent,
    fuse_talents,
    evolve_talent,
    accept_talent,
    reject_talent,
    toggle_lock,
)
from talent.cultivation_bridge import describe_bonuses  # CHANGE 1
from talent.models import PlayerTalent, PlayerTalentData
from db import talents as db
from ui.embed import build_embed, error_embed

log = logging.getLogger("bot.cogs.talent")

INVENTORY_MAX = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rarity_color(rarity: str) -> discord.Color:
    return discord.Color(RARITIES.get(rarity, {}).get("color", 0xFFFFFF))


def _enrich_talent(talent: PlayerTalent) -> PlayerTalent:
    """
    Fill in color and emoji from RARITIES if the talent was loaded from DB
    (DB rows don't store these derived fields).
    """
    rarity_data   = RARITIES.get(talent.rarity, {})
    talent.color  = rarity_data.get("color", 0xFFFFFF)
    talent.emoji  = rarity_data.get("emoji", "")
    return talent


async def _guard_cultivator(ctx: commands.Context) -> dict | None:
    """Ensure the user is a registered cultivator."""
    try:
        from db import cultivators as cult_db
        row = await cult_db.get_cultivator(ctx.author.id)
    except Exception:
        log.exception("Talent » DB fetch failed for discord_id=%s", ctx.author.id)
        await ctx.send(
            embed=error_embed(ctx, title="Database Error",
                              description="Could not fetch your profile. Try again later."),
            ephemeral=True,
        )
        return None

    if row is None:
        await ctx.send(
            embed=error_embed(ctx, title="Not a Cultivator",
                              description="You have not yet walked the Path. Use `z!start` to begin."),
            ephemeral=True,
        )
        return None

    return row


async def _load_player(discord_id: int, guild_id: int) -> PlayerTalentData:
    raw = await db.get_player_talent_data(discord_id, guild_id)
    return raw if raw is not None else PlayerTalentData(user_id=discord_id, guild_id=guild_id)


def _talent_embed(
    ctx_or_interaction,
    talent: PlayerTalent,
    title: str,
    extra_desc: str = "",
    show_footer: bool = True,
) -> discord.Embed:
    talent      = _enrich_talent(talent)
    rarity_data = RARITIES.get(talent.rarity, {})
    stage_label = ["", " ✦", " ✦✦"][talent.evolution_stage]
    flags = "  ".join(filter(None, [
        "🔒 Locked"    if talent.is_locked    else "",
        "☠️ Corrupted" if talent.is_corrupted else "",
    ]))

    desc = (
        f"{talent.emoji} **{talent.name}**{stage_label}\n"
        f"**Rarity:** {rarity_data.get('emoji', '')} {talent.rarity}\n"
        f"**Multiplier:** ×{talent.multiplier:.2f}\n"
        f"**Tags:** {', '.join(talent.tags) if talent.tags else '—'}\n\n"
        f"*{talent.description}*"
    )
    if flags:
        desc += f"\n\n{flags}"
    if extra_desc:
        desc += f"\n\n{extra_desc}"

    return build_embed(
        ctx_or_interaction,
        title=title,
        description=desc,
        color=_rarity_color(talent.rarity),
        show_footer=show_footer,
        show_timestamp=True,
    )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Talent(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /talent ───────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="talent",
        description="View your active talent",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def talent(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)

        if player.active_talent is None:
            await ctx.send(
                embed=error_embed(ctx, title="No Active Talent",
                                  description="You have no talent yet. Ask a mod for a spin token."),
                ephemeral=True,
            )
            return

        # CHANGE 2 — include cultivation bonuses in the embed
        pity_lines = "\n".join(
            f"**{tier}:** {player.spin_pity.get(tier, 0)} / {threshold}"
            for tier, threshold in SPIN_PITY.items()
        )
        cult_bonuses = describe_bonuses(player.active_talent)
        extra = (
            f"**Spin Pity**\n{pity_lines}\n**Total Spins:** {player.total_spins}"
            f"\n\n**✨ Cultivation Bonuses**\n{cult_bonuses}"
        )
        await ctx.send(
            embed=_talent_embed(
                ctx, player.active_talent,
                title=f"🌟 {ctx.author.display_name}'s Talent",
                extra_desc=extra,
            ),
            ephemeral=True,
        )

    # ── /inventory ────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="inventory",
        description="Browse your talent inventory",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def inventory(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)

        if not player.inventory:
            await ctx.send(
                embed=error_embed(ctx, title="Empty Inventory",
                                  description="Your inventory is empty."),
                ephemeral=True,
            )
            return

        lines = []
        for i, t in enumerate(player.inventory, start=1):
            t           = _enrich_talent(t)
            stage_label = ["", " ✦", " ✦✦"][t.evolution_stage]
            lock        = "🔒" if t.is_locked else ""
            corrupt     = "☠️" if t.is_corrupted else ""
            lines.append(
                f"`{i:>2}.` {t.emoji} **{t.name}**{stage_label} "
                f"[{t.rarity}] ×{t.multiplier:.2f} {lock}{corrupt}"
            )

        desc  = "\n".join(lines)
        desc += f"\n\n`{len(player.inventory)} / {INVENTORY_MAX}` slots used"

        await ctx.send(
            embed=build_embed(
                ctx,
                title=f"🎒 {ctx.author.display_name}'s Inventory",
                description=desc,
                color=discord.Color.blurple(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── /fuse ─────────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="fuse",
        description="Fuse two talents from your inventory",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fuse(
        self,
        ctx: commands.Context,
        slot_a: int,
        slot_b: int,
        mode: str = "auto",
    ) -> None:
        """
        Fuse the talent in slot_a with slot_b.
        mode: auto | same | cross | rng
        Slots are 1-indexed as shown in /inventory.
        """
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)
        inv      = player.inventory

        if not (1 <= slot_a <= len(inv)) or not (1 <= slot_b <= len(inv)):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Slot",
                                  description=f"You have **{len(inv)}** talents in inventory. "
                                              f"Choose valid slot numbers."),
                ephemeral=True,
            )
            return

        if slot_a == slot_b:
            await ctx.send(
                embed=error_embed(ctx, title="Same Slot",
                                  description="You cannot fuse a talent with itself."),
                ephemeral=True,
            )
            return

        talent_a = inv[slot_a - 1]
        talent_b = inv[slot_b - 1]

        if talent_a.is_locked or talent_b.is_locked:
            locked = talent_a.name if talent_a.is_locked else talent_b.name
            await ctx.send(
                embed=error_embed(ctx, title="Talent Locked",
                                  description=f"**{locked}** is locked. Unlock it first with `/lock`."),
                ephemeral=True,
            )
            return

        if mode not in ("auto", "same", "cross", "rng"):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Mode",
                                  description="Mode must be: `auto`, `same`, `cross`, or `rng`."),
                ephemeral=True,
            )
            return

        result = fuse_talents(player, talent_a, talent_b, mode=mode)

        # Remove consumed talents (higher index first to avoid index shifting)
        for idx in sorted([slot_a - 1, slot_b - 1], reverse=True):
            player.inventory.pop(idx)

        if result["success"]:
            result_talent = result["result_talent"]
            if result_talent:
                player.inventory.append(result_talent)

            pity_note = ""
            if result["pity_guarantee"]:
                pity_note = "\n\n🛡️ *Fusion pity guarantee triggered.*"
            elif result["pity_bonus"]:
                pity_note = "\n\n✨ *Fusion pity bonus applied (+20% chance).*"

            if result_talent:
                embed = _talent_embed(
                    ctx, result_talent,
                    title="⚗️ Fusion Successful!",
                    extra_desc=(
                        f"**{talent_a.name}** + **{talent_b.name}** → **{result_talent.name}**"
                        f"{pity_note}"
                    ),
                )
            else:
                embed = build_embed(
                    ctx, title="⚗️ Fusion Successful — No Result?",
                    description="Something went wrong resolving the result talent.",
                    color=discord.Color.orange(),
                )

        else:
            failure      = result["failure_outcome"]
            failure_desc = result["failure_description"]

            if failure == "corruption" and result["result_talent"]:
                player.inventory.append(result["result_talent"])
                embed = _talent_embed(
                    ctx, result["result_talent"],
                    title="💀 Fusion Failed — Corruption",
                    extra_desc=f"*{failure_desc}*\n\nPity counter: **{result['new_pity']}**",
                )
            elif failure == "mutation" and result["result_talent"]:
                player.inventory.append(result["result_talent"])
                embed = _talent_embed(
                    ctx, result["result_talent"],
                    title="🧬 Fusion Failed — Mutation!",
                    extra_desc=f"*{failure_desc}*\n\nPity counter: **{result['new_pity']}**",
                )
            else:
                title_map = {
                    "backfire":     "💥 Fusion Failed — Backfire",
                    "catastrophic": "☠️ Fusion Failed — Catastrophic",
                }
                embed = build_embed(
                    ctx,
                    title=title_map.get(failure, "❌ Fusion Failed"),
                    description=f"*{failure_desc}*\n\nPity counter: **{result['new_pity']}**",
                    color=discord.Color.red(),
                    show_footer=True,
                )

        await db.save_player_talent_data(player)

        # FIX #1/#2: pass strings, not objects; use resolved_mode for the ENUM
        await db.log_fusion(
            discord_id=ctx.author.id,
            guild_id=guild_id,
            talent_a=talent_a.name,
            talent_b=talent_b.name,
            mode=result["resolved_mode"],          # "same" / "cross" / "rng"
            success=result["success"],
            result_name=(
                result["result_talent"].name if result.get("result_talent") else None
            ),
            failure_outcome=result.get("failure_outcome"),
        )

        await ctx.send(embed=embed)

    # ── /evolve ───────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="evolve",
        description="Evolve a talent in your inventory",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def evolve(
        self,
        ctx: commands.Context,
        slot: int,
        crystals: int = 0,
    ) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)
        inv      = player.inventory

        if not (1 <= slot <= len(inv)):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Slot",
                                  description=f"You have **{len(inv)}** talents in inventory."),
                ephemeral=True,
            )
            return

        talent = inv[slot - 1]
        success, updated_talent, message = evolve_talent(player, talent, evolution_items=crystals)
        inv[slot - 1] = updated_talent

        await db.save_player_talent_data(player)

        if success:
            await ctx.send(
                embed=_talent_embed(ctx, updated_talent, title="✦ Evolution", extra_desc=message)
            )
        else:
            await ctx.send(
                embed=error_embed(ctx, title="Evolution Failed", description=message),
                ephemeral=True,
            )

    # ── /lock ─────────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="lock",
        description="Toggle lock on a talent in your inventory",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def lock(self, ctx: commands.Context, slot: int) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)
        inv      = player.inventory

        if not (1 <= slot <= len(inv)):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Slot",
                                  description=f"You have **{len(inv)}** talents in inventory."),
                ephemeral=True,
            )
            return

        talent        = inv[slot - 1]
        message       = toggle_lock(talent)
        inv[slot - 1] = talent

        await db.save_player_talent_data(player)

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🔒 Lock Toggled",
                description=message,
                color=discord.Color.greyple(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── /set_active ───────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="set_active",
        description="Swap a talent from your inventory into your active slot",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def set_active(self, ctx: commands.Context, slot: int) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)
        inv      = player.inventory

        if not (1 <= slot <= len(inv)):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Slot",
                                  description=f"You have **{len(inv)}** talents in inventory."),
                ephemeral=True,
            )
            return

        chosen = inv[slot - 1]

        # FIX #4: remove chosen from inventory BEFORE calling accept_talent
        # so it can never appear in both active and inventory simultaneously.
        player.inventory = [t for t in player.inventory if t is not chosen]

        msg = accept_talent(player, chosen, replace_active=True)
        # accept_talent sets chosen as active and pushes old active to inventory.

        await db.save_player_talent_data(player)

        await ctx.send(
            embed=_talent_embed(ctx, chosen, title="✅ Active Talent Updated", extra_desc=msg),
            ephemeral=True,
        )

    # =========================================================================
    # ADMIN COMMANDS
    # =========================================================================

    @commands.hybrid_group(name="admin_talent", description="Admin talent management")
    @commands.has_permissions(manage_guild=True)
    async def admin_talent(self, ctx: commands.Context) -> None:
        pass

    @admin_talent.command(name="grant_spin", description="Grant spin token(s) to a user")
    @commands.has_permissions(manage_guild=True)
    async def grant_spin(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int = 1,
    ) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        if amount < 1:
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Amount",
                                  description="Amount must be at least 1."),
                ephemeral=True,
            )
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        await db.add_spin_tokens(member.id, guild_id, amount)

        log.info(
            "Talent » grant_spin  admin=%s  target=%s  amount=%d",
            ctx.author.id, member.id, amount,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🎟️ Spin Token Granted",
                description=(
                    f"**{amount}** spin token(s) granted to {member.mention}.\n"
                    f"They can now use `/use_spin` to roll their talent."
                ),
                color=discord.Color.green(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    @admin_talent.command(name="view", description="View any user's talent profile")
    @commands.has_permissions(manage_guild=True)
    async def admin_view(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(member.id, guild_id)
        tokens   = await db.get_spin_tokens(member.id, guild_id)

        lines = [
            f"**Tokens available:** {tokens}",
            f"**Total spins:** {player.total_spins}",
            f"**Total fusions:** {player.total_fusions}",
        ]

        if player.active_talent:
            t           = _enrich_talent(player.active_talent)
            stage_label = ["", " ✦", " ✦✦"][t.evolution_stage]
            lines.append(
                f"\n**Active:** {t.emoji} {t.name}{stage_label} [{t.rarity}] ×{t.multiplier:.2f}"
            )
        else:
            lines.append("\n**Active:** None")

        if player.inventory:
            lines.append(f"\n**Inventory ({len(player.inventory)}/{INVENTORY_MAX}):**")
            for i, t in enumerate(player.inventory, 1):
                t           = _enrich_talent(t)
                stage_label = ["", " ✦", " ✦✦"][t.evolution_stage]
                lock        = "🔒" if t.is_locked    else ""
                corrupt     = "☠️" if t.is_corrupted else ""
                lines.append(f"`{i:>2}.` {t.emoji} **{t.name}**{stage_label} [{t.rarity}] {lock}{corrupt}")
        else:
            lines.append("\n**Inventory:** Empty")

        await ctx.send(
            embed=build_embed(
                ctx,
                title=f"🔍 {member.display_name}'s Talent Profile",
                description="\n".join(lines),
                color=discord.Color.blurple(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    @admin_talent.command(name="reset", description="Wipe a user's entire talent data")
    @commands.has_permissions(administrator=True)
    async def admin_reset(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        guild_id = ctx.guild.id if ctx.guild else 0
        await db.reset_player_talent_data(member.id, guild_id)

        log.warning(
            "Talent » admin_reset  admin=%s  target=%s",
            ctx.author.id, member.id,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🗑️ Talent Data Reset",
                description=f"All talent data for {member.mention} has been wiped.",
                color=discord.Color.red(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # =========================================================================
    # PLAYER SPIN (token-gated)
    # =========================================================================

    @commands.hybrid_command(
        name="use_spin",
        description="Use a spin token to roll for a talent",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def use_spin(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        tokens   = await db.get_spin_tokens(ctx.author.id, guild_id)

        if tokens < 1:
            await ctx.send(
                embed=error_embed(ctx, title="No Spin Tokens",
                                  description="You have no spin tokens. Ask a mod to grant you one."),
                ephemeral=True,
            )
            return

        player = await _load_player(ctx.author.id, guild_id)

        if len(player.inventory) >= INVENTORY_MAX:
            await ctx.send(
                embed=error_embed(
                    ctx, title="Inventory Full",
                    description=f"Your inventory is full ({INVENTORY_MAX} slots). "
                                f"Fuse or discard talents to make room.",
                ),
                ephemeral=True,
            )
            return

        claimed                = await db.get_claimed_one_per_server(guild_id)
        talent, pity_triggered = spin_talent(player, claimed)

        await db.consume_spin_token(ctx.author.id, guild_id)
        await db.save_player_talent_data(player)

        # FIX #1: pass strings, not the PlayerTalent object
        await db.log_spin(
            ctx.author.id, guild_id,
            talent.name,
            talent.rarity,
            pity_triggered,
            accepted=False,
        )

        pity_note  = "\n\n✨ *Pity threshold reached — guaranteed pull!*" if pity_triggered else ""
        token_line = f"\n\n🎟️ **Spin tokens remaining:** {tokens - 1}"

        view  = _AcceptRejectView(ctx, player, talent, guild_id)
        embed = _talent_embed(
            ctx, talent,
            title="🎲 Talent Revealed",
            extra_desc=f"Accept this talent or discard it?{pity_note}{token_line}",
        )
        await ctx.send(embed=embed, view=view)


# ---------------------------------------------------------------------------
# Accept / Reject UI
# ---------------------------------------------------------------------------

class _AcceptRejectView(discord.ui.View):
    def __init__(
        self,
        ctx: commands.Context,
        player: PlayerTalentData,
        talent: PlayerTalent,
        guild_id: int,
    ) -> None:
        super().__init__(timeout=60)
        self.ctx      = ctx
        self.player   = player
        self.talent   = talent
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()

        replace = self.player.active_talent is None
        msg     = accept_talent(self.player, self.talent, replace_active=replace)

        await db.save_player_talent_data(self.player)
        await db.mark_last_spin_accepted(interaction.user.id, self.guild_id)

        if self.talent.name in ONE_PER_SERVER_TALENTS:
            await db.claim_one_per_server(self.guild_id, interaction.user.id, self.talent.name)

        await interaction.response.edit_message(
            embed=build_embed(
                interaction,  # type: ignore[arg-type]
                title="✅ Talent Accepted",
                description=msg,
                color=discord.Color.green(),
            ),
            view=None,
        )

    @discord.ui.button(label="❌ Discard", style=discord.ButtonStyle.danger)
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()

        msg = reject_talent(self.talent)

        await interaction.response.edit_message(
            embed=build_embed(
                interaction,  # type: ignore[arg-type]
                title="❌ Talent Discarded",
                description=msg,
                color=discord.Color.red(),
            ),
            view=None,
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Talent(bot))