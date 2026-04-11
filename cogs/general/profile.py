"""
cogs/general/profile.py
~~~~~~~~~~~~~~~~~~~~~~~~
/profile — Rich cultivator profile card.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from cultivation.constants import (
    AFFINITY_DISPLAY,
    REALM_DISPLAY,
    get_reputation_title,
    compute_current_qi,
)
from db import cultivators as db
from db import spirit_roots as spirit_roots_db
from db import talent as talent_db
from spirit_roots.data import get_tier_by_value
from ui.embed import build_embed, error_embed
from ui.interaction_utils import safe_defer

log = logging.getLogger("bot.cogs.profile")

# ── Affinity accent colours ───────────────────────────────────────────────────
_AFFINITY_COLOURS: dict[str, discord.Color] = {
    "fire":      discord.Color(0xE8472A),
    "water":     discord.Color(0x3A8FD4),
    "lightning": discord.Color(0xF5C518),
    "wood":      discord.Color(0x4CAF50),
    "earth":     discord.Color(0xA0785A),
}
_DEFAULT_COLOUR = discord.Color(0x7B68EE)

# ── Qi progress bar ───────────────────────────────────────────────────────────
_BAR_WIDTH = 18


def _qi_bar(current: int, threshold: int) -> str:
    pct    = min(current / threshold, 1.0) if threshold else 0.0
    filled = round(pct * _BAR_WIDTH)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


# ── Reputation badge ──────────────────────────────────────────────────────────
def _rep_badge(rep: int) -> str:
    if   rep >= 800: return "👑"
    elif rep >= 500: return "🔱"
    elif rep >= 300: return "⚔️"
    elif rep >= 150: return "🗡️"
    elif rep >= 75:  return "🌀"
    elif rep >= 30:  return "🌱"
    elif rep >= 0:   return "🪨"
    else:            return "💀"


# ── Spirit Root line ──────────────────────────────────────────────────────────
def _root_line(current_value: int) -> str:
    try:
        tier = get_tier_by_value(current_value)
        return f"{tier.emoji} **{tier.name}** `Tier {tier.value}`"
    except Exception:
        return "*Unknown*"


# ── Talent line ───────────────────────────────────────────────────────────────
def _talent_line(active_talent) -> str:
    if active_talent is None:
        return "*None — use `/use_spin` to awaken one*"
    try:
        from talent.constants import RARITIES
        rarity_data = RARITIES.get(active_talent.rarity, {})
        emoji       = rarity_data.get("emoji", "")
        stage_stars = ["", " ✦", " ✦✦"][active_talent.evolution_stage]
        flags       = ("" if not active_talent.is_corrupted else " ☠️") + ("" if not active_talent.is_locked else " 🔒")
        return (
            f"{emoji} **{active_talent.name}**{stage_stars}{flags}\n"
            f"╰ `{active_talent.rarity}` · ×{active_talent.multiplier:.2f}"
        )
    except Exception:
        return "*Error loading talent*"


# ── Profile embed builder ─────────────────────────────────────────────────────
async def _build_profile_embed(
    ctx: commands.Context,
    target: discord.Member | discord.User,
    row: dict,
    guild_id: int,
) -> discord.Embed:

    affinity    = row.get("affinity")
    colour      = _AFFINITY_COLOURS.get(affinity, _DEFAULT_COLOUR)
    realm_label = REALM_DISPLAY.get(row["realm"], row["realm"].replace("_", " ").title())
    aff_label   = AFFINITY_DISPLAY.get(affinity, "✨ Not Chosen") if affinity else "✨ Not Chosen"
    rep         = row.get("reputation", 0)
    rep_title   = get_reputation_title(rep)
    rep_badge   = _rep_badge(rep)

    # ── Live Qi ───────────────────────────────────────────────────────
    try:
        current_qi, _ = compute_current_qi(
            qi_stored         = row["qi"],
            qi_threshold      = row["qi_threshold"],
            last_updated      = row.get("last_updated"),
            affinity          = affinity,
            closed_cult_until = row.get("closed_cult_until"),
        )
        current_qi = int(current_qi)
    except Exception:
        current_qi = int(row.get("qi", 0))

    threshold = row["qi_threshold"]
    pct       = min(current_qi / threshold, 1.0) if threshold else 0.0
    bar       = _qi_bar(current_qi, threshold)

    # ── Status notes ──────────────────────────────────────────────────
    status_notes = ""
    if row.get("in_tribulation"):
        status_notes += "\n⚡ **Tribulation Pending** — use `/breakthrough`!"

    closed_until = row.get("closed_cult_until")
    if closed_until:
        from cultivation.constants import _as_utc
        if _as_utc(closed_until) > datetime.now(timezone.utc):
            status_notes += f"\n🔒 In seclusion until <t:{int(_as_utc(closed_until).timestamp())}:R>"

    # ── Spirit Root & Talent ──────────────────────────────────────────
    root_text   = "*None*"
    talent_text = "*None — use `/use_spin` to awaken one*"

    try:
        root_record = await spirit_roots_db.get_spirit_root(target.id, guild_id)
        if root_record:
            root_text = _root_line(root_record.current_value)
    except Exception:
        log.warning("Profile » spirit root load failed discord_id=%s", target.id)

    try:
        player_data = await talent_db.get_player_talent_data(target.id, guild_id)
        talent_text = _talent_line(player_data.active_talent if player_data else None)
    except Exception:
        log.warning("Profile » talent load failed discord_id=%s", target.id)

    # ── Combat stats ──────────────────────────────────────────────────
    wins   = row.get("total_wins",      0)
    losses = row.get("total_losses",    0)
    fled   = row.get("fled_challenges", 0)
    total  = wins + losses + fled
    wr_str = f"{wins / total * 100:.0f}%" if total else "—"

    # ── Fields ────────────────────────────────────────────────────────
    fields = [
        {
            "name":   "⚙️ ── CULTIVATION ──────────────────",
            "value":  (
                f"**Realm:** {realm_label}  **·**  **Stage:** `{row['stage']}`\n"
                f"`{bar}` **{pct*100:.1f}%**\n"
                f"**Qi:** `{current_qi:,}` / `{threshold:,}`"
                f"{status_notes}"
            ),
            "inline": False,
        },
        {
            "name":   "🌿 Spirit Root",
            "value":  root_text,
            "inline": True,
        },
        {
            "name":   "🌟 Active Talent",
            "value":  talent_text,
            "inline": True,
        },
        {
            "name":   "⚔️ ── COMBAT RECORD ─────────────────",
            "value":  (
                f"🏆 **{wins}** Wins  ·  💀 **{losses}** Losses  ·  🏃 **{fled}** Fled\n"
                f"Win Rate: `{wr_str}`  ·  Reputation: `{rep:+d}`"
            ),
            "inline": False,
        },
    ]

    embed = build_embed(
        ctx,
        title=f"{rep_badge} {target.display_name}'s Cultivation Record",
        description=f"{aff_label}  ·  *{rep_title}*",
        color=colour,
        fields=fields,
        thumbnail=str(target.display_avatar.url),
        show_footer=True,
        show_timestamp=True,
    )

    return embed


# ── Cog ───────────────────────────────────────────────────────────────────────

class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="profile",
        description="View your (or another cultivator's) cultivation record",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def profile(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction)

        target   = member or ctx.author
        guild_id = ctx.guild.id if ctx.guild else 0

        try:
            row = await db.get_cultivator(target.id)
        except Exception:
            log.exception("Profile » DB fetch failed discord_id=%s", target.id)
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Database Error",
                    description="Could not load profile. Try again later.",
                ),
                ephemeral=True,
            )
            return

        if row is None:
            who = "You have" if target == ctx.author else f"**{target.display_name}** has"
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="⛩️ Not a Cultivator",
                    description=f"{who} not yet walked the Path. Use `z!start` to begin.",
                ),
                ephemeral=True,
            )
            return

        embed = await _build_profile_embed(ctx, target, row, guild_id)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))