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
from db import training as training_db
from spirit_roots.data import get_tier_by_value
from training.constants import STAT_CAPS, TIER_DISPLAY
from ui.embed import build_embed, error_embed
from ui.interaction_utils import safe_defer

log = logging.getLogger("bot.cogs.profile")

_AFFINITY_COLOURS = {
    "fire":      discord.Color(0xFF4C4C),
    "water":     discord.Color(0x4DA6FF),
    "lightning": discord.Color(0xFFD93D),
    "wood":      discord.Color(0x4CAF50),
    "earth":     discord.Color(0xC2A679),
}
_DEFAULT_COLOUR = discord.Color.blurple()

_BAR_WIDTH = 18


def _qi_bar(current: int, threshold: int) -> str:
    pct = min(current / threshold, 1.0) if threshold else 0.0
    filled = round(pct * _BAR_WIDTH)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _stat_bar(value: float, cap: int, width: int = 8) -> str:
    pct = min(value / max(cap, 1), 1.0)
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


def _rep_badge(rep: int) -> str:
    if rep >= 800: return "👑"
    if rep >= 500: return "🔱"
    if rep >= 300: return "⚔️"
    if rep >= 150: return "🗡️"
    if rep >= 75:  return "🌀"
    if rep >= 30:  return "🌱"
    if rep >= 0:   return "🪨"
    return "💀"


def _root_line(value: int) -> str:
    try:
        tier = get_tier_by_value(value)
        return f"{tier.emoji} **{tier.name}** `Tier {tier.value}`"
    except Exception:
        return "*Unknown*"


def _talent_line(talent) -> str:
    if talent is None:
        return "*None — use `/use_spin` to awaken one*"
    try:
        from talent.constants import RARITIES
        r = RARITIES.get(talent.rarity, {})
        emoji = r.get("emoji", "")
        stars = ["", " ✦", " ✦✦"][talent.evolution_stage]
        flags = ("" if not talent.is_corrupted else " ☠️") + ("" if not talent.is_locked else " 🔒")
        return (
            f"{emoji} **{talent.name}**{stars}{flags}\n"
            f"╰ `{talent.rarity}` · ×{talent.multiplier:.2f}"
        )
    except Exception:
        return "*Error loading talent*"


def _training_block(t) -> str:
    if t is None:
        return "*No training record yet — use `/train` to begin.*"

    atk_cap = STAT_CAPS[t.tier_body]["atk"]
    def_cap = STAT_CAPS[t.tier_body]["def"]
    spe_cap = STAT_CAPS[t.tier_flow]["spe"]
    eva_cap = STAT_CAPS[t.tier_flow]["eva"]
    crit_cap = STAT_CAPS[t.tier_killing]["crit_chance"]
    crit_dmg_cap = STAT_CAPS[t.tier_killing]["crit_dmg"]

    def line(e, l, v, c):
        return f"{e} **{l}** `{_stat_bar(v, c)}` `{int(v)}/{c}`"

    warnings = []
    if t.injury_body_remaining > 0:
        warnings.append(f"🩹 Body `{t.injury_body_remaining}s`")
    if t.injury_flow_remaining > 0:
        warnings.append(f"🩹 Flow `{t.injury_flow_remaining}s`")
    if t.injury_killing_remaining > 0:
        warnings.append(f"🩹 Killing `{t.injury_killing_remaining}s`")
    if t.cascade_lock > 0:
        warnings.append(f"⚠️ Cascade `{t.cascade_lock}s`")

    warn = "  ".join(warnings)
    power = int(t.atk + t.def_ + t.spe + t.eva + t.crit_chance + t.crit_dmg)

    rows = [
        f"**Total Power** `{power}` {warn}",
        f"🩸 {TIER_DISPLAY[t.tier_body]}",
        line("⚔️", "ATK", t.atk, atk_cap),
        line("🛡️", "DEF", t.def_, def_cap),
        f"🌬️ {TIER_DISPLAY[t.tier_flow]}",
        line("💨", "SPE", t.spe, spe_cap),
        line("🌀", "EVA", t.eva, eva_cap),
        f"🔥 {TIER_DISPLAY[t.tier_killing]}",
        line("🎯", "CRIT%", t.crit_chance, crit_cap),
        line("💥", "CRIT DMG", t.crit_dmg, crit_dmg_cap),
    ]

    if t.passive_tags:
        rows.append("🧬 " + "  ".join(f"`{x}`" for x in t.passive_tags))

    return "\n".join(rows)


