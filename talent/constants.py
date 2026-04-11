# ============================================================
#  TALENT SYSTEM — CONSTANTS
#  All talent data, rarities, weights, evolutions, fusions
# ============================================================
from __future__ import annotations

# ── Rarity tiers ────────────────────────────────────────────
RARITIES = {
    "Trash":    {"weight": 400, "multiplier": 0.5,   "color": 0x808080, "emoji": "🗑️"},
    "Common":   {"weight": 300, "multiplier": 1.0,   "color": 0xFFFFFF, "emoji": "⚪"},
    "Rare":     {"weight": 180, "multiplier": 2.0,   "color": 0x3498DB, "emoji": "🔵"},
    "Elite":    {"weight":  80, "multiplier": 4.0,   "color": 0x9B59B6, "emoji": "🟣"},
    "Heavenly": {"weight":  25, "multiplier": 8.0,   "color": 0xF1C40F, "emoji": "⭐"},
    "Mythical": {"weight":   5, "multiplier": 16.0,  "color": 0xFF4500, "emoji": "🔥"},
    "Divine":   {"weight":   1, "multiplier": 32.0,  "color": 0x00FFFF, "emoji": "💠"},
    # NEW: Cosmic — above Divine, unreachable by normal spin, only via special fusion/events
    "Cosmic":   {"weight":   0, "multiplier": 64.0,  "color": 0xFF00FF, "emoji": "🌌"},
}

# ── Pity thresholds (spin-based) ─────────────────────────────
SPIN_PITY = {
    "Elite":    50,
    "Heavenly": 150,
    "Mythical": 300,
}

# ── Fusion pity thresholds ───────────────────────────────────
FUSION_PITY = {
    "boost":     8,   # NERFED: was 5 → now 8 failures before +20% boost
    "guarantee": 15,  # NERFED: was 10 → now 15 failures before guaranteed success
    "bonus":     20,  # NERFED: was 15 → now 20 failures before rarity tier-up
}

# ── One-per-server legendary talents ─────────────────────────
ONE_PER_SERVER_TALENTS = [
    "Heaven's Sole Heir",
    "Dao Incarnate",
    "Origin Singularity",
    "Primordial Abyss",
    "Celestial Throne Holder",
    # Cosmic exclusives are also one-per-server
    "Boundless Cosmic Throne",
    "Eternal Void Emperor",
]

