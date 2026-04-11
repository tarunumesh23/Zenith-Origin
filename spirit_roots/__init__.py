# spirit_roots/__init__.py
from .data import (
    PITY_THRESHOLD,
    FLOOR_GAP,
    SPIN_COOLDOWN_SECONDS,
    ROOT_TIERS,
    RootTier,
    get_tier_by_value,
    roll_root,
)
from .engine import SpinResult, resolve_spin

__all__ = [
    # data
    "PITY_THRESHOLD",
    "FLOOR_GAP",
    "SPIN_COOLDOWN_SECONDS",
    "ROOT_TIERS",
    "RootTier",
    "get_tier_by_value",
    "roll_root",
    # engine
    "SpinResult",
    "resolve_spin",
]