from __future__ import annotations

from datetime import datetime
from typing import Final, TypeAlias, TypedDict

import discord
import discord.ext.commands
import pytz

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IST: Final = pytz.timezone("Asia/Kolkata")

CtxOrInteraction: TypeAlias = discord.ext.commands.Context | discord.Interaction


class EmbedField(TypedDict, total=False):
    name: str
    value: str
    inline: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _author_info(ctx: CtxOrInteraction) -> tuple[str, str]:
    """Return (display_name, avatar_url) from Context or Interaction."""
    user = ctx.author if isinstance(ctx, discord.ext.commands.Context) else ctx.user
    return user.display_name, user.display_avatar.url


def _now_ist() -> datetime:
    return datetime.now(IST)


# ---------------------------------------------------------------------------
# UI helpers (clean + reusable)
# ---------------------------------------------------------------------------

def section(title: str, content: str) -> str:
    """Bold section title with content."""
    return f"**{title}**\n{content}"


def quote(text: str) -> str:
    """Blockquote style text."""
    return f"> {text}"


def stat(name: str, value: str | int) -> str:
    """Single stat line."""
    return f"• {name}: `{value}`"


def spacer() -> EmbedField:
    """Empty field spacer."""
    return {"name": "\u200b", "value": "\u200b", "inline": False}


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
    """Build a clean, consistent embed."""

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=_now_ist() if show_timestamp else None,
        url=url,
    )

    # Fields
    if fields:
        for field in fields:
            embed.add_field(
                name=field.get("name", "\u200b"),
                value=field.get("value", "\u200b"),
                inline=field.get("inline", False),
            )

    # Media
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    if image:
        embed.set_image(url=image)

    # Footer
    if show_footer:
        name, avatar = _author_info(ctx)
        embed.set_footer(
            text=f"{name} • Cultivation",
            icon_url=avatar
        )

    return embed


# ---------------------------------------------------------------------------
# Presets (clean + consistent)
# ---------------------------------------------------------------------------

def success_embed(
    ctx: CtxOrInteraction,
    description: str,
    title: str = "Success",
    **kwargs,
) -> discord.Embed:
    return build_embed(
        ctx,
        title=f"🌿 {title}",
        description=description,
        color=discord.Color.green(),
        **kwargs,
    )


def error_embed(
    ctx: CtxOrInteraction,
    description: str,
    title: str = "Error",
    **kwargs,
) -> discord.Embed:
    return build_embed(
        ctx,
        title=f"❌ {title}",
        description=description,
        color=discord.Color.red(),
        **kwargs,
    )


def info_embed(
    ctx: CtxOrInteraction,
    description: str,
    title: str = "Info",
    **kwargs,
) -> discord.Embed:
    return build_embed(
        ctx,
        title=f"ℹ️ {title}",
        description=description,
        color=discord.Color.blurple(),
        **kwargs,
    )


def warning_embed(
    ctx: CtxOrInteraction,
    description: str,
    title: str = "Warning",
    **kwargs,
) -> discord.Embed:
    return build_embed(
        ctx,
        title=f"⚠️ {title}",
        description=description,
        color=discord.Color.yellow(),
        **kwargs,
    )


def loading_embed(
    ctx: CtxOrInteraction,
    description: str = "Please wait...",
    title: str = "Loading",
    **kwargs,
) -> discord.Embed:
    return build_embed(
        ctx,
        title=f"⏳ {title}",
        description=description,
        color=discord.Color.light_grey(),
        **kwargs,
    )