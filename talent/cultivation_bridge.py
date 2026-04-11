"""
spirit_roots/cultivation_bridge.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Single source-of-truth for how a player's Spirit Root tier influences
cultivation.

All cultivation code should call ``get_spirit_root_bonuses(root_value)`` and
merge the returned dict into the talent bonuses dict before applying them to
breakthroughs / Qi accrual.

Bonus dict schema  (same keys as talent/cultivation_bridge.py so the caller
can simply add both dicts together)
──────────────────────────────────────────────────────────────────────────────
{
    "qi_multiplier":          float,   # multiplicative on BASE_QI_PER_SECOND
    "breakthrough_bonus":     float,   # additive % points on success chance
    "overflow_chance":        float,   # additive on base 0.05 overflow prob
    "negate_qi_loss_chance":  float,   # 0.0–1.0 chance to negate Qi loss
    "meditate_cooldown_mult": float,   # shrinks meditate cooldown (< 1.0)
    "qi_threshold_bonus":     float,   # additive fraction of base threshold
}

Design notes
────────────
• Tier 1 (Mortal Root)   — small penalties: cultivation is genuinely harder.
• Tier 2 (Iron Root)     — neutral baseline; no bonus, no penalty.
• Tier 3 (Jade Root)     — modest bonuses across the board.
• Tier 4 (Golden Root)   — meaningful bonuses; noticeably faster progression.
• Tier 5 (Heavenly Root) — top-tier bonuses; rivals a Mythical talent.

Root bonuses intentionally do NOT stack with themselves (you only have one
root), but they DO stack additively with talent bonuses so that both systems
reward investment.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# Per-tier bonus tables
# ---------------------------------------------------------------------------

# Each entry maps root value (1–5) → bonus dict.
# Keys must match the schema described above.
_TIER_BONUSES: dict[int, dict[str, float]] = {
    1: {   # Mortal Root — slightly hampered cultivation
        "qi_multiplier":          0.85,   # −15 % Qi accrual
        "breakthrough_bonus":    -5.0,    # −5 pp success chance
        "overflow_chance":        0.00,   # no bonus to overflow
        "negate_qi_loss_chance":  0.00,
        "meditate_cooldown_mult": 0.00,   # no cooldown reduction
        "qi_threshold_bonus":    -0.05,   # −5 % Qi threshold
    },
    2: {   # Iron Root — neutral
        "qi_multiplier":          1.00,
        "breakthrough_bonus":     0.0,
        "overflow_chance":        0.00,
        "negate_qi_loss_chance":  0.00,
        "meditate_cooldown_mult": 0.00,
        "qi_threshold_bonus":     0.00,
    },
    3: {   # Jade Root — modest gains
        "qi_multiplier":          1.10,   # +10 % Qi accrual
        "breakthrough_bonus":     3.0,    # +3 pp success chance
        "overflow_chance":        0.01,   # +1 pp overflow
        "negate_qi_loss_chance":  0.05,   # 5 % chance to negate Qi loss
        "meditate_cooldown_mult": -0.05,  # −5 % meditate cooldown
        "qi_threshold_bonus":     0.05,   # +5 % Qi threshold
    },
    4: {   # Golden Root — strong gains
        "qi_multiplier":          1.25,   # +25 % Qi accrual
        "breakthrough_bonus":     7.0,    # +7 pp success chance
        "overflow_chance":        0.03,   # +3 pp overflow
        "negate_qi_loss_chance":  0.12,   # 12 % negate Qi loss
        "meditate_cooldown_mult": -0.12,  # −12 % meditate cooldown
        "qi_threshold_bonus":     0.12,   # +12 % Qi threshold
    },
    5: {   # Heavenly Root — exceptional gains
        "qi_multiplier":          1.50,   # +50 % Qi accrual
        "breakthrough_bonus":    15.0,    # +15 pp success chance
        "overflow_chance":        0.07,   # +7 pp overflow
        "negate_qi_loss_chance":  0.25,   # 25 % negate Qi loss
        "meditate_cooldown_mult": -0.20,  # −20 % meditate cooldown
        "qi_threshold_bonus":     0.20,   # +20 % Qi threshold
    },
}

# Identity / neutral values (no root equipped or root value unknown)
_IDENTITY: dict[str, float] = {
    "qi_multiplier":          1.00,
    "breakthrough_bonus":     0.0,
    "overflow_chance":        0.00,
    "negate_qi_loss_chance":  0.00,
    "meditate_cooldown_mult": 0.00,
    "qi_threshold_bonus":     0.00,
}

# Hard caps (applied after merging with talent bonuses in the caller)
# These are provided so callers can re-apply safety rails after combining both
# bonus sources.  They mirror talent/cultivation_bridge.py _CAPS.
ROOT_BONUS_CAPS: dict[str, float] = {
    "qi_multiplier":          3.00,
    "breakthrough_bonus":    20.00,
    "overflow_chance":        0.30,
    "negate_qi_loss_chance":  0.75,
    "meditate_cooldown_mult": 0.40,   # floor — mult cannot drop below 40 % of base
    "qi_threshold_bonus":     0.50,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_spirit_root_bonuses(root_value: int | None) -> dict[str, float]:
    """
    Return the cultivation bonus dict for a given root tier value (1–5).

    Parameters
    ----------
    root_value:
        The player's current Spirit Root value, or ``None`` if the player has
        not yet awakened a root.

    Returns
    -------
    dict[str, float]
        Bonus dict.  For ``root_value=None`` or an unrecognised value, the
        identity dict is returned (no bonuses, no penalties).
    """
    if root_value is None:
        return dict(_IDENTITY)
    bonuses = _TIER_BONUSES.get(root_value)
    if bonuses is None:
        return dict(_IDENTITY)
    return dict(bonuses)


def merge_bonuses(
    talent_bonuses: dict[str, float],
    root_bonuses: dict[str, float],
) -> dict[str, float]:
    """
    Merge talent and Spirit Root bonus dicts into a single combined dict.

    Merging rules
    ─────────────
    • ``qi_multiplier``         — multiplicative  (talent × root)
    • ``meditate_cooldown_mult``— multiplicative  (both shrink the cooldown)
    • All other keys            — additive

    The merged dict is capped by ``ROOT_BONUS_CAPS`` after merging.

    Parameters
    ----------
    talent_bonuses:
        Dict from ``talent.cultivation_bridge.get_cultivation_bonuses()``.
    root_bonuses:
        Dict from ``get_spirit_root_bonuses()``.

    Returns
    -------
    dict[str, float]
        Combined, capped bonus dict.
    """
    merged: dict[str, float] = {}

    # Multiplicative keys
    for key in ("qi_multiplier", "meditate_cooldown_mult"):
        t_val = talent_bonuses.get(key, _IDENTITY[key])
        r_val = root_bonuses.get(key, _IDENTITY[key])

        if key == "meditate_cooldown_mult":
            # Both values are used as (1.0 + delta) style already in talent bridge.
            # Here root stores a delta (negative = reduction); add to talent's value.
            merged[key] = t_val + r_val
        else:
            # qi_multiplier: talent already expressed as a multiplier (e.g. 1.2).
            # Root also expressed as a multiplier (e.g. 1.1).
            # Combine multiplicatively.
            merged[key] = t_val * r_val

    # Additive keys
    for key in ("breakthrough_bonus", "overflow_chance",
                "negate_qi_loss_chance", "qi_threshold_bonus"):
        t_val = talent_bonuses.get(key, 0.0)
        r_val = root_bonuses.get(key, 0.0)
        merged[key] = t_val + r_val

    # Apply caps
    caps = ROOT_BONUS_CAPS
    merged["qi_multiplier"]         = min(merged["qi_multiplier"],         caps["qi_multiplier"])
    merged["breakthrough_bonus"]    = min(merged["breakthrough_bonus"],     caps["breakthrough_bonus"])
    merged["overflow_chance"]       = min(merged["overflow_chance"],        caps["overflow_chance"])
    merged["negate_qi_loss_chance"] = min(merged["negate_qi_loss_chance"],  caps["negate_qi_loss_chance"])
    merged["meditate_cooldown_mult"]= max(caps["meditate_cooldown_mult"],   merged["meditate_cooldown_mult"])
    merged["qi_threshold_bonus"]    = min(merged["qi_threshold_bonus"],     caps["qi_threshold_bonus"])

    return merged


def describe_spirit_root_bonuses(root_value: int | None) -> str:
    """
    Human-readable summary of Spirit Root cultivation bonuses.

    Used by ``/root`` command to show the player what their root does.
    Returns ``"No cultivation bonuses."`` for Tier 1 (Mortal Root) penalties
    are described explicitly rather than treated as bonuses.
    """
    if root_value is None:
        return "Awaken a Spirit Root to gain cultivation bonuses."

    bonuses = get_spirit_root_bonuses(root_value)
    lines: list[str] = []

    qi_mult = bonuses["qi_multiplier"]
    if qi_mult > 1.0:
        lines.append(f"⚡ Qi accrual **×{qi_mult:.2f}**")
    elif qi_mult < 1.0:
        lines.append(f"⚡ Qi accrual **×{qi_mult:.2f}** *(penalised)*")

    bt = bonuses["breakthrough_bonus"]
    if bt > 0:
        lines.append(f"🎯 Breakthrough chance **+{bt:.1f}%**")
    elif bt < 0:
        lines.append(f"🎯 Breakthrough chance **{bt:.1f}%** *(penalised)*")

    ov = bonuses["overflow_chance"]
    if ov > 0:
        lines.append(f"✨ Overflow (double-stage) chance **+{ov * 100:.1f}%**")

    negate = bonuses["negate_qi_loss_chance"]
    if negate > 0:
        lines.append(f"🛡️ Negate Qi loss on failure **{negate * 100:.0f}%** chance")

    cd = bonuses["meditate_cooldown_mult"]
    if cd < 0:
        lines.append(f"🧘 Meditate cooldown **{abs(cd) * 100:.0f}%** shorter")

    thr = bonuses["qi_threshold_bonus"]
    if thr > 0:
        lines.append(f"📈 Qi threshold **+{thr * 100:.0f}%**")
    elif thr < 0:
        lines.append(f"📈 Qi threshold **{thr * 100:.0f}%** *(penalised)*")

    return "\n".join(lines) if lines else "No cultivation bonuses."