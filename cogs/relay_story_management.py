import os
import discord
from discord.ext import commands
import yaml

class RelayStoryManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_author_id = None

        with open('assets/message.yaml', encoding='utf-8') as f:
            messages = yaml.safe_load(f)
        self._warn_too_long = messages['relay_story']['too_long']
        self._warn_consecutive = messages['relay_story']['consecutive']

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        if message.channel.id != int(os.getenv("RELAY_STORY_ID")):
            return

        content = message.content
        author_id = message.author.id
        warn_template = None

        if len(content) > 200:
            warn_template = self._warn_too_long
        elif self._last_author_id == author_id:
            warn_template = self._warn_consecutive

        if warn_template:
            await message.delete()
            try:
                await message.author.send(warn_template.format(content=content))
            except discord.Forbidden:
                pass
        else:
            self._last_author_id = author_id

async def setup(bot):
    await bot.add_cog(RelayStoryManagement(bot))