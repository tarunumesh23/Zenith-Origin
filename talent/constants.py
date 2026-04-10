# ============================================================
#  TALENT SYSTEM — CONSTANTS
#  All talent data, rarities, weights, evolutions, fusions
# ============================================================
from __future__ import annotations

# ── Rarity tiers ────────────────────────────────────────────
RARITIES = {
    "Trash":    {"weight": 400, "multiplier": 0.5,  "color": 0x808080, "emoji": "🗑️"},
    "Common":   {"weight": 300, "multiplier": 1.0,  "color": 0xFFFFFF, "emoji": "⚪"},
    "Rare":     {"weight": 180, "multiplier": 2.0,  "color": 0x3498DB, "emoji": "🔵"},
    "Elite":    {"weight":  80, "multiplier": 4.0,  "color": 0x9B59B6, "emoji": "🟣"},
    "Heavenly": {"weight":  25, "multiplier": 8.0,  "color": 0xF1C40F, "emoji": "⭐"},
    "Mythical": {"weight":   5, "multiplier": 16.0, "color": 0xFF4500, "emoji": "🔥"},
    "Divine":   {"weight":   1, "multiplier": 32.0, "color": 0x00FFFF, "emoji": "💠"},
}

# ── Pity thresholds (spin-based) ─────────────────────────────
SPIN_PITY = {
    "Elite":    50,   # guaranteed Elite+ after 50 spins without one
    "Heavenly": 150,  # guaranteed Heavenly+ after 150 spins without one
    "Mythical": 300,  # guaranteed Mythical+ after 300 spins without one
}

# ── Fusion pity thresholds ───────────────────────────────────
# These are checked in order: guarantee supersedes boost.
# A player at pity=15 will have ALL three flags true simultaneously;
# only pity_guarantee matters for the success roll — pity_tier_up
# still fires to bump the result rarity one extra step.
FUSION_PITY = {
    "boost":     5,   # after 5 failures  → +20% success chance
    "guarantee": 10,  # after 10 failures → guaranteed success
    "bonus":     15,  # after 15 failures → result rarity bumped +1
}

# ── One-per-server legendary talents ─────────────────────────
ONE_PER_SERVER_TALENTS = [
    "Heaven's Sole Heir",
    "Dao Incarnate",
    "Origin Singularity",
    "Primordial Abyss",
    "Celestial Throne Holder",
]

# ── All talents ───────────────────────────────────────────────
# Format:
#   name        : unique talent name
#   rarity      : tier key from RARITIES
#   description : flavour text
#   evolution   : (evolved_name, final_form_name) or None
#   tags        : list of keywords used for cross-fusion matching

