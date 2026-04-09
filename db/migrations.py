from __future__ import annotations

import asyncio
import logging

from db.database import connect, disconnect, execute

log = logging.getLogger("bot.migrations")

MIGRATIONS: list[str] = [
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

        -- Affinity
        affinity        ENUM('fire','water','lightning','wood','earth') DEFAULT NULL,

        -- Passive tick
        last_tick_at    DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,

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
]


async def run_migrations() -> None:
    for i, query in enumerate(MIGRATIONS, start=1):
        try:
            await execute(query)
            log.debug("Migration %d/%d OK", i, len(MIGRATIONS))
        except Exception:
            log.exception("Migration %d/%d FAILED — query:\n%s", i, len(MIGRATIONS), query.strip())
            raise

    log.info("Migrations  » %d statement(s) applied", len(MIGRATIONS))


async def _run_standalone() -> None:
    await connect()
    try:
        await run_migrations()
    finally:
        await disconnect()


if __name__ == "__main__":
    asyncio.run(_run_standalone())