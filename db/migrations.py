from __future__ import annotations

import asyncio
import logging

from db.database import connect, disconnect, execute, fetch_one

log = logging.getLogger("bot.migrations")

# ---------------------------------------------------------------------------
# Migration list
# Each entry is either:
#   - a plain SQL string  → executed directly
#   - a callable          → async def(total: int) -> None
#
# Callables are used for migrations that need conditional logic because
# MySQL does not support ADD COLUMN IF NOT EXISTS (MariaDB-only syntax).
# ---------------------------------------------------------------------------


async def _migration_7(total: int) -> None:
    """
    Add last_updated column for installs that ran migrations 1-6 before
    this column was introduced.
    FIX #9: pass an explicit empty tuple for args instead of None.
    """
    row = await fetch_one(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'cultivators'
          AND COLUMN_NAME  = 'last_updated'
        """,
        (),   # FIX #9: explicit empty args tuple
    )
    if row and row["cnt"]:
        log.debug("Migration 7/%d SKIP — last_updated already exists", total)
        return

    await execute(
        """
        ALTER TABLE cultivators
            ADD COLUMN last_updated DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                AFTER qi_threshold
        """
    )
    log.debug("Migration 7/%d OK — last_updated column added", total)


MIGRATIONS: list[str | object] = [
    # 1. Core cultivators table
    """
    CREATE TABLE IF NOT EXISTS cultivators (
        discord_id      BIGINT UNSIGNED     NOT NULL PRIMARY KEY,
        username        VARCHAR(100)        NOT NULL,
        display_name    VARCHAR(100)        NOT NULL,
        joined_at       DATETIME            NOT NULL,
        registered_at   DATETIME            NOT NULL,
        outcome         ENUM('pass','retry','fail') NOT NULL,

        realm           ENUM('mortal','qi_gathering','qi_condensation','qi_refining')
                            NOT NULL DEFAULT 'mortal',
        stage           TINYINT UNSIGNED    NOT NULL DEFAULT 1,
        qi              INT UNSIGNED        NOT NULL DEFAULT 0,
        qi_threshold    INT UNSIGNED        NOT NULL DEFAULT 100,

        affinity ENUM('fire','water','lightning','wood','earth')
            NULL DEFAULT NULL,

        last_updated    DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

        in_tribulation          BOOLEAN     NOT NULL DEFAULT FALSE,
        tribulation_started_at  DATETIME    DEFAULT NULL,
        breakthrough_cooldown   DATETIME    DEFAULT NULL,

        closed_cult_until       DATETIME    DEFAULT NULL,
        stabilise_used          BOOLEAN     NOT NULL DEFAULT FALSE,

        reputation      SMALLINT            NOT NULL DEFAULT 0,
        total_wins      SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
        total_losses    SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
        fled_challenges SMALLINT UNSIGNED   NOT NULL DEFAULT 0,

        ward_until              DATETIME    DEFAULT NULL,
        crippled_until          DATETIME    DEFAULT NULL,

        foundation_bonus        SMALLINT UNSIGNED NOT NULL DEFAULT 0

    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 2. Breakthrough history log
    """
    CREATE TABLE IF NOT EXISTS breakthrough_log (
        id              BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT PRIMARY KEY,
        discord_id      BIGINT UNSIGNED     NOT NULL,
        realm           VARCHAR(30)         NOT NULL,
        stage           TINYINT UNSIGNED    NOT NULL,
        outcome         ENUM('success','minor_fail','major_fail') NOT NULL,
        qi_lost         INT UNSIGNED        NOT NULL DEFAULT 0,
        overflow        BOOLEAN             NOT NULL DEFAULT FALSE,
        attempted_at    DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 3. Cooldowns table
    """
    CREATE TABLE IF NOT EXISTS cooldowns (
        discord_id      BIGINT UNSIGNED     NOT NULL,
        command         VARCHAR(50)         NOT NULL,
        expires_at      DATETIME            NOT NULL,
        PRIMARY KEY (discord_id, command),
        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 4. Rivals / combat log
    """
    CREATE TABLE IF NOT EXISTS rivals (
        id              BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT PRIMARY KEY,
        challenger_id   BIGINT UNSIGNED     NOT NULL,
        target_id       BIGINT UNSIGNED     NOT NULL,
        fight_type      ENUM('spar','challenge','duel','ambush') NOT NULL,
        outcome         ENUM('challenger_win','target_win') NOT NULL,
        qi_transferred  INT UNSIGNED        NOT NULL DEFAULT 0,
        vendetta_active BOOLEAN             NOT NULL DEFAULT FALSE,
        fought_at       DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (challenger_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE,
        FOREIGN KEY (target_id)    REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 5. Pending Dao Challenges
    """
    CREATE TABLE IF NOT EXISTS pending_challenges (
        challenger_id   BIGINT UNSIGNED     NOT NULL,
        target_id       BIGINT UNSIGNED     NOT NULL,
        issued_at       DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at      DATETIME            NOT NULL,
        accepted        BOOLEAN             NOT NULL DEFAULT FALSE,
        PRIMARY KEY (challenger_id, target_id),
        FOREIGN KEY (challenger_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE,
        FOREIGN KEY (target_id)    REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 6. Pending Life-and-Death Duels
    """
    CREATE TABLE IF NOT EXISTS pending_duels (
        challenger_id   BIGINT UNSIGNED     NOT NULL,
        target_id       BIGINT UNSIGNED     NOT NULL,
        requested_at    DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at      DATETIME            NOT NULL,
        accepted        BOOLEAN             NOT NULL DEFAULT FALSE,
        PRIMARY KEY (challenger_id, target_id),
        FOREIGN KEY (challenger_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE,
        FOREIGN KEY (target_id)    REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 7. Add last_updated for installs that pre-date the column.
    _migration_7,

    # 8. Ensure affinity column has the correct definition (idempotent).
    """
    ALTER TABLE cultivators
        MODIFY COLUMN affinity ENUM('fire','water','lightning','wood','earth')
            NULL DEFAULT NULL
    """,

    # 9. Backfill last_updated for rows inserted before migration 7.
    """
    UPDATE cultivators
    SET last_updated = registered_at
    WHERE last_updated = '2000-01-01 00:00:00'
       OR last_updated IS NULL
    """,

    # 10. FIX #10: removed the unconditional Water→NULL wipe that would
    #     destroy legitimate Water affinity choices.  If you still need to
    #     reset the old hardcoded default, add a date guard here, e.g.:
    #
    #   UPDATE cultivators SET affinity = NULL
    #   WHERE affinity = 'water'
    #     AND registered_at < '<date you deployed affinity choice>'
    #
    # This migration is intentionally a no-op so the numbering stays stable.
    "SELECT 1  /* migration 10 — intentional no-op, see comment above */",

    # =========================================================================
    #  TALENT SYSTEM TABLES  (migrations 11–18)
    # =========================================================================

    # 11. Active talent per player (one row per player)
    """
    CREATE TABLE IF NOT EXISTS player_talents (
        discord_id        BIGINT UNSIGNED     NOT NULL PRIMARY KEY,
        guild_id          BIGINT UNSIGNED     NOT NULL,

        talent_name       VARCHAR(100)        NOT NULL,
        talent_rarity     ENUM(
                            'Trash','Common','Rare','Elite',
                            'Heavenly','Mythical','Divine'
                          )                   NOT NULL,
        talent_multiplier FLOAT               NOT NULL DEFAULT 1.0,
        evolution_stage   TINYINT UNSIGNED    NOT NULL DEFAULT 0,
        is_corrupted      BOOLEAN             NOT NULL DEFAULT FALSE,
        is_locked         BOOLEAN             NOT NULL DEFAULT FALSE,

        tags              TEXT                NOT NULL DEFAULT ('[]'),

        acquired_at       DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_updated      DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 12. Talent inventory (multiple talents per player)
    """
    CREATE TABLE IF NOT EXISTS talent_inventory (
        id                BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT PRIMARY KEY,
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL,

        talent_name       VARCHAR(100)        NOT NULL,
        talent_rarity     ENUM(
                            'Trash','Common','Rare','Elite',
                            'Heavenly','Mythical','Divine'
                          )                   NOT NULL,
        talent_multiplier FLOAT               NOT NULL DEFAULT 1.0,
        evolution_stage   TINYINT UNSIGNED    NOT NULL DEFAULT 0,
        is_corrupted      BOOLEAN             NOT NULL DEFAULT FALSE,
        is_locked         BOOLEAN             NOT NULL DEFAULT FALSE,

        tags              TEXT                NOT NULL DEFAULT ('[]'),

        acquired_at       DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 13. Spin pity counters — FIX #11: added guild_id for per-server pity
    """
    CREATE TABLE IF NOT EXISTS talent_spin_pity (
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL DEFAULT 0,
        pity_elite        SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
        pity_heavenly     SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
        pity_mythical     SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
        total_spins       INT UNSIGNED        NOT NULL DEFAULT 0,

        PRIMARY KEY (discord_id, guild_id),
        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 14. Fusion pity counters — FIX #11: added guild_id for per-server pity
    """
    CREATE TABLE IF NOT EXISTS talent_fusion_pity (
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL DEFAULT 0,
        fusion_pity       SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
        total_fusions     INT UNSIGNED        NOT NULL DEFAULT 0,

        PRIMARY KEY (discord_id, guild_id),
        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 15. Immutable spin audit log
    """
    CREATE TABLE IF NOT EXISTS talent_spin_log (
        id                BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT PRIMARY KEY,
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL,
        talent_name       VARCHAR(100)        NOT NULL,
        talent_rarity     ENUM(
                            'Trash','Common','Rare','Elite',
                            'Heavenly','Mythical','Divine'
                          )                   NOT NULL,
        pity_triggered    BOOLEAN             NOT NULL DEFAULT FALSE,
        accepted          BOOLEAN             NOT NULL DEFAULT FALSE,
        spun_at           DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 16. Immutable fusion audit log — FIX #12: added 'auto' to mode ENUM
    """
    CREATE TABLE IF NOT EXISTS talent_fusion_log (
        id                BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT PRIMARY KEY,
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL,
        talent_a          VARCHAR(100)        NOT NULL,
        talent_b          VARCHAR(100)        NOT NULL,
        mode              ENUM('auto','same','cross','rng') NOT NULL,
        success           BOOLEAN             NOT NULL,
        result_name       VARCHAR(100)        DEFAULT NULL,
        failure_outcome   ENUM('backfire','corruption','mutation','catastrophic') DEFAULT NULL,
        fused_at          DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 17. One-per-server claimed legendary talents
    """
    CREATE TABLE IF NOT EXISTS server_claimed_talents (
        guild_id          BIGINT UNSIGNED     NOT NULL,
        discord_id        BIGINT UNSIGNED     NOT NULL,
        talent_name       VARCHAR(100)        NOT NULL,
        claimed_at        DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

        PRIMARY KEY (guild_id, talent_name),
        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 18. FIX #13: spin tokens table (was entirely missing from schema)
    """
    CREATE TABLE IF NOT EXISTS spin_tokens (
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL,
        tokens            SMALLINT UNSIGNED   NOT NULL DEFAULT 0,

        PRIMARY KEY (discord_id, guild_id),
        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
]


async def run_migrations() -> None:
    total = len(MIGRATIONS)
    for i, migration in enumerate(MIGRATIONS, start=1):
        try:
            if callable(migration):
                await migration(total)
            else:
                await execute(migration)
                log.debug("Migration %d/%d OK", i, total)
        except Exception:
            log.exception(
                "Migration %d/%d FAILED — query:\n%s",
                i,
                total,
                migration.strip() if isinstance(migration, str) else repr(migration),
            )
            raise

    log.info("Migrations  » %d statement(s) processed", total)


async def _run_standalone() -> None:
    await connect()
    try:
        await run_migrations()
    finally:
        await disconnect()


if __name__ == "__main__":
    asyncio.run(_run_standalone())