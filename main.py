import discord
import os
import asyncio
from discord.ext import commands
from dotenv import load_dotenv

async def main():
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)

    
    await bot.load_extension('cogs.general')

    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())