from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent.parent


class DocumentationTests(unittest.TestCase):
    def test_public_guides_ship_korean_and_english_versions(self):
        pairs = (
            ("readme.md", "readme.ko.md"),
            ("admin_commands.md", "admin_commands.ko.md"),
            ("document/secretae-incremental.en.md", "document/secretae-incremental.md"),
            ("document/community-features.en.md", "document/community-features.md"),
            ("document/migration-recovery.md", "document/migration-recovery.ko.md"),
            ("document/release-checklist.md", "document/release-checklist.ko.md"),
            ("document/localization-review.md", "document/localization-review.ko.md"),
        )
        for english, korean in pairs:
            with self.subTest(english=english, korean=korean):
                self.assertTrue((ROOT / english).is_file())
                self.assertTrue((ROOT / korean).is_file())

    def test_onboarding_guides_link_the_release_checklists(self):
        self.assertIn("document/release-checklist.md", (ROOT / "readme.md").read_text())
        self.assertIn("document/release-checklist.ko.md", (ROOT / "readme.ko.md").read_text())

    def test_release_checklists_link_the_localization_review_process(self):
        self.assertIn("localization-review.md", (ROOT / "document/release-checklist.md").read_text())
        self.assertIn("localization-review.ko.md", (ROOT / "document/release-checklist.ko.md").read_text())

    def test_document_pairs_reference_the_same_commands(self):
        """A translated guide must not silently omit an operational workflow."""
        pairs = (
            ("readme.md", "readme.ko.md"),
            ("admin_commands.md", "admin_commands.ko.md"),
            ("document/secretae-incremental.en.md", "document/secretae-incremental.md"),
            ("document/community-features.en.md", "document/community-features.md"),
            ("document/migration-recovery.md", "document/migration-recovery.ko.md"),
            ("document/release-checklist.md", "document/release-checklist.ko.md"),
        )
        command = re.compile(r"`(/[^`\s]+)")
        for english, korean in pairs:
            with self.subTest(english=english, korean=korean):
                english_commands = set(command.findall((ROOT / english).read_text()))
                korean_commands = set(command.findall((ROOT / korean).read_text()))
                self.assertSetEqual(english_commands, korean_commands)

    def test_migration_guides_cover_each_required_operational_phase(self):
        required = {
            "document/migration-recovery.md": ("Before cutover", "Acceptance window", "Recovery", "Guild-scoped game verification"),
            "document/migration-recovery.ko.md": ("전환 전", "검증 기간", "복구", "길드 범위 게임 검증"),
        }
        for guide, headings in required.items():
            body = (ROOT / guide).read_text()
            for heading in headings:
                with self.subTest(guide=guide, heading=heading):
                    self.assertIn(f"## {heading}", body)
