"""
cogs/AdminSpiritRoots.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Admin-only Spirit Root management commands, fully wired to db/spirit_roots.py.

Commands (all under the hybrid group /admin_root)
--------------------------------------------------
  view        — Full profile: root, pity bar, cooldown status, last 5 spins.
  set         — Force-set a player's root to any tier 1–5.
  reset       — Wipe a player's entire spirit root record (admin only).
  reset_pity  — Zero out a player's pity counter.
  grant_spin  — Clear a player's spin cooldown so they can spin immediately.

Fixes over the previous revision
---------------------------------
• All responses go through safe_respond_or_followup / safe_edit so every
  code path (slash + prefix) is interaction-safe with no Unknown Interaction crashes.
• Replaced the repeated ``if ctx.interaction: await safe_defer(...)`` pattern
  with a single ``_ack`` helper that handles both slash and prefix contexts.
• cog_command_error now catches ALL subcommands (not just the group root),
  covering MissingPermissions and unexpected DB errors everywhere.
• admin_set / admin_reset_pity / admin_view no longer crash when the target
  player has no row — they show a clean error embed instead.
• acquired_at timezone handling is centralised in one place (_fmt_ts).
• Bot-target guard added to every write command (not just grant_spin).
• All DB calls (apply_spin_result, log_spin, set_spin_cooldown, etc.) use
  the real db/spirit_roots.py signatures exactly as defined.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

from db import spirit_roots as db
from spirit_roots.data import PITY_THRESHOLD, get_tier_by_value
from spirit_roots.cultivation_bridge import describe_spirit_root_bonuses
from ui.embed import build_embed, error_embed
from ui.interaction_utils import safe_defer, safe_edit, safe_respond_or_followup

log = logging.getLogger("bot.cogs.admin_spirit_roots")


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """Ensure *dt* is UTC-aware regardless of whether MySQL returned a naive value."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt_ts(dt: datetime | None, style: str = "R") -> str:
    """Return a Discord timestamp string, or *'—'* if *dt* is None."""
    if dt is None:
        return "—"
    return f"<t:{int(_as_utc(dt).timestamp())}:{style}>"


def _pity_bar(current: int, threshold: int, width: int = 10) -> str:
    filled = min(round((current / threshold) * width), width)
    return f"{'█' * filled}{'░' * (width - filled)}  {current}/{threshold}"


def _tier_line(value: int) -> str:
    t = get_tier_by_value(value)
    return f"{t.emoji} **{t.name}** (Tier {value})"


def _is_bot_target(member: discord.Member, ctx: commands.Context) -> bool:
    return member.bot


# ---------------------------------------------------------------------------
# Context acknowledgement helper
# ---------------------------------------------------------------------------

async def _ack(ctx: commands.Context) -> None:
    """
    Defer a slash interaction or do nothing for prefix commands.
    Safe to call even if the interaction was already acknowledged.
    """
    if ctx.interaction:
        await safe_defer(ctx.interaction, ephemeral=True)


