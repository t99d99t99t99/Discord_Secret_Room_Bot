"""Deterministic balance scenarios using the production domain functions."""

from .constants import COLORS, INITIAL_ORGANICS, INITIAL_SECRETS, SECRETS
from .db import (
    ONE,
    community_reward_step,
    concentration_gain,
    max_synthesis_step,
    maximum,
    production_step,
)
from .numbers import LayeredDecimal as N


def fresh_state(total_essence=0):
    """Return a database-independent state for a newly created guild player."""
    return {
        "shards": ONE,
        "essence": N.of(0),
        "total": N.of(total_essence),
        "highest": N.of(total_essence),
        "secrets": {key: N.of(INITIAL_SECRETS) for key in SECRETS},
        "organics": {key: N.of(INITIAL_ORGANICS) for key in COLORS},
    }


def seven_day_route(state, use_max=True, rewards_by_day=None, days=7):
    """Run a production route; the default is the seven-day fresh-account route."""
    purchases = []
    organic_gains = []
    rewards_by_day = rewards_by_day or {}
    reward_results = []
    for day in range(days):
        for policy in rewards_by_day.get(day + 1, ()):
            reward_results.append((day + 1, policy, community_reward_step(state, policy)))
        organic_gains.append(production_step(state))
        if use_max and day != days - 1:
            purchases.append(max_synthesis_step(state))
    return {
        "final_shards": state["shards"],
        "purchases": purchases,
        "organic_gains": organic_gains,
        "can_concentrate": concentration_gain(state).sign > 0,
        "essence_gain": concentration_gain(state),
        "reward_results": reward_results,
    }


def concentrate_for_simulation(state):
    """Apply the non-clock portion of concentration for a subsequent-route test."""
    gain = concentration_gain(state)
    if not gain.sign:
        raise ValueError("cannot concentrate")
    state["essence"] = state["essence"] + gain
    state["total"] = state["total"] + gain
    state["highest"] = maximum(state["highest"], state["essence"])
    state["shards"] = ONE
    state["secrets"] = {key: N.of(INITIAL_SECRETS) for key in SECRETS}
    state["organics"] = {key: N.of(INITIAL_ORGANICS) for key in COLORS}
    return gain
