"""Start the SecretRoom Discord bot."""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv
from db import init_pool
from cogs.secretae_incremental.migration import run_pending_migrations
from cogs.localization import CommandTranslator

logger = logging.getLogger(__name__)


async def main():
    """Build infrastructure before loading cogs that depend on it."""
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required to start the bot")

    intents = discord.Intents.default()
    intents.message_content = True

    # Prefix commands are intentionally limited to mentions; supported commands
    # are registered as slash commands by the loaded cogs.
    bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)
    # Schema and migrations must complete before a cog can query guild state.
    bot.db = await init_pool(os.getenv("DATABASE_URL"))
    await bot.tree.set_translator(CommandTranslator())
    await run_pending_migrations(bot.db)

    # Load dependency-light cogs before feature cogs that call their services.
    await bot.load_extension("cogs.general")
    await bot.load_extension("cogs.admin")
    await bot.load_extension("cogs.notice_queues")
    await bot.load_extension("cogs.relay_stories")
    await bot.load_extension("cogs.secretae_incremental.game")
    async with bot:
        logger.info("Starting Discord bot connection")
        await bot.start(token)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
