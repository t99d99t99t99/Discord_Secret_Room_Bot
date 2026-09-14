"""Database pool façade.

Schema setup is organized in :mod:`database.bootstrap`; this module deliberately
contains only connection configuration and the public pool factory used by the bot.
"""

from __future__ import annotations
import json
import asyncpg
from database.bootstrap import initialize_database


async def _init_connection(conn) -> None:
    """Register JSON codecs once for every pooled connection."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def init_pool(dsn: str) -> asyncpg.Pool:
    """Create the application pool and bring its database schema up to date."""
    # The per-connection callback installs JSON codecs before any schema code
    # reads or writes JSONB game and community state.
    pool = await asyncpg.create_pool(dsn, init=_init_connection)
    async with pool.acquire() as conn:
        await initialize_database(conn)
    return pool
