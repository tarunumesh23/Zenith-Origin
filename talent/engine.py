# ============================================================
#  TALENT SYSTEM — ENGINE
#  Core logic: spin, fusion, evolution, pity, corruption
# ============================================================
from __future__ import annotations

import random
from typing import Optional

from .constants import (
    RARITIES, RARITY_ORDER, TALENT_POOL,
    SPIN_PITY, FUSION_PITY,
    FUSION_SAME_RARITY_SUCCESS_CHANCE,
    FUSION_CROSS_SUCCESS_CHANCE,
    FUSION_RNG_SUCCESS_CHANCE,
    FAILURE_OUTCOMES, CROSS_FUSION_RECIPES,
    CORRUPTION_NAMES, ONE_PER_SERVER_TALENTS,
)
from .models import Talent, PlayerTalent, PlayerTalentData


# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────

def _rarity_index(rarity: str) -> int:
    return RARITY_ORDER.index(rarity)


def _bump_rarity(rarity: str, steps: int = 1) -> str:
    idx = min(_rarity_index(rarity) + steps, len(RARITY_ORDER) - 1)
    return RARITY_ORDER[idx]


def _build_talent_obj(entry: dict) -> Talent:
    rarity_data = RARITIES[entry["rarity"]]
    return Talent(
        name=entry["name"],
        rarity=entry["rarity"],
        description=entry["description"],
        tags=entry.get("tags", []),
        evolution=entry.get("evolution"),
        multiplier=rarity_data["multiplier"],
        color=rarity_data["color"],
        emoji=rarity_data["emoji"],
        one_per_server=entry.get("one_per_server", False),
    )


def _talent_by_name(name: str) -> Optional[Talent]:
    for entry in TALENT_POOL:
        if entry["name"] == name:
            return _build_talent_obj(entry)
    return None


def _player_talent_from(talent: Talent) -> PlayerTalent:
    return PlayerTalent(
        name=talent.name,
        base_name=talent.name,
        rarity=talent.rarity,
        description=talent.description,
        multiplier=talent.multiplier,
        color=talent.color,
        emoji=talent.emoji,
        tags=talent.tags,
    )


# ─────────────────────────────────────────────────────────────
#  STARTER TALENT  (assigned on /start — pure RNG, no Divine)
# ─────────────────────────────────────────────────────────────

def roll_starter_talent(claimed_one_per_server: list[str]) -> PlayerTalent:
    """
    Roll a random starting talent for a brand-new cultivator.

    Rules:
    - Weighted by rarity (same weights as regular spins).
    - Divine talents are excluded — they should be earned, not given.
    - One-per-server talents that are already claimed are excluded.
    """
    eligible = [
        e for e in TALENT_POOL
        if e["rarity"] != "Divine"
        and not (e.get("one_per_server") and e["name"] in claimed_one_per_server)
    ]

    weights = [RARITIES[e["rarity"]]["weight"] for e in eligible]
    entry   = random.choices(eligible, weights=weights, k=1)[0]
    return _player_talent_from(_build_talent_obj(entry))


# ─────────────────────────────────────────────────────────────
#  SPIN ENGINE
# ─────────────────────────────────────────────────────────────

def spin_talent(
    player_data: PlayerTalentData,
    claimed_one_per_server: list[str],
) -> tuple[PlayerTalent, bool]:
    """
    Roll a talent for the player (token-gated spin).

    Returns:
        (PlayerTalent, is_pity_trigger)
    """
    # ── determine minimum rarity from pity ──────────────────
    min_rarity     = "Trash"
    pity_triggered = False

    for tier, threshold in SPIN_PITY.items():
        if player_data.spin_pity.get(tier, 0) >= threshold:
            if _rarity_index(tier) > _rarity_index(min_rarity):
                min_rarity     = tier
                pity_triggered = True

    # ── filter pool by min_rarity & one-per-server rules ────
    eligible = [
        e for e in TALENT_POOL
        if _rarity_index(e["rarity"]) >= _rarity_index(min_rarity)
        and not (e.get("one_per_server") and e["name"] in claimed_one_per_server)
    ]
    if not eligible:
        eligible = TALENT_POOL

    # ── weighted rarity selection ─────────────────────────────
    rarity_weights = {
        r: (RARITIES[r]["weight"] if _rarity_index(r) >= _rarity_index(min_rarity) else 0)
        for r in RARITY_ORDER
    }

    chosen_rarity = random.choices(
        list(rarity_weights.keys()),
        weights=list(rarity_weights.values()),
        k=1,
    )[0]

    rarity_pool = [e for e in eligible if e["rarity"] == chosen_rarity] or eligible
    entry       = random.choice(rarity_pool)
    talent      = _build_talent_obj(entry)

    # ── update pity counters ─────────────────────────────────
    player_data.total_spins += 1
    for tier in SPIN_PITY:
        if _rarity_index(chosen_rarity) >= _rarity_index(tier):
            player_data.spin_pity[tier] = 0
        else:
            player_data.spin_pity[tier] = player_data.spin_pity.get(tier, 0) + 1

    return _player_talent_from(talent), pity_triggered


