"""
spirit_roots/data.py
~~~~~~~~~~~~~~~~~~~~
All static game-data for the Spirit Root system.

Root values run 1–5 (higher = rarer / stronger).

Tuning knobs at the top — edit these to rebalance the whole system without
touching any other file.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

#: Failed-to-improve spins before pity guarantee fires.
PITY_THRESHOLD: int = 10

#: Best-root floor gap.
#: If best_value = N, the roll floor = max(1, N - FLOOR_GAP).
#: FLOOR_GAP = 1 → you can never roll more than one tier below your personal best.
FLOOR_GAP: int = 1

#: Seconds between free spins (86 400 = 24 h).
SPIN_COOLDOWN_SECONDS: int = 86_400


# ---------------------------------------------------------------------------
# RootTier dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RootTier:
    value:       int    # 1–5, used for all comparisons
    name:        str    # display name shown to players
    colour:      int    # Discord embed colour (hex int)
    weight:      float  # relative draw weight (higher = more common)
    emoji:       str    # decorative emoji for embeds
    description: str    # flavour text shown in spin result / profile

    @property
    def is_max(self) -> bool:
        return self.value == 5

    def __lt__(self, other: "RootTier") -> bool:  return self.value <  other.value
    def __le__(self, other: "RootTier") -> bool:  return self.value <= other.value
    def __gt__(self, other: "RootTier") -> bool:  return self.value >  other.value
    def __ge__(self, other: "RootTier") -> bool:  return self.value >= other.value


# ---------------------------------------------------------------------------
# Tier definitions  (value 1 → 5, ascending quality)
# ---------------------------------------------------------------------------

ROOT_TIERS: list[RootTier] = [
    RootTier(
        value=1, name="Mortal Root", colour=0x8B8B8B, weight=40.0, emoji="🪨",
        description="A dull, unremarkable root. The path of cultivation will be long and arduous.",
    ),
    RootTier(
        value=2, name="Iron Root", colour=0xA0714F, weight=30.0, emoji="⚙️",
        description="Sturdy but unrefined. Progress is possible with enough determination.",
    ),
    RootTier(
        value=3, name="Jade Root", colour=0x3CB371, weight=18.0, emoji="💚",
        description="A promising foundation. Those who bear this root walk a brighter path.",
    ),
    RootTier(
        value=4, name="Golden Root", colour=0xFFD700, weight=9.0, emoji="✨",
        description="Rare and radiant. Sects compete fiercely for cultivators of this calibre.",
    ),
    RootTier(
        value=5, name="Heavenly Root", colour=0x9B59B6, weight=3.0, emoji="🌌",
        description=(
            "A root born once in ten thousand generations. "
            "The heavens themselves take notice."
        ),
    ),
]

# Fast lookup maps — built once at import time
_BY_VALUE: dict[int, RootTier] = {t.value: t for t in ROOT_TIERS}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_tier_by_value(value: int) -> RootTier:
    """Return the RootTier for *value* (1–5).  Raises KeyError for invalid values."""
    try:
        return _BY_VALUE[value]
    except KeyError:
        raise KeyError(f"No RootTier with value={value!r}. Valid range: 1–5.") from None


def roll_root(*, floor: int = 1) -> RootTier:
    """
    Randomly draw a RootTier using weighted sampling.

    Parameters
    ----------
    floor:
        Minimum root value that can be drawn.  Tiers below this are excluded.

    Returns
    -------
    RootTier
    """
    if not (1 <= floor <= 5):
        raise ValueError(f"floor must be 1–5, got {floor!r}")

    eligible = [t for t in ROOT_TIERS if t.value >= floor]
    weights  = [t.weight for t in eligible]
    return random.choices(eligible, weights=weights, k=1)[0]