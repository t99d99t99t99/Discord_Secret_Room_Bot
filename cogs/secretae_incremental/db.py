"""Transactional domain operations for the guild-scoped incremental game.

This module remains cohesive despite its size: every public operation owns one
atomic game-state transition.  Pure arithmetic is shared with simulations, and
the transaction functions centralize locking and durable persistence here.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from .constants import *
from .numbers import LayeredDecimal as N, maximum

ZERO, ONE = N.of(0), N.of(1)
MILKY_WAY_BASE = N.of("0.2")
MILKY_WAY_LOG_BONUS = N.of("0.035")


def _amounts(keys, initial):
    """Create a complete resource collection with one initial value."""
    return {key: N.of(initial) for key in keys}


def _encode(values):
    """Encode a resource collection for JSONB storage."""
    return {key: value.to_json() for key, value in values.items()}


def _decode(raw, keys):
    """Validate the exact resource keys and decode their quantities."""
    if not isinstance(raw, dict) or set(raw) != set(keys):
        raise ValueError("Game data is corrupted.")

    return {key: N.from_json(raw[key]) for key in keys}


def game_date(now=None):
    """Return the game date, whose daily boundary is 05:00 KST."""
    now = now or datetime.now(timezone.utc)
    kst = now.astimezone(timezone(timedelta(hours=9)))
    return (kst - timedelta(days=1) if kst.hour < 5 else kst).date()


def concentration_week_start(now=None):
    """Return the Monday game date for the current concentration period."""
    current = game_date(now)
    return current - timedelta(days=current.weekday())


def concentration_available(state, now=None):
    """Return whether the player has not concentrated this KST game week."""
    return state["player"]["last_concentrated_week_start"] != concentration_week_start(
        now
    )


def milky_way_multiplier(total_essence):
    """Return the global production factor with diminishing returns.

    A linear ``total_essence + 1`` multiplier let a large guild carry a new
    player through their first concentration in one production.  A logarithmic
    factor preserves shared-world rewards without an exponential colour cascade.
    """
    size = total_essence + ONE
    return MILKY_WAY_BASE + size.ln() * MILKY_WAY_LOG_BONUS


async def ensure_player(conn, guild_id, discord_id):
    """Create the guild world and player with default resources if absent."""
    # World state must exist before this player can participate in shared progress.
    await conn.execute(
        "INSERT INTO guild_game_world_state(guild_id,total_essence,highest_essence) VALUES($1,$2,$2) ON CONFLICT(guild_id) DO NOTHING",
        guild_id,
        ZERO.to_json(),
    )
    # Player scalar state, secrets, and organics are independently idempotent
    # so a partially initialized account repairs safely on its next command.
    await conn.execute(
        "INSERT INTO guild_game_players(guild_id,discord_id,shards,essence) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
        guild_id,
        discord_id,
        ONE.to_json(),
        ZERO.to_json(),
    )
    await conn.execute(
        "INSERT INTO guild_game_secrets(guild_id,discord_id,amounts) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
        guild_id,
        discord_id,
        _encode(_amounts(SECRETS, 100)),
    )
    await conn.execute(
        "INSERT INTO guild_game_organics(guild_id,discord_id,amounts) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
        guild_id,
        discord_id,
        _encode(_amounts(COLORS, 1)),
    )


async def _state(conn, guild_id, discord_id, world_lock="FOR SHARE"):
    """항상 세계를 먼저 잠근 뒤 플레이어 상태를 잠그고 디코딩합니다."""
    # Create the guild world before taking its lock; the insert is idempotent.
    await conn.execute(
        "INSERT INTO guild_game_world_state(guild_id,total_essence,highest_essence) VALUES($1,$2,$2) ON CONFLICT(guild_id) DO NOTHING",
        guild_id,
        ZERO.to_json(),
    )
    world = await conn.fetchrow(
        f"SELECT * FROM guild_game_world_state WHERE guild_id=$1 {world_lock}", guild_id
    )
    await ensure_player(conn, guild_id, discord_id)
    player = await conn.fetchrow(
        "SELECT * FROM guild_game_players WHERE guild_id=$1 AND discord_id=$2 FOR UPDATE",
        guild_id,
        discord_id,
    )
    secrets = await conn.fetchrow(
        "SELECT amounts FROM guild_game_secrets WHERE guild_id=$1 AND discord_id=$2 FOR UPDATE",
        guild_id,
        discord_id,
    )
    organics = await conn.fetchrow(
        "SELECT amounts FROM guild_game_organics WHERE guild_id=$1 AND discord_id=$2 FOR UPDATE",
        guild_id,
        discord_id,
    )
    return {
        "player": player,
        "shards": N.from_json(player["shards"]),
        "essence": N.from_json(player["essence"]),
        "secrets": _decode(secrets["amounts"], SECRETS),
        "organics": _decode(organics["amounts"], COLORS),
        "total": N.from_json(world["total_essence"]),
        "highest": N.from_json(world["highest_essence"]),
    }


async def get_status(pool, guild_id, discord_id):
    """잠긴 일관된 플레이어 게임 상태 스냅샷을 반환합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await _state(conn, guild_id, discord_id)


