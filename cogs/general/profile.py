from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from cultivation.constants import (
    AFFINITY_DISPLAY,
    REALM_DISPLAY,
    get_reputation_title,
)
from db.cultivators import get_cultivator
from ui.embed import build_embed, error_embed

log = logging.getLogger("bot.cogs.profile")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cultivation_age(registered_at: datetime) -> str:
    """
    2 real hours  = 1 cultivation year
    1 real minute = 1 cultivation month
    1 real second = 1 cultivation day
    """
    if registered_at.tzinfo is None:
        registered_at = registered_at.replace(tzinfo=timezone.utc)

    elapsed       = datetime.now(timezone.utc) - registered_at
    total_seconds = max(int(elapsed.total_seconds()), 0)

    years   = total_seconds // 7200
    months  = (total_seconds % 7200) // 60
    days    = total_seconds % 60

    parts: list[str] = []
    if years:
        parts.append(f"**{years}** year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"**{months}** month{'s' if months != 1 else ''}")
    if days:
        parts.append(f"**{days}** day{'s' if days != 1 else ''}")

    return ", ".join(parts) if parts else "**0** days"


def _qi_bar(qi: int, threshold: int, length: int = 10) -> str:
    """Visual Qi progress bar."""
    filled = round((qi / threshold) * length) if threshold else 0
    return "█" * filled + "░" * (length - filled)


def _build_profile_embed(
    ctx: commands.Context,
    target: discord.Member,
    row: dict,
) -> discord.Embed:
    age      = _cultivation_age(row["registered_at"])
    realm    = REALM_DISPLAY.get(row["realm"], row["realm"])
    affinity = AFFINITY_DISPLAY.get(row["affinity"], "None") if row["affinity"] else "*Not chosen*"
    qi       = row["qi"]
    thresh   = row["qi_threshold"]
    bar      = _qi_bar(qi, thresh)
    rep      = row["reputation"]
    title_   = get_reputation_title(rep)

    tribulation_note = ""
    if row["in_tribulation"]:
        tribulation_note = "\n\n⚡ *Tribulation looms. Use `/breakthrough` before your energy destabilises.*"

    closed_note = ""
    if row["closed_cult_until"]:
        from datetime import timezone as tz
        until = row["closed_cult_until"]
        if until.tzinfo is None:
            until = until.replace(tzinfo=tz.utc)
        from datetime import datetime as dt
        if until > dt.now(tz.utc):
            closed_note = f"\n🔒 *In closed cultivation until <t:{int(until.timestamp())}:t>*"

    embed = build_embed(
        ctx,
        title=f"⚡ {target.display_name}",
        description=(
            f"*A soul walking the endless Path of Cultivation.*"
            f"{tribulation_note}{closed_note}"
        ),
        color=discord.Color.dark_teal(),
        fields=[
            {
                "name": "👤 Cultivator",
                "value": f"`{target.display_name}`",
                "inline": True,
            },
            {
                "name": "🌀 Realm & Stage",
                "value": f"`{realm} — Stage {row['stage']}`",
                "inline": True,
            },
            {
                "name": "⚗️ Affinity",
                "value": affinity,
                "inline": True,
            },
            {
                "name": "💠 Qi",
                "value": f"`{qi} / {thresh}`\n{bar}",
                "inline": True,
            },
            {
                "name": "🏆 Reputation",
                "value": f"`{rep}` — *{title_}*",
                "inline": True,
            },
            {
                "name": "⚔️ Record",
                "value": f"`{row['total_wins']}W / {row['total_losses']}L`",
                "inline": True,
            },
            {
                "name": "🕰️ Cultivation Age",
                "value": age,
                "inline": True,
            },
        ],
        thumbnail=target.display_avatar.url,
        show_footer=True,
        show_timestamp=True,
    )

    return embed


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="profile",
        description="View your cultivation profile or another cultivator's",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def profile(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        target = member or ctx.author

        if ctx.interaction:
            await ctx.interaction.response.defer()

        try:
            row = await get_cultivator(target.id)
        except Exception:
            log.exception("Profile » DB fetch failed for discord_id=%s", target.id)
            await ctx.send(
                embed=error_embed(ctx, title="Database Error",
                                  description="Could not fetch profile data. Please try again later."),
                ephemeral=True,
            )
            return

        if row is None:
            desc = (
                "You have not yet walked the Path. Use `z!start` to begin your trial."
                if target == ctx.author
                else f"{target.mention} has not yet walked the Path."
            )
            await ctx.send(
                embed=error_embed(ctx, title="Not a Cultivator", description=desc),
                ephemeral=True,
            )
            return

        embed = _build_profile_embed(ctx, target, row)
        log.debug("Profile » %s viewed profile of %s", ctx.author, target)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))