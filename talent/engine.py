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
    CORRUPTION_NAMES, CORRUPTION_TAG_ROOTS,
    ONE_PER_SERVER_TALENTS,
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


def _player_talent_from(talent: Talent, force_corrupted: bool = False) -> PlayerTalent:
    return PlayerTalent(
        name=talent.name,
        base_name=talent.name,
        rarity=talent.rarity,
        description=talent.description,
        multiplier=talent.multiplier,
        color=talent.color,
        emoji=talent.emoji,
        tags=talent.tags,
        is_corrupted=force_corrupted,
    )


def _exclusive_pool(exclusive_type: str) -> list[dict]:
    """Return entries that are gated behind a specific exclusive type."""
    return [e for e in TALENT_POOL if e.get("exclusive") == exclusive_type]


# ─────────────────────────────────────────────────────────────
#  STARTER TALENT  (assigned on /start — pure RNG, no Divine/Cosmic)
# ─────────────────────────────────────────────────────────────

def roll_starter_talent(claimed_one_per_server: list[str]) -> PlayerTalent:
    """
    Roll a random starting talent for a brand-new cultivator.

    Rules:
    - Divine and Cosmic are excluded — they should be earned.
    - Exclusive talents (fusion/mutation/corruption) are excluded.
    - One-per-server talents already claimed are excluded.
    """
    eligible = [
        e for e in TALENT_POOL
        if e["rarity"] not in ("Divine", "Cosmic")
        and not e.get("exclusive")
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

    Exclusive talents (fusion/mutation/corruption) and Cosmic are never spun.

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

    # ── filter pool: no Cosmic, no exclusives, no one-per-server already claimed ──
    eligible = [
        e for e in TALENT_POOL
        if _rarity_index(e["rarity"]) >= _rarity_index(min_rarity)
        and e["rarity"] != "Cosmic"
        and not e.get("exclusive")
        and not (e.get("one_per_server") and e["name"] in claimed_one_per_server)
    ]
    if not eligible:
        # safety fallback — should never happen unless pool is extremely sparse
        eligible = [e for e in TALENT_POOL if e["rarity"] not in ("Divine", "Cosmic") and not e.get("exclusive")]

    # ── weighted rarity selection ─────────────────────────────
    rarity_weights = {
        r: (RARITIES[r]["weight"] if _rarity_index(r) >= _rarity_index(min_rarity) and r != "Cosmic" else 0)
        for r in RARITY_ORDER
    }

    chosen_rarity = random.choices(
        list(rarity_weights.keys()),
        weights=list(rarity_weights.values()),
        k=1,
    )[0]

    rarity_pool = [e for e in eligible if e["rarity"] == chosen_rarity]
    if not rarity_pool:
        rarity_pool = eligible
    entry  = random.choice(rarity_pool)
    talent = _build_talent_obj(entry)

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


def _get_cross_recipe(t1: PlayerTalent, t2: PlayerTalent) -> Optional[dict]:
    """
    Return the best matching cross-fusion recipe dict or None.

    Exclusive recipes take priority over standard ones.
    Cosmic recipes require at least one Divine-rarity talent.
    """
    combined_tags = set(t1.tags) | set(t2.tags)
    has_divine    = t1.rarity in ("Divine", "Cosmic") or t2.rarity in ("Divine", "Cosmic")

    # Sort: exclusive recipes checked first, then standard.
    sorted_recipes = sorted(CROSS_FUSION_RECIPES, key=lambda r: (not r.get("exclusive", False)))

    for recipe in sorted_recipes:
        tags_required = set(recipe["tags_required"])
        if not tags_required.issubset(combined_tags):
            continue
        if recipe.get("requires_divine") and not has_divine:
            continue
        return recipe

    return None


def _resolve_corruption_exclusive(source_talent: PlayerTalent) -> Optional[PlayerTalent]:
    """
    BUFFED: Try to produce an exclusive corruption-only root based on the source talent's tags.
    Falls back to a named corrupted variant of the source if no exclusive matches.
    """
    talent_tags = set(source_talent.tags)

    # Try to match an exclusive corruption root
    for required_tags, root_name in CORRUPTION_TAG_ROOTS:
        if required_tags.issubset(talent_tags):
            entry = next((e for e in TALENT_POOL if e["name"] == root_name), None)
            if entry:
                pt = _player_talent_from(_build_talent_obj(entry), force_corrupted=True)
                return pt

    # Fallback: named corrupted variant or "Darkened <name>"
    corrupt_name  = CORRUPTION_NAMES.get(source_talent.name, f"Darkened {source_talent.name}")
    rarity_data   = RARITIES[source_talent.rarity]
    return PlayerTalent(
        name=corrupt_name,
        base_name=source_talent.base_name,
        rarity=source_talent.rarity,
        description="A dark, twisted version. Something went very wrong.",
        multiplier=source_talent.multiplier * 0.6,   # BUFFED: was 0.5 → now 0.6
        color=0x2C2C2C,
        emoji="☠️",
        is_corrupted=True,
        tags=source_talent.tags,
    )


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
      - "same"  → force same-rarity path
      - "cross" → force cross-fusion path
      - "rng"   → pure random regardless of rarities

    Returns a result dict:
    {
        "success":             bool,
        "result_talent":       PlayerTalent | None,
        "failure_outcome":     str | None,
        "failure_description": str | None,
        "pity_bonus":          bool,
        "pity_guarantee":      bool,
        "new_pity":            int,
        "resolved_mode":       str,
    }
    """
    player_data.total_fusions += 1
    pity = player_data.fusion_pity

    # ── resolve mode ────────────────────────────────────────
    if mode == "auto":
        resolved_mode = "same" if talent_a.rarity == talent_b.rarity else "cross"
    elif mode in ("same", "cross", "rng"):
        resolved_mode = mode
    else:
        resolved_mode = "cross"

    if resolved_mode == "same":
        base_chance = FUSION_SAME_RARITY_SUCCESS_CHANCE
    elif resolved_mode == "rng":
        base_chance = FUSION_RNG_SUCCESS_CHANCE
    else:
        base_chance = FUSION_CROSS_SUCCESS_CHANCE

    # ── pity bonuses ─────────────────────────────────────────
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

        result_talent: Optional[PlayerTalent] = None

        if resolved_mode == "same":
            new_rarity = _bump_rarity(talent_a.rarity)
            # Exclude exclusives from normal same-rarity fusion result pool
            pool = [e for e in TALENT_POOL if e["rarity"] == new_rarity and not e.get("exclusive")]
            if pool:
                result_talent = _player_talent_from(_build_talent_obj(random.choice(pool)))

        elif resolved_mode == "cross":
            recipe = _get_cross_recipe(talent_a, talent_b)
            if recipe:
                t = _talent_by_name(recipe["result"])
                if t:
                    result_talent = _player_talent_from(t)
            if not result_talent:
                # No recipe matched — fall back to bumped rarity from the higher of the two
                higher_rarity = max(
                    talent_a.rarity, talent_b.rarity,
                    key=lambda r: _rarity_index(r),
                )
                new_rarity = _bump_rarity(higher_rarity)
                pool = [e for e in TALENT_POOL if e["rarity"] == new_rarity and not e.get("exclusive")]
                if pool:
                    result_talent = _player_talent_from(_build_talent_obj(random.choice(pool)))

        else:  # rng
            # Exclude Cosmic, Divine, and exclusives from pure-rng roll
            pool = [
                e for e in TALENT_POOL
                if e["rarity"] not in ("Trash", "Cosmic")
                and not e.get("exclusive")
            ]
            if pool:
                result_talent = _player_talent_from(_build_talent_obj(random.choice(pool)))

        # pity tier-up: bump result rarity one extra step (excluding exclusives and Cosmic)
        if pity_tier_up and result_talent:
            bumped_rarity = _bump_rarity(result_talent.rarity)
            pool = [e for e in TALENT_POOL if e["rarity"] == bumped_rarity and not e.get("exclusive") and bumped_rarity != "Cosmic"]
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

        result_talent: Optional[PlayerTalent] = None

        if failure["outcome"] == "corruption":
            # BUFFED: corruption now produces exclusive corruption roots
            result_talent = _resolve_corruption_exclusive(talent_a)

        elif failure["outcome"] == "mutation":
            # NERFED: mutation now draws from the exclusive mutation pool only
            mutation_pool = _exclusive_pool("mutation")
            if mutation_pool:
                result_talent = _player_talent_from(_build_talent_obj(random.choice(mutation_pool)))
            else:
                # Safety fallback if pool somehow empty
                pool = [
                    e for e in TALENT_POOL
                    if _rarity_index(e["rarity"]) >= _rarity_index("Rare")
                    and not e.get("exclusive")
                ]
                if pool:
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

    # Look up by base_name so evolved talents can still find their entry
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
    before calling this (to avoid duplication).
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