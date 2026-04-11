from __future__ import annotations

import asyncio
import logging

from db.database import connect, disconnect, execute, fetch_one

log = logging.getLogger("bot.migrations")


async def _migration_7(total: int) -> None:
    """
    Add last_updated column for installs that ran migrations 1-6 before
    this column was introduced.
    """
    row = await fetch_one(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'cultivators'
          AND COLUMN_NAME  = 'last_updated'
        """,
        (),
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


async def _migration_19(total: int) -> None:
    """
    Create spirit_roots table if it does not already exist.
    """
    row = await fetch_one(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'spirit_roots'
        """,
        (),
    )
    if row and row["cnt"]:
        log.debug("Migration 19/%d SKIP — spirit_roots already exists", total)
        return

    await execute(
        """
        CREATE TABLE spirit_roots (
            discord_id      BIGINT UNSIGNED     NOT NULL,
            guild_id        BIGINT UNSIGNED     NOT NULL,

            current_value   TINYINT UNSIGNED    NOT NULL DEFAULT 1,
            best_value      TINYINT UNSIGNED    NOT NULL DEFAULT 1,
            pity_counter    SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
            total_spins     INT UNSIGNED        NOT NULL DEFAULT 0,

            acquired_at     DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_spin_at    DATETIME            DEFAULT NULL,

            PRIMARY KEY (discord_id, guild_id),
            FOREIGN KEY (discord_id)
                REFERENCES cultivators(discord_id) ON DELETE CASCADE,

            CONSTRAINT chk_sr_current CHECK (current_value BETWEEN 1 AND 5),
            CONSTRAINT chk_sr_best    CHECK (best_value    BETWEEN 1 AND 5)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """
    )
    log.debug("Migration 19/%d OK — spirit_roots table created", total)


