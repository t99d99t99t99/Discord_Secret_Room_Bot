import os
import datetime
import asyncio

import discord
from discord.ext import commands


KST = datetime.timezone(datetime.timedelta(hours=9))


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


def publication_marker(message_id: int) -> str:
    return f"||관리 ID: {message_id}||"


def publication_content(content: str, marker: str) -> str:
    return f"{marker}\n{content[:2000 - len(marker) - 1]}"


async def find_publication(notify_ch, marker: str):
    async for message in notify_ch.history(limit=None, oldest_first=False):
        if marker in message.content:
            return message.thread.id if message.thread else None
    return None


async def claim_publication(db, table: str, message_id: int, marker: str):
    if table not in {"kyohoon_submissions", "tomak_submissions"}:
        raise ValueError(f"Unsupported submission table: {table}")
    return await db.fetchval(
        f"UPDATE {table} "
        "SET publication_marker = $1, publishing_at = COALESCE(publishing_at, NOW()) "
        "WHERE message_id = $2 AND posted_at IS NULL RETURNING message_id",
        marker, message_id,
    )


async def recover_or_publish(db, table: str, selected, notify_ch, title: str, submission_msg):
    """Return the destination thread ID, recovering a post made before a DB failure."""
    marker = selected["publication_marker"] or publication_marker(selected["message_id"])
    claimed = await claim_publication(db, table, selected["message_id"], marker)
    if claimed is None:
        return None

    publication_thread_id = await find_publication(notify_ch, marker)
    if publication_thread_id is not None:
        return publication_thread_id

    files = [await attachment.to_file() for attachment in submission_msg.attachments]
    created = await notify_ch.create_thread(
        name=title,
        content=publication_content(submission_msg.content, marker),
        files=files,
    )
    return created_thread(created).id


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
    """Shared state and configuration helpers for submission cogs."""

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
