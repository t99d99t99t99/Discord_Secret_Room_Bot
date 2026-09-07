import asyncio
import unittest

from cogs.game_config import reward_policy, reward_policy_label
from cogs.secretae_incremental.db import grant_configured_reward


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _ExistingRewardConnection:
    def __init__(self):
        self.queries = []

    def transaction(self):
        return _Transaction()

    async def execute(self, query, *args):
        self.queries.append(query)

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        return {"state": "granted"}


class _PoolContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return False


class _ExistingRewardPool:
    def __init__(self):
        self.connection = _ExistingRewardConnection()

    def acquire(self):
        return _PoolContext(self.connection)


class RewardPolicyTests(unittest.TestCase):
    def test_policy_label_includes_amount_and_cap(self):
        self.assertEqual(
            reward_policy_label(
                {"type": "production_multiplier", "amount": "1.1", "weekly_cap": 7}
            ),
            "생산 배율 1.1 (주 7회 한도)",
        )
        self.assertEqual(
            reward_policy_label(
                {"type": "production_multiplier", "amount": "1.1", "weekly_cap": 7}, "en"
            ),
            "Production multiplier 1.1 (weekly cap: 7)",
        )

    def test_idempotent_reward_is_returned_before_weekly_cap_check(self):
        async def run():
            pool = _ExistingRewardPool()
            result = await grant_configured_reward(
                pool, 1, "notice_queue", 2, 3, 4,
                {"type": "production_multiplier", "amount": "1.1", "weekly_cap": 1},
            )
            self.assertEqual(result, {"awarded": False, "already_awarded": True})
            self.assertFalse(any("COUNT(*)" in query for query in pool.connection.queries))

        asyncio.run(run())

    def test_production_policy_can_store_a_weekly_cap(self):
        self.assertEqual(
            reward_policy("production_multiplier", "1.1", weekly_cap=7),
            {"type": "production_multiplier", "amount": "1.1", "weekly_cap": 7},
        )

    def test_weekly_cap_is_bounded_to_a_daily_limit(self):
        with self.assertRaises(ValueError):
            reward_policy("production_multiplier", "1.1", weekly_cap=0)
        with self.assertRaises(ValueError):
            reward_policy("production_multiplier", "1.1", weekly_cap=8)

    def test_fixed_essence_first_grant_is_preserved(self):
        self.assertEqual(
            reward_policy("fixed_essence", "1", "2"),
            {"type": "fixed_essence", "amount": "1", "first_grant_amount": "2"},
        )