# ── All talents ───────────────────────────────────────────────
# Format:
#   name        : unique talent name
#   rarity      : tier key from RARITIES
#   description : flavour text
#   evolution   : (evolved_name, final_form_name) or None
#   tags        : list of keywords used for cross-fusion matching
#   exclusive   : "fusion" | "mutation" | "corruption" | None
#                 Exclusive talents can ONLY be obtained through that specific
#                 outcome. They never appear in the normal spin pool.

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

    # ── COSMIC ───────────────────────────────────────────────
    # Cosmic talents are UNREACHABLE via normal spin (weight=0).
    # They can only be obtained through exclusive fusion/mutation/corruption paths.
    {"name": "Boundless Cosmic Throne", "rarity": "Cosmic",
     "description": "You sit at the axis of all realities. The stars bow. 🌌👑",
     "evolution": None, "tags": ["cosmic", "void", "dao", "chaos"],
     "one_per_server": True, "exclusive": "fusion"},

    {"name": "Eternal Void Emperor",    "rarity": "Cosmic",
     "description": "Emptiness itself kneels before you. 🌌👑",
     "evolution": None, "tags": ["cosmic", "void", "shadow", "fate"],
     "one_per_server": True, "exclusive": "fusion"},

    # ── EXCLUSIVE: FUSION-ONLY ROOTS ─────────────────────────
    # These can ONLY be produced by specific cross-fusion recipes.
    # They never appear in the spin pool.
    {"name": "Shattered Heaven Root",   "rarity": "Mythical",
     "description": "Born from the violent collision of divine wills. Only fusion births this.",
     "evolution": ("Fractured Heaven Core", "Heaven-Rending Physique"),
     "tags": ["heaven", "chaos", "combat"], "exclusive": "fusion"},

    {"name": "Abyssal Dao Seed",        "rarity": "Mythical",
     "description": "The Dao sank into the abyss and returned changed. Only fusion births this.",
     "evolution": ("Abyssal Dao Bloom",    "Void-Consuming Dao Body"),
     "tags": ["dao", "void", "shadow"], "exclusive": "fusion"},

    {"name": "Twin Flame Meridians",    "rarity": "Heavenly",
     "description": "Two fires merged into an undying double helix. Only fusion births this.",
     "evolution": ("Eternal Twin Blaze",   "Undying Dual Pyre Core"),
     "tags": ["fire", "rebirth", "qi"], "exclusive": "fusion"},

    {"name": "Collapsed Star Core",     "rarity": "Heavenly",
     "description": "A dying star compressed into your dantian. Only fusion births this.",
     "evolution": ("Neutron Star Dantian","Stellar Singularity Core"),
     "tags": ["star", "body", "chaos"], "exclusive": "fusion"},

    {"name": "Voidsteel Frame",         "rarity": "Elite",
     "description": "Iron and void forged together by impossible heat. Only fusion births this.",
     "evolution": ("Voidsteel Skeleton",   "Dimensional Iron Sovereign"),
     "tags": ["iron", "void", "body"], "exclusive": "fusion"},

    # ── EXCLUSIVE: MUTATION-ONLY ROOTS ───────────────────────
    # These can ONLY appear as a mutation failure result.
    {"name": "Aberrant Qi Root",        "rarity": "Elite",
     "description": "Your Qi mutated into something the heavens never intended. Only mutation births this.",
     "evolution": ("Deviant Qi Vortex",    "Chaos Qi Sovereign"),
     "tags": ["qi", "chaos", "void"], "exclusive": "mutation"},

    {"name": "Inverted Fate Vein",      "rarity": "Heavenly",
     "description": "Destiny ran backwards through your meridians and stuck. Only mutation births this.",
     "evolution": ("Reversed Heaven Path", "Anti-Fate Embodiment"),
     "tags": ["fate", "shadow", "void"], "exclusive": "mutation"},

    {"name": "Pale Bone Demon Root",    "rarity": "Rare",
     "description": "Something inhuman woke inside your skeleton. Only mutation births this.",
     "evolution": ("Bone Demon Spine",     "Undying Demon Skeleton"),
     "tags": ["body", "shadow", "rebirth"], "exclusive": "mutation"},

    {"name": "Fractured Lightning Soul","rarity": "Elite",
     "description": "A lightning strike split your soul — then it healed wrong. Only mutation births this.",
     "evolution": ("Split Thunder Psyche", "Dual Soul Lightning Form"),
     "tags": ["lightning", "spirit", "chaos"], "exclusive": "mutation"},

    {"name": "Unwritten Dao Shard",     "rarity": "Mythical",
     "description": "A fragment of a Dao that doesn't exist yet. Only mutation births this.",
     "evolution": ("Proto-Dao Embryo",     "Axiom of the Unwritten"),
     "tags": ["dao", "chaos", "fate"], "exclusive": "mutation"},

    # ── EXCLUSIVE: CORRUPTION-ONLY ROOTS ─────────────────────
    # These can ONLY be obtained through corruption failure.
    # Corruption has been BUFFED — these are genuinely strong dark alternatives.
    {"name": "Devouring Dark Root",     "rarity": "Elite",
     "description": "Corruption consumed your talent and awakened something ravenous. Only corruption births this.",
     "evolution": ("Hungering Void Core",  "All-Consuming Abyss Root"),
     "tags": ["void", "shadow", "chaos"], "exclusive": "corruption", "is_corrupted": True},

    {"name": "Cursed Dragon Marrow",    "rarity": "Heavenly",
     "description": "Dragon blood turned black, but runs hotter than ever. Only corruption births this.",
     "evolution": ("Plague Dragon Body",   "Undying Cursed Dragon Sovereign"),
     "tags": ["dragon", "shadow", "rebirth"], "exclusive": "corruption", "is_corrupted": True},

    {"name": "Shattered Nirvana Core",  "rarity": "Heavenly",
     "description": "You died in the fusion and came back wrong — and stronger. Only corruption births this.",
     "evolution": ("Broken Nirvana Body",  "Undead Nirvana Emperor"),
     "tags": ["rebirth", "chaos", "spirit"], "exclusive": "corruption", "is_corrupted": True},

    {"name": "Voidrot Physique",        "rarity": "Mythical",
     "description": "Void energy rotted your foundation — then rebuilt it from nothing. Only corruption births this.",
     "evolution": ("Void Decay Sovereign", "Absolute Voidrot Manifestation"),
     "tags": ["void", "body", "chaos"], "exclusive": "corruption", "is_corrupted": True},

    {"name": "Accursed Heaven Brand",   "rarity": "Elite",
     "description": "Heaven marked you for death. You ignored it. Only corruption births this.",
     "evolution": ("Heaven-Defying Scar", "Heavenbreaker Stigma"),
     "tags": ["heaven", "fate", "combat"], "exclusive": "corruption", "is_corrupted": True},

    {"name": "Necrotic Flame Seed",     "rarity": "Rare",
     "description": "The fire didn't purify. It decayed. But decay has its own power. Only corruption births this.",
     "evolution": ("Death Flame Core",    "Undying Plague Pyre"),
     "tags": ["fire", "shadow", "rebirth"], "exclusive": "corruption", "is_corrupted": True},
]

# ── Fusion rules ──────────────────────────────────────────────
# NERFED from original values
FUSION_SAME_RARITY_SUCCESS_CHANCE = 0.70   # NERFED: was 0.85 → now 70%
FUSION_CROSS_SUCCESS_CHANCE       = 0.40   # NERFED: was 0.55 → now 40%
FUSION_RNG_SUCCESS_CHANCE         = 0.20   # NERFED: was 0.35 → now 20%

