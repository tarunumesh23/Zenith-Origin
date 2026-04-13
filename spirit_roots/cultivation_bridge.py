"""
spirit_roots/cultivation_bridge.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Translates a Spirit Root tier value (1–5) into concrete cultivation bonuses
that plug into the same bonus-dict schema used by talent/cultivation_bridge.py.

Imported BY talent/cultivation_bridge.py (as a proxy) to avoid circular imports.
Never import talent/ modules from here.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Per-tier bonus tables
# ---------------------------------------------------------------------------
# Each root tier value (1–5) maps to additive bonuses on top of the identity.
# These stack with talent bonuses via merge_bonuses() and respect hard caps.

_ROOT_BONUSES: dict[int, dict[str, float]] = {
    1: {  # Mortal Root — essentially no bonus
        "qi_multiplier":          1.00,
        "breakthrough_bonus":     0.0,
        "overflow_chance":        0.00,
        "negate_qi_loss_chance":  0.00,
        "meditate_cooldown_mult": 1.00,
        "qi_threshold_bonus":     0.00,
    },
    2: {  # Iron Root — slight Qi tick boost
        "qi_multiplier":          1.05,
        "breakthrough_bonus":     0.0,
        "overflow_chance":        0.00,
        "negate_qi_loss_chance":  0.00,
        "meditate_cooldown_mult": 1.00,
        "qi_threshold_bonus":     0.00,
    },
    3: {  # Jade Root — meaningful Qi boost + small breakthrough help
        "qi_multiplier":          1.12,
        "breakthrough_bonus":     2.0,
        "overflow_chance":        0.01,
        "negate_qi_loss_chance":  0.05,
        "meditate_cooldown_mult": 0.95,
        "qi_threshold_bonus":     0.05,
    },
    4: {  # Golden Root — strong across the board
        "qi_multiplier":          1.25,
        "breakthrough_bonus":     5.0,
        "overflow_chance":        0.03,
        "negate_qi_loss_chance":  0.12,
        "meditate_cooldown_mult": 0.85,
        "qi_threshold_bonus":     0.10,
    },
    5: {  # Heavenly Root — exceptional
        "qi_multiplier":          1.50,
        "breakthrough_bonus":    10.0,
        "overflow_chance":        0.07,
        "negate_qi_loss_chance":  0.25,
        "meditate_cooldown_mult": 0.70,
        "qi_threshold_bonus":     0.20,
    },
}

# Hard caps (mirrors talent/cultivation_bridge._CAPS)
_CAPS: dict[str, float] = {
    "qi_multiplier":          3.00,
    "breakthrough_bonus":    20.00,
    "overflow_chance":        0.30,
    "negate_qi_loss_chance":  0.75,
    "meditate_cooldown_mult": 0.40,   # lower is better; this is a floor, not a ceiling
    "qi_threshold_bonus":     0.50,
}

_IDENTITY: dict[str, float] = {
    "qi_multiplier":          1.00,
    "breakthrough_bonus":     0.00,
    "overflow_chance":        0.00,
    "negate_qi_loss_chance":  0.00,
    "meditate_cooldown_mult": 1.00,
    "qi_threshold_bonus":     0.00,
}


def get_spirit_root_bonuses(root_value: int | None) -> dict[str, float]:
    """
    Return a bonus dict for the given Spirit Root tier value (1–5).
    Returns identity (all-zero / neutral) if root_value is None or invalid.
    """
    if root_value is None or root_value not in _ROOT_BONUSES:
        return dict(_IDENTITY)
    return dict(_ROOT_BONUSES[root_value])


def describe_spirit_root_bonuses(root_value: int | None) -> str:
    """Human-readable summary of a Spirit Root's cultivation bonuses."""
    if root_value is None:
        return "No Spirit Root."

    bonuses = get_spirit_root_bonuses(root_value)
    lines: list[str] = []

    qi_mult = bonuses["qi_multiplier"]
    if qi_mult > 1.0:
        lines.append(f"⚡ Qi accrual **×{qi_mult:.2f}**")

    bt = bonuses["breakthrough_bonus"]
    if bt > 0:
        lines.append(f"🎯 Breakthrough chance **+{bt:.1f}%**")

    ov = bonuses["overflow_chance"]
    if ov > 0:
        lines.append(f"✨ Overflow chance **+{ov * 100:.1f}%**")

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


def merge_bonuses(
    talent_bonuses: dict[str, float],
    root_bonuses:   dict[str, float],
) -> dict[str, float]:
    """
    Merge talent and Spirit Root bonus dicts, then apply hard caps.

    Merging rules
    -------------
    ``qi_multiplier``
        True multiplicative: ``talent × root``.
        Both values are already ≥ 1.0 (identity = 1.0), so multiplying them
        gives correct compounding — e.g. ×1.25 talent × ×1.12 root = ×1.40.

    ``meditate_cooldown_mult``
        Also multiplicative — lower is better, so the values compound as
        reductions: 0.85 × 0.95 = 0.8075 (≈19% shorter total cooldown).
        Capped at ``_CAPS["meditate_cooldown_mult"]`` as a *floor* (``max``).

    All other keys
        Simple additive.  Capped at ``_CAPS[key]`` as a ceiling (``min``).
    """
    merged: dict[str, float] = {}

    for key in _IDENTITY:
        t = talent_bonuses.get(key, _IDENTITY[key])
        r = root_bonuses.get(key,   _IDENTITY[key])

        if key in ("qi_multiplier", "meditate_cooldown_mult"):
            # Both sources are expressed as multipliers with identity = their
            # respective neutral values (1.0 for both here), so straight
            # multiplication is the correct compounding operation.
            merged[key] = t * r
        else:
            merged[key] = t + r

    # Apply caps — ceiling for most stats, floor for cooldown reduction.
    for key, cap in _CAPS.items():
        if key == "meditate_cooldown_mult":
            merged[key] = max(merged[key], cap)   # lower is better → floor
        else:
            merged[key] = min(merged[key], cap)   # higher is better → ceiling

    return merged