from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Optional

# Maximum inventory size — enforced in the logic layer, declared here so
# it can be imported by both engine.py and any UI that needs to show the cap.
INVENTORY_MAX: Final[int] = 20


# ---------------------------------------------------------------------------
# Talent  (immutable definition from TALENT_POOL)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Talent:
    """
    An immutable talent definition sourced from ``TALENT_POOL``.

    This object is never mutated after construction — use :class:`PlayerTalent`
    for per-player mutable state.
    """
    name: str
    rarity: str
    description: str
    tags: tuple[str, ...]               # immutable; list is unsafe on frozen dataclasses
    evolution: Optional[tuple[str, str]]  # (evolved_name, final_form_name) or None
    multiplier: float
    color: int
    emoji: str
    one_per_server: bool = False

    def __str__(self) -> str:
        return f"{self.emoji} **{self.name}** [{self.rarity}]"

    @property
    def evolved_name(self) -> Optional[str]:
        """The first evolution form name, or ``None`` if this talent cannot evolve."""
        return self.evolution[0] if self.evolution else None

    @property
    def final_form_name(self) -> Optional[str]:
        """The final evolution form name, or ``None`` if this talent cannot evolve."""
        return self.evolution[1] if self.evolution else None

    @property
    def can_evolve(self) -> bool:
        """``True`` if this talent has an evolution path defined."""
        return self.evolution is not None


# ---------------------------------------------------------------------------
# Evolution stage
# ---------------------------------------------------------------------------

_STAGE_SUFFIX: Final[tuple[str, str, str]] = ("", " ✦", " ✦✦")
_STAGE_LABELS: Final[tuple[str, str, str]] = ("Base", "Evolved", "Final Form")
MAX_EVOLUTION_STAGE: Final[int] = 2


# ---------------------------------------------------------------------------
# PlayerTalent  (mutable, per-player instance)
# ---------------------------------------------------------------------------

@dataclass
class PlayerTalent:
    """
    A talent as owned by a player — may be evolved, corrupted, or locked.

    ``base_name`` always refers to the original :class:`Talent` entry so that
    look-ups in ``TALENT_POOL`` still work after evolution renames the talent.
    """
    name: str           # current display name (changes on evolution)
    base_name: str      # original TALENT_POOL key (never changes)
    rarity: str
    description: str
    multiplier: float
    color: int
    emoji: str
    evolution_stage: int  = 0      # 0 = base, 1 = evolved, 2 = final form
    is_corrupted: bool    = False
    is_locked: bool       = False
    acquired_at: str      = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tags: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def display(self) -> str:
        """Full formatted name with stage suffix, rarity, and status icons."""
        stage  = _STAGE_SUFFIX[self.evolution_stage]
        lock   = " 🔒" if self.is_locked else ""
        corrupt = " ☠️" if self.is_corrupted else ""
        return f"{self.emoji} **{self.name}**{stage} [{self.rarity}]{lock}{corrupt}"

    def __str__(self) -> str:
        return self.display()

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    @property
    def stage_label(self) -> str:
        """Human-readable evolution stage: ``'Base'``, ``'Evolved'``, or ``'Final Form'``."""
        return _STAGE_LABELS[self.evolution_stage]

    @property
    def is_max_evolution(self) -> bool:
        """``True`` when this talent can no longer evolve."""
        return self.evolution_stage >= MAX_EVOLUTION_STAGE


# ---------------------------------------------------------------------------
# PlayerTalentData  (aggregate per-player talent state)
# ---------------------------------------------------------------------------

@dataclass
class PlayerTalentData:
    """
    All talent-related state for one player in one guild.

    Inventory size is capped at :data:`INVENTORY_MAX` by the logic layer;
    this dataclass does not enforce it to keep persistence simple.
    """
    user_id: int
    guild_id: int

    # Active talent — ``None`` means the player has not yet received a talent.
    active_talent: Optional[PlayerTalent] = None

    # Inventory of non-active talents (capped at INVENTORY_MAX in engine).
    inventory: list[PlayerTalent] = field(default_factory=list)

    # Spin pity counters — keys match SPIN_PITY in constants.py.
    spin_pity: dict[str, int] = field(default_factory=lambda: {
        "Elite": 0, "Heavenly": 0, "Mythical": 0,
    })

    # Fusion pity counter; resets to 0 on any successful fusion.
    fusion_pity: int = 0

    # Lifetime counters.
    total_spins: int   = 0
    total_fusions: int = 0

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def inventory_count(self) -> int:
        """Current number of talents in the inventory."""
        return len(self.inventory)

    @property
    def inventory_full(self) -> bool:
        """``True`` when the inventory has reached :data:`INVENTORY_MAX`."""
        return self.inventory_count >= INVENTORY_MAX

    def find_in_inventory(self, name: str) -> Optional[PlayerTalent]:
        """
        Return the first inventory talent whose ``name`` or ``base_name``
        matches *name* (case-insensitive), or ``None``.
        """
        name_lower = name.lower()
        return next(
            (t for t in self.inventory
             if t.name.lower() == name_lower or t.base_name.lower() == name_lower),
            None,
        )