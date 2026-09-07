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


@unittest.skipUnless(FEATURE_DEPS_AVAILABLE, "Discord/asyncpg dependencies are not installed")
class CommunityFeatureTests(unittest.IsolatedAsyncioTestCase):
    def test_submission_validation_defaults_and_rules(self):
        unrestricted = {"min_content_length": None, "max_content_length": None, "allow_attachments": True, "allow_links": True}
        self.assertIsNone(NoticeQueueService._invalid_submission_reason(unrestricted, "https://example.test", True))
        constrained = {"min_content_length": 3, "max_content_length": 5, "allow_attachments": False, "allow_links": False}
        self.assertEqual(NoticeQueueService._invalid_submission_reason(constrained, "x", False), "최소 3자")
        self.assertEqual(
            NoticeQueueService._invalid_submission_reason(constrained, "x", False, "en"),
            "at least 3 characters are required",
        )
        self.assertEqual(NoticeQueueService._invalid_submission_reason(constrained, "123456", False), "최대 5자")
        self.assertEqual(NoticeQueueService._invalid_submission_reason(constrained, "123", True), "첨부파일 허용 안 됨")
        self.assertEqual(NoticeQueueService._invalid_submission_reason(constrained, "https://x", False), "최대 5자")

    def test_publication_templates_keep_content_and_attribution(self):
        queue = {"display_name": "Weekly Notes", "title_template": "{{queue_name}} — {{author}}", "content_template": "By {{author}}:\\n{{content}}"}
        self.assertEqual(
            NoticeQueueService._render_publication(queue, "Ada", "A contribution"),
            ("Weekly Notes — Ada", "By Ada:\\nA contribution"),
        )

    async def test_reply_moderation_is_not_captured_as_queue_or_story_content(self):
        class FailDb:
            async def fetchrow(self, *args):
                raise AssertionError("reply moderation must not query contribution state")

        message = SimpleNamespace(
            author=SimpleNamespace(bot=False), guild=object(), reference=object(),
            content="!moderate reject", channel=object(),
        )
        queue_service = object.__new__(NoticeQueueService)
        queue_service.bot = SimpleNamespace(db=FailDb())
        with patch("cogs.notice_queues.discord.Thread", object):
            await NoticeQueueService.on_message(queue_service, message)
        story_service = object.__new__(RelayStoryService)
        story_service.bot = SimpleNamespace(db=FailDb())
        await RelayStoryService.on_message(story_service, message)

    def test_legacy_reward_wording_is_preserved(self):
        result = {"before_essence": N.of(0).to_json(), "after_essence": N.of(1).to_json(), "reward_amount": N.of(1).to_json()}
        dm, public = NoticeQueueService._reward_messages("legacy_kyohoon", result, "Ada")
        self.assertTrue(dm.startswith("이야기의 정수 보상을 받았습니다."))
        self.assertTrue(public.startswith("Ada 님이 이 교훈을 게시하여 이야기의 정수 보상을 받았습니다."))
        self.assertIn("기존 정수가 0이면", dm)

    async def test_configured_reward_deletion_creates_a_linked_case(self):
        db = _RewardDb(ledger={"id": 44})
        case = await register_reward_reconciliation(db, 10, "relay_story", 99, "deleted")
        self.assertEqual(case, {"ledger_id": 44, "reward_kind": "configured"})
        execute = [call for call in db.calls if call[0] == "execute"]
        self.assertEqual(len(execute), 1)
        self.assertEqual(execute[0][2], (10, "relay_story", 99, 44, "configured", "deleted"))

    async def test_unrewarded_deletion_does_not_create_a_case(self):
        db = _RewardDb()
        case = await register_reward_reconciliation(db, 10, "relay_story", 99, "deleted")
        self.assertIsNone(case)
        self.assertFalse(any(call[0] == "execute" for call in db.calls))
