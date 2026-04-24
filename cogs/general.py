import discord
from discord.ext import commands, tasks
import datetime

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @tasks.loop(time = datetime.time(22, 30, tzinfo = datetime.timezone(datetime.timedelta(hours=9))))
    async def update_Kyohoon(self):
        '''
        오늘의 교훈을 업데이트합니다
        '''

        # 일요일이 아닐 경우 메소드를 종료합니다
        if datetime.datetime.now().weekday() != 6:
            return
        
    @commands.Cog.listener()
    async def assign_Kyohoon(self, message):
        # 본인의 메시지는 무시합니다
        if message.author == self.bot.user:
            return