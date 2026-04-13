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

import db.spirit_roots as sr_db
from spirit_roots.cultivation_bridge import (
    describe_spirit_root_bonuses,
    get_spirit_root_bonuses,
)
from spirit_roots.data import (
    PITY_THRESHOLD,
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
# DB layer — wired to db/spirit_roots.py
# ---------------------------------------------------------------------------

async def _db_get_or_create(user_id: int, guild_id: int) -> dict[str, Any]:
    """
    Fetch the player's spirit root record, creating a Tier-1 row if absent.
    Returns a plain dict matching the shape _do_spin / _profile_embed expect.
    """
    record = await sr_db.get_spirit_root(user_id, guild_id)
    if record is None:
        record = await sr_db.create_spirit_root(user_id, guild_id, root_value=1)
    return {
        "user_id":       record.discord_id,
        "current_value": record.current_value,
        "best_value":    record.best_value,
        "pity_counter":  record.pity_counter,
        "total_spins":   record.total_spins,
        "last_spin_at":  record.last_spin_at,
    }


async def _db_apply_spin(user_id: int, guild_id: int, result: SpinResult) -> None:
    """Persist spin outcome, append audit log, and set cooldown."""
    await sr_db.apply_spin_result(
        discord_id=user_id,
        guild_id=guild_id,
        rolled_value=result.rolled_tier.value,
        outcome=result.outcome,
        pity_triggered=result.pity_triggered,
    )
    await sr_db.log_spin(
        discord_id=user_id,
        guild_id=guild_id,
        rolled_value=result.rolled_tier.value,
        pity_triggered=result.pity_triggered,
        outcome=result.outcome,
    )
    await sr_db.set_spin_cooldown(user_id, SPIN_COOLDOWN_SECONDS)


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
    tier = result.final_tier
    colour = tier.colour if result.is_improved else _OUTCOME_COLOURS[result.outcome]

    embed = discord.Embed(
        title=_OUTCOME_TITLES[result.outcome],
        colour=colour,
    )

    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)

    # ── Description ─────────────────────────────
    if result.is_improved:
        embed.description = (
            f"✨ **Your Spirit Root has evolved!**\n\n"
            f"{tier.emoji} **{tier.name}**\n"
            f"*{tier.description}*"
        )

    elif result.is_protected:
        embed.description = (
            f"🛡️ The heavens tested you — but your foundation held firm.\n\n"
            f"Rolled **{result.rolled_tier.name}** → Kept **{tier.name}**"
        )

    else:
        embed.description = (
            f"⚖️ The heavens remain unchanged.\n\n"
            f"{tier.emoji} **{tier.name}**"
        )

    # ── Bonuses ─────────────────────────────
    if result.is_improved:
        bonuses_text = describe_spirit_root_bonuses(tier.value)
        if bonuses_text and bonuses_text != "No cultivation bonuses.":
            embed.add_field(
                name="📊 Cultivation Bonuses",
                value=bonuses_text,
                inline=False,
            )

    # ── Pity ─────────────────────────────
    if result.pity_triggered:
        embed.add_field(
            name="✨ Pity Triggered",
            value=f"After **{result.pity_before}** failed attempts, fate intervened.",
            inline=False,
        )

    if not result.is_improved:
        embed.add_field(
            name="🔮 Pity Progress",
            value=_pity_bar(result.pity_after, PITY_THRESHOLD),
            inline=False,
        )

    embed.set_footer(
        text=f"Floor: Tier {result.floor_applied}  •  Rolled: {result.rolled_tier.name}"
    )

    return embed


