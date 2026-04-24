import asyncpg

async def init_pool(dsn: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn)
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
    return pool
