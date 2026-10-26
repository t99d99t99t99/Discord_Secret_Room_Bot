"""Guild configuration, moderation, notice, and relay schema."""

from __future__ import annotations


async def initialize_community_schema(conn) -> None:
    """Create community-facing schema groups in dependency order."""
    await initialize_guild_game_schema(conn)
    await initialize_audit_and_rewards_schema(conn)
    await initialize_notice_schema(conn)
    await initialize_relay_schema(conn)
    await initialize_backup_schema(conn)


async def initialize_backup_schema(conn) -> None:
    """Create durable per-guild backup schedules and run idempotency records."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS backup_schedules (
            guild_id BIGINT PRIMARY KEY,
            cadence TEXT NOT NULL CHECK (cadence IN ('daily', 'weekly')),
            schedule_time TIME NOT NULL,
            schedule_weekday SMALLINT CHECK (schedule_weekday BETWEEN 0 AND 6),
            timezone TEXT NOT NULL DEFAULT 'UTC',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK ((cadence = 'weekly') = (schedule_weekday IS NOT NULL))
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS backup_jobs (
            guild_id BIGINT NOT NULL,
            scheduled_for TIMESTAMPTZ NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('running', 'completed', 'failed')),
            finished_at TIMESTAMPTZ,
            last_error TEXT,
            PRIMARY KEY (guild_id, scheduled_for)
        )
    """)


async def initialize_guild_game_schema(conn) -> None:
    """Create guild configuration and shared game state tables."""
    # Additive configuration foundation. Legacy tables remain intact while
    # existing servers complete their migration.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id BIGINT PRIMARY KEY,
            locale TEXT NOT NULL DEFAULT 'en' CHECK(locale IN ('en', 'ko')),
            admin_role_id BIGINT,
            audit_channel_id BIGINT,
            moderator_policy TEXT NOT NULL DEFAULT 'disabled'
                CHECK(moderator_policy IN ('disabled', 'manage_messages', 'role')),
            moderator_role_id BIGINT,
            game_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            setup_state TEXT NOT NULL DEFAULT 'not_started'
                CHECK(setup_state IN ('not_started', 'in_progress', 'complete')),
            config_version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK((moderator_policy = 'role') = (moderator_role_id IS NOT NULL))
        )
    """)
    await conn.execute(
        "ALTER TABLE guild_settings "
        "ADD COLUMN IF NOT EXISTS game_name TEXT NOT NULL DEFAULT 'Essence Foundry'"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS game_reward_ledger (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('notice_queue', 'relay_story')),
            source_id BIGINT NOT NULL,
            contribution_message_id BIGINT NOT NULL,
            recipient_id BIGINT NOT NULL,
            policy JSONB NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending', 'granted', 'failed', 'revoked')),
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            UNIQUE(guild_id, source_type, contribution_message_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS game_state_snapshots (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            player_id BIGINT NOT NULL,
            reward_ledger_id BIGINT UNIQUE REFERENCES game_reward_ledger(id) ON DELETE CASCADE,
            state_payload JSONB NOT NULL,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            pruned_at TIMESTAMPTZ
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_game_players (
            guild_id BIGINT NOT NULL,
            discord_id BIGINT NOT NULL,
            shards JSONB NOT NULL, essence JSONB NOT NULL,
            last_produced_game_date DATE, last_concentrated_week_start DATE,
            last_game_command_game_date DATE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(guild_id, discord_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_game_secrets (
            guild_id BIGINT NOT NULL,
            discord_id BIGINT NOT NULL,
            amounts JSONB NOT NULL,
            PRIMARY KEY(guild_id, discord_id),
            FOREIGN KEY(guild_id, discord_id)
                REFERENCES guild_game_players(guild_id, discord_id) ON DELETE CASCADE
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_game_organics (
            guild_id BIGINT NOT NULL,
            discord_id BIGINT NOT NULL,
            amounts JSONB NOT NULL,
            PRIMARY KEY(guild_id, discord_id),
            FOREIGN KEY(guild_id, discord_id)
                REFERENCES guild_game_players(guild_id, discord_id) ON DELETE CASCADE
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_game_world_state (
            guild_id BIGINT PRIMARY KEY,
            total_essence JSONB NOT NULL,
            highest_essence JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_game_alert_settings (
            guild_id BIGINT NOT NULL,
            discord_id BIGINT NOT NULL,
            alert_type TEXT NOT NULL DEFAULT 'disabled'
                CHECK(alert_type IN ('disabled', 'concentration', 'game')),
            alert_hour SMALLINT CHECK(alert_hour BETWEEN 0 AND 23),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(guild_id, discord_id),
            FOREIGN KEY(guild_id, discord_id)
                REFERENCES guild_game_players(guild_id, discord_id) ON DELETE CASCADE
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_game_relay_rewards (
            guild_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            discord_id BIGINT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(guild_id, message_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_game_relay_turn_state (
            guild_id BIGINT PRIMARY KEY,
            last_rewarded_discord_id BIGINT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS game_migration_evidence (
            migration_key TEXT PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            source_checksum TEXT,
            report JSONB NOT NULL DEFAULT '{}'::jsonb,
            migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


async def initialize_audit_and_rewards_schema(conn) -> None:
    """Create audit history and reward reconciliation tables."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            actor_id BIGINT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            before_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            after_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS admin_audit_log_guild_created_idx ON admin_audit_log(guild_id, created_at DESC)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS reward_reconciliation_cases (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('notice_queue', 'relay_story')),
            contribution_message_id BIGINT NOT NULL,
            reward_ledger_id BIGINT REFERENCES game_reward_ledger(id) ON DELETE SET NULL,
            reward_kind TEXT NOT NULL CHECK(reward_kind IN ('configured', 'legacy')),
            reason TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending', 'resolved', 'not_required')),
            resolution_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            UNIQUE(guild_id, source_type, contribution_message_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS reward_reconciliation_pending_idx ON reward_reconciliation_cases(guild_id, state, created_at DESC)"
    )


