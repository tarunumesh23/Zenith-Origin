"""
cogs/training.py
~~~~~~~~~~~~~~~~
Discord slash commands for the Cultivation Combat Training system.

Commands
--------
/train <path>          — run a training session on a chosen path
/training              — view your current training stats, tiers, fatigue
/training leaderboard  — top warriors by total training power
/rest                  — reduce fatigue (1-hour cooldown)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db import training as db_training
from training.constants import (
    PATH_BODY,
    PATH_DISPLAY,
    PATH_EMOJI,
    PATH_FLOW,
    PATH_KILLING,
    SESSION_COOLDOWN_SECONDS,
    STAT_CAPS,
    TIER_DISPLAY,
    TIER_MASTERY_THRESHOLD,
)
from training.engine import TrainingState, resolve_session
from ui.embed import build_embed, error_embed, success_embed
from ui.interaction_utils import safe_defer, safe_edit

log = logging.getLogger("bot.cogs.training")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATH_CHOICES = [
    app_commands.Choice(name="🩸 Body Tempering  (ATK / DEF)",    value=PATH_BODY),
    app_commands.Choice(name="🌬️ Flow Arts        (SPE / EVA)",    value=PATH_FLOW),
    app_commands.Choice(name="🔥 Killing Sense    (CRIT % / DMG)", value=PATH_KILLING),
]

# Maps a path constant → its mastery attribute name on the record.
_PATH_MASTERY_ATTR: dict[str, str] = {
    PATH_BODY:    "mastery_body",
    PATH_FLOW:    "mastery_flow",
    PATH_KILLING: "mastery_killing",
}

# Ordered tier list used for next-threshold lookups.
_TIER_ORDER = ["beginner", "advanced", "forbidden"]


def _mastery_bar(current: float, cap: int, width: int = 12) -> str:
    """Return a filled/empty block bar scaled to *cap*."""
    pct    = min(current / max(cap, 1), 1.0)
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


def _stat_bar(value: float, cap: int, width: int = 10) -> str:
    """Return a filled/empty block bar for a stat."""
    pct    = min(value / max(cap, 1), 1.0)
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


def _fatigue_bar(fatigue: float, width: int = 10) -> str:
    """Return a fire/empty bar for fatigue (0–10 scale)."""
    filled = int(min(max(fatigue, 0.0), float(width)))
    return "🔥" * filled + "⬜" * (width - filled)


def _next_mastery_threshold(tier: str) -> int:
    """Return the mastery EXP threshold for the tier *after* the given one."""
    idx = _TIER_ORDER.index(tier)
    next_tier = _TIER_ORDER[min(idx + 1, len(_TIER_ORDER) - 1)]
    return TIER_MASTERY_THRESHOLD.get(next_tier, 600)


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class TrainingCog(commands.Cog, name="Training"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -----------------------------------------------------------------------
    # /train
    # -----------------------------------------------------------------------

    @app_commands.command(name="train", description="Conduct a cultivation training session.")
    @app_commands.describe(path="Which training path to follow this session.")
    @app_commands.choices(path=_PATH_CHOICES)
    async def train(self, interaction: discord.Interaction, path: str) -> None:
        await safe_defer(interaction, ephemeral=False, thinking=True)

        discord_id = interaction.user.id
        guild_id   = interaction.guild_id or 0

        # ── Cooldown check ────────────────────────────────────────────────
        cd_expires = await db_training.get_training_cooldown(discord_id, path)
        if cd_expires:
            remaining = (_ensure_utc(cd_expires) - datetime.now(timezone.utc)).total_seconds()
            mins, secs = divmod(int(remaining), 60)
            await safe_edit(interaction, embed=error_embed(
                interaction,
                f"Your body hasn't recovered yet.\n"
                f"⏳ **{PATH_DISPLAY[path]}** cooldown: `{mins}m {secs}s`",
                title="Training on Cooldown",
            ))
            return

        # ── Load / create record ──────────────────────────────────────────
        record = await db_training.get_or_create_training_stats(discord_id, guild_id)

        # ── Passive fatigue decay ─────────────────────────────────────────
        hours_elapsed = (
            datetime.now(timezone.utc) - _ensure_utc(record.last_updated)
        ).total_seconds() / 3600
        if hours_elapsed > 0:
            await db_training.decay_fatigue(discord_id, guild_id, hours_elapsed)
            record = await db_training.get_training_stats(discord_id, guild_id)

        # ── Consecutive-session count ─────────────────────────────────────
        consecutive = (
            record.consecutive_path_sessions + 1
            if record.last_path_trained == path
            else 1
        )

        # ── Build engine state ────────────────────────────────────────────
        state = TrainingState(
            discord_id=discord_id,
            path=path,
            atk=record.atk,
            def_=record.def_,
            spe=record.spe,
            eva=record.eva,
            crit_chance=record.crit_chance,
            crit_dmg=record.crit_dmg,
            mastery_body=record.mastery_body,
            mastery_flow=record.mastery_flow,
            mastery_killing=record.mastery_killing,
            tier_body=record.tier_body,
            tier_flow=record.tier_flow,
            tier_killing=record.tier_killing,
            fatigue=record.fatigue,
            consecutive_path_sessions=record.consecutive_path_sessions,
            last_path_trained=record.last_path_trained,
            injury_locks={
                PATH_BODY:    record.injury_body_remaining,
                PATH_FLOW:    record.injury_flow_remaining,
                PATH_KILLING: record.injury_killing_remaining,
            },
            deviation_streak=record.deviation_streak,
            passive_tags=record.passive_tags,
            cascade_lock=record.cascade_lock,
        )

        # ── Resolve session ───────────────────────────────────────────────
        result = resolve_session(state)
        risk   = result.risk_event

        # ── Persist result ────────────────────────────────────────────────
        await db_training.apply_session_result(
            discord_id=discord_id,
            guild_id=guild_id,
            path=path,
            tier=result.tier,
            stats_delta=result.stats_gained,
            mastery_gained=result.mastery_gained,
            new_tier=result.new_tier,
            fatigue_after=result.fatigue_after,
            risk_event_type=risk.event_type      if risk else None,
            path_locked=risk.path_locked          if risk else None,
            lock_sessions=risk.lock_sessions      if risk else 0,
            deviation_cascade=risk.cascade_triggered if risk else False,
            mutation_tag=risk.mutation_tag        if risk else None,
            overtraining=result.overtraining,
            consecutive=consecutive,
        )

        await db_training.log_session(
            discord_id=discord_id,
            guild_id=guild_id,
            path=path,
            tier=result.tier,
            stats_gained=result.stats_gained,
            mastery_gained=result.mastery_gained,
            risk_event_type=risk.event_type if risk else None,
            overtraining=result.overtraining,
        )

        # ── Cooldown — always set after a non-blocked session ─────────────
        await db_training.set_training_cooldown(discord_id, path, SESSION_COOLDOWN_SECONDS)

        # ── Build embed ───────────────────────────────────────────────────
        mastery_attr  = _PATH_MASTERY_ATTR[path]
        old_mastery   = getattr(record, mastery_attr)
        new_mastery   = old_mastery + result.mastery_gained
        next_thresh   = _next_mastery_threshold(result.tier)
        bar           = _mastery_bar(new_mastery, next_thresh)

        gain_lines = [
            f"`{stat.upper().replace('_', ' '):<12}` "
            f"{'+'if delta > 0 else ''}{delta:.1f}"
            for stat, delta in result.stats_gained.items()
            if delta != 0
        ]

        color_map = {
            "beginner":  discord.Color.blue(),
            "advanced":  discord.Color.purple(),
            "forbidden": discord.Color.red(),
        }

        embed = build_embed(
            interaction,
            title=f"{PATH_EMOJI[path]} {PATH_DISPLAY[path]} — {TIER_DISPLAY[result.tier]} Tier",
            description=result.narrative,
            color=color_map.get(result.tier, discord.Color.blurple()),
            fields=[
                {
                    "name":   "📊 Stat Gains",
                    "value":  "\n".join(gain_lines) or "*No gains this session*",
                    "inline": True,
                },
                {
                    "name":  f"📿 Mastery [{bar}]",
                    "value": f"`{new_mastery}` / `{next_thresh}` EXP  (+{result.mastery_gained})",
                    "inline": True,
                },
                {
                    "name":  "💢 Fatigue",
                    "value": f"{_fatigue_bar(result.fatigue_after)} `{result.fatigue_after:.1f}/10`",
                    "inline": False,
                },
            ],
        )

        if result.streak_bonus > 0:
            embed.set_footer(
                text=(
                    f"Streak bonus active: +{result.streak_bonus * 100:.0f}% gains  •  "
                    "Rotate paths to recover fatigue"
                )
            )

        await safe_edit(interaction, embed=embed)

    # -----------------------------------------------------------------------
    # /training  (profile)
    # -----------------------------------------------------------------------

    @app_commands.command(name="training", description="View your training stats and progress.")
    async def training_profile(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction, ephemeral=True)

        discord_id = interaction.user.id
        guild_id   = interaction.guild_id or 0

        record = await db_training.get_or_create_training_stats(discord_id, guild_id)

        caps_body    = STAT_CAPS[record.tier_body]
        caps_flow    = STAT_CAPS[record.tier_flow]
        caps_killing = STAT_CAPS[record.tier_killing]

        def _line(emoji: str, label: str, val: float, cap: int) -> str:
            return (
                f"{emoji} **{label}**  "
                f"`{_stat_bar(val, cap)}` {int(val)}/{cap}"
            )

        body_lines = "\n".join([
            _line("⚔️", "ATK", record.atk,  caps_body["atk"]),
            _line("🛡️", "DEF", record.def_, caps_body["def"]),
            f"📿 Mastery: `{record.mastery_body}` EXP  |  Tier: **{TIER_DISPLAY[record.tier_body]}**",
        ])
        flow_lines = "\n".join([
            _line("💨", "SPE", record.spe, caps_flow["spe"]),
            _line("🌀", "EVA", record.eva, caps_flow["eva"]),
            f"📿 Mastery: `{record.mastery_flow}` EXP  |  Tier: **{TIER_DISPLAY[record.tier_flow]}**",
        ])
        kill_lines = "\n".join([
            _line("🎯", "CRIT %",   record.crit_chance, caps_killing["crit_chance"]),
            _line("💥", "CRIT DMG", record.crit_dmg,    caps_killing["crit_dmg"]),
            f"📿 Mastery: `{record.mastery_killing}` EXP  |  Tier: **{TIER_DISPLAY[record.tier_killing]}**",
        ])

        # Injury / lock status
        status_parts: list[str] = []
        if record.injury_body_remaining    > 0:
            status_parts.append(f"🩹 Body Tempering locked `{record.injury_body_remaining}` session(s)")
        if record.injury_flow_remaining    > 0:
            status_parts.append(f"🩹 Flow Arts locked `{record.injury_flow_remaining}` session(s)")
        if record.injury_killing_remaining > 0:
            status_parts.append(f"🩹 Killing Sense locked `{record.injury_killing_remaining}` session(s)")
        if record.cascade_lock             > 0:
            status_parts.append(f"⚠️ Qi Cascade — all paths locked `{record.cascade_lock}` session(s)")
        status_str = "\n".join(status_parts) or "✅ No injuries or locks."

        tags_str = (
            "  ".join(f"`{t.replace('_', ' ').title()}`" for t in record.passive_tags)
            if record.passive_tags
            else "*None yet*"
        )

        total = int(
            record.atk + record.def_ + record.spe
            + record.eva + record.crit_chance + record.crit_dmg
        )

        embed = build_embed(
            interaction,
            title=f"⚔️ {interaction.user.display_name}'s Training Record",
            description=f"**Total Combat Power from Training:** `{total}`",
            color=discord.Color.dark_gold(),
            fields=[
                {"name": "🩸 Body Tempering", "value": body_lines,  "inline": False},
                {"name": "🌬️ Flow Arts",       "value": flow_lines,  "inline": False},
                {"name": "🔥 Killing Sense",   "value": kill_lines,  "inline": False},
                {
                    "name":  "💢 Fatigue",
                    "value": f"{_fatigue_bar(record.fatigue)} `{record.fatigue:.1f}/10`",
                    "inline": True,
                },
                {"name": "⚠️ Status",       "value": status_str, "inline": True},
                {"name": "🧬 Passive Tags",  "value": tags_str,   "inline": False},
            ],
        )
        await safe_edit(interaction, embed=embed)

    # -----------------------------------------------------------------------
    # /rest
    # -----------------------------------------------------------------------

    @app_commands.command(name="rest", description="Meditate to reduce fatigue. 1-hour cooldown.")
    async def rest(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction, ephemeral=True)

        discord_id = interaction.user.id
        guild_id   = interaction.guild_id or 0

        cd = await db_training.get_training_cooldown(discord_id, "rest")
        if cd:
            remaining = (_ensure_utc(cd) - datetime.now(timezone.utc)).total_seconds()
            mins, secs = divmod(int(remaining), 60)
            await safe_edit(interaction, embed=error_embed(
                interaction,
                f"You are still recovering. Come back in `{mins}m {secs}s`.",
                title="Already Resting",
            ))
            return

        await db_training.decay_fatigue(discord_id, guild_id, hours_elapsed=6.0)
        await db_training.set_training_cooldown(discord_id, "rest", 3600)

        record  = await db_training.get_training_stats(discord_id, guild_id)
        fatigue = record.fatigue if record else 0.0

        await safe_edit(interaction, embed=success_embed(
            interaction,
            f"You settle into a meditative stance.\n"
            f"Fatigue reduced — current: `{fatigue:.1f}/10`",
            title="🧘 Rest Session Complete",
        ))

    # -----------------------------------------------------------------------
    # /training_leaderboard
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="training_leaderboard",
        description="Top warriors ranked by total training power.",
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction, ephemeral=False)

        guild_id = interaction.guild_id or 0
        rows     = await db_training.get_leaderboard(guild_id, limit=10)

        if not rows:
            await safe_edit(interaction, embed=error_embed(
                interaction,
                "No warriors have trained yet.",
                title="Training Leaderboard",
            ))
            return

        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        lines  = [
            f"{medals[i]} **{row['display_name']}**  `Power {int(row['total_power'])}`  "
            f"⚔️{int(row['atk'])} 💨{int(row['spe'])} 🎯{int(row['crit_chance'])}%"
            for i, row in enumerate(rows)
        ]

        embed = build_embed(
            interaction,
            title="⚔️ Training Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await safe_edit(interaction, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TrainingCog(bot))
    log.info("TrainingCog loaded")