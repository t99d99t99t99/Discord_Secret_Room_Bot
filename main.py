import discord
import os
import asyncio
from discord.ext import commands
from dotenv import load_dotenv
from db import init_pool
from cogs.secretae_incremental.migration import run_pending_migrations
from cogs.localization import CommandTranslator

async def main():
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)
    bot.db = await init_pool(os.getenv("DATABASE_URL"))
    await bot.tree.set_translator(CommandTranslator())
    await run_pending_migrations(bot.db)

    await bot.load_extension('cogs.general')
    await bot.load_extension('cogs.admin')
    await bot.load_extension('cogs.notice_queues')
    await bot.load_extension('cogs.relay_stories')
    await bot.load_extension('cogs.secretae_incremental.game')
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