ALERT_TYPES = {"disabled", "concentration", "game"}


async def record_game_command(pool, guild_id, discord_id, now=None):
    """플레이어가 이 게임 날짜에 /게임 명령을 사용했음을 기록합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_player(conn, guild_id, discord_id)
            await conn.execute(
                "UPDATE guild_game_players SET last_game_command_game_date=$3, updated_at=NOW() WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                game_date(now),
            )


async def save_alert_setting(pool, guild_id, discord_id, alert_type, alert_hour=None):
    """플레이어별 알림 설정을 저장합니다."""
    if alert_type not in ALERT_TYPES:
        raise ValueError("알 수 없는 알림 유형입니다.")
    if alert_type == "disabled":
        alert_hour = None
    elif not isinstance(alert_hour, int) or not 0 <= alert_hour <= 23:
        raise ValueError("알림 시각은 0시부터 23시 사이여야 합니다.")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_player(conn, guild_id, discord_id)
            await conn.execute(
                "INSERT INTO guild_game_alert_settings(guild_id,discord_id,alert_type,alert_hour) VALUES($1,$2,$3,$4) "
                "ON CONFLICT(guild_id,discord_id) DO UPDATE SET alert_type=EXCLUDED.alert_type, "
                "alert_hour=EXCLUDED.alert_hour, updated_at=NOW()",
                guild_id,
                discord_id,
                alert_type,
                alert_hour,
            )


async def due_alert_players(pool, guild_id, hour, is_concentration_deadline, now=None):
    """주간 마감일을 포함하여 선택한 시각의 알림 대상 플레이어를 반환합니다."""
    current_date = game_date(now)
    current_week = concentration_week_start(now)
    return await pool.fetch(
        "SELECT settings.discord_id, settings.alert_type, "
        "players.last_concentrated_week_start IS DISTINCT FROM $2 AS needs_concentration, "
        "players.last_game_command_game_date IS DISTINCT FROM $3 AS needs_game "
        "FROM guild_game_alert_settings AS settings "
        "JOIN guild_game_players AS players ON players.guild_id=settings.guild_id AND players.discord_id=settings.discord_id "
        "WHERE settings.guild_id=$1 AND settings.alert_hour=$2 AND ("
        "(settings.alert_type='concentration' AND $5 AND players.last_concentrated_week_start IS DISTINCT FROM $3) OR "
        "(settings.alert_type='game' AND ("
        "players.last_game_command_game_date IS DISTINCT FROM $4 OR "
        "($5 AND players.last_concentrated_week_start IS DISTINCT FROM $3)"
        "))"
        ")",
        guild_id,
        hour,
        current_week,
        current_date,
        is_concentration_deadline,
    )


def quote_synthesis(state, key, amount):
    """상태를 바꾸지 않고 반올림된 합성 가격을 계산합니다."""
    if key not in SECRETS or amount.sign <= 0:
        raise ValueError("수량은 1 이상의 정수여야 합니다.")

    current = state["secrets"][key]
    # 등비수열의 합: r^current * (r^n - 1) / (r - 1), r = 1.1^(1/100)
    r = N.of(Decimal("1.1") ** Decimal(".01"))
    base = r**current * (r**amount - ONE) / (r - ONE)
    gap = state["highest"] - state["essence"]
    discount = (
        min(Decimal(".5"), gap.mag / 100)
        if gap.sign > 0 and gap.layer == "0"
        else Decimal(".5")
    )
    square = state["secrets"][SQUARE]
    multiplier = ONE / (ONE + (square + ONE).ln() / N.of(17))
    return maximum(ONE, (base * N.of(1 - discount) * multiplier).floor())


def production_step(state):
    """Apply one production run to an in-memory state and return its gains.

    Database transactions and balance simulations share this function.  It must
    therefore contain no clock or database access.
    """
    before_shards = state["shards"]
    before_organics = dict(state["organics"])
    organic_gains = {color: ZERO for color in COLORS}
    shard_gain = ZERO
    galaxy = milky_way_multiplier(state["total"])
    circle = ONE + state["secrets"][CIRCLE] / N.of(100)
    for color in PRODUCTION_ORDER:
        effective = state["secrets"][color] * circle
        raw = (
            state["organics"][color]
            * effective ** N.of("1.05")
            / N.of(PRODUCTION_DIVISOR)
            * galaxy
        )
        gain = (raw ** (ONE + state["essence"] / N.of(240))).ceil()
        if color == RED:
            state["shards"] = state["shards"] + gain
            shard_gain = gain
        else:
            previous_color = COLORS[COLORS.index(color) - 1]
            state["organics"][previous_color] = state["organics"][previous_color] + gain
            organic_gains[previous_color] = gain
    return {
        "shards": (before_shards, shard_gain),
        "organics": {
            color: (before_organics[color], organic_gains[color]) for color in COLORS
        },
    }


def max_synthesis_step(state):
    """Apply the maximum-synthesis priority algorithm to an in-memory state."""
    bought = {key: ZERO for key in SECRETS}
    original_shards = state["shards"]
    original_secrets = dict(state["secrets"])
    for key in MAX_SYNTHESIS_ORDER:
        lower, upper = ZERO, ONE
        while state["shards"] >= quote_synthesis(state, key, upper):
            lower, upper = upper, upper * N.of(2)
        while True:
            try:
                if upper - lower <= ONE:
                    break
            except ValueError:
                break
            midpoint = ((lower + upper) / N.of(2)).floor()
            if state["shards"] >= quote_synthesis(state, key, midpoint):
                lower = midpoint
            else:
                upper = midpoint
        if lower.sign:
            cost = quote_synthesis(state, key, lower)
            state["shards"] = state["shards"] - cost
            state["secrets"][key] = state["secrets"][key] + lower
            bought[key] = lower
    return {
        "shards": (original_shards, original_shards - state["shards"]),
        "secrets": {key: (original_secrets[key], bought[key]) for key in SECRETS},
    }


def community_reward_step(state, policy):
    """Apply one validated community reward to an in-memory guild player state."""
    policy_type = policy.get("type")
    amount = N.of(policy.get("amount", "0"))
    if policy_type in {"essence_multiplier", "fixed_essence"}:
        before = state["essence"]
        if not before.sign and policy.get("first_grant_amount"):
            after = N.of(policy["first_grant_amount"])
        elif policy_type == "essence_multiplier":
            after = (before * amount).floor()
        else:
            after = before + amount
        state["essence"] = after
        state["total"] = state["total"] + (after - before)
        state["highest"] = maximum(state["highest"], after)
        return {"after_essence": after.to_json()}
    if policy_type == "each_secret_multiplier":
        after = {
            key: (value * amount).floor() for key, value in state["secrets"].items()
        }
        state["secrets"] = after
        return {"after_secrets": _encode(after)}
    if policy_type == "production_multiplier":
        after = {
            key: (value * amount).floor() for key, value in state["organics"].items()
        }
        state["organics"] = after
        return {"after_organics": _encode(after)}
    raise ValueError("지원하지 않는 보상 정책입니다.")


async def synthesize(pool, guild_id, discord_id, key, amount_text):
    """한 종류의 비밀 묶음을 구매합니다."""
    if not amount_text.isdecimal():
        raise ValueError("수량은 1 이상의 정수여야 합니다.")
    amount = N.of(amount_text)
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _state(conn, guild_id, discord_id)
            cost = quote_synthesis(state, key, amount)
            if not state["shards"].is_affordable(cost):
                raise ValueError("비밀 파편이 부족합니다.")

            state["shards"] = state["shards"] - cost
            state["secrets"][key] = state["secrets"][key] + amount
            await conn.execute(
                "UPDATE guild_game_players SET shards=$3, updated_at=NOW() WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                state["shards"].to_json(),
            )
            await conn.execute(
                "UPDATE guild_game_secrets SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                _encode(state["secrets"]),
            )
            return cost, state


async def produce(pool, guild_id, discord_id):
    """KST 기준 하루 생산을 한 번 실행하고 모든 획득량을 함께 저장합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _state(conn, guild_id, discord_id)
            today = game_date()
            if state["player"]["last_produced_game_date"] == today:
                raise ValueError("오늘은 이미 생산했습니다.")

            summary = production_step(state)

            await conn.execute(
                "UPDATE guild_game_players SET shards=$3, last_produced_game_date=$4, updated_at=NOW() WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                state["shards"].to_json(),
                today,
            )
            await conn.execute(
                "UPDATE guild_game_organics SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                _encode(state["organics"]),
            )
            return state, summary


