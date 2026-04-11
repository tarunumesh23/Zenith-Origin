from __future__ import annotations

"""
cogs/AdminSpiritRoots.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Admin-only Spirit Root management commands.
"""
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from db import spirit_roots as db
from spirit_roots import (
    PITY_THRESHOLD,
    SPIN_COOLDOWN_SECONDS,
    get_tier_by_value,
)
from ui.embed import build_embed, error_embed
from ui.interaction_utils import safe_defer

log = logging.getLogger("bot.cogs.admin_spirit_roots")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pity_bar(current: int, threshold: int, width: int = 10) -> str:
    filled = min(round((current / threshold) * width), width)
    return f"{'█' * filled}{'░' * (width - filled)}  {current}/{threshold}"


def _tier_line(value: int) -> str:
    t = get_tier_by_value(value)
    return f"{t.emoji} **{t.name}** (Tier {value})"


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AdminSpiritRoots(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /admin_root (group) ───────────────────────────────────────────────

    @commands.hybrid_group(name="admin_root", description="Admin Spirit Root management")
    @commands.has_permissions(manage_guild=True)
    async def admin_root(self, ctx: commands.Context) -> None:
        """Root group — invoking directly does nothing."""
        pass

    # ── view ──────────────────────────────────────────────────────────────

    @admin_root.command(name="view", description="Inspect any player's full spirit root profile.")
    @commands.has_permissions(manage_guild=True)
    async def admin_view(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        guild_id = ctx.guild.id if ctx.guild else 0
        record   = await db.get_spirit_root(member.id, guild_id)

        if record is None:
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="No Spirit Root",
                    description=f"{member.mention} has not yet awakened a Spirit Root.",
                ),
                ephemeral=True,
            )
            return

        cooldown = await db.get_spin_cooldown(member.id)
        if cooldown and _as_utc(cooldown) > _now():
            remaining = int((_as_utc(cooldown) - _now()).total_seconds())
            h, rem    = divmod(remaining, 3600)
            m         = rem // 60
            cd_str    = f"On cooldown — **{h}h {m}m** remaining"
        else:
            cd_str = "**Ready to spin**"

        history = await db.get_spin_history(member.id, guild_id, limit=5)
        hist_lines: list[str] = []
        ICONS = {"improved": "⬆️", "equal": "➡️", "protected": "🛡️"}
        for e in history:
            tier  = get_tier_by_value(e.rolled_value)
            icon  = ICONS.get(e.outcome, "❓")
            pity  = " *(pity)*" if e.pity_triggered else ""
            ts    = e.spun_at.strftime("%m/%d %H:%M")
            hist_lines.append(f"`{ts}` {icon} {tier.emoji} {tier.name} — {e.outcome}{pity}")

        hist_text = "\n".join(hist_lines) if hist_lines else "*No spins yet.*"

        await ctx.send(
            embed=build_embed(
                ctx,
                title=f"🔍 {member.display_name}'s Spirit Root Profile",
                description=f"Full profile data for {member.mention}.",
                color=discord.Color.blurple(),
                fields=[
                    {"name": "Current Root",   "value": _tier_line(record.current_value), "inline": True},
                    {"name": "Best Root",       "value": _tier_line(record.best_value),    "inline": True},
                    {"name": "Total Spins",     "value": str(record.total_spins),           "inline": True},
                    {
                        "name":  "Pity Counter",
                        "value": f"`{_pity_bar(record.pity_counter, PITY_THRESHOLD)}`",
                        "inline": False,
                    },
                    {"name": "Spin Cooldown",  "value": cd_str,   "inline": False},
                    {"name": "Last 5 Spins",   "value": hist_text, "inline": False},
                    {
                        "name":  "Acquired",
                        "value": f"<t:{int(record.acquired_at.replace(tzinfo=timezone.utc).timestamp())}:R>"
                                 if record.acquired_at.tzinfo is None
                                 else f"<t:{int(record.acquired_at.timestamp())}:R>",
                        "inline": True,
                    },
                ],
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── set ───────────────────────────────────────────────────────────────

    @admin_root.command(name="set", description="Force-set a player's spirit root value.")
    @commands.has_permissions(manage_guild=True)
    async def admin_set(
        self,
        ctx: commands.Context,
        member: discord.Member,
        value: int,
    ) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        if not (1 <= value <= 5):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Value", description="Value must be between **1** and **5**."),
                ephemeral=True,
            )
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        record   = await db.get_spirit_root(member.id, guild_id)
        if record is None:
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="No Spirit Root",
                    description=f"{member.mention} has no spirit root. They must `/root spin` first.",
                ),
                ephemeral=True,
            )
            return

        updated = await db.admin_set_root(member.id, guild_id, value)
        tier    = get_tier_by_value(value)

        log.warning(
            "AdminSpiritRoots » set  admin=%s  target=%s  value=%s",
            ctx.author.id, member.id, value,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="⚙️ Spirit Root Override",
                description=(
                    f"Set {member.mention}'s Spirit Root to "
                    f"{tier.emoji} **{tier.name}** (Tier {value})."
                ),
                color=discord.Colour(tier.colour),
                fields=[
                    {"name": "New Current", "value": _tier_line(updated.current_value), "inline": True},
                    {"name": "New Best",    "value": _tier_line(updated.best_value),    "inline": True},
                ],
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── reset ─────────────────────────────────────────────────────────────

    @admin_root.command(name="reset", description="Wipe a player's entire spirit root data.")
    @commands.has_permissions(administrator=True)
    async def admin_reset(self, ctx: commands.Context, member: discord.Member) -> None:
        """Requires administrator (not just manage_guild) — this is destructive."""
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        guild_id = ctx.guild.id if ctx.guild else 0
        await db.admin_reset_root(member.id, guild_id)

        log.warning(
            "AdminSpiritRoots » reset  admin=%s  target=%s",
            ctx.author.id, member.id,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🗑️ Spirit Root Reset",
                description=(
                    f"All spirit root data for {member.mention} has been wiped.\n"
                    "They may use `/root spin` to receive a new starting root."
                ),
                color=discord.Color.red(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── reset_pity ────────────────────────────────────────────────────────

    @admin_root.command(name="reset_pity", description="Zero out a player's pity counter.")
    @commands.has_permissions(manage_guild=True)
    async def admin_reset_pity(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        guild_id = ctx.guild.id if ctx.guild else 0
        record   = await db.get_spirit_root(member.id, guild_id)

        if record is None:
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="No Spirit Root",
                    description=f"{member.mention} has no spirit root record.",
                ),
                ephemeral=True,
            )
            return

        old_pity = record.pity_counter
        updated  = await db.admin_reset_pity(member.id, guild_id)

        log.info(
            "AdminSpiritRoots » reset_pity  admin=%s  target=%s  was=%s",
            ctx.author.id, member.id, old_pity,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🔄 Pity Reset",
                description=(
                    f"Reset {member.mention}'s pity counter.\n"
                    f"Was **{old_pity}** → now **{updated.pity_counter}**."
                ),
                color=discord.Color.orange(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── grant_spin ────────────────────────────────────────────────────────

    @admin_root.command(name="grant_spin", description="Clear a player's spin cooldown immediately.")
    @commands.has_permissions(manage_guild=True)
    async def admin_grant_spin(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        if member.bot:
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Target", description="Bots cannot spin."),
                ephemeral=True,
            )
            return

        await db.clear_spin_cooldown(member.id)

        log.info(
            "AdminSpiritRoots » grant_spin  admin=%s  target=%s",
            ctx.author.id, member.id,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🎟️ Free Spin Granted",
                description=(
                    f"Cleared {member.mention}'s spin cooldown.\n"
                    "They may use `/root spin` immediately."
                ),
                color=discord.Color.green(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── Error handler ──────────────────────────────────────────────────────

    @admin_root.error
    async def admin_root_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Missing Permissions",
                    description="You need **Manage Server** (or **Administrator** for reset) to use admin root commands.",
                ),
                ephemeral=True,
            )
        else:
            log.exception("Unhandled error in admin_root", exc_info=error)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminSpiritRoots(bot))
    log.info("Cog loaded  » AdminSpiritRoots")