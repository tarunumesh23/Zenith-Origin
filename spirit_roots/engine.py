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

    Attributes
    ----------
    rolled_tier:
        Raw RNG output *before* any pity override is applied.
    final_tier:
        The tier the player actually ends up with (may differ from
        ``rolled_tier`` when pity fires).
    pity_triggered:
        True if the pity guarantee fired this spin.
    pity_before:
        Counter value *before* this spin (read from DB).
    pity_after:
        Counter value the DB should write after this spin.
    floor_applied:
        The minimum value used when drawing from the weighted pool.
    """
    rolled_tier:    RootTier
    final_tier:     RootTier
    outcome:        str
    pity_triggered: bool
    pity_before:    int
    pity_after:     int
    floor_applied:  int

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise ValueError(
                f"Invalid outcome {self.outcome!r}. Must be one of {_OUTCOMES}."
            )

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
        The highest root the player has ever held (1–5).
        Must be ``>= current_value``.
    pity_counter:
        Number of consecutive non-improving spins accumulated so far.

    Returns
    -------
    SpinResult
        Immutable result.  Persist via ``db.spirit_roots.apply_spin_result``.

    Raises
    ------
    ValueError
        If inputs are outside valid ranges.
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

    # 1. Floor — prevents rolling more than FLOOR_GAP below personal best ──
    floor = max(1, best_value - FLOOR_GAP)

    # 2. Pity check — fires when counter ≥ threshold AND player is not maxed ─
    pity_triggered = (pity_counter >= PITY_THRESHOLD) and (best_value < 5)

    # 3. Raw roll ────────────────────────────────────────────────────────────
    rolled_tier: RootTier = roll_root(floor=floor)

    # 4. Pity override — guarantee at least current + 1 (capped at 5).
    #    We derive the boosted final tier separately so that rolled_tier always
    #    reflects the true RNG output for audit purposes.
    if pity_triggered:
        min_pity_value = min(current_value + 1, 5)
        final_tier = (
            get_tier_by_value(min_pity_value)
            if rolled_tier.value < min_pity_value
            else rolled_tier
        )
    else:
        final_tier = rolled_tier

    # 5. Outcome determination ───────────────────────────────────────────────
    if final_tier.value > current_value:
        outcome    = "improved"
        pity_after = 0

    elif final_tier.value == current_value:
        # Equal spin: no progress, pity advances toward the next guarantee.
        outcome    = "equal"
        final_tier = get_tier_by_value(current_value)   # ensure correct object
        pity_after = pity_counter + 1

    else:
        # Safe System: rolled below current → player keeps their root.
        outcome    = "protected"
        final_tier = get_tier_by_value(current_value)
        pity_after = 0 if pity_triggered else pity_counter + 1

    return SpinResult(
        rolled_tier=rolled_tier,       # raw RNG output, never overwritten
        final_tier=final_tier,
        outcome=outcome,
        pity_triggered=pity_triggered,
        pity_before=pity_counter,
        pity_after=pity_after,
        floor_applied=floor,
    )