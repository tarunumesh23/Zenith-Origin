"""
cultivation/breakthrough.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Core breakthrough resolution.

Changes in this revision
────────────────────────
• Spirit Root bonuses are now injected here alongside talent bonuses.
  The caller (cultivate.py) should pass ``root_value`` (int | None) so the
  engine can merge root bonuses into the combined bonus set.
• ``attempt_breakthrough`` signature gains one new optional parameter:
  ``root_value: int | None = None``

  If you are not yet ready to thread ``root_value`` through your cog, it
  defaults to ``None`` which means no root bonus is applied — fully backward-
  compatible.

• The merge uses ``spirit_roots.cultivation_bridge.merge_bonuses`` which
  combines talent and root bonuses, applying hard caps after merging.
"""
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
from talent.cultivation_bridge import get_spirit_root_bonuses, merge_bonuses


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BreakthroughResult:
    outcome:         str             # "success" | "fail"
    overflow:        bool            # True → double-stage advance
    qi_lost:         int
    cooldown_end:    datetime | None
    realm_before:    str
    stage_before:    int
    realm_after:     str
    stage_after:     int
    message:         str             # flavour text for the embed
    qi_loss_negated: bool = False    # True → talent/root rebirth bonus triggered


# ---------------------------------------------------------------------------
# Core resolution
# ---------------------------------------------------------------------------

async def attempt_breakthrough(
    row: dict,
    talent_breakthrough_bonus: float = 0.0,
    talent_overflow_chance:    float = 0.05,
    talent_negate_qi_loss:     float = 0.0,
    *,
    root_value: int | None = None,
) -> BreakthroughResult:
    """
    Resolve a breakthrough attempt for the given (already-flushed) cultivator row.

    Parameters
    ----------
    row:
        Cultivator DB row produced by ``_flush_qi`` immediately before this call.
    talent_breakthrough_bonus:
        Additive percentage points from ``talent.cultivation_bridge``.
    talent_overflow_chance:
        Absolute overflow probability from ``talent.cultivation_bridge``.
    talent_negate_qi_loss:
        0.0–1.0 chance from ``talent.cultivation_bridge``.
    root_value:
        The player's current Spirit Root tier value (1–5), or ``None``.
        When supplied, root bonuses are merged with the talent bonuses using
        ``spirit_roots.cultivation_bridge.merge_bonuses`` before applying caps.

    Side-effects applied here:
    - Stage advance (success path)
    - Qi loss (fail path, unless negated)
    - Tribulation exit (both paths)
    - Breakthrough audit log entry (both paths)

    Failure policy: no realm or stage regression — only Qi loss + cooldown.
    """
    discord_id = row["discord_id"]
    realm      = row["realm"]
    stage      = row["stage"]
    affinity   = row["affinity"] or "water"
    stabilised = row["stabilise_used"]

    now = datetime.now(timezone.utc)

    # ── Merge talent + Spirit Root bonuses ──────────────────────────────────
    talent_bonuses: dict[str, float] = {
        "qi_multiplier":          1.0,                      # not used here but required by merge
        "breakthrough_bonus":     talent_breakthrough_bonus,
        "overflow_chance":        talent_overflow_chance,
        "negate_qi_loss_chance":  talent_negate_qi_loss,
        "meditate_cooldown_mult": 1.0,                      # not used here
        "qi_threshold_bonus":     0.0,                      # not used here
    }
    root_bonuses  = get_spirit_root_bonuses(root_value)
    combined      = merge_bonuses(talent_bonuses, root_bonuses)

    breakthrough_bonus  = combined["breakthrough_bonus"]
    overflow_chance     = combined["overflow_chance"]
    negate_qi_loss      = combined["negate_qi_loss_chance"]

    # ── Build adjusted success chance ─────────────────────────────────────
    base_success   = BREAKTHROUGH_ODDS[realm]
    affinity_mod   = AFFINITY_BREAKTHROUGH_MODIFIER.get(affinity, 0.0)
    stabilise_mod  = 10.0 if not stabilised else 0.0
    success_chance = min(
        base_success + affinity_mod + stabilise_mod + breakthrough_bonus,
        97.0,
    )

    roll = random.uniform(0, 100)

    # ── Success ─────────────────────────────────────────────────────────────
    if roll < success_chance:
        overflow = random.random() < overflow_chance

        updated = await db.advance_stage(discord_id, row)
        if overflow:
            updated = await db.advance_stage(discord_id, updated)

        await db.exit_tribulation(discord_id)
        await db.log_breakthrough(
            discord_id, realm, stage, "success", overflow=overflow
        )

        return BreakthroughResult(
            outcome="success",
            overflow=overflow,
            qi_lost=0,
            cooldown_end=None,
            realm_before=realm,
            stage_before=stage,
            realm_after=updated["realm"],
            stage_after=updated["stage"],
            message=_success_message(realm, overflow),
        )

    # ── Failure ─────────────────────────────────────────────────────────────
    loss_pct, cd_minutes = FAIL_CONSEQUENCES[realm]

    qi_loss_negated = (
        negate_qi_loss > 0
        and random.random() < negate_qi_loss
    )

    if qi_loss_negated:
        updated = await db.set_qi(discord_id, row["qi"], now)
        qi_lost = 0
    else:
        updated = await db.apply_qi_loss(discord_id, loss_pct, now=now)
        qi_lost = row["qi"] - updated["qi"]

    await db.exit_tribulation(discord_id)

    cooldown_end = now + timedelta(minutes=cd_minutes)

    await db.log_breakthrough(
        discord_id, realm, stage, "fail", qi_lost=qi_lost
    )

    return BreakthroughResult(
        outcome="fail",
        overflow=False,
        qi_lost=qi_lost,
        cooldown_end=cooldown_end,
        realm_before=realm,
        stage_before=stage,
        realm_after=realm,
        stage_after=stage,
        message=_fail_message(realm, affinity, qi_loss_negated),
        qi_loss_negated=qi_loss_negated,
    )