# ─────────────────────────────────────────────────────────────
#  FUSION ENGINE
# ─────────────────────────────────────────────────────────────

def _resolve_failure(pity: int) -> dict:
    outcomes = list(FAILURE_OUTCOMES.items())
    weights  = [v["weight"] for _, v in outcomes]
    key, data = random.choices(outcomes, weights=weights, k=1)[0]
    return {"outcome": key, **data}


def _get_cross_recipe(t1: PlayerTalent, t2: PlayerTalent) -> Optional[str]:
    combined_tags = set(t1.tags) | set(t2.tags)
    for recipe in CROSS_FUSION_RECIPES:
        if all(tag in combined_tags for tag in recipe["tags_required"]):
            return recipe["result"]
    return None


def fuse_talents(
    player_data: PlayerTalentData,
    talent_a: PlayerTalent,
    talent_b: PlayerTalent,
    mode: str = "auto",
) -> dict:
    """
    Fuse two talents.

    mode: "auto" | "same" | "cross" | "rng"
      - "auto"  → engine decides based on rarity match
      - "same"  → force same-rarity path (error if rarities differ)
      - "cross" → force cross-fusion path
      - "rng"   → pure random regardless of rarities

    Returns a result dict:
    {
        "success": bool,
        "result_talent": PlayerTalent | None,
        "failure_outcome": str | None,
        "failure_description": str | None,
        "pity_bonus": bool,
        "pity_guarantee": bool,
        "new_pity": int,
        "resolved_mode": str,   # actual mode used (for db logging)
    }
    """
    player_data.total_fusions += 1
    pity = player_data.fusion_pity

    # ── FIX #5: resolve mode independently from success-chance ──
    # User-supplied mode is respected; "auto" picks same vs cross.
    if mode == "auto":
        resolved_mode = "same" if talent_a.rarity == talent_b.rarity else "cross"
    elif mode in ("same", "cross", "rng"):
        resolved_mode = mode
    else:
        resolved_mode = "cross"  # safe fallback

    if resolved_mode == "same":
        base_chance = FUSION_SAME_RARITY_SUCCESS_CHANCE
    elif resolved_mode == "rng":
        base_chance = FUSION_RNG_SUCCESS_CHANCE
    else:
        base_chance = FUSION_CROSS_SUCCESS_CHANCE

    # ── pity bonuses ─────────────────────────────────────────
    # FIX #8 note: at pity ≥ 15 all three flags are True;
    # guarantee overrides boost for the roll, tier_up still fires.
    pity_bonus     = pity >= FUSION_PITY["boost"]
    pity_guarantee = pity >= FUSION_PITY["guarantee"]
    pity_tier_up   = pity >= FUSION_PITY["bonus"]

    if pity_guarantee:
        success_chance = 1.0
    elif pity_bonus:
        success_chance = min(base_chance + 0.20, 1.0)
    else:
        success_chance = base_chance

    success = random.random() < success_chance

    if success:
        player_data.fusion_pity = 0

        result_talent = None

        if resolved_mode == "same":
            new_rarity = _bump_rarity(talent_a.rarity)
            pool = [e for e in TALENT_POOL if e["rarity"] == new_rarity]
            if pool:
                result_talent = _player_talent_from(_build_talent_obj(random.choice(pool)))

        elif resolved_mode == "cross":
            recipe_name = _get_cross_recipe(talent_a, talent_b)
            if recipe_name:
                t = _talent_by_name(recipe_name)
                if t:
                    result_talent = _player_talent_from(t)
            if not result_talent:
                higher_rarity = max(
                    talent_a.rarity, talent_b.rarity,
                    key=lambda r: _rarity_index(r),
                )
                new_rarity = _bump_rarity(higher_rarity)
                pool = [e for e in TALENT_POOL if e["rarity"] == new_rarity]
                if pool:
                    result_talent = _player_talent_from(_build_talent_obj(random.choice(pool)))

        else:  # rng
            pool = [e for e in TALENT_POOL if e["rarity"] != "Trash"]
            result_talent = _player_talent_from(_build_talent_obj(random.choice(pool)))

        # pity tier-up: bump result rarity one extra step
        if pity_tier_up and result_talent:
            bumped_rarity = _bump_rarity(result_talent.rarity)
            pool = [e for e in TALENT_POOL if e["rarity"] == bumped_rarity]
            if pool:
                result_talent = _player_talent_from(_build_talent_obj(random.choice(pool)))

        return {
            "success":             True,
            "result_talent":       result_talent,
            "failure_outcome":     None,
            "failure_description": None,
            "pity_bonus":          pity_bonus,
            "pity_guarantee":      pity_guarantee,
            "new_pity":            0,
            "resolved_mode":       resolved_mode,
        }

    else:
        player_data.fusion_pity = pity + 1
        failure = _resolve_failure(pity)

        result_talent = None
        if failure["outcome"] == "corruption":
            corrupt_name = CORRUPTION_NAMES.get(talent_a.name, f"Darkened {talent_a.name}")
            result_talent = PlayerTalent(
                name=corrupt_name,
                base_name=talent_a.base_name,
                rarity=talent_a.rarity,
                description="A dark, twisted version. Something went very wrong.",
                multiplier=talent_a.multiplier * 0.5,
                color=0x2C2C2C,
                emoji="☠️",
                is_corrupted=True,
                tags=talent_a.tags,
            )

        elif failure["outcome"] == "mutation":
            pool = [
                e for e in TALENT_POOL
                if _rarity_index(e["rarity"]) >= _rarity_index("Rare")
            ]
            result_talent = _player_talent_from(_build_talent_obj(random.choice(pool)))

        return {
            "success":             False,
            "result_talent":       result_talent,
            "failure_outcome":     failure["outcome"],
            "failure_description": failure["description"],
            "pity_bonus":          pity_bonus,
            "pity_guarantee":      pity_guarantee,
            "new_pity":            player_data.fusion_pity,
            "resolved_mode":       resolved_mode,
        }


