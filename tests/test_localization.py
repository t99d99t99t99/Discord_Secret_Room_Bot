import unittest
from pathlib import Path
import re

from cogs.localization import CommandTranslator, normalize_locale, text, validate_catalogs
from cogs.secretae_incremental.game import _amount_table
from cogs.secretae_incremental.numbers import LayeredDecimal as N


ROOT = Path(__file__).resolve().parent.parent


class LocalizationTests(unittest.TestCase):
    def test_shipped_catalogs_have_identical_keys_and_placeholders(self):
        validate_catalogs()

    def test_korean_and_english_render_the_same_template_contract(self):
        self.assertIn("Essence Foundry", text("game.help", "en", game_name="Essence Foundry"))
        self.assertIn("정수 공방", text("game.help", "ko", game_name="정수 공방"))
        self.assertEqual("Notice queue", text("game.feature.queue", "en"))
        self.assertEqual("공지 대기열", text("game.feature.queue", "ko"))

    def test_locale_normalization_has_a_safe_english_fallback(self):
        self.assertEqual("ko", normalize_locale("ko-KR"))
        self.assertEqual("en", normalize_locale("en-US"))
        self.assertEqual("en", normalize_locale(None))
        self.assertEqual("en", normalize_locale("unsupported"))

    def test_command_metadata_has_registered_locale_overrides(self):
        translator = CommandTranslator()
        self.assertEqual(
            "CommunityNoticeBot을 구성하고 운영합니다.",
            translator._copy["Configure and operate CommunityNoticeBot."]["ko"],
        )
        self.assertEqual(
            "Manage relay stories.",
            translator._copy["릴레이 이야기를 관리합니다."]["en"],
        )

    def test_all_registered_command_descriptions_have_the_other_locale(self):
        """Discord metadata is separate from runtime messages and needs its own catalog."""
        description = re.compile(r'description="([^"]+)"')
        korean = re.compile(r"[가-힣]")
        descriptions = []
        for source in (ROOT / "cogs/admin.py", ROOT / "cogs/secretae_incremental/game.py"):
            descriptions.extend(description.findall(source.read_text()))
        for value in descriptions:
            with self.subTest(description=value):
                locale = "en" if korean.search(value) else "ko"
                self.assertIn(value, CommandTranslator._copy)
                self.assertIn(locale, CommandTranslator._copy[value])

    def test_game_result_table_uses_selected_locale(self):
        table = _amount_table([("★", N.of(3), N.of(1), N.of(2))], "Production", "en")
        self.assertIn("Result", table)
        self.assertIn("Before", table)