async def max_synthesize(pool, guild_id, discord_id):
    """엄격한 우선순위대로 각 비밀의 구매 가능한 최대 묶음을 구매합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _state(conn, guild_id, discord_id)
            summary = max_synthesis_step(state)
            if not any(gain.sign for _, gain in summary["secrets"].values()):
                raise ValueError("합성할 비밀 파편이 부족합니다.")

            await conn.execute(
                "UPDATE guild_game_players SET shards=$3, updated_at=NOW() WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                state["shards"].to_json(),
            )
            await conn.execute(
                "UPDATE guild_game_secrets SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                _encode(state["secrets"]),
            )
            return state, summary


def concentration_gain(state):
    """상태를 바꾸지 않고 현재 플레이어에게 유리한 정수 획득량을 계산합니다."""
    shards = state["shards"]
    if shards < N.of("1e15"):
        return ZERO

    base = (shards.ln() / N.of("34.538776394910684")).ceil()
    heart_multiplier = ONE + (state["secrets"][HEART] / N.of(100)).ceil()
    run_gain = base * heart_multiplier

    # 완료한 회차는 이중 로그 효율 항만 기여하고, 기존 정수는 복리의 기반이 됩니다.
    # 따라서 한 번의 매우 큰 파편 수가 여러 지수 구간을 건너뛰지 않고 진행도에 따라
    # 십진 지수가 부드럽게 증가합니다.
    run_efficiency = (run_gain + ONE).log10().log10()
    if run_efficiency.sign < 0:
        run_efficiency = ZERO

    return ((state["essence"] + ONE) * (ONE + run_efficiency)).ceil()


async def concentrate(pool, guild_id, discord_id):
    """정수를 지급하고 플레이어의 비정수 자원을 원자적으로 초기화합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _state(conn, guild_id, discord_id, "FOR UPDATE")
            if not concentration_available(state):
                raise ValueError("농축은 매주 월요일 오전 5시(KST)에 초기화됩니다.")

            gain = concentration_gain(state)
            if not gain.sign:
                raise ValueError("농축으로 얻을 정수가 없습니다.")

            new_essence = state["essence"] + gain
            state["total"] = state["total"] + gain
            state["highest"] = maximum(state["highest"], new_essence)
            await conn.execute(
                "UPDATE guild_game_players SET shards=$3, essence=$4, last_concentrated_week_start=$5, "
                "updated_at=NOW() WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                ONE.to_json(),
                new_essence.to_json(),
                concentration_week_start(),
            )
            await conn.execute(
                "UPDATE guild_game_secrets SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                _encode(_amounts(SECRETS, 100)),
            )
            await conn.execute(
                "UPDATE guild_game_organics SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                _encode(_amounts(COLORS, 1)),
            )
            await conn.execute(
                "UPDATE guild_game_world_state SET total_essence=$2, highest_essence=$3, "
                "updated_at=NOW() WHERE guild_id=$1",
                guild_id,
                state["total"].to_json(),
                state["highest"].to_json(),
            )
            return gain, state["essence"], new_essence


