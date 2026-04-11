from __future__ import annotations

"""
cogs/admin_talent.py
────────────────────
Admin-only talent management commands.
Requires manage_guild (or administrator for destructive ops).

Commands
────────
/admin_talent grant_spin   — give spin tokens to a user
/admin_talent revoke_spin  — remove spin tokens from a user
/admin_talent view         — inspect any user's full talent profile
/admin_talent reset        — wipe a user's entire talent data

Load alongside cogs/talent.py in your bot setup:
    await bot.load_extension("cogs.admin_talent")
"""

import logging

import discord
from discord.ext import commands

from talent.constants import RARITIES, SPIN_PITY
from talent.models import PlayerTalent, PlayerTalentData
from db import talent as db
from ui.embed import build_embed, error_embed

log = logging.getLogger("bot.cogs.admin_talent")

INVENTORY_MAX = 20


# ---------------------------------------------------------------------------
# Helpers (duplicated minimally to keep this cog self-contained)
# ---------------------------------------------------------------------------

def _enrich_talent(talent: PlayerTalent) -> PlayerTalent:
    rarity_data   = RARITIES.get(talent.rarity, {})
    talent.color  = rarity_data.get("color", 0xFFFFFF)
    talent.emoji  = rarity_data.get("emoji", "")
    return talent


async def _load_player(discord_id: int, guild_id: int) -> PlayerTalentData:
    raw = await db.get_player_talent_data(discord_id, guild_id)
    return raw if raw is not None else PlayerTalentData(user_id=discord_id, guild_id=guild_id)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AdminTalent(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /admin_talent (group) ─────────────────────────────────────────────

    @commands.hybrid_group(name="admin_talent", description="Admin talent management")
    @commands.has_permissions(manage_guild=True)
    async def admin_talent(self, ctx: commands.Context) -> None:
        """Root group — invoking directly does nothing."""
        pass

    # ── grant_spin ────────────────────────────────────────────────────────

    @admin_talent.command(name="grant_spin", description="Grant spin token(s) to a user")
    @commands.has_permissions(manage_guild=True)
    async def grant_spin(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int = 1,
    ) -> None:
        """
        Add `amount` spin tokens to `member`'s balance.
        Only mods with manage_guild can use this.
        """
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        if amount < 1:
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Amount",
                                  description="Amount must be at least 1."),
                ephemeral=True,
            )
            return

        if member.bot:
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Target",
                                  description="Bots cannot receive spin tokens."),
                ephemeral=True,
            )
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        await db.add_spin_tokens(member.id, guild_id, amount)
        new_balance = await db.get_spin_tokens(member.id, guild_id)

        log.info(
            "AdminTalent » grant_spin  admin=%s  target=%s  amount=%d  new_balance=%d",
            ctx.author.id, member.id, amount, new_balance,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🎟️ Spin Token(s) Granted",
                description=(
                    f"**{amount}** spin token(s) granted to {member.mention}.\n"
                    f"Their new balance: **{new_balance}** token(s).\n\n"
                    f"They can use `/use_spin` to roll."
                ),
                color=discord.Color.green(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── revoke_spin ───────────────────────────────────────────────────────

    @admin_talent.command(name="revoke_spin", description="Remove spin token(s) from a user")
    @commands.has_permissions(manage_guild=True)
    async def revoke_spin(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int = 1,
    ) -> None:
        """
        Remove up to `amount` spin tokens from `member`'s balance.
        Will not reduce below zero — excess is silently clamped.
        """
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        if amount < 1:
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Amount",
                                  description="Amount must be at least 1."),
                ephemeral=True,
            )
            return

        guild_id      = ctx.guild.id if ctx.guild else 0
        current       = await db.get_spin_tokens(member.id, guild_id)
        actual_remove = min(amount, current)   # clamp to available balance

        if actual_remove == 0:
            await ctx.send(
                embed=error_embed(ctx, title="No Tokens to Remove",
                                  description=f"{member.mention} has no spin tokens."),
                ephemeral=True,
            )
            return

        await db.consume_spin_token(member.id, guild_id, count=actual_remove)
        new_balance = await db.get_spin_tokens(member.id, guild_id)

        log.warning(
            "AdminTalent » revoke_spin  admin=%s  target=%s  removed=%d  new_balance=%d",
            ctx.author.id, member.id, actual_remove, new_balance,
        )

        note = f"\n*(Requested {amount}, only {actual_remove} removed — balance was {current}.)*" if actual_remove < amount else ""
        await ctx.send(
            embed=build_embed(
                ctx,
                title="🗑️ Spin Token(s) Revoked",
                description=(
                    f"Removed **{actual_remove}** spin token(s) from {member.mention}.\n"
                    f"Their new balance: **{new_balance}** token(s).{note}"
                ),
                color=discord.Color.orange(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── view ──────────────────────────────────────────────────────────────

    @admin_talent.command(name="view", description="View any user's full talent profile")
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
            f"**Fusion pity counter:** {player.fusion_pity}",
        ]

        # Spin pity
        pity_parts = ", ".join(
            f"{tier}: {player.spin_pity.get(tier, 0)}"
            for tier in SPIN_PITY
        )
        lines.append(f"**Spin pity:** {pity_parts}")

        if player.active_talent:
            t           = _enrich_talent(player.active_talent)
            stage_label = ["", " ✦", " ✦✦"][t.evolution_stage]
            cosmic      = " 🌌" if t.rarity == "Cosmic" else ""
            corrupt     = " ☠️" if t.is_corrupted else ""
            lines.append(
                f"\n**Active:** {t.emoji} {t.name}{stage_label} [{t.rarity}]{cosmic}{corrupt} ×{t.multiplier:.2f}"
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
                cosmic      = "🌌" if t.rarity == "Cosmic" else ""
                lines.append(
                    f"`{i:>2}.` {t.emoji} **{t.name}**{stage_label} "
                    f"[{t.rarity}] ×{t.multiplier:.2f} {lock}{corrupt}{cosmic}"
                )
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

    # ── reset ─────────────────────────────────────────────────────────────

    @admin_talent.command(name="reset", description="Wipe a user's entire talent data")
    @commands.has_permissions(administrator=True)
    async def admin_reset(self, ctx: commands.Context, member: discord.Member) -> None:
        """Requires administrator (not just manage_guild) — destructive action."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        guild_id = ctx.guild.id if ctx.guild else 0
        await db.reset_player_talent_data(member.id, guild_id)

        log.warning(
            "AdminTalent » admin_reset  admin=%s  target=%s",
            ctx.author.id, member.id,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🗑️ Talent Data Reset",
                description=(
                    f"All talent data for {member.mention} has been wiped.\n"
                    f"Their spin tokens are **not** affected by this action."
                ),
                color=discord.Color.red(),
                show_footer=True,
            ),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminTalent(bot))