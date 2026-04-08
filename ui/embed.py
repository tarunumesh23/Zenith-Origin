from __future__ import annotations

from datetime import datetime
from typing import TypedDict

import discord
import discord.ext.commands
import pytz

IST = pytz.timezone("Asia/Kolkata")


class EmbedField(TypedDict, total=False):
    name: str
    value: str
    inline: bool


def build_embed(
    ctx: discord.ext.commands.Context,
    *,
    title: str | None = None,
    description: str | None = None,
    color: discord.Color = discord.Color.blurple(),
    fields: list[EmbedField] | None = None,
    thumbnail: str | None = None,
    image: str | None = None,
    show_footer: bool = True,
    show_timestamp: bool = True,
) -> discord.Embed:
    """
    Build a reusable Discord embed.

    Parameters
    ----------
    ctx             : Command context (used for requester info in footer)
    title           : Embed title
    description     : Embed description
    color           : Embed color (default: blurple)
    fields          : List of EmbedField dicts — keys: name, value, inline (optional)
    thumbnail       : URL for thumbnail image
    image           : URL for large image
    show_footer     : Whether to show requester footer (default: True)
    show_timestamp  : Whether to show IST timestamp (default: True)
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(IST) if show_timestamp else None,
    )

    for field in fields or []:
        embed.add_field(
            name=field.get("name", "\u200b"),
            value=field.get("value", "\u200b"),
            inline=field.get("inline", False),
        )

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    if image:
        embed.set_image(url=image)

    if show_footer:
        embed.set_footer(
            text=f"Requested by {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url,
        )

    return embed


# ---------------------------------------------------------------------------
# Shorthand helpers
# ---------------------------------------------------------------------------

def success_embed(ctx: discord.ext.commands.Context, description: str, title: str = "Success") -> discord.Embed:
    return build_embed(ctx, title=f"✅  {title}", description=description, color=discord.Color.green())


def error_embed(ctx: discord.ext.commands.Context, description: str, title: str = "Error") -> discord.Embed:
    return build_embed(ctx, title=f"❌  {title}", description=description, color=discord.Color.red())


def info_embed(ctx: discord.ext.commands.Context, description: str, title: str = "Info") -> discord.Embed:
    return build_embed(ctx, title=f"ℹ️  {title}", description=description, color=discord.Color.blurple())


def warning_embed(ctx: discord.ext.commands.Context, description: str, title: str = "Warning") -> discord.Embed:
    return build_embed(ctx, title=f"⚠️  {title}", description=description, color=discord.Color.yellow())