from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cultivation.constants import (
    AFFINITY_BREAKTHROUGH_MODIFIER,
    BREAKTHROUGH_ODDS,
    FAIL_CONSEQUENCES,
)
from db import cultivators as db


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class BreakthroughResult:
    outcome:      str            # "success" | "minor_fail" | "major_fail"
    overflow:     bool           # True if double-stage advance
    qi_lost:      int
    cooldown_end: datetime | None
    realm_before: str
    stage_before: int
    realm_after:  str
    stage_after:  int
    message:      str            # Flavour text for the embed


# ---------------------------------------------------------------------------
# Core resolution
# ---------------------------------------------------------------------------

async def attempt_breakthrough(row: dict) -> BreakthroughResult:
    """
    Resolve a breakthrough attempt for the given cultivator row.
    Applies all DB side-effects (qi loss, stage advance, cooldown).
    Returns a BreakthroughResult describing what happened.
    """
    discord_id   = row["discord_id"]
    realm        = row["realm"]
    stage        = row["stage"]
    affinity     = row["affinity"] or "water"
    stabilise    = row["stabilise_used"]
    current_qi   = row["qi"]

    # --- Build adjusted odds ---
    base_success, base_minor, base_major = BREAKTHROUGH_ODDS[realm]
    affinity_mod = AFFINITY_BREAKTHROUGH_MODIFIER.get(affinity, 0.0)
    stabilise_mod = 10.0 if not stabilise else 0.0   # stabilise not yet consumed = bonus

    success_chance = min(base_success + affinity_mod + stabilise_mod, 97.0)
    minor_chance   = base_minor
    # major absorbs whatever is left
    major_chance   = max(100.0 - success_chance - minor_chance, 0.0)

    roll = random.uniform(0, 100)

    realm_before = realm
    stage_before = stage

    # --- Success ---
    if roll < success_chance:
        # Qi Overflow: rare on high foundation — simplified here as a 5% extra roll on success
        overflow = random.random() < 0.05

        updated = await db.advance_stage(discord_id, row)
        if overflow:
            # Advance once more if not already at max
            updated = await db.advance_stage(discord_id, updated)

        await db.exit_tribulation(discord_id)
        await db.log_breakthrough(discord_id, realm, stage, "success", overflow=overflow)

        msg = _success_message(realm, stage, overflow)
        return BreakthroughResult(
            outcome="success",
            overflow=overflow,
            qi_lost=0,
            cooldown_end=None,
            realm_before=realm_before,
            stage_before=stage_before,
            realm_after=updated["realm"],
            stage_after=updated["stage"],
            message=msg,
        )

    # --- Minor Failure ---
    elif roll < success_chance + minor_chance:
        loss_pct, cd_minutes = FAIL_CONSEQUENCES[realm]["minor_fail"]
        qi_lost = int(current_qi * loss_pct)

        await db.apply_qi_loss(discord_id, loss_pct)
        await db.exit_tribulation(discord_id)

        cooldown_end = None
        if cd_minutes:
            cooldown_end = datetime.now(timezone.utc) + timedelta(minutes=cd_minutes)
            await db.set_breakthrough_cooldown(discord_id, cooldown_end)

        await db.log_breakthrough(discord_id, realm, stage, "minor_fail", qi_lost=qi_lost)

        return BreakthroughResult(
            outcome="minor_fail",
            overflow=False,
            qi_lost=qi_lost,
            cooldown_end=cooldown_end,
            realm_before=realm_before,
            stage_before=stage_before,
            realm_after=realm,
            stage_after=stage,
            message=_minor_fail_message(realm, affinity),
        )

    # --- Major Failure ---
    else:
        loss_pct, cd_minutes = FAIL_CONSEQUENCES[realm]["major_fail"]
        qi_lost = int(current_qi * loss_pct)

        await db.apply_qi_loss(discord_id, loss_pct)
        await db.exit_tribulation(discord_id)

        cooldown_end = datetime.now(timezone.utc) + timedelta(minutes=cd_minutes)
        await db.set_breakthrough_cooldown(discord_id, cooldown_end)

        await db.log_breakthrough(discord_id, realm, stage, "major_fail", qi_lost=qi_lost)

        return BreakthroughResult(
            outcome="major_fail",
            overflow=False,
            qi_lost=qi_lost,
            cooldown_end=cooldown_end,
            realm_before=realm_before,
            stage_before=stage_before,
            realm_after=realm,
            stage_after=stage,
            message=_major_fail_message(realm, affinity),
        )


# ---------------------------------------------------------------------------
# Flavour text
# ---------------------------------------------------------------------------

def _success_message(realm: str, stage: int, overflow: bool) -> str:
    if overflow:
        return (
            "⚡ **Qi Overflow!** The heavens tremble. Your foundation was immaculate — "
            "the tribulation energy surged through your core and carried you two stages forward. "
            "The cultivation world takes notice."
        )
    lines = {
        "mortal":          "The barrier crumbles. A faint warmth spreads through your limbs — the first breath of a true cultivator.",
        "qi_gathering":    "Heaven and earth Qi floods your meridians. The threshold breaks and your core stirs with new power.",
        "qi_condensation": "Your scattered Qi compresses into a dense orb. The tribulation energy dissipates. You endure.",
        "qi_refining":     "Impurities burn away. What remains is harder, purer. The Path ahead grows narrower — and more treacherous.",
    }
    return f"✅ **Breakthrough!** {lines.get(realm, 'You advance.')}"


def _minor_fail_message(realm: str, affinity: str) -> str:
    affinity_lines = {
        "fire":      "Your flames burned too eagerly — the surge collapsed inward.",
        "water":     "The current faltered. Your flow lost its rhythm at the critical moment.",
        "lightning": "The bolt misfired. The discharge scattered before it could reshape your core.",
        "wood":      "Your roots held, but the branch bent too far and snapped.",
        "earth":     "The stone cracked under its own pressure. Stability was not enough.",
    }
    return (
        f"⚠️ **Minor Failure.** {affinity_lines.get(affinity, 'The attempt faltered.')} "
        f"Some Qi was lost but the damage is shallow. Recover and try again."
    )


def _major_fail_message(realm: str, affinity: str) -> str:
    affinity_lines = {
        "fire":      "The inferno turned on itself. Your meridians scorched from within.",
        "water":     "The tide reversed. Your gathered Qi drained away like water through broken stone.",
        "lightning": "Overload. The discharge tore through your channels, leaving them frayed and weakened.",
        "wood":      "The root system collapsed. Seasons of growth undone in a single moment.",
        "earth":     "The mountain fell. What you spent so long building crumbled under tribulation pressure.",
    }
    return (
        f"❌ **Major Failure.** {affinity_lines.get(affinity, 'The tribulation overwhelmed you.')} "
        f"A significant portion of your Qi has scattered. You must wait before attempting again."
    )