"""
db/talent.py
~~~~~~~~~~~~
All MySQL queries for the Talent system.

Changes in this revision
────────────────────────
• Fixed all ``ON DUPLICATE KEY UPDATE ... VALUES(col)`` deprecation warnings.
  MySQL 8.0.20+ deprecates the ``VALUES()`` function inside ODKU clauses.
  All queries now use the row alias pattern:
      INSERT INTO t (...) VALUES (...) AS new_row
      ON DUPLICATE KEY UPDATE col = new_row.col
• ``consume_spin_token`` retains the ``count`` keyword argument (fixes the
  AdminTalent.revoke_spin TypeError from the original single-arg version).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from db.database import execute, fetch_one, fetch_all

log = logging.getLogger("bot.database.talents")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ---------------------------------------------------------------------------
# Active talent  (player_talents — one row per player)
# ---------------------------------------------------------------------------

async def get_player_talent(discord_id: int) -> dict | None:
    return await fetch_one(
        "SELECT * FROM player_talents WHERE discord_id = %s",
        (discord_id,),
    )


async def has_talent(discord_id: int) -> bool:
    row = await fetch_one(
        "SELECT 1 FROM player_talents WHERE discord_id = %s",
        (discord_id,),
    )
    return row is not None


async def upsert_player_talent(
    discord_id: int,
    guild_id: int,
    talent_name: str,
    talent_rarity: str,
    talent_multiplier: float,
    evolution_stage: int = 0,
    is_corrupted: bool = False,
    is_locked: bool = False,
    tags: list[str] | None = None,
) -> dict:
    now       = _now_naive()
    tags_json = json.dumps(tags or [])

    # FIX: replaced deprecated VALUES(col) with row alias pattern
    await execute(
        """
        INSERT INTO player_talents
            (discord_id, guild_id, talent_name, talent_rarity,
             talent_multiplier, evolution_stage, is_corrupted, is_locked,
             tags, acquired_at, last_updated)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        AS new_row
        ON DUPLICATE KEY UPDATE
            guild_id          = new_row.guild_id,
            talent_name       = new_row.talent_name,
            talent_rarity     = new_row.talent_rarity,
            talent_multiplier = new_row.talent_multiplier,
            evolution_stage   = new_row.evolution_stage,
            is_corrupted      = new_row.is_corrupted,
            is_locked         = new_row.is_locked,
            tags              = new_row.tags,
            last_updated      = new_row.last_updated
        """,
        (
            discord_id, guild_id, talent_name, talent_rarity,
            talent_multiplier, evolution_stage, is_corrupted, is_locked,
            tags_json, now, now,
        ),
    )
    log.info(
        "Talents » upserted active talent discord_id=%s talent=%s rarity=%s",
        discord_id, talent_name, talent_rarity,
    )
    return await fetch_one(
        "SELECT * FROM player_talents WHERE discord_id = %s",
        (discord_id,),
    )


async def set_talent_lock(discord_id: int, locked: bool) -> None:
    await execute(
        "UPDATE player_talents SET is_locked = %s WHERE discord_id = %s",
        (locked, discord_id),
    )


async def set_evolution_stage(
    discord_id: int,
    stage: int,
    new_name: str,
    new_multiplier: float,
) -> dict:
    await execute(
        """
        UPDATE player_talents
        SET evolution_stage   = %s,
            talent_name       = %s,
            talent_multiplier = %s,
            last_updated      = %s
        WHERE discord_id = %s
        """,
        (stage, new_name, new_multiplier, _now_naive(), discord_id),
    )
    return await fetch_one(
        "SELECT * FROM player_talents WHERE discord_id = %s",
        (discord_id,),
    )


async def corrupt_active_talent(
    discord_id: int,
    corrupt_name: str,
    new_multiplier: float,
) -> None:
    await execute(
        """
        UPDATE player_talents
        SET talent_name       = %s,
            talent_multiplier = %s,
            is_corrupted      = TRUE,
            last_updated      = %s
        WHERE discord_id = %s
        """,
        (corrupt_name, new_multiplier, _now_naive(), discord_id),
    )


# ---------------------------------------------------------------------------
# Talent inventory  (talent_inventory — multiple rows per player)
# ---------------------------------------------------------------------------

async def get_inventory(discord_id: int) -> list[dict]:
    return await fetch_all(
        "SELECT * FROM talent_inventory WHERE discord_id = %s ORDER BY acquired_at ASC",
        (discord_id,),
    )


async def get_inventory_slot(discord_id: int, slot: int) -> dict | None:
    rows = await get_inventory(discord_id)
    if slot < 1 or slot > len(rows):
        return None
    return rows[slot - 1]


async def add_to_inventory(
    discord_id: int,
    guild_id: int,
    talent_name: str,
    talent_rarity: str,
    talent_multiplier: float,
    evolution_stage: int = 0,
    is_corrupted: bool = False,
    is_locked: bool = False,
    tags: list[str] | None = None,
) -> int:
    tags_json = json.dumps(tags or [])
    await execute(
        """
        INSERT INTO talent_inventory
            (discord_id, guild_id, talent_name, talent_rarity,
             talent_multiplier, evolution_stage, is_corrupted, is_locked, tags)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            discord_id, guild_id, talent_name, talent_rarity,
            talent_multiplier, evolution_stage, is_corrupted, is_locked,
            tags_json,
        ),
    )
    row = await fetch_one(
        """
        SELECT id FROM talent_inventory
        WHERE discord_id = %s
        ORDER BY acquired_at DESC
        LIMIT 1
        """,
        (discord_id,),
    )
    return row["id"] if row else -1