# Failure outcomes (weighted)
# BUFFED: corruption weight raised significantly
FAILURE_OUTCOMES = {
    "backfire":    {"weight": 30, "description": "Minor setback — the fusion destabilizes."},
    "corruption":  {"weight": 50, "description": "A dark power seizes the fusion — something corrupted awakens."},  # BUFFED: was 30 → 50
    "mutation":    {"weight": 15, "description": "Rare mutation — something entirely unexpected manifests!"},
    "catastrophic":{"weight":  5, "description": "Catastrophic collapse — both talents are destroyed."},
}

# Tag-based cross-fusion combinations → produce a specific named result
# Includes recipes for exclusive fusion-only roots
CROSS_FUSION_RECIPES = [
    # Standard recipes (produce normal pool talents)
    {"tags_required": ["fire", "body"],      "result": "Dragon Body",            "min_rarity": "Common"},
    {"tags_required": ["ice", "body"],       "result": "Eternal Ice Veins",      "min_rarity": "Rare"},
    {"tags_required": ["void", "shadow"],    "result": "Void Walker",            "min_rarity": "Elite"},
    {"tags_required": ["chaos", "heaven"],   "result": "Heaven Devourer",        "min_rarity": "Heavenly"},
    {"tags_required": ["dao", "void"],       "result": "Absolute Dao Root",      "min_rarity": "Mythical"},
    {"tags_required": ["lightning", "body"], "result": "Thunder Veins",          "min_rarity": "Rare"},
    {"tags_required": ["fire", "rebirth"],   "result": "Phoenix Root",           "min_rarity": "Rare"},
    {"tags_required": ["mind", "heaven"],    "result": "Heavenly Mind",          "min_rarity": "Elite"},
    {"tags_required": ["dragon", "chaos"],   "result": "God Slayer Body",        "min_rarity": "Mythical"},

    # Exclusive fusion-only recipes (produce exclusive roots)
    {"tags_required": ["heaven", "chaos", "combat"],   "result": "Shattered Heaven Root",   "min_rarity": "Mythical", "exclusive": True},
    {"tags_required": ["dao", "void", "shadow"],       "result": "Abyssal Dao Seed",         "min_rarity": "Mythical", "exclusive": True},
    {"tags_required": ["fire", "rebirth", "qi"],       "result": "Twin Flame Meridians",     "min_rarity": "Heavenly", "exclusive": True},
    {"tags_required": ["star", "body", "chaos"],       "result": "Collapsed Star Core",      "min_rarity": "Heavenly", "exclusive": True},
    {"tags_required": ["iron", "void", "body"],        "result": "Voidsteel Frame",          "min_rarity": "Elite",    "exclusive": True},

    # Cosmic recipes — require Divine-tier ingredients
    {"tags_required": ["cosmic", "void", "dao"],       "result": "Boundless Cosmic Throne",  "min_rarity": "Divine",   "exclusive": True, "requires_divine": True},
    {"tags_required": ["cosmic", "void", "shadow"],    "result": "Eternal Void Emperor",     "min_rarity": "Divine",   "exclusive": True, "requires_divine": True},
    # Cosmic can also be triggered by fusing two Mythicals with dao+chaos overlap
    {"tags_required": ["dao", "chaos", "void", "fate"],"result": "Boundless Cosmic Throne", "min_rarity": "Mythical",  "exclusive": True},
]

# ── Corruption name mappings ──────────────────────────────────
# BUFFED: Corruption now produces exclusive corruption-only roots instead of weak named variants.
# The exclusive roots in TALENT_POOL handle corruption outcomes.
# This mapping is used as a FALLBACK for talents without a dedicated corruption exclusive.
CORRUPTION_NAMES = {
    # These fallback names still exist for talents not covered by exclusive roots
    "Dragon Body":       "Corrupted Dragon Husk",
    "Heavenly Mind":     "Fractured Void Psyche",
    "Phoenix Root":      "Ashen Undeath Root",
    "Star Fate":         "Cursed Star Brand",
    "Chaos Root":        "Devouring Chaos Rot",
    "Iron Body":         "Rotting Iron Cage",
    "Thunder Veins":     "Crackling Cursed Veins",
    # fallback: any talent not listed → "Darkened <name>"
}

# Corruption outcome: which exclusive root a corrupted talent produces based on its tags.
# Each entry: (tag_set, exclusive_corruption_root_name)
# Checked in order — first match wins. If no match, falls back to CORRUPTION_NAMES.
CORRUPTION_TAG_ROOTS: list[tuple[frozenset[str], str]] = [
    (frozenset({"dragon"}),          "Cursed Dragon Marrow"),
    (frozenset({"rebirth", "fire"}), "Necrotic Flame Seed"),
    (frozenset({"rebirth"}),         "Shattered Nirvana Core"),
    (frozenset({"void", "body"}),    "Voidrot Physique"),
    (frozenset({"heaven", "fate"}),  "Accursed Heaven Brand"),
    (frozenset({"void"}),            "Devouring Dark Root"),
    # Final catch-all — any corrupted talent with any void/shadow/chaos tag
    (frozenset({"shadow"}),          "Devouring Dark Root"),
    (frozenset({"chaos"}),           "Devouring Dark Root"),
]

RARITY_ORDER = ["Trash", "Common", "Rare", "Elite", "Heavenly", "Mythical", "Divine", "Cosmic"]