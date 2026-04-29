import discord
import os
import asyncio
from discord.ext import commands
from dotenv import load_dotenv
from db import init_pool

async def main():
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)
    bot.db = await init_pool(os.getenv("DATABASE_URL"))

    await bot.load_extension('cogs.general')
    await bot.load_extension('cogs.relay_story_management')
    await bot.load_extension('cogs.kyohoon_management')
    await bot.load_extension('cogs.secretae')

    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())