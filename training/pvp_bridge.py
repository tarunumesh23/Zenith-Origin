"""
training/pvp_bridge.py
~~~~~~~~~~~~~~~~~~~~~~~
Bridges training stats into the existing combat/resolver.py power formula.

How training stats affect combat
---------------------------------
ATK         → multiplicative boost on raw power roll
DEF         → damage reduction applied to incoming effective damage
SPE         → small additive roll bonus (fast fighter advantage)
EVA         → per-hit dodge chance (50% damage reduction on dodge)
CRIT_CHANCE → chance to deal 1.5× effective damage on a hit
CRIT_DMG    → scales the crit multiplier beyond the base 1.5×

Usage in cogs (challenge / duel / ambush) — replace the bare _roll_power call:

    from training.pvp_bridge import TrainingModifiers, load_modifiers, apply_training_to_round

    # Load modifiers once per fight
    a_mods = await load_modifiers(a_row["discord_id"], guild_id)
    b_mods = await load_modifiers(b_row["discord_id"], guild_id)

    # Inside the round-resolution loop:
    result = apply_training_to_round(
        a_raw_power, b_raw_power,
        a_mods, b_mods,
        a_action, b_action,   # "strike" | "guard"
    )
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from training.constants import (
    PVP_ATK_WEIGHT,
    PVP_CRIT_DMG_BONUS,
    PVP_DEF_MAX_REDUCTION,
    PVP_DEF_SCALE_CAP,
    PVP_EVA_DODGE_CAP,
    PVP_EVA_SCALE_CAP,
    PVP_SPE_ROLL_BONUS,
)


# ---------------------------------------------------------------------------
# Modifier snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrainingModifiers:
    """Immutable training stat snapshot for one combatant, used per fight."""
    discord_id:  int
    atk:         float = 0.0
    def_:        float = 0.0
    spe:         float = 0.0
    eva:         float = 0.0
    crit_chance: float = 0.0
    crit_dmg:    float = 0.0

    # Passive tags (affect combat logic)
    passive_tags: tuple[str, ...] = ()

    # ── Derived properties ─────────────────────────────────

    @property
    def atk_multiplier(self) -> float:
        """Raw power roll multiplier from ATK: e.g. ATK=50 → ×1.60"""
        return 1.0 + (self.atk * PVP_ATK_WEIGHT)

    @property
    def def_reduction(self) -> float:
        """Fraction of incoming damage absorbed: DEF 100 → 30% reduction."""
        return min(self.def_ / PVP_DEF_SCALE_CAP * PVP_DEF_MAX_REDUCTION, PVP_DEF_MAX_REDUCTION)

    @property
    def spe_bonus(self) -> float:
        """Additive roll bonus from SPE."""
        return self.spe * PVP_SPE_ROLL_BONUS

    @property
    def eva_chance(self) -> float:
        """Probability to dodge (reduce incoming damage by 50%)."""
        raw = self.eva / PVP_EVA_SCALE_CAP * PVP_EVA_DODGE_CAP
        return min(raw, PVP_EVA_DODGE_CAP)

    @property
    def crit_multiplier(self) -> float:
        """
        How hard a crit hits.
        Base = PVP_CRIT_DMG_BONUS (1.5×).
        CRIT_DMG scales it further: CRIT_DMG 100 → ×2.0
        """
        extra = (self.crit_dmg / 100.0) * 0.50   # 0–0.5 additional multiplier
        return PVP_CRIT_DMG_BONUS + extra

    @property
    def crit_chance_fraction(self) -> float:
        """CRIT_CHANCE stat converted to 0–1 probability (max 50%)."""
        return min(self.crit_chance / 100.0 * 0.50, 0.50)


# ---------------------------------------------------------------------------
# DB loader
# ---------------------------------------------------------------------------

async def load_modifiers(discord_id: int, guild_id: int) -> TrainingModifiers:
    """
    Fetch training stats from DB and return a frozen TrainingModifiers.
    Returns all-zero modifiers if the player has no training record yet
    (graceful — doesn't break older players or fights before training exists).
    """
    from db.training import get_training_stats

    record = await get_training_stats(discord_id, guild_id)
    if record is None:
        return TrainingModifiers(discord_id=discord_id)

    return TrainingModifiers(
        discord_id=discord_id,
        atk=record.atk,
        def_=record.def_,
        spe=record.spe,
        eva=record.eva,
        crit_chance=record.crit_chance,
        crit_dmg=record.crit_dmg,
        passive_tags=tuple(record.passive_tags),
    )


# ---------------------------------------------------------------------------
# Round application
# ---------------------------------------------------------------------------

@dataclass
class TrainingRoundResult:
    """Extended round result including training-derived events."""
    a_effective:    float   # damage dealt BY A to B (after B's defences)
    b_effective:    float   # damage dealt BY B to A (after A's defences)
    a_critted:      bool
    b_critted:      bool
    a_dodged:       bool    # A dodged B's hit
    b_dodged:       bool    # B dodged A's hit
    round_winner:   str     # "a" | "b" | "tie"
    training_notes: list[str]   # flavour lines for the embed


def apply_training_to_round(
    a_raw_power:  float,
    b_raw_power:  float,
    a_mods:       TrainingModifiers,
    b_mods:       TrainingModifiers,
    a_action:     str = "strike",
    b_action:     str = "strike",
) -> TrainingRoundResult:
    """
    Apply training modifiers to a single combat round.

    Parameters
    ----------
    a_raw_power / b_raw_power:
        Raw power values from combat.resolver._roll_power (unchanged).
    a_mods / b_mods:
        Training modifier snapshots loaded at fight start.
    a_action / b_action:
        "strike" or "guard" — from CombatSession action views.

    The existing guard absorption constants from combat/session.py are
    preserved; training stats layer on top.
    """
    GUARD_POWER_MOD = 0.60
    GUARD_ABSORB    = 0.40

    notes: list[str] = []

    # ── Apply ATK and SPE to raw power ──────────────────────────────────
    a_power = (a_raw_power * a_mods.atk_multiplier + a_mods.spe_bonus) * (
        GUARD_POWER_MOD if a_action == "guard" else 1.0
    )
    b_power = (b_raw_power * b_mods.atk_multiplier + b_mods.spe_bonus) * (
        GUARD_POWER_MOD if b_action == "guard" else 1.0
    )

    # ── Damage taken (before DEF and EVA) ───────────────────────────────
    a_takes_raw = b_power * (1.0 - GUARD_ABSORB if a_action == "guard" else 1.0)
    b_takes_raw = a_power * (1.0 - GUARD_ABSORB if b_action == "guard" else 1.0)

    # ── CRIT check ───────────────────────────────────────────────────────
    a_critted = random.random() < a_mods.crit_chance_fraction
    b_critted = random.random() < b_mods.crit_chance_fraction

    if a_critted:
        b_takes_raw *= a_mods.crit_multiplier
        notes.append(f"⚡ Critical Strike! ({a_mods.crit_multiplier:.1f}× damage)")

    if b_critted:
        a_takes_raw *= b_mods.crit_multiplier
        notes.append(f"⚡ Critical Counter! ({b_mods.crit_multiplier:.1f}× damage)")

    # ── EVA dodge check ──────────────────────────────────────────────────
    a_dodged = random.random() < a_mods.eva_chance
    b_dodged = random.random() < b_mods.eva_chance

    if a_dodged:
        a_takes_raw *= 0.50
        notes.append("💨 Evasion! (50% damage reduced)")

    if b_dodged:
        b_takes_raw *= 0.50
        notes.append("💨 Evasion! (50% damage reduced)")

    # ── DEF damage reduction ─────────────────────────────────────────────
    a_effective = a_takes_raw * (1.0 - a_mods.def_reduction)
    b_effective = b_takes_raw * (1.0 - b_mods.def_reduction)

    # ── Round winner: who dealt more damage to opponent ──────────────────
    if b_effective > a_effective:
        round_winner = "a"
    elif a_effective > b_effective:
        round_winner = "b"
    else:
        round_winner = "tie"

    return TrainingRoundResult(
        a_effective=round(a_effective, 2),
        b_effective=round(b_effective, 2),
        a_critted=a_critted,
        b_critted=b_critted,
        a_dodged=a_dodged,
        b_dodged=b_dodged,
        round_winner=round_winner,
        training_notes=notes,
    )


# ---------------------------------------------------------------------------
# Stat summary for embed fields
# ---------------------------------------------------------------------------

def format_training_stats_inline(mods: TrainingModifiers) -> str:
    """
    One-line training power summary for fight embeds.
    e.g.  ⚔️45  🛡️30  💨52  🌀38  🎯22%  💥1.7×
    """
    parts = [
        f"⚔️{int(mods.atk)}",
        f"🛡️{int(mods.def_)}",
        f"💨{int(mods.spe)}",
        f"🌀{int(mods.eva)}",
        f"🎯{int(mods.crit_chance)}%",
        f"💥{mods.crit_multiplier:.1f}×",
    ]
    return "  ".join(parts)