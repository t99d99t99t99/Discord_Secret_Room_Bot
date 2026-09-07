"""Procedural bootstrap steps kept separate from declarative schema modules."""

from __future__ import annotations

from .incremental_schema import INCREMENTAL_GAME_SCHEMA
from .schema_bootstrap import initialize_base_schema


async def execute_all(conn, statements) -> None:
    """Execute statements in dependency order."""
    for statement in statements:
        await conn.execute(statement)


async def initialize_database(conn) -> None:
    """Run declarative setup followed by the ordered data/schema upgrades."""
    await initialize_base_schema(conn)
    await run_legacy_schema_migrations(conn)
    await initialize_incremental_schema(conn)


async def run_legacy_schema_migrations(conn) -> None:
    """Apply the small, versioned repairs for the pre-custom-setup tables."""
    applied = {
        row["version"]
        for row in await conn.fetch("SELECT version FROM sc_migrations")
    }

    if 1 not in applied:
        rows = await conn.fetch("SELECT id, width, height, arr FROM sc_factories")
        for row in rows:
            arr, width, height = row["arr"], row["width"], row["height"]
            if width == height or len(arr) != width or any(
                len(column) != height for column in arr
            ):
                continue
            transposed = [
                [arr[x][y] for x in range(width)] for y in range(height)
            ]
            await conn.execute(
                "UPDATE sc_factories SET arr = $1 WHERE id = $2",
                transposed,
                row["id"],
            )
        await conn.execute("INSERT INTO sc_migrations(version) VALUES(1)")

    if 2 not in applied:
        users = await conn.fetch("SELECT discord_id FROM sc_users")
        for user in users:
            for slot in range(3):
                await conn.execute(
                    "INSERT INTO sc_items(discord_id, slot, item) VALUES($1, $2, $3) "
                    "ON CONFLICT (discord_id, slot) DO NOTHING",
                    user["discord_id"],
                    slot,
                    {"type": "Empty", "rank": 0},
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


async def initialize_incremental_schema(conn) -> None:
    """Create and upgrade the isolated Secretae Incremental data model."""
    await execute_all(conn, INCREMENTAL_GAME_SCHEMA)
    await conn.execute(
        "ALTER TABLE si_migration_reports "
        "ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ"
    )
    await conn.execute(
        "ALTER TABLE si_kyohoon_rewards "
        "ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )
    await conn.execute(
        "ALTER TABLE si_players "
        "ADD COLUMN IF NOT EXISTS last_concentrated_week_start DATE"
    )
    await conn.execute(
        "ALTER TABLE si_players "
        "ADD COLUMN IF NOT EXISTS last_game_command_game_date DATE"
    )
    await conn.execute(
        "ALTER TABLE si_relay_rewards "
        "ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
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
    await conn.execute(
        "INSERT INTO si_relay_turn_state(singleton) VALUES(TRUE) "
        "ON CONFLICT(singleton) DO NOTHING"
    )
    await conn.execute(
        "INSERT INTO si_world_state(singleton,total_essence,highest_essence) "
        "VALUES(TRUE,$1,$1) ON CONFLICT(singleton) DO NOTHING",
        {"sign": 0, "layer": "0", "mag": "0"},
    )
