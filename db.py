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
            CREATE TABLE IF NOT EXISTS sc_users (
                discord_id         BIGINT PRIMARY KEY,
                last_produced_date DATE
            )
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
    return pool
