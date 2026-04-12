"""
spirit_roots/engine.py
~~~~~~~~~~~~~~~~~~~~~~~
Pure game-logic layer.  Zero Discord imports.  Zero DB imports.

Takes current player state → returns an immutable SpinResult.
This is the single source of truth for all spin mechanics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .data import (
    FLOOR_GAP,
    PITY_THRESHOLD,
    RootTier,
    get_tier_by_value,
    roll_root,
)

# Valid outcome literals — used for type narrowing and guard-clause validation.
_OUTCOMES: Final[frozenset[str]] = frozenset({"improved", "equal", "protected"})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpinResult:
    """
    Immutable record of one resolved spin.

    Persist via ``db.spirit_roots.apply_spin_result``; never mutate after
    construction.
    """
    rolled_tier:    RootTier  # raw RNG output (before pity override)
    final_tier:     RootTier  # what the player actually ends up with
    outcome:        str       # 'improved' | 'equal' | 'protected'
    pity_triggered: bool      # True if the pity guarantee fired this spin
    pity_before:    int       # counter value BEFORE this spin
    pity_after:     int       # counter value AFTER this spin (for DB write)
    floor_applied:  int       # the floor value used for this roll

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise ValueError(f"Invalid outcome {self.outcome!r}. Must be one of {_OUTCOMES}.")

    @property
    def is_improved(self)  -> bool: return self.outcome == "improved"
    @property
    def is_equal(self)     -> bool: return self.outcome == "equal"
    @property
    def is_protected(self) -> bool: return self.outcome == "protected"


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def resolve_spin(
    current_value: int,
    best_value:    int,
    pity_counter:  int,
) -> SpinResult:
    """
    Resolve one spin given the player's current state.

    Parameters
    ----------
    current_value:
        The player's current root value (1–5).
    best_value:
        The highest root the player has ever held (1–5).  Must be
        ``>= current_value``.
    pity_counter:
        Number of consecutive non-improving spins accumulated so far.

    Returns
    -------
    SpinResult
        Immutable result.  Persist via ``db.spirit_roots.apply_spin_result``.

    Raises
    ------
    ValueError
        If ``current_value`` or ``best_value`` are outside the valid 1–5 range,
        or if ``best_value < current_value``.
    """
    if not (1 <= current_value <= 5):
        raise ValueError(f"current_value must be 1–5, got {current_value!r}")
    if not (1 <= best_value <= 5):
        raise ValueError(f"best_value must be 1–5, got {best_value!r}")
    if best_value < current_value:
        raise ValueError(
            f"best_value ({best_value}) must be >= current_value ({current_value})"
        )
    if pity_counter < 0:
        raise ValueError(f"pity_counter must be >= 0, got {pity_counter!r}")

    # 1. Floor — prevents rolling below (best - FLOOR_GAP) ─────────────────
    floor = max(1, best_value - FLOOR_GAP)

    # 2. Pity check — fires when counter ≥ threshold AND not already maxed ──
    pity_triggered = (pity_counter >= PITY_THRESHOLD) and (best_value < 5)

    # 3. Roll ───────────────────────────────────────────────────────────────
    rolled: RootTier = roll_root(floor=floor)

    # 4. Pity override — guarantee at least current + 1 (capped at 5) ──────
    if pity_triggered:
        min_pity_value = min(current_value + 1, 5)
        if rolled.value < min_pity_value:
            rolled = get_tier_by_value(min_pity_value)

    # 5. Outcome determination ──────────────────────────────────────────────
    if rolled.value > current_value:
        outcome    = "improved"
        final_tier = rolled
        pity_after = 0

    elif rolled.value == current_value:
        outcome    = "equal"
        final_tier = get_tier_by_value(current_value)
        pity_after = pity_counter      # equal spin does not advance pity

    else:
        # Safe System: rolled < current → player keeps their current root
        outcome    = "protected"
        final_tier = get_tier_by_value(current_value)
        pity_after = 0 if pity_triggered else pity_counter + 1

    return SpinResult(
        rolled_tier=rolled,
        final_tier=final_tier,
        outcome=outcome,
        pity_triggered=pity_triggered,
        pity_before=pity_counter,
        pity_after=pity_after,
        floor_applied=floor,
    )