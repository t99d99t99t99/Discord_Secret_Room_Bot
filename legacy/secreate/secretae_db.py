import random
from collections import defaultdict

from .constants import (
    COLOR_NAMES,
    FACTORY_COUNT,
    FACTORY_HEIGHT,
    FACTORY_WIDTH,
    INITIAL_CAPITAL,
    ITEM_COUNT,
    ITEM_USES_PER_DAY,
    KYOHOON_EXTRA_PRODUCE_PER_FACTORY,
    SHAPE_NAMES,
)
from .game import calculate_production


def _random_arr(width: int, height: int) -> list:
    return [
        [{"color": random.randint(0, 7), "shape": random.randint(0, 2)} for _ in range(width)]
        for _ in range(height)
    ]


def _default_color_secrets() -> dict:
    return {name: INITIAL_CAPITAL for name in COLOR_NAMES}


def _default_shape_secrets() -> dict:
    return {name: INITIAL_CAPITAL for name in SHAPE_NAMES}


def _default_item() -> dict:
    return {"type": "Empty", "rank": 0}


async def get_user(pool, discord_id: int):
    return await pool.fetchrow("SELECT * FROM sc_users WHERE discord_id = $1", discord_id)


async def _ensure_game_user_conn(conn, discord_id: int) -> bool:
    inserted = await conn.fetchval(
        "INSERT INTO sc_users(discord_id) VALUES($1) "
        "ON CONFLICT (discord_id) DO NOTHING RETURNING 1",
        discord_id,
    )
    created = inserted == 1
    for slot in range(FACTORY_COUNT):
        await conn.execute(
            "INSERT INTO sc_factories(discord_id, slot, width, height, arr) "
            "VALUES($1, $2, $3, $4, $5) "
            "ON CONFLICT (discord_id, slot) DO NOTHING",
            discord_id, slot, FACTORY_WIDTH, FACTORY_HEIGHT,
            _random_arr(FACTORY_WIDTH, FACTORY_HEIGHT),
        )
    await conn.execute(
        "INSERT INTO sc_secrets(discord_id, color_secrets, shape_secrets) "
        "VALUES($1, $2, $3) ON CONFLICT (discord_id) DO NOTHING",
        discord_id, _default_color_secrets(), _default_shape_secrets(),
    )
    for slot in range(ITEM_COUNT):
        await conn.execute(
            "INSERT INTO sc_items(discord_id, slot, item) VALUES($1, $2, $3) "
            "ON CONFLICT (discord_id, slot) DO NOTHING",
            discord_id, slot, _default_item(),
        )
    return created


async def ensure_game_user(pool, discord_id: int) -> bool:
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await _ensure_game_user_conn(conn, discord_id)


