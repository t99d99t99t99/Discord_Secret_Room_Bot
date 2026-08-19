import json
import asyncpg

async def _init_connection(conn):
    await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
    await conn.set_type_codec('json',  encoder=json.dumps, decoder=json.loads, schema='pg_catalog')

async def init_pool(dsn: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn, init=_init_connection)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS kyohoon_submissions (
                id           SERIAL PRIMARY KEY,
                thread_id    BIGINT NOT NULL,
                user_id      BIGINT NOT NULL,
                message_id   BIGINT NOT NULL UNIQUE,
                submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                posted_at    TIMESTAMPTZ,
                reward_posted_thread_id BIGINT,
                rewarded_at  TIMESTAMPTZ,
                UNIQUE (thread_id, user_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS kyohoon_mgmt_messages (
                thread_id  BIGINT PRIMARY KEY,
                message_id BIGINT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tomak_submissions (
                id           SERIAL PRIMARY KEY,
                thread_id    BIGINT NOT NULL,
                user_id      BIGINT NOT NULL,
                message_id   BIGINT NOT NULL UNIQUE,
                submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                posted_at    TIMESTAMPTZ,
                reward_posted_thread_id BIGINT,
                rewarded_at  TIMESTAMPTZ
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tomak_mgmt_messages (
                thread_id  BIGINT PRIMARY KEY,
                message_id BIGINT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS submission_schedule_settings (
                schedule_name TEXT PRIMARY KEY,
                enabled       BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            INSERT INTO submission_schedule_settings(schedule_name, enabled)
            VALUES ('kyohoon', TRUE), ('tomak', TRUE)
            ON CONFLICT (schedule_name) DO NOTHING
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sc_users (
                discord_id         BIGINT PRIMARY KEY,
                last_produced_date DATE
            )
        """)
        await conn.execute("""
            ALTER TABLE sc_users
            ADD COLUMN IF NOT EXISTS last_item_used_date DATE,
            ADD COLUMN IF NOT EXISTS item_uses_today SMALLINT NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS story_essence_count INT NOT NULL DEFAULT 0
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sc_factories (
                id         SERIAL PRIMARY KEY,
                discord_id BIGINT NOT NULL REFERENCES sc_users(discord_id),
                slot       SMALLINT NOT NULL,
                width      SMALLINT NOT NULL,
                height     SMALLINT NOT NULL,
                arr        JSONB NOT NULL,
                UNIQUE (discord_id, slot)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sc_secrets (
                discord_id    BIGINT PRIMARY KEY REFERENCES sc_users(discord_id),
                color_secrets JSONB NOT NULL,
                shape_secrets JSONB NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sc_items (
                discord_id BIGINT NOT NULL REFERENCES sc_users(discord_id),
                slot       SMALLINT NOT NULL,
                item       JSONB NOT NULL,
                PRIMARY KEY (discord_id, slot)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sc_story_essence_rewards (
                message_id  BIGINT PRIMARY KEY,
                discord_id  BIGINT NOT NULL,
                awarded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sc_kyohoon_rewards (
                submission_message_id BIGINT PRIMARY KEY,
                discord_id            BIGINT NOT NULL,
                thread_id             BIGINT NOT NULL,
                awarded_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                reward_summary        JSONB NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sc_tomak_rewards (
                submission_message_id BIGINT PRIMARY KEY,
                discord_id            BIGINT NOT NULL,
                thread_id             BIGINT NOT NULL,
                awarded_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sc_migrations (
                version    INT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        applied = {row["version"] for row in await conn.fetch("SELECT version FROM sc_migrations")}

        if 1 not in applied:
            rows = await conn.fetch("SELECT id, width, height, arr FROM sc_factories")
            for row in rows:
                arr, width, height = row["arr"], row["width"], row["height"]
                if width == height or len(arr) != width or any(len(col) != height for col in arr):
                    continue
                transposed = [[arr[x][y] for x in range(width)] for y in range(height)]
                await conn.execute(
                    "UPDATE sc_factories SET arr = $1 WHERE id = $2",
                    transposed, row["id"],
                )
            await conn.execute("INSERT INTO sc_migrations(version) VALUES(1)")
        if 2 not in applied:
            users = await conn.fetch("SELECT discord_id FROM sc_users")
            for user in users:
                for slot in range(3):
                    await conn.execute(
                        "INSERT INTO sc_items(discord_id, slot, item) VALUES($1, $2, $3) "
                        "ON CONFLICT (discord_id, slot) DO NOTHING",
                        user["discord_id"], slot, {"type": "Empty", "rank": 0},
                    )
            await conn.execute("INSERT INTO sc_migrations(version) VALUES(2)")
        if 4 not in applied:
            for table in ("kyohoon_submissions", "tomak_submissions"):
                await conn.execute(
                    f"ALTER TABLE {table} "
                    "DROP COLUMN IF EXISTS publishing_at, "
                    "DROP COLUMN IF EXISTS publication_marker, "
                    "DROP COLUMN IF EXISTS publication_thread_id"
                )
            await conn.execute("INSERT INTO sc_migrations(version) VALUES(4)")
        # Secretae Incremental is deliberately isolated from the legacy sc_* tables.
        for statement in (
            "CREATE TABLE IF NOT EXISTS si_players (discord_id BIGINT PRIMARY KEY, shards JSONB NOT NULL CHECK(jsonb_typeof(shards)='object'), essence JSONB NOT NULL CHECK(jsonb_typeof(essence)='object'), last_produced_game_date DATE, last_concentrated_week_start DATE, last_game_command_game_date DATE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE TABLE IF NOT EXISTS si_secrets (discord_id BIGINT PRIMARY KEY REFERENCES si_players(discord_id) ON DELETE CASCADE, amounts JSONB NOT NULL CHECK(jsonb_typeof(amounts)='object'))",
            "CREATE TABLE IF NOT EXISTS si_organics (discord_id BIGINT PRIMARY KEY REFERENCES si_players(discord_id) ON DELETE CASCADE, amounts JSONB NOT NULL CHECK(jsonb_typeof(amounts)='object'))",
            "CREATE TABLE IF NOT EXISTS si_world_state (singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton), total_essence JSONB NOT NULL CHECK(jsonb_typeof(total_essence)='object'), highest_essence JSONB NOT NULL CHECK(jsonb_typeof(highest_essence)='object'), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE TABLE IF NOT EXISTS si_migrations (migration_key TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), details JSONB NOT NULL DEFAULT '{}'::jsonb)",
            "CREATE TABLE IF NOT EXISTS si_migration_reports (discord_id BIGINT PRIMARY KEY, migration_key TEXT NOT NULL, report JSONB NOT NULL, migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE TABLE IF NOT EXISTS si_migration_snapshots (migration_key TEXT PRIMARY KEY, snapshot JSONB NOT NULL, checksum TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE TABLE IF NOT EXISTS si_tomak_rewards (submission_message_id BIGINT PRIMARY KEY, discord_id BIGINT NOT NULL, source_thread_id BIGINT NOT NULL, posted_thread_id BIGINT NOT NULL, posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), details JSONB NOT NULL DEFAULT '{}'::jsonb)",
            "CREATE TABLE IF NOT EXISTS si_kyohoon_rewards (submission_message_id BIGINT PRIMARY KEY, discord_id BIGINT NOT NULL, source_thread_id BIGINT NOT NULL, posted_thread_id BIGINT NOT NULL, posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), reward_amount JSONB NOT NULL CHECK(jsonb_typeof(reward_amount)='object'), details JSONB NOT NULL DEFAULT '{}'::jsonb)",
            "CREATE TABLE IF NOT EXISTS si_relay_rewards (message_id BIGINT PRIMARY KEY, discord_id BIGINT NOT NULL, awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE TABLE IF NOT EXISTS si_relay_turn_state (singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton), last_rewarded_discord_id BIGINT, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE TABLE IF NOT EXISTS si_alert_settings (discord_id BIGINT PRIMARY KEY REFERENCES si_players(discord_id) ON DELETE CASCADE, alert_type TEXT NOT NULL DEFAULT 'disabled' CHECK(alert_type IN ('disabled', 'concentration', 'game')), alert_hour SMALLINT CHECK(alert_hour BETWEEN 0 AND 23), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        ):
            await conn.execute(statement)
        await conn.execute("ALTER TABLE si_migration_reports ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ")
        await conn.execute(
            "ALTER TABLE si_kyohoon_rewards "
            "ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
        )
        await conn.execute("ALTER TABLE si_players ADD COLUMN IF NOT EXISTS last_concentrated_week_start DATE")
        await conn.execute("ALTER TABLE si_players ADD COLUMN IF NOT EXISTS last_game_command_game_date DATE")
        await conn.execute("ALTER TABLE si_relay_rewards ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb")
        for table in ("kyohoon_submissions", "tomak_submissions"):
            await conn.execute(
                f"ALTER TABLE {table} "
                "ADD COLUMN IF NOT EXISTS reward_posted_thread_id BIGINT, "
                "ADD COLUMN IF NOT EXISTS rewarded_at TIMESTAMPTZ"
            )
            await conn.execute(
                f"UPDATE {table} SET rewarded_at=posted_at "
                "WHERE posted_at IS NOT NULL AND reward_posted_thread_id IS NULL "
                "AND rewarded_at IS NULL"
            )
        await conn.execute("INSERT INTO si_relay_turn_state(singleton) VALUES(TRUE) ON CONFLICT(singleton) DO NOTHING")
        await conn.execute("INSERT INTO si_world_state(singleton,total_essence,highest_essence) VALUES(TRUE,$1,$1) ON CONFLICT(singleton) DO NOTHING", {"sign": 0, "layer": "0", "mag": "0"})
    return pool