# ---------------------------------------------------------------------------
# Flavour text
# ---------------------------------------------------------------------------

_SUCCESS_LINES: dict[str, str] = {
    "mortal":
        "The barrier crumbles. A faint warmth spreads through your limbs — "
        "the first breath of a true cultivator.",
    "qi_gathering":
        "Heaven and earth Qi floods your meridians. The threshold breaks "
        "and your core stirs with new power.",
    "qi_condensation":
        "Your scattered Qi compresses into a dense orb. "
        "The tribulation energy dissipates. You endure.",
    "qi_refining":
        "Impurities burn away. What remains is harder, purer. "
        "The Path ahead grows narrower — and more treacherous.",
}

_FAIL_LINES: dict[str, str] = {
    "fire":
        "Your flames burned too eagerly — the surge collapsed inward. "
        "The tribulation dissipates, but your Qi scattered.",
    "water":
        "The current faltered at the final moment. "
        "Your flow broke and Qi drained away like a receding tide.",
    "lightning":
        "The bolt misfired. The discharge scattered before it could reshape your core. "
        "The heavens are unimpressed.",
    "wood":
        "Your roots held, but the branch bent too far and snapped under tribulation pressure.",
    "earth":
        "The mountain cracked. Stability alone was not enough — the tribulation demanded more.",
}


def _success_message(realm: str, overflow: bool) -> str:
    if overflow:
        return (
            "⚡ **Qi Overflow!** The heavens tremble. Your foundation was immaculate — "
            "the tribulation energy surged through your core and carried you two stages forward. "
            "The cultivation world takes notice."
        )
    line = _SUCCESS_LINES.get(realm, "You advance.")
    return f"✅ **Breakthrough!** {line}"


def _fail_message(realm: str, affinity: str, negated: bool) -> str:
    if negated:
        return (
            "❌ **Breakthrough Failed — but your root held firm.** "
            "The tribulation energy scattered against your innate power. "
            "Your Qi was preserved. The Path endures."
        )
    line = _FAIL_LINES.get(affinity, "The tribulation overwhelmed you.")
    return (
        f"❌ **Breakthrough Failed.** {line} "
        f"Your realm and stage are preserved. Recover your Qi and try again."
    )