async def remove_inventory_item(item_id: int) -> None:
    await execute("DELETE FROM talent_inventory WHERE id = %s", (item_id,))


async def update_inventory_item(
    item_id: int,
    talent_name: str,
    talent_multiplier: float,
    evolution_stage: int,
    is_corrupted: bool,
    is_locked: bool,
) -> None:
    await execute(
        """
        UPDATE talent_inventory
        SET talent_name       = %s,
            talent_multiplier = %s,
            evolution_stage   = %s,
            is_corrupted      = %s,
            is_locked         = %s
        WHERE id = %s
        """,
        (talent_name, talent_multiplier, evolution_stage, is_corrupted, is_locked, item_id),
    )


# ---------------------------------------------------------------------------
# Spin pity  (talent_spin_pity)
# ---------------------------------------------------------------------------

async def get_spin_pity(discord_id: int) -> dict | None:
    return await fetch_one(
        "SELECT * FROM talent_spin_pity WHERE discord_id = %s",
        (discord_id,),
    )


async def upsert_spin_pity(
    discord_id: int,
    pity_elite: int,
    pity_heavenly: int,
    pity_mythical: int,
    total_spins: int,
) -> None:
    # FIX: replaced deprecated VALUES(col) with row alias pattern
    await execute(
        """
        INSERT INTO talent_spin_pity
            (discord_id, pity_elite, pity_heavenly, pity_mythical, total_spins)
        VALUES
            (%s, %s, %s, %s, %s)
        AS new_row
        ON DUPLICATE KEY UPDATE
            pity_elite    = new_row.pity_elite,
            pity_heavenly = new_row.pity_heavenly,
            pity_mythical = new_row.pity_mythical,
            total_spins   = new_row.total_spins
        """,
        (discord_id, pity_elite, pity_heavenly, pity_mythical, total_spins),
    )


# ---------------------------------------------------------------------------
# Fusion pity  (talent_fusion_pity)
# ---------------------------------------------------------------------------

async def get_fusion_pity(discord_id: int) -> dict | None:
    return await fetch_one(
        "SELECT * FROM talent_fusion_pity WHERE discord_id = %s",
        (discord_id,),
    )


async def upsert_fusion_pity(
    discord_id: int,
    fusion_pity: int,
    total_fusions: int,
) -> None:
    # FIX: replaced deprecated VALUES(col) with row alias pattern
    await execute(
        """
        INSERT INTO talent_fusion_pity
            (discord_id, fusion_pity, total_fusions)
        VALUES
            (%s, %s, %s)
        AS new_row
        ON DUPLICATE KEY UPDATE
            fusion_pity   = new_row.fusion_pity,
            total_fusions = new_row.total_fusions
        """,
        (discord_id, fusion_pity, total_fusions),
    )


# ---------------------------------------------------------------------------
# Spin tokens  (spin_tokens — guild-scoped)
# ---------------------------------------------------------------------------

