from __future__ import annotations

import random
from dataclasses import dataclass

from cultivation.constants import (
    AFFINITY_COMBAT_BONUS,
    AFFINITY_MATCHUP,
    REALM_WEIGHT,
)


@dataclass
class Combatant:
    discord_id: int
    display_name: str
    realm: str
    stage: int
    affinity: str
    qi: int


@dataclass
class RoundResult:
    challenger_power: float
    target_power: float
    challenger_won: bool


@dataclass
class CombatResult:
    challenger_won: bool
    rounds: list[RoundResult]
    challenger_wins: int
    target_wins: int


def _roll_power(combatant: Combatant, opponent: Combatant) -> float:
    """
    power = realm_weight × stage × affinity_combat_bonus × matchup_bonus × random(0.85–1.15)
    """
    base = REALM_WEIGHT[combatant.realm] * combatant.stage
    combat_bonus = AFFINITY_COMBAT_BONUS.get(combatant.affinity, 1.0)
    matchup_bonus = AFFINITY_MATCHUP.get(combatant.affinity, {}).get(opponent.affinity, 1.0)
    variance = random.uniform(0.85, 1.15)
    return base * combat_bonus * matchup_bonus * variance


def resolve_combat(challenger: Combatant, target: Combatant) -> CombatResult:
    """
    Best-of-3 round combat resolution.
    Returns full round-by-round breakdown plus overall winner.
    """
    rounds: list[RoundResult] = []
    challenger_wins = 0
    target_wins = 0

    for _ in range(3):
        cp = _roll_power(challenger, target)
        tp = _roll_power(target, challenger)
        c_won = cp > tp
        if c_won:
            challenger_wins += 1
        else:
            target_wins += 1
        rounds.append(RoundResult(challenger_power=cp, target_power=tp, challenger_won=c_won))

    return CombatResult(
        challenger_won=challenger_wins > target_wins,
        rounds=rounds,
        challenger_wins=challenger_wins,
        target_wins=target_wins,
    )


def qi_steal_amount(loser_qi: int, challenger_won: bool, margin: int) -> int:
    """
    Steal 10–25% of loser's Qi scaled by margin of victory (0–3 rounds won).
    margin = winning side's round wins (2 or 3 out of 3).
    """
    pct = 0.10 + (margin - 2) * 0.075   # margin=2 → 10%, margin=3 → 17.5% (capped at 25%)
    pct = min(pct, 0.25)
    return max(1, int(loser_qi * pct))