TALENT_POOL = [
    # ── TRASH ───────────────────────────────────────────────
    {"name": "Mud Roots",          "rarity": "Trash",    "description": "Your foundation is… questionable.",
     "evolution": ("Cracked Earth Roots", "Stone Pillar Foundation"), "tags": ["earth", "body"]},
    {"name": "Dull Spirit",        "rarity": "Trash",    "description": "A barely flickering inner flame.",
     "evolution": ("Dim Ember Spirit",    "Smoldering Core"),         "tags": ["spirit", "fire"]},
    {"name": "Brittle Bones",      "rarity": "Trash",    "description": "Snap under the slightest pressure.",
     "evolution": ("Hardened Bones",      "Iron Skeleton"),           "tags": ["body", "iron"]},
    {"name": "Foggy Mind",         "rarity": "Trash",    "description": "Comprehension is… slow.",
     "evolution": ("Clearing Mist Mind",  "Sharp Clarity"),           "tags": ["mind", "water"]},
    {"name": "Weak Meridians",     "rarity": "Trash",    "description": "Qi flows like a trickle.",
     "evolution": ("Open Meridians",      "Meridian Master"),         "tags": ["qi", "flow"]},

    # ── COMMON ──────────────────────────────────────────────
    {"name": "Stone Body",         "rarity": "Common",   "description": "Solid, but unremarkable.",
     "evolution": ("Iron Body",           "Steel Demon Body"),        "tags": ["body", "earth", "iron"]},
    {"name": "Wind Steps",         "rarity": "Common",   "description": "Feet that barely kiss the ground.",
     "evolution": ("Gale Steps",          "Void Wind Stride"),        "tags": ["wind", "speed"]},
    {"name": "Flame Spark",        "rarity": "Common",   "description": "A tiny fire burns within.",
     "evolution": ("Rising Flame",        "Eternal Pyre Core"),       "tags": ["fire", "spirit"]},
    {"name": "Calm Waters",        "rarity": "Common",   "description": "Mind as still as a pond.",
     "evolution": ("Deep River Mind",     "Boundless Ocean Psyche"),  "tags": ["water", "mind"]},
    {"name": "Iron Fists",         "rarity": "Common",   "description": "Strikes carry weight.",
     "evolution": ("Crushing Iron Fists", "Mountain-Shattering Palms"), "tags": ["iron", "combat", "body"]},
    {"name": "Bark Skin",          "rarity": "Common",   "description": "Tougher than it looks.",
     "evolution": ("Ironwood Hide",       "Ancient Tree Armor"),      "tags": ["earth", "body", "defense"]},
    {"name": "Quick Wits",         "rarity": "Common",   "description": "Faster thinking than most.",
     "evolution": ("Sharp Intellect",     "Heaven-Piercing Wisdom"),  "tags": ["mind", "lightning"]},

    # ── RARE ────────────────────────────────────────────────
    {"name": "Dragon Body",        "rarity": "Rare",     "description": "The bloodline stirs.",
     "evolution": ("True Dragon Body",    "Ancient Dragon Sovereign"), "tags": ["dragon", "body", "fire"]},
    {"name": "Jade Bones",         "rarity": "Rare",     "description": "Qi flows unimpeded.",
     "evolution": ("Crystal Jade Frame",  "Divine Jade Skeleton"),    "tags": ["body", "earth", "qi"]},
    {"name": "Thunder Veins",      "rarity": "Rare",     "description": "Lightning crackles in your blood.",
     "evolution": ("Storm Veins",         "Heaven Thunder Physique"),  "tags": ["lightning", "body", "speed"]},
    {"name": "Shadow Cloak",       "rarity": "Rare",     "description": "Darkness bends to your will.",
     "evolution": ("Void Shadow",         "Abyssal Phantom"),         "tags": ["shadow", "void", "stealth"]},
    {"name": "Phoenix Root",       "rarity": "Rare",     "description": "Death is only a setback.",
     "evolution": ("Rising Phoenix",      "Undying Nirvana Body"),    "tags": ["fire", "rebirth", "spirit"]},
    {"name": "Chaos Root",         "rarity": "Rare",     "description": "Order and disorder coexist within.",
     "evolution": ("Chaotic Core",        "Primordial Chaos Physique"), "tags": ["chaos", "void", "spirit"]},

    # ── ELITE ────────────────────────────────────────────────
    {"name": "Heavenly Mind",      "rarity": "Elite",    "description": "Comprehension transcends mortals.",
     "evolution": ("Celestial Intellect", "Omniscient Heavenly Psyche"), "tags": ["mind", "heaven", "lightning"]},
    {"name": "Blood Dragon Core",  "rarity": "Elite",    "description": "True dragon blood flows through you.",
     "evolution": ("Awakened Dragon Core","Supreme Dragon Emperor"),  "tags": ["dragon", "fire", "body"]},
    {"name": "Void Walker",        "rarity": "Elite",    "description": "Space itself yields to you.",
     "evolution": ("Space Sovereign",     "Dimensional Monarch"),     "tags": ["void", "space", "shadow"]},
    {"name": "Star Fate",          "rarity": "Elite",    "description": "Heaven itself marked you.",
     "evolution": ("Astral Destiny",      "Heaven's Chosen Mark"),    "tags": ["heaven", "fate", "star"]},
    {"name": "Eternal Ice Veins",  "rarity": "Elite",    "description": "Cold beyond absolute zero.",
     "evolution": ("Absolute Zero Core",  "Glacial Immortal Physique"), "tags": ["ice", "water", "body"]},

    # ── HEAVENLY ─────────────────────────────────────────────
    {"name": "True Chaos Body",    "rarity": "Heavenly", "description": "You embody the formless beginning.",
     "evolution": ("Chaos Sovereign Body","Primordial Formless Physique"), "tags": ["chaos", "void", "body"]},
    {"name": "Nine Lives Spirit",  "rarity": "Heavenly", "description": "Death has tried nine times.",
     "evolution": ("Deathless Soul",      "Eternal Revenant Core"),   "tags": ["spirit", "rebirth", "shadow"]},
    {"name": "Heaven Devourer",    "rarity": "Heavenly", "description": "You consume fate itself.",
     "evolution": ("Fate Eater",          "Devourer of Heavens"),     "tags": ["chaos", "heaven", "void"]},
    {"name": "Immortal Flame",     "rarity": "Heavenly", "description": "A fire that never dies.",
     "evolution": ("Undying Blaze",       "Origin Fire of Immortality"), "tags": ["fire", "rebirth", "spirit"]},

    # ── MYTHICAL ─────────────────────────────────────────────
    {"name": "Absolute Dao Root",  "rarity": "Mythical", "description": "Your existence resonates with the Dao.",
     "evolution": ("Dao Embryo",          "Living Dao Manifestation"), "tags": ["dao", "heaven", "chaos"]},
    {"name": "God Slayer Body",    "rarity": "Mythical", "description": "Made to kill what shouldn't die.",
     "evolution": ("Divine Killer Frame", "Apex Godslayer Physique"), "tags": ["body", "chaos", "combat"]},
    {"name": "Primal Origin",      "rarity": "Mythical", "description": "You existed before existence.",
     "evolution": ("Ancient Primal Core", "Singularity of Origin"),   "tags": ["chaos", "void", "fate"]},

    # ── DIVINE ───────────────────────────────────────────────
    {"name": "Heaven's Sole Heir", "rarity": "Divine",   "description": "Only one may carry this burden. 👑",
     "evolution": None, "tags": ["heaven", "fate", "dao"], "one_per_server": True},
    {"name": "Dao Incarnate",      "rarity": "Divine",   "description": "You are not a cultivator. You are the Dao. 👑",
     "evolution": None, "tags": ["dao", "chaos", "void"], "one_per_server": True},
    {"name": "Origin Singularity", "rarity": "Divine",   "description": "The point before the beginning. 👑",
     "evolution": None, "tags": ["chaos", "void", "fate"], "one_per_server": True},
]

