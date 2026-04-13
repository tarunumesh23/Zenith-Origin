"""
training/constants.py
~~~~~~~~~~~~~~~~~~~~~
All static data and tuning constants for the Cultivation Combat Training system.

Three Paths:
  BODY_TEMPERING  → ATK, DEF, HP_BONUS
  FLOW_ARTS       → SPE, EVA
  KILLING_SENSE   → CRIT_CHANCE, CRIT_DMG

Tiers (per-path mastery gates):
  beginner  → advanced → forbidden
"""
from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Training Paths
# ---------------------------------------------------------------------------

PATH_BODY     = "body_tempering"
PATH_FLOW     = "flow_arts"
PATH_KILLING  = "killing_sense"

PATHS: Final[list[str]] = [PATH_BODY, PATH_FLOW, PATH_KILLING]

PATH_DISPLAY: Final[dict[str, str]] = {
    PATH_BODY:    "🩸 Body Tempering",
    PATH_FLOW:    "🌬️ Flow Arts",
    PATH_KILLING: "🔥 Killing Sense",
}

PATH_EMOJI: Final[dict[str, str]] = {
    PATH_BODY:    "🩸",
    PATH_FLOW:    "🌬️",
    PATH_KILLING: "🔥",
}

# Stats each path trains
PATH_STATS: Final[dict[str, list[str]]] = {
    PATH_BODY:    ["atk", "def"],
    PATH_FLOW:    ["spe", "eva"],
    PATH_KILLING: ["crit_chance", "crit_dmg"],
}

# ---------------------------------------------------------------------------
# Training Tiers
# ---------------------------------------------------------------------------

TIER_BEGINNER  = "beginner"
TIER_ADVANCED  = "advanced"
TIER_FORBIDDEN = "forbidden"

TIER_ORDER: Final[list[str]] = [TIER_BEGINNER, TIER_ADVANCED, TIER_FORBIDDEN]

TIER_DISPLAY: Final[dict[str, str]] = {
    TIER_BEGINNER:  "Beginner",
    TIER_ADVANCED:  "Advanced",
    TIER_FORBIDDEN: "⛧ Forbidden",
}

# Mastery EXP required to unlock each tier (cumulative)
TIER_MASTERY_THRESHOLD: Final[dict[str, int]] = {
    TIER_BEGINNER:  0,
    TIER_ADVANCED:  200,
    TIER_FORBIDDEN: 600,
}

# ---------------------------------------------------------------------------
# Stat Caps by Tier
# The soft curve grinds to near-zero past these; tier upgrade breaks ceiling.
# ---------------------------------------------------------------------------

STAT_CAPS: Final[dict[str, dict[str, int]]] = {
    TIER_BEGINNER: {
        "atk":        30,
        "def":        30,
        "spe":        30,
        "eva":        25,
        "crit_chance": 20,
        "crit_dmg":   25,
    },
    TIER_ADVANCED: {
        "atk":        60,
        "def":        60,
        "spe":        65,
        "eva":        60,
        "crit_chance": 45,
        "crit_dmg":   55,
    },
    TIER_FORBIDDEN: {
        "atk":        100,
        "def":        100,
        "spe":        100,
        "eva":        100,
        "crit_chance": 100,
        "crit_dmg":   100,
    },
}

# ---------------------------------------------------------------------------
# Stat Gain per Session  (base range before soft-curve modifier)
# Format: (min_gain, max_gain) per stat trained this session
# ---------------------------------------------------------------------------

TIER_GAIN_RANGE: Final[dict[str, tuple[float, float]]] = {
    TIER_BEGINNER:  (1.5, 3.5),
    TIER_ADVANCED:  (2.0, 5.0),
    TIER_FORBIDDEN: (3.0, 8.0),
}

# Mastery EXP granted per session
TIER_MASTERY_GAIN: Final[dict[str, int]] = {
    TIER_BEGINNER:  8,
    TIER_ADVANCED:  12,
    TIER_FORBIDDEN: 18,
}

# ---------------------------------------------------------------------------
# Soft-Curve Modifier
# gain_actual = base_gain * soft_curve(current_stat, cap)
# The closer the stat is to the cap, the smaller the multiplier.
# ---------------------------------------------------------------------------

def soft_curve(current: float, cap: int) -> float:
    """
    Returns a multiplier in (0.0, 1.0] that shrinks as current approaches cap.
    Segments:
        0 – 30% of cap  → 1.00   (full gains)
        30–60%           → 0.65
        60–80%           → 0.35
        80–100%          → 0.12
    """
    ratio = current / max(cap, 1)
    if ratio < 0.30:
        return 1.00
    elif ratio < 0.60:
        return 0.65
    elif ratio < 0.80:
        return 0.35
    else:
        return 0.12

