"""Guild-scoped scheduled notice queues.

Discord is deliberately an adapter here: durable submission and publication
transitions are recorded before/after network operations so retries do not post
the same selected submission twice.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks
from .game_config import game_enabled
from .localization import guild_locale, text


def valid_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
        return True
    except ZoneInfoNotFoundError:
        return False


class NoticeQueueService(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self.run_schedules.start()
        self.prune_retention.start()

    def cog_unload(self):
        self.run_schedules.cancel()
        self.prune_retention.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        await self._import_legacy_queues()

    async def _import_legacy_queues(self):
        """Import the existing server exactly once; never duplicate queue data."""
        guild_id = os.getenv("SECRET_ROOM_SERVER_ID")
        if not guild_id or not guild_id.isdecimal():
            return
        definitions = (
            ("tomak", "오늘의 토막상식", "TOMAK_SUBMISSION_ID", "TOMAK_NOTIFY_ID", "daily", None, time(23, 30), "fair", 10, {"type": "legacy_tomak"}),
            ("kyohoon", "이 주의 교훈", "KYOHOON_SUBMISSION_ID", "KYOHOON_NOTIFY_ID", "weekly", 6, time(22, 30), "fair", 1, {"type": "legacy_kyohoon"}),
        )
        for key, name, submission_env, publication_env, cadence, weekday, scheduled, selection, limit, reward in definitions:
            source, destination = os.getenv(submission_env), os.getenv(publication_env)
            if not (source and destination and source.isdecimal() and destination.isdecimal()):
                continue
            enabled = await self.bot.db.fetchval(
                "SELECT enabled FROM submission_schedule_settings WHERE schedule_name=$1", key
            )
            row = await self.bot.db.fetchrow(
                """INSERT INTO notice_queues(guild_id,queue_key,display_name,state,accepting_submissions,
                   submission_channel_id,publication_channel_id,cadence,schedule_weekday,schedule_time,timezone,
                   selection_policy,per_member_limit,reward_policy,provenance)
                   VALUES($1,$2,$3,$4,TRUE,$5,$6,$7,$8,$9,'Asia/Seoul',$10,$11,$12,$13)
                   ON CONFLICT(guild_id,queue_key) DO UPDATE SET updated_at=NOW()
                   RETURNING id""",
                int(guild_id), key, name, "active" if enabled is not False else "paused",
                int(source), int(destination), cadence, weekday, scheduled, selection, limit, reward,
                {"migration_key": "legacy-notice-queues-v1", "legacy_kind": key},
            )
            # Adopt the currently open bot-owned submission thread so the
            # generalized listener continues accepting entries immediately.
            channel = self.bot.get_channel(int(source))
            if channel is not None and hasattr(channel, "threads"):
                threads = [thread for thread in channel.threads if thread.owner_id == self.bot.user.id]
                if threads:
                    latest = max(threads, key=lambda thread: thread.created_at)
                    await self.bot.db.execute(
                        "UPDATE notice_queues SET active_submission_thread_id=$2 WHERE id=$1",
                        row["id"], latest.id,
                    )
            await self._import_legacy_submissions(row["id"], key)

    async def _import_legacy_submissions(self, queue_id: int, kind: str):
        table = f"{kind}_submissions"
        rows = await self.bot.db.fetch(
            f"SELECT thread_id,user_id,message_id,submitted_at,posted_at,reward_posted_thread_id,rewarded_at FROM {table}"
        )
        for row in rows:
            status = "published" if row["posted_at"] else "eligible"
            reward_state = "granted" if row["rewarded_at"] else ("pending" if row["posted_at"] else "not_applicable")
            await self.bot.db.execute(
                """INSERT INTO notice_submissions(queue_id,source_thread_id,source_message_id,author_id,status,
                   submitted_at,published_at,publication_thread_id,reward_state)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT(source_message_id) DO NOTHING""",
                queue_id, row["thread_id"], row["message_id"], row["user_id"], status,
                row["submitted_at"], row["posted_at"], row["reward_posted_thread_id"], reward_state,
            )

    async def legacy_mismatches(self, guild_id: int) -> list[str]:
        """Compare import-critical legacy environment values with queue records."""
        checks = (
            ("tomak", "TOMAK_SUBMISSION_ID", "TOMAK_NOTIFY_ID", "daily", 23, 30),
            ("kyohoon", "KYOHOON_SUBMISSION_ID", "KYOHOON_NOTIFY_ID", "weekly", 22, 30),
        )
        mismatches = []
        for key, source_env, target_env, cadence, hour, minute in checks:
            source, target = os.getenv(source_env), os.getenv(target_env)
            if not (source and target and source.isdecimal() and target.isdecimal()):
                continue
            row = await self.bot.db.fetchrow("SELECT * FROM notice_queues WHERE guild_id=$1 AND queue_key=$2", guild_id, key)
            if row is None:
                mismatches.append(f"{key}: 가져온 대기열 없음"); continue
            if (row["submission_channel_id"], row["publication_channel_id"], row["cadence"], row["schedule_time"].hour, row["schedule_time"].minute) != (int(source), int(target), cadence, hour, minute):
                mismatches.append(f"{key}: 채널 또는 일정 불일치")
        return mismatches

    async def create_queue(self, guild_id: int, name: str, submission_channel_id: int,
                           publication_channel_id: int, cadence: str, schedule_time: time,
                           timezone_name: str, weekday: int | None = None, description: str | None = None):
        if submission_channel_id == publication_channel_id:
            raise ValueError("Submission and publication channels must be different.")
        if not valid_timezone(timezone_name):
            raise ValueError("Use a valid IANA time zone, for example Asia/Seoul.")
        if cadence == "weekly" and weekday is None:
            raise ValueError("A weekly queue needs a weekday (0=Monday through 6=Sunday).")
        await self._validate_channel(guild_id, submission_channel_id, "submission")
        await self._validate_channel(guild_id, publication_channel_id, "publication")
        key = "queue-" + name.lower().replace(" ", "-")[:40]
        return await self.bot.db.fetchrow(
            """INSERT INTO notice_queues(guild_id,queue_key,display_name,description,state,accepting_submissions,
               submission_channel_id,publication_channel_id,cadence,schedule_time,schedule_weekday,timezone)
               VALUES($1,$2,$3,$4,'active',TRUE,$5,$6,$7,$8,$9,$10) RETURNING *""",
            guild_id, key, name, description, submission_channel_id, publication_channel_id,
            cadence, schedule_time, weekday, timezone_name,
        )

    async def _validate_channel(self, guild_id: int, channel_id: int, purpose: str):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if getattr(channel.guild, "id", None) != guild_id or not hasattr(channel, "permissions_for"):
            raise ValueError("채널은 이 서버에 있어야 합니다.")
        permissions = channel.permissions_for(channel.guild.me)
        required = permissions.view_channel and permissions.send_messages and permissions.read_message_history
        if purpose in {"submission", "publication"}:
            required = required and permissions.create_public_threads
        if not required:
            raise ValueError("봇에 채널 보기, 메시지 보내기, 기록 보기 및 필요한 스레드 생성 권한이 없습니다.")

    async def _alert(self, guild_id: int, content: str):
        audit_id = await self.bot.db.fetchval("SELECT audit_channel_id FROM guild_settings WHERE guild_id=$1", guild_id)
        if not audit_id:
            return
        channel = self.bot.get_channel(audit_id)
        if channel and hasattr(channel, "send"):
            try:
                await channel.send(f"[CommunityNoticeBot] {content}")
            except discord.HTTPException:
                pass

    async def open_submissions(self, queue_id: int):
        queue = await self.bot.db.fetchrow("SELECT * FROM notice_queues WHERE id=$1", queue_id)
        if queue is None:
            raise ValueError("Queue not found.")
        await self._validate_channel(queue["guild_id"], queue["submission_channel_id"], "submission")
        channel = self.bot.get_channel(queue["submission_channel_id"]) or await self.bot.fetch_channel(queue["submission_channel_id"])
        if not hasattr(channel, "create_thread"):
            raise ValueError("The submission destination cannot create a thread.")
        created = await channel.create_thread(name=f"{queue['display_name']} submissions", content=f"Submit an entry for **{queue['display_name']}** here.")
        thread = created.thread if hasattr(created, "thread") else created
        await self.bot.db.execute("UPDATE notice_queues SET active_submission_thread_id=$2, submission_opened_at=NOW(), accepting_submissions=TRUE, updated_at=NOW() WHERE id=$1", queue_id, thread.id)
        return thread

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not isinstance(message.channel, discord.Thread) or message.guild is None:
            return
        # The one supported reply-only legacy command is handled by Admin.
        # Never accept the command text itself as a queue submission first.
        if message.reference and message.content.lstrip().lower().startswith("!moderate"):
            return
        queue = await self.bot.db.fetchrow(
            "SELECT * FROM notice_queues WHERE guild_id=$1 AND active_submission_thread_id=$2",
            message.guild.id, message.channel.id,
        )
        if queue is None:
            return
        if queue["state"] != "active" or not queue["accepting_submissions"]:
            await message.delete()
            return
        if queue["submission_period_hours"] and queue["submission_opened_at"]:
            closes_at = queue["submission_opened_at"] + timedelta(hours=queue["submission_period_hours"])
            if datetime.now(timezone.utc) >= closes_at:
                await self.bot.db.execute("UPDATE notice_queues SET accepting_submissions=FALSE,updated_at=NOW() WHERE id=$1", queue["id"])
                await message.delete()
                try:
                    locale = await guild_locale(self.bot.db, message.guild.id, message.guild.preferred_locale)
                    await message.author.send(text("queue.submission_closed", locale, name=queue["display_name"]))
                except discord.HTTPException:
                    pass
                return
        locale = await guild_locale(self.bot.db, message.guild.id, message.guild.preferred_locale)
        invalid_reason = self._invalid_submission_reason(queue, message.content, bool(message.attachments), locale)
        if invalid_reason:
            await message.delete()
            try:
                await message.author.send(text("queue.submission_deleted", locale, name=queue["display_name"], reason=invalid_reason))
            except discord.HTTPException:
                pass
            return
        count = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM notice_submissions WHERE queue_id=$1 AND source_thread_id=$2 AND author_id=$3 AND status IN ('eligible','selected')",
            queue["id"], message.channel.id, message.author.id,
        )
        if count >= queue["per_member_limit"]:
            await message.delete()
            try:
                await message.author.send(text("queue.limit_reached", locale, name=queue["display_name"], limit=queue["per_member_limit"]))
            except discord.HTTPException:
                pass
            return
        await self.bot.db.execute(
            "INSERT INTO notice_submissions(queue_id,source_thread_id,source_message_id,author_id) VALUES($1,$2,$3,$4) ON CONFLICT(source_message_id) DO NOTHING",
            queue["id"], message.channel.id, message.id, message.author.id,
        )

    @staticmethod
    def _invalid_submission_reason(queue, content: str, has_attachments: bool, locale="ko") -> str | None:
        length = len(content.strip())
        if queue["min_content_length"] is not None and length < queue["min_content_length"]:
            return text("queue.reason.min_length", locale, length=queue["min_content_length"])
        if queue["max_content_length"] is not None and length > queue["max_content_length"]:
            return text("queue.reason.max_length", locale, length=queue["max_content_length"])
        if not queue["allow_attachments"] and has_attachments:
            return text("queue.reason.attachments", locale)
        if not queue["allow_links"] and ("http://" in content or "https://" in content):
            return text("queue.reason.links", locale)
        return None

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        row = await self.bot.db.fetchrow("SELECT s.id,q.* FROM notice_submissions s JOIN notice_queues q ON q.id=s.queue_id WHERE s.source_message_id=$1 AND s.status='eligible'", payload.message_id)
        if row is None:
            return
        content = payload.data.get("content", "")
        if not row["allow_edits"]:
            await self.bot.db.execute("UPDATE notice_submissions SET status='invalid',moderated_at=NOW(),moderation_reason='edits are disabled' WHERE id=$1", row["id"])
            return
        reason = self._invalid_submission_reason(row, content, bool(payload.data.get("attachments")))
        if reason:
            await self.bot.db.execute("UPDATE notice_submissions SET status='invalid',moderated_at=NOW(),moderation_reason=$2 WHERE id=$1", row["id"], f"edited: {reason}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        row = await self.bot.db.fetchrow(
            "UPDATE notice_submissions SET status='withdrawn', moderated_at=NOW(), moderation_reason='source message deleted' WHERE source_message_id=$1 AND status='eligible' RETURNING queue_id,author_id",
            payload.message_id,
        )
        if row:
            queue = await self.bot.db.fetchrow("SELECT guild_id FROM notice_queues WHERE id=$1", row["queue_id"])
            await self.bot.db.execute("INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) VALUES($1,$2,'submission_withdrawn','notice_submission',$3,$4)", queue["guild_id"], row["author_id"], str(payload.message_id), {"reason": "source message deleted"})
            locale = await guild_locale(self.bot.db, queue["guild_id"])
            await self._alert(queue["guild_id"], text("queue.audit.withdrawn", locale, message_id=payload.message_id))

    async def publish(self, queue_id: int, scheduled_for: datetime | None = None) -> str:
        async with self._lock:
            queue = await self.bot.db.fetchrow("SELECT * FROM notice_queues WHERE id=$1", queue_id)
            if queue is None or queue["state"] != "active":
                raise ValueError("Queue is not active.")
            async with self.bot.db.acquire() as conn, conn.transaction():
                if queue["selection_policy"] == "random":
                    order = "random()"
                elif queue["selection_policy"] == "fair":
                    order = "COALESCE((SELECT MAX(previous.published_at) FROM notice_submissions previous WHERE previous.queue_id=s.queue_id AND previous.author_id=s.author_id AND previous.status='published'), 'epoch'::timestamptz), s.submitted_at, s.id"
                else:
                    order = "s.submitted_at, s.id"
                submission = await conn.fetchrow(f"SELECT s.* FROM notice_submissions s WHERE s.queue_id=$1 AND s.status='eligible' ORDER BY {order} FOR UPDATE SKIP LOCKED LIMIT 1", queue_id)
                if submission is None:
                    await conn.execute("INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) VALUES($1,0,'notice_queue_empty','notice_queue',$2,$3)", queue["guild_id"], str(queue_id), {"action": queue["no_entry_action"]})
                    raise ValueError("No eligible submission is available.")
                await conn.execute("UPDATE notice_submissions SET status='selected' WHERE id=$1", submission["id"])
                if scheduled_for:
                    await conn.execute("INSERT INTO notice_jobs(queue_id,job_type,scheduled_for,state,lease_expires_at,attempt_count) VALUES($1,'publish',$2,'running',NOW() + INTERVAL '10 minutes',1) ON CONFLICT(queue_id,job_type,scheduled_for) DO NOTHING", queue_id, scheduled_for)
            source = self.bot.get_channel(submission["source_thread_id"]) or await self.bot.fetch_channel(submission["source_thread_id"])
            try:
                source_message = await source.fetch_message(submission["source_message_id"])
            except discord.NotFound:
                await self.bot.db.execute("UPDATE notice_submissions SET status='withdrawn', moderation_reason='source message unavailable' WHERE id=$1", submission["id"])
                raise ValueError("The selected submission was deleted.")
            destination = self.bot.get_channel(queue["publication_channel_id"]) or await self.bot.fetch_channel(queue["publication_channel_id"])
            await self._validate_channel(queue["guild_id"], queue["publication_channel_id"], "publication")
            author = source_message.author.display_name if queue["attribution_policy"] == "display_name" else ""
            title, content = self._render_publication(queue, author, source_message.content)
            files = [await attachment.to_file() for attachment in source_message.attachments]
            created = await destination.create_thread(name=title[:100] or queue["display_name"], content=content, files=files)
            publication = created.thread if hasattr(created, "thread") else created
            await self.bot.db.execute("UPDATE notice_submissions SET status='published',published_at=NOW(),publication_channel_id=$2,publication_thread_id=$3,reward_state=$4 WHERE id=$1", submission["id"], destination.id, publication.id, "pending" if queue["reward_policy"].get("type") != "none" else "not_applicable")
            await self._issue_legacy_reward(queue, submission, source_message, publication)
            # A successful publication advances the queue into its next
            # submission period.  Failure is reported but never changes the
            # already durable publication/reward outcome.
            try:
                await self.open_submissions(queue_id)
            except (discord.HTTPException, ValueError) as exc:
                await self._alert(queue["guild_id"], f"**{queue['display_name']}** 다음 신청 스레드를 열지 못했습니다: {exc}")
            if scheduled_for:
                await self.bot.db.execute("UPDATE notice_jobs SET state='completed',completed_at=NOW(),lease_expires_at=NULL WHERE queue_id=$1 AND job_type='publish' AND scheduled_for=$2", queue_id, scheduled_for)
            return publication.mention

    async def _issue_legacy_reward(self, queue, submission, source_message, publication):
        if not await game_enabled(self.bot.db, queue["guild_id"]):
            await self.bot.db.execute("UPDATE notice_submissions SET reward_state='not_applicable' WHERE id=$1", submission["id"])
            return
        kind = queue["reward_policy"].get("type")
        if kind == "none":
            return
        try:
            if kind in {"legacy_tomak", "legacy_kyohoon"}:
                from .secretae_incremental.db import grant_tomak_reward, grant_kyohoon_reward
                grant = grant_tomak_reward if kind == "legacy_tomak" else grant_kyohoon_reward
                result = await grant(self.bot.db, queue["guild_id"], source_message.author.id, submission["source_thread_id"], submission["source_message_id"], publication.id)
            else:
                from .secretae_incremental.db import grant_configured_reward
                result = await grant_configured_reward(self.bot.db, queue["guild_id"], "notice_queue", queue["id"], submission["source_message_id"], source_message.author.id, queue["reward_policy"])
            state = "granted" if result.get("awarded") or result.get("already_awarded") else ("not_applicable" if result.get("cap_reached") else "failed")
            await self.bot.db.execute("UPDATE notice_submissions SET reward_state=$2 WHERE id=$1", submission["id"], state)
            if result.get("awarded"):
                text, public_text = self._reward_messages(kind, result, source_message.author.display_name)
                try:
                    await source_message.author.send(text)
                except discord.HTTPException:
                    pass
                try:
                    await publication.send(public_text)
                except discord.HTTPException:
                    pass
        except Exception as exc:
            await self.bot.db.execute("UPDATE notice_submissions SET reward_state='failed' WHERE id=$1", submission["id"])
            await self.bot.db.execute("INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) VALUES($1,0,'notice_reward_failed','notice_submission',$2,$3)", queue["guild_id"], str(submission["id"]), {"error": type(exc).__name__})

    @staticmethod
    def _reward_messages(kind: str, result: dict, recipient_name: str) -> tuple[str, str]:
        """Retain the legacy DM/thread wording and numerical formatting."""
        if kind == "legacy_tomak":
            from .secretae_incremental.constants import SECRETS, SYMBOLS
            from .secretae_incremental.numbers import LayeredDecimal, format_amount
            lines = ["모든 비밀이 2배가 되었습니다."]
            public_lines = [f"{recipient_name} 님이 이 토막상식을 게시하여 모든 비밀이 2배가 되었습니다."]
            for key in SECRETS:
                transition = f"{SYMBOLS[key]} {format_amount(LayeredDecimal.from_json(result['before'][key]))} → {format_amount(LayeredDecimal.from_json(result['after'][key]))}"
                lines.append(transition); public_lines.append(transition)
            return "\n".join(lines), "\n".join(public_lines)
        if kind == "legacy_kyohoon":
            from .secretae_incremental.numbers import LayeredDecimal, format_amount
            before = format_amount(LayeredDecimal.from_json(result["before_essence"]))
            after = format_amount(LayeredDecimal.from_json(result["after_essence"]))
            gained = format_amount(LayeredDecimal.from_json(result["reward_amount"]))
            body = f"이야기의 정수 보상을 받았습니다.\n정수: {before} → {after} (획득 {gained})\n기존 정수가 0이면 첫 보상으로 1을 받고, 그 외에는 정수가 두 배가 됩니다."
            return body, f"{recipient_name} 님이 이 교훈을 게시하여 {body}"
        return "게시된 기여에 대한 보상이 지급되었습니다.", f"{recipient_name} 님의 게시 기여에 대한 보상이 지급되었습니다."

    @staticmethod
    def _render_publication(queue, author: str, source_content: str) -> tuple[str, str]:
        values = {"{{queue_name}}": queue["display_name"], "{{author}}": author, "{{content}}": source_content}
        title, content = queue["title_template"], queue["content_template"]
        for token, value in values.items():
            title, content = title.replace(token, value), content.replace(token, value)
        return title.strip(" —"), content

    @tasks.loop(seconds=60)
    async def run_schedules(self):
        await self.bot.db.execute(
            "UPDATE notice_submissions SET status='eligible' WHERE status='selected' AND publication_thread_id IS NULL "
            "AND id IN (SELECT s.id FROM notice_submissions s JOIN notice_jobs j ON j.queue_id=s.queue_id "
            "WHERE j.state='running' AND j.lease_expires_at < NOW())"
        )
        await self.bot.db.execute(
            "UPDATE notice_jobs SET state='failed',last_error=COALESCE(last_error,'lease expired'),lease_expires_at=NULL "
            "WHERE state='running' AND lease_expires_at < NOW()"
        )
        now = datetime.now(timezone.utc)
        queues = await self.bot.db.fetch("SELECT * FROM notice_queues WHERE state='active' AND cadence <> 'manual'")
        for queue in queues:
            local = now.astimezone(ZoneInfo(queue["timezone"]))
            if local.hour != queue["schedule_time"].hour or local.minute != queue["schedule_time"].minute:
                continue
            if queue["cadence"] == "weekly" and local.weekday() != queue["schedule_weekday"]:
                continue
            scheduled_for = local.replace(second=0, microsecond=0).astimezone(timezone.utc)
            existing = await self.bot.db.fetchrow("SELECT state FROM notice_jobs WHERE queue_id=$1 AND job_type='publish' AND scheduled_for=$2", queue["id"], scheduled_for)
            if existing and existing["state"] in {"completed", "running"}:
                continue
            try:
                await self.publish(queue["id"], scheduled_for)
            except Exception as exc:
                if str(exc) == "No eligible submission is available." and queue["no_entry_action"] == "alert_and_reopen":
                    try:
                        await self.open_submissions(queue["id"])
                    except (discord.HTTPException, ValueError):
                        pass
                await self._alert(queue["guild_id"], f"**{queue['display_name']}** 예약 게시를 처리하지 못했습니다: {exc}")
                await self.bot.db.execute("INSERT INTO notice_jobs(queue_id,job_type,scheduled_for,state,attempt_count,last_error) VALUES($1,'publish',$2,'failed',1,$3) ON CONFLICT(queue_id,job_type,scheduled_for) DO UPDATE SET state='failed',attempt_count=notice_jobs.attempt_count+1,last_error=$3", queue["id"], scheduled_for, str(exc)[:500])

    @run_schedules.before_loop
    async def before_run_schedules(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def prune_retention(self):
        """Retain publication/reward evidence; prune stale non-content moderation rows."""
        await self.bot.db.execute(
            "DELETE FROM notice_submissions WHERE status IN ('rejected','withdrawn','invalid') "
            "AND COALESCE(moderated_at,submitted_at) < NOW() - INTERVAL '90 days'"
        )
        await self.bot.db.execute(
            "DELETE FROM notice_jobs WHERE state IN ('completed','failed') "
            "AND COALESCE(completed_at,scheduled_for) < NOW() - INTERVAL '90 days'"
        )
        await self.bot.db.execute(
            "DELETE FROM admin_audit_log WHERE created_at < NOW() - INTERVAL '365 days'"
        )

    @prune_retention.before_loop
    async def before_prune_retention(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(NoticeQueueService(bot))