async def grant_kyohoon_reward(
    pool, guild_id, discord_id, source_thread_id, message_id, posted_thread_id
):
    """게시된 교훈 신청에 대해 이야기의 정수를 한 번 두 배로 늘립니다. 정수가 0인
    플레이어에게는 정수 1개를 지급합니다.

    멱등성 기록과 모든 자원 변경은 함께 커밋됩니다. 호출자는 신청의
    ``posted_at`` 갱신이 성공한 뒤에만 이 함수를 호출해야 합니다.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchval(
                "INSERT INTO si_kyohoon_rewards("
                "submission_message_id, discord_id, source_thread_id, posted_thread_id, reward_amount, details"
                ") VALUES($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT(submission_message_id) DO NOTHING RETURNING 1",
                message_id,
                discord_id,
                source_thread_id,
                posted_thread_id,
                ZERO.to_json(),
                {},
            )
            if inserted != 1:
                return {"awarded": False, "already_awarded": True}

            state = await _state(conn, guild_id, discord_id, "FOR UPDATE")
            before = state["essence"]
            after = ONE if not before.sign else before * N.of(2)
            reward = after - before
            total = state["total"] + reward
            highest = maximum(state["highest"], after)
            details = {
                "before_essence": before.to_json(),
                "after_essence": after.to_json(),
                "reward_amount": reward.to_json(),
            }
            await conn.execute(
                "UPDATE guild_game_players SET essence=$3, updated_at=NOW() WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                after.to_json(),
            )
            await conn.execute(
                "UPDATE guild_game_world_state SET total_essence=$2, highest_essence=$3, updated_at=NOW() WHERE guild_id=$1",
                guild_id,
                total.to_json(),
                highest.to_json(),
            )
            await conn.execute(
                "UPDATE si_kyohoon_rewards SET reward_amount=$2, details=$3 WHERE submission_message_id=$1",
                message_id,
                reward.to_json(),
                details,
            )
            return {"awarded": True, **details}


async def grant_tomak_reward(
    pool, guild_id, discord_id, source_thread_id, message_id, posted_thread_id
):
    """게시된 토막상식에 대해 모든 비밀을 한 번 100% 증가시킵니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchval(
                "INSERT INTO si_tomak_rewards("
                "submission_message_id, discord_id, source_thread_id, posted_thread_id, details"
                ") VALUES($1, $2, $3, $4, $5) "
                "ON CONFLICT(submission_message_id) DO NOTHING RETURNING 1",
                message_id,
                discord_id,
                source_thread_id,
                posted_thread_id,
                {},
            )
            if inserted != 1:
                return {"awarded": False, "already_awarded": True}

            state = await _state(conn, guild_id, discord_id)
            before = dict(state["secrets"])
            after = {key: (value * N.of("2")).floor() for key, value in before.items()}
            increases = {key: after[key] - before[key] for key in SECRETS}
            details = {
                "multiplier": "2",
                "before": _encode(before),
                "after": _encode(after),
                "increases": _encode(increases),
            }
            await conn.execute(
                "UPDATE guild_game_secrets SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                _encode(after),
            )
            await conn.execute(
                "UPDATE si_tomak_rewards SET details=$2 WHERE submission_message_id=$1",
                message_id,
                details,
            )
            return {"awarded": True, **details}


