import datetime
import os
import traceback

import discord
import yaml
from discord.ext import commands, tasks

from ..secretae_db import grant_tomak_post_reward
from .common import (
    KST,
    channel_detail as _channel_detail,
    created_thread as _created_thread,
    get_bot_thread_by_id as _get_thread_by_id,
    get_channel as _get_channel,
    get_latest_bot_thread as _get_latest_thread,
    schedule_update_enabled,
    SubmissionManagementCog,
    toggle_schedule_update,
)
TOMAK_MAX_SUBMISSIONS_PER_THREAD = 10
TOMAK_MIN_LENGTH = 20
TOMAK_MAX_LENGTH = 300
TOMAK_NEW_THREAD_THRESHOLD = 5


def _tomak_thread_title(now_kst: datetime.datetime) -> str:
    return f"{now_kst:%Y년 %m월 %d일} 토막상식 신청"


def _tomak_post_title(now_kst: datetime.datetime, display_name: str) -> str:
    return f"{now_kst:%Y년 %m월 %d일} 토막상식, {display_name}"


def _tomak_content_length(content: str) -> int:
    return len(content.strip())


def _is_invalid_tomak_submission(message: discord.Message) -> tuple[bool, int]:
    length = _tomak_content_length(message.content)
    if length > TOMAK_MAX_LENGTH:
        return True, length
    if not message.attachments and length < TOMAK_MIN_LENGTH:
        return True, length
    return False, length


