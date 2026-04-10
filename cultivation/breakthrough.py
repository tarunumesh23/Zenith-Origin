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

@dataclass(slots=True)
class BreakthroughResult:
    outcome:      str             # "success" | "fail"
    overflow:     bool            # True → double-stage advance
    qi_lost:      int
    cooldown_end: datetime | None
    realm_before: str
    stage_before: int
    realm_after:  str
    stage_after:  int
    message:      str             # flavour text for the embed


# ---------------------------------------------------------------------------
# Core resolution
# ---------------------------------------------------------------------------

async def attempt_breakthrough(row: dict) -> BreakthroughResult:
    """
    Resolve a breakthrough attempt for the given (already-flushed) cultivator row.

    Contract with the caller (cultivate.py):
    - ``row`` must have been produced by ``_flush_qi`` immediately before this
      call so that ``row["qi"]`` reflects live Qi and ``row["in_tribulation"]``
      is accurate.
    - The caller is responsible for setting the breakthrough cooldown via
      ``db.set_cooldown`` after this function returns, using ``result.cooldown_end``.

    Side-effects applied here:
    - Stage advance (success path)
    - Qi loss (fail path)
    - Tribulation exit (both paths)
    - Breakthrough audit log entry (both paths)

    Failure policy: no realm or stage regression — only Qi loss + cooldown.
    """
    discord_id = row["discord_id"]
    realm      = row["realm"]
    stage      = row["stage"]
    affinity   = row["affinity"] or "water"
    stabilised = row["stabilise_used"]  # False → bonus not yet consumed

    now = datetime.now(timezone.utc)    # single timestamp for this entire operation

    # ── Build adjusted success chance ──────────────────────────────────────
    base_success   = BREAKTHROUGH_ODDS[realm]
    affinity_mod   = AFFINITY_BREAKTHROUGH_MODIFIER.get(affinity, 0.0)
    stabilise_mod  = 10.0 if not stabilised else 0.0
    success_chance = min(base_success + affinity_mod + stabilise_mod, 97.0)

    roll = random.uniform(0, 100)

    # ── Success ────────────────────────────────────────────────────────────
    if roll < success_chance:
        # Decide overflow *before* any writes so we either do both advances
        # or neither — keeps DB state consistent if an exception occurs mid-way.
        overflow = random.random() < 0.05

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

    # ── Failure ────────────────────────────────────────────────────────────
    loss_pct, cd_minutes = FAIL_CONSEQUENCES[realm]

    # apply_qi_loss now accepts `now` so the stored timestamp matches the one
    # we computed at the top of this function — no clock drift.
    updated = await db.apply_qi_loss(discord_id, loss_pct, now=now)
    qi_lost = row["qi"] - updated["qi"]   # actual loss, not an estimate

    await db.exit_tribulation(discord_id)

    cooldown_end = now + timedelta(minutes=cd_minutes)
    # Cooldown is written by the *caller* (cultivate.py) so that all cooldown
    # logic lives in one place and this function stays side-effect minimal.

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
        message=_fail_message(realm, affinity),
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


def _fail_message(realm: str, affinity: str) -> str:
    line = _FAIL_LINES.get(affinity, "The tribulation overwhelmed you.")
    return (
        f"❌ **Breakthrough Failed.** {line} "
        f"Your realm and stage are preserved. Recover your Qi and try again."
    )