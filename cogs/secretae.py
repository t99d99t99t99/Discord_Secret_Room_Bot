import datetime
import os
import yaml
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from .secretae_db import (
    COLOR_NAMES, SHAPE_NAMES,
    get_user, create_user, get_factories, get_secrets,
    save_secrets, update_last_produced,
)

KST = datetime.timezone(datetime.timedelta(hours=9))

SYMBOL = [
    ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "⚫️", "⚪️"],  # CIRCLE
    ["🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "⬛️", "⬜️"],  # SQUARE
    ["❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍"],   # HEART
]


def _game_date() -> datetime.date:
    now = datetime.datetime.now(KST)
    if now.hour < 6:
        return (now - datetime.timedelta(days=1)).date()
    return now.date()


def _fmt(num: int) -> str:
    s = str(num)
    return s if len(s) < 5 else f"{s[0]}.{s[1:3]}e{len(s) - 1}"


def _render_grid(arr, width: int, height: int) -> str:
    lines = []
    for y in range(height):
        line = "".join("\\" + SYMBOL[arr[x][y]["shape"]][arr[x][y]["color"]] for x in range(width))
        lines.append(line)
    return "\n".join(lines)


def _produce(arr, width: int, height: int) -> tuple[dict, dict]:
    color_prod: dict[int, int] = defaultdict(int)
    shape_prod: dict[int, int] = defaultdict(int)
    color_vis = [[False] * height for _ in range(width)]
    shape_vis = [[False] * height for _ in range(width)]
    dx = [1, -1, 0, 0]
    dy = [0, 0, 1, -1]

    def cdfs(x, y, c):
        n = 1
        for i in range(4):
            nx, ny = x + dx[i], y + dy[i]
            if 0 <= nx < width and 0 <= ny < height \
                    and not color_vis[nx][ny] and arr[nx][ny]["color"] == c:
                color_vis[nx][ny] = True
                n += cdfs(nx, ny, c)
        return n

    def sdfs(x, y, s):
        n = 1
        for i in range(4):
            nx, ny = x + dx[i], y + dy[i]
            if 0 <= nx < width and 0 <= ny < height \
                    and not shape_vis[nx][ny] and arr[nx][ny]["shape"] == s:
                shape_vis[nx][ny] = True
                n += sdfs(nx, ny, s)
        return n

    for x in range(width):
        for y in range(height):
            if not color_vis[x][y]:
                color_vis[x][y] = True
                c = arr[x][y]["color"]
                color_prod[c] += cdfs(x, y, c) ** 2
            if not shape_vis[x][y]:
                shape_vis[x][y] = True
                s = arr[x][y]["shape"]
                shape_prod[s] += sdfs(x, y, s) ** 2

    return color_prod, shape_prod