class TomakManagement(SubmissionManagementCog):
    def __init__(self, bot):
        super().__init__(bot)
        with open('assets/message.yaml', encoding='utf-8') as f:
            self._msgs = yaml.safe_load(f)['tomak']
        self.update_Tomak.start()

    def cog_unload(self):
        self.update_Tomak.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._synced:
            return
        self._synced = True

        submission_ch = self._get_configured_channel("TOMAK_SUBMISSION_ID")
        if submission_ch is None:
            return
        latest_thread = await _get_latest_thread(submission_ch, self.bot.user.id)
        if latest_thread is None:
            return

        seen_counts = {}
        async for msg in latest_thread.history(limit=None, oldest_first=True):
            if msg.author.bot:
                continue
            invalid, _ = _is_invalid_tomak_submission(msg)
            if invalid:
                continue
            count = seen_counts.get(msg.author.id)
            if count is None:
                count = await self.bot.db.fetchval(
                    "SELECT COUNT(*) FROM tomak_submissions WHERE thread_id = $1 AND user_id = $2",
                    latest_thread.id, msg.author.id,
                )
            if count >= TOMAK_MAX_SUBMISSIONS_PER_THREAD:
                continue
            await self.bot.db.execute(
                "INSERT INTO tomak_submissions(thread_id, user_id, message_id) "
                "VALUES($1, $2, $3) ON CONFLICT DO NOTHING",
                latest_thread.id, msg.author.id, msg.id,
            )
            seen_counts[msg.author.id] = count + 1

    # ------------------------------------------------------------------ #
    # 토막상식 등록 (매일 23:30 KST)
    # ------------------------------------------------------------------ #

    @tasks.loop(time=datetime.time(23, 30, tzinfo=KST))
    async def update_Tomak(self):
        if not await schedule_update_enabled(self.bot.db, "tomak"):
            return
        await self._execute_tomak_update("자동", None)

    @update_Tomak.before_loop
    async def before_update_Tomak(self):
        await self.bot.wait_until_ready()

    async def _execute_tomak_update(self, trigger: str, operator, raise_errors: bool = True) -> bool:
        started_at = datetime.datetime.now(KST)
        submission_ch = self._get_configured_channel("TOMAK_SUBMISSION_ID")
        notify_ch = self._get_configured_channel("TOMAK_NOTIFY_ID")
        mgmt_thread = self._get_configured_channel("TOMAK_MANAGEMENT_ID")

        async with self._update_lock:
            try:
                return await self._run_tomak_update(
                    started_at, submission_ch, notify_ch, mgmt_thread, trigger, operator,
                )
            except Exception as exc:
                tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=5))
                await self._send_log_thread_message(
                    "[토막상식 갱신 실패]\n"
                    f"- 실행 방식: {trigger}\n"
                    f"- 실행자: {operator.mention if operator else '시스템'}\n"
                    f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                    f"- 신청 채널: {_channel_detail(submission_ch)}\n"
                    f"- 게시 채널: {_channel_detail(notify_ch)}\n"
                    f"- 관리 스레드: {_channel_detail(mgmt_thread)}\n"
                    f"- 사유: 예외 발생 (`{type(exc).__name__}: {exc}`)\n"
                    f"```py\n{tb[-1500:]}\n```"
                )
                if raise_errors:
                    raise
                return False

    async def _run_tomak_update(self, started_at, submission_ch, notify_ch, mgmt_thread, trigger, operator):
        if submission_ch is None or notify_ch is None or mgmt_thread is None:
            await self._send_log_thread_message(
                "[토막상식 갱신 실패]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 실행자: {operator.mention if operator else '시스템'}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 채널: {_channel_detail(submission_ch)}\n"
                f"- 게시 채널: {_channel_detail(notify_ch)}\n"
                f"- 관리 스레드: {_channel_detail(mgmt_thread)}\n"
                "- 사유: 필요한 채널 또는 스레드를 찾지 못했습니다."
            )
            return False

        latest_sub_thread = await _get_latest_thread(submission_ch, self.bot.user.id)
        if latest_sub_thread is None:
            created_thread = await self._create_tomak_submission_thread(submission_ch, started_at)
            await self._send_log_thread_message(
                "[토막상식 신청 스레드 생성]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 실행자: {operator.mention if operator else '시스템'}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 채널: {_channel_detail(submission_ch)}\n"
                f"- 생성된 스레드: {created_thread.mention} (`{created_thread.id}`, {created_thread.name})\n"
                "- 사유: 기존 신청 스레드가 없습니다."
            )
            return True

        unposted = await self.bot.db.fetch(
            "SELECT thread_id, user_id, message_id, submitted_at "
            "FROM tomak_submissions "
            "WHERE posted_at IS NULL ORDER BY submitted_at",
        )
        if not unposted:
            created_thread = await self._create_tomak_submission_thread(submission_ch, started_at)
            await self._send_log_thread_message(
                "[토막상식 신청 스레드 생성]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 실행자: {operator.mention if operator else '시스템'}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 이전 신청 스레드: {latest_sub_thread.mention} (`{latest_sub_thread.id}`, {latest_sub_thread.name})\n"
                f"- 생성된 스레드: {created_thread.mention} (`{created_thread.id}`, {created_thread.name})\n"
                "- 사유: 게시 대기 중인 토막상식 신청이 없습니다."
            )
            return True

        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        submission_msg = None
        selected = None
        source_thread = None
        while unposted:
            priorities = []
            for row in unposted:
                last = await self.bot.db.fetchval(
                    "SELECT MAX(posted_at) FROM tomak_submissions "
                    "WHERE user_id = $1 AND posted_at IS NOT NULL",
                    row["user_id"],
                ) or epoch
                priorities.append((last, row["submitted_at"], row["message_id"], row))
            selected = min(priorities, key=lambda item: (item[0], item[1], item[2]))[3]

            source_thread = await _get_thread_by_id(submission_ch, selected["thread_id"], self.bot.user.id)
            if source_thread is None:
                await self.bot.db.execute(
                    "DELETE FROM tomak_submissions WHERE message_id = $1",
                    selected["message_id"],
                )
                unposted = [row for row in unposted if row["message_id"] != selected["message_id"]]
                continue
            try:
                submission_msg = await source_thread.fetch_message(selected["message_id"])
                break
            except discord.NotFound:
                await self.bot.db.execute(
                    "DELETE FROM tomak_submissions WHERE message_id = $1",
                    selected["message_id"],
                )
                unposted = [row for row in unposted if row["message_id"] != selected["message_id"]]

        if source_thread is None:
            await self._send_log_thread_message(
                "[토막상식 갱신 실패]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 실행자: {operator.mention if operator else '시스템'}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 스레드 ID: `{selected['thread_id']}`\n"
                f"- 선택된 신청자: <@{selected['user_id']}> (`{selected['user_id']}`)\n"
                f"- 신청 메시지 ID: `{selected['message_id']}`\n"
                "- 사유: 선택된 신청 스레드를 찾을 수 없습니다."
            )
            return False

        if submission_msg is None:
            await self._send_log_thread_message(
                "[토막상식 갱신 실패]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 실행자: {operator.mention if operator else '시스템'}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 스레드: {source_thread.mention} (`{source_thread.id}`, {source_thread.name})\n"
                f"- 선택된 신청자: <@{selected['user_id']}> (`{selected['user_id']}`)\n"
                f"- 신청 메시지 ID: `{selected['message_id']}`\n"
                "- 사유: 게시 대기 중인 신청 메시지를 찾을 수 없습니다."
            )
            return False

        now_kst = datetime.datetime.now(KST)
        title = _tomak_post_title(now_kst, submission_msg.author.display_name)
        files = [await attachment.to_file() for attachment in submission_msg.attachments]
        created = await notify_ch.create_thread(
            name=title,
            content=submission_msg.content,
            files=files,
        )
        publication_thread = _created_thread(created)

        try:
            await self.bot.db.execute(
                "UPDATE tomak_submissions SET posted_at = $1 WHERE message_id = $2",
                now_kst, selected["message_id"],
            )
        except Exception as exc:
            await self._send_log_thread_message(
                "[토막상식 DB 갱신 실패]\n"
                "- Discord 게시물은 생성되었지만 DB 게시 상태를 저장하지 못했습니다.\n"
                f"- 신청 메시지: `{selected['message_id']}`\n"
                f"- 생성된 게시 스레드: {publication_thread.mention} (`{publication_thread.id}`)\n"
                "- 다음 갱신 전에 DB 상태를 수동으로 확인해 주세요.\n"
                f"- 사유: `{type(exc).__name__}: {exc}`"
            )
            return False
        reward = await grant_tomak_post_reward(
            self.bot.db, submission_msg.author.id, source_thread.id, submission_msg.id,
        )
        if reward.get("awarded"):
            try:
                await submission_msg.author.send(self._format_tomak_reward_dm(reward))
            except discord.Forbidden:
                pass

        try:
            await self._update_management_message(mgmt_thread, source_thread.id, source_thread.name)
        except discord.HTTPException as exc:
            await self._send_log_thread_message(
                f"[토막상식 관리 메시지 갱신 실패] 게시 상태는 저장되었습니다. ({type(exc).__name__}: {exc})"
            )

        remaining = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM tomak_submissions WHERE posted_at IS NULL",
        )
        created_submission_thread = None
        if remaining < TOMAK_NEW_THREAD_THRESHOLD and latest_sub_thread.id == source_thread.id:
            created_submission_thread = await self._create_tomak_submission_thread(submission_ch, now_kst)

        await self._send_log_thread_message(
            "[토막상식 갱신 성공]\n"
            f"- 실행 방식: {trigger}\n"
            f"- 실행자: {operator.mention if operator else '시스템'}\n"
            f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
            f"- 완료 시각: {datetime.datetime.now(KST):%Y-%m-%d %H:%M:%S KST}\n"
            f"- 신청 스레드: {source_thread.mention} (`{source_thread.id}`, {source_thread.name})\n"
            f"- 게시 대상자: {submission_msg.author.mention} (`{submission_msg.author.id}`)\n"
            f"- 신청 메시지: `{submission_msg.id}`\n"
            f"- 게시 제목: **{title}**\n"
            f"- 첨부파일 수: {len(submission_msg.attachments)}개\n"
            f"- 보상 지급: {'예' if reward.get('awarded') else '아니오'}\n"
            f"- 남은 미게시 신청: {remaining}개\n"
            f"- 새 신청 스레드 생성: {created_submission_thread.mention if created_submission_thread else '아니오'}"
        )
        return True

    async def _create_tomak_submission_thread(self, submission_ch, now_kst=None):
        if now_kst is None:
            now_kst = datetime.datetime.now(KST)
        created = await submission_ch.create_thread(
            name=_tomak_thread_title(now_kst),
            content="토막상식 신청하세요!!!!!!!!!!!!!!",
        )
        new_thread = _created_thread(created)
        await new_thread.send("@everyone")
        return new_thread

    async def _handle_force_operation(self, message) -> bool:
        command = message.content.strip()
        if command not in ("!force_tomak_update", "!force_tomak_submission", "!toggle_tomak"):
            return False

        force_thread = self._get_configured_channel("FORCE_OPERATION_ID")
        if force_thread is None or message.channel.id != force_thread.id:
            return True
        if not self._has_admin_role(message.author):
            await message.channel.send("이 명령어를 실행할 권한이 없습니다.")
            return True

        if command == "!toggle_tomak":
            enabled = await toggle_schedule_update(self.bot.db, "tomak")
            state = "활성화" if enabled else "비활성화"
            await message.channel.send(f"토막상식 정기 갱신을 {state}했습니다.")
            await self._send_log_thread_message(
                "[토막상식 정기 갱신 설정 변경]\n"
                f"- 실행자: {message.author.mention}\n"
                f"- 변경 결과: {state}"
            )
            return True

        if command == "!force_tomak_update":
            ok = await self._execute_tomak_update("강제", message.author, raise_errors=False)
            await message.channel.send(
                "토막상식 갱신을 강제로 실행했습니다. 상세 결과는 LOG_THREAD를 확인해 주세요."
                if ok else
                "토막상식 갱신 강제 실행에 실패했습니다. 상세 사유는 LOG_THREAD를 확인해 주세요."
            )
            return True

        submission_ch = self._get_configured_channel("TOMAK_SUBMISSION_ID")
        started_at = datetime.datetime.now(KST)
        if submission_ch is None:
            await self._send_log_thread_message(
                "[토막상식 신청 스레드 강제 생성 실패]\n"
                f"- 실행자: {message.author.mention}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                "- 사유: TOMAK_SUBMISSION_ID 채널을 찾지 못했습니다."
            )
            await message.channel.send("토막상식 신청 스레드 강제 생성에 실패했습니다. 상세 사유는 LOG_THREAD를 확인해 주세요.")
            return True

        try:
            new_thread = await self._create_tomak_submission_thread(submission_ch, started_at)
        except Exception as exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=5))
            await self._send_log_thread_message(
                "[토막상식 신청 스레드 강제 생성 실패]\n"
                f"- 실행자: {message.author.mention}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 채널: {_channel_detail(submission_ch)}\n"
                f"- 사유: 예외 발생 (`{type(exc).__name__}: {exc}`)\n"
                f"```py\n{tb[-1500:]}\n```"
            )
            await message.channel.send("토막상식 신청 스레드 강제 생성에 실패했습니다. 상세 사유는 LOG_THREAD를 확인해 주세요.")
            return True

        await self._send_log_thread_message(
            "[토막상식 신청 스레드 강제 생성 성공]\n"
            f"- 실행자: {message.author.mention}\n"
            f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
            f"- 신청 채널: {_channel_detail(submission_ch)}\n"
            f"- 생성된 스레드: {new_thread.mention} (`{new_thread.id}`, {new_thread.name})"
        )
        await message.channel.send(f"토막상식 신청 스레드를 강제로 생성했습니다: {new_thread.mention}")
        return True

    # ------------------------------------------------------------------ #
    # 토막상식 신청 감지
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        submission_ch = self._get_configured_channel("TOMAK_SUBMISSION_ID")
        if submission_ch is None or thread.parent_id != submission_ch.id:
            return
        if thread.owner_id == self.bot.user.id:
            return
        try:
            await thread.delete(reason="Only bot-created Tomak submission threads are allowed")
        except discord.HTTPException:
            await self._send_log_thread_message(
                f"[토막상식 신청 스레드 삭제 실패] 비공식 스레드 `{thread.id}`를 삭제하지 못했습니다."
            )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if await self._handle_force_operation(message):
            return

        submission_ch = self._get_configured_channel("TOMAK_SUBMISSION_ID")
        if submission_ch is None:
            return
        if not isinstance(message.channel, discord.Thread):
            return
        if message.channel.parent_id != submission_ch.id:
            return

        latest_thread = await _get_latest_thread(submission_ch, self.bot.user.id)
        log_thread = self._get_configured_channel("LOG_THREAD_ID")
        mgmt_thread = self._get_configured_channel("TOMAK_MANAGEMENT_ID")
        content = message.content
        author = message.author

        if latest_thread is None or message.channel.id != latest_thread.id:
            await self._delete_and_dm(message, self._msgs["not_latest_thread"].format(content=content))
            if log_thread:
                await log_thread.send(
                    f"[토막상식 신청 위반] {author.mention}이 만료된 스레드 **{message.channel.name}**에 신청하려 했습니다."
                )
            return

        invalid, length = _is_invalid_tomak_submission(message)
        if invalid:
            await self._delete_and_dm(
                message,
                self._msgs["invalid_length"].format(content=content, length=length),
            )
            if log_thread:
                await log_thread.send(
                    f"[토막상식 길이 위반] {author.mention}의 신청이 삭제되었습니다. "
                    f"(길이: {length}자, 첨부파일: {len(message.attachments)}개)"
                )
            return

        count = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM tomak_submissions WHERE thread_id = $1 AND user_id = $2",
            message.channel.id, author.id,
        )
        if count >= TOMAK_MAX_SUBMISSIONS_PER_THREAD:
            await self._delete_and_dm(message, self._msgs["too_many_submissions"].format(content=content))
            if log_thread:
                await log_thread.send(
                    f"[토막상식 신청 초과] {author.mention}이 한 스레드에 10개를 초과해 신청하려 했습니다."
                )
            return

        await self.bot.db.execute(
            "INSERT INTO tomak_submissions(thread_id, user_id, message_id) VALUES($1, $2, $3) "
            "ON CONFLICT (message_id) DO NOTHING",
            message.channel.id, author.id, message.id,
        )
        if mgmt_thread:
            await self._update_management_message(mgmt_thread, message.channel.id, message.channel.name)

    async def _delete_and_dm(self, message, dm_content: str):
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        try:
            await message.author.send(dm_content)
        except discord.Forbidden:
            pass

    # ------------------------------------------------------------------ #
    # 신청 메시지 수정/삭제 감지
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        author_data = payload.data.get("author", {})
        if author_data.get("bot"):
            return
        user_id_str = author_data.get("id")
        if user_id_str:
            await self._check_posted_modification(payload.channel_id, payload.message_id, int(user_id_str))
        await self._remove_invalid_edited_submission(payload.channel_id, payload.message_id)

    async def _remove_invalid_edited_submission(self, channel_id: int, message_id: int):
        submission_ch = self._get_configured_channel("TOMAK_SUBMISSION_ID")
        if submission_ch is None:
            return
        channel = await _get_channel(self.bot, channel_id)
        if not isinstance(channel, discord.Thread) or channel.parent_id != submission_ch.id:
            return

        latest_thread = await _get_latest_thread(submission_ch, self.bot.user.id)
        if latest_thread is None or channel.id != latest_thread.id:
            return

        row = await self.bot.db.fetchrow(
            "SELECT posted_at FROM tomak_submissions WHERE message_id = $1",
            message_id,
        )
        if row is None or row["posted_at"] is not None:
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return
        invalid, length = _is_invalid_tomak_submission(message)
        if not invalid:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            return
        await self.bot.db.execute(
            "DELETE FROM tomak_submissions WHERE message_id = $1",
            message_id,
        )
        mgmt_thread = self._get_configured_channel("TOMAK_MANAGEMENT_ID")
        if mgmt_thread:
            try:
                await self._update_management_message(mgmt_thread, channel.id, channel.name)
            except discord.HTTPException:
                pass
        try:
            await message.author.send(
                self._msgs["invalid_length"].format(content=message.content, length=length),
            )
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        user_id = None
        if payload.cached_message and not payload.cached_message.author.bot:
            user_id = payload.cached_message.author.id
        await self._delete_submission_on_message_delete(payload.channel_id, payload.message_id, user_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        for message_id in payload.message_ids:
            await self._delete_submission_on_message_delete(payload.channel_id, message_id, None)

    async def _delete_submission_on_message_delete(self, channel_id: int, message_id: int, user_id: int | None):
        submission_ch = self._get_configured_channel("TOMAK_SUBMISSION_ID")
        if submission_ch is None:
            return
        channel = await _get_channel(self.bot, channel_id)
        if not isinstance(channel, discord.Thread) or channel.parent_id != submission_ch.id:
            return

        row = await self.bot.db.fetchrow(
            "DELETE FROM tomak_submissions WHERE message_id = $1 "
            "RETURNING user_id, posted_at",
            message_id,
        )
        if row is not None:
            mgmt_thread = self._get_configured_channel("TOMAK_MANAGEMENT_ID")
            if mgmt_thread:
                try:
                    await self._update_management_message(mgmt_thread, channel.id, channel.name)
                except discord.HTTPException:
                    pass
        if row is not None and row["posted_at"] is not None:
            await self._check_posted_modification(channel_id, message_id, user_id, row)

    async def _check_posted_modification(self, channel_id: int, message_id: int, user_id: int | None, row=None):
        submission_ch = self._get_configured_channel("TOMAK_SUBMISSION_ID")
        if submission_ch is None:
            return
        channel = await _get_channel(self.bot, channel_id)
        if not isinstance(channel, discord.Thread) or channel.parent_id != submission_ch.id:
            return

        latest_thread = await _get_latest_thread(submission_ch, self.bot.user.id)
        if latest_thread is None or channel.id != latest_thread.id:
            return

        if row is None:
            row = await self.bot.db.fetchrow(
                "SELECT user_id, posted_at FROM tomak_submissions WHERE message_id = $1",
                message_id,
            )
        if row is None or row["posted_at"] is None:
            return

        if user_id is None:
            user_id = row["user_id"]

        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        log_thread = self._get_configured_channel("LOG_THREAD_ID")

        try:
            await user.send(self._msgs["edit_after_posted"])
        except discord.Forbidden:
            pass
        if log_thread:
            await log_thread.send(
                f"[게시 후 수정/삭제] {user.mention}이 이미 게시된 토막상식을 수정하거나 삭제했습니다."
            )

    # ------------------------------------------------------------------ #
    # 관리 메시지 갱신
    # ------------------------------------------------------------------ #

    async def _update_management_message(self, mgmt_thread, thread_id: int, thread_name: str):
        rows = await self.bot.db.fetch(
            "SELECT user_id, "
            "COUNT(*) AS total, "
            "COUNT(posted_at) AS posted "
            "FROM tomak_submissions WHERE thread_id = $1 "
            "GROUP BY user_id ORDER BY MIN(submitted_at)",
            thread_id,
        )

        total = sum(row["total"] for row in rows)
        posted = sum(row["posted"] for row in rows)
        lines = [f"**{thread_name} 신청 현황**"]
        for row in rows:
            waiting = row["total"] - row["posted"]
            lines.append(
                f"- <@{row['user_id']}> {row['total']}개 신청 / {row['posted']}개 게시 / {waiting}개 대기"
            )
        lines.extend([
            "",
            f"총 신청: {total}개",
            f"게시됨: {posted}개",
            f"대기중: {total - posted}개",
        ])
        content = "\n".join(lines)
        if len(content) > 2000:
            content = "\n".join(lines[:45]) + "\n...(이하 생략)"
        if len(content) > 2000:
            content = content[:1950] + "\n...(이하 생략)"

        mgmt_msg_id = await self.bot.db.fetchval(
            "SELECT message_id FROM tomak_mgmt_messages WHERE thread_id = $1",
            thread_id,
        )
        if mgmt_msg_id:
            try:
                msg = await mgmt_thread.fetch_message(mgmt_msg_id)
                await msg.edit(content=content)
                return
            except discord.NotFound:
                pass

        new_msg = await mgmt_thread.send(content)
        await self.bot.db.execute(
            "INSERT INTO tomak_mgmt_messages(thread_id, message_id) VALUES($1, $2) "
            "ON CONFLICT(thread_id) DO UPDATE SET message_id = EXCLUDED.message_id",
            thread_id, new_msg.id,
        )

    def _format_tomak_reward_dm(self, reward: dict) -> str:
        lines = [
            "신청하신 토막상식이 게시되어 **이야기의 정수** 1개를 얻었습니다.",
        ]
        if reward.get("created_user"):
            lines.append("시크리타이 게임 기록이 없어 기본 계정을 함께 생성했습니다.")
        lines.append(f"현재 보유한 이야기의 정수: {reward['story_essence_count']}개")
        return "\n".join(lines)


async def setup(bot):
    await bot.add_cog(TomakManagement(bot))
