"""
ui/interaction_utils.py
~~~~~~~~~~~~~~~~~~~~~~~~
Production-grade, zero-crash wrappers around every Discord interaction
response surface.

Why this exists
---------------
Discord interactions fail silently (or loudly) for several reasons:

  Code 10062 – Unknown Interaction
      The 3-second acknowledgement window elapsed before defer() was called,
      OR the user double-clicked a button before the first callback finished.

  Code 10015 – Unknown Webhook
      The webhook backing a followup has been deleted or invalidated.

  discord.InteractionResponded
      respond/defer was called twice in the same handler.

  discord.HTTPException (5xx / rate-limit)
      Transient Discord-side error; worth one retry.

  asyncio.TimeoutError / aiohttp.ClientError
      Network hiccup between your host and Discord.

All public helpers in this module handle every known failure mode so a
stale click, network blip, or race condition never brings down the bot.

Quick-start
-----------
Swap bare discord.py calls like this:

    # Before
    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(embed=e)
    await interaction.response.send_message("done", ephemeral=True)

    # After
    from ui.interaction_utils import safe_defer, safe_edit, safe_send
    await safe_defer(interaction, ephemeral=True)
    await safe_edit(interaction, embed=e)
    await safe_send(interaction, "done", ephemeral=True)

For commands that may or may not be deferred:

    from ui.interaction_utils import safe_respond_or_followup
    await safe_respond_or_followup(interaction, embed=e, ephemeral=True)

Decorator shortcut (wraps an entire callback):

    from ui.interaction_utils import interaction_handler

    @interaction_handler(ephemeral=True, thinking=True)
    async def my_button_callback(self, interaction: discord.Interaction):
        # interaction is already deferred when you enter here
        await safe_edit(interaction, embed=build_embed())
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, Coroutine, Final, TypeVar

import discord

log = logging.getLogger("bot.ui.interaction_utils")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Discord REST error codes we treat as "stale / already handled"
_STALE_CODES: Final[frozenset[int]] = frozenset({
    10062,  # Unknown Interaction  – window expired or double-ack
    10015,  # Unknown Webhook      – followup webhook gone
    40060,  # Interaction already acknowledged
})

# HTTP status codes worth a single transparent retry
_RETRY_STATUS: Final[frozenset[int]] = frozenset({500, 502, 503, 504})

# Seconds to wait before the one automatic retry
_RETRY_DELAY: Final[float] = 0.4

# Generic error embed shown to the user when an unhandled exception occurs
_ERROR_EMBED: Final = discord.Embed(
    title="Something went wrong",
    description=(
        "An unexpected error occurred. "
        "Please try again or contact a server admin if it persists."
    ),
    color=discord.Color.red(),
)

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def _is_stale(exc: Exception) -> bool:
    """Return ``True`` when *exc* is a known stale/duplicate interaction error."""
    if isinstance(exc, discord.InteractionResponded):
        return True
    if isinstance(exc, discord.HTTPException) and exc.code in _STALE_CODES:
        return True
    return False


def _is_retryable(exc: Exception) -> bool:
    """Return ``True`` when *exc* is a transient error worth one retry."""
    if isinstance(exc, discord.HTTPException) and exc.status in _RETRY_STATUS:
        return True
    if isinstance(exc, asyncio.TimeoutError):
        return True
    return False


# ---------------------------------------------------------------------------
# Logging helpers — avoid repeating the same format string everywhere
# ---------------------------------------------------------------------------

def _log_stale(fn_name: str, interaction: discord.Interaction, exc: Exception) -> None:
    log.debug(
        "%s: stale interaction suppressed  id=%s  user=%s  error=%s",
        fn_name,
        interaction.id,
        getattr(interaction.user, "id", "?"),
        exc,
    )


def _log_unexpected(fn_name: str, interaction: discord.Interaction) -> None:
    log.exception(
        "%s: unexpected error  id=%s  user=%s",
        fn_name,
        interaction.id,
        getattr(interaction.user, "id", "?"),
    )


# ---------------------------------------------------------------------------
# Internal retry wrapper
# ---------------------------------------------------------------------------

async def _call_with_retry(coro_factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """
    Call ``coro_factory()`` once. On a retryable error wait ``_RETRY_DELAY``
    seconds and try once more. Any other exception propagates immediately.
    """
    try:
        return await coro_factory()
    except Exception as exc:
        if not _is_retryable(exc):
            raise
        log.warning("interaction_utils: transient error, retrying once – %s", exc)
        await asyncio.sleep(_RETRY_DELAY)
        return await coro_factory()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

async def safe_defer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = False,
    thinking: bool = False,
) -> bool:
    """
    Defer an interaction response, suppressing stale/duplicate errors.

    Parameters
    ----------
    interaction:
        The Discord interaction to defer.
    ephemeral:
        If ``True`` the eventual response will only be visible to the invoker.
    thinking:
        If ``True`` Discord shows the "… is thinking" indicator.

    Returns
    -------
    bool
        ``True`` if the defer succeeded.
        ``False`` if the interaction was already acknowledged or had expired.
    """
    if interaction.response.is_done():
        log.debug("safe_defer: interaction already done  id=%s", interaction.id)
        return False

    try:
        await _call_with_retry(
            lambda: interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        )
        return True
    except Exception as exc:
        if _is_stale(exc):
            _log_stale("safe_defer", interaction, exc)
            return False
        _log_unexpected("safe_defer", interaction)
        return False


async def safe_send(
    interaction: discord.Interaction,
    *args: Any,
    ephemeral: bool = False,
    **kwargs: Any,
) -> bool:
    """
    Send an interaction response via ``response.send_message``, falling back
    to ``followup.send`` when the interaction is already deferred.

    Parameters
    ----------
    interaction:
        The target interaction.
    *args:
        Positional arguments forwarded to ``send_message`` / ``followup.send``
        (typically the message content string).
    ephemeral:
        Defaults ``ephemeral=True`` in the kwargs if not already set.
    **kwargs:
        Keyword arguments forwarded to the underlying send call.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the interaction had expired.
    """
    kwargs.setdefault("ephemeral", ephemeral)

    async def _send() -> None:
        if interaction.response.is_done():
            await interaction.followup.send(*args, **kwargs)
        else:
            await interaction.response.send_message(*args, **kwargs)

    try:
        await _call_with_retry(_send)
        return True
    except Exception as exc:
        if _is_stale(exc):
            _log_stale("safe_send", interaction, exc)
            return False
        _log_unexpected("safe_send", interaction)
        return False


async def safe_edit(
    interaction: discord.Interaction,
    *,
    content: str | None = discord.utils.MISSING,
    embed: discord.Embed | None = discord.utils.MISSING,
    embeds: list[discord.Embed] | None = discord.utils.MISSING,
    view: discord.ui.View | None = discord.utils.MISSING,
    attachments: list[discord.Attachment] | None = discord.utils.MISSING,
    **kwargs: Any,
) -> bool:
    """
    Edit the original interaction response safely.

    Only keys explicitly passed are forwarded, so partial edits work correctly
    (you won't accidentally wipe an embed by not passing it).

    Parameters
    ----------
    interaction:
        The target interaction.
    content:
        New text content for the message.
    embed:
        Single embed to replace the current embed(s).
    embeds:
        List of embeds (mutually exclusive with ``embed``).
    view:
        New ``discord.ui.View`` (pass ``None`` to remove all components).
    attachments:
        New attachments (replaces all current attachments).
    **kwargs:
        Any additional kwargs supported by ``edit_original_response``.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the interaction had expired.
    """
    _MISSING = discord.utils.MISSING
    edit_kwargs: dict[str, Any] = {
        k: v for k, v in {
            "content":     content,
            "embed":       embed,
            "embeds":      embeds,
            "view":        view,
            "attachments": attachments,
        }.items()
        if v is not _MISSING
    }
    edit_kwargs.update(kwargs)

    try:
        await _call_with_retry(
            lambda: interaction.edit_original_response(**edit_kwargs)
        )
        return True
    except Exception as exc:
        if _is_stale(exc):
            _log_stale("safe_edit", interaction, exc)
            return False
        _log_unexpected("safe_edit", interaction)
        return False


async def safe_respond_or_followup(
    interaction: discord.Interaction,
    *args: Any,
    ephemeral: bool = True,
    **kwargs: Any,
) -> bool:
    """
    Unified helper: respond if fresh, followup if already deferred/responded.

    Parameters
    ----------
    interaction:
        The target interaction.
    *args / **kwargs:
        Forwarded to ``send_message`` or ``followup.send``.
    ephemeral:
        Defaults to ``True``.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the interaction had expired.
    """
    kwargs.setdefault("ephemeral", ephemeral)

    async def _respond() -> None:
        if not interaction.response.is_done():
            await interaction.response.send_message(*args, **kwargs)
        else:
            await interaction.followup.send(*args, **kwargs)

    try:
        await _call_with_retry(_respond)
        return True
    except Exception as exc:
        if _is_stale(exc):
            _log_stale("safe_respond_or_followup", interaction, exc)
            return False
        _log_unexpected("safe_respond_or_followup", interaction)
        return False


async def safe_delete_original(interaction: discord.Interaction) -> bool:
    """
    Delete the original interaction response safely.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the interaction had expired or
        the message no longer exists.
    """
    try:
        await _call_with_retry(interaction.delete_original_response)
        return True
    except discord.NotFound as exc:
        if _is_stale(exc):
            _log_stale("safe_delete_original", interaction, exc)
        else:
            log.debug("safe_delete_original: message already deleted  id=%s", interaction.id)
        return False
    except Exception as exc:
        if _is_stale(exc):
            _log_stale("safe_delete_original", interaction, exc)
            return False
        _log_unexpected("safe_delete_original", interaction)
        return False


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


def _find_interaction(*args: Any, **kwargs: Any) -> discord.Interaction | None:
    """Locate the first :class:`discord.Interaction` in a function's arguments."""
    for arg in args:
        if isinstance(arg, discord.Interaction):
            return arg
    for val in kwargs.values():
        if isinstance(val, discord.Interaction):
            return val
    return None


def interaction_handler(
    *,
    ephemeral: bool = False,
    thinking: bool = False,
    auto_defer: bool = True,
) -> Callable[[F], F]:
    """
    Decorator that wraps a button/select/modal callback so it:

    1. Auto-defers the interaction before your code runs (prevents 3-second
       timeout during slow DB queries or API calls).
    2. Catches any otherwise-unhandled exception and logs it without crashing
       the bot. The user sees a generic ephemeral error message instead of
       a broken "This interaction failed" state.

    Parameters
    ----------
    ephemeral:
        Passed to the auto-defer so the eventual response is private.
    thinking:
        Passed to the auto-defer so Discord shows the thinking indicator.
    auto_defer:
        Set to ``False`` if you want to handle deferring yourself (but still
        want the crash-guard).

    Usage
    -----
    ::

        class MyView(discord.ui.View):

            @discord.ui.button(label="Click me")
            @interaction_handler(ephemeral=True, thinking=True)
            async def my_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # interaction is already deferred here
                data = await some_slow_db_call()
                await safe_edit(interaction, embed=build_embed(data))
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> None:
            interaction = _find_interaction(*args, **kwargs)

            if interaction is not None and auto_defer:
                await safe_defer(interaction, ephemeral=ephemeral, thinking=thinking)

            try:
                await func(*args, **kwargs)
            except Exception as exc:
                if interaction is not None and _is_stale(exc):
                    log.debug(
                        "interaction_handler: stale interaction in %s  id=%s",
                        func.__qualname__,
                        interaction.id,
                    )
                    return

                log.exception(
                    "interaction_handler: unhandled exception in %s  id=%s",
                    func.__qualname__,
                    getattr(interaction, "id", "?"),
                )

                if interaction is not None:
                    await safe_respond_or_followup(
                        interaction,
                        embed=_ERROR_EMBED,
                        ephemeral=True,
                    )

        return wrapper  # type: ignore[return-value]
    return decorator