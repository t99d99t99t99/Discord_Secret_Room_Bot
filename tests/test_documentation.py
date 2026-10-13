from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DOCUMENT_PAIRS = (
    ("readme.md", "readme.ko.md"),
    ("admin_commands.md", "admin_commands.ko.md"),
    ("document/getting-started.md", "document/getting-started.ko.md"),
    ("document/codebase.md", "document/codebase.ko.md"),
    ("document/secretae-incremental.md", "document/secretae-incremental.ko.md"),
    ("document/community-features.md", "document/community-features.ko.md"),
    ("document/migration-recovery.md", "document/migration-recovery.ko.md"),
    ("document/release-checklist.md", "document/release-checklist.ko.md"),
    ("document/localization-review.md", "document/localization-review.ko.md"),
)


class DocumentationTests(unittest.TestCase):
    def test_public_guides_ship_korean_and_english_versions(self):
        for english, korean in DOCUMENT_PAIRS:
            with self.subTest(english=english, korean=korean):
                self.assertTrue((ROOT / english).is_file())
                self.assertTrue((ROOT / korean).is_file())

    def test_every_guide_starts_with_buttons_to_its_language_pair(self):
        documented = {ROOT / filename for pair in DOCUMENT_PAIRS for filename in pair}
        public_guides = (set(ROOT.glob("*.md")) - {ROOT / "AGENTS.md"}) | set(
            (ROOT / "document").rglob("*.md")
        )
        self.assertSetEqual(documented, public_guides)
        for english, korean in DOCUMENT_PAIRS:
            expected = (
                f'<a href="{Path(english).name}"><kbd>English</kbd></a> '
                f'<a href="{Path(korean).name}"><kbd>한국어</kbd></a>'
            )
            for filename in (english, korean):
                with self.subTest(guide=filename):
                    self.assertEqual(
                        (ROOT / filename).read_text().splitlines()[0], expected
                    )

    def test_document_links_resolve_after_reorganization(self):
        markdown_link = re.compile(r"\[[^\]]+\]\(([^\s)]+)\)")
        html_link = re.compile(r'href="([^"]+)"')
        for pair in DOCUMENT_PAIRS:
            for filename in pair:
                source = ROOT / filename
                body = source.read_text()
                for target in markdown_link.findall(body) + html_link.findall(body):
                    parsed = urlsplit(target)
                    if parsed.scheme or parsed.netloc or not parsed.path:
                        continue
                    with self.subTest(guide=filename, target=target):
                        self.assertTrue((source.parent / unquote(parsed.path)).exists())

    def test_onboarding_guides_link_the_release_checklists(self):
        self.assertIn("document/release-checklist.md", (ROOT / "readme.md").read_text())
        self.assertIn(
            "document/release-checklist.ko.md", (ROOT / "readme.ko.md").read_text()
        )

    def test_release_checklists_link_the_localization_review_process(self):
        self.assertIn(
            "localization-review.md",
            (ROOT / "document/release-checklist.md").read_text(),
        )
        self.assertIn(
            "localization-review.ko.md",
            (ROOT / "document/release-checklist.ko.md").read_text(),
        )

    def test_document_pairs_reference_the_same_commands(self):
        """A translated guide must not silently omit an operational workflow."""
        command = re.compile(r"`(/[^`\s]+)")
        # Command names are localized by Discord, so compare their stable roots.
        localized_roots = {"/관리": "/admin", "/게임": "/game"}
        for english, korean in DOCUMENT_PAIRS:
            with self.subTest(english=english, korean=korean):
                english_commands = {
                    localized_roots.get(root, root)
                    for root in command.findall((ROOT / english).read_text())
                }
                korean_commands = {
                    localized_roots.get(root, root)
                    for root in command.findall((ROOT / korean).read_text())
                }
                self.assertSetEqual(english_commands, korean_commands)

    def test_migration_guides_cover_each_required_operational_phase(self):
        required = {
            "document/migration-recovery.md": (
                "Before cutover",
                "Acceptance window",
                "Recovery",
                "Guild-scoped game verification",
            ),
            "document/migration-recovery.ko.md": (
                "전환 전",
                "검증 기간",
                "복구",
                "길드 범위 게임 검증",
            ),
        }
        for guide, headings in required.items():
            body = (ROOT / guide).read_text()
            for heading in headings:
                with self.subTest(guide=guide, heading=heading):
                    self.assertIn(f"## {heading}", body)