async def create_user(pool, discord_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            created = await _ensure_game_user_conn(conn, discord_id)
            if not created:
                raise ValueError("이미 등록된 유저입니다")


async def get_factories(pool, discord_id: int):
    return await pool.fetch(
        "SELECT * FROM sc_factories WHERE discord_id = $1 ORDER BY slot",
        discord_id,
    )


async def get_items(pool, discord_id: int):
    rows = await pool.fetch(
        "SELECT slot, item FROM sc_items WHERE discord_id = $1 ORDER BY slot",
        discord_id,
    )
    items = {row["slot"]: row["item"] for row in rows}
    missing = [slot for slot in range(ITEM_COUNT) if slot not in items]
    if missing:
        for slot in missing:
            await pool.execute(
                "INSERT INTO sc_items(discord_id, slot, item) VALUES($1, $2, $3) "
                "ON CONFLICT (discord_id, slot) DO NOTHING",
                discord_id, slot, _default_item(),
            )
            items[slot] = _default_item()
    return [items[slot] for slot in range(ITEM_COUNT)]


async def save_item(pool, discord_id: int, slot: int, item: dict) -> None:
    await pool.execute(
        "UPDATE sc_items SET item = $1 WHERE discord_id = $2 AND slot = $3",
        item, discord_id, slot,
    )


async def save_factory_arr(pool, factory_id: int, arr: list) -> None:
    await pool.execute("UPDATE sc_factories SET arr = $1 WHERE id = $2", arr, factory_id)


async def get_secrets(pool, discord_id: int):
    return await pool.fetchrow(
        "SELECT * FROM sc_secrets WHERE discord_id = $1",
        discord_id,
    )


async def save_secrets(pool, discord_id: int, color_secrets: dict, shape_secrets: dict) -> None:
    await pool.execute(
        "UPDATE sc_secrets SET color_secrets = $1, shape_secrets = $2 WHERE discord_id = $3",
        color_secrets, shape_secrets, discord_id,
    )


async def update_last_produced(pool, discord_id: int, date) -> None:
    await pool.execute(
        "UPDATE sc_users SET last_produced_date = $1 WHERE discord_id = $2",
        date, discord_id,
    )


def item_uses_left(user, date) -> int:
    if user["last_item_used_date"] != date:
        return ITEM_USES_PER_DAY
    return max(0, ITEM_USES_PER_DAY - user["item_uses_today"])


async def record_item_use(pool, discord_id: int, date) -> None:
    await pool.execute(
        "UPDATE sc_users SET "
        "last_item_used_date = $2, "
        "item_uses_today = CASE WHEN last_item_used_date = $2 THEN item_uses_today + 1 ELSE 1 END "
        "WHERE discord_id = $1",
        discord_id, date,
    )


async def grant_story_essence(pool, discord_id: int, message_id: int) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            created_user = await _ensure_game_user_conn(conn, discord_id)
            inserted = await conn.fetchval(
                "INSERT INTO sc_story_essence_rewards(message_id, discord_id) "
                "VALUES($1, $2) ON CONFLICT (message_id) DO NOTHING RETURNING 1",
                message_id, discord_id,
            )
            if inserted != 1:
                return {"awarded": False, "created_user": created_user}
            count = await conn.fetchval(
                "UPDATE sc_users SET story_essence_count = story_essence_count + 1 "
                "WHERE discord_id = $1 RETURNING story_essence_count",
                discord_id,
            )
            return {"awarded": True, "created_user": created_user, "story_essence_count": count}


async def grant_tomak_post_reward(pool, discord_id: int, thread_id: int, message_id: int) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            created_user = await _ensure_game_user_conn(conn, discord_id)
            inserted = await conn.fetchval(
                "INSERT INTO sc_tomak_rewards(submission_message_id, discord_id, thread_id) "
                "VALUES($1, $2, $3) ON CONFLICT (submission_message_id) DO NOTHING RETURNING 1",
                message_id, discord_id, thread_id,
            )
            if inserted != 1:
                return {"awarded": False, "created_user": created_user}
            count = await conn.fetchval(
                "UPDATE sc_users SET story_essence_count = story_essence_count + 1 "
                "WHERE discord_id = $1 RETURNING story_essence_count",
                discord_id,
            )
            return {"awarded": True, "created_user": created_user, "story_essence_count": count}


async def consume_story_essence(pool, discord_id: int) -> bool:
    result = await pool.fetchval(
        "UPDATE sc_users SET story_essence_count = story_essence_count - 1 "
        "WHERE discord_id = $1 AND story_essence_count > 0 RETURNING 1",
        discord_id,
    )
    return result == 1


async def produce_all_factories(pool, discord_id: int, date, use_story_essence: bool = False) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT * FROM sc_users WHERE discord_id = $1 FOR UPDATE", discord_id)
            if user is None:
                return {"ok": False, "reason": "not_registered"}
            already_produced = user["last_produced_date"] == date
            if already_produced:
                if not use_story_essence:
                    return {"ok": False, "reason": "already_produced"}
                if user["story_essence_count"] <= 0:
                    return {"ok": False, "reason": "no_story_essence"}

            factories = await conn.fetch(
                "SELECT * FROM sc_factories WHERE discord_id = $1 ORDER BY slot FOR UPDATE",
                discord_id,
            )
            if not factories:
                return {"ok": False, "reason": "factory_not_found"}

            secrets = await conn.fetchrow("SELECT * FROM sc_secrets WHERE discord_id = $1 FOR UPDATE", discord_id)
            color_secrets = dict(secrets["color_secrets"])
            shape_secrets = dict(secrets["shape_secrets"])
            total_color = defaultdict(int)
            total_shape = defaultdict(int)
            factory_summaries = []

            for factory in factories:
                color_prod, shape_prod = calculate_production(
                    factory["arr"], factory["width"], factory["height"],
                )
                color_sum = 0
                shape_sum = 0
                for i, name in enumerate(COLOR_NAMES):
                    gain = color_prod.get(i, 0)
                    color_secrets[name] += gain
                    total_color[i] += gain
                    color_sum += gain
                for i, name in enumerate(SHAPE_NAMES):
                    gain = shape_prod.get(i, 0)
                    shape_secrets[name] += gain
                    total_shape[i] += gain
                    shape_sum += gain
                factory_summaries.append({
                    "slot": factory["slot"],
                    "color": color_sum,
                    "shape": shape_sum,
                })

            await conn.execute(
                "UPDATE sc_secrets SET color_secrets = $1, shape_secrets = $2 WHERE discord_id = $3",
                color_secrets, shape_secrets, discord_id,
            )
            if use_story_essence and already_produced:
                await conn.execute(
                    "UPDATE sc_users SET story_essence_count = story_essence_count - 1 WHERE discord_id = $1",
                    discord_id,
                )
            else:
                await conn.execute(
                    "UPDATE sc_users SET last_produced_date = $2 WHERE discord_id = $1",
                    discord_id, date,
                )

            return {
                "ok": True,
                "color_prod": dict(total_color),
                "shape_prod": dict(total_shape),
                "color_secrets": color_secrets,
                "shape_secrets": shape_secrets,
                "factories": factory_summaries,
                "used_story_essence": use_story_essence and already_produced,
            }


async def grant_kyohoon_submission_reward(pool, discord_id: int, thread_id: int, message_id: int) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            created_user = await _ensure_game_user_conn(conn, discord_id)
            exists = await conn.fetchval(
                "SELECT 1 FROM sc_kyohoon_rewards WHERE submission_message_id = $1",
                message_id,
            )
            if exists:
                return {"awarded": False, "created_user": created_user}

            factories = await conn.fetch(
                "SELECT * FROM sc_factories WHERE discord_id = $1 ORDER BY slot FOR UPDATE",
                discord_id,
            )
            secrets = await conn.fetchrow("SELECT * FROM sc_secrets WHERE discord_id = $1 FOR UPDATE", discord_id)
            color_secrets = dict(secrets["color_secrets"])
            shape_secrets = dict(secrets["shape_secrets"])
            total_color = {name: 0 for name in COLOR_NAMES}
            total_shape = {name: 0 for name in SHAPE_NAMES}
            factory_summaries = []

            for factory in factories:
                color_prod, shape_prod = calculate_production(
                    factory["arr"], factory["width"], factory["height"],
                )
                color_sum = 0
                shape_sum = 0
                for i, name in enumerate(COLOR_NAMES):
                    gain = color_prod.get(i, 0) * KYOHOON_EXTRA_PRODUCE_PER_FACTORY
                    color_secrets[name] += gain
                    total_color[name] += gain
                    color_sum += gain
                for i, name in enumerate(SHAPE_NAMES):
                    gain = shape_prod.get(i, 0) * KYOHOON_EXTRA_PRODUCE_PER_FACTORY
                    shape_secrets[name] += gain
                    total_shape[name] += gain
                    shape_sum += gain
                factory_summaries.append({
                    "slot": factory["slot"],
                    "color": color_sum,
                    "shape": shape_sum,
                })

            summary = {
                "times": KYOHOON_EXTRA_PRODUCE_PER_FACTORY,
                "color": total_color,
                "shape": total_shape,
                "factories": factory_summaries,
            }
            await conn.execute(
                "UPDATE sc_secrets SET color_secrets = $1, shape_secrets = $2 WHERE discord_id = $3",
                color_secrets, shape_secrets, discord_id,
            )
            await conn.execute(
                "INSERT INTO sc_kyohoon_rewards(submission_message_id, discord_id, thread_id, reward_summary) "
                "VALUES($1, $2, $3, $4)",
                message_id, discord_id, thread_id, summary,
            )
            return {"awarded": True, "created_user": created_user, "summary": summary}
