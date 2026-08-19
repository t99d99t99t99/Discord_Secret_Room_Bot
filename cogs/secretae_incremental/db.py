"""시크리타이 인크리멘탈의 트랜잭션 도메인 작업을 제공합니다."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from .constants import *
from .numbers import LayeredDecimal as N, maximum

ZERO, ONE = N.of(0), N.of(1)
MILKY_WAY_BASE = N.of("0.2")
MILKY_WAY_LOG_BONUS = N.of("0.035")


def _amounts(keys, initial):
    """하나의 초기값으로 완전한 자원 모음을 생성합니다."""
    return {key: N.of(initial) for key in keys}


def _encode(values):
    """자원 모음을 JSONB 저장 형식으로 인코딩합니다."""
    return {key: value.to_json() for key, value in values.items()}


def _decode(raw, keys):
    """정확한 자원 키 집합을 검증하고 수량을 디코딩합니다."""
    if not isinstance(raw, dict) or set(raw) != set(keys):
        raise ValueError("손상된 게임 데이터입니다.")

    return {key: N.from_json(raw[key]) for key in keys}


def game_date(now=None):
    """하루 경계가 KST 오전 5시인 게임 날짜를 반환합니다."""
    now = now or datetime.now(timezone.utc)
    kst = now.astimezone(timezone(timedelta(hours=9)))
    return (kst - timedelta(days=1) if kst.hour < 5 else kst).date()


def concentration_week_start(now=None):
    """주간 농축 기간의 월요일 게임 날짜를 반환합니다."""
    current = game_date(now)
    return current - timedelta(days=current.weekday())


def concentration_available(state, now=None):
    """플레이어가 이번 KST 게임 주에 아직 농축하지 않았는지 확인합니다."""
    return state["player"]["last_concentrated_week_start"] != concentration_week_start(
        now
    )


def milky_way_multiplier(total_essence):
    """감소 효율이 적용된 전역 생산 계수를 반환합니다.

    기존의 선형 ``total_essence + 1`` 배율은 큰 서버에서 신규 플레이어가
    한 번의 생산으로 첫 농축을 끝낼 수 있게 했습니다. 로그 계수는 색 비밀
    연쇄로 기하급수적 성장이 일어나지 않도록 하면서 공유 세계의 보상을 유지합니다.
    """
    size = total_essence + ONE
    return MILKY_WAY_BASE + size.ln() * MILKY_WAY_LOG_BONUS


async def ensure_player(conn, discord_id):
    """플레이어가 없으면 기본 자원과 함께 생성합니다."""
    await conn.execute(
        "INSERT INTO si_players(discord_id, shards, essence) VALUES($1, $2, $3) ON CONFLICT DO NOTHING",
        discord_id,
        ONE.to_json(),
        ZERO.to_json(),
    )
    await conn.execute(
        "INSERT INTO si_secrets(discord_id, amounts) VALUES($1, $2) ON CONFLICT DO NOTHING",
        discord_id,
        _encode(_amounts(SECRETS, 100)),
    )
    await conn.execute(
        "INSERT INTO si_organics(discord_id, amounts) VALUES($1, $2) ON CONFLICT DO NOTHING",
        discord_id,
        _encode(_amounts(COLORS, 1)),
    )


async def _state(conn, discord_id, world_lock="FOR SHARE"):
    """항상 세계를 먼저 잠근 뒤 플레이어 상태를 잠그고 디코딩합니다."""
    world = await conn.fetchrow(
        f"SELECT * FROM si_world_state WHERE singleton=TRUE {world_lock}"
    )
    await ensure_player(conn, discord_id)
    player = await conn.fetchrow(
        "SELECT * FROM si_players WHERE discord_id=$1 FOR UPDATE", discord_id
    )
    secrets = await conn.fetchrow(
        "SELECT amounts FROM si_secrets WHERE discord_id=$1 FOR UPDATE", discord_id
    )
    organics = await conn.fetchrow(
        "SELECT amounts FROM si_organics WHERE discord_id=$1 FOR UPDATE", discord_id
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


async def get_status(pool, discord_id):
    """잠긴 일관된 플레이어 게임 상태 스냅샷을 반환합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await _state(conn, discord_id)


ALERT_TYPES = {"disabled", "concentration", "game"}


