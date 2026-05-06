import os
import discord
from discord.ext import commands, tasks
import datetime
import yaml
from .secretae_db import COLOR_NAMES, SHAPE_NAMES, grant_kyohoon_submission_reward

KST = datetime.timezone(datetime.timedelta(hours=9))


async def _get_latest_thread(channel) -> discord.Thread | None:
    threads = list(channel.threads)
    async for t in channel.archived_threads(limit=None):
        threads.append(t)
    return max(threads, key=lambda t: t.created_at) if threads else None

def _week_of_month(date: datetime.date) -> int:
    count = 0
    d = date.replace(day=1)
    while d <= date:
        if d.weekday() == 6:
            count += 1
        d += datetime.timedelta(days=1)
    return count


class KyohoonManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._synced = False
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

        submission_ch = self.bot.get_channel(int(os.getenv("KYOHOON_SUBMISSION_ID")))
        if submission_ch is None:
            return
        latest_thread = await _get_latest_thread(submission_ch)
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

        submission_ch = self.bot.get_channel(int(os.getenv("KYOHOON_SUBMISSION_ID")))
        notify_ch     = self.bot.get_channel(int(os.getenv("KYOHOON_NOTIFY_ID")))
        mgmt_thread   = self.bot.get_channel(int(os.getenv("KYOHOON_MANAGEMENT_ID")))

        latest_sub_thread = await _get_latest_thread(submission_ch)
        if latest_sub_thread is None:
            return

        # 미게시 신청 목록 조회
        unposted = await self.bot.db.fetch(
            "SELECT user_id, message_id FROM kyohoon_submissions "
            "WHERE thread_id = $1 AND posted_at IS NULL ORDER BY submitted_at",
            latest_sub_thread.id
        )
        if not unposted:
            return

        # 우선순위: 이전 교훈 게시 시점이 가장 오래된 사람 → submitted_at 오름차순
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
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
        except discord.NotFound:
            return

        # 이-주의-교훈 채널에 새 스레드로 등록
        now_kst = datetime.datetime.now(KST)
        week  = _week_of_month(now_kst.date())
        title = f"{now_kst.month}월 {week}주차, {submission_msg.author.display_name}"
        files = [await att.to_file() for att in submission_msg.attachments]
        await notify_ch.create_thread(name=title, content=submission_msg.content, files=files)

        # DB 갱신 및 관리 메시지 표시 갱신
        await self.bot.db.execute(
            "UPDATE kyohoon_submissions SET posted_at = $1 WHERE message_id = $2",
            now_kst, selected['message_id']
        )
        await self._update_management_message(mgmt_thread, latest_sub_thread.id, latest_sub_thread.name)

        # 모든 신청이 게시되었으면 새 신청 스레드 생성
        remaining = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM kyohoon_submissions WHERE thread_id = $1 AND posted_at IS NULL",
            latest_sub_thread.id
        )
        if remaining == 0:
            new_title  = f"{now_kst.month}월 {week}주차 교훈 신청"
            new_thread = await submission_ch.create_thread(
                name=new_title,
                content="교훈 신청하세요!!!!!!!!!!",
            )
            await new_thread.send("@everyone")

    @update_Kyohoon.before_loop
    async def before_update_Kyohoon(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ #
    # 교훈 신청 감지
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        submission_ch_id = int(os.getenv("KYOHOON_SUBMISSION_ID"))
        if not isinstance(message.channel, discord.Thread):
            return
        if message.channel.parent_id != submission_ch_id:
            return

        submission_ch = self.bot.get_channel(submission_ch_id)
        latest_thread = await _get_latest_thread(submission_ch)
        log_thread    = self.bot.get_channel(int(os.getenv("LOG_THREAD_ID")))
        mgmt_thread   = self.bot.get_channel(int(os.getenv("KYOHOON_MANAGEMENT_ID")))

        content = message.content
        author  = message.author

        # 최신 스레드가 아닌 경우
        if latest_thread is None or message.channel.id != latest_thread.id:
            await message.delete()
            try:
                await author.send(self._msgs['not_latest_thread'].format(content=content))
            except discord.Forbidden:
                pass
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
            await log_thread.send(
                f"[교훈 중복 신청] {author.mention}이 이번 주차 교훈을 이미 신청했음에도 재신청하려 했습니다."
            )
            return

        # 신청 등록 및 관리 메시지 갱신
        await self.bot.db.execute(
            "INSERT INTO kyohoon_submissions(thread_id, user_id, message_id) VALUES($1, $2, $3)",
            message.channel.id, author.id, message.id
        )
        await self._update_management_message(mgmt_thread, message.channel.id, message.channel.name)
        reward = await grant_kyohoon_submission_reward(
            self.bot.db, author.id, message.channel.id, message.id,
        )
        if reward.get("awarded"):
            try:
                await author.send(self._format_kyohoon_reward_dm(reward))
            except discord.Forbidden:
                pass

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
        await self._check_posted_modification(payload.channel_id, payload.message_id, user_id)

    async def _check_posted_modification(self, channel_id: int, message_id: int, user_id: int | None):
        submission_ch_id = int(os.getenv("KYOHOON_SUBMISSION_ID"))
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.Thread) or channel.parent_id != submission_ch_id:
            return

        submission_ch = self.bot.get_channel(submission_ch_id)
        latest_thread = await _get_latest_thread(submission_ch)
        if latest_thread is None or channel.id != latest_thread.id:
            return

        row = await self.bot.db.fetchrow(
            "SELECT user_id, posted_at FROM kyohoon_submissions WHERE message_id = $1",
            message_id
        )
        if row is None or row['posted_at'] is None:
            return

        if user_id is None:
            user_id = row['user_id']

        user       = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        log_thread = self.bot.get_channel(int(os.getenv("LOG_THREAD_ID")))

        try:
            await user.send(self._msgs['edit_after_posted'])
        except discord.Forbidden:
            pass
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

    def _format_kyohoon_reward_dm(self, reward: dict) -> str:
        summary = reward["summary"]
        lines = [
            "교훈 신청이 등록되어 시크리타이 보상을 받았습니다.",
            f"보유한 모든 공장에서 각각 {summary['times']}회씩 추가 생산을 진행했습니다.",
        ]
        if reward.get("created_user"):
            lines.append("시크리타이 게임 기록이 없어 기본 계정을 함께 생성했습니다.")

        color_total = sum(summary["color"].get(name, 0) for name in COLOR_NAMES)
        shape_total = sum(summary["shape"].get(name, 0) for name in SHAPE_NAMES)
        lines.append(f"획득: 색깔 Secret {color_total}개 / 모양 Secret {shape_total}개")
        for factory in summary["factories"]:
            lines.append(
                f"- {factory['slot'] + 1}번 공장: 색 {factory['color']}개 / 모양 {factory['shape']}개"
            )
        return "\n".join(lines)


async def setup(bot):
    await bot.add_cog(KyohoonManagement(bot))
