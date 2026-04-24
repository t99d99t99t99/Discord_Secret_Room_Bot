import os
import discord
from discord.ext import commands
import yaml

class RelayStoryManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        with open('assets/message.yaml', encoding='utf-8') as f:
            messages = yaml.safe_load(f)
        self._warn_too_long = messages['relay_story']['too_long']
        self._warn_consecutive = messages['relay_story']['consecutive']

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if message.channel.id != int(os.getenv("RELAY_STORY_ID")):
            return

        content = message.content
        warn_template = None

        if len(content) > 200:
            warn_template = self._warn_too_long
        else:
            # 채널 히스토리에서 직전 비봇 메시지 작성자와 동일인인지 확인
            async for prev in message.channel.history(limit=10, before=message):
                if not prev.author.bot:
                    if prev.author.id == message.author.id:
                        warn_template = self._warn_consecutive
                    break

        if warn_template:
            await message.delete()
            try:
                await message.author.send(warn_template.format(content=content))
            except discord.Forbidden:
                pass
            log_thread = self.bot.get_channel(int(os.getenv("LOG_THREAD_ID")))
            reason = "200자 초과" if len(content) > 200 else "연속 작성"
            await log_thread.send(
                f"[이야기잇기 규칙 위반] {message.author.mention}의 메시지가 삭제되었습니다. (사유: {reason})"
            )

async def setup(bot):
    await bot.add_cog(RelayStoryManagement(bot))