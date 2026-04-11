# spirit_roots/__init__.py
# ``spirit_roots`` so they never need to drill into sub-modules.
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
from talent.cultivation_bridge import get_spirit_root_bonuses, describe_spirit_root_bonuses

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
    # cultivation bridge
    "get_spirit_root_bonuses",
    "describe_spirit_root_bonuses",
]