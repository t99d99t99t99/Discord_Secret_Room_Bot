from discord.ext import commands

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def manage_Kyohoon(self, message):
        # 본인의 메시지는 무시합니다
        if message.author == self.bot.user:
            return

async def setup(bot):
    await bot.add_cog(General(bot))