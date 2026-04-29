import random

COLOR_NAMES = ["RED", "ORANGE", "YELLOW", "GREEN", "BLUE", "PURPLE", "WHITE", "BLACK"]
SHAPE_NAMES = ["CIRCLE", "SQUARE", "HEART"]
INITIAL_CAPITAL = 1000
FACTORY_WIDTH = 5
FACTORY_HEIGHT = 3
FACTORY_COUNT = 3


def _random_arr(width: int, height: int) -> list:
    return [
        [{"color": random.randint(0, 7), "shape": random.randint(0, 2)} for _ in range(height)]
        for _ in range(width)
    ]


def _default_color_secrets() -> dict:
    return {name: INITIAL_CAPITAL for name in COLOR_NAMES}


def _default_shape_secrets() -> dict:
    return {name: INITIAL_CAPITAL for name in SHAPE_NAMES}


async def get_user(pool, discord_id: int):
    return await pool.fetchrow("SELECT * FROM sc_users WHERE discord_id = $1", discord_id)


async def create_user(pool, discord_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO sc_users(discord_id) VALUES($1)",
                discord_id,
            )
            for slot in range(FACTORY_COUNT):
                await conn.execute(
                    "INSERT INTO sc_factories(discord_id, slot, width, height, arr) "
                    "VALUES($1, $2, $3, $4, $5)",
                    discord_id, slot, FACTORY_WIDTH, FACTORY_HEIGHT,
                    _random_arr(FACTORY_WIDTH, FACTORY_HEIGHT),
                )
            await conn.execute(
                "INSERT INTO sc_secrets(discord_id, color_secrets, shape_secrets) "
                "VALUES($1, $2, $3)",
                discord_id, _default_color_secrets(), _default_shape_secrets(),
            )


async def get_factories(pool, discord_id: int):
    return await pool.fetch(
        "SELECT * FROM sc_factories WHERE discord_id = $1 ORDER BY slot",
        discord_id,
    )


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
