import os
import re
import discord
from discord.ext import commands, tasks
import datetime
import yaml

KST = datetime.timezone(datetime.timedelta(hours=9))

# 관리 메시지 항목 형식:
# - <@user_id> (✅ 게시됨 2024-04-14T22:30:00+09:00) [mid:message_id]
# - <@user_id> (⏳ 대기중) [mid:message_id]
_ENTRY_RE = re.compile(
    r'- <@(\d+)> \((?:✅ 게시됨 ([\S]+)|⏳ 대기중)\) \[mid:(\d+)\]'
)


def _parse_entries(content: str) -> list[dict]:
    return [
        {
            'user_id':   int(m.group(1)),
            'posted_at': datetime.datetime.fromisoformat(m.group(2)) if m.group(2) else None,
            'message_id': int(m.group(3)),
        }
        for m in _ENTRY_RE.finditer(content)
    ]

def _format_entry(user_id: int, message_id: int, posted_at: datetime.datetime | None) -> str:
    status = f"✅ 게시됨 {posted_at.isoformat()}" if posted_at else "⏳ 대기중"
    return f"- <@{user_id}> ({status}) [mid:{message_id}]"

def _build_content(thread_name: str, entries: list[dict]) -> str:
    lines = [f"**{thread_name} 신청 현황**"]
    lines += [_format_entry(e['user_id'], e['message_id'], e['posted_at']) for e in entries]
    return "\n".join(lines)


async def _get_latest_thread(channel) -> discord.Thread | None:
    threads = list(channel.threads)
    async for t in channel.archived_threads(limit=None):
        threads.append(t)
    return max(threads, key=lambda t: t.created_at) if threads else None

async def _find_mgmt_message(mgmt_thread, thread_name: str) -> discord.Message | None:
    """관리 스레드에서 해당 주차 관리 메시지를 찾아 반환"""
    header = f"**{thread_name} 신청 현황**"
    async for msg in mgmt_thread.history(limit=None):
        if msg.author.bot and msg.content.startswith(header):
            return msg
    return None

async def _get_last_posted_time(mgmt_thread, user_id: int) -> datetime.datetime | None:
    """전체 관리 메시지 이력에서 사용자의 가장 최근 교훈 게시 시각을 조회"""
    last = None
    async for msg in mgmt_thread.history(limit=None):
        if not (msg.author.bot and '신청 현황' in msg.content):
            continue
        for entry in _parse_entries(msg.content):
            if entry['user_id'] == user_id and entry['posted_at']:
                if last is None or entry['posted_at'] > last:
                    last = entry['posted_at']
    return last

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
        with open('assets/message.yaml', encoding='utf-8') as f:
            self._msgs = yaml.safe_load(f)['kyohoon']
        self.update_Kyohoon.start()

    def cog_unload(self):
        self.update_Kyohoon.cancel()

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

        mgmt_msg = await _find_mgmt_message(mgmt_thread, latest_sub_thread.name)
        if mgmt_msg is None:
            return

        entries = _parse_entries(mgmt_msg.content)
        unposted = [e for e in entries if e['posted_at'] is None]
        if not unposted:
            return

        # 우선순위: 이전 교훈 게시 시점이 가장 오래된 사람 → message_id 오름차순(먼저 신청한 순)
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        priorities = [
            (await _get_last_posted_time(mgmt_thread, e['user_id']) or epoch, e['message_id'], e)
            for e in unposted
        ]
        selected = min(priorities, key=lambda x: (x[0], x[1]))[2]

        try:
            submission_msg = await latest_sub_thread.fetch_message(selected['message_id'])
        except discord.NotFound:
            return

        # 이-주의-교훈 채널에 새 스레드로 등록
        now_kst = datetime.datetime.now(KST)
        week  = _week_of_month(now_kst.date())
        title = f"이 주의 교훈, {now_kst.month}월 {week}주차, {submission_msg.author.display_name}"
        await notify_ch.create_thread(name=title, content=submission_msg.content)

        # 관리 메시지 갱신 (게시 시각 기록)
        for e in entries:
            if e['message_id'] == selected['message_id']:
                e['posted_at'] = now_kst
        await mgmt_msg.edit(content=_build_content(latest_sub_thread.name, entries))

        # 모든 신청이 게시되었으면 새 신청 스레드 생성
        if all(e['posted_at'] is not None for e in entries):
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

        # 관리 메시지에서 중복 신청 여부 확인
        mgmt_msg = await _find_mgmt_message(mgmt_thread, message.channel.name)
        entries  = _parse_entries(mgmt_msg.content) if mgmt_msg else []

        if any(e['user_id'] == author.id for e in entries):
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
        entries.append({'user_id': author.id, 'message_id': message.id, 'posted_at': None})
        new_content = _build_content(message.channel.name, entries)
        if mgmt_msg:
            await mgmt_msg.edit(content=new_content)
        else:
            await mgmt_thread.send(new_content)

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

        mgmt_thread = self.bot.get_channel(int(os.getenv("KYOHOON_MANAGEMENT_ID")))
        mgmt_msg    = await _find_mgmt_message(mgmt_thread, channel.name)
        if mgmt_msg is None:
            return

        entries = _parse_entries(mgmt_msg.content)
        matched = next((e for e in entries if e['message_id'] == message_id), None)
        if matched is None or matched['posted_at'] is None:
            return

        if user_id is None:
            user_id = matched['user_id']

        user       = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        log_thread = self.bot.get_channel(int(os.getenv("LOG_THREAD_ID")))

        try:
            await user.send(self._msgs['edit_after_posted'])
        except discord.Forbidden:
            pass
        await log_thread.send(
            f"[게시 후 수정/삭제] {user.mention}이 이미 게시된 교훈을 수정하거나 삭제했습니다."
        )


async def setup(bot):
    await bot.add_cog(KyohoonManagement(bot))