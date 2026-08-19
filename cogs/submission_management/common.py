import os
import datetime
import asyncio

import discord
from discord.ext import commands


KST = datetime.timezone(datetime.timedelta(hours=9))
SCHEDULE_NAMES = {"kyohoon", "tomak"}


async def get_latest_bot_thread(channel, bot_user_id: int) -> discord.Thread | None:
    threads = list(channel.threads)
    async for thread in channel.archived_threads(limit=None):
        threads.append(thread)
    official_threads = [thread for thread in threads if thread.owner_id == bot_user_id]
    return max(official_threads, key=lambda thread: thread.created_at) if official_threads else None


async def get_bot_thread_by_id(channel, thread_id: int, bot_user_id: int) -> discord.Thread | None:
    for thread in channel.threads:
        if thread.id == thread_id and thread.owner_id == bot_user_id:
            return thread
    async for thread in channel.archived_threads(limit=None):
        if thread.id == thread_id and thread.owner_id == bot_user_id:
            return thread
    return None


async def get_channel(bot, channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except discord.HTTPException:
        return None


def get_configured_channel(bot, env_name: str):
    channel_id = os.getenv(env_name)
    if channel_id is None:
        return None
    try:
        return bot.get_channel(int(channel_id))
    except ValueError:
        return None


def channel_detail(channel) -> str:
    return "없음" if channel is None else f"{channel.mention} (`{channel.id}`)"


def created_thread(result):
    return result.thread if hasattr(result, "thread") else result


def _validate_schedule_name(schedule_name: str):
    if schedule_name not in SCHEDULE_NAMES:
        raise ValueError(f"Unsupported submission schedule: {schedule_name}")


async def schedule_update_enabled(db, schedule_name: str) -> bool:
    _validate_schedule_name(schedule_name)
    enabled = await db.fetchval(
        "SELECT enabled FROM submission_schedule_settings WHERE schedule_name = $1",
        schedule_name,
    )
    return True if enabled is None else enabled


async def toggle_schedule_update(db, schedule_name: str) -> bool:
    _validate_schedule_name(schedule_name)
    return await db.fetchval(
        "INSERT INTO submission_schedule_settings(schedule_name, enabled) VALUES($1, FALSE) "
        "ON CONFLICT (schedule_name) DO UPDATE "
        "SET enabled = NOT submission_schedule_settings.enabled, updated_at = NOW() "
        "RETURNING enabled",
        schedule_name,
    )


def has_admin_role(member) -> bool:
    role_id = os.getenv("ADMIN_ROLE_ID")
    if role_id is None:
        return False
    try:
        return isinstance(member, discord.Member) and member.get_role(int(role_id)) is not None
    except ValueError:
        return False


async def send_log_thread_message(bot, content: str):
    log_thread = get_configured_channel(bot, "LOG_THREAD_ID")
    if log_thread is None:
        return
    if len(content) > 2000:
        content = content[:1950] + "\n...(이하 생략)"
    try:
        await log_thread.send(content)
    except discord.HTTPException:
        pass


class SubmissionManagementCog(commands.Cog):
    """신청 관리 코그의 공통 상태 및 설정 도우미입니다."""

    def __init__(self, bot):
        self.bot = bot
        self._synced = False
        self._update_lock = asyncio.Lock()

    def _get_configured_channel(self, env_name: str):
        return get_configured_channel(self.bot, env_name)

    def _has_admin_role(self, member) -> bool:
        return has_admin_role(member)

    async def _send_log_thread_message(self, content: str):
        await send_log_thread_message(self.bot, content)
