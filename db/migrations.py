from __future__ import annotations

import asyncio
import logging

from db.database import connect, disconnect, execute, fetch_one

log = logging.getLogger("bot.migrations")

# ---------------------------------------------------------------------------
# Migration list
# Each entry is either:
#   - a plain SQL string  → executed directly
#   - a callable          → async def(total: int) -> None, handles its own logic
#
# The callable form is used for migrations that need conditional logic
# (e.g. "ADD COLUMN only if it doesn't already exist") because MySQL does
# not support ADD COLUMN IF NOT EXISTS — that syntax is MariaDB-only.
# ---------------------------------------------------------------------------

async def _migration_7(total: int) -> None:
    """
    Add last_updated column for installs that ran migrations 1-6
    before this column was introduced.

    MySQL does NOT support ADD COLUMN IF NOT EXISTS (that is MariaDB only).
    We guard the ALTER at the Python level instead.
    """
    row = await fetch_one(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'cultivators'
          AND COLUMN_NAME  = 'last_updated'
        """
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

        -- Cultivation progression
        realm           ENUM('mortal','qi_gathering','qi_condensation','qi_refining')
                            NOT NULL DEFAULT 'mortal',
        stage           TINYINT UNSIGNED    NOT NULL DEFAULT 1,
        qi              INT UNSIGNED        NOT NULL DEFAULT 0,
        qi_threshold    INT UNSIGNED        NOT NULL DEFAULT 100,

        -- Affinity (NULL = not yet chosen)
        affinity ENUM('fire','water','lightning','wood','earth')
            NULL DEFAULT NULL,

        -- Real-time Qi accrual: qi is the stored value at last_updated;
        -- current Qi is computed as min(qi + rate * elapsed, qi_threshold).
        last_updated    DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

        -- Breakthrough state
        in_tribulation          BOOLEAN     NOT NULL DEFAULT FALSE,
        tribulation_started_at  DATETIME    DEFAULT NULL,
        breakthrough_cooldown   DATETIME    DEFAULT NULL,

        -- Active buffs
        closed_cult_until       DATETIME    DEFAULT NULL,
        stabilise_used          BOOLEAN     NOT NULL DEFAULT FALSE,

        -- PvP stats
        reputation      SMALLINT            NOT NULL DEFAULT 0,
        total_wins      SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
        total_losses    SMALLINT UNSIGNED   NOT NULL DEFAULT 0,
        fled_challenges SMALLINT UNSIGNED   NOT NULL DEFAULT 0,

        -- PvP debuffs & defences
        ward_until              DATETIME    DEFAULT NULL,
        crippled_until          DATETIME    DEFAULT NULL,

        -- Life-and-Death Duel permanent bonus
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

    # 5. Pending Dao Challenges (survive bot restarts)
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
    #    Uses a Python-level guard because MySQL does not support
    #    ADD COLUMN IF NOT EXISTS (MariaDB-only syntax).
    _migration_7,

    # 8. Ensure affinity column has the correct definition.
    #    MODIFY COLUMN is idempotent — safe to re-run.
    """
    ALTER TABLE cultivators
        MODIFY COLUMN affinity ENUM('fire','water','lightning','wood','earth')
            NULL DEFAULT NULL
    """,

    # 9. Backfill last_updated for rows inserted before migration 7.
    #    Sets last_updated = registered_at so accrual is calculated from when
    #    the cultivator actually joined, not from epoch / NULL.
    """
    UPDATE cultivators
    SET last_updated = registered_at
    WHERE last_updated = '2000-01-01 00:00:00'
       OR last_updated IS NULL
    """,

    # 10. Backfill affinity: rows that still carry the old DEFAULT 'water'
    #     but never explicitly chose an affinity are reset to NULL so the
    #     /choose_affinity prompt will appear for them.
    #
    #     WARNING: any cultivator who legitimately chose Water before this
    #     migration runs will have their affinity wiped.  Narrow the WHERE
    #     clause with a date guard if that is a concern, e.g.:
    #       AND registered_at >= '<date you deployed the affinity system>'
    """
    UPDATE cultivators
    SET affinity = NULL
    WHERE affinity = 'water'
    """,
]


async def run_migrations() -> None:
    total = len(MIGRATIONS)
    for i, migration in enumerate(MIGRATIONS, start=1):
        try:
            if callable(migration):
                # Callable migrations handle their own logging / skipping
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