from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Talent:
    """A talent definition (from TALENT_POOL)."""
    name: str
    rarity: str
    description: str
    tags: list[str]
    evolution: Optional[tuple[str, str]]   # (evolved_name, final_form_name)
    multiplier: float
    color: int
    emoji: str
    one_per_server: bool = False

    def __str__(self):
        return f"{self.emoji} **{self.name}** [{self.rarity}]"

    @property
    def evolved_name(self) -> Optional[str]:
        return self.evolution[0] if self.evolution else None

    @property
    def final_form_name(self) -> Optional[str]:
        return self.evolution[1] if self.evolution else None


@dataclass
class PlayerTalent:
    """A talent owned by a player (may be evolved / corrupted)."""
    name: str                         # current display name
    base_name: str                    # original talent name (for lookup)
    rarity: str
    description: str
    multiplier: float
    color: int
    emoji: str
    evolution_stage: int = 0          # 0 = base, 1 = evolved, 2 = final form
    is_corrupted: bool = False
    is_locked: bool = False
    acquired_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tags: list[str] = field(default_factory=list)

    def display(self) -> str:
        lock = "🔒" if self.is_locked else ""
        corrupt = "☠️" if self.is_corrupted else ""
        stage_label = ["", " ✦", " ✦✦"][self.evolution_stage]
        return f"{self.emoji} **{self.name}**{stage_label} [{self.rarity}] {lock}{corrupt}"


@dataclass
class PlayerTalentData:
    """All talent-related data for one player."""
    user_id: int
    guild_id: int

    # Current active talent (None = no talent yet)
    active_talent: Optional[PlayerTalent] = None

    # Talent inventory (up to INVENTORY_MAX — enforced in logic layer)
    inventory: list[PlayerTalent] = field(default_factory=list)

    # Spin pity counters  { "Elite": int, "Heavenly": int, "Mythical": int }
    spin_pity: dict[str, int] = field(default_factory=lambda: {
        "Elite": 0, "Heavenly": 0, "Mythical": 0
    })

    # Fusion pity counter (global, resets on any successful fusion)
    fusion_pity: int = 0

    # Total spins ever
    total_spins: int = 0

    # Total fusions ever
    total_fusions: int = 0