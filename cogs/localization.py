"""Small, strict locale catalog service for member and administrator copy.

Discord application-command names/descriptions are registered before an
interaction and therefore cannot be selected per user at runtime.  Runtime
responses, DMs, notices, and help text must use this module instead of embedded
copy.  The configured guild locale wins; a Discord-provided user/guild locale is
used only while no configured setting is available; English is the fallback.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping

import yaml
from discord import Locale, app_commands


CATALOG_DIRECTORY = Path(__file__).resolve().parent.parent / "messages"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = frozenset({"en", "ko"})
_PLACEHOLDER = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def normalize_locale(value: object | None) -> str:
    """Return a supported locale code, accepting Discord locale values."""
    text = getattr(value, "value", value)
    if isinstance(text, str) and text.lower().replace("_", "-").startswith("ko"):
        return "ko"
    return "en"


@lru_cache(maxsize=1)
def catalogs() -> dict[str, dict[str, str]]:
    """Load and validate every shipped catalog once per process."""
    loaded: dict[str, dict[str, str]] = {}
    for locale in SUPPORTED_LOCALES:
        path = CATALOG_DIRECTORY / f"{locale}.yml"
        with path.open(encoding="utf-8") as source:
            catalog = yaml.safe_load(source)
        if not isinstance(catalog, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in catalog.items()
        ):
            raise ValueError(f"Locale catalog {path} must be a string-key/string-value mapping")
        loaded[locale] = catalog
    validate_catalogs(loaded)
    return loaded


def validate_catalogs(loaded: Mapping[str, Mapping[str, str]] | None = None) -> None:
    """Reject missing keys and template-placeholder drift before runtime."""
    loaded = catalogs() if loaded is None else loaded
    reference = loaded[DEFAULT_LOCALE]
    for locale, catalog in loaded.items():
        missing, extra = set(reference) - set(catalog), set(catalog) - set(reference)
        if missing or extra:
            raise ValueError(f"Locale {locale} key mismatch; missing={sorted(missing)}, extra={sorted(extra)}")
        for key, english in reference.items():
            expected, actual = set(_PLACEHOLDER.findall(english)), set(_PLACEHOLDER.findall(catalog[key]))
            if expected != actual:
                raise ValueError(f"Locale {locale} placeholder mismatch for {key}: {actual} != {expected}")


def text(key: str, locale: object | None = None, /, **values: Any) -> str:
    """Format one catalog entry; a missing key is a programming error, not copy."""
    selected = normalize_locale(locale)
    try:
        template = catalogs()[selected][key]
    except KeyError as exc:
        raise KeyError(f"Unknown localization key: {key}") from exc
    return template.format(**values)


async def guild_locale(db, guild_id: int, discord_locale: object | None = None) -> str:
    """Resolve configured guild locale, then Discord locale, then English."""
    configured = await db.fetchval("SELECT locale FROM guild_settings WHERE guild_id=$1", guild_id)
    return normalize_locale(configured if configured in SUPPORTED_LOCALES else discord_locale)


async def interaction_locale(interaction) -> str:
    """Use user locale for a private response only if server configuration is absent."""
    if interaction.guild is None:
        return normalize_locale(getattr(interaction, "locale", None))
    return await guild_locale(
        interaction.client.db,
        interaction.guild.id,
        getattr(interaction, "locale", None) or getattr(interaction.guild, "preferred_locale", None),
    )


class CommandTranslator(app_commands.Translator):
    """Translate registered command metadata where Discord asks for a locale.

    Runtime copy must use :func:`text`; this hook covers the separate
    application-command registration protocol. Unmapped text intentionally uses
    Discord's default-language value rather than an internal identifier.
    """

    _copy = {
        "Configure and operate CommunityNoticeBot.": {"ko": "CommunityNoticeBot을 구성하고 운영합니다."},
        "Guided server setup.": {"ko": "안내형 서버 설정입니다."},
        "Operate imported notice queues.": {"ko": "공지 대기열을 운영합니다."},
        "릴레이 이야기를 관리합니다.": {"en": "Manage relay stories."},
        "Essence Foundry 게임을 관리합니다.": {"en": "Manage the Essence Foundry game."},
        "Inspect configuration and legacy migration state.": {"ko": "구성과 레거시 마이그레이션 상태를 확인합니다."},
        "Start or resume guided setup.": {"ko": "안내형 설정을 시작하거나 다시 엽니다."},
        "Show setup progress and disabled optional features.": {"ko": "설정 진행도와 비활성 선택 기능을 확인합니다."},
        "Set the server's default language.": {"ko": "서버 기본 언어를 설정합니다."},
        "Allow a role to administer the bot.": {"ko": "역할에 봇 관리 권한을 부여합니다."},
        "Set the optional administrator audit destination.": {"ko": "선택 사항인 관리자 감사 대상을 설정합니다."},
        "Configure queue/story moderation authority.": {"ko": "공지/릴레이 검수 권한을 설정합니다."},
        "게임 활성화 상태와 구성된 보상을 확인합니다.": {"en": "View game enablement and configured rewards."},
        "이 서버의 게임을 활성화합니다.": {"en": "Enable this server's game."},
        "이 서버의 게임을 비활성화합니다.": {"en": "Disable this server's game."},
        "서버에서 표시할 게임 이름을 설정합니다.": {"en": "Set the game name shown in this server."},
        "공지 대기열 또는 릴레이 이야기의 보상 정책을 설정합니다.": {"en": "Set the reward policy for a notice queue or relay story."},
        "지급 직전 스냅샷으로 플레이어 상태를 전체 되돌립니다.": {"en": "Restore a player's entire state to the pre-reward snapshot."},
        "지급·스냅샷·조정 상태를 검토합니다.": {"en": "Review reward, snapshot, and reconciliation state."},
        "삭제·부적격 기여의 보상 검토 대기 목록을 봅니다.": {"en": "View pending reward reviews for deleted or disqualified contributions."},
        "외부/수동 보상 조정을 감사 기록으로 완료 처리합니다.": {"en": "Record completion of an external or manual reward adjustment."},
        "Create an active notice queue with safe defaults.": {"ko": "안전한 기본값으로 활성 공지 대기열을 만듭니다."},
        "List this server's notice queues.": {"ko": "이 서버의 공지 대기열을 표시합니다."},
        "View notice queue configuration and health.": {"ko": "공지 대기열 설정과 상태를 확인합니다."},
        "Edit queue name, description, or accepting state.": {"ko": "대기열 이름, 설명 또는 신청 접수 상태를 변경합니다."},
        "Configure selection, schedule, limits, attribution, and template.": {"ko": "선택, 일정, 제한, 출처 표시, 서식을 설정합니다."},
        "Redirect submission/publication channels after confirmation.": {"ko": "확인 후 신청/게시 채널을 변경합니다."},
        "신청 내용, 수정, 첨부파일, 링크 정책을 설정합니다.": {"en": "Configure submission content, edits, attachments, and link policy."},
        "Pause a notice queue.": {"ko": "공지 대기열을 일시 정지합니다."},
        "Resume a paused notice queue.": {"ko": "일시 정지한 공지 대기열을 재개합니다."},
        "Archive a queue after confirmation; submissions are retained.": {"ko": "확인 후 대기열을 보관합니다. 신청 기록은 유지됩니다."},
        "Open a new submission thread.": {"ko": "새 신청 스레드를 엽니다."},
        "Publish the oldest eligible entry now (confirmation required).": {"ko": "가장 오래된 적격 신청을 지금 게시합니다(확인 필요)."},
        "Return a failed/selected submission to eligibility for safe retry.": {"ko": "실패 또는 선택된 신청을 안전하게 재시도할 수 있도록 적격 상태로 돌립니다."},
        "Reject or remove an eligible submission; moderators may use this.": {"ko": "적격 신청을 거절 또는 제거합니다. 검수자가 사용할 수 있습니다."},
        "새 릴레이 이야기를 만듭니다.": {"en": "Create a new relay story."},
        "이 서버의 릴레이 이야기를 표시합니다.": {"en": "List this server's relay stories."},
        "릴레이 이야기 설정과 상태를 확인합니다.": {"en": "View relay-story configuration and state."},
        "릴레이 이야기 이름, 채널 또는 규칙을 변경합니다.": {"en": "Edit a relay story's name, channel, or rules."},
        "관리자가 제공한 이야기 규칙을 채널에 고정합니다.": {"en": "Pin administrator-supplied story rules in the channel."},
        "기여 순서와 선택적 글자 수 제한을 설정합니다.": {"en": "Configure contribution order and optional character limit."},
        "이 이야기 전용 운영진 정책을 설정합니다.": {"en": "Configure this story's dedicated moderator policy."},
        "릴레이 이야기를 일시 정지합니다.": {"en": "Pause a relay story."},
        "릴레이 이야기를 다시 시작합니다.": {"en": "Resume a relay story."},
        "릴레이 이야기를 보관 처리합니다. 기여 기록은 삭제하지 않습니다.": {"en": "Archive a relay story without deleting contribution history."},
        "릴레이 기여를 삭제하고 부적격 처리합니다.": {"en": "Delete and disqualify a relay contribution."},
        "Look up an imported notice reward by submission message ID.": {"ko": "제출 메시지 ID로 가져온 공지 보상을 찾습니다."},
        "가져온 공지 대기열과 릴레이 이야기 상태를 확인합니다.": {"en": "Inspect imported notice-queue and relay-story state."},
        "이 서버의 증분 게임": {"en": "This server's incremental game"},
        "이 서버의 게임 안내를 봅니다.": {"en": "View this server's game guide."},
        "이 서버의 공지·릴레이 게임 보상을 확인합니다.": {"en": "View this server's notice and relay game rewards."},
        "게임 알림을 설정합니다.": {"en": "Configure game alerts."},
        "현재 게임 현황을 봅니다.": {"en": "View current game status."},
        "오늘의 비밀 유기체 생산을 실행합니다.": {"en": "Run today's secret-organism production."},
        "비밀 파편으로 비밀을 합성합니다.": {"en": "Synthesize secrets with secret shards."},
        "현재 비밀 합성 가격을 확인합니다.": {"en": "View current secret synthesis prices."},
        "우선순위에 따라 가능한 비밀을 최대 합성합니다.": {"en": "Synthesize the maximum possible secrets in priority order."},
        "초기화하고 이야기의 정수를 얻습니다.": {"en": "Reset run resources and gain story essence."},
        "Disabled": {"ko": "비활성화"},
        "Manage Messages permission": {"ko": "메시지 관리 권한"},
        "One role": {"ko": "역할 하나"},
        "Daily": {"ko": "일일"},
        "Weekly": {"ko": "주간"},
        "Manual only": {"ko": "수동만"},
        "Oldest eligible": {"ko": "가장 오래된 적격 신청"},
        "Fair rotation": {"ko": "공정 순환"},
        "Random": {"ko": "무작위"},
        "Display name": {"ko": "표시 이름"},
        "No attribution": {"ko": "출처 표시 안 함"},
        "Reject": {"ko": "거절"},
        "Disqualify": {"ko": "부적격"},
        "Skip": {"ko": "건너뛰기"},
        "서버 기본 정책": {"en": "Server default policy"},
        "메시지 관리 권한": {"en": "Manage Messages permission"},
        "역할 하나": {"en": "One role"},
        "운영진 알림 및 새 스레드": {"en": "Alert moderators and open a new thread"},
        "운영진 알림만": {"en": "Alert moderators only"},
        "건너뛰기": {"en": "Skip"},
        "공지 대기열": {"en": "Notice queue"},
        "릴레이 이야기": {"en": "Relay story"},
        "보상 없음": {"en": "No reward"},
        "정수 배율": {"en": "Essence multiplier"},
        "모든 비밀 배율": {"en": "All-secret multiplier"},
        "생산 배율": {"en": "Production multiplier"},
        "고정 정수": {"en": "Fixed essence"},
    }

    async def translate(self, string: app_commands.locale_str, locale: Locale, context):
        return self._copy.get(string.message, {}).get(normalize_locale(locale))
