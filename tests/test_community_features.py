import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from cogs.notice_queues import NoticeQueueService
    from cogs.relay_stories import RelayStoryService
    from cogs.secretae_incremental.db import register_reward_reconciliation
    from cogs.secretae_incremental.numbers import LayeredDecimal as N

    FEATURE_DEPS_AVAILABLE = True
except ModuleNotFoundError:
    # Number tests remain runnable in the lightweight source-check environment;
    # these adapter tests run whenever declared dependencies are installed.
    FEATURE_DEPS_AVAILABLE = False


class _RewardDb:
    def __init__(self, ledger=None, legacy=False):
        self.ledger, self.legacy, self.calls = ledger, legacy, []

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self.ledger

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return 1 if self.legacy else None

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))


@unittest.skipUnless(
    FEATURE_DEPS_AVAILABLE, "Discord/asyncpg dependencies are not installed"
)
class CommunityFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_channel_cannot_be_reused_by_a_story(self):
        """A channel must have one feature owner, preventing dual listeners."""

        class ConflictDb:
            async def fetchval(self, query, *args):
                return 17 if "FROM relay_stories" in query else None

        permissions = SimpleNamespace(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            create_public_threads=True,
        )
        guild = SimpleNamespace(id=10, me=object())
        channel = SimpleNamespace(
            guild=guild, permissions_for=lambda member: permissions
        )
        service = object.__new__(NoticeQueueService)
        service.bot = SimpleNamespace(
            db=ConflictDb(), get_channel=lambda channel_id: channel
        )

        with self.assertRaisesRegex(ValueError, "이미 사용"):
            await service._validate_channel(10, 20, "submission")

    async def test_story_channel_cannot_be_reused_by_a_queue(self):
        """Relay validation applies the same ownership rule in reverse."""

        class ConflictDb:
            async def fetchval(self, query, *args):
                return 17 if "FROM notice_queues" in query else None

        permissions = SimpleNamespace(
            view_channel=True, send_messages=True, read_message_history=True
        )
        guild = SimpleNamespace(id=10, me=object())
        channel = SimpleNamespace(
            guild=guild, permissions_for=lambda member: permissions
        )
        service = object.__new__(RelayStoryService)
        service.bot = SimpleNamespace(
            db=ConflictDb(), get_channel=lambda channel_id: channel
        )

        with self.assertRaisesRegex(ValueError, "already used"):
            await service.validate_story_channel(10, 20)

    def test_submission_validation_defaults_and_rules(self):
        unrestricted = {
            "min_content_length": None,
            "max_content_length": None,
            "allow_attachments": True,
            "allow_links": True,
        }
        self.assertIsNone(
            NoticeQueueService._invalid_submission_reason(
                unrestricted, "https://example.test", True
            )
        )
        constrained = {
            "min_content_length": 3,
            "max_content_length": 5,
            "allow_attachments": False,
            "allow_links": False,
        }
        self.assertEqual(
            NoticeQueueService._invalid_submission_reason(constrained, "x", False),
            "최소 3자",
        )
        self.assertEqual(
            NoticeQueueService._invalid_submission_reason(
                constrained, "x", False, "en"
            ),
            "at least 3 characters are required",
        )
        self.assertEqual(
            NoticeQueueService._invalid_submission_reason(constrained, "123456", False),
            "최대 5자",
        )
        self.assertEqual(
            NoticeQueueService._invalid_submission_reason(constrained, "123", True),
            "첨부파일 허용 안 됨",
        )
        self.assertEqual(
            NoticeQueueService._invalid_submission_reason(
                constrained, "https://x", False
            ),
            "최대 5자",
        )

    def test_publication_templates_keep_content_and_attribution(self):
        queue = {
            "display_name": "Weekly Notes",
            "title_template": "{{queue_name}} — {{author}}",
            "content_template": "By {{author}}:\\n{{content}}",
        }
        self.assertEqual(
            NoticeQueueService._render_publication(queue, "Ada", "A contribution"),
            ("Weekly Notes — Ada", "By Ada:\\nA contribution"),
        )

    def test_publication_template_formats_local_date_and_sunday_week(self):
        from datetime import datetime, timezone

        queue = {
            "display_name": "Weekly Notes",
            "timezone": "Asia/Seoul",
            "title_template": "{{YYYY}} {{YY}} {{MM}} {{M}} {{DD}} {{D}} / {{week}} / {{queue_name}} / {{author}}",
            "content_template": "{{content}}",
        }
        published_at = datetime(2026, 10, 10, 15, 30, tzinfo=timezone.utc)

        self.assertEqual(
            NoticeQueueService._render_publication(
                queue, "Ada", "A contribution", published_at
            ),
            (
                "2026 26 10 10 11 11 / 2 / Weekly Notes / Ada",
                "A contribution",
            ),
        )

    def test_publication_title_drops_separator_when_author_is_hidden(self):
        from datetime import datetime, timezone

        queue = {
            "display_name": "토막상식",
            "timezone": "Asia/Seoul",
            "title_template": "{{YYYY}}년 {{MM}}월 {{DD}}일 토막상식, {{author}}",
            "content_template": "{{content}}",
        }
        published_at = datetime(2026, 10, 11, 0, 0, tzinfo=timezone.utc)

        title, _ = NoticeQueueService._render_publication(
            queue, "", "내용", published_at
        )

        self.assertEqual(title, "2026년 10월 11일 토막상식")

    def test_long_publication_content_is_split_without_loss(self):
        from cogs.notice_queues import split_discord_message_content

        content = "x" * 4_001
        chunks = split_discord_message_content(content)

        self.assertEqual([len(chunk) for chunk in chunks], [2_000, 2_000, 1])
        self.assertEqual("".join(chunks), content)

    async def test_reply_moderation_is_not_captured_as_queue_or_story_content(self):
        class FailDb:
            async def fetchrow(self, *args):
                raise AssertionError(
                    "reply moderation must not query contribution state"
                )

        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            guild=object(),
            reference=object(),
            content="!moderate reject",
            channel=object(),
        )
        queue_service = object.__new__(NoticeQueueService)
        queue_service.bot = SimpleNamespace(db=FailDb())
        with patch("cogs.notice_queues.discord.Thread", object):
            await NoticeQueueService.on_message(queue_service, message)
        story_service = object.__new__(RelayStoryService)
        story_service.bot = SimpleNamespace(db=FailDb())
        await RelayStoryService.on_message(story_service, message)

    def test_legacy_reward_wording_is_preserved(self):
        result = {
            "before_essence": N.of(0).to_json(),
            "after_essence": N.of(1).to_json(),
            "reward_amount": N.of(1).to_json(),
        }
        dm, public = NoticeQueueService._reward_messages(
            "legacy_kyohoon", result, "Ada"
        )
        self.assertTrue(dm.startswith("이야기의 정수 보상을 받았습니다."))
        self.assertTrue(
            public.startswith(
                "Ada 님이 이 교훈을 게시하여 이야기의 정수 보상을 받았습니다."
            )
        )
        self.assertIn("기존 정수가 0이면", dm)

    async def test_configured_reward_deletion_creates_a_linked_case(self):
        db = _RewardDb(ledger={"id": 44})
        case = await register_reward_reconciliation(
            db, 10, "relay_story", 99, "deleted"
        )
        self.assertEqual(case, {"ledger_id": 44, "reward_kind": "configured"})
        execute = [call for call in db.calls if call[0] == "execute"]
        self.assertEqual(len(execute), 1)
        self.assertEqual(
            execute[0][2], (10, "relay_story", 99, 44, "configured", "deleted")
        )

    async def test_unrewarded_deletion_does_not_create_a_case(self):
        db = _RewardDb()
        case = await register_reward_reconciliation(
            db, 10, "relay_story", 99, "deleted"
        )
        self.assertIsNone(case)
        self.assertFalse(any(call[0] == "execute" for call in db.calls))
