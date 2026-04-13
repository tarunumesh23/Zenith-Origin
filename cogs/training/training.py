"""
cogs/training.py
~~~~~~~~~~~~~~~~
Discord slash commands for the Cultivation Combat Training system.

Commands
--------
/train <path>         — run a training session on a chosen path
/training             — view your current training stats, tiers, fatigue
/training leaderboard — top warriors by total training power
/training rest        — reduce fatigue (1-hour cooldown)
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
    PATHS,
    SESSION_COOLDOWN_SECONDS,
    STAT_CAPS,
    TIER_DISPLAY,
    TIER_FORBIDDEN,
    TIER_MASTERY_THRESHOLD,
)
from training.engine import TrainingState, resolve_session
from ui.embed import build_embed, error_embed, success_embed
from ui.interaction_utils import safe_defer, safe_edit, safe_send

log = logging.getLogger("bot.cogs.training")

# Path choices for autocomplete
_PATH_CHOICES = [
    app_commands.Choice(name="🩸 Body Tempering  (ATK / DEF)",   value=PATH_BODY),
    app_commands.Choice(name="🌬️ Flow Arts       (SPE / EVA)",   value=PATH_FLOW),
    app_commands.Choice(name="🔥 Killing Sense   (CRIT % / DMG)", value=PATH_KILLING),
]


class TrainingCog(commands.Cog, name="Training"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /train
    # ------------------------------------------------------------------

    @app_commands.command(name="train", description="Conduct a cultivation training session.")
    @app_commands.describe(path="Which training path to follow this session.")
    @app_commands.choices(path=_PATH_CHOICES)
    async def train(self, interaction: discord.Interaction, path: str) -> None:
        await safe_defer(interaction, ephemeral=False, thinking=True)

        discord_id = interaction.user.id
        guild_id   = interaction.guild_id or 0

        # ── Cooldown check ────────────────────────────────────────────
        cd_expires = await db_training.get_training_cooldown(discord_id, path)
        if cd_expires:
            cd_aware = cd_expires if cd_expires.tzinfo else cd_expires.replace(tzinfo=timezone.utc)
            remaining = (cd_aware - datetime.now(timezone.utc)).total_seconds()
            mins, secs = divmod(int(remaining), 60)
            await safe_edit(interaction, embed=error_embed(
                interaction,
                f"Your body hasn't recovered yet.\n⏳ **{PATH_DISPLAY[path]}** cooldown: `{mins}m {secs}s`",
                title="Training on Cooldown",
            ))
            return

        # ── Load / create record ──────────────────────────────────────
        record = await db_training.get_or_create_training_stats(discord_id, guild_id)

        # ── Decay fatigue based on elapsed time ───────────────────────
        last_updated = record.last_updated
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        hours_elapsed = (datetime.now(timezone.utc) - last_updated).total_seconds() / 3600
        if hours_elapsed > 0:
            await db_training.decay_fatigue(discord_id, guild_id, hours_elapsed)
            record = await db_training.get_training_stats(discord_id, guild_id)

        # ── Build engine state ────────────────────────────────────────
        injury_locks = {
            PATH_BODY:    record.injury_body_remaining,
            PATH_FLOW:    record.injury_flow_remaining,
            PATH_KILLING: record.injury_killing_remaining,
        }

        consecutive = (
            record.consecutive_path_sessions + 1
            if record.last_path_trained == path
            else 1
        )

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
            injury_locks=injury_locks,
            deviation_streak=record.deviation_streak,
            passive_tags=record.passive_tags,
            cascade_lock=record.cascade_lock,
        )

        # ── Resolve session ───────────────────────────────────────────
        result = resolve_session(state)

        # ── Persist result ────────────────────────────────────────────
        risk = result.risk_event
        await db_training.apply_session_result(
            discord_id=discord_id,
            guild_id=guild_id,
            path=path,
            tier=result.tier,
            stats_delta=result.stats_gained,
            mastery_gained=result.mastery_gained,
            new_tier=result.new_tier,
            fatigue_after=result.fatigue_after,
            risk_event_type=risk.event_type if risk else None,
            path_locked=risk.path_locked if risk else None,
            lock_sessions=risk.lock_sessions if risk else 0,
            deviation_cascade=risk.cascade_triggered if risk else False,
            mutation_tag=risk.mutation_tag if risk else None,
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

        # ── Set cooldown (skip if blocked) ────────────────────────────
        if result.stats_gained or (result.mastery_gained > 0):
            await db_training.set_training_cooldown(discord_id, path, SESSION_COOLDOWN_SECONDS)

        # ── Build embed ───────────────────────────────────────────────
        tier_display = TIER_DISPLAY[result.tier]
        path_display = PATH_DISPLAY[path]

        # Stat gain lines
        gain_lines = []
        for stat, delta in result.stats_gained.items():
            if delta != 0:
                sign  = "+" if delta > 0 else ""
                label = stat.upper().replace("_", " ")
                gain_lines.append(f"`{label:<12}` {sign}{delta:.1f}")

        # Mastery bar
        mastery_key   = f"mastery_{path.split('_')[0]}"
        new_mastery   = getattr(record, mastery_key) + result.mastery_gained
        tier_idx      = ["beginner", "advanced", "forbidden"].index(result.tier)
        next_thresh   = TIER_MASTERY_THRESHOLD.get(
            ["beginner", "advanced", "forbidden"][min(tier_idx + 1, 2)], 600
        )
        mastery_pct   = min(new_mastery / max(next_thresh, 1), 1.0)
        bar_filled    = int(mastery_pct * 12)
        mastery_bar   = "█" * bar_filled + "░" * (12 - bar_filled)

        # Fatigue indicator
        fatigue_after = result.fatigue_after
        fatigue_bar   = "🔥" * int(fatigue_after) + "⬜" * int(10 - fatigue_after)

        embed_color = {
            "beginner":  discord.Color.blue(),
            "advanced":  discord.Color.purple(),
            "forbidden": discord.Color.red(),
        }.get(result.tier, discord.Color.blurple())

        desc = result.narrative

        embed = build_embed(
            interaction,
            title=f"{PATH_EMOJI[path]} {path_display} — {tier_display} Tier",
            description=desc,
            color=embed_color,
            fields=[
                {
                    "name":   "📊 Stat Gains",
                    "value":  "\n".join(gain_lines) if gain_lines else "*No gains this session*",
                    "inline": True,
                },
                {
                    "name":  f"📿 Mastery [{mastery_bar}]",
                    "value": f"`{new_mastery}` / `{next_thresh}` EXP (+{result.mastery_gained})",
                    "inline": True,
                },
                {
                    "name":  "💢 Fatigue",
                    "value": f"{fatigue_bar} `{fatigue_after:.1f}/10`",
                    "inline": False,
                },
            ],
        )

        if result.streak_bonus > 0:
            embed.set_footer(text=f"Streak bonus active: +{result.streak_bonus*100:.0f}% gains  •  Rotate paths to recover fatigue")

        await safe_edit(interaction, embed=embed)

    # ------------------------------------------------------------------
    # /training  (profile view)
    # ------------------------------------------------------------------

    @app_commands.command(name="training", description="View your training stats and progress.")
    async def training_profile(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction, ephemeral=True)

        discord_id = interaction.user.id
        guild_id   = interaction.guild_id or 0

        record = await db_training.get_or_create_training_stats(discord_id, guild_id)

        # Stat bars
        def stat_bar(value: float, cap: int) -> str:
            pct    = value / max(cap, 1)
            filled = int(pct * 10)
            return "█" * filled + "░" * (10 - filled)

        # Use current tier caps for the bar display
        atk_cap = STAT_CAPS[record.tier_body]["atk"]
        def_cap = STAT_CAPS[record.tier_body]["def"]
        spe_cap = STAT_CAPS[record.tier_flow]["spe"]
        eva_cap = STAT_CAPS[record.tier_flow]["eva"]
        crit_cap    = STAT_CAPS[record.tier_killing]["crit_chance"]
        crit_dmg_cap = STAT_CAPS[record.tier_killing]["crit_dmg"]

        body_lines = (
            f"⚔️ **ATK**  `{stat_bar(record.atk, atk_cap)}` {int(record.atk)}/{atk_cap}\n"
            f"🛡️ **DEF**  `{stat_bar(record.def_, def_cap)}` {int(record.def_)}/{def_cap}\n"
            f"📿 Mastery: `{record.mastery_body}` EXP  |  Tier: **{TIER_DISPLAY[record.tier_body]}**"
        )
        flow_lines = (
            f"💨 **SPE**  `{stat_bar(record.spe, spe_cap)}` {int(record.spe)}/{spe_cap}\n"
            f"🌀 **EVA**  `{stat_bar(record.eva, eva_cap)}` {int(record.eva)}/{eva_cap}\n"
            f"📿 Mastery: `{record.mastery_flow}` EXP  |  Tier: **{TIER_DISPLAY[record.tier_flow]}**"
        )
        kill_lines = (
            f"🎯 **CRIT%**  `{stat_bar(record.crit_chance, crit_cap)}` {int(record.crit_chance)}/{crit_cap}\n"
            f"💥 **CRIT DMG** `{stat_bar(record.crit_dmg, crit_dmg_cap)}` {int(record.crit_dmg)}/{crit_dmg_cap}\n"
            f"📿 Mastery: `{record.mastery_killing}` EXP  |  Tier: **{TIER_DISPLAY[record.tier_killing]}**"
        )

        # Injury / lock status
        status_parts = []
        if record.injury_body_remaining > 0:
            status_parts.append(f"🩹 Body Tempering locked `{record.injury_body_remaining}` session(s)")
        if record.injury_flow_remaining > 0:
            status_parts.append(f"🩹 Flow Arts locked `{record.injury_flow_remaining}` session(s)")
        if record.injury_killing_remaining > 0:
            status_parts.append(f"🩹 Killing Sense locked `{record.injury_killing_remaining}` session(s)")
        if record.cascade_lock > 0:
            status_parts.append(f"⚠️ Qi Cascade — all paths locked `{record.cascade_lock}` session(s)")

        fatigue_bar = "🔥" * int(record.fatigue) + "⬜" * int(10 - record.fatigue)
        status_str  = "\n".join(status_parts) if status_parts else "✅ No injuries or locks."

        tags_str = (
            "  ".join(f"`{t.replace('_', ' ').title()}`" for t in record.passive_tags)
            if record.passive_tags else "*None yet*"
        )

        total = int(record.atk + record.def_ + record.spe + record.eva + record.crit_chance + record.crit_dmg)

        embed = build_embed(
            interaction,
            title=f"⚔️ {interaction.user.display_name}'s Training Record",
            description=f"**Total Combat Power from Training:** `{total}`",
            color=discord.Color.dark_gold(),
            fields=[
                {"name": "🩸 Body Tempering",  "value": body_lines,  "inline": False},
                {"name": "🌬️ Flow Arts",        "value": flow_lines,  "inline": False},
                {"name": "🔥 Killing Sense",    "value": kill_lines,  "inline": False},
                {"name": "💢 Fatigue",           "value": f"{fatigue_bar} `{record.fatigue:.1f}/10`", "inline": True},
                {"name": "⚠️ Status",            "value": status_str,  "inline": True},
                {"name": "🧬 Passive Tags",      "value": tags_str,    "inline": False},
            ],
        )
        await safe_edit(interaction, embed=embed)

    # ------------------------------------------------------------------
    # /training rest
    # ------------------------------------------------------------------

    @app_commands.command(name="rest", description="Meditate to reduce fatigue. 1-hour cooldown.")
    async def rest(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction, ephemeral=True)

        discord_id = interaction.user.id
        guild_id   = interaction.guild_id or 0

        cd = await db_training.get_training_cooldown(discord_id, "rest")
        if cd:
            cd_aware = cd if cd.tzinfo else cd.replace(tzinfo=timezone.utc)
            remaining = (cd_aware - datetime.now(timezone.utc)).total_seconds()
            mins, secs = divmod(int(remaining), 60)
            await safe_edit(interaction, embed=error_embed(
                interaction,
                f"You are still recovering. Come back in `{mins}m {secs}s`.",
                title="Already Resting",
            ))
            return

        # Reduce fatigue by 3.0, set 1-hour cooldown
        await db_training.decay_fatigue(discord_id, guild_id, hours_elapsed=6.0)
        await db_training.set_training_cooldown(discord_id, "rest", 3600)

        record = await db_training.get_training_stats(discord_id, guild_id)
        fatigue = record.fatigue if record else 0.0

        await safe_edit(interaction, embed=success_embed(
            interaction,
            f"You settle into a meditative stance.\nFatigue reduced — current: `{fatigue:.1f}/10`",
            title="🧘 Rest Session Complete",
        ))

    # ------------------------------------------------------------------
    # /training leaderboard
    # ------------------------------------------------------------------

    @app_commands.command(name="training_leaderboard", description="Top warriors ranked by total training power.")
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

        lines = []
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        for i, row in enumerate(rows):
            name  = row["display_name"]
            total = int(row["total_power"])
            atk   = int(row["atk"])
            spe   = int(row["spe"])
            crit  = int(row["crit_chance"])
            lines.append(
                f"{medals[i]} **{name}**  `Power {total}`  "
                f"⚔️{atk} 💨{spe} 🎯{crit}%"
            )

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