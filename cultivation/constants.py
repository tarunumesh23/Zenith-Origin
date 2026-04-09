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
# Single roll: (success%, minor_fail%, major_fail%)
# No Qi Deviation in these early realms.
# ---------------------------------------------------------------------------

BREAKTHROUGH_ODDS: dict[str, tuple[float, float, float]] = {
    "mortal":          (90.0,  8.0,  2.0),
    "qi_gathering":    (82.0, 13.0,  5.0),
    "qi_condensation": (75.0, 18.0,  7.0),
    "qi_refining":     (68.0, 22.0, 10.0),
}

# ---------------------------------------------------------------------------
# Failure consequences per realm
# minor_fail  → (qi_loss_percent, cooldown_minutes)
# major_fail  → (qi_loss_percent, cooldown_minutes)
# No stage regression in early realms.
# ---------------------------------------------------------------------------

FAIL_CONSEQUENCES: dict[str, dict[str, tuple[float, int]]] = {
    "mortal": {
        "minor_fail": (0.10,  0),   # 10% Qi loss, no cooldown
        "major_fail": (0.25, 30),   # 25% Qi loss, 30m cooldown
    },
    "qi_gathering": {
        "minor_fail": (0.10,  0),
        "major_fail": (0.25, 45),
    },
    "qi_condensation": {
        "minor_fail": (0.10,  0),
        "major_fail": (0.25, 60),
    },
    "qi_refining": {
        "minor_fail": (0.10,  0),
        "major_fail": (0.25, 60),
    },
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

# Passive Qi gain multiplier per affinity
AFFINITY_QI_MULTIPLIER: dict[str, float] = {
    "fire":      1.15,
    "water":     0.95,
    "lightning": 1.00,
    "wood":      1.10,
    "earth":     0.90,
}

# Breakthrough success chance modifier (additive %)
AFFINITY_BREAKTHROUGH_MODIFIER: dict[str, float] = {
    "fire":      -5.0,   # Aggressive but unstable
    "water":      5.0,   # Smooth and forgiving
    "lightning": -8.0,   # High variance, high reward
    "wood":       3.0,   # Steady
    "earth":      2.0,   # Stable
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
# Passive tick config
# ---------------------------------------------------------------------------

TICK_INTERVAL_SECONDS = 1800       # 30 minutes
BASE_QI_PER_TICK      = 10         # before affinity multiplier
CLOSED_CULT_MULTIPLIER = 2.0       # 2x Qi gain during closed cultivation

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

REP_WIN_CHALLENGE  =  10
REP_WIN_DUEL       =  25
REP_WIN_ABOVE_REALM=  30
REP_AMBUSH_SUCCESS =   5
REP_FLEE           = -15
REP_AMBUSH_FAIL    = -20
REP_VENDETTA_CLEAR =  15

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