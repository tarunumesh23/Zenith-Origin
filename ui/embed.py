from __future__ import annotations

from datetime import datetime
from typing import Final, TypeAlias, TypedDict

import discord
import discord.ext.commands
import pytz

IST: Final = pytz.timezone("Asia/Kolkata")

# Unified type for anything that carries a user (Context or Interaction).
CtxOrInteraction: TypeAlias = discord.ext.commands.Context | discord.Interaction


class EmbedField(TypedDict, total=False):
    name: str
    value: str
    inline: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _author_info(ctx: CtxOrInteraction) -> tuple[str, str]:
    """Return ``(display_name, avatar_url)`` from either a Context or Interaction."""
    user = ctx.author if isinstance(ctx, discord.ext.commands.Context) else ctx.user
    return user.display_name, user.display_avatar.url


def _now_ist() -> datetime:
    return datetime.now(IST)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_embed(
    ctx: CtxOrInteraction,
    *,
    title: str | None = None,
    description: str | None = None,
    color: discord.Color = discord.Color.blurple(),
    fields: list[EmbedField] | None = None,
    thumbnail: str | None = None,
    image: str | None = None,
    show_footer: bool = True,
    show_timestamp: bool = True,
    url: str | None = None,
) -> discord.Embed:
    """
    Build a consistently styled :class:`discord.Embed`.

    Parameters
    ----------
    ctx:
        The originating ``Context`` or ``Interaction`` — used to populate the footer.
    title:
        Embed title. Accepts ``None`` to omit.
    description:
        Embed body text.
    color:
        Embed accent color. Defaults to blurple.
    fields:
        Optional list of ``EmbedField`` dicts to attach.
    thumbnail:
        URL for the small top-right thumbnail image.
    image:
        URL for the large bottom image.
    show_footer:
        When ``True`` (default) appends "Requested by <name>" footer.
    show_timestamp:
        When ``True`` (default) stamps the embed with the current IST time.
    url:
        Optional hyperlink on the embed title.
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=_now_ist() if show_timestamp else None,
        url=url,
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

def success_embed(
    ctx: CtxOrInteraction,
    description: str,
    title: str = "Success",
    **kwargs,
) -> discord.Embed:
    """Green embed for successful operations."""
    return build_embed(
        ctx, title=f"✅  {title}", description=description,
        color=discord.Color.green(), **kwargs,
    )


def error_embed(
    ctx: CtxOrInteraction,
    description: str,
    title: str = "Error",
    **kwargs,
) -> discord.Embed:
    """Red embed for errors or failures."""
    return build_embed(
        ctx, title=f"❌  {title}", description=description,
        color=discord.Color.red(), **kwargs,
    )


def info_embed(
    ctx: CtxOrInteraction,
    description: str,
    title: str = "Info",
    **kwargs,
) -> discord.Embed:
    """Blurple embed for neutral information."""
    return build_embed(
        ctx, title=f"ℹ️  {title}", description=description,
        color=discord.Color.blurple(), **kwargs,
    )


def warning_embed(
    ctx: CtxOrInteraction,
    description: str,
    title: str = "Warning",
    **kwargs,
) -> discord.Embed:
    """Yellow embed for non-fatal warnings."""
    return build_embed(
        ctx, title=f"⚠️  {title}", description=description,
        color=discord.Color.yellow(), **kwargs,
    )


def loading_embed(
    ctx: CtxOrInteraction,
    description: str = "Please wait…",
    title: str = "Loading",
    **kwargs,
) -> discord.Embed:
    """Grey embed for in-progress operations. Swap out once the task completes."""
    return build_embed(
        ctx, title=f"⏳  {title}", description=description,
        color=discord.Color.light_grey(), **kwargs,
    )