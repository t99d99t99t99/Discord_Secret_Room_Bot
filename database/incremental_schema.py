"""Schema statements for the isolated Secretae Incremental data model."""

INCREMENTAL_GAME_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS si_players (
        discord_id BIGINT PRIMARY KEY,
        shards JSONB NOT NULL CHECK(jsonb_typeof(shards) = 'object'),
        essence JSONB NOT NULL CHECK(jsonb_typeof(essence) = 'object'),
        last_produced_game_date DATE,
        last_concentrated_week_start DATE,
        last_game_command_game_date DATE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_secrets (
        discord_id BIGINT PRIMARY KEY
            REFERENCES si_players(discord_id) ON DELETE CASCADE,
        amounts JSONB NOT NULL CHECK(jsonb_typeof(amounts) = 'object')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_organics (
        discord_id BIGINT PRIMARY KEY
            REFERENCES si_players(discord_id) ON DELETE CASCADE,
        amounts JSONB NOT NULL CHECK(jsonb_typeof(amounts) = 'object')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_world_state (
        singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
        total_essence JSONB NOT NULL CHECK(jsonb_typeof(total_essence) = 'object'),
        highest_essence JSONB NOT NULL CHECK(jsonb_typeof(highest_essence) = 'object'),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_migrations (
        migration_key TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        details JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_migration_reports (
        discord_id BIGINT PRIMARY KEY,
        migration_key TEXT NOT NULL,
        report JSONB NOT NULL,
        migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_migration_snapshots (
        migration_key TEXT PRIMARY KEY,
        snapshot JSONB NOT NULL,
        checksum TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_tomak_rewards (
        submission_message_id BIGINT PRIMARY KEY,
        discord_id BIGINT NOT NULL,
        source_thread_id BIGINT NOT NULL,
        posted_thread_id BIGINT NOT NULL,
        posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        details JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_kyohoon_rewards (
        submission_message_id BIGINT PRIMARY KEY,
        discord_id BIGINT NOT NULL,
        source_thread_id BIGINT NOT NULL,
        posted_thread_id BIGINT NOT NULL,
        posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        reward_amount JSONB NOT NULL CHECK(jsonb_typeof(reward_amount) = 'object'),
        details JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_relay_rewards (
        message_id BIGINT PRIMARY KEY,
        discord_id BIGINT NOT NULL,
        awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_relay_turn_state (
        singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
        last_rewarded_discord_id BIGINT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS si_alert_settings (
        discord_id BIGINT PRIMARY KEY
            REFERENCES si_players(discord_id) ON DELETE CASCADE,
        alert_type TEXT NOT NULL DEFAULT 'disabled'
            CHECK(alert_type IN ('disabled', 'concentration', 'game')),
        alert_hour SMALLINT CHECK(alert_hour BETWEEN 0 AND 23),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)
