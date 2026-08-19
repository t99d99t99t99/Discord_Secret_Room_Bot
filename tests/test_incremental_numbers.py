from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

from cogs.secretae_incremental.constants import HEART
from cogs.secretae_incremental.db import (
    concentration_gain,
    concentration_week_start,
    game_date,
    milky_way_multiplier,
)
from cogs.secretae_incremental.numbers import (
    LayeredDecimal as N,
    format_amount,
    maximum,
)
from cogs.secretae_incremental.migration import _valid_nonnegative_int
from cogs.secretae_incremental import db as incremental_db


class LayeredDecimalTests(unittest.TestCase):
    def test_legacy_boolean_is_not_a_valid_integer(self):
        self.assertEqual(_valid_nonnegative_int(True, 100), 100)
        self.assertEqual(_valid_nonnegative_int(False, 100), 100)
        self.assertEqual(_valid_nonnegative_int(42, 100), 42)

    def test_json_round_trip_and_zero_encoding(self):
        self.assertEqual(N.from_json(N.of(0).to_json()), N.of(0))
        self.assertEqual(N.from_json(N.of("123.5").to_json()), N.of("123.5"))

    def test_basic_arithmetic_and_affordability(self):
        self.assertEqual(N.of(7) + N.of(5), N.of(12))
        self.assertEqual(N.of(7) - N.of(5), N.of(2))
        self.assertEqual(N.of("-0.5") - N.of("-0.3"), N.of("-0.2"))
        self.assertEqual(N.of(7) * N.of(5), N.of(35))
        self.assertEqual((N.of(7) / N.of(2)).floor(), N.of(3))
        self.assertEqual(5 + N.of(7), N.of(12))
        self.assertEqual(5 - N.of(7), N.of(-2))
        self.assertEqual(5 * N.of(7), N.of(35))
        self.assertEqual(35 / N.of(7), N.of(5))
        self.assertEqual((N.of(2) ** 3).floor(), N.of(8))
        self.assertLess(N.of(7), N.of(8))
        self.assertGreaterEqual(N.of(7), N.of(7))
        self.assertTrue(N.of(10).is_affordable(N.of(10)))
        self.assertEqual(maximum(N.of(3), N.of(4)), N.of(4))

    def test_layered_multiplication_and_division_preserve_scale(self):
        self.assertEqual(N.of("1e15") * N.of(200), N.of("2e17"))
        self.assertEqual(N.of("2e17") / N.of(200), N.of("1e15"))
        self.assertEqual(N.of(10) ** N.of(1000), N.of("1e1000"))
        self.assertEqual(
            (N.of("1e1000") ** N.of("1e1000")).to_json(),
            {"sign": 1, "layer": "2", "mag": "1003"},
        )

    def test_formatting_boundaries(self):
        self.assertEqual(format_amount(N.of("9999.9")), "9999")
        self.assertEqual(format_amount(N.of("38600000")), "3.86e7")
        self.assertTrue(format_amount(N.of("1e15")).endswith("e15"))

    def test_unbounded_layer_never_uses_integer_parsing(self):
        layer = "9" * 80
        value = N.from_json({"sign": 1, "layer": layer, "mag": "2"})
        self.assertEqual(value.to_json()["layer"], layer)
        self.assertEqual(value.log10().to_json()["layer"], "9" * 79 + "8")

    def test_very_large_layered_operations(self):
        million_exponent = N.of("1e1000000")
        two_million_exponent = N.of("1e2000000")
        three_million_exponent = N.of("1e3000000")

        self.assertEqual(
            million_exponent * two_million_exponent, three_million_exponent
        )
        self.assertEqual(
            three_million_exponent / two_million_exponent, million_exponent
        )
        self.assertEqual(
            -three_million_exponent / two_million_exponent, -million_exponent
        )
        self.assertGreater(two_million_exponent, million_exponent)

        layer = "9" * 80
        value = N.from_json({"sign": 1, "layer": layer, "mag": "2"})
        smaller = N.of("1e1000")

        self.assertEqual(value + smaller, value)
        self.assertEqual(value - smaller, value)
        self.assertEqual(value * N.of(10), value)
        self.assertEqual(value / N.of(10), value)
        self.assertEqual(value / value, N.of(1))
        self.assertEqual(value ** N.of(1), value)
        self.assertEqual(value ** N.of(2), value)
        self.assertEqual(value + -value, N.of(0))

        negative = -value
        self.assertEqual(negative * N.of(10), negative)
        self.assertEqual(negative / N.of(10), negative)

        exponentiated = N.of(10) ** value
        self.assertEqual(exponentiated.to_json()["layer"], "1" + "0" * 80)
        self.assertEqual(exponentiated.to_json()["mag"], "2")


class GameDateTests(unittest.TestCase):
    def test_kst_five_am_boundary(self):
        self.assertEqual(
            game_date(datetime(2026, 1, 1, 19, 59, tzinfo=timezone.utc)).isoformat(),
            "2026-01-01",
        )
        self.assertEqual(
            game_date(datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)).isoformat(),
            "2026-01-02",
        )

    def test_concentration_week_resets_monday_at_kst_five_am(self):
        # Sunday 19:59 UTC is Monday 04:59 KST, still in the prior game week.
        self.assertEqual(
            concentration_week_start(
                datetime(2026, 1, 4, 19, 59, tzinfo=timezone.utc)
            ).isoformat(),
            "2025-12-29",
        )
        self.assertEqual(
            concentration_week_start(
                datetime(2026, 1, 4, 20, 0, tzinfo=timezone.utc)
            ).isoformat(),
            "2026-01-05",
        )

    def test_milky_way_multiplier_is_diminishing_at_size_70(self):
        # Size is total Essence + 1, so size 70 means 69 total Essence.
        multiplier = milky_way_multiplier(N.of(69))
        self.assertAlmostEqual(float(multiplier.mag), 0.348697, places=6)

    def test_concentration_gain_compounds_from_current_essence(self):
        state = {
            "shards": N.of("1e3000"),
            "essence": N.of(99),
            "secrets": {HEART: N.of(100)},
        }
        # R=400, so the efficiency is log10(log10(401)); gain=ceil(100×(1+efficiency)).
        self.assertEqual(concentration_gain(state), N.of(142))


class PendingRewardTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_marks_an_existing_idempotent_reward_as_complete(self):
        pool = unittest.mock.Mock()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "discord_id": 1,
                    "source_thread_id": 2,
                    "message_id": 3,
                    "reward_posted_thread_id": 4,
                }
            ]
        )

        with (
            patch.object(
                incremental_db,
                "grant_kyohoon_reward",
                AsyncMock(return_value={"awarded": False, "already_awarded": True}),
            ),
            patch.object(
                incremental_db, "mark_submission_rewarded", AsyncMock()
            ) as mark,
        ):
            summary = await incremental_db.retry_pending_submission_rewards(
                pool, "kyohoon"
            )

        self.assertEqual(summary, {"awarded": 0, "already_awarded": 1, "failed": []})
        mark.assert_awaited_once_with(pool, "kyohoon", 3)