async def _build_profile_embed(ctx, target, row, guild_id):
    affinity = row.get("affinity")
    colour = _AFFINITY_COLOURS.get(affinity, _DEFAULT_COLOUR)

    realm = REALM_DISPLAY.get(row["realm"], row["realm"])
    aff = AFFINITY_DISPLAY.get(affinity, "✨ Not Chosen") if affinity else "✨ Not Chosen"

    rep = row.get("reputation", 0)
    rep_title = get_reputation_title(rep)

    try:
        qi, _ = compute_current_qi(
            qi_stored=row["qi"],
            qi_threshold=row["qi_threshold"],
            last_updated=row.get("last_updated"),
            affinity=affinity,
            closed_cult_until=row.get("closed_cult_until"),
        )
        qi = int(qi)
    except Exception:
        qi = int(row.get("qi", 0))

    threshold = row["qi_threshold"]
    pct = min(qi / threshold, 1.0) if threshold else 0
    bar = _qi_bar(qi, threshold)

    notes = ""
    if row.get("in_tribulation"):
        notes += "\n⚡ Tribulation Pending"

    root = "*None*"
    talent = "*None*"
    training = "*No training data*"

    try:
        r = await spirit_roots_db.get_spirit_root(target.id, guild_id)
        if r:
            root = _root_line(r.current_value)
    except Exception:
        pass

    try:
        t = await talent_db.get_player_talent_data(target.id, guild_id)
        talent = _talent_line(t.active_talent if t else None)
    except Exception:
        pass

    try:
        tr = await training_db.get_training_stats(target.id, guild_id)
        training = _training_block(tr)
    except Exception:
        pass

    wins = row.get("total_wins", 0)
    losses = row.get("total_losses", 0)
    fled = row.get("fled_challenges", 0)
    total = wins + losses + fled
    wr = f"{wins/total*100:.0f}%" if total else "—"

    fields = [
        {
            "name": "⚙️ Cultivation",
            "value": (
                f"{realm} · Stage `{row['stage']}`\n"
                f"`{bar}` {pct*100:.1f}%\n"
                f"`{qi:,} / {threshold:,}`{notes}"
            ),
            "inline": False,
        },
        {"name": "🌿 Root", "value": root, "inline": True},
        {"name": "🌟 Talent", "value": talent, "inline": True},
        {"name": "⚔️ Training", "value": training, "inline": False},
        {
            "name": "🏟️ Record",
            "value": (
                f"🏆 {wins} · 💀 {losses} · 🏃 {fled}\n"
                f"WR `{wr}` · Rep `{rep:+d}`"
            ),
            "inline": False,
        },
    ]

    embed = build_embed(
        ctx,
        title=f"{_rep_badge(rep)} {target.display_name}",
        description=f"{aff} · *{rep_title}*",
        color=colour,
        fields=fields,
        thumbnail=str(target.display_avatar.url),
        show_footer=True,
        show_timestamp=True,
    )

    return embed


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="profile", description="View cultivation profile")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def profile(self, ctx, member: discord.Member | None = None):
        if ctx.interaction:
            await safe_defer(ctx.interaction)

        target = member or ctx.author
        gid = ctx.guild.id if ctx.guild else 0

        try:
            row = await db.get_cultivator(target.id)
        except Exception:
            await ctx.send(embed=error_embed(ctx, title="Error", description="Try again"), ephemeral=True)
            return

        if row is None:
            await ctx.send(embed=error_embed(ctx, title="Not a cultivator"), ephemeral=True)
            return

        embed = await _build_profile_embed(ctx, target, row, gid)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Profile(bot))