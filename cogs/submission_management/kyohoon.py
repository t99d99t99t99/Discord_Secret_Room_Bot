import os
import discord
from discord.ext import commands, tasks
import datetime
import traceback
import yaml
from ..secretae_incremental.db import (
    get_submission_reward,
    grant_kyohoon_reward,
    mark_submission_rewarded,
    retry_pending_submission_rewards,
)
from ..secretae_incremental.numbers import LayeredDecimal, format_amount
from .common import (
    KST,
    channel_detail as _channel_detail,
    created_thread as _created_thread,
    get_channel as _get_channel,
    get_latest_bot_thread as _get_latest_thread,
    schedule_update_enabled,
    SubmissionManagementCog,
    toggle_schedule_update,
)

def _week_of_month(date: datetime.date) -> int:
    count = 0
    d = date.replace(day=1)
    while d <= date:
        if d.weekday() == 6:
            count += 1
        d += datetime.timedelta(days=1)
    return count


class KyohoonManagement(SubmissionManagementCog):
    def __init__(self, bot):
        super().__init__(bot)
        with open('assets/message.yaml', encoding='utf-8') as f:
            self._msgs = yaml.safe_load(f)['kyohoon']
        self.update_Kyohoon.start()

    def cog_unload(self):
        self.update_Kyohoon.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._synced:
            return
        self._synced = True

        await self._retry_pending_rewards("시작")

        submission_ch = self._get_configured_channel("KYOHOON_SUBMISSION_ID")
        if submission_ch is None:
            return
        latest_thread = await _get_latest_thread(submission_ch, self.bot.user.id)
        if latest_thread is None:
            return

        async for msg in latest_thread.history(limit=None, oldest_first=True):
            if msg.author.bot:
                continue
            await self.bot.db.execute(
                "INSERT INTO kyohoon_submissions(thread_id, user_id, message_id) "
                "VALUES($1, $2, $3) ON CONFLICT DO NOTHING",
                latest_thread.id, msg.author.id, msg.id
            )

    # ------------------------------------------------------------------ #
    # 교훈 등록 (매주 일요일 22:30 KST)
    # ------------------------------------------------------------------ #

    @tasks.loop(time=datetime.time(22, 30, tzinfo=KST))
    async def update_Kyohoon(self):
        if datetime.datetime.now(KST).weekday() != 6:
            return
        if not await schedule_update_enabled(self.bot.db, "kyohoon"):
            return

        await self._retry_pending_rewards("정기")
        await self._execute_kyohoon_update("자동", None)

    async def _retry_pending_rewards(self, trigger: str):
        summary = await retry_pending_submission_rewards(self.bot.db, "kyohoon")
        if summary["awarded"] or summary["already_awarded"] or summary["failed"]:
            await self._send_log_thread_message(
                "[교훈 보상 재시도]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 새 지급: {summary['awarded']}건\n"
                f"- 기존 지급 확인: {summary['already_awarded']}건\n"
                f"- 실패: {len(summary['failed'])}건"
            )
        return summary

    async def _execute_kyohoon_update(self, trigger: str, operator, raise_errors: bool = True) -> bool:
        started_at = datetime.datetime.now(KST)
        submission_ch = self._get_configured_channel("KYOHOON_SUBMISSION_ID")
        notify_ch     = self._get_configured_channel("KYOHOON_NOTIFY_ID")
        mgmt_thread   = self._get_configured_channel("KYOHOON_MANAGEMENT_ID")

        async with self._update_lock:
            try:
                return await self._run_kyohoon_update(
                    started_at, submission_ch, notify_ch, mgmt_thread, trigger, operator,
                )
            except Exception as exc:
                tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=5))
                await self._send_log_thread_message(
                    "[교훈 갱신 실패]\n"
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

    async def _run_kyohoon_update(self, started_at, submission_ch, notify_ch, mgmt_thread, trigger, operator):
        if submission_ch is None or notify_ch is None or mgmt_thread is None:
            await self._send_log_thread_message(
                "[교훈 갱신 실패]\n"
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
            await self._send_log_thread_message(
                "[교훈 갱신 실패]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 실행자: {operator.mention if operator else '시스템'}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 채널: {_channel_detail(submission_ch)}\n"
                "- 사유: 신청 채널에 교훈 신청 스레드가 없습니다."
            )
            return False

        # 미게시 신청 목록 조회
        unposted = await self.bot.db.fetch(
            "SELECT user_id, message_id FROM kyohoon_submissions "
            "WHERE thread_id = $1 AND posted_at IS NULL ORDER BY submitted_at",
            latest_sub_thread.id
        )
        if not unposted:
            await self._send_log_thread_message(
                "[교훈 갱신 실패]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 실행자: {operator.mention if operator else '시스템'}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 스레드: {latest_sub_thread.mention} (`{latest_sub_thread.id}`, {latest_sub_thread.name})\n"
                "- 사유: 게시 대기 중인 교훈 신청이 없습니다."
            )
            return False

        # 우선순위: 이전 교훈 게시 시점이 가장 오래된 사람 → submitted_at 오름차순
        # 삭제 이벤트를 놓쳐 DB에 남은 메시지는 제거하고 다음 신청을 시도한다.
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        submission_msg = None
        selected = None
        while unposted:
            priorities = []
            for row in unposted:
                last = await self.bot.db.fetchval(
                    "SELECT MAX(posted_at) FROM kyohoon_submissions "
                    "WHERE user_id = $1 AND posted_at IS NOT NULL",
                    row['user_id']
                ) or epoch
                priorities.append((last, row['message_id'], row))
            selected = min(priorities, key=lambda x: (x[0], x[1]))[2]

            try:
                submission_msg = await latest_sub_thread.fetch_message(selected['message_id'])
                break
            except discord.NotFound:
                await self.bot.db.execute(
                    "DELETE FROM kyohoon_submissions WHERE message_id = $1",
                    selected['message_id'],
                )
                unposted = [row for row in unposted if row['message_id'] != selected['message_id']]

        if submission_msg is None:
            await self._send_log_thread_message(
                "[교훈 갱신 실패]\n"
                f"- 실행 방식: {trigger}\n"
                f"- 실행자: {operator.mention if operator else '시스템'}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 스레드: {latest_sub_thread.mention} (`{latest_sub_thread.id}`, {latest_sub_thread.name})\n"
                f"- 선택된 신청자: <@{selected['user_id']}> (`{selected['user_id']}`)\n"
                f"- 신청 메시지 ID: `{selected['message_id']}`\n"
                "- 사유: 게시 대기 중인 신청 메시지를 찾을 수 없습니다."
            )
            return False

        # 이-주의-교훈 채널에 새 스레드로 등록
        now_kst = datetime.datetime.now(KST)
        week  = _week_of_month(now_kst.date())
        title = f"{now_kst.month}월 {week}주차, {submission_msg.author.display_name}"
        files = [await attachment.to_file() for attachment in submission_msg.attachments]
        created = await notify_ch.create_thread(
            name=title,
            content=submission_msg.content,
            files=files,
        )
        publication_thread = _created_thread(created)

        # DB 갱신 및 관리 메시지 표시 갱신
        try:
            await self.bot.db.execute(
                "UPDATE kyohoon_submissions SET posted_at = $1, reward_posted_thread_id = $2, "
                "rewarded_at = NULL WHERE message_id = $3",
                now_kst, publication_thread.id, selected['message_id'],
            )
        except Exception as exc:
            await self._send_log_thread_message(
                "[교훈 DB 갱신 실패]\n"
                "- Discord 게시물은 생성되었지만 DB 게시 상태를 저장하지 못했습니다.\n"
                f"- 신청 메시지: `{selected['message_id']}`\n"
                f"- 생성된 게시 스레드: {publication_thread.mention} (`{publication_thread.id}`)\n"
                "- 다음 갱신 전에 DB 상태를 수동으로 확인해 주세요.\n"
                f"- 사유: `{type(exc).__name__}: {exc}`"
            )
            return False

        reward = await grant_kyohoon_reward(
            self.bot.db,
            submission_msg.author.id,
            latest_sub_thread.id,
            submission_msg.id,
            publication_thread.id,
        )
        if reward["awarded"] or reward.get("already_awarded"):
            await mark_submission_rewarded(self.bot.db, "kyohoon", submission_msg.id)
        if reward["awarded"]:
            reward_message = self._format_kyohoon_reward(reward)
            try:
                await submission_msg.author.send(reward_message)
            except discord.HTTPException as exc:
                await self._send_log_thread_message(
                    f"[교훈 보상 DM 실패] 신청 메시지 `{submission_msg.id}`: {type(exc).__name__}: {exc}"
                )
            try:
                await publication_thread.send(reward_message)
            except discord.HTTPException as exc:
                await self._send_log_thread_message(
                    f"[교훈 보상 게시 알림 실패] 신청 메시지 `{submission_msg.id}`: {type(exc).__name__}: {exc}"
                )
        try:
            await self._update_management_message(mgmt_thread, latest_sub_thread.id, latest_sub_thread.name)
        except discord.HTTPException as exc:
            await self._send_log_thread_message(
                f"[교훈 관리 메시지 갱신 실패] 게시 상태는 저장되었습니다. ({type(exc).__name__}: {exc})"
            )

        # 모든 신청이 게시되었으면 새 신청 스레드 생성
        remaining = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM kyohoon_submissions WHERE thread_id = $1 AND posted_at IS NULL",
            latest_sub_thread.id
        )
        created_submission_thread = None
        if remaining == 0:
            created_submission_thread = await self._create_kyohoon_submission_thread(submission_ch, now_kst)

        await self._send_log_thread_message(
            "[교훈 갱신 성공]\n"
            f"- 실행 방식: {trigger}\n"
            f"- 실행자: {operator.mention if operator else '시스템'}\n"
            f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
            f"- 완료 시각: {datetime.datetime.now(KST):%Y-%m-%d %H:%M:%S KST}\n"
            f"- 신청 스레드: {latest_sub_thread.mention} (`{latest_sub_thread.id}`, {latest_sub_thread.name})\n"
            f"- 게시 대상자: {submission_msg.author.mention} (`{submission_msg.author.id}`)\n"
            f"- 신청 메시지: `{submission_msg.id}`\n"
            f"- 게시 제목: **{title}**\n"
            f"- 첨부파일 수: {len(submission_msg.attachments)}개\n"
            f"- 보상 지급: {'예' if reward['awarded'] else '아니오'}\n"
            f"- 남은 미게시 신청: {remaining}개\n"
            f"- 새 신청 스레드 생성: {created_submission_thread.mention if created_submission_thread else '아니오'}"
        )
        return True

    @update_Kyohoon.before_loop
    async def before_update_Kyohoon(self):
        await self.bot.wait_until_ready()

    async def _create_kyohoon_submission_thread(self, submission_ch, now_kst=None):
        if now_kst is None:
            now_kst = datetime.datetime.now(KST)
        week = _week_of_month(now_kst.date())
        new_title = f"{now_kst.month}월 {week}주차 교훈 신청"
        created = await submission_ch.create_thread(
            name=new_title,
            content="교훈 신청하세요!!!!!!!!!!",
        )
        new_thread = _created_thread(created)
        await new_thread.send("@everyone")
        return new_thread

    async def _handle_force_operation(self, message) -> bool:
        command = message.content.strip()
        if not (
            command in ("!force_update", "!force_submission", "!toggle_kyohoon", "!force_reward")
            or command.startswith("!reward_lookup ")
        ):
            return False

        force_thread = self._get_configured_channel("FORCE_OPERATION_ID")
        if force_thread is None or message.channel.id != force_thread.id:
            return True
        if not self._has_admin_role(message.author):
            await message.channel.send("이 명령어를 실행할 권한이 없습니다.")
            return True

        if command == "!force_reward":
            summary = await retry_pending_submission_rewards(self.bot.db)
            failed = "\n".join(
                f"- {kind} `{message_id}`: {reason}"
                for kind, message_id, reason in summary["failed"]
            )
            await message.channel.send(
                "보상 재시도를 완료했습니다.\n"
                f"- 새 지급: {summary['awarded']}건\n"
                f"- 기존 지급 확인: {summary['already_awarded']}건\n"
                f"- 실패: {len(summary['failed'])}건"
                + (f"\n{failed}" if failed else "")
            )
            await self._send_log_thread_message(
                "[보상 강제 재시도]\n"
                f"- 실행자: {message.author.mention}\n"
                f"- 새 지급: {summary['awarded']}건\n"
                f"- 기존 지급 확인: {summary['already_awarded']}건\n"
                f"- 실패: {len(summary['failed'])}건"
            )
            return True

        if command.startswith("!reward_lookup "):
            message_id = command.removeprefix("!reward_lookup ").strip()
            if not message_id.isdecimal():
                await message.channel.send("메시지 ID는 숫자로 입력해 주세요.")
                return True
            reward = await get_submission_reward(self.bot.db, int(message_id))
            if reward is None:
                await message.channel.send("해당 메시지 ID의 증분형 게임 보상 기록이 없습니다.")
                return True
            await message.channel.send(
                "[증분형 보상 조회]\n"
                f"- 종류: {reward['reward_type']}\n"
                f"- 신청 메시지: `{reward['message_id']}`\n"
                f"- 사용자: <@{reward['discord_id']}>\n"
                f"- 원본 스레드: `{reward['source_thread_id']}`\n"
                f"- 게시 스레드: `{reward['posted_thread_id']}`\n"
                f"- 지급 시각: {reward['posted_at']}"
            )
            return True

        if command == "!toggle_kyohoon":
            enabled = await toggle_schedule_update(self.bot.db, "kyohoon")
            state = "활성화" if enabled else "비활성화"
            await message.channel.send(f"교훈 정기 갱신을 {state}했습니다.")
            await self._send_log_thread_message(
                "[교훈 정기 갱신 설정 변경]\n"
                f"- 실행자: {message.author.mention}\n"
                f"- 변경 결과: {state}"
            )
            return True

        if command == "!force_update":
            ok = await self._execute_kyohoon_update("강제", message.author, raise_errors=False)
            await message.channel.send(
                "교훈 갱신을 강제로 실행했습니다. 상세 결과는 LOG_THREAD를 확인해 주세요."
                if ok else
                "교훈 갱신 강제 실행에 실패했습니다. 상세 사유는 LOG_THREAD를 확인해 주세요."
            )
            return True

        submission_ch = self._get_configured_channel("KYOHOON_SUBMISSION_ID")
        started_at = datetime.datetime.now(KST)
        if submission_ch is None:
            await self._send_log_thread_message(
                "[교훈 신청 스레드 강제 생성 실패]\n"
                f"- 실행자: {message.author.mention}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                "- 사유: KYOHOON_SUBMISSION_ID 채널을 찾지 못했습니다."
            )
            await message.channel.send("교훈 신청 스레드 강제 생성에 실패했습니다. 상세 사유는 LOG_THREAD를 확인해 주세요.")
            return True

        try:
            new_thread = await self._create_kyohoon_submission_thread(submission_ch, started_at)
        except Exception as exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=5))
            await self._send_log_thread_message(
                "[교훈 신청 스레드 강제 생성 실패]\n"
                f"- 실행자: {message.author.mention}\n"
                f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
                f"- 신청 채널: {_channel_detail(submission_ch)}\n"
                f"- 사유: 예외 발생 (`{type(exc).__name__}: {exc}`)\n"
                f"```py\n{tb[-1500:]}\n```"
            )
            await message.channel.send("교훈 신청 스레드 강제 생성에 실패했습니다. 상세 사유는 LOG_THREAD를 확인해 주세요.")
            return True

        await self._send_log_thread_message(
            "[교훈 신청 스레드 강제 생성 성공]\n"
            f"- 실행자: {message.author.mention}\n"
            f"- 실행 시각: {started_at:%Y-%m-%d %H:%M:%S KST}\n"
            f"- 신청 채널: {_channel_detail(submission_ch)}\n"
            f"- 생성된 스레드: {new_thread.mention} (`{new_thread.id}`, {new_thread.name})"
        )
        await message.channel.send(f"교훈 신청 스레드를 강제로 생성했습니다: {new_thread.mention}")
        return True

    # ------------------------------------------------------------------ #
    # 교훈 신청 감지
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        submission_ch = self._get_configured_channel("KYOHOON_SUBMISSION_ID")
        if submission_ch is None or thread.parent_id != submission_ch.id:
            return
        if thread.owner_id == self.bot.user.id:
            return
        try:
            await thread.delete(reason="Only bot-created Kyohoon submission threads are allowed")
        except discord.HTTPException:
            await self._send_log_thread_message(
                f"[교훈 신청 스레드 삭제 실패] 비공식 스레드 `{thread.id}`를 삭제하지 못했습니다."
            )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if await self._handle_force_operation(message):
            return

        submission_ch = self._get_configured_channel("KYOHOON_SUBMISSION_ID")
        if submission_ch is None:
            return
        submission_ch_id = submission_ch.id
        if not isinstance(message.channel, discord.Thread):
            return
        if message.channel.parent_id != submission_ch_id:
            return

        latest_thread = await _get_latest_thread(submission_ch, self.bot.user.id)
        log_thread    = self._get_configured_channel("LOG_THREAD_ID")
        mgmt_thread   = self._get_configured_channel("KYOHOON_MANAGEMENT_ID")

        content = message.content
        author  = message.author

        # 최신 스레드가 아닌 경우
        if latest_thread is None or message.channel.id != latest_thread.id:
            await message.delete()
            try:
                await author.send(self._msgs['not_latest_thread'].format(content=content))
            except discord.Forbidden:
                pass
            if log_thread:
                await log_thread.send(
                    f"[교훈 신청 위반] {author.mention}이 만료된 스레드 **{message.channel.name}**에 신청하려 했습니다."
                )
            return

        # 중복 신청 검사
        existing = await self.bot.db.fetchrow(
            "SELECT 1 FROM kyohoon_submissions WHERE thread_id = $1 AND user_id = $2",
            message.channel.id, author.id
        )
        if existing:
            await message.delete()
            try:
                await author.send(self._msgs['duplicate_submission'].format(content=content))
            except discord.Forbidden:
                pass
            if log_thread:
                await log_thread.send(
                    f"[교훈 중복 신청] {author.mention}이 이번 주차 교훈을 이미 신청했음에도 재신청하려 했습니다."
                )
            return

        # 신청 등록 및 관리 메시지 갱신
        await self.bot.db.execute(
            "INSERT INTO kyohoon_submissions(thread_id, user_id, message_id) VALUES($1, $2, $3)",
            message.channel.id, author.id, message.id
        )
        if mgmt_thread:
            await self._update_management_message(mgmt_thread, message.channel.id, message.channel.name)
    # ------------------------------------------------------------------ #
    # 신청 메시지 수정/삭제 감지
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        author_data = payload.data.get('author', {})
        if author_data.get('bot'):
            return
        user_id_str = author_data.get('id')
        if not user_id_str:
            return
        await self._check_posted_modification(payload.channel_id, payload.message_id, int(user_id_str))

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
        submission_ch_id = int(os.getenv("KYOHOON_SUBMISSION_ID"))
        channel = await _get_channel(self.bot, channel_id)
        if not isinstance(channel, discord.Thread) or channel.parent_id != submission_ch_id:
            return

        row = await self.bot.db.fetchrow(
            "DELETE FROM kyohoon_submissions WHERE message_id = $1 "
            "RETURNING user_id, posted_at",
            message_id,
        )
        if row is not None:
            mgmt_thread = self._get_configured_channel("KYOHOON_MANAGEMENT_ID")
            if mgmt_thread:
                try:
                    await self._update_management_message(mgmt_thread, channel.id, channel.name)
                except discord.HTTPException:
                    pass
        if row is not None and row['posted_at'] is not None:
            await self._check_posted_modification(channel_id, message_id, user_id, row)

    async def _check_posted_modification(self, channel_id: int, message_id: int, user_id: int | None, row=None):
        submission_ch_id = int(os.getenv("KYOHOON_SUBMISSION_ID"))
        channel = await _get_channel(self.bot, channel_id)
        if not isinstance(channel, discord.Thread) or channel.parent_id != submission_ch_id:
            return

        submission_ch = self._get_configured_channel("KYOHOON_SUBMISSION_ID")
        if submission_ch is None:
            return
        latest_thread = await _get_latest_thread(submission_ch, self.bot.user.id)
        if latest_thread is None or channel.id != latest_thread.id:
            return

        if row is None:
            row = await self.bot.db.fetchrow(
                "SELECT user_id, posted_at FROM kyohoon_submissions WHERE message_id = $1",
                message_id
            )
        if row is None or row['posted_at'] is None:
            return

        if user_id is None:
            user_id = row['user_id']

        user       = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        log_thread = self._get_configured_channel("LOG_THREAD_ID")

        try:
            await user.send(self._msgs['edit_after_posted'])
        except discord.Forbidden:
            pass
        if log_thread:
            await log_thread.send(
                f"[게시 후 수정/삭제] {user.mention}이 이미 게시된 교훈을 수정하거나 삭제했습니다."
            )

    # ------------------------------------------------------------------ #
    # 관리 메시지 갱신 (교훈 관리 스레드)
    # ------------------------------------------------------------------ #

    async def _update_management_message(self, mgmt_thread, thread_id: int, thread_name: str):
        rows = await self.bot.db.fetch(
            "SELECT user_id, posted_at FROM kyohoon_submissions "
            "WHERE thread_id = $1 ORDER BY submitted_at",
            thread_id
        )

        lines = [f"**{thread_name} 신청 현황**"]
        for row in rows:
            status = "✅ 게시됨" if row['posted_at'] else "⏳ 대기중"
            lines.append(f"- <@{row['user_id']}> ({status})")
        content = "\n".join(lines)
        if len(content) > 2000:
            content = content[:1950] + "\n...(이하 생략)"

        mgmt_msg_id = await self.bot.db.fetchval(
            "SELECT message_id FROM kyohoon_mgmt_messages WHERE thread_id = $1",
            thread_id
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
            "INSERT INTO kyohoon_mgmt_messages(thread_id, message_id) VALUES($1, $2) "
            "ON CONFLICT(thread_id) DO UPDATE SET message_id = EXCLUDED.message_id",
            thread_id, new_msg.id
        )

    def _format_kyohoon_reward(self, reward: dict) -> str:
        """커밋된 인크리멘탈 교훈 보상을 DM과 스레드용으로 렌더링합니다."""
        before = format_amount(LayeredDecimal.from_json(reward["before_essence"]))
        after = format_amount(LayeredDecimal.from_json(reward["after_essence"]))
        gained = format_amount(LayeredDecimal.from_json(reward["reward_amount"]))
        return (
            "교훈이 게시되어 이야기의 정수 보상을 받았습니다.\n"
            f"정수: {before} → {after} (획득 {gained})\n"
            "기존 정수가 0이면 첫 보상으로 1을 받고, 그 외에는 정수가 두 배가 됩니다."
        )


async def setup(bot):
    await bot.add_cog(KyohoonManagement(bot))
