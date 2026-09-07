"""Legacy Secret Room game schema retained for backwards compatibility."""

from __future__ import annotations


async def initialize_legacy_schema(conn) -> None:
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

