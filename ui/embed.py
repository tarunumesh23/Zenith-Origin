from __future__ import annotations

from datetime import datetime
from typing import TypedDict, Union

import discord
import discord.ext.commands
import pytz

IST = pytz.timezone("Asia/Kolkata")

# build_embed accepts either a command Context or a raw Interaction so that
# session.py and other non-command code can build embeds without a Context.
_CtxOrInteraction = Union[discord.ext.commands.Context, discord.Interaction]


class EmbedField(TypedDict, total=False):
    name: str
    value: str
    inline: bool


def _author_info(ctx: _CtxOrInteraction) -> tuple[str, str]:
    """Return (display_name, avatar_url) from either Context or Interaction."""
    if isinstance(ctx, discord.ext.commands.Context):
        user = ctx.author
    else:
        user = ctx.user
    return user.display_name, user.display_avatar.url


def build_embed(
    ctx: _CtxOrInteraction,
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
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(IST) if show_timestamp else None,
    )

    for f in fields or []:
        embed.add_field(
            name=f.get("name", "\u200b"),
            value=f.get("value", "\u200b"),
            inline=f.get("inline", False),
        )

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)

    if show_footer:
        name, avatar = _author_info(ctx)
        embed.set_footer(text=f"Requested by {name}", icon_url=avatar)

    return embed


# ---------------------------------------------------------------------------
# Shorthand helpers
# ---------------------------------------------------------------------------

def success_embed(ctx: _CtxOrInteraction, description: str, title: str = "Success") -> discord.Embed:
    return build_embed(ctx, title=f"✅  {title}", description=description, color=discord.Color.green())


def error_embed(ctx: _CtxOrInteraction, description: str, title: str = "Error") -> discord.Embed:
    return build_embed(ctx, title=f"❌  {title}", description=description, color=discord.Color.red())


def info_embed(ctx: _CtxOrInteraction, description: str, title: str = "Info") -> discord.Embed:
    return build_embed(ctx, title=f"ℹ️  {title}", description=description, color=discord.Color.blurple())


def warning_embed(ctx: _CtxOrInteraction, description: str, title: str = "Warning") -> discord.Embed:
    return build_embed(ctx, title=f"⚠️  {title}", description=description, color=discord.Color.yellow())