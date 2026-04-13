"""
training/engine.py
~~~~~~~~~~~~~~~~~~~
Pure game-logic layer.  Zero Discord imports.  Zero DB imports.

Takes current player training state → returns immutable SessionResult.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .constants import (
    BASE_FAILURE_CHANCE,
    DEVIATION_CASCADE_THRESHOLD,
    FATIGUE_FAILURE_SCALE,
    FATIGUE_PER_SESSION,
    INJURY_LOCKOUT_SESSIONS,
    MUTATION_OUTCOMES,
    OVERTRAIN_THRESHOLD,
    PASSIVE_TAGS,
    PATH_BODY,
    PATH_FLOW,
    PATH_KILLING,
    PATH_STATS,
    PATHS,
    STAT_CAPS,
    STREAK_BONUS_PER_SESSION,
    STREAK_MAX_BONUS,
    TIER_ADVANCED,
    TIER_BEGINNER,
    TIER_FORBIDDEN,
    TIER_GAIN_RANGE,
    TIER_MASTERY_GAIN,
    TIER_MASTERY_THRESHOLD,
    TIER_ORDER,
    soft_curve,
)


# ---------------------------------------------------------------------------
# Input state (caller must populate from DB)
# ---------------------------------------------------------------------------

@dataclass
class TrainingState:
    """All training state for one player — populated from DB before calling engine."""
    discord_id:     int
    path:           str             # which path being trained this session

    # Current stat values (float stored, shown as int)
    atk:            float = 0.0
    def_:           float = 0.0     # 'def' is a Python keyword, use def_
    spe:            float = 0.0
    eva:            float = 0.0
    crit_chance:    float = 0.0
    crit_dmg:       float = 0.0

    # Mastery EXP per path
    mastery_body:   int = 0
    mastery_flow:   int = 0
    mastery_killing:int = 0

    # Tier per path
    tier_body:      str = TIER_BEGINNER
    tier_flow:      str = TIER_BEGINNER
    tier_killing:   str = TIER_BEGINNER

    # Risk state
    fatigue:        float = 0.0
    consecutive_path_sessions: int = 0   # streak on current path
    last_path_trained: Optional[str] = None

    # Injury locks: path → sessions remaining
    injury_locks:   dict[str, int] = field(default_factory=dict)

    # Qi Deviation counter (resets on rest)
    deviation_streak: int = 0

    # Passive tags earned via Mutation
    passive_tags:   list[str] = field(default_factory=list)

    # Cascade lock (sessions remaining — all paths blocked)
    cascade_lock: int = 0

    def get_stat(self, stat: str) -> float:
        return getattr(self, stat if stat != "def" else "def_")

    def get_mastery(self, path: str) -> int:
        return getattr(self, f"mastery_{path.split('_')[0]}")   # body / flow / killing

    def get_tier(self, path: str) -> str:
        key = path.split("_")[0]   # body / flow / killing
        return getattr(self, f"tier_{key}")


# ---------------------------------------------------------------------------
# Output result
# ---------------------------------------------------------------------------

@dataclass
class RiskEvent:
    event_type:  str               # qi_deviation | injury | mental_fracture | mutation
    description: str
    stat_delta:  dict[str, float] = field(default_factory=dict)
    path_locked: Optional[str]    = None
    lock_sessions: int            = 0
    mutation_tag: Optional[str]   = None
    cascade_triggered: bool       = False


@dataclass
class SessionResult:
    path:           str
    tier:           str
    stats_gained:   dict[str, float]      # stat → gain (can be 0 on failure)
    mastery_gained: int
    new_tier:       Optional[str]         # non-None if tier advanced this session
    fatigue_after:  float
    streak_bonus:   float                 # 0.0 – STREAK_MAX_BONUS
    overtraining:   bool                  # efficiency halved
    risk_event:     Optional[RiskEvent]
    narrative:      str                   # flavour text


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def resolve_session(state: TrainingState) -> SessionResult:
    """
    Resolve one training session.

    Does NOT mutate state — caller applies the result to the DB.
    """
    path = state.path

    # ── Guard: cascade lock ──────────────────────────────────────────────
    if state.cascade_lock > 0:
        return _blocked_result(path, f"⚠️ Qi Deviation Cascade is active. All training locked for {state.cascade_lock} more session(s). Rest and recover.")

    # ── Guard: injury lock ───────────────────────────────────────────────
    remaining = state.injury_locks.get(path, 0)
    if remaining > 0:
        return _blocked_result(path, f"🩹 Injury blocks **{_path_display(path)}** for {remaining} more session(s).")

    tier = state.get_tier(path)

    # ── Streak / overtraining ────────────────────────────────────────────
    if state.last_path_trained == path:
        consecutive = state.consecutive_path_sessions + 1
    else:
        consecutive = 1

    overtraining = consecutive > OVERTRAIN_THRESHOLD
    streak_sessions = min(consecutive - 1, 5)
    streak_bonus = min(streak_sessions * STREAK_BONUS_PER_SESSION, STREAK_MAX_BONUS)

    # ── Failure roll ─────────────────────────────────────────────────────
    base_fail = BASE_FAILURE_CHANCE[tier]
    fail_chance = base_fail * (1.0 + state.fatigue * FATIGUE_FAILURE_SCALE)
    if overtraining:
        fail_chance *= 1.30   # overtraining inflates failure chance

    risk_event: Optional[RiskEvent] = None
    failed = random.random() < fail_chance

    if failed:
        risk_event = _resolve_risk(state, path, tier)

    # ── Stat gains ───────────────────────────────────────────────────────
    stats_gained: dict[str, float] = {}

    if risk_event and risk_event.event_type in ("qi_deviation", "mental_fracture"):
        # Zero gain + apply risk stat delta only
        for stat in PATH_STATS[path]:
            stats_gained[stat] = risk_event.stat_delta.get(stat, 0.0)
    else:
        gain_min, gain_max = TIER_GAIN_RANGE[tier]
        for stat in PATH_STATS[path]:
            current = state.get_stat(stat)
            cap     = STAT_CAPS[tier][stat]
            curve   = soft_curve(current, cap)

            base = random.uniform(gain_min, gain_max)
            modified = base * curve * (1.0 + streak_bonus)

            if overtraining:
                modified *= 0.50

            # Clamp so stat doesn't exceed current tier cap
            room = max(0.0, cap - current)
            gain = min(modified, room)
            stats_gained[stat] = round(gain, 2)

        # Mutation may override gains
        if risk_event and risk_event.event_type == "mutation":
            for stat, delta in risk_event.stat_delta.items():
                stats_gained[stat] = stats_gained.get(stat, 0.0) + delta

    # ── Mastery EXP ──────────────────────────────────────────────────────
    mastery_gained = TIER_MASTERY_GAIN[tier]
    if failed and risk_event and risk_event.event_type == "qi_deviation":
        mastery_gained = mastery_gained // 2

    # ── Tier advancement check ────────────────────────────────────────────
    old_mastery = state.get_mastery(path)
    new_mastery = old_mastery + mastery_gained
    new_tier    = _check_tier_advance(tier, new_mastery)

    # ── Fatigue ──────────────────────────────────────────────────────────
    fatigue_after = min(state.fatigue + FATIGUE_PER_SESSION, 10.0)

    # ── Narrative ────────────────────────────────────────────────────────
    narrative = _build_narrative(path, tier, stats_gained, risk_event, overtraining, new_tier)

    return SessionResult(
        path=path,
        tier=tier,
        stats_gained=stats_gained,
        mastery_gained=mastery_gained,
        new_tier=new_tier,
        fatigue_after=fatigue_after,
        streak_bonus=streak_bonus,
        overtraining=overtraining,
        risk_event=risk_event,
        narrative=narrative,
    )


# ---------------------------------------------------------------------------
# Risk resolution
# ---------------------------------------------------------------------------

def _resolve_risk(state: TrainingState, path: str, tier: str) -> RiskEvent:
    # Mental Fracture is exclusive to Killing Sense
    weights = {
        "qi_deviation":    50,
        "injury":          30,
        "mental_fracture": 15 if path == PATH_KILLING else 0,
        "mutation":         5,
    }
    keys    = [k for k, w in weights.items() if w > 0]
    wts     = [weights[k] for k in keys]
    outcome = random.choices(keys, weights=wts, k=1)[0]

    if outcome == "qi_deviation":
        # Stat loss in trained path
        stat_delta = {}
        for stat in PATH_STATS[path]:
            current = state.get_stat(stat)
            loss = round(random.uniform(1.0, 4.0), 2)
            stat_delta[stat] = -min(loss, current)

        # Check cascade
        new_streak = state.deviation_streak + 1
        cascade = new_streak >= DEVIATION_CASCADE_THRESHOLD

        return RiskEvent(
            event_type="qi_deviation",
            description=(
                "⚡ **Qi Deviation!** Your meridians misfired — stats temporarily regressed. "
                + ("⚠️ **Cascade triggered!** All training locked." if cascade else "")
            ),
            stat_delta=stat_delta,
            cascade_triggered=cascade,
        )

    elif outcome == "injury":
        # Check blood_furnace passive — reduces lockout by 1
        lockout = INJURY_LOCKOUT_SESSIONS
        if "blood_furnace" in state.passive_tags:
            lockout = max(1, lockout - 1)

        return RiskEvent(
            event_type="injury",
            description=f"🩹 **Injury!** {_injury_flavour(path)} Training path locked for {lockout} session(s).",
            path_locked=path,
            lock_sessions=lockout,
        )

    elif outcome == "mental_fracture":
        stat_delta = {}
        for stat in PATH_STATS[path]:
            current = state.get_stat(stat)
            loss = round(random.uniform(2.0, 5.0), 2)
            stat_delta[stat] = -min(loss, current)

        return RiskEvent(
            event_type="mental_fracture",
            description="🌀 **Mental Fracture!** The Martial Soul recoiled. Killing instinct inverted — your edge dulled.",
            stat_delta=stat_delta,
        )

    else:  # mutation
        return _resolve_mutation(state, path)


def _resolve_mutation(state: TrainingState, path: str) -> RiskEvent:
    wts    = [m["weight"] for m in MUTATION_OUTCOMES]
    chosen = random.choices(MUTATION_OUTCOMES, weights=wts, k=1)[0]

    stat_delta: dict[str, float] = {}

    if chosen["type"] == "stat_spike":
        stat = random.choice(PATH_STATS[path])
        stat_delta[stat] = round(random.uniform(5.0, 12.0), 2)

    elif chosen["type"] == "stat_convert":
        # A stat from the trained path loses points; a random other stat gains them
        src_stat  = random.choice(PATH_STATS[path])
        all_stats = ["atk", "def_", "spe", "eva", "crit_chance", "crit_dmg"]
        other     = [s for s in all_stats if s not in PATH_STATS[path]]
        if other:
            dst_stat = random.choice(other)
            amt = round(random.uniform(3.0, 7.0), 2)
            stat_delta[src_stat]  = -min(amt, state.get_stat(src_stat))
            stat_delta[dst_stat]  = amt

    elif chosen["type"] == "passive_tag":
        available = [t for t in PASSIVE_TAGS if t not in state.passive_tags]
        tag = random.choice(available) if available else None
        if tag:
            return RiskEvent(
                event_type="mutation",
                description=f"🧬 **Mutation — Passive Tag!** {chosen['description']} You gained: **{tag.replace('_', ' ').title()}**",
                stat_delta={},
                mutation_tag=tag,
            )

    elif chosen["type"] == "stat_drain":
        stat = random.choice(PATH_STATS[path])
        current = state.get_stat(stat)
        loss = round(random.uniform(3.0, 8.0), 2)
        stat_delta[stat] = -min(loss, current)

    return RiskEvent(
        event_type="mutation",
        description=f"🧬 **Mutation!** {chosen['description']}",
        stat_delta=stat_delta,
    )


# ---------------------------------------------------------------------------
# Tier advancement
# ---------------------------------------------------------------------------

def _check_tier_advance(current_tier: str, new_mastery: int) -> Optional[str]:
    """Return the next tier name if mastery threshold is crossed, else None."""
    idx = TIER_ORDER.index(current_tier)
    if idx + 1 >= len(TIER_ORDER):
        return None   # already at max
    next_tier = TIER_ORDER[idx + 1]
    if new_mastery >= TIER_MASTERY_THRESHOLD[next_tier]:
        return next_tier
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blocked_result(path: str, reason: str) -> SessionResult:
    return SessionResult(
        path=path,
        tier=TIER_BEGINNER,
        stats_gained={},
        mastery_gained=0,
        new_tier=None,
        fatigue_after=0.0,
        streak_bonus=0.0,
        overtraining=False,
        risk_event=None,
        narrative=reason,
    )


def _path_display(path: str) -> str:
    from .constants import PATH_DISPLAY
    return PATH_DISPLAY.get(path, path)


def _injury_flavour(path: str) -> str:
    flavours = {
        PATH_BODY:    "Your body gave out under the strain — muscles torn, meridians stressed.",
        PATH_FLOW:    "A misstep during shadow drills left your movement circuits disrupted.",
        PATH_KILLING: "Pushing killing intent too hard caused a psychic rebound.",
    }
    return flavours.get(path, "Training went wrong.")


def _build_narrative(
    path: str,
    tier: str,
    stats_gained: dict[str, float],
    risk_event: Optional[RiskEvent],
    overtraining: bool,
    new_tier: Optional[str],
) -> str:
    lines: list[str] = []

    if overtraining:
        lines.append("⚠️ *You've hammered the same path too many times in a row — efficiency is halved.*")

    if risk_event:
        lines.append(risk_event.description)

    total_gain = sum(v for v in stats_gained.values() if v > 0)
    if total_gain > 0:
        gain_strs = [f"**{s.upper().replace('_', ' ')}** +{v:.1f}" for s, v in stats_gained.items() if v > 0]
        lines.append("📈 " + " · ".join(gain_strs))

    losses = {s: v for s, v in stats_gained.items() if v < 0}
    if losses:
        loss_strs = [f"**{s.upper().replace('_', ' ')}** {v:.1f}" for s, v in losses.items()]
        lines.append("📉 " + " · ".join(loss_strs))

    if new_tier:
        from .constants import TIER_DISPLAY
        lines.append(f"\n✨ **Tier Breakthrough!** You've advanced to **{TIER_DISPLAY[new_tier]}** in {_path_display(path)}!")

    if not lines:
        lines.append("*The session ends without incident.*")

    return "\n".join(lines)