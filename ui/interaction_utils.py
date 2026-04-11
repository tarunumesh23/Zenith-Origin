"""
ui/interaction_utils.py
~~~~~~~~~~~~~~~~~~~~~~~~
Safe wrappers around Discord interaction responses.

The ``Unknown interaction`` (error code 10062) crash happens when:
  1. The user double-clicks a button before the first callback finishes.
  2. The 3-second response window expires before ``defer()`` is called
     (e.g. slow DB query before the defer).
  3. The interaction was already acknowledged by a previous call.

All public helpers in this module silently swallow 10062 / Already-responded
errors so a stale click never brings down the bot.

Usage
-----
Replace every bare ``await interaction.response.defer(...)`` with:

    from ui.interaction_utils import safe_defer
    await safe_defer(interaction, ephemeral=True)

Replace ``await interaction.response.send_message(...)`` with:

    from ui.interaction_utils import safe_send
    await safe_send(interaction, embed=..., ephemeral=True)

Replace ``await interaction.edit_original_response(...)`` with:

    from ui.interaction_utils import safe_edit
    await safe_edit(interaction, embed=..., view=...)
"""
from __future__ import annotations

import logging
from typing import Any

import discord

log = logging.getLogger("bot.ui.interaction_utils")

# Discord error codes we silently suppress
_EXPIRED_CODES    = {10062}   # Unknown interaction — expired or already used
_RESPONDED_MSG    = "already been acknowledged"  # substring in InteractionResponded msg


def _is_stale(exc: Exception) -> bool:
    """Return True if *exc* represents a stale/duplicate interaction response."""
    if isinstance(exc, discord.NotFound) and exc.code in _EXPIRED_CODES:
        return True
    if isinstance(exc, discord.InteractionResponded):
        return True
    return False


async def safe_defer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = False,
    thinking: bool = False,
) -> bool:
    """
    Defer an interaction response safely.

    Returns
    -------
    bool
        ``True`` if the defer succeeded, ``False`` if the interaction was
        already acknowledged or had expired (caller can use this to decide
        whether to skip follow-up edits).
    """
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except Exception as exc:
        if _is_stale(exc):
            log.debug(
                "safe_defer: stale interaction ignored  id=%s  user=%s",
                interaction.id,
                getattr(interaction.user, "id", "?"),
            )
            return False
        raise


async def safe_send(
    interaction: discord.Interaction,
    *args: Any,
    ephemeral: bool = False,
    **kwargs: Any,
) -> bool:
    """
    Send an interaction response (``response.send_message``) safely.

    Falls back to ``followup.send`` if the interaction was already deferred.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the interaction had expired.
    """
    kwargs.setdefault("ephemeral", ephemeral)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(*args, **kwargs)
        else:
            await interaction.response.send_message(*args, **kwargs)
        return True
    except Exception as exc:
        if _is_stale(exc):
            log.debug(
                "safe_send: stale interaction ignored  id=%s  user=%s",
                interaction.id,
                getattr(interaction.user, "id", "?"),
            )
            return False
        raise


async def safe_edit(
    interaction: discord.Interaction,
    *,
    content: str | None = discord.utils.MISSING,
    embed:   discord.Embed | None = discord.utils.MISSING,
    embeds:  list[discord.Embed] | None = discord.utils.MISSING,
    view:    discord.ui.View | None = discord.utils.MISSING,
    **kwargs: Any,
) -> bool:
    """
    Edit the original interaction response safely.

    Tries ``interaction.edit_original_response`` first; if the interaction
    has expired it silently returns ``False``.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the interaction had expired.
    """
    edit_kwargs: dict[str, Any] = {}
    if content  is not discord.utils.MISSING: edit_kwargs["content"]  = content
    if embed    is not discord.utils.MISSING: edit_kwargs["embed"]    = embed
    if embeds   is not discord.utils.MISSING: edit_kwargs["embeds"]   = embeds
    if view     is not discord.utils.MISSING: edit_kwargs["view"]     = view
    edit_kwargs.update(kwargs)

    try:
        await interaction.edit_original_response(**edit_kwargs)
        return True
    except Exception as exc:
        if _is_stale(exc):
            log.debug(
                "safe_edit: stale interaction ignored  id=%s  user=%s",
                interaction.id,
                getattr(interaction.user, "id", "?"),
            )
            return False
        raise


async def safe_respond_or_followup(
    interaction: discord.Interaction,
    *args: Any,
    ephemeral: bool = True,
    **kwargs: Any,
) -> bool:
    """
    Unified helper: send a response if the interaction is fresh, or a followup
    if it has already been deferred/responded to.

    This is the recommended single-call replacement for the common pattern:

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        ...
        await ctx.send(embed=..., ephemeral=True)

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the interaction had expired.
    """
    kwargs.setdefault("ephemeral", ephemeral)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(*args, **kwargs)
        else:
            await interaction.followup.send(*args, **kwargs)
        return True
    except Exception as exc:
        if _is_stale(exc):
            log.debug(
                "safe_respond_or_followup: stale interaction ignored  id=%s  user=%s",
                interaction.id,
                getattr(interaction.user, "id", "?"),
            )
            return False
        raise