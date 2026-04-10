from __future__ import annotations

"""
talent/cultivation_bridge.py
────────────────────────────
Single source-of-truth for how an active PlayerTalent influences cultivation.

All cultivation code should import *only* ``get_cultivation_bonuses`` —
it never needs to know about tags, rarities, or evolution stages directly.

Bonus dict schema
─────────────────
{
    "qi_multiplier":          float,   # multiplies BASE_QI_PER_SECOND (1.0 = no effect)
    "breakthrough_bonus":     float,   # additive % points on success chance (e.g. 5.0 = +5%)
    "overflow_chance":        float,   # absolute overflow probability (replaces default 0.05)
    "negate_qi_loss_chance":  float,   # 0.0–1.0 probability of negating Qi loss on fail
    "meditate_cooldown_mult": float,   # multiplies the 3600 s meditate cooldown (< 1.0 = shorter)
    "qi_threshold_bonus":     float,   # additive fraction of base threshold (e.g. 0.10 = +10%)
}
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from talent.models import PlayerTalent

# ---------------------------------------------------------------------------
# Tag → bonus-key mapping
# Each entry: (tags_required, bonus_key, base_value)
# base_value is scaled by rarity multiplier and evolution stage before use.
# ---------------------------------------------------------------------------

_TAG_BONUSES: list[tuple[frozenset[str], str, float]] = [
    # Qi accrual
    (frozenset({"qi"}),          "qi_multiplier",          0.05),
    (frozenset({"flow"}),        "qi_multiplier",          0.03),
    (frozenset({"spirit"}),      "qi_multiplier",          0.04),

    # Breakthrough success
    (frozenset({"heaven"}),      "breakthrough_bonus",     2.0),
    (frozenset({"fate"}),        "breakthrough_bonus",     2.0),
    (frozenset({"dao"}),         "breakthrough_bonus",     3.0),

    # Overflow / double-stage chance
    (frozenset({"dao"}),         "overflow_chance",        0.02),
    (frozenset({"chaos"}),       "overflow_chance",        0.03),

    # Negate Qi loss on breakthrough failure
    (frozenset({"rebirth"}),     "negate_qi_loss_chance",  0.15),

    # Meditate cooldown reduction (bonus shrinks the multiplier below 1.0)
    (frozenset({"mind"}),        "meditate_cooldown_mult", -0.05),
    (frozenset({"lightning"}),   "meditate_cooldown_mult", -0.04),

    # Qi threshold expansion
    (frozenset({"body"}),        "qi_threshold_bonus",     0.03),
    (frozenset({"earth"}),       "qi_threshold_bonus",     0.04),
]

# ---------------------------------------------------------------------------
# Neutral / identity values for each bonus key
# ---------------------------------------------------------------------------

_IDENTITY: dict[str, float] = {
    "qi_multiplier":          1.0,
    "breakthrough_bonus":     0.0,
    "overflow_chance":        0.05,   # default chance in breakthrough.py
    "negate_qi_loss_chance":  0.0,
    "meditate_cooldown_mult": 1.0,
    "qi_threshold_bonus":     0.0,
}

# ---------------------------------------------------------------------------
# Evolution stage amplifiers  (stage 0 / 1 / 2)
# ---------------------------------------------------------------------------

_EVOLUTION_AMP: list[float] = [1.0, 1.35, 1.75]

# ---------------------------------------------------------------------------
# Hard caps (safety rails)
# ---------------------------------------------------------------------------

_CAPS: dict[str, float] = {
    "qi_multiplier":          3.0,    # at most 3× base Qi rate from talent alone
    "breakthrough_bonus":    20.0,    # at most +20 percentage points
    "overflow_chance":        0.30,   # at most 30% overflow
    "negate_qi_loss_chance":  0.75,   # at most 75% negate
    "meditate_cooldown_mult": 0.40,   # cooldown cannot drop below 40% of base
    "qi_threshold_bonus":     0.50,   # threshold cannot grow more than +50%
}

# Minimum caps for bonuses that shrink toward zero
_FLOOR: dict[str, float] = {
    "meditate_cooldown_mult": 0.40,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cultivation_bonuses(active_talent: "PlayerTalent | None") -> dict[str, float]:
    """
    Return a bonus dict for the active talent.

    If the player has no active talent, returns the identity dict (no bonuses).
    Cultivation cogs should call this once per command invocation and pass the
    relevant keys where needed.
    """
    result = dict(_IDENTITY)

    if active_talent is None:
        return result

    talent_tags   = frozenset(active_talent.tags)
    rarity_mult   = active_talent.multiplier                          # e.g. 1.0, 2.0, 8.0 …
    evolution_amp = _EVOLUTION_AMP[active_talent.evolution_stage]     # 1.0 / 1.35 / 1.75

    for required_tags, bonus_key, base_value in _TAG_BONUSES:
        if not required_tags.issubset(talent_tags):
            continue

        scaled = base_value * rarity_mult * evolution_amp

        if bonus_key == "qi_multiplier":
            result["qi_multiplier"] += scaled          # additive stacking
        elif bonus_key == "breakthrough_bonus":
            result["breakthrough_bonus"] += scaled
        elif bonus_key == "overflow_chance":
            result["overflow_chance"] += scaled        # additive on top of default
        elif bonus_key == "negate_qi_loss_chance":
            result["negate_qi_loss_chance"] += scaled
        elif bonus_key == "meditate_cooldown_mult":
            result["meditate_cooldown_mult"] += scaled  # scaled is negative, so this shrinks
        elif bonus_key == "qi_threshold_bonus":
            result["qi_threshold_bonus"] += scaled

    # Apply caps
    for key, cap in _CAPS.items():
        if bonus_key == "meditate_cooldown_mult":
            result[key] = max(_FLOOR.get(key, 0.0), min(result[key], cap))
        else:
            result[key] = min(result[key], cap)

    # Floor pass for any shrinking bonuses
    for key, floor in _FLOOR.items():
        result[key] = max(floor, result[key])

    return result


def describe_bonuses(active_talent: "PlayerTalent | None") -> str:
    """
    Human-readable summary of active cultivation bonuses.
    Used by /talent command to show the player what their talent does.
    """
    bonuses = get_cultivation_bonuses(active_talent)
    lines: list[str] = []

    qi_mult = bonuses["qi_multiplier"]
    if qi_mult > 1.0:
        lines.append(f"⚡ Qi accrual **×{qi_mult:.2f}**")

    bt_bonus = bonuses["breakthrough_bonus"]
    if bt_bonus > 0:
        lines.append(f"🎯 Breakthrough chance **+{bt_bonus:.1f}%**")

    ov = bonuses["overflow_chance"]
    if ov > 0.05:
        lines.append(f"✨ Overflow (double-stage) chance **{ov * 100:.1f}%**")

    negate = bonuses["negate_qi_loss_chance"]
    if negate > 0:
        lines.append(f"🛡️ Negate Qi loss on failure **{negate * 100:.0f}%** chance")

    cd_mult = bonuses["meditate_cooldown_mult"]
    if cd_mult < 1.0:
        lines.append(f"🧘 Meditate cooldown **{cd_mult * 100:.0f}%** of base")

    thr = bonuses["qi_threshold_bonus"]
    if thr > 0:
        lines.append(f"📈 Qi threshold **+{thr * 100:.0f}%**")

    return "\n".join(lines) if lines else "No cultivation bonuses."