# ── Fusion rules ──────────────────────────────────────────────
FUSION_SAME_RARITY_SUCCESS_CHANCE = 0.85   # 85% success for same-rarity fusion
FUSION_CROSS_SUCCESS_CHANCE       = 0.55   # 55% success for cross-fusion
FUSION_RNG_SUCCESS_CHANCE         = 0.35   # 35% for pure RNG fusion

# Failure outcomes (weighted)
FAILURE_OUTCOMES = {
    "backfire":    {"weight": 50, "description": "Minor setback — talent weakened temporarily."},
    "corruption":  {"weight": 30, "description": "Talent corrupted into a dark version."},
    "mutation":    {"weight": 15, "description": "Rare mutation — unexpected unique result!"},
    "catastrophic":{"weight":  5, "description": "Catastrophic failure — talent destroyed."},
}

# Tag-based cross-fusion combinations → produce a specific named result
CROSS_FUSION_RECIPES = [
    {"tags_required": ["fire", "body"],     "result": "Dragon Body",       "min_rarity": "Common"},
    {"tags_required": ["ice", "body"],      "result": "Eternal Ice Veins", "min_rarity": "Rare"},
    {"tags_required": ["void", "shadow"],   "result": "Void Walker",       "min_rarity": "Elite"},
    {"tags_required": ["chaos", "heaven"],  "result": "Heaven Devourer",   "min_rarity": "Heavenly"},
    {"tags_required": ["dao", "void"],      "result": "Absolute Dao Root", "min_rarity": "Mythical"},
    {"tags_required": ["lightning", "body"],"result": "Thunder Veins",     "min_rarity": "Rare"},
    {"tags_required": ["fire", "rebirth"],  "result": "Phoenix Root",      "min_rarity": "Rare"},
    {"tags_required": ["mind", "heaven"],   "result": "Heavenly Mind",     "min_rarity": "Elite"},
    {"tags_required": ["dragon", "chaos"],  "result": "God Slayer Body",   "min_rarity": "Mythical"},
]

# ── Corruption name mappings ──────────────────────────────────
CORRUPTION_NAMES = {
    "Dragon Body":       "Corrupted Dragon Husk",
    "Heavenly Mind":     "Fractured Void Psyche",
    "Phoenix Root":      "Ashen Undeath Root",
    "Star Fate":         "Cursed Star Brand",
    "Chaos Root":        "Devouring Chaos Rot",
    "Iron Body":         "Rotting Iron Cage",
    "Thunder Veins":     "Crackling Cursed Veins",
    # fallback: any talent not listed → "Darkened <name>"
}

RARITY_ORDER = ["Trash", "Common", "Rare", "Elite", "Heavenly", "Mythical", "Divine"]