async def record_game_command(pool, discord_id, now=None):
    """플레이어가 이 게임 날짜에 /게임 명령을 사용했음을 기록합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_player(conn, discord_id)
            await conn.execute(
                "UPDATE si_players SET last_game_command_game_date=$2, updated_at=NOW() WHERE discord_id=$1",
                discord_id,
                game_date(now),
            )


async def save_alert_setting(pool, discord_id, alert_type, alert_hour=None):
    """플레이어별 알림 설정을 저장합니다."""
    if alert_type not in ALERT_TYPES:
        raise ValueError("알 수 없는 알림 유형입니다.")
    if alert_type == "disabled":
        alert_hour = None
    elif not isinstance(alert_hour, int) or not 0 <= alert_hour <= 23:
        raise ValueError("알림 시각은 0시부터 23시 사이여야 합니다.")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_player(conn, discord_id)
            await conn.execute(
                "INSERT INTO si_alert_settings(discord_id, alert_type, alert_hour) VALUES($1, $2, $3) "
                "ON CONFLICT(discord_id) DO UPDATE SET alert_type=EXCLUDED.alert_type, "
                "alert_hour=EXCLUDED.alert_hour, updated_at=NOW()",
                discord_id,
                alert_type,
                alert_hour,
            )


async def due_alert_players(pool, hour, is_concentration_deadline, now=None):
    """주간 마감일을 포함하여 선택한 시각의 알림 대상 플레이어를 반환합니다."""
    current_date = game_date(now)
    current_week = concentration_week_start(now)
    return await pool.fetch(
        "SELECT settings.discord_id, settings.alert_type, "
        "players.last_concentrated_week_start IS DISTINCT FROM $2 AS needs_concentration, "
        "players.last_game_command_game_date IS DISTINCT FROM $3 AS needs_game "
        "FROM si_alert_settings AS settings "
        "JOIN si_players AS players ON players.discord_id=settings.discord_id "
        "WHERE settings.alert_hour=$1 AND ("
        "(settings.alert_type='concentration' AND $4 AND players.last_concentrated_week_start IS DISTINCT FROM $2) OR "
        "(settings.alert_type='game' AND ("
        "players.last_game_command_game_date IS DISTINCT FROM $3 OR "
        "($4 AND players.last_concentrated_week_start IS DISTINCT FROM $2)"
        "))"
        ")",
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


async def synthesize(pool, discord_id, key, amount_text):
    """한 종류의 비밀 묶음을 구매합니다."""
    if not amount_text.isdecimal():
        raise ValueError("수량은 1 이상의 정수여야 합니다.")
    amount = N.of(amount_text)
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _state(conn, discord_id)
            cost = quote_synthesis(state, key, amount)
            if not state["shards"].is_affordable(cost):
                raise ValueError("비밀 파편이 부족합니다.")

            state["shards"] = state["shards"] - cost
            state["secrets"][key] = state["secrets"][key] + amount
            await conn.execute(
                "UPDATE si_players SET shards=$2, updated_at=NOW() WHERE discord_id=$1",
                discord_id,
                state["shards"].to_json(),
            )
            await conn.execute(
                "UPDATE si_secrets SET amounts=$2 WHERE discord_id=$1",
                discord_id,
                _encode(state["secrets"]),
            )
            return cost, state


async def produce(pool, discord_id):
    """KST 기준 하루 생산을 한 번 실행하고 모든 획득량을 함께 저장합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _state(conn, discord_id)
            today = game_date()
            if state["player"]["last_produced_game_date"] == today:
                raise ValueError("오늘은 이미 생산했습니다.")

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
                    / N.of(125)
                    * galaxy
                )
                gain = raw ** (ONE + state["essence"] / N.of(240))
                gain = gain.ceil()
                if color == RED:
                    state["shards"] = state["shards"] + gain
                    shard_gain = gain
                else:
                    previous_color = COLORS[COLORS.index(color) - 1]
                    state["organics"][previous_color] = (
                        state["organics"][previous_color] + gain
                    )
                    organic_gains[previous_color] = gain

            await conn.execute(
                "UPDATE si_players SET shards=$2, last_produced_game_date=$3, updated_at=NOW() WHERE discord_id=$1",
                discord_id,
                state["shards"].to_json(),
                today,
            )
            await conn.execute(
                "UPDATE si_organics SET amounts=$2 WHERE discord_id=$1",
                discord_id,
                _encode(state["organics"]),
            )
            return state, {
                "shards": (before_shards, shard_gain),
                "organics": {
                    color: (before_organics[color], organic_gains[color])
                    for color in COLORS
                },
            }


