"""
talent/cultivation_bridge.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Translates a PlayerTalent's tags and multiplier into concrete cultivation
bonuses used by cultivate.py and breakthrough.py.

Also serves as the single import point for spirit-root bonus helpers so that
breakthrough.py and spirit_roots/__init__.py can import everything from here
without creating circular imports.
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
    "meditate_cooldown_mult": 1.00,   # 1.0 = full cooldown, < 1.0 = reduced
    "qi_threshold_bonus":     0.00,
}

# ---------------------------------------------------------------------------
# Tag → bonus mapping
# Each tag contributes a flat amount to one or more bonus keys.
# The talent multiplier scales the total contribution.
# ---------------------------------------------------------------------------
_TAG_BONUSES: dict[str, dict[str, float]] = {
    "qi_boost": {
        "qi_multiplier": 0.10,
    },
    "breakthrough": {
        "breakthrough_bonus": 5.0,
    },
    "overflow": {
        "overflow_chance": 0.03,
    },
    "resilience": {
        "negate_qi_loss_chance": 0.10,
    },
    "swift": {
        "meditate_cooldown_mult": -0.10,
    },
    "threshold": {
        "qi_threshold_bonus": 0.05,
    },
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

    return "\n".join(lines) if lines else "No cultivation bonuses."


# ---------------------------------------------------------------------------
# Spirit Root proxies
# ---------------------------------------------------------------------------
# spirit_roots/__init__.py and breakthrough.py import these from here to
# avoid circular imports (spirit_roots → talent → spirit_roots).

def get_spirit_root_bonuses(root_value: int | None) -> dict[str, float]:
    """
    Proxy for spirit_roots.cultivation_bridge.get_spirit_root_bonuses.
    Import this from talent.cultivation_bridge to avoid circular imports.
    """
    from talent.cultivation_bridge import (
        get_spirit_root_bonuses as _get,
    )
    return _get(root_value)


def describe_spirit_root_bonuses(root_value: int | None) -> str:
    """
    Proxy for spirit_roots.cultivation_bridge.describe_spirit_root_bonuses.
    Import this from talent.cultivation_bridge to avoid circular imports.
    """
    from talent.cultivation_bridge import (
        describe_spirit_root_bonuses as _describe,
    )
    return _describe(root_value)


def merge_bonuses(
    talent_bonuses: dict[str, float],
    root_bonuses: dict[str, float],
) -> dict[str, float]:
    """
    Proxy for spirit_roots.cultivation_bridge.merge_bonuses.
    Import this from talent.cultivation_bridge to avoid circular imports.
    """
    from talent.cultivation_bridge import merge_bonuses as _merge
    return _merge(talent_bonuses, root_bonuses)