# ─────────────────────────────────────────────────────────────
#  EVOLUTION ENGINE
# ─────────────────────────────────────────────────────────────

def evolve_talent(
    player_data: PlayerTalentData,
    talent: PlayerTalent,
    evolution_items: int = 0,
) -> tuple[bool, PlayerTalent, str]:
    """
    Attempt to evolve a talent.

    Returns:
        (success, updated_talent, message)
    """
    if talent.evolution_stage >= 2:
        return False, talent, "This talent has already reached its **Final Form**. ✦✦"

    entry = next((e for e in TALENT_POOL if e["name"] == talent.base_name), None)
    if not entry or not entry.get("evolution"):
        return False, talent, "This talent **cannot evolve**."

    evolved_name, final_name = entry["evolution"]

    if talent.evolution_stage == 0:
        required_items = 3
        if evolution_items < required_items:
            return False, talent, (
                f"Need **{required_items} Evolution Crystals** (you have {evolution_items})."
            )
        talent.evolution_stage = 1
        talent.name            = evolved_name
        talent.multiplier     *= 1.5
        return True, talent, (
            f"✦ **{talent.base_name}** evolved into **{evolved_name}**! (×1.5 multiplier)"
        )

    elif talent.evolution_stage == 1:
        required_items = 8
        if evolution_items < required_items:
            return False, talent, (
                f"Need **{required_items} Evolution Crystals** (you have {evolution_items})."
            )
        talent.evolution_stage = 2
        talent.name            = final_name
        talent.multiplier     *= 2.0
        talent.emoji           = "🌟" + talent.emoji
        return True, talent, (
            f"✦✦ **{evolved_name}** reached its **Final Form**: **{final_name}**! (×2.0 multiplier)"
        )

    return False, talent, "Something went wrong with evolution."


# ─────────────────────────────────────────────────────────────
#  LOCK / UNLOCK
# ─────────────────────────────────────────────────────────────

def toggle_lock(talent: PlayerTalent) -> str:
    talent.is_locked = not talent.is_locked
    state = "🔒 Locked" if talent.is_locked else "🔓 Unlocked"
    return f"{talent.name} is now **{state}**."


# ─────────────────────────────────────────────────────────────
#  ACCEPT / REJECT
# ─────────────────────────────────────────────────────────────

def accept_talent(
    player_data: PlayerTalentData,
    new_talent: PlayerTalent,
    replace_active: bool = True,
) -> str:
    """
    Set new_talent as active (replace_active=True) or push to inventory.

    When replacing, the old active talent is pushed to inventory.
    The caller is responsible for removing new_talent from inventory
    if it was promoted from there (to avoid duplication).
    """
    if replace_active:
        old = player_data.active_talent
        player_data.active_talent = new_talent
        if old is not None:
            player_data.inventory.append(old)
        return f"✅ **{new_talent.name}** is now your active talent!"
    else:
        player_data.inventory.append(new_talent)
        return f"✅ **{new_talent.name}** added to your inventory."


def reject_talent(new_talent: PlayerTalent) -> str:
    return f"❌ **{new_talent.name}** was discarded."