"""
talent/cultivation_bridge.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Translates a PlayerTalent's tags and multiplier into concrete cultivation
bonuses used by cultivate.py and breakthrough.py.

Also re-exports Spirit Root bonus helpers so that breakthrough.py and
cultivate.py have a single import point and circular imports are avoided.

Import order safety
-------------------
This module imports from spirit_roots.cultivation_bridge at the bottom
(deferred / inside functions is NOT needed — spirit_roots never imports
from talent/, so there is no cycle).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from talent.models import PlayerTalent

# ---------------------------------------------------------------------------
# Hard caps
# ---------------------------------------------------------------------------
_CAPS: dict[str, float] = {
    "qi_multiplier":          3.00,
    "breakthrough_bonus":    20.00,
    "overflow_chance":        0.30,
    "negate_qi_loss_chance":  0.75,
    "meditate_cooldown_mult": 0.40,
    "qi_threshold_bonus":     0.50,
}

# ---------------------------------------------------------------------------
# Identity / neutral bonus dict
# ---------------------------------------------------------------------------
_IDENTITY: dict[str, float] = {
    "qi_multiplier":          1.00,
    "breakthrough_bonus":     0.00,
    "overflow_chance":        0.05,   # base overflow chance even with no talent
    "negate_qi_loss_chance":  0.00,
    "meditate_cooldown_mult": 1.00,
    "qi_threshold_bonus":     0.00,
}

# ---------------------------------------------------------------------------
# Tag → bonus mapping
#
# IMPORTANT: these tags must match what's actually in TALENT_POOL entries.
# Talent tags use thematic words (fire, body, dragon, etc.).
# We map those thematic tags to mechanical bonuses here.
# ---------------------------------------------------------------------------
_TAG_BONUSES: dict[str, dict[str, float]] = {
    # Qi accrual tags
    "qi":           {"qi_multiplier": 0.08},
    "flow":         {"qi_multiplier": 0.06},
    "spirit":       {"qi_multiplier": 0.05},

    # Breakthrough tags
    "heaven":       {"breakthrough_bonus": 3.0, "overflow_chance": 0.01},
    "dao":          {"breakthrough_bonus": 4.0},
    "fate":         {"breakthrough_bonus": 2.0},
    "rebirth":      {"negate_qi_loss_chance": 0.08, "overflow_chance": 0.01},

    # Body / endurance tags
    "body":         {"qi_threshold_bonus": 0.04, "negate_qi_loss_chance": 0.03},
    "iron":         {"qi_threshold_bonus": 0.03},
    "earth":        {"qi_threshold_bonus": 0.04},

    # Speed / cooldown tags
    "wind":         {"meditate_cooldown_mult": -0.05},
    "speed":        {"meditate_cooldown_mult": -0.04},
    "lightning":    {"meditate_cooldown_mult": -0.06, "qi_multiplier": 0.04},

    # Combat / chaos tags (smaller cultivation benefit)
    "fire":         {"qi_multiplier": 0.06, "breakthrough_bonus": 1.0},
    "water":        {"breakthrough_bonus": 2.0, "negate_qi_loss_chance": 0.04},
    "wood":         {"qi_multiplier": 0.04, "qi_threshold_bonus": 0.02},
    "dragon":       {"qi_multiplier": 0.07, "qi_threshold_bonus": 0.03},
    "chaos":        {"overflow_chance": 0.02, "breakthrough_bonus": 1.0},
    "void":         {"overflow_chance": 0.02, "meditate_cooldown_mult": -0.03},
    "shadow":       {"meditate_cooldown_mult": -0.03, "negate_qi_loss_chance": 0.03},
    "star":         {"breakthrough_bonus": 2.0, "overflow_chance": 0.01},
    "mind":         {"breakthrough_bonus": 2.0, "meditate_cooldown_mult": -0.04},
    "ice":          {"negate_qi_loss_chance": 0.05, "qi_threshold_bonus": 0.03},
    "cosmic":       {"qi_multiplier": 0.15, "breakthrough_bonus": 5.0, "overflow_chance": 0.03},
    "combat":       {"qi_threshold_bonus": 0.02},

    # Exclusive/special tags
    "space":        {"meditate_cooldown_mult": -0.05, "overflow_chance": 0.01},
    "defense":      {"negate_qi_loss_chance": 0.05, "qi_threshold_bonus": 0.03},
}


# ---------------------------------------------------------------------------
# Talent bonus API
# ---------------------------------------------------------------------------

def get_cultivation_bonuses(talent: "PlayerTalent | None") -> dict[str, float]:
    """
    Return a bonus dict for the given active talent.

    If ``talent`` is ``None`` (no active talent), the identity dict is
    returned so callers never have to guard against missing keys.
    """
    bonuses = dict(_IDENTITY)

    if talent is None:
        return bonuses

    multiplier = talent.multiplier or 1.0
    tags       = talent.tags or []

    for tag in tags:
        tag_contrib = _TAG_BONUSES.get(tag)
        if tag_contrib is None:
            continue
        for key, base_value in tag_contrib.items():
            bonuses[key] += base_value * multiplier

    # Apply hard caps
    bonuses["qi_multiplier"]          = min(bonuses["qi_multiplier"],          _CAPS["qi_multiplier"])
    bonuses["breakthrough_bonus"]     = min(bonuses["breakthrough_bonus"],      _CAPS["breakthrough_bonus"])
    bonuses["overflow_chance"]        = min(bonuses["overflow_chance"],         _CAPS["overflow_chance"])
    bonuses["negate_qi_loss_chance"]  = min(bonuses["negate_qi_loss_chance"],   _CAPS["negate_qi_loss_chance"])
    bonuses["meditate_cooldown_mult"] = max(_CAPS["meditate_cooldown_mult"],    bonuses["meditate_cooldown_mult"])
    bonuses["qi_threshold_bonus"]     = min(bonuses["qi_threshold_bonus"],      _CAPS["qi_threshold_bonus"])

    return bonuses


def describe_bonuses(talent: "PlayerTalent | None") -> str:
    """
    Human-readable summary of a talent's cultivation bonuses.
    Used by the /talent command.
    """
    if talent is None:
        return "No active talent."

    bonuses = get_cultivation_bonuses(talent)
    lines: list[str] = []

    qi_mult = bonuses["qi_multiplier"]
    if qi_mult > 1.0:
        lines.append(f"⚡ Qi accrual **×{qi_mult:.2f}**")

    bt = bonuses["breakthrough_bonus"]
    if bt > 0:
        lines.append(f"🎯 Breakthrough chance **+{bt:.1f}%**")

    ov = bonuses["overflow_chance"]
    if ov > 0.05:
        lines.append(f"✨ Overflow chance **{ov * 100:.1f}%**")

    negate = bonuses["negate_qi_loss_chance"]
    if negate > 0:
        lines.append(f"🛡️ Negate Qi loss **{negate * 100:.0f}%** chance")

    cd = bonuses["meditate_cooldown_mult"]
    if cd < 1.0:
        lines.append(f"🧘 Meditate cooldown **{(1.0 - cd) * 100:.0f}%** shorter")

    thr = bonuses["qi_threshold_bonus"]
    if thr > 0:
        lines.append(f"📈 Qi threshold **+{thr * 100:.0f}%**")

    return "\n".join(lines) if lines else "No cultivation bonuses from this talent."


# ---------------------------------------------------------------------------
# Spirit Root proxies — import from the real implementation, not ourselves
# ---------------------------------------------------------------------------

from spirit_roots.cultivation_bridge import (          # noqa: E402
    get_spirit_root_bonuses,
    describe_spirit_root_bonuses,
    merge_bonuses,
)

__all__ = [
    "get_cultivation_bonuses",
    "describe_bonuses",
    "get_spirit_root_bonuses",
    "describe_spirit_root_bonuses",
    "merge_bonuses",
]