async def grant_relay_secret_reward(pool, guild_id, discord_id, message_id):
    """다른 작성자가 릴레이를 이을 때까지 토막상식 방식 보상을 한 번 적용합니다.

    길드별 ``last_rewarded_discord_id``는 Discord 메시지가 삭제되어도 유지됩니다. 따라서
    플레이어는 보상받은 메시지를 삭제한 뒤 다른 사람이 이야기를 잇기 전에 다시
    작성하여 두 번째 보상을 얻을 수 없습니다.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO guild_game_relay_turn_state(guild_id) VALUES($1) ON CONFLICT(guild_id) DO NOTHING",
                guild_id,
            )
            state = await _state(conn, guild_id, discord_id)
            turn = await conn.fetchrow(
                "SELECT last_rewarded_discord_id FROM guild_game_relay_turn_state WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            )
            if turn["last_rewarded_discord_id"] == discord_id:
                return {"awarded": False, "reason": "waiting_for_another_author"}

            inserted = await conn.fetchval(
                "INSERT INTO guild_game_relay_rewards(guild_id,message_id,discord_id) VALUES($1,$2,$3) "
                "ON CONFLICT(guild_id,message_id) DO NOTHING RETURNING 1",
                guild_id,
                message_id,
                discord_id,
            )
            if inserted != 1:
                return {"awarded": False}

            before = dict(state["secrets"])
            after = {
                key: (value * N.of("1.05")).floor() for key, value in before.items()
            }
            increases = {key: after[key] - before[key] for key in SECRETS}
            details = {
                "multiplier": "1.05",
                "before": _encode(before),
                "after": _encode(after),
                "increases": _encode(increases),
            }
            await conn.execute(
                "UPDATE guild_game_secrets SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                discord_id,
                _encode(after),
            )
            await conn.execute(
                "UPDATE guild_game_relay_rewards SET details=$3 WHERE guild_id=$1 AND message_id=$2",
                guild_id,
                message_id,
                details,
            )
            await conn.execute(
                "UPDATE guild_game_relay_turn_state SET last_rewarded_discord_id=$2, updated_at=NOW() WHERE guild_id=$1",
                guild_id,
                discord_id,
            )
            return {"awarded": True, **details}


async def grant_configured_reward(
    pool, guild_id, source_type, source_id, message_id, discord_id, policy
):
    """Apply a typed queue/story policy exactly once and retain a rollback snapshot.

    The policy is intentionally applied in the same transaction as its ledger
    state.  This is the generic successor to the legacy Tomak/Kyohoon branches.
    """
    policy_type = policy.get("type")
    if policy_type == "none":
        return {"awarded": False, "not_applicable": True}
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialize an idempotency retry and the feature's weekly-cap
            # check, so concurrent publications cannot over-grant.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0))",
                str(guild_id),
                str(source_id),
            )
            existing = await conn.fetchrow(
                "SELECT state FROM game_reward_ledger WHERE guild_id=$1 AND source_type=$2 "
                "AND contribution_message_id=$3 FOR UPDATE",
                guild_id,
                source_type,
                message_id,
            )
            if existing is not None:
                return {
                    "awarded": False,
                    "already_awarded": existing["state"] == "granted",
                }
            weekly_cap = policy.get("weekly_cap")
            if weekly_cap is not None:
                grants = await conn.fetchval(
                    "SELECT COUNT(*) FROM game_reward_ledger WHERE guild_id=$1 AND source_type=$2 "
                    "AND source_id=$3 AND state='granted' AND created_at >= "
                    "(date_trunc('week', NOW() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul')",
                    guild_id,
                    source_type,
                    source_id,
                )
                if grants >= weekly_cap:
                    return {"awarded": False, "cap_reached": True}
            ledger = await conn.fetchrow(
                """INSERT INTO game_reward_ledger(guild_id,source_type,source_id,contribution_message_id,recipient_id,policy)
                   VALUES($1,$2,$3,$4,$5,$6)
                   ON CONFLICT(guild_id,source_type,contribution_message_id) DO NOTHING
                   RETURNING id""",
                guild_id,
                source_type,
                source_id,
                message_id,
                discord_id,
                policy,
            )
            if ledger is None:
                return {"awarded": False, "already_awarded": True}
            state = await _state(conn, guild_id, discord_id, "FOR UPDATE")
            snapshot = {
                "player": {
                    "shards": state["shards"].to_json(),
                    "essence": state["essence"].to_json(),
                    "last_produced_game_date": (
                        state["player"]["last_produced_game_date"].isoformat()
                        if state["player"]["last_produced_game_date"]
                        else None
                    ),
                    "last_concentrated_week_start": (
                        state["player"]["last_concentrated_week_start"].isoformat()
                        if state["player"]["last_concentrated_week_start"]
                        else None
                    ),
                },
                "secrets": _encode(state["secrets"]),
                "organics": _encode(state["organics"]),
                "world": {
                    "total": state["total"].to_json(),
                    "highest": state["highest"].to_json(),
                },
            }
            await conn.execute(
                "INSERT INTO game_state_snapshots(guild_id,player_id,reward_ledger_id,state_payload,expires_at) VALUES($1,$2,$3,$4,NOW() + INTERVAL '30 days')",
                guild_id,
                discord_id,
                ledger["id"],
                snapshot,
            )
            details = {"before": snapshot, "policy": policy}
            result = community_reward_step(state, policy)
            details.update(result)
            if policy_type in {"essence_multiplier", "fixed_essence"}:
                await conn.execute(
                    "UPDATE guild_game_players SET essence=$3,updated_at=NOW() WHERE guild_id=$1 AND discord_id=$2",
                    guild_id,
                    discord_id,
                    state["essence"].to_json(),
                )
                await conn.execute(
                    "UPDATE guild_game_world_state SET total_essence=$2,highest_essence=$3,updated_at=NOW() WHERE guild_id=$1",
                    guild_id,
                    state["total"].to_json(),
                    state["highest"].to_json(),
                )
            elif policy_type == "each_secret_multiplier":
                await conn.execute(
                    "UPDATE guild_game_secrets SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                    guild_id,
                    discord_id,
                    _encode(state["secrets"]),
                )
            else:
                await conn.execute(
                    "UPDATE guild_game_organics SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                    guild_id,
                    discord_id,
                    _encode(state["organics"]),
                )
            await conn.execute(
                "UPDATE game_reward_ledger SET state='granted',details=$2,resolved_at=NOW() WHERE id=$1",
                ledger["id"],
                details,
            )
            return {"awarded": True, "ledger_id": ledger["id"], **details}


async def rollback_configured_reward(pool, guild_id, ledger_id):
    """Restore the recorded player snapshot when it does not change shared world state."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT ledger.*, snapshot.state_payload, snapshot.expires_at, snapshot.pruned_at
                   FROM game_reward_ledger ledger JOIN game_state_snapshots snapshot ON snapshot.reward_ledger_id=ledger.id
                   WHERE ledger.id=$1 AND ledger.guild_id=$2 FOR UPDATE""",
                ledger_id,
                guild_id,
            )
            if row is None or row["state"] != "granted":
                raise ValueError("되돌릴 수 있는 지급 기록을 찾지 못했습니다.")
            if row["pruned_at"] or row["expires_at"] <= datetime.now(timezone.utc):
                raise ValueError(
                    "보상 스냅샷 보존 기간이 지나 전체 되돌리기를 할 수 없습니다."
                )
            if row["policy"].get("type") in {"essence_multiplier", "fixed_essence"}:
                raise ValueError(
                    "공유 세계 정수를 변경한 보상은 안전하게 전체 되돌리기를 할 수 없습니다."
                )
            snapshot = row["state_payload"]
            player = snapshot["player"]
            await conn.execute(
                "UPDATE guild_game_players SET shards=$3,essence=$4,last_produced_game_date=$5,last_concentrated_week_start=$6,updated_at=NOW() WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                row["recipient_id"],
                player["shards"],
                player["essence"],
                player["last_produced_game_date"],
                player["last_concentrated_week_start"],
            )
            await conn.execute(
                "UPDATE guild_game_secrets SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                row["recipient_id"],
                snapshot["secrets"],
            )
            await conn.execute(
                "UPDATE guild_game_organics SET amounts=$3 WHERE guild_id=$1 AND discord_id=$2",
                guild_id,
                row["recipient_id"],
                snapshot["organics"],
            )
            await conn.execute(
                "UPDATE game_reward_ledger SET state='revoked',resolved_at=NOW() WHERE id=$1",
                ledger_id,
            )
            return {"recipient_id": row["recipient_id"], "policy": row["policy"]}


async def prune_expired_reward_snapshots(pool):
    """Mark expired snapshots as pruned while retaining immutable ledger evidence."""
    return await pool.execute(
        "UPDATE game_state_snapshots SET pruned_at=NOW() "
        "WHERE pruned_at IS NULL AND expires_at <= NOW()"
    )


async def register_reward_reconciliation(
    pool, guild_id, source_type, message_id, reason
):
    """Durably queue a human-reviewed reward reversal case.

    Deleting a Discord contribution must never falsely claim that a reward was
    reversed.  Generic rewards have snapshots but restoring one may also erase
    later player progress; legacy rewards have no safe inverse at all.  This
    function therefore creates one idempotent reconciliation case and leaves
    the original grant evidence intact for an administrator to resolve.
    """
    ledger = await pool.fetchrow(
        "SELECT id FROM game_reward_ledger WHERE guild_id=$1 AND source_type=$2 "
        "AND contribution_message_id=$3 AND state='granted'",
        guild_id,
        source_type,
        message_id,
    )
    if ledger is not None:
        reward_kind = "configured"
    elif source_type == "relay_story":
        legacy_rewarded = await pool.fetchval(
            "SELECT 1 FROM guild_game_relay_rewards WHERE guild_id=$1 AND message_id=$2 "
            "UNION ALL SELECT 1 FROM si_relay_rewards WHERE message_id=$2 LIMIT 1",
            guild_id,
            message_id,
        )
        reward_kind = "legacy" if legacy_rewarded else None
    else:
        legacy_rewarded = await pool.fetchval(
            "SELECT 1 FROM si_tomak_rewards WHERE submission_message_id=$1 "
            "UNION ALL SELECT 1 FROM si_kyohoon_rewards WHERE submission_message_id=$1 LIMIT 1",
            message_id,
        )
        reward_kind = "legacy" if legacy_rewarded else None
    if reward_kind is None:
        return None
    await pool.execute(
        """INSERT INTO reward_reconciliation_cases(guild_id,source_type,contribution_message_id,
               reward_ledger_id,reward_kind,reason)
           VALUES($1,$2,$3,$4,$5,$6)
           ON CONFLICT(guild_id,source_type,contribution_message_id) DO NOTHING""",
        guild_id,
        source_type,
        message_id,
        ledger["id"] if ledger else None,
        reward_kind,
        reason,
    )
    return {"ledger_id": ledger["id"] if ledger else None, "reward_kind": reward_kind}


async def get_submission_reward(pool, message_id):
    """존재한다면 운영진 조회용 변경 불가능한 보상 감사 기록을 반환합니다."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT '교훈' AS reward_type, submission_message_id AS message_id, discord_id, "
            "source_thread_id, posted_thread_id, posted_at, details FROM si_kyohoon_rewards "
            "WHERE submission_message_id=$1 "
            "UNION ALL "
            "SELECT '토막상식' AS reward_type, submission_message_id AS message_id, discord_id, "
            "source_thread_id, posted_thread_id, posted_at, details FROM si_tomak_rewards "
            "WHERE submission_message_id=$1",
            message_id,
        )
    return dict(row) if row else None