def _cooldown_embed(seconds_left: float, user: discord.User | discord.Member) -> discord.Embed:
    hours, rem = divmod(int(seconds_left), 3600)
    minutes, secs = divmod(rem, 60)

    if hours:
        time_str = f"{hours}h {minutes}m"
    elif minutes:
        time_str = f"{minutes}m {secs}s"
    else:
        time_str = f"{secs}s"

    embed = discord.Embed(
        title="⏳ Spirit Root Cooldown",
        description=(
            "Your spiritual energy is still stabilizing.\n\n"
            f"⏱️ Try again in **{time_str}**"
        ),
        colour=0xE74C3C,
    )

    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    return embed


def _profile_embed(row: dict[str, Any], user: discord.User | discord.Member) -> discord.Embed:
    current = get_tier_by_value(row["current_value"])
    best    = get_tier_by_value(row["best_value"])

    embed = discord.Embed(
        title=f"{current.emoji} {current.name} • Spirit Root",  # ← visual polish
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
    Expires after 60 seconds.
    """

    def __init__(self, cog: "SpiritRootsCog", user_id: int, guild_id: int) -> None:
        super().__init__(timeout=60)
        self.cog      = cog
        self.user_id  = user_id
        self.guild_id = guild_id

    async def on_timeout(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="🎲 Spin Again", style=discord.ButtonStyle.primary)
    async def spin_again(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:

        # ownership check
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

        # prevent double click spam
        if button.disabled:
            return

        button.disabled = True

        # safe defer (handles already responded edge cases)
        try:
            await safe_defer(interaction, ephemeral=False, thinking=True)
        except Exception:
            return

        # update button state immediately
        try:
            await safe_edit(interaction, view=self)
        except Exception:
            pass  # message may be gone, ignore

        # run spin
        try:
            result_embed, new_view = await self.cog._do_spin(
                interaction.user,
                self.guild_id,
            )
        except Exception as e:
            log.exception("SpinView error", exc_info=e)
            return

        # final update
        try:
            await safe_edit(interaction, embed=result_embed, view=new_view)
        except Exception:
            pass  # interaction expired or message gone


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class SpiritRootsCog(commands.Cog, name="Spirit Roots"):
    """Spirit Root awakening, pity system, and cultivation bonuses."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._spin_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------
    # Core spin logic — shared by /spin_root and SpinView.spin_again
    # ------------------------------------------------------------------

    async def _do_spin(
        self,
        user: discord.User | discord.Member,
        guild_id: int,
    ) -> tuple[discord.Embed, SpinView | None]:
        """
        Resolve one spin attempt for *user* in *guild_id*.

        Returns ``(embed, view)``.
        *view* is ``None`` when the user is on cooldown (no button shown).
        """
        async with self._spin_locks[user.id]:
            row = await _db_get_or_create(user.id, guild_id)

            # Cooldown check — prefer db cooldown table over last_spin_at heuristic
            cooldown_expiry = await sr_db.get_spin_cooldown(user.id)
            if cooldown_expiry is not None:
                if cooldown_expiry.tzinfo is None:
                    cooldown_expiry = cooldown_expiry.replace(tzinfo=timezone.utc)
                now = datetime.now(tz=timezone.utc)
                if now < cooldown_expiry:
                    seconds_left = (cooldown_expiry - now).total_seconds()
                    return _cooldown_embed(seconds_left, user), None

            result = resolve_spin(
                current_value=row["current_value"],
                best_value=row["best_value"],
                pity_counter=row["pity_counter"],
            )

            await _db_apply_spin(user.id, guild_id, result)

        log.info(
            "spin  user=%s  guild=%s  rolled=%s  final=%s  outcome=%s  pity=%d→%d",
            user.id,
            guild_id,
            result.rolled_tier.name,
            result.final_tier.name,
            result.outcome,
            result.pity_before,
            result.pity_after,
        )

        return _spin_embed(result, user), SpinView(self, user.id, guild_id)

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
        guild_id = interaction.guild_id or 0
        embed, view = await self._do_spin(interaction.user, guild_id)
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
        target   = user or interaction.user
        guild_id = interaction.guild_id or 0
        row      = await _db_get_or_create(target.id, guild_id)
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