async def get_spin_tokens(discord_id: int, guild_id: int) -> int:
    row = await fetch_one(
        "SELECT tokens FROM spin_tokens WHERE discord_id = %s AND guild_id = %s",
        (discord_id, guild_id),
    )
    return int(row["tokens"]) if row else 0


async def add_spin_tokens(discord_id: int, guild_id: int, amount: int) -> None:
    # FIX: replaced deprecated VALUES(col) with row alias pattern
    await execute(
        """
        INSERT INTO spin_tokens (discord_id, guild_id, tokens)
        VALUES (%s, %s, %s)
        AS new_row
        ON DUPLICATE KEY UPDATE tokens = tokens + new_row.tokens
        """,
        (discord_id, guild_id, amount),
    )


async def consume_spin_token(
    discord_id: int,
    guild_id: int,
    *,
    count: int = 1,
) -> None:
    """
    Deduct ``count`` spin tokens from the player's balance (floor: 0).

    ``count`` is keyword-only to prevent accidental positional misuse.
    """
    if count < 1:
        return
    await execute(
        """
        UPDATE spin_tokens
        SET tokens = GREATEST(tokens - %s, 0)
        WHERE discord_id = %s AND guild_id = %s
        """,
        (count, discord_id, guild_id),
    )


# ---------------------------------------------------------------------------
# Spin audit log  (talent_spin_log)
# ---------------------------------------------------------------------------

async def log_spin(
    discord_id: int,
    guild_id: int,
    talent_name: str,
    talent_rarity: str,
    pity_triggered: bool,
    accepted: bool,
) -> None:
    await execute(
        """
        INSERT INTO talent_spin_log
            (discord_id, guild_id, talent_name, talent_rarity, pity_triggered, accepted)
        VALUES
            (%s, %s, %s, %s, %s, %s)
        """,
        (discord_id, guild_id, talent_name, talent_rarity, pity_triggered, accepted),
    )


async def mark_last_spin_accepted(discord_id: int, guild_id: int) -> None:
    await execute(
        """
        UPDATE talent_spin_log
        SET accepted = TRUE
        WHERE discord_id = %s AND guild_id = %s
        ORDER BY spun_at DESC
        LIMIT 1
        """,
        (discord_id, guild_id),
    )


# ---------------------------------------------------------------------------
# Fusion audit log  (talent_fusion_log)
# ---------------------------------------------------------------------------