async def initialize_notice_schema(conn) -> None:
    """Create notice queue, submission, and scheduled job tables."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS notice_queues (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            queue_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT,
            state TEXT NOT NULL DEFAULT 'draft'
                CHECK(state IN ('draft', 'active', 'paused', 'attention_required', 'archived')),
            accepting_submissions BOOLEAN NOT NULL DEFAULT FALSE,
            submission_channel_id BIGINT NOT NULL,
            publication_channel_id BIGINT NOT NULL,
            active_submission_thread_id BIGINT,
            submission_title_template TEXT NOT NULL DEFAULT '{{queue_name}} submissions',
            submission_description_template TEXT NOT NULL DEFAULT 'Submit an entry for **{{queue_name}}** here.',
            submission_mention_role_id BIGINT,
            submission_mention_everyone BOOLEAN NOT NULL DEFAULT FALSE,
            new_thread_threshold INTEGER NOT NULL DEFAULT 1 CHECK(new_thread_threshold >= 0),
            cadence TEXT NOT NULL DEFAULT 'daily' CHECK(cadence IN ('daily', 'weekly', 'manual')),
            schedule_time TIME NOT NULL DEFAULT '09:00',
            schedule_weekday SMALLINT CHECK(schedule_weekday BETWEEN 0 AND 6),
            timezone TEXT NOT NULL DEFAULT 'UTC',
            per_member_limit INTEGER NOT NULL DEFAULT 1 CHECK(per_member_limit > 0),
            min_content_length INTEGER CHECK(min_content_length >= 0),
            max_content_length INTEGER DEFAULT 2000 CHECK(max_content_length >= min_content_length),
            selection_policy TEXT NOT NULL DEFAULT 'oldest'
                CHECK(selection_policy IN ('oldest', 'random', 'fair')),
            attribution_policy TEXT NOT NULL DEFAULT 'display_name'
                CHECK(attribution_policy IN ('display_name', 'none')),
            title_template TEXT NOT NULL DEFAULT '{{YYYY}}년 {{MM}}월 {{DD}}일 {{queue_name}}, {{author}}',
            reward_policy JSONB NOT NULL DEFAULT '{"type":"none"}'::jsonb,
            provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
            config_version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(guild_id, queue_key),
            CHECK(submission_channel_id <> publication_channel_id),
            CHECK((cadence <> 'weekly') OR schedule_weekday IS NOT NULL)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS notice_submissions (
            id BIGSERIAL PRIMARY KEY,
            queue_id BIGINT NOT NULL REFERENCES notice_queues(id) ON DELETE CASCADE,
            source_thread_id BIGINT NOT NULL,
            source_message_id BIGINT NOT NULL UNIQUE,
            author_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'eligible'
                CHECK(status IN ('eligible', 'selected', 'published', 'rejected', 'withdrawn', 'invalid', 'failed')),
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            moderated_at TIMESTAMPTZ,
            moderator_id BIGINT,
            moderation_reason TEXT,
            published_at TIMESTAMPTZ,
            publication_channel_id BIGINT,
            publication_message_id BIGINT,
            publication_thread_id BIGINT,
            reward_state TEXT NOT NULL DEFAULT 'not_applicable'
                CHECK(reward_state IN ('not_applicable', 'pending', 'granted', 'failed', 'revoked')),
            UNIQUE(queue_id, source_message_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS notice_submissions_select_idx ON notice_submissions(queue_id, status, submitted_at)"
    )
    for statement in (
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS min_content_length INTEGER CHECK(min_content_length >= 0)",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS max_content_length INTEGER CHECK(max_content_length >= min_content_length)",
        "ALTER TABLE notice_queues ALTER COLUMN max_content_length SET DEFAULT 2000",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS allow_attachments BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS allow_links BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS allow_edits BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS no_entry_action TEXT NOT NULL DEFAULT 'alert_and_reopen' CHECK(no_entry_action IN ('alert_and_reopen','alert_only','skip'))",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS submission_period_hours INTEGER CHECK(submission_period_hours > 0)",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS submission_opened_at TIMESTAMPTZ",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS content_template TEXT NOT NULL DEFAULT '{{content}}'",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS submission_title_template TEXT NOT NULL DEFAULT '{{queue_name}} submissions'",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS submission_description_template TEXT NOT NULL DEFAULT 'Submit an entry for **{{queue_name}}** here.'",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS submission_mention_role_id BIGINT",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS submission_mention_everyone BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE notice_queues ADD COLUMN IF NOT EXISTS new_thread_threshold INTEGER NOT NULL DEFAULT 1 CHECK(new_thread_threshold >= 0)",
        "ALTER TABLE notice_queues ALTER COLUMN title_template SET DEFAULT '{{YYYY}}년 {{MM}}월 {{DD}}일 {{queue_name}}, {{author}}'",
    ):
        await conn.execute(statement)
    # Migrate only the old built-in template. Custom titles belong to queue owners.
    await conn.execute("""
        UPDATE notice_queues
        SET title_template = CASE
            WHEN provenance->>'legacy_kind' = 'tomak'
                THEN '{{YYYY}}년 {{MM}}월 {{DD}}일 토막상식, {{author}}'
            WHEN provenance->>'legacy_kind' = 'kyohoon' OR cadence = 'weekly'
                THEN '{{MM}}월 {{week}}주차, {{author}}'
            ELSE '{{YYYY}}년 {{MM}}월 {{DD}}일 {{queue_name}}, {{author}}'
        END
        WHERE title_template = '{{queue_name}} — {{author}}'
    """)
    # Upgrade queues imported before submission-thread presentation was configurable.
    await conn.execute("""
        UPDATE notice_queues
        SET submission_title_template = CASE
                WHEN provenance->>'legacy_kind' = 'tomak' THEN '{{YYYY}}년 {{MM}}월 {{DD}}일 토막상식 신청'
                WHEN provenance->>'legacy_kind' = 'kyohoon' THEN '{{M}}월 {{week}}주차 교훈 신청'
                ELSE submission_title_template END,
            submission_description_template = CASE
                WHEN provenance->>'legacy_kind' = 'tomak' THEN '토막상식 신청하세요!!!!!!!!!!!!!!'
                WHEN provenance->>'legacy_kind' = 'kyohoon' THEN '교훈 신청하세요!!!!!!!!!!'
                ELSE submission_description_template END,
            submission_mention_everyone = CASE WHEN provenance->>'legacy_kind' IN ('tomak','kyohoon') THEN TRUE ELSE submission_mention_everyone END,
            new_thread_threshold = CASE
                WHEN provenance->>'legacy_kind' = 'tomak' AND new_thread_threshold = 1 THEN 5
                ELSE new_thread_threshold END
        WHERE submission_title_template = '{{queue_name}} submissions'
           OR submission_description_template = 'Submit an entry for **{{queue_name}}** here.'
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS notice_jobs (
            id BIGSERIAL PRIMARY KEY,
            queue_id BIGINT NOT NULL REFERENCES notice_queues(id) ON DELETE CASCADE,
            job_type TEXT NOT NULL CHECK(job_type IN ('publish', 'open_submissions')),
            scheduled_for TIMESTAMPTZ NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending', 'running', 'completed', 'failed')),
            lease_expires_at TIMESTAMPTZ,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            completed_at TIMESTAMPTZ,
            UNIQUE(queue_id, job_type, scheduled_for)
        )
    """)


async def initialize_relay_schema(conn) -> None:
    """Create relay story and contribution tables."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_stories (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            story_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            channel_id BIGINT NOT NULL,
            state TEXT NOT NULL DEFAULT 'paused'
                CHECK(state IN ('active', 'paused', 'attention_required', 'archived')),
            rules_text TEXT,
            posting_rules JSONB NOT NULL DEFAULT '{"require_intervening_contributor":true}'::jsonb,
            reward_policy JSONB NOT NULL DEFAULT '{"type":"none"}'::jsonb,
            provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
            config_version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(guild_id, story_key),
            UNIQUE(guild_id, channel_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_contributions (
            id BIGSERIAL PRIMARY KEY,
            story_id BIGINT NOT NULL REFERENCES relay_stories(id) ON DELETE CASCADE,
            message_id BIGINT NOT NULL UNIQUE,
            author_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'eligible'
                CHECK(status IN ('eligible', 'disqualified', 'withdrawn', 'reward_failed')),
            contributed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            moderated_at TIMESTAMPTZ,
            moderator_id BIGINT,
            moderation_reason TEXT,
            reward_state TEXT NOT NULL DEFAULT 'not_applicable'
                CHECK(reward_state IN ('not_applicable', 'pending', 'granted', 'failed', 'revoked')),
            rewarded_at TIMESTAMPTZ,
            UNIQUE(story_id, message_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS relay_contributions_turn_idx ON relay_contributions(story_id, status, contributed_at DESC)"
    )
    await conn.execute(
        "ALTER TABLE relay_stories ADD COLUMN IF NOT EXISTS moderator_policy TEXT"
    )
    await conn.execute(
        "ALTER TABLE relay_stories ADD COLUMN IF NOT EXISTS moderator_role_id BIGINT"
    )
