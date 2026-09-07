import unittest

from cogs.secretae_incremental.constants import PRODUCTION_DIVISOR
from cogs.secretae_incremental.db import (
    community_reward_step,
    concentration_gain,
    max_synthesis_step,
    production_step,
)
from cogs.secretae_incremental.numbers import LayeredDecimal as N
from cogs.secretae_incremental.simulation import (
    concentrate_for_simulation,
    fresh_state,
    seven_day_route,
)


class GameBalanceTests(unittest.TestCase):
    def test_community_reward_step_uses_guild_local_state(self):
        state = fresh_state()
        other_guild = fresh_state()
        state["organics"]["RED"] = N.of(10)
        other_guild["organics"]["RED"] = N.of(10)
        result = community_reward_step(
            state, {"type": "production_multiplier", "amount": "1.1"}
        )
        self.assertIn("after_organics", result)
        self.assertGreater(state["organics"]["RED"], other_guild["organics"]["RED"])

    def test_fresh_v1_reaches_first_concentration_in_seven_productions(self):
        self.assertEqual(PRODUCTION_DIVISOR, 77)
        state = fresh_state()
        result = seven_day_route(state)
        self.assertTrue(result["can_concentrate"])
        self.assertGreaterEqual(result["final_shards"], N.of("1e15"))
        self.assertLess(result["final_shards"], N.of("1e16"))
        self.assertGreaterEqual(result["essence_gain"], N.of(1))
        self.assertLessEqual(result["essence_gain"], N.of(3))
        self.assertEqual(len(result["purchases"]), 6)
        self.assertTrue(
            all(any(gain.sign for _, gain in step["secrets"].values()) for step in result["purchases"])
        )

    def test_fresh_v1_without_max_cannot_concentrate(self):
        state = fresh_state()
        result = seven_day_route(state, use_max=False)
        self.assertFalse(result["can_concentrate"])
        self.assertLess(result["final_shards"], N.of("1e15"))

    def test_first_prestige_makes_the_next_route_complete_in_six_productions(self):
        state = fresh_state()
        first = seven_day_route(state)
        self.assertGreaterEqual(concentrate_for_simulation(state), N.of(1))
        for _ in range(5):
            production_step(state)
            max_synthesis_step(state)
        production_step(state)
        self.assertTrue(concentration_gain(state).sign)
        self.assertGreater(concentration_gain(state), first["essence_gain"])

    def test_three_prestige_routes_keep_advancing_without_overflow(self):
        state = fresh_state()
        gains = []
        for _ in range(3):
            result = seven_day_route(state)
            self.assertTrue(result["can_concentrate"])
            gains.append(concentrate_for_simulation(state))
        self.assertGreater(state["essence"], N.of(2))
        self.assertTrue(all(gain.sign for gain in gains))

    def test_simulated_guild_states_do_not_share_world_essence(self):
        quiet = fresh_state()
        active = fresh_state(total_essence=10_000)
        seven_day_route(active)
        quiet_result = seven_day_route(quiet)
        baseline_result = seven_day_route(fresh_state())
        self.assertEqual(quiet_result["final_shards"], baseline_result["final_shards"])

    def test_huge_amount_max_synthesis_finishes(self):
        state = fresh_state()
        state["shards"] = N.of("1e1000000")
        result = max_synthesis_step(state)
        self.assertTrue(any(gain.sign for _, gain in result["secrets"].values()))

    def test_small_and_active_server_production_rewards_do_not_replace_first_loop(self):
        policy = {"type": "production_multiplier", "amount": "1.1"}
        small = seven_day_route(fresh_state(), rewards_by_day={1: [policy]})
        active = seven_day_route(
            fresh_state(), rewards_by_day={day: [policy] for day in range(1, 8)}
        )
        # A weekly small-server reward and a capped daily active-server reward
        # accelerate shards without granting fixed Essence or reaching the
        # threshold in the first four production days.
        self.assertTrue(small["can_concentrate"])
        self.assertTrue(active["can_concentrate"])
        for days in range(1, 5):
            early = seven_day_route(
                fresh_state(), rewards_by_day={day: [policy] for day in range(1, days + 1)}, days=days
            )
            self.assertFalse(early["can_concentrate"])
        self.assertLessEqual(active["essence_gain"], N.of(3))