async def log_fusion(
    discord_id: int,
    guild_id: int,
    talent_a: str,
    talent_b: str,
    mode: str,
    success: bool,
    result_name: str | None,
    failure_outcome: str | None,
) -> None:
    await execute(
        """
        INSERT INTO talent_fusion_log
            (discord_id, guild_id, talent_a, talent_b, mode,
             success, result_name, failure_outcome)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            discord_id, guild_id, talent_a, talent_b, mode,
            success, result_name, failure_outcome,
        ),
    )


# ---------------------------------------------------------------------------
# One-per-server claimed talents  (server_claimed_talents)
# ---------------------------------------------------------------------------

async def get_claimed_server_talents(guild_id: int) -> list[str]:
    rows = await fetch_all(
        "SELECT talent_name FROM server_claimed_talents WHERE guild_id = %s",
        (guild_id,),
    )
    return [r["talent_name"] for r in rows]


async def get_claimed_one_per_server(guild_id: int) -> list[str]:
    return await get_claimed_server_talents(guild_id)


async def claim_server_talent(
    guild_id: int,
    discord_id: int,
    talent_name: str,
) -> bool:
    rows_affected = await execute(
        """
        INSERT IGNORE INTO server_claimed_talents
            (guild_id, discord_id, talent_name)
        VALUES (%s, %s, %s)
        """,
        (guild_id, discord_id, talent_name),
    )
    if rows_affected:
        log.info(
            "Talents » server talent claimed guild_id=%s discord_id=%s talent=%s",
            guild_id, discord_id, talent_name,
        )
    return rows_affected > 0


async def claim_one_per_server(guild_id: int, discord_id: int, talent_name: str) -> bool:
    return await claim_server_talent(guild_id, discord_id, talent_name)


async def get_server_talent_holder(guild_id: int, talent_name: str) -> dict | None:
    return await fetch_one(
        """
        SELECT * FROM server_claimed_talents
        WHERE guild_id = %s AND talent_name = %s
        """,
        (guild_id, talent_name),
    )


# ---------------------------------------------------------------------------
# Composite load / save  (used by all cog commands)
# ---------------------------------------------------------------------------

from talent.models import PlayerTalent, PlayerTalentData  # noqa: E402


def _row_to_player_talent(row: dict) -> PlayerTalent:
    return PlayerTalent(
        name=row["talent_name"],
        base_name=row.get("base_name") or row["talent_name"],
        rarity=row["talent_rarity"],
        description=row.get("description", ""),
        multiplier=float(row["talent_multiplier"]),
        color=0xFFFFFF,
        emoji="",
        evolution_stage=int(row.get("evolution_stage", 0)),
        is_corrupted=bool(row.get("is_corrupted", False)),
        is_locked=bool(row.get("is_locked", False)),
        tags=json.loads(row.get("tags") or "[]"),
    )


async def get_player_talent_data(
    discord_id: int,
    guild_id: int,
) -> PlayerTalentData | None:
    active_row = await get_player_talent(discord_id)
    inv_rows   = await get_inventory(discord_id)
    pity_row   = await get_spin_pity(discord_id)
    fusion_row = await get_fusion_pity(discord_id)

    if active_row is None and not inv_rows and pity_row is None:
        return None

    player = PlayerTalentData(user_id=discord_id, guild_id=guild_id)

    if active_row:
        player.active_talent = _row_to_player_talent(active_row)

    player.inventory = [_row_to_player_talent(r) for r in inv_rows]

    if pity_row:
        player.spin_pity = {
            "Elite":    pity_row.get("pity_elite",    0),
            "Heavenly": pity_row.get("pity_heavenly", 0),
            "Mythical": pity_row.get("pity_mythical", 0),
        }
        player.total_spins = pity_row.get("total_spins", 0)

    if fusion_row:
        player.fusion_pity   = fusion_row.get("fusion_pity",   0)
        player.total_fusions = fusion_row.get("total_fusions", 0)

    return player


async def save_player_talent_data(player: PlayerTalentData) -> None:
    did = player.user_id
    gid = player.guild_id

    if player.active_talent:
        t = player.active_talent
        await upsert_player_talent(
            discord_id=did,
            guild_id=gid,
            talent_name=t.name,
            talent_rarity=t.rarity,
            talent_multiplier=t.multiplier,
            evolution_stage=t.evolution_stage,
            is_corrupted=t.is_corrupted,
            is_locked=t.is_locked,
            tags=t.tags,
        )

    await execute(
        "DELETE FROM talent_inventory WHERE discord_id = %s",
        (did,),
    )
    for t in player.inventory:
        await add_to_inventory(
            discord_id=did,
            guild_id=gid,
            talent_name=t.name,
            talent_rarity=t.rarity,
            talent_multiplier=t.multiplier,
            evolution_stage=t.evolution_stage,
            is_corrupted=t.is_corrupted,
            is_locked=t.is_locked,
            tags=t.tags,
        )

    await upsert_spin_pity(
        discord_id=did,
        pity_elite=player.spin_pity.get("Elite", 0),
        pity_heavenly=player.spin_pity.get("Heavenly", 0),
        pity_mythical=player.spin_pity.get("Mythical", 0),
        total_spins=player.total_spins,
    )
    await upsert_fusion_pity(
        discord_id=did,
        fusion_pity=player.fusion_pity,
        total_fusions=player.total_fusions,
    )


# ---------------------------------------------------------------------------
# Admin reset
# ---------------------------------------------------------------------------

async def reset_player_talent_data(discord_id: int, guild_id: int) -> None:
    """Wipe every talent record for a player."""
    await execute("DELETE FROM player_talents     WHERE discord_id = %s", (discord_id,))
    await execute("DELETE FROM talent_inventory   WHERE discord_id = %s", (discord_id,))
    await execute("DELETE FROM talent_spin_pity   WHERE discord_id = %s", (discord_id,))
    await execute("DELETE FROM talent_fusion_pity WHERE discord_id = %s", (discord_id,))
    await execute(
        "DELETE FROM spin_tokens WHERE discord_id = %s AND guild_id = %s",
        (discord_id, guild_id),
    )
    log.info(
        "Talents » reset all talent data discord_id=%s guild_id=%s",
        discord_id, guild_id,
    )