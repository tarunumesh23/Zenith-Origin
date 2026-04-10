from __future__ import annotations

from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Realm display names & order
# ---------------------------------------------------------------------------

REALM_DISPLAY: dict[str, str] = {
    "mortal":          "Mortal",
    "qi_gathering":    "Qi Gathering",
    "qi_condensation": "Qi Condensation",
    "qi_refining":     "Qi Refining",
}

REALM_ORDER: list[str] = ["mortal", "qi_gathering", "qi_condensation", "qi_refining"]

# ---------------------------------------------------------------------------
# Breakthrough odds per realm  (success_chance_percent,)
#
# The second element was previously an unused "fail%" value.  It has been
# removed — the fail chance is simply 100 - success_chance at every call site.
# ---------------------------------------------------------------------------

BREAKTHROUGH_ODDS: dict[str, float] = {
    "mortal":          90.0,
    "qi_gathering":    82.0,
    "qi_condensation": 75.0,
    "qi_refining":     68.0,
}

# ---------------------------------------------------------------------------
# Failure consequences  (qi_loss_fraction, cooldown_minutes)
# No realm or stage regression on failure — only Qi loss + cooldown.
# ---------------------------------------------------------------------------

FAIL_CONSEQUENCES: dict[str, tuple[float, int]] = {
    "mortal":          (0.15, 10),
    "qi_gathering":    (0.20, 20),
    "qi_condensation": (0.25, 40),
    "qi_refining":     (0.30, 60),
}

# ---------------------------------------------------------------------------
# Elemental affinities
# ---------------------------------------------------------------------------

AFFINITIES: list[str] = ["fire", "water", "lightning", "wood", "earth"]

AFFINITY_DISPLAY: dict[str, str] = {
    "fire":      "🔥 Fire",
    "water":     "💧 Water",
    "lightning": "⚡ Lightning",
    "wood":      "🌿 Wood",
    "earth":     "🪨 Earth",
}

# Per-second Qi gain multiplier per affinity.
# None / unset affinity → 1.0 (neutral) until the player chooses.
AFFINITY_QI_MULTIPLIER: dict[str, float] = {
    "fire":      1.15,
    "water":     0.95,
    "lightning": 1.00,
    "wood":      1.10,
    "earth":     0.90,
}

# Breakthrough success chance modifier (additive percentage points).
AFFINITY_BREAKTHROUGH_MODIFIER: dict[str, float] = {
    "fire":      -5.0,
    "water":      5.0,
    "lightning": -8.0,
    "wood":       3.0,
    "earth":      2.0,
}

# Combat power multiplier.
AFFINITY_COMBAT_BONUS: dict[str, float] = {
    "fire":      1.10,
    "water":     1.00,
    "lightning": 1.12,
    "wood":      1.05,
    "earth":     1.08,
}

# Elemental matchup bonuses: attacker → defender → multiplier.
AFFINITY_MATCHUP: dict[str, dict[str, float]] = {
    "fire":      {"wood": 1.15, "fire": 0.95},
    "water":     {"fire": 1.15, "water": 0.95},
    "lightning": {"earth": 1.15, "lightning": 0.95},
    "wood":      {"water": 1.15, "wood": 0.95},
    "earth":     {"lightning": 1.15, "earth": 0.95},
}

# ---------------------------------------------------------------------------
# Real-time Qi accrual
#
# The DB stores a snapshot:  (qi, last_updated).
# Live Qi at time T  =  min(qi + rate × elapsed_seconds, qi_threshold)
#
# rate  =  BASE_QI_PER_SECOND
#         × AFFINITY_QI_MULTIPLIER[affinity]   (1.0 if affinity not yet set)
#         × CLOSED_CULT_MULTIPLIER              (only during closed cultivation)
#         × talent_multiplier                   (future hook, default 1.0)
# ---------------------------------------------------------------------------

BASE_QI_PER_SECOND:     float = 0.40
CLOSED_CULT_MULTIPLIER: float = 2.0
QI_LIVE_UPDATE_INTERVAL: int  = 5   # seconds between /qi embed refreshes

# ---------------------------------------------------------------------------
# Realm weight for combat power
# ---------------------------------------------------------------------------

REALM_WEIGHT: dict[str, int] = {
    "mortal":          1,
    "qi_gathering":    3,
    "qi_condensation": 7,
    "qi_refining":     15,
}

# ---------------------------------------------------------------------------
# Reputation
# ---------------------------------------------------------------------------

REP_WIN_CHALLENGE:  int =  10
REP_WIN_DUEL:       int =  25
REP_WIN_ABOVE_REALM:int =  30
REP_AMBUSH_SUCCESS: int =   5
REP_FLEE:           int = -15
REP_AMBUSH_FAIL:    int = -20
REP_VENDETTA_CLEAR: int =  15

REPUTATION_TITLES: list[tuple[int, str]] = [
    (-50, "Coward of the Eastern Peaks"),
    (  0, "Unknown Wanderer"),
    ( 30, "Promising Disciple"),
    ( 75, "Rising Cultivator"),
    (150, "Seasoned Fighter"),
    (300, "Fearsome Cultivator"),
    (500, "Realm Crusher"),
    (800, "Undefeated"),
]


def get_reputation_title(rep: int) -> str:
    title = REPUTATION_TITLES[0][1]
    for threshold, name in REPUTATION_TITLES:
        if rep >= threshold:
            title = name
    return title


# ---------------------------------------------------------------------------
# Qi computation helper
# ---------------------------------------------------------------------------

def _as_utc(dt: datetime) -> datetime:
    """Return dt with UTC tzinfo, attaching it if absent."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_current_qi(
    qi_stored: float,
    qi_threshold: float,
    last_updated: datetime | None,
    affinity: str | None,
    closed_cult_until: datetime | None,
    talent_multiplier: float = 1.0,
    now: datetime | None = None,
) -> tuple[float, float]:
    """
    Return ``(current_qi, qi_rate_per_second)``.

    ``current_qi`` is always capped at ``qi_threshold``.
    ``last_updated=None`` → elapsed time treated as 0 (no accrual yet).
    ``affinity=None``     → multiplier of 1.0 (neutral until chosen).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    aff_mult = AFFINITY_QI_MULTIPLIER.get(affinity, 1.0) if affinity else 1.0
    cc_mult  = (
        CLOSED_CULT_MULTIPLIER
        if closed_cult_until and _as_utc(closed_cult_until) > now
        else 1.0
    )
    rate = BASE_QI_PER_SECOND * aff_mult * cc_mult * talent_multiplier

    elapsed = (
        max(0.0, (now - _as_utc(last_updated)).total_seconds())
        if last_updated else 0.0
    )

    current = min(qi_stored + rate * elapsed, float(qi_threshold))
    return current, rate