async def mark_submission_rewarded(pool, submission_type, message_id):
    """지급이 완료된 게시 신청의 보상 완료 시각을 기록합니다."""
    if submission_type == "kyohoon":
        table = "kyohoon_submissions"
    elif submission_type == "tomak":
        table = "tomak_submissions"
    else:
        raise ValueError("알 수 없는 신청 유형입니다.")

    await pool.execute(
        f"UPDATE {table} SET rewarded_at=NOW() "
        "WHERE message_id=$1 AND posted_at IS NOT NULL "
        "AND reward_posted_thread_id IS NOT NULL AND rewarded_at IS NULL",
        message_id,
    )


async def retry_pending_submission_rewards(pool, guild_id, submission_type=None):
    """게시됐지만 지급 완료로 기록되지 않은 보상을 멱등적으로 재시도합니다."""
    queries = {
        "kyohoon": (
            "SELECT user_id AS discord_id, thread_id AS source_thread_id, message_id, "
            "reward_posted_thread_id FROM kyohoon_submissions "
            "WHERE posted_at IS NOT NULL AND rewarded_at IS NULL "
            "AND reward_posted_thread_id IS NOT NULL"
        ),
        "tomak": (
            "SELECT user_id AS discord_id, thread_id AS source_thread_id, message_id, "
            "reward_posted_thread_id FROM tomak_submissions "
            "WHERE posted_at IS NOT NULL AND rewarded_at IS NULL "
            "AND reward_posted_thread_id IS NOT NULL"
        ),
    }
    if submission_type is not None and submission_type not in queries:
        raise ValueError("알 수 없는 신청 유형입니다.")

    types = (submission_type,) if submission_type else tuple(queries)
    summary = {"awarded": 0, "already_awarded": 0, "failed": []}
    for current_type in types:
        for row in await pool.fetch(queries[current_type]):
            try:
                grant = (
                    grant_kyohoon_reward
                    if current_type == "kyohoon"
                    else grant_tomak_reward
                )
                reward = await grant(
                    pool,
                    guild_id,
                    row["discord_id"],
                    row["source_thread_id"],
                    row["message_id"],
                    row["reward_posted_thread_id"],
                )
                if reward["awarded"]:
                    summary["awarded"] += 1
                elif reward.get("already_awarded"):
                    summary["already_awarded"] += 1
                else:
                    summary["failed"].append(
                        (current_type, row["message_id"], "알 수 없는 보상 상태")
                    )
                    continue

                await mark_submission_rewarded(pool, current_type, row["message_id"])
            except Exception as exc:
                summary["failed"].append(
                    (current_type, row["message_id"], f"{type(exc).__name__}: {exc}")
                )

    return summary


async def get_unacknowledged_migration_report(pool, discord_id):
    """존재한다면 플레이어의 변경 불가능한 기존 데이터 전환 보고서를 한 번 반환합니다."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT report FROM si_migration_reports WHERE discord_id=$1 AND acknowledged_at IS NULL",
            discord_id,
        )
        return row["report"] if row else None


async def acknowledge_migration_report(pool, discord_id):
    """Discord가 보고서를 성공적으로 수락한 뒤에만 전달 완료로 표시합니다."""
    await pool.execute(
        "UPDATE si_migration_reports SET acknowledged_at=NOW() "
        "WHERE discord_id=$1 AND acknowledged_at IS NULL",
        discord_id,
    )