async def max_synthesize(pool, discord_id):
    """엄격한 우선순위대로 각 비밀의 구매 가능한 최대 묶음을 구매합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _state(conn, discord_id)
            bought = {key: ZERO for key in SECRETS}
            original_shards = state["shards"]
            original_secrets = dict(state["secrets"])

            for key in MAX_SYNTHESIS_ORDER:
                # 지수적 범위 확장과 이진 탐색으로 묶음 크기에 대해 로그 시간에 처리합니다.
                lower, upper = N.of(0), N.of(1)
                while state["shards"] >= quote_synthesis(state, key, upper):
                    lower, upper = upper, upper * N.of(2)

                while True:
                    try:
                        if upper - lower <= ONE:
                            break
                    except ValueError:
                        # 높은 계층에서는 인접한 경계를 정확히 뺄 수 없습니다.
                        # ``lower``는 구매 가능함이 확인된 값이므로 그대로 사용합니다.
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

            if not any(amount.sign for amount in bought.values()):
                raise ValueError("합성할 비밀 파편이 부족합니다.")

            await conn.execute(
                "UPDATE si_players SET shards=$2, updated_at=NOW() WHERE discord_id=$1",
                discord_id,
                state["shards"].to_json(),
            )
            await conn.execute(
                "UPDATE si_secrets SET amounts=$2 WHERE discord_id=$1",
                discord_id,
                _encode(state["secrets"]),
            )
            return state, {
                "shards": (original_shards, original_shards - state["shards"]),
                "secrets": {
                    key: (original_secrets[key], bought[key]) for key in SECRETS
                },
            }


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


async def concentrate(pool, discord_id):
    """정수를 지급하고 플레이어의 비정수 자원을 원자적으로 초기화합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _state(conn, discord_id, "FOR UPDATE")
            if not concentration_available(state):
                raise ValueError("농축은 매주 월요일 오전 5시(KST)에 초기화됩니다.")

            gain = concentration_gain(state)
            if not gain.sign:
                raise ValueError("농축으로 얻을 정수가 없습니다.")

            new_essence = state["essence"] + gain
            state["total"] = state["total"] + gain
            state["highest"] = maximum(state["highest"], new_essence)
            await conn.execute(
                "UPDATE si_players SET shards=$2, essence=$3, last_concentrated_week_start=$4, "
                "updated_at=NOW() WHERE discord_id=$1",
                discord_id,
                ONE.to_json(),
                new_essence.to_json(),
                concentration_week_start(),
            )
            await conn.execute(
                "UPDATE si_secrets SET amounts=$2 WHERE discord_id=$1",
                discord_id,
                _encode(_amounts(SECRETS, 100)),
            )
            await conn.execute(
                "UPDATE si_organics SET amounts=$2 WHERE discord_id=$1",
                discord_id,
                _encode(_amounts(COLORS, 1)),
            )
            await conn.execute(
                "UPDATE si_world_state SET total_essence=$1, highest_essence=$2, "
                "updated_at=NOW() WHERE singleton=TRUE",
                state["total"].to_json(),
                state["highest"].to_json(),
            )
            return gain, state["essence"], new_essence


async def grant_kyohoon_reward(
    pool, discord_id, source_thread_id, message_id, posted_thread_id
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

            state = await _state(conn, discord_id, "FOR UPDATE")
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
                "UPDATE si_players SET essence=$2, updated_at=NOW() WHERE discord_id=$1",
                discord_id,
                after.to_json(),
            )
            await conn.execute(
                "UPDATE si_world_state SET total_essence=$1, highest_essence=$2, updated_at=NOW() WHERE singleton=TRUE",
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
    pool, discord_id, source_thread_id, message_id, posted_thread_id
):
    """게시된 토막상식에 대해 모든 비밀을 한 번 5% 증가시킵니다."""
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

            state = await _state(conn, discord_id)
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
                "UPDATE si_secrets SET amounts=$2 WHERE discord_id=$1",
                discord_id,
                _encode(after),
            )
            await conn.execute(
                "UPDATE si_tomak_rewards SET details=$2 WHERE submission_message_id=$1",
                message_id,
                details,
            )
            return {"awarded": True, **details}


async def grant_relay_secret_reward(pool, discord_id, message_id):
    """다른 작성자가 릴레이를 이을 때까지 토막상식 방식 보상을 한 번 적용합니다.

    ``last_rewarded_discord_id``는 Discord 메시지가 삭제되어도 유지됩니다. 따라서
    플레이어는 보상받은 메시지를 삭제한 뒤 다른 사람이 이야기를 잇기 전에 다시
    작성하여 두 번째 보상을 얻을 수 없습니다.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO si_relay_turn_state(singleton) VALUES(TRUE) ON CONFLICT(singleton) DO NOTHING"
            )
            state = await _state(conn, discord_id)
            turn = await conn.fetchrow(
                "SELECT last_rewarded_discord_id FROM si_relay_turn_state WHERE singleton=TRUE FOR UPDATE"
            )
            if turn["last_rewarded_discord_id"] == discord_id:
                return {"awarded": False, "reason": "waiting_for_another_author"}

            inserted = await conn.fetchval(
                "INSERT INTO si_relay_rewards(message_id, discord_id) VALUES($1, $2) "
                "ON CONFLICT(message_id) DO NOTHING RETURNING 1",
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
                "UPDATE si_secrets SET amounts=$2 WHERE discord_id=$1",
                discord_id,
                _encode(after),
            )
            await conn.execute(
                "UPDATE si_relay_rewards SET details=$2 WHERE message_id=$1",
                message_id,
                details,
            )
            await conn.execute(
                "UPDATE si_relay_turn_state SET last_rewarded_discord_id=$1, updated_at=NOW() WHERE singleton=TRUE",
                discord_id,
            )
            return {"awarded": True, **details}


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


async def retry_pending_submission_rewards(pool, submission_type=None):
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
