"""
cogs/spirit_roots.py
~~~~~~~~~~~~~~~~~~~~~
Discord Cog for the Spirit Root spin system.

Commands
--------
/spin_root         — Spend a spin attempt (free once per day).
/root_profile      — View your current root, best root, pity counter.
/root_info         — Static embed explaining what every root tier does.

Mechanics
---------
• Rarity is extremely low.  Tuned weights override data.py at import time.
• Pity fires after PITY_THRESHOLD consecutive non-improving spins → guarantees +1 tier.
• Safe System: rolling below your current root is blocked; you keep what you have.
• Floor System: can never roll more than FLOOR_GAP tiers below your personal best.
• One free spin per SPIN_COOLDOWN_SECONDS.

DB contract (replace the _db_* stubs with your real async calls)
-----------------------------------------------------------------
Table: spirit_roots
  user_id          BIGINT PRIMARY KEY
  current_value    SMALLINT NOT NULL DEFAULT 1
  best_value       SMALLINT NOT NULL DEFAULT 1
  pity_counter     SMALLINT NOT NULL DEFAULT 0
  total_spins      INT      NOT NULL DEFAULT 0
  last_spin_at     TIMESTAMPTZ
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from spirit_roots.cultivation_bridge import (
    describe_spirit_root_bonuses,
    get_spirit_root_bonuses,
)
from spirit_roots.data import (
    PITY_THRESHOLD,
    ROOT_TIERS,
    SPIN_COOLDOWN_SECONDS,
    get_tier_by_value,
)
from spirit_roots.engine import SpinResult, resolve_spin
from ui.interaction_utils import (
    interaction_handler,
    safe_defer,
    safe_edit,
    safe_respond_or_followup,
    safe_send,
)

log = logging.getLogger("bot.cogs.spirit_roots")

# ---------------------------------------------------------------------------
# Rarity override — patches spirit_roots.data at import time
# ---------------------------------------------------------------------------
# data.py ships with placeholder weights; we replace them here so the cog
# fully owns rarity without touching shared game-data files.

import spirit_roots.data as _srd  # noqa: E402

_TUNED_WEIGHTS: dict[int, float] = {
    1: 55.0,   # Mortal    ~55 %
    2: 28.0,   # Iron      ~28 %
    3: 11.0,   # Jade      ~11 %
    4: 4.5,    # Golden    ~ 4.5 %
    5: 1.5,    # Heavenly  ~ 1.5 %
}

_srd.ROOT_TIERS = [
    _srd.RootTier(
        value=t.value,
        name=t.name,
        colour=t.colour,
        weight=_TUNED_WEIGHTS[t.value],
        emoji=t.emoji,
        description=t.description,
    )
    for t in _srd.ROOT_TIERS
]
_srd._BY_VALUE = {t.value: t for t in _srd.ROOT_TIERS}


# ---------------------------------------------------------------------------
# DB stubs — replace with your real async DB layer
# ---------------------------------------------------------------------------

async def _db_get_or_create(user_id: int) -> dict[str, Any]:
    """
    Fetch the spirit_roots row for *user_id*, creating a default row if absent.

    Must return a dict with keys:
        user_id, current_value, best_value, pity_counter, total_spins, last_spin_at
    ``last_spin_at`` should be a timezone-aware ``datetime`` or ``None``.
    """
    raise NotImplementedError(
        "_db_get_or_create: wire up your DB layer. "
        "Return dict keys: user_id / current_value / best_value / "
        "pity_counter / total_spins / last_spin_at"
    )


async def _db_apply_spin(user_id: int, result: SpinResult) -> None:
    """
    Persist the outcome of one resolved spin.

    Suggested SQL (asyncpg):
        UPDATE spirit_roots
        SET current_value = $1,
            best_value    = GREATEST(best_value, $1),
            pity_counter  = $2,
            total_spins   = total_spins + 1,
            last_spin_at  = NOW()
        WHERE user_id = $3
    """
    raise NotImplementedError("_db_apply_spin: wire up your DB layer.")


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

_OUTCOME_TITLES: dict[str, str] = {
    "improved":  "🌟 Spirit Root Awakened!",
    "equal":     "⚖️  Root Unchanged",
    "protected": "🛡️  Safe System Activated",
}

_OUTCOME_COLOURS: dict[str, int] = {
    "improved":  0xFFD700,
    "equal":     0x5865F2,
    "protected": 0x2ECC71,
}


def _pity_bar(current: int, threshold: int) -> str:
    filled = "█" * current
    empty  = "░" * max(0, threshold - current)
    return f"`{filled}{empty}` {current}/{threshold}"


def _spin_embed(result: SpinResult, user: discord.User | discord.Member) -> discord.Embed:
    tier   = result.final_tier
    colour = tier.colour if result.is_improved else _OUTCOME_COLOURS[result.outcome]
    embed  = discord.Embed(title=_OUTCOME_TITLES[result.outcome], colour=colour)
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)

    if result.is_improved:
        embed.description = (
            f"Your Spirit Root has advanced!\n\n"
            f"{tier.emoji}  **{tier.name}**\n"
            f"*{tier.description}*"
        )
        bonuses_text = describe_spirit_root_bonuses(tier.value)
        if bonuses_text and bonuses_text != "No cultivation bonuses.":
            embed.add_field(name="📊 Cultivation Bonuses", value=bonuses_text, inline=False)

    elif result.is_protected:
        embed.description = (
            f"The heavens attempted to diminish your root — the Safe System held firm.\n\n"
            f"Rolled **{result.rolled_tier.name}** → kept **{result.final_tier.name}**."
        )

    else:  # equal
        embed.description = (
            f"The heavens offered the same root you already possess.\n\n"
            f"{tier.emoji}  **{tier.name}**"
        )

    if result.pity_triggered:
        embed.add_field(
            name="✨ Pity Guarantee Triggered",
            value=f"After **{result.pity_before}** non-improving spins the heavens relented.",
            inline=False,
        )

    if not result.is_improved and result.pity_after > 0:
        embed.add_field(
            name="🔮 Pity Progress",
            value=_pity_bar(result.pity_after, PITY_THRESHOLD),
            inline=False,
        )

    embed.set_footer(text=f"Floor: Tier {result.floor_applied}  •  Rolled: {result.rolled_tier.name}")
    return embed


def _cooldown_embed(seconds_left: float, user: discord.User | discord.Member) -> discord.Embed:
    hours, rem    = divmod(int(seconds_left), 3600)
    minutes, secs = divmod(rem, 60)
    time_str = f"{hours}h {minutes}m {secs}s" if hours else f"{minutes}m {secs}s"
    embed = discord.Embed(
        title="⏳ Cooldown Active",
        description=(
            f"Your Spirit Root is still settling from the last awakening.\n\n"
            f"You may spin again in **{time_str}**."
        ),
        colour=0xE74C3C,
    )
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    return embed


def _profile_embed(row: dict[str, Any], user: discord.User | discord.Member) -> discord.Embed:
    current = get_tier_by_value(row["current_value"])
    best    = get_tier_by_value(row["best_value"])

    embed = discord.Embed(
        title=f"{current.emoji} {current.name}",
        description=current.description,
        colour=current.colour,
    )
    embed.set_author(name=f"{user.display_name}'s Spirit Root", icon_url=user.display_avatar.url)

    embed.add_field(name="Current Root",  value=f"{current.emoji} {current.name}", inline=True)
    embed.add_field(name="Personal Best", value=f"{best.emoji} {best.name}",       inline=True)
    embed.add_field(name="Total Spins",   value=str(row.get("total_spins", 0)),     inline=True)

    pity = row.get("pity_counter", 0)
    embed.add_field(
        name="🔮 Pity Counter",
        value=_pity_bar(pity, PITY_THRESHOLD),
        inline=False,
    )

    bonuses_text = describe_spirit_root_bonuses(current.value)
    if bonuses_text and bonuses_text != "No cultivation bonuses.":
        embed.add_field(name="📊 Active Bonuses", value=bonuses_text, inline=False)

    # Timestamp goes in a field (not footer) so Discord's <t:> tag renders
    last_spin: datetime | None = row.get("last_spin_at")
    if last_spin is not None:
        if last_spin.tzinfo is None:
            last_spin = last_spin.replace(tzinfo=timezone.utc)
        ts = int(last_spin.timestamp())
        embed.add_field(name="Last Spin", value=f"<t:{ts}:R>", inline=False)

    return embed


def _root_info_embed() -> discord.Embed:
    total_weight = sum(t.weight for t in _srd.ROOT_TIERS)
    embed = discord.Embed(
        title="📖 Spirit Root Reference",
        description=(
            "Spirit Roots govern your cultivation speed and breakthrough potential.\n"
            f"Spin once every **{SPIN_COOLDOWN_SECONDS // 3600}h**. "
            f"Pity guarantee fires after **{PITY_THRESHOLD}** non-improving spins.\n"
            "The **Safe System** prevents your root from dropping below its current tier."
        ),
        colour=0x9B59B6,
    )
    for tier in sorted(_srd.ROOT_TIERS, key=lambda t: t.value, reverse=True):
        bonuses    = get_spirit_root_bonuses(tier.value)
        chance_pct = tier.weight / total_weight * 100
        lines      = [f"*{tier.description}*", f"Drop chance: **{chance_pct:.1f}%**"]

        qi_pct = (bonuses["qi_multiplier"] - 1.0) * 100
        if qi_pct > 0:
            lines.append(f"Qi ×{bonuses['qi_multiplier']:.2f}  (+{qi_pct:.0f}%)")
        bt = bonuses["breakthrough_bonus"]
        if bt > 0:
            lines.append(f"Breakthrough +{bt:.0f}%")

        embed.add_field(
            name=f"{tier.emoji} {tier.name}  (Tier {tier.value})",
            value="\n".join(lines),
            inline=False,
        )
    return embed


# ---------------------------------------------------------------------------
# Spin Again view
# ---------------------------------------------------------------------------

class SpinView(discord.ui.View):
    """
    Single 'Spin Again' button attached to every spin result.

    Owned by the user who triggered the original spin.
    Disables itself on first click to prevent double-fires.
    Expires after 60 seconds (on_timeout just marks items disabled locally;
    the interaction token is gone so no message edit is possible).
    """

    def __init__(self, cog: "SpiritRootsCog", user_id: int) -> None:
        super().__init__(timeout=60)
        self.cog     = cog
        self.user_id = user_id

    @discord.ui.button(label="🎲 Spin Again", style=discord.ButtonStyle.primary)
    async def spin_again(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        # Guard: only the original user may use this button
        if interaction.user.id != self.user_id:
            await safe_send(
                interaction,
                embed=discord.Embed(
                    description="This spin belongs to someone else.",
                    colour=0xE74C3C,
                ),
                ephemeral=True,
            )
            return

        # Disable the button before deferring to prevent any double-click
        button.disabled = True

        # Defer immediately — must happen before any slow work
        deferred = await safe_defer(interaction, ephemeral=False, thinking=True)
        if not deferred:
            # Interaction window already closed — nothing we can do
            return

        # Push the disabled-button state to Discord
        await safe_edit(interaction, view=self)

        # Slow path: DB read + RNG + DB write
        result_embed, new_view = await self.cog._do_spin(interaction.user)

        # Replace the message with the new spin result
        await safe_edit(interaction, embed=result_embed, view=new_view)

    async def on_timeout(self) -> None:
        # Mark items disabled locally for correctness; cannot edit — token is gone
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class SpiritRootsCog(commands.Cog, name="Spirit Roots"):
    """Spirit Root awakening, pity system, and cultivation bonuses."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # defaultdict — one permanent lock per user, created on first access
        self._spin_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------
    # Core spin logic — shared by /spin_root and SpinView.spin_again
    # ------------------------------------------------------------------

    async def _do_spin(
        self,
        user: discord.User | discord.Member,
    ) -> tuple[discord.Embed, SpinView | None]:
        """
        Resolve one spin attempt for *user*.

        Returns ``(embed, view)``.
        *view* is ``None`` when the user is on cooldown (no button shown).
        """
        async with self._spin_locks[user.id]:
            row = await _db_get_or_create(user.id)

            # Cooldown check
            last_spin: datetime | None = row.get("last_spin_at")
            if last_spin is not None:
                if last_spin.tzinfo is None:
                    last_spin = last_spin.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(tz=timezone.utc) - last_spin).total_seconds()
                if elapsed < SPIN_COOLDOWN_SECONDS:
                    return _cooldown_embed(SPIN_COOLDOWN_SECONDS - elapsed, user), None

            # Resolve spin
            result = resolve_spin(
                current_value=row["current_value"],
                best_value=row["best_value"],
                pity_counter=row["pity_counter"],
            )

            # Persist
            await _db_apply_spin(user.id, result)

        log.info(
            "spin  user=%s  rolled=%s  final=%s  outcome=%s  pity=%d→%d",
            user.id,
            result.rolled_tier.name,
            result.final_tier.name,
            result.outcome,
            result.pity_before,
            result.pity_after,
        )

        return _spin_embed(result, user), SpinView(self, user.id)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @app_commands.command(
        name="spin_root",
        description="Attempt to awaken or upgrade your Spirit Root.",
    )
    @app_commands.guild_only()
    @interaction_handler(ephemeral=False, thinking=True)
    async def spin_root(self, interaction: discord.Interaction) -> None:
        embed, view = await self._do_spin(interaction.user)
        await safe_edit(interaction, embed=embed, view=view)

    @app_commands.command(
        name="root_profile",
        description="View your Spirit Root profile and cultivation bonuses.",
    )
    @app_commands.describe(user="Another cultivator's profile to inspect (optional).")
    @app_commands.guild_only()
    @interaction_handler(ephemeral=False, thinking=True)
    async def root_profile(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        target = user or interaction.user
        row    = await _db_get_or_create(target.id)
        await safe_edit(interaction, embed=_profile_embed(row, target))

    @app_commands.command(
        name="root_info",
        description="Learn about Spirit Root tiers, rarity, and cultivation bonuses.",
    )
    @interaction_handler(ephemeral=True, thinking=False)
    async def root_info(self, interaction: discord.Interaction) -> None:
        await safe_edit(interaction, embed=_root_info_embed())

    # ------------------------------------------------------------------
    # Cog-level error handler
    # ------------------------------------------------------------------

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = getattr(error, "original", error)

        if isinstance(original, NotImplementedError):
            embed = discord.Embed(
                title="⚙️ Not Configured",
                description=(
                    "The database layer hasn't been connected yet.\n"
                    "Replace `_db_get_or_create` and `_db_apply_spin` "
                    "in `cogs/spirit_roots.py` with your real DB calls."
                ),
                colour=0xE74C3C,
            )
        else:
            log.exception("Unhandled error in SpiritRootsCog", exc_info=original)
            embed = discord.Embed(
                title="❌ Something Went Wrong",
                description="An unexpected error occurred. Please try again later.",
                colour=0xE74C3C,
            )

        await safe_respond_or_followup(interaction, embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpiritRootsCog(bot))