from __future__ import annotations

# ---------------------------------------------------------------------------
# Realm display names
# ---------------------------------------------------------------------------

REALM_DISPLAY = {
    "mortal":          "Mortal",
    "qi_gathering":    "Qi Gathering",
    "qi_condensation": "Qi Condensation",
    "qi_refining":     "Qi Refining",
}

REALM_ORDER = ["mortal", "qi_gathering", "qi_condensation", "qi_refining"]

# ---------------------------------------------------------------------------
# Breakthrough odds per realm
# Single roll: (success%, fail%)
# Failures never drop realm or stage — only Qi loss + cooldown.
# ---------------------------------------------------------------------------

BREAKTHROUGH_ODDS: dict[str, tuple[float, float]] = {
    "mortal":          (90.0, 10.0),
    "qi_gathering":    (82.0, 18.0),
    "qi_condensation": (75.0, 25.0),
    "qi_refining":     (68.0, 32.0),
}

# ---------------------------------------------------------------------------
# Failure consequences per realm
# fail → (qi_loss_percent, cooldown_minutes)
# No realm regression. No stage regression.
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

AFFINITIES = ["fire", "water", "lightning", "wood", "earth"]

AFFINITY_DISPLAY = {
    "fire":      "🔥 Fire",
    "water":     "💧 Water",
    "lightning": "⚡ Lightning",
    "wood":      "🌿 Wood",
    "earth":     "🪨 Earth",
}

# Per-second Qi gain multiplier per affinity
# Base rate is BASE_QI_PER_SECOND; multiply by this value.
AFFINITY_QI_MULTIPLIER: dict[str, float] = {
    "fire":      1.15,
    "water":     0.95,
    "lightning": 1.00,
    "wood":      1.10,
    "earth":     0.90,
}

# Breakthrough success chance modifier (additive %)
AFFINITY_BREAKTHROUGH_MODIFIER: dict[str, float] = {
    "fire":      -5.0,
    "water":      5.0,
    "lightning": -8.0,
    "wood":       3.0,
    "earth":      2.0,
}

# Combat power multiplier
AFFINITY_COMBAT_BONUS: dict[str, float] = {
    "fire":      1.10,
    "water":     1.00,
    "lightning": 1.12,
    "wood":      1.05,
    "earth":     1.08,
}

# Elemental matchup bonuses: attacker -> defender -> multiplier
AFFINITY_MATCHUP: dict[str, dict[str, float]] = {
    "fire":      {"wood": 1.15, "fire": 0.95},
    "water":     {"fire": 1.15, "water": 0.95},
    "lightning": {"earth": 1.15, "lightning": 0.95},
    "wood":      {"water": 1.15, "wood": 0.95},
    "earth":     {"lightning": 1.15, "earth": 0.95},
}

# ---------------------------------------------------------------------------
# Real-time Qi accrual config
#
# Qi accumulates continuously at BASE_QI_PER_SECOND (before multipliers).
# The DB stores (qi_stored, last_updated); current Qi is always:
#
#   current_qi = qi_stored + floor(qi_rate_per_second * elapsed_seconds)
#
# where elapsed_seconds = now - last_updated, capped so qi never exceeds
# qi_threshold.
#
# qi_rate_per_second for a cultivator =
#   BASE_QI_PER_SECOND
#   × AFFINITY_QI_MULTIPLIER[affinity]
#   × CLOSED_CULT_MULTIPLIER   (if in closed cultivation)
#   × talent_multiplier         (future hook — default 1.0)
# ---------------------------------------------------------------------------

BASE_QI_PER_SECOND     = 0.40   # base Qi gained per real-world second
CLOSED_CULT_MULTIPLIER = 2.0    # 2× Qi rate during closed cultivation

# How often the /qi live embed updates itself (seconds)
QI_LIVE_UPDATE_INTERVAL = 5

# ---------------------------------------------------------------------------
# Realm weight for combat power calculation
# ---------------------------------------------------------------------------

REALM_WEIGHT: dict[str, int] = {
    "mortal":          1,
    "qi_gathering":    3,
    "qi_condensation": 7,
    "qi_refining":     15,
}

# ---------------------------------------------------------------------------
# Reputation changes
# ---------------------------------------------------------------------------

REP_WIN_CHALLENGE   =  10
REP_WIN_DUEL        =  25
REP_WIN_ABOVE_REALM =  30
REP_AMBUSH_SUCCESS  =   5
REP_FLEE            = -15
REP_AMBUSH_FAIL     = -20
REP_VENDETTA_CLEAR  =  15

# ---------------------------------------------------------------------------
# Reputation title thresholds (ascending)
# ---------------------------------------------------------------------------

REPUTATION_TITLES: list[tuple[int, str]] = [
    (-50,  "Coward of the Eastern Peaks"),
    (  0,  "Unknown Wanderer"),
    ( 30,  "Promising Disciple"),
    ( 75,  "Rising Cultivator"),
    (150,  "Seasoned Fighter"),
    (300,  "Fearsome Cultivator"),
    (500,  "Realm Crusher"),
    (800,  "Undefeated"),
]


def get_reputation_title(rep: int) -> str:
    title = REPUTATION_TITLES[0][1]
    for threshold, name in REPUTATION_TITLES:
        if rep >= threshold:
            title = name
    return title


# ---------------------------------------------------------------------------
# Qi helper — compute current Qi from stored state
# ---------------------------------------------------------------------------

def compute_current_qi(
    qi_stored: float,
    qi_threshold: float,
    last_updated,           # datetime (UTC) or None
    affinity: str | None,
    closed_cult_until,      # datetime (UTC) or None
    talent_multiplier: float = 1.0,
    now=None,
) -> tuple[float, float]:
    """
    Return (current_qi, qi_rate_per_second).

    current_qi is capped at qi_threshold.
    last_updated=None means we treat elapsed time as 0.
    """
    from datetime import datetime, timezone

    if now is None:
        now = datetime.now(timezone.utc)

    def _utc(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    aff_mult = AFFINITY_QI_MULTIPLIER.get(affinity or "water", 1.0)
    cc_mult  = CLOSED_CULT_MULTIPLIER if (
        _utc(closed_cult_until) and _utc(closed_cult_until) > now
    ) else 1.0

    rate = BASE_QI_PER_SECOND * aff_mult * cc_mult * talent_multiplier

    lu = _utc(last_updated)
    elapsed = max(0.0, (now - lu).total_seconds()) if lu else 0.0

    current = min(qi_stored + rate * elapsed, float(qi_threshold))
    return current, rate