# ---------------------------------------------------------------------------
# Cooldown between sessions (seconds)
# ---------------------------------------------------------------------------

SESSION_COOLDOWN_SECONDS: Final[int] = 3600   # 1 hour per path

# ---------------------------------------------------------------------------
# Fatigue  (tracked 0–10; high fatigue = higher failure chance)
# Fatigue accrues per session and decays over time.
# ---------------------------------------------------------------------------

FATIGUE_PER_SESSION: Final[float]     = 1.0
FATIGUE_DECAY_PER_HOUR: Final[float]  = 0.5   # passive decay
FATIGUE_MAX: Final[float]             = 10.0

# Overtraining: gain halved after N consecutive same-path sessions
OVERTRAIN_THRESHOLD: Final[int] = 4   # sessions in a row on same path

# ---------------------------------------------------------------------------
# Failure  (risk events per session)
# ---------------------------------------------------------------------------

# Base failure chance by tier (before fatigue modifier)
BASE_FAILURE_CHANCE: Final[dict[str, float]] = {
    TIER_BEGINNER:  0.06,
    TIER_ADVANCED:  0.12,
    TIER_FORBIDDEN: 0.28,
}

# Fatigue multiplier on failure chance: failure_chance *= (1 + fatigue * FATIGUE_FAILURE_SCALE)
FATIGUE_FAILURE_SCALE: Final[float] = 0.12

# Failure types and their weights
FAILURE_WEIGHTS: Final[dict[str, int]] = {
    "qi_deviation":      50,   # stat drop + stamina penalty (1-2 sessions to recover)
    "injury":            30,   # path locked for N sessions
    "mental_fracture":   15,   # killing_sense specific — CRIT stats invert briefly
    "mutation":           5,   # rare — stat spike OR conversion OR passive tag
}

# Injury lockout duration (sessions)
INJURY_LOCKOUT_SESSIONS: Final[int] = 3

# Qi Deviation Cascade: 3 deviations in a row → all paths locked
DEVIATION_CASCADE_THRESHOLD: Final[int] = 3
CASCADE_LOCKOUT_SESSIONS: Final[int]    = 5

# ---------------------------------------------------------------------------
# Streak bonus
# ---------------------------------------------------------------------------

STREAK_BONUS_PER_SESSION: Final[float] = 0.08   # +8% gain per consecutive session (same path)
STREAK_MAX_BONUS: Final[float]         = 0.40   # capped at +40%

# ---------------------------------------------------------------------------
# Mutation outcomes (weights)
# ---------------------------------------------------------------------------

MUTATION_OUTCOMES: Final[list[dict]] = [
    {"type": "stat_spike",      "weight": 40, "description": "Qi surges unexpectedly — a stat jumps forward."},
    {"type": "stat_convert",    "weight": 30, "description": "Energy shifts — one stat bleeds into another."},
    {"type": "passive_tag",     "weight": 20, "description": "Something lodges in your meridians. A strange mark takes hold."},
    {"type": "stat_drain",      "weight": 10, "description": "The heavens take as well as give. A stat recedes."},
]

PASSIVE_TAGS: Final[list[str]] = [
    "iron_vein",        # DEF gains also feed ATK (5%)
    "ghost_step",       # EVA gains above 50 also boost SPE (5%)
    "killing_clarity",  # CRIT above 40 converts excess into CRIT DMG
    "void_reflex",      # Qi Deviation has 20% chance to grant random +stat instead
    "blood_furnace",    # Injury heals 1 session faster
    "phantom_meridian", # Flow Arts mastery EXP +20%
    "heaven_eye",       # Killing Sense sessions cannot trigger Mental Fracture
    "demonic_frame",    # Body Tempering injury risk -15%
]

# ---------------------------------------------------------------------------
# PvP integration — how training stats scale into combat power
# ---------------------------------------------------------------------------

# ATK modifier on combat power roll
PVP_ATK_WEIGHT:         Final[float] = 0.012   # +1.2% power per ATK point
# DEF modifier on incoming damage reduction (0–1 scale)
PVP_DEF_MAX_REDUCTION:  Final[float] = 0.30    # DEF 100 = 30% damage reduction
PVP_DEF_SCALE_CAP:      Final[int]   = 100
# SPE modifier: fast fighter acts with a small roll bonus
PVP_SPE_ROLL_BONUS:     Final[float] = 0.008   # +0.8% roll per SPE point
# EVA: chance to partially dodge an incoming hit (reduce damage by 50%)
PVP_EVA_DODGE_CAP:      Final[float] = 0.35    # max 35% dodge chance regardless of EVA
PVP_EVA_SCALE_CAP:      Final[int]   = 100
# CRIT: chance to deal bonus damage on a hit
PVP_CRIT_DMG_BONUS:     Final[float] = 1.5     # crit hit = 1.5× effective damage