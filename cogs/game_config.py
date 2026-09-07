"""Guild game feature flags and typed community-reward policy validation."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


REWARD_TYPES = {"none", "essence_multiplier", "each_secret_multiplier", "production_multiplier", "fixed_essence"}


async def game_enabled(pool, guild_id: int) -> bool:
    enabled = await pool.fetchval("SELECT game_enabled FROM guild_settings WHERE guild_id=$1", guild_id)
    return bool(enabled)


def reward_policy_label(policy: dict, locale: str = "ko") -> str:
    """Return an administrator/member-facing policy summary in ``locale``."""
    kind = policy.get("type", "none")
    names = {
        "ko": {
            "none": "보상 없음", "essence_multiplier": "정수 배율", "each_secret_multiplier": "모든 비밀 배율",
            "production_multiplier": "생산 배율", "fixed_essence": "고정 정수",
            "legacy_kyohoon": "정수: 0이면 1, 그 외 ×2", "legacy_tomak": "모든 비밀 ×2",
            "legacy_secret_multiplier": "모든 비밀 ×1.05",
        },
        "en": {
            "none": "No reward", "essence_multiplier": "Essence multiplier", "each_secret_multiplier": "Each-secret multiplier",
            "production_multiplier": "Production multiplier", "fixed_essence": "Fixed essence",
            "legacy_kyohoon": "Essence: 1 from zero, otherwise ×2", "legacy_tomak": "Each secret ×2",
            "legacy_secret_multiplier": "Each secret ×1.05",
        },
    }["ko" if locale == "ko" else "en"]
    if kind == "none":
        return names[kind]
    if kind.startswith("legacy_"):
        return names[kind]
    text = f"{names.get(kind, kind)} {policy.get('amount', '')}".strip()
    if policy.get("first_grant_amount"):
        text += f" (첫 정수: {policy['first_grant_amount']})" if locale == "ko" else f" (first essence: {policy['first_grant_amount']})"
    if policy.get("weekly_cap"):
        text += f" (주 {policy['weekly_cap']}회 한도)" if locale == "ko" else f" (weekly cap: {policy['weekly_cap']})"
    return text


def reward_policy(policy_type: str, amount: str | None = None, first_grant_amount: str | None = None, weekly_cap: int | None = None) -> dict:
    if policy_type not in REWARD_TYPES:
        raise ValueError("admin.reward_policy.invalid_type")
    if policy_type == "none":
        return {"type": "none"}
    if amount is None:
        raise ValueError("admin.reward_policy.amount_required")
    try:
        numeric = Decimal(amount)
    except InvalidOperation as exc:
        raise ValueError("admin.reward_policy.amount_invalid") from exc
    upper = Decimal("3") if policy_type == "production_multiplier" else Decimal("2")
    lower = Decimal("1") if policy_type == "production_multiplier" else Decimal("1.1")
    if policy_type == "fixed_essence":
        if numeric <= 0:
            raise ValueError("admin.reward_policy.fixed_positive")
    elif not lower <= numeric <= upper:
        raise ValueError("admin.reward_policy.range")
    result = {"type": policy_type, "amount": str(numeric)}
    if first_grant_amount is not None:
        try:
            first = Decimal(first_grant_amount)
        except InvalidOperation as exc:
            raise ValueError("admin.reward_policy.first_invalid") from exc
        if first <= 0:
            raise ValueError("admin.reward_policy.first_invalid")
        result["first_grant_amount"] = str(first)
    if weekly_cap is not None:
        if not isinstance(weekly_cap, int) or not 1 <= weekly_cap <= 7:
            raise ValueError("admin.reward_policy.cap_invalid")
        result["weekly_cap"] = weekly_cap
    return result