class FactorySelect(discord.ui.Select):
    def __init__(self, bot, discord_id: int, factories):
        self.bot = bot
        self.discord_id = discord_id
        self._factories = {f["slot"]: f for f in factories}

        options = []
        for f in factories:
            color_prod, shape_prod = _produce(f["arr"], f["width"], f["height"])
            options.append(discord.SelectOption(
                label=f"{f['slot'] + 1}번 공장  ({f['width']}×{f['height']})",
                description=f"색 {_fmt(sum(color_prod.values()))}  /  모양 {_fmt(sum(shape_prod.values()))}",
                value=str(f["slot"]),
            ))
        super().__init__(placeholder="가동할 공장을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        user = await get_user(self.bot.db, self.discord_id)
        if user["last_produced_date"] == _game_date():
            await interaction.followup.send(
                "이미 오늘 생산을 완료했습니다. 매일 오전 6시(KST)에 초기화됩니다.",
            )
            self.view.stop()
            return

        f = self._factories[int(self.values[0])]
        arr, width, height = f["arr"], f["width"], f["height"]
        color_prod, shape_prod = _produce(arr, width, height)

        secrets = await get_secrets(self.bot.db, self.discord_id)
        color_secrets = dict(secrets["color_secrets"])
        shape_secrets = dict(secrets["shape_secrets"])

        lines = ["**생산이 완료되었습니다!**\n", "**색깔 Secret**"]
        for i, name in enumerate(COLOR_NAMES):
            produced = color_prod.get(i, 0)
            before = color_secrets[name]
            color_secrets[name] = before + produced
            lines.append(f"`{name:<7}` {_fmt(color_secrets[name])}  ←  {_fmt(before)} + {_fmt(produced)}")

        lines.append("\n**모양 Secret**")
        for i, name in enumerate(SHAPE_NAMES):
            produced = shape_prod.get(i, 0)
            before = shape_secrets[name]
            shape_secrets[name] = before + produced
            lines.append(f"`{name:<7}` {_fmt(shape_secrets[name])}  ←  {_fmt(before)} + {_fmt(produced)}")

        await save_secrets(self.bot.db, self.discord_id, color_secrets, shape_secrets)
        await update_last_produced(self.bot.db, self.discord_id, _game_date())
        await interaction.followup.send("\n".join(lines))
        self.view.stop()


class ProduceView(discord.ui.View):
    def __init__(self, bot, discord_id: int, factories):
        super().__init__(timeout=60)
        self.add_item(FactorySelect(bot, discord_id, factories))


class Secretae(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._synced = False
        with open('assets/message.yaml', encoding='utf-8') as f:
            self._msgs = yaml.safe_load(f)['help']

    @commands.Cog.listener()
    async def on_ready(self):
        if self._synced:
            return
        self._synced = True
        guild = discord.Object(id=int(os.getenv("SECRET_ROOM_SERVER_ID")))
        self.bot.tree.copy_global_to(guild=guild)
        await self.bot.tree.sync(guild=guild)

    @app_commands.command(name="help", description="봇 기능 안내를 표시합니다.")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(self._msgs['secretae'], ephemeral=True)

    game = app_commands.Group(name="게임", description="시크리타이 게임 명령어")

    @game.command(name="등록", description="신규 유저를 게임에 등록합니다. 공장 3개가 지급됩니다.")
    async def register(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if await get_user(self.bot.db, interaction.user.id):
            await interaction.followup.send("이미 등록된 유저입니다.")
            return
        await create_user(self.bot.db, interaction.user.id)
        await interaction.followup.send(
            "등록이 완료되었습니다! 공장 3개가 지급되었어요.\n`/게임 프로필`로 현황을 확인할 수 있습니다.",
        )

    @game.command(name="프로필", description="보유 Secret과 공장 목록을 조회합니다.")
    async def profile(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not await get_user(self.bot.db, interaction.user.id):
            await interaction.followup.send("`/게임 등록`으로 먼저 등록해 주세요.")
            return

        secrets = await get_secrets(self.bot.db, interaction.user.id)
        factories = await get_factories(self.bot.db, interaction.user.id)

        embed = discord.Embed(title=f"{interaction.user.display_name}의 프로필", color=0x9B59B6)

        color_lines = [f"`{n:<7}` {_fmt(secrets['color_secrets'][n])}" for n in COLOR_NAMES]
        shape_lines = [f"`{n:<7}` {_fmt(secrets['shape_secrets'][n])}" for n in SHAPE_NAMES]
        embed.add_field(name="색깔 Secret", value="\n".join(color_lines), inline=True)
        embed.add_field(name="모양 Secret", value="\n".join(shape_lines), inline=True)

        factory_lines = []
        for f in factories:
            color_prod, shape_prod = _produce(f["arr"], f["width"], f["height"])
            factory_lines.append(
                f"**{f['slot'] + 1}번 공장** ({f['width']}×{f['height']})\n"
                f"　색 {_fmt(sum(color_prod.values()))}  /  모양 {_fmt(sum(shape_prod.values()))}"
            )
        embed.add_field(name="보유 공장", value="\n".join(factory_lines), inline=False)

        await interaction.followup.send(embed=embed)

    @game.command(name="생산", description="공장을 선택하여 오늘의 Secret을 생산합니다. 하루 1회 제한.")
    async def produce(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user = await get_user(self.bot.db, interaction.user.id)
        if not user:
            await interaction.followup.send("`/게임 등록`으로 먼저 등록해 주세요.")
            return
        if user["last_produced_date"] == _game_date():
            await interaction.followup.send(
                "오늘은 이미 생산했습니다. 매일 오전 6시(KST)에 초기화됩니다."
            )
            return
        factories = await get_factories(self.bot.db, interaction.user.id)
        view = ProduceView(self.bot, interaction.user.id, factories)
        await interaction.followup.send("가동할 공장을 선택하세요.", view=view)

    @game.command(name="공장", description="공장의 이모지 배열을 자세히 봅니다.")
    @app_commands.describe(번호="조회할 공장 번호 (1, 2, 3)")
    async def factory(self, interaction: discord.Interaction, 번호: int):
        await interaction.response.defer()
        if not await get_user(self.bot.db, interaction.user.id):
            await interaction.followup.send("`/게임 등록`으로 먼저 등록해 주세요.")
            return
        factories = await get_factories(self.bot.db, interaction.user.id)
        if not 1 <= 번호 <= len(factories):
            await interaction.followup.send(f"1~{len(factories)} 사이의 번호를 입력하세요.")
            return
        f = factories[번호 - 1]
        color_prod, shape_prod = _produce(f["arr"], f["width"], f["height"])
        embed = discord.Embed(
            title=f"{번호}번 공장  ({f['width']}×{f['height']})",
            description=_render_grid(f["arr"], f["width"], f["height"]),
            color=0x9B59B6,
        )
        embed.add_field(
            name="예상 생산량",
            value=f"색 {_fmt(sum(color_prod.values()))}  /  모양 {_fmt(sum(shape_prod.values()))}",
        )
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Secretae(bot))
