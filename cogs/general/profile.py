from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from db.cultivators import get_cultivator
from ui.embed import build_embed, error_embed

log = logging.getLogger("bot.cogs.profile")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cultivation_age(registered_at: datetime) -> str:
    """
    1 real hour   = 1 cultivation year
    1 real minute = 1 cultivation month
    1 real second = 1 cultivation day
    """
    if registered_at.tzinfo is None:
        registered_at = registered_at.replace(tzinfo=timezone.utc)

    elapsed       = datetime.now(timezone.utc) - registered_at
    total_seconds = max(int(elapsed.total_seconds()), 0)

    years   = total_seconds // 3600
    months  = (total_seconds % 3600) // 60
    days    = total_seconds % 60

    parts: list[str] = []
    if years:
        parts.append(f"**{years}** year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"**{months}** month{'s' if months != 1 else ''}")
    if days:
        parts.append(f"**{days}** day{'s' if days != 1 else ''}")

    return ", ".join(parts) if parts else "**0** days"


def _build_profile_embed(
    ctx: commands.Context,
    target: discord.Member,
    row: dict,
) -> discord.Embed:
    age = _cultivation_age(row["registered_at"])

    embed = build_embed(
        ctx,
        title=f"⚡ {target.display_name}",
        description="*A soul walking the endless Path of Cultivation.*",
        color=discord.Color.dark_teal(),
        fields=[
            {
                "name": "👤 Name",
                "value": f"`{target.display_name}`",
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

        # Defer for slash command responsiveness
        if ctx.interaction:
            await ctx.interaction.response.defer()

        # Fetch from DB
        try:
            row = await get_cultivator(target.id)
        except Exception:
            log.exception("Profile » DB fetch failed for discord_id=%s", target.id)
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Database Error",
                    description="Could not fetch profile data. Please try again later.",
                ),
                ephemeral=True,
            )
            return

        # Not registered
        if row is None:
            if target == ctx.author:
                desc = "You have not yet walked the Path. Use `z!start` to begin your trial."
            else:
                desc = f"{target.mention} has not yet walked the Path."

            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Not a Cultivator",
                    description=desc,
                ),
                ephemeral=True,
            )
            return

        # Build and send embed
        try:
            embed = _build_profile_embed(ctx, target, row)
        except Exception:
            log.exception("Profile » Failed to build embed for discord_id=%s", target.id)
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Error",
                    description="Something went wrong while building the profile. Please try again.",
                ),
                ephemeral=True,
            )
            return

        log.info("Profile » %s viewed profile of %s", ctx.author, target)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))