async def _migration_20(total: int) -> None:
    """
    Create spirit_root_spin_log table if it does not already exist.
    """
    row = await fetch_one(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'spirit_root_spin_log'
        """,
        (),
    )
    if row and row["cnt"]:
        log.debug("Migration 20/%d SKIP — spirit_root_spin_log already exists", total)
        return

    await execute(
        """
        CREATE TABLE spirit_root_spin_log (
            id              BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT PRIMARY KEY,
            discord_id      BIGINT UNSIGNED     NOT NULL,
            guild_id        BIGINT UNSIGNED     NOT NULL,

            rolled_value    TINYINT UNSIGNED    NOT NULL,
            pity_triggered  BOOLEAN             NOT NULL DEFAULT FALSE,
            outcome         ENUM('improved','equal','protected') NOT NULL,
            spun_at         DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (discord_id)
                REFERENCES cultivators(discord_id) ON DELETE CASCADE,

            INDEX idx_spirit_log_player (discord_id, guild_id, spun_at)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """
    )
    log.debug("Migration 20/%d OK — spirit_root_spin_log table created", total)


async def _migration_21(total: int) -> None:
    """
    Fix breakthrough_log.outcome ENUM — remove old 'minor_fail'/'major_fail'
    values and normalise everything to 'success'/'fail'.

    Skip if the column is already clean (no 'minor_fail' in the ENUM definition).
    """
    row = await fetch_one(
        """
        SELECT COLUMN_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'breakthrough_log'
          AND COLUMN_NAME  = 'outcome'
        """,
        (),
    )

    if row is None:
        # Table doesn't exist yet — nothing to fix
        log.debug("Migration 21/%d SKIP — breakthrough_log not found", total)
        return

    column_type = (row.get("COLUMN_TYPE") or "").lower()

    # Only run if the old values are still present in the ENUM definition
    if "minor_fail" not in column_type and "major_fail" not in column_type:
        log.debug("Migration 21/%d SKIP — outcome ENUM already clean", total)
        return

    # Step 1: normalise any rows that still carry the old values
    await execute(
        """
        UPDATE breakthrough_log
        SET outcome = 'fail'
        WHERE outcome IN ('minor_fail', 'major_fail')
        """
    )

    # Step 2: shrink the ENUM now that no rows use the old values
    await execute(
        """
        ALTER TABLE breakthrough_log
        MODIFY COLUMN outcome ENUM('success','fail') NOT NULL
        """
    )

    log.debug("Migration 21/%d OK — breakthrough_log.outcome ENUM fixed", total)


async def _migration_22(total: int) -> None:
    """
    Ensure player_talents has a Cosmic rarity option.
    Some installs were created before Cosmic was added to the ENUM.
    Also covers talent_inventory and talent_spin_log.
    """
    for table in ("player_talents", "talent_inventory", "talent_spin_log"):
        row = await fetch_one(
            """
            SELECT COLUMN_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME   = %s
              AND COLUMN_NAME  = 'talent_rarity'
            """,
            (table,),
        )
        if row is None:
            continue
        column_type = (row.get("COLUMN_TYPE") or "").lower()
        if "cosmic" in column_type:
            log.debug("Migration 22/%d SKIP — %s.talent_rarity already has Cosmic", total, table)
            continue

        await execute(
            f"""
            ALTER TABLE {table}
            MODIFY COLUMN talent_rarity
                ENUM('Trash','Common','Rare','Elite','Heavenly','Mythical','Divine','Cosmic')
                NOT NULL
            """
        )
        log.debug("Migration 22/%d OK — added Cosmic to %s.talent_rarity", total, table)


MIGRATIONS: list[str | object] = [
    # =========================================================================
    #  CORE CULTIVATOR TABLES  (migrations 1–10)
    # =========================================================================

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
        outcome         ENUM('success','fail') NOT NULL,
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

    # 10. Intentional no-op.
    "SELECT 1  /* migration 10 — intentional no-op */",

    # =========================================================================
    #  TALENT SYSTEM TABLES  (migrations 11–18)
    # =========================================================================

    # 11. Active talent per player
    """
    CREATE TABLE IF NOT EXISTS player_talents (
        discord_id        BIGINT UNSIGNED     NOT NULL PRIMARY KEY,
        guild_id          BIGINT UNSIGNED     NOT NULL,

        talent_name       VARCHAR(100)        NOT NULL,
        talent_rarity     ENUM(
                            'Trash','Common','Rare','Elite',
                            'Heavenly','Mythical','Divine','Cosmic'
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

    # 12. Talent inventory
    """
    CREATE TABLE IF NOT EXISTS talent_inventory (
        id                BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT PRIMARY KEY,
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL,

        talent_name       VARCHAR(100)        NOT NULL,
        talent_rarity     ENUM(
                            'Trash','Common','Rare','Elite',
                            'Heavenly','Mythical','Divine','Cosmic'
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

    # 13. Spin pity counters
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

    # 14. Fusion pity counters
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

    # 15. Spin audit log
    """
    CREATE TABLE IF NOT EXISTS talent_spin_log (
        id                BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT PRIMARY KEY,
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL,
        talent_name       VARCHAR(100)        NOT NULL,
        talent_rarity     ENUM(
                            'Trash','Common','Rare','Elite',
                            'Heavenly','Mythical','Divine','Cosmic'
                          )                   NOT NULL,
        pity_triggered    BOOLEAN             NOT NULL DEFAULT FALSE,
        accepted          BOOLEAN             NOT NULL DEFAULT FALSE,
        spun_at           DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # 16. Fusion audit log
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

    # 18. Spin tokens table
    """
    CREATE TABLE IF NOT EXISTS spin_tokens (
        discord_id        BIGINT UNSIGNED     NOT NULL,
        guild_id          BIGINT UNSIGNED     NOT NULL,
        tokens            SMALLINT UNSIGNED   NOT NULL DEFAULT 0,

        PRIMARY KEY (discord_id, guild_id),
        FOREIGN KEY (discord_id) REFERENCES cultivators(discord_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,

    # =========================================================================
    #  SPIRIT ROOT SYSTEM TABLES  (migrations 19–20)
    # =========================================================================

    _migration_19,
    _migration_20,

    # =========================================================================
    #  BUG FIXES  (migrations 21–22)
    # =========================================================================

    # 21. Fix breakthrough_log.outcome ENUM mismatch.
    _migration_21,

    # 22. Add Cosmic rarity to talent ENUM columns on existing installs.
    _migration_22,
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