async def _send(ctx: commands.Context, **kwargs: Any) -> None:
    """
    Send an ephemeral response that works for both slash and prefix contexts.

    For slash commands this edits the deferred response (from _ack).
    For prefix commands it falls back to ctx.send.
    """
    if ctx.interaction:
        await safe_edit(ctx.interaction, **kwargs)
    else:
        # Prefix: strip ephemeral kwarg (not supported by ctx.send)
        kwargs.pop("ephemeral", None)
        # Unpack embed from kwargs if present so prefix also receives it
        await ctx.send(**kwargs)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AdminSpiritRoots(commands.Cog, name="Admin Spirit Roots"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /admin_root (group) ───────────────────────────────────────────────

    @commands.hybrid_group(name="admin_root", description="Admin Spirit Root management.")
    @commands.has_permissions(manage_guild=True)
    async def admin_root(self, ctx: commands.Context) -> None:
        """Invoking the group directly sends a help hint."""
        if ctx.invoked_subcommand is None:
            await _ack(ctx)
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="Admin Root",
                    description=(
                        "Available subcommands: `view`, `set`, `reset`, "
                        "`reset_pity`, `grant_spin`."
                    ),
                ),
            )

    # ── view ──────────────────────────────────────────────────────────────

    @admin_root.command(name="view", description="Inspect any player's full spirit root profile.")
    @commands.has_permissions(manage_guild=True)
    async def admin_view(self, ctx: commands.Context, member: discord.Member) -> None:
        await _ack(ctx)

        guild_id = ctx.guild.id if ctx.guild else 0
        record   = await db.get_spirit_root(member.id, guild_id)

        if record is None:
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="No Spirit Root",
                    description=f"{member.mention} has not yet awakened a Spirit Root.",
                ),
            )
            return

        # Cooldown
        cooldown_dt = await db.get_spin_cooldown(member.id)
        if cooldown_dt and _as_utc(cooldown_dt) > _now():
            remaining = int((_as_utc(cooldown_dt) - _now()).total_seconds())
            h, rem = divmod(remaining, 3600)
            m      = rem // 60
            cd_str = f"On cooldown — **{h}h {m}m** remaining"
        else:
            cd_str = "**Ready to spin ✅**"

        # Spin history
        history    = await db.get_spin_history(member.id, guild_id, limit=5)
        ICONS      = {"improved": "⬆️", "equal": "➡️", "protected": "🛡️"}
        hist_lines = []
        for entry in history:
            tier = get_tier_by_value(entry.rolled_value)
            icon = ICONS.get(entry.outcome, "❓")
            pity = " *(pity)*" if entry.pity_triggered else ""
            ts   = entry.spun_at.strftime("%m/%d %H:%M")
            hist_lines.append(
                f"`{ts}` {icon} {tier.emoji} {tier.name} — {entry.outcome}{pity}"
            )
        hist_text = "\n".join(hist_lines) if hist_lines else "*No spins yet.*"

        # Active bonuses for current root
        bonuses_text = describe_spirit_root_bonuses(record.current_value)

        await _send(
            ctx,
            embed=build_embed(
                ctx,
                title=f"🔍 {member.display_name}'s Spirit Root Profile",
                description=f"Full profile data for {member.mention}.",
                color=discord.Color.blurple(),
                fields=[
                    {
                        "name": "Current Root",
                        "value": _tier_line(record.current_value),
                        "inline": True,
                    },
                    {
                        "name": "Best Root",
                        "value": _tier_line(record.best_value),
                        "inline": True,
                    },
                    {
                        "name": "Total Spins",
                        "value": str(record.total_spins),
                        "inline": True,
                    },
                    {
                        "name": "Pity Counter",
                        "value": f"`{_pity_bar(record.pity_counter, PITY_THRESHOLD)}`",
                        "inline": False,
                    },
                    {
                        "name": "Spin Cooldown",
                        "value": cd_str,
                        "inline": False,
                    },
                    {
                        "name": "Active Bonuses",
                        "value": bonuses_text or "None",
                        "inline": False,
                    },
                    {
                        "name": "Last 5 Spins",
                        "value": hist_text,
                        "inline": False,
                    },
                    {
                        "name": "Acquired",
                        "value": _fmt_ts(record.acquired_at),
                        "inline": True,
                    },
                    {
                        "name": "Last Spin",
                        "value": _fmt_ts(record.last_spin_at),
                        "inline": True,
                    },
                ],
                show_footer=True,
            ),
        )

    # ── set ───────────────────────────────────────────────────────────────

    @admin_root.command(name="set", description="Force-set a player's spirit root value (1–5).")
    @commands.has_permissions(manage_guild=True)
    async def admin_set(
        self,
        ctx: commands.Context,
        member: discord.Member,
        value: int,
    ) -> None:
        await _ack(ctx)

        if _is_bot_target(member, ctx):
            await _send(
                ctx,
                embed=error_embed(ctx, title="Invalid Target", description="Bots cannot have Spirit Roots."),
            )
            return

        if not (1 <= value <= 5):
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="Invalid Value",
                    description="Root value must be between **1** and **5**.",
                ),
            )
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        record   = await db.get_spirit_root(member.id, guild_id)

        if record is None:
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="No Spirit Root",
                    description=(
                        f"{member.mention} has no spirit root yet. "
                        "They must use `/spin_root` first to initialise their record."
                    ),
                ),
            )
            return

        updated = await db.admin_set_root(member.id, guild_id, value)
        tier    = get_tier_by_value(value)

        log.warning(
            "admin_set  admin=%s  target=%s  guild=%s  new_value=%s",
            ctx.author.id, member.id, guild_id, value,
        )

        await _send(
            ctx,
            embed=build_embed(
                ctx,
                title="⚙️ Spirit Root Override",
                description=(
                    f"Set {member.mention}'s Spirit Root to "
                    f"{tier.emoji} **{tier.name}** (Tier {value})."
                ),
                color=discord.Colour(tier.colour),
                fields=[
                    {
                        "name": "New Current",
                        "value": _tier_line(updated.current_value),
                        "inline": True,
                    },
                    {
                        "name": "New Best",
                        "value": _tier_line(updated.best_value),
                        "inline": True,
                    },
                ],
                show_footer=True,
            ),
        )

    # ── reset ─────────────────────────────────────────────────────────────

    @admin_root.command(
        name="reset",
        description="⚠️ Permanently wipe a player's spirit root data. Requires Administrator.",
    )
    @commands.has_permissions(administrator=True)
    async def admin_reset(self, ctx: commands.Context, member: discord.Member) -> None:
        await _ack(ctx)

        if _is_bot_target(member, ctx):
            await _send(
                ctx,
                embed=error_embed(ctx, title="Invalid Target", description="Bots cannot have Spirit Roots."),
            )
            return

        guild_id = ctx.guild.id if ctx.guild else 0

        # Guard: nothing to reset
        record = await db.get_spirit_root(member.id, guild_id)
        if record is None:
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="No Record Found",
                    description=f"{member.mention} has no spirit root record to reset.",
                ),
            )
            return

        await db.admin_reset_root(member.id, guild_id)

        log.warning(
            "admin_reset  admin=%s  target=%s  guild=%s",
            ctx.author.id, member.id, guild_id,
        )

        await _send(
            ctx,
            embed=build_embed(
                ctx,
                title="🗑️ Spirit Root Reset",
                description=(
                    f"All spirit root data for {member.mention} has been permanently wiped.\n"
                    "They may use `/spin_root` to receive a new starting root."
                ),
                color=discord.Color.red(),
                show_footer=True,
            ),
        )

    # ── reset_pity ────────────────────────────────────────────────────────

    @admin_root.command(name="reset_pity", description="Zero out a player's pity counter.")
    @commands.has_permissions(manage_guild=True)
    async def admin_reset_pity(self, ctx: commands.Context, member: discord.Member) -> None:
        await _ack(ctx)

        if _is_bot_target(member, ctx):
            await _send(
                ctx,
                embed=error_embed(ctx, title="Invalid Target", description="Bots cannot have Spirit Roots."),
            )
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        record   = await db.get_spirit_root(member.id, guild_id)

        if record is None:
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="No Spirit Root",
                    description=f"{member.mention} has no spirit root record.",
                ),
            )
            return

        old_pity = record.pity_counter
        updated  = await db.admin_reset_pity(member.id, guild_id)

        log.info(
            "admin_reset_pity  admin=%s  target=%s  guild=%s  was=%s",
            ctx.author.id, member.id, guild_id, old_pity,
        )

        await _send(
            ctx,
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
        )

    # ── grant_spin ────────────────────────────────────────────────────────

    @admin_root.command(
        name="grant_spin",
        description="Clear a player's spin cooldown so they can spin immediately.",
    )
    @commands.has_permissions(manage_guild=True)
    async def admin_grant_spin(self, ctx: commands.Context, member: discord.Member) -> None:
        await _ack(ctx)

        if _is_bot_target(member, ctx):
            await _send(
                ctx,
                embed=error_embed(ctx, title="Invalid Target", description="Bots cannot spin."),
            )
            return

        # Uses the real db helper — clean DELETE, no 1-second TTL hack
        await db.clear_spin_cooldown(member.id)

        log.info(
            "admin_grant_spin  admin=%s  target=%s",
            ctx.author.id, member.id,
        )

        await _send(
            ctx,
            embed=build_embed(
                ctx,
                title="🎟️ Free Spin Granted",
                description=(
                    f"Cleared {member.mention}'s spin cooldown.\n"
                    "They may use `/spin_root` immediately."
                ),
                color=discord.Color.green(),
                show_footer=True,
            ),
        )

    # ── Cog-level error handler ───────────────────────────────────────────
    # This fires for ALL subcommands, not just the group root.
    # The old @admin_root.error only caught group-level invocations.

    async def cog_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        # Unwrap hybrid/app command wrappers
        original = getattr(error, "original", error)

        if isinstance(original, commands.MissingPermissions):
            required = ", ".join(
                f"**{p.replace('_', ' ').title()}**"
                for p in original.missing_permissions
            )
            await _ack(ctx)
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="Missing Permissions",
                    description=f"You need {required} to use this command.",
                ),
            )
            return

        if isinstance(original, commands.MemberNotFound):
            await _ack(ctx)
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="Member Not Found",
                    description=f"Could not find a member matching `{original.argument}`.",
                ),
            )
            return

        if isinstance(original, commands.BadArgument):
            await _ack(ctx)
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="Invalid Argument",
                    description=str(original),
                ),
            )
            return

        # Unexpected — log full traceback, show generic message
        log.exception(
            "Unhandled error in AdminSpiritRoots  cmd=%s  user=%s",
            getattr(ctx.command, "qualified_name", "?"),
            ctx.author.id,
            exc_info=original,
        )
        try:
            await _ack(ctx)
            await _send(
                ctx,
                embed=error_embed(
                    ctx,
                    title="Unexpected Error",
                    description="Something went wrong. The error has been logged.",
                ),
            )
        except Exception:
            pass  # absolute last resort — never let the error handler itself crash


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminSpiritRoots(bot))
    log.info("Cog loaded  » AdminSpiritRoots")