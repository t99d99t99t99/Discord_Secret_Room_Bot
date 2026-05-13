import datetime
import os
import random
import yaml
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from .secretae_db import (
    COLOR_NAMES, SHAPE_NAMES, ITEM_USES_PER_DAY,
    get_user, create_user, get_factories, get_items, get_secrets,
    save_secrets, save_item, save_factory_arr, item_uses_left,
    record_item_use, consume_story_essence, produce_all_factories,
)

KST = datetime.timezone(datetime.timedelta(hours=9))

SYMBOL = [
    ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "⚪️", "⚫️"],  # CIRCLE
    ["🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "⬜️", "⬛️"],  # SQUARE
    ["❤️", "🧡", "💛", "💚", "💙", "💜", "🤍", "🖤"],   # HEART
]

COLOR_ADJ = ["빨간", "주황색", "노란", "초록색", "파란", "보라색", "하얀", "검정색"]
COLOR_NOUN = ["빨강", "주황", "노랑", "초록", "파랑", "보라", "하양", "검정"]
SHAPE_ADJ = ["원형", "사각형", "하트 모양"]
SHAPE_NOUN = ["원형", "사각형", "하트 모양"]


def _game_date() -> datetime.date:
    now = datetime.datetime.now(KST)
    if now.hour < 6:
        return (now - datetime.timedelta(days=1)).date()
    return now.date()


def _fmt(num: int) -> str:
    s = str(num)
    return s if len(s) < 5 else f"{s[0]}.{s[1:3]}e{len(s) - 1}"


def _normalize_arr(arr, width: int, height: int):
    if len(arr) == height and all(len(row) == width for row in arr):
        return arr
    if width != height and len(arr) == width and all(len(col) == height for col in arr):
        return [[arr[x][y] for x in range(width)] for y in range(height)]
    return arr


def _render_grid(arr, width: int, height: int) -> str:
    arr = _normalize_arr(arr, width, height)
    lines = []
    for y in range(height):
        line = "".join(SYMBOL[arr[y][x]["shape"]][arr[y][x]["color"]] for x in range(width))
        lines.append(line)
    return "\n".join(lines)


def _produce(arr, width: int, height: int) -> tuple[dict, dict]:
    arr = _normalize_arr(arr, width, height)
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
                    and not color_vis[nx][ny] and arr[ny][nx]["color"] == c:
                color_vis[nx][ny] = True
                n += cdfs(nx, ny, c)
        return n

    def sdfs(x, y, s):
        n = 1
        for i in range(4):
            nx, ny = x + dx[i], y + dy[i]
            if 0 <= nx < width and 0 <= ny < height \
                    and not shape_vis[nx][ny] and arr[ny][nx]["shape"] == s:
                shape_vis[nx][ny] = True
                n += sdfs(nx, ny, s)
        return n

    for x in range(width):
        for y in range(height):
            if not color_vis[x][y]:
                color_vis[x][y] = True
                c = arr[y][x]["color"]
                color_prod[c] += cdfs(x, y, c) ** 2
            if not shape_vis[x][y]:
                shape_vis[x][y] = True
                s = arr[y][x]["shape"]
                shape_prod[s] += sdfs(x, y, s) ** 2

    return color_prod, shape_prod


def _normalize_item(item: dict) -> dict:
    item_type = item.get("type", "Empty")
    rank = int(item.get("rank", 0))
    if item_type == "Paintbrush":
        return {"type": item_type, "rank": rank, "color": int(item.get("color", 0))}
    if item_type in ("Blade", "Refinery", "Compressor"):
        return {"type": item_type, "rank": rank, "shape": int(item.get("shape", 0))}
    return {"type": "Empty", "rank": 0}


def _item_label(item: dict) -> str:
    item = _normalize_item(item)
    rank = item["rank"]
    if item["type"] == "Paintbrush":
        return f"{COLOR_ADJ[item['color']]} 마법 붓 [+{rank}]"
    if item["type"] == "Blade":
        return f"{SHAPE_ADJ[item['shape']]} 조각도 [+{rank}]"
    if item["type"] == "Refinery":
        return f"{SHAPE_ADJ[item['shape']]} 비밀 정제기 [+{rank}]"
    if item["type"] == "Compressor":
        return f"{SHAPE_ADJ[item['shape']]} 비밀 압착기 [+{rank}]"
    return "빈 깡통 [+0]"


def _item_description(item: dict) -> str:
    item = _normalize_item(item)
    rank = item["rank"]
    if item["type"] == "Paintbrush":
        return f"선택 지점 주변 {rank}칸 이내를 {COLOR_NOUN[item['color']]}으로 칠합니다."
    if item["type"] == "Blade":
        return f"선택 지점 주변 {rank}칸 이내를 {SHAPE_NOUN[item['shape']]} 모양으로 세공합니다."
    if item["type"] == "Refinery":
        return f"{SHAPE_NOUN[item['shape']]} Secret을 소모해 색깔 Secret을 생성합니다."
    if item["type"] == "Compressor":
        return f"색깔 Secret을 소모해 {SHAPE_NOUN[item['shape']]} Secret을 생성합니다."
    return "아무 기능이 없는 빈 아이템입니다."


def _item_identity(item: dict):
    item = _normalize_item(item)
    if item["type"] == "Paintbrush":
        return item["type"], item["color"]
    if item["type"] in ("Blade", "Refinery", "Compressor"):
        return item["type"], item["shape"]
    return item["type"], None


def _item_power_cost(rank: int) -> int:
    return int(1000 * (1.5 ** rank))


def _item_transform_cost(rank: int) -> int:
    return int(500 * (1.5 ** rank))


def _random_item(rank: int, exclude=None) -> dict:
    candidates = []
    for color in range(len(COLOR_NAMES)):
        candidates.append(("Paintbrush", color))
    for shape in range(len(SHAPE_NAMES)):
        candidates.append(("Blade", shape))
        candidates.append(("Refinery", shape))
        candidates.append(("Compressor", shape))
    if exclude is not None:
        candidates = [candidate for candidate in candidates if candidate != exclude]
    item_type, attr = random.choice(candidates)
    if item_type == "Paintbrush":
        return {"type": item_type, "rank": rank, "color": attr}
    return {"type": item_type, "rank": rank, "shape": attr}


def _apply_factory_item(arr, width: int, height: int, item: dict, x: int, y: int):
    item = _normalize_item(item)
    arr = [[dict(cell) for cell in row] for row in _normalize_arr(arr, width, height)]
    rank = item["rank"]
    field = "color" if item["type"] == "Paintbrush" else "shape"
    value = item[field]

    for delta_x in range(-rank, rank + 1):
        for delta_y in range(-(rank - abs(delta_x)), rank - abs(delta_x) + 1):
            nx, ny = x + delta_x, y + delta_y
            if 0 <= nx < width and 0 <= ny < height:
                arr[ny][nx][field] = value

    random_values = range(len(COLOR_NAMES)) if field == "color" else range(len(SHAPE_NAMES))
    for _ in range(rank * 2 // 3):
        nx = random.randint(0, width - 1)
        ny = random.randint(0, height - 1)
        arr[ny][nx][field] = random.choice(list(random_values))
    return arr


def _secret_gain_lines(title: str, gain: dict) -> list[str]:
    lines = [title]
    for name, amount in gain.items():
        if amount:
            lines.append(f"`{name:<7}` +{_fmt(amount)}")
    if len(lines) == 1:
        lines.append("없음")
    return lines


def _format_production_result(result: dict) -> str:
    if not result.get("ok"):
        reason = result.get("reason")
        if reason == "already_produced":
            return "오늘은 이미 생산했습니다. 매일 오전 6시(KST)에 초기화됩니다."
        if reason == "no_story_essence":
            return "이야기의 정수가 부족합니다."
        return "생산을 진행할 수 없습니다."

    lines = ["**생산이 완료되었습니다!**"]
    if result.get("factories"):
        lines.append("가동 공장: " + ", ".join(
            f"{factory['slot'] + 1}번(색 {_fmt(factory['color'])} / 모양 {_fmt(factory['shape'])})"
            for factory in result["factories"]
        ))

    lines.append("\n**색깔 Secret**")
    for i, name in enumerate(COLOR_NAMES):
        produced = result["color_prod"].get(i, 0)
        after = result["color_secrets"][name]
        before = after - produced
        lines.append(f"`{name:<7}` {_fmt(after)}  ←  {_fmt(before)} + {_fmt(produced)}")

    lines.append("\n**모양 Secret**")
    for i, name in enumerate(SHAPE_NAMES):
        produced = result["shape_prod"].get(i, 0)
        after = result["shape_secrets"][name]
        before = after - produced
        lines.append(f"`{name:<7}` {_fmt(after)}  ←  {_fmt(before)} + {_fmt(produced)}")

    if result.get("used_story_essence"):
        lines.append("\n이야기의 정수 1개를 사용했습니다.")
    return "\n".join(lines)


async def _record_successful_item_use(bot, discord_id: int, date, use_story_essence: bool):
    if use_story_essence:
        await consume_story_essence(bot.db, discord_id)
    else:
        await record_item_use(bot.db, discord_id, date)


class UserBoundView(discord.ui.View):
    def __init__(self, discord_id: int, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.discord_id = discord_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("이 메뉴는 명령어를 실행한 사람만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True


class StoryEssenceProduceView(UserBoundView):
    def __init__(self, bot, discord_id: int):
        super().__init__(discord_id)
        self.bot = bot

    @discord.ui.button(label="이야기의 정수 사용", style=discord.ButtonStyle.primary)
    async def use(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        result = await produce_all_factories(
            self.bot.db, self.discord_id, _game_date(), use_story_essence=True,
        )
        await interaction.followup.send(_format_production_result(result))
        self.stop()


class ItemActionView(UserBoundView):
    def __init__(self, bot, discord_id: int, items):
        super().__init__(discord_id)
        self.bot = bot
        self.items = items

    async def _send_item_select(self, interaction: discord.Interaction, action: str):
        await interaction.response.defer()
        await interaction.followup.send(
            "대상 아이템을 선택하세요.",
            view=ItemSelectView(self.bot, self.discord_id, self.items, action),
        )

    @discord.ui.button(label="사용", style=discord.ButtonStyle.primary)
    async def use_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_item_select(interaction, "use")

    @discord.ui.button(label="강화", style=discord.ButtonStyle.secondary)
    async def upgrade_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_item_select(interaction, "upgrade")

    @discord.ui.button(label="변환", style=discord.ButtonStyle.secondary)
    async def transform_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_item_select(interaction, "transform")

    @discord.ui.button(label="분해", style=discord.ButtonStyle.danger)
    async def dismantle_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_item_select(interaction, "dismantle")


class ItemSelect(discord.ui.Select):
    def __init__(self, bot, discord_id: int, items, action: str):
        self.bot = bot
        self.discord_id = discord_id
        self.items = [_normalize_item(item) for item in items]
        self.action = action
        options = [
            discord.SelectOption(
                label=f"{idx + 1}번: {_item_label(item)}",
                description=_item_description(item)[:100],
                value=str(idx),
            )
            for idx, item in enumerate(self.items)
        ]
        super().__init__(placeholder="아이템을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        slot = int(self.values[0])
        item = self.items[slot]
        if self.action == "use":
            await _begin_item_use(interaction, self.bot, self.discord_id, slot, item, False)
        elif self.action == "upgrade":
            await _begin_item_upgrade(interaction, self.bot, self.discord_id, slot, item)
        elif self.action == "transform":
            await _begin_item_transform(interaction, self.bot, self.discord_id, slot, item)
        elif self.action == "dismantle":
            await _dismantle_item(interaction, self.bot, self.discord_id, slot, item)
        self.view.stop()


class ItemSelectView(UserBoundView):
    def __init__(self, bot, discord_id: int, items, action: str):
        super().__init__(discord_id)
        self.add_item(ItemSelect(bot, discord_id, items, action))


class StoryEssenceItemUseView(UserBoundView):
    def __init__(self, bot, discord_id: int, slot: int, item: dict):
        super().__init__(discord_id)
        self.bot = bot
        self.slot = slot
        self.item = item

    @discord.ui.button(label="이야기의 정수 사용", style=discord.ButtonStyle.primary)
    async def use(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await _begin_item_use(interaction, self.bot, self.discord_id, self.slot, self.item, True)
        self.stop()


class ItemFactorySelect(discord.ui.Select):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, factories, use_story_essence: bool):
        self.bot = bot
        self.discord_id = discord_id
        self.slot = slot
        self.item = item
        self.use_story_essence = use_story_essence
        self.factories = {f["slot"]: f for f in factories}
        options = [
            discord.SelectOption(
                label=f"{f['slot'] + 1}번 공장 ({f['width']}×{f['height']})",
                value=str(f["slot"]),
            )
            for f in factories
        ]
        super().__init__(placeholder="대상 공장을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        factory = self.factories[int(self.values[0])]
        await interaction.followup.send(
            "변경할 중심 좌표를 선택하세요.",
            embed=_factory_embed(factory),
            view=CoordinateSelectView(
                self.bot, self.discord_id, self.slot, self.item, factory, self.use_story_essence,
            ),
        )
        self.view.stop()


class ItemFactorySelectView(UserBoundView):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, factories, use_story_essence: bool):
        super().__init__(discord_id)
        self.add_item(ItemFactorySelect(bot, discord_id, slot, item, factories, use_story_essence))


class CoordinateSelect(discord.ui.Select):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, factory, use_story_essence: bool):
        self.bot = bot
        self.discord_id = discord_id
        self.slot = slot
        self.item = item
        self.factory = factory
        self.use_story_essence = use_story_essence
        options = []
        for y in range(factory["height"]):
            for x in range(factory["width"]):
                options.append(discord.SelectOption(label=f"{x + 1}번 세로줄, {y + 1}번 가로줄", value=f"{x},{y}"))
        super().__init__(placeholder="좌표를 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        x, y = map(int, self.values[0].split(","))
        updated = _apply_factory_item(
            self.factory["arr"], self.factory["width"], self.factory["height"], self.item, x, y,
        )
        await save_factory_arr(self.bot.db, self.factory["id"], updated)
        await _record_successful_item_use(self.bot, self.discord_id, _game_date(), self.use_story_essence)
        embed = discord.Embed(
            title=f"{_item_label(self.item)} 사용 결과",
            description=_render_grid(updated, self.factory["width"], self.factory["height"]),
            color=0x9B59B6,
        )
        if self.use_story_essence:
            embed.set_footer(text="이야기의 정수 1개를 사용했습니다.")
        await interaction.followup.send(embed=embed)
        self.view.stop()


class CoordinateSelectView(UserBoundView):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, factory, use_story_essence: bool):
        super().__init__(discord_id)
        self.add_item(CoordinateSelect(bot, discord_id, slot, item, factory, use_story_essence))


class ColorSelect(discord.ui.Select):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, action: str, use_story_essence: bool):
        self.bot = bot
        self.discord_id = discord_id
        self.slot = slot
        self.item = item
        self.action = action
        self.use_story_essence = use_story_essence
        options = [discord.SelectOption(label=COLOR_NOUN[i], value=str(i)) for i in range(len(COLOR_NAMES))]
        super().__init__(placeholder="색깔을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        color = int(self.values[0])
        if self.action == "refinery":
            await _send_amount_select(interaction, self.bot, self.discord_id, self.slot, self.item, "refinery", color, self.use_story_essence)
        elif self.action == "compressor":
            await _send_amount_select(interaction, self.bot, self.discord_id, self.slot, self.item, "compressor", color, self.use_story_essence)
        elif self.action == "transform":
            await _finish_transform(interaction, self.bot, self.discord_id, self.slot, self.item, color)
        self.view.stop()


class ColorSelectView(UserBoundView):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, action: str, use_story_essence: bool = False):
        super().__init__(discord_id)
        self.add_item(ColorSelect(bot, discord_id, slot, item, action, use_story_essence))


class ShapeSelect(discord.ui.Select):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, action: str):
        self.bot = bot
        self.discord_id = discord_id
        self.slot = slot
        self.item = item
        self.action = action
        options = [discord.SelectOption(label=SHAPE_NOUN[i], value=str(i)) for i in range(len(SHAPE_NAMES))]
        super().__init__(placeholder="모양 Secret을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await _finish_upgrade(interaction, self.bot, self.discord_id, self.slot, self.item, int(self.values[0]))
        self.view.stop()


class ShapeSelectView(UserBoundView):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, action: str):
        super().__init__(discord_id)
        self.add_item(ShapeSelect(bot, discord_id, slot, item, action))


class AmountSelect(discord.ui.Select):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, action: str, attr: int, amounts, use_story_essence: bool):
        self.bot = bot
        self.discord_id = discord_id
        self.slot = slot
        self.item = item
        self.action = action
        self.attr = attr
        self.use_story_essence = use_story_essence
        options = [discord.SelectOption(label=f"{amount}개", value=str(amount)) for amount in amounts[:25]]
        super().__init__(placeholder="소모 수량을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        amount = int(self.values[0])
        if self.action == "refinery":
            await _finish_refinery(interaction, self.bot, self.discord_id, self.item, self.attr, amount, self.use_story_essence)
        else:
            await _finish_compressor(interaction, self.bot, self.discord_id, self.item, self.attr, amount, self.use_story_essence)
        self.view.stop()


class AmountSelectView(UserBoundView):
    def __init__(self, bot, discord_id: int, slot: int, item: dict, action: str, attr: int, amounts, use_story_essence: bool):
        super().__init__(discord_id)
        self.add_item(AmountSelect(bot, discord_id, slot, item, action, attr, amounts, use_story_essence))


def _factory_embed(factory) -> discord.Embed:
    color_prod, shape_prod = _produce(factory["arr"], factory["width"], factory["height"])
    embed = discord.Embed(
        title=f"{factory['slot'] + 1}번 공장 ({factory['width']}×{factory['height']})",
        description=_render_grid(factory["arr"], factory["width"], factory["height"]),
        color=0x9B59B6,
    )
    embed.add_field(
        name="예상 생산량",
        value=f"색 {_fmt(sum(color_prod.values()))} / 모양 {_fmt(sum(shape_prod.values()))}",
    )
    return embed


async def _begin_item_use(interaction, bot, discord_id: int, slot: int, item: dict, use_story_essence: bool):
    item = _normalize_item(item)
    if item["type"] == "Empty":
        await interaction.followup.send("빈 깡통은 사용할 수 없습니다.")
        return
    if item["rank"] <= 0:
        await interaction.followup.send("+0 아이템은 사용할 수 없습니다. 먼저 강화해 주세요.")
        return

    user = await get_user(bot.db, discord_id)
    if use_story_essence and user["story_essence_count"] <= 0:
        await interaction.followup.send("이야기의 정수가 부족합니다.")
        return
    if not use_story_essence and item_uses_left(user, _game_date()) <= 0:
        if user["story_essence_count"] > 0:
            await interaction.followup.send(
                "오늘 아이템 사용 횟수를 모두 썼습니다. 이야기의 정수 1개로 추가 사용하시겠습니까?",
                view=StoryEssenceItemUseView(bot, discord_id, slot, item),
            )
        else:
            await interaction.followup.send(f"오늘은 이미 아이템을 {ITEM_USES_PER_DAY}번 사용했습니다.")
        return

    if item["type"] in ("Paintbrush", "Blade"):
        factories = await get_factories(bot.db, discord_id)
        await interaction.followup.send(
            "아이템을 사용할 공장을 선택하세요.",
            view=ItemFactorySelectView(bot, discord_id, slot, item, factories, use_story_essence),
        )
    elif item["type"] == "Refinery":
        await interaction.followup.send(
            "생성할 색깔 Secret을 선택하세요.",
            view=ColorSelectView(bot, discord_id, slot, item, "refinery", use_story_essence),
        )
    elif item["type"] == "Compressor":
        await interaction.followup.send(
            "소모할 색깔 Secret을 선택하세요.",
            view=ColorSelectView(bot, discord_id, slot, item, "compressor", use_story_essence),
        )


async def _send_amount_select(interaction, bot, discord_id: int, slot: int, item: dict, action: str, attr: int, use_story_essence: bool):
    secrets = await get_secrets(bot.db, discord_id)
    step = item["rank"] * 100
    if action == "refinery":
        available = secrets["shape_secrets"][SHAPE_NAMES[item["shape"]]]
    else:
        available = secrets["color_secrets"][COLOR_NAMES[attr]]
    if available < step:
        await interaction.followup.send(f"Secret이 부족합니다. 필요 수량: {step}개, 보유: {available}개")
        return
    max_amount = min(item["rank"] * 1000, available)
    amounts = list(range(step, max_amount + 1, step))
    await interaction.followup.send(
        "소모할 수량을 선택하세요.",
        view=AmountSelectView(bot, discord_id, slot, item, action, attr, amounts, use_story_essence),
    )


async def _finish_refinery(interaction, bot, discord_id: int, item: dict, target_color: int, amount: int, use_story_essence: bool):
    secrets = await get_secrets(bot.db, discord_id)
    color_secrets = dict(secrets["color_secrets"])
    shape_secrets = dict(secrets["shape_secrets"])
    shape_name = SHAPE_NAMES[item["shape"]]
    if shape_secrets[shape_name] < amount:
        await interaction.followup.send("Secret이 부족하여 아이템을 사용할 수 없습니다.")
        return
    shape_secrets[shape_name] -= amount
    gain = {name: 0 for name in COLOR_NAMES}
    others = [i for i in range(len(COLOR_NAMES)) if i != target_color]
    for _ in range(amount // 2):
        color = target_color if random.random() < 0.8 else random.choice(others)
        gain[COLOR_NAMES[color]] += 1
    for name, value in gain.items():
        color_secrets[name] += value
    await save_secrets(bot.db, discord_id, color_secrets, shape_secrets)
    await _record_successful_item_use(bot, discord_id, _game_date(), use_story_essence)
    lines = [f"{SHAPE_NOUN[item['shape']]} Secret {amount}개를 소모했습니다."]
    lines.extend(_secret_gain_lines("**색깔 Secret 획득**", gain))
    if use_story_essence:
        lines.append("\n이야기의 정수 1개를 사용했습니다.")
    await interaction.followup.send("\n".join(lines))


async def _finish_compressor(interaction, bot, discord_id: int, item: dict, source_color: int, amount: int, use_story_essence: bool):
    secrets = await get_secrets(bot.db, discord_id)
    color_secrets = dict(secrets["color_secrets"])
    shape_secrets = dict(secrets["shape_secrets"])
    color_name = COLOR_NAMES[source_color]
    if color_secrets[color_name] < amount:
        await interaction.followup.send("Secret이 부족하여 아이템을 사용할 수 없습니다.")
        return
    color_secrets[color_name] -= amount
    gain = {name: 0 for name in SHAPE_NAMES}
    others = [i for i in range(len(SHAPE_NAMES)) if i != item["shape"]]
    for _ in range(amount * 2 // 3):
        shape = item["shape"] if random.random() < 0.8 else random.choice(others)
        gain[SHAPE_NAMES[shape]] += 1
    for name, value in gain.items():
        shape_secrets[name] += value
    await save_secrets(bot.db, discord_id, color_secrets, shape_secrets)
    await _record_successful_item_use(bot, discord_id, _game_date(), use_story_essence)
    lines = [f"{COLOR_NOUN[source_color]} Secret {amount}개를 소모했습니다."]
    lines.extend(_secret_gain_lines("**모양 Secret 획득**", gain))
    if use_story_essence:
        lines.append("\n이야기의 정수 1개를 사용했습니다.")
    await interaction.followup.send("\n".join(lines))


async def _begin_item_upgrade(interaction, bot, discord_id: int, slot: int, item: dict):
    cost = _item_power_cost(item["rank"])
    secrets = await get_secrets(bot.db, discord_id)
    affordable = [i for i, name in enumerate(SHAPE_NAMES) if secrets["shape_secrets"][name] >= cost]
    if not affordable:
        await interaction.followup.send(f"모양 Secret이 부족합니다. 필요 수량: {cost}")
        return
    await interaction.followup.send(
        f"강화 비용으로 소모할 모양 Secret을 선택하세요. 필요 수량: {_fmt(cost)}",
        view=ShapeSelectView(bot, discord_id, slot, item, "upgrade"),
    )


async def _finish_upgrade(interaction, bot, discord_id: int, slot: int, item: dict, shape: int):
    cost = _item_power_cost(item["rank"])
    secrets = await get_secrets(bot.db, discord_id)
    color_secrets = dict(secrets["color_secrets"])
    shape_secrets = dict(secrets["shape_secrets"])
    shape_name = SHAPE_NAMES[shape]
    if shape_secrets[shape_name] < cost:
        await interaction.followup.send("Secret이 부족하여 강화할 수 없습니다.")
        return
    shape_secrets[shape_name] -= cost
    new_rank = item["rank"] + 1
    new_item = _random_item(new_rank) if item["type"] == "Empty" else dict(item, rank=new_rank)
    await save_secrets(bot.db, discord_id, color_secrets, shape_secrets)
    await save_item(bot.db, discord_id, slot, new_item)
    await interaction.followup.send(
        f"{SHAPE_NOUN[shape]} Secret {_fmt(cost)}개를 소모하여 강화했습니다.\n"
        f"강화 결과: {_item_label(new_item)}"
    )


async def _begin_item_transform(interaction, bot, discord_id: int, slot: int, item: dict):
    cost = _item_transform_cost(item["rank"])
    secrets = await get_secrets(bot.db, discord_id)
    affordable = [i for i, name in enumerate(COLOR_NAMES) if secrets["color_secrets"][name] >= cost]
    if not affordable:
        await interaction.followup.send(f"색깔 Secret이 부족합니다. 필요 수량: {cost}")
        return
    await interaction.followup.send(
        f"변환 비용으로 소모할 색깔 Secret을 선택하세요. 필요 수량: {_fmt(cost)}",
        view=ColorSelectView(bot, discord_id, slot, item, "transform"),
    )


async def _finish_transform(interaction, bot, discord_id: int, slot: int, item: dict, color: int):
    cost = _item_transform_cost(item["rank"])
    secrets = await get_secrets(bot.db, discord_id)
    color_secrets = dict(secrets["color_secrets"])
    shape_secrets = dict(secrets["shape_secrets"])
    color_name = COLOR_NAMES[color]
    if color_secrets[color_name] < cost:
        await interaction.followup.send("Secret이 부족하여 변환할 수 없습니다.")
        return
    color_secrets[color_name] -= cost
    before = _item_label(item)
    new_item = _random_item(item["rank"], exclude=_item_identity(item))
    await save_secrets(bot.db, discord_id, color_secrets, shape_secrets)
    await save_item(bot.db, discord_id, slot, new_item)
    await interaction.followup.send(
        f"{COLOR_NOUN[color]} Secret {_fmt(cost)}개를 소모하여 변환했습니다.\n"
        f"변환 전: {before}\n변환 후: {_item_label(new_item)}"
    )


async def _dismantle_item(interaction, bot, discord_id: int, slot: int, item: dict):
    if item["rank"] < 1:
        await interaction.followup.send("rank가 1 이상인 아이템만 분해할 수 있습니다.")
        return
    total = int(_item_power_cost(item["rank"]) * 0.7)
    color_gain = {name: 0 for name in COLOR_NAMES}
    shape_gain = {name: 0 for name in SHAPE_NAMES}
    secret_types = [("color", name) for name in COLOR_NAMES] + [("shape", name) for name in SHAPE_NAMES]
    for _ in range(total):
        secret_type, name = random.choice(secret_types)
        if secret_type == "color":
            color_gain[name] += 1
        else:
            shape_gain[name] += 1
    secrets = await get_secrets(bot.db, discord_id)
    color_secrets = dict(secrets["color_secrets"])
    shape_secrets = dict(secrets["shape_secrets"])
    for name, amount in color_gain.items():
        color_secrets[name] += amount
    for name, amount in shape_gain.items():
        shape_secrets[name] += amount
    before = _item_label(item)
    await save_secrets(bot.db, discord_id, color_secrets, shape_secrets)
    await save_item(bot.db, discord_id, slot, {"type": "Empty", "rank": 0})
    lines = [f"{before} 아이템을 분해하여 총 {_fmt(total)}개의 Secret을 얻었습니다."]
    lines.extend(_secret_gain_lines("**색깔 Secret 획득**", color_gain))
    lines.extend(_secret_gain_lines("**모양 Secret 획득**", shape_gain))
    await interaction.followup.send("\n".join(lines))


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
        user = await get_user(self.bot.db, interaction.user.id)
        if not user:
            await interaction.followup.send("`/게임 등록`으로 먼저 등록해 주세요.")
            return

        secrets = await get_secrets(self.bot.db, interaction.user.id)
        factories = await get_factories(self.bot.db, interaction.user.id)
        items = await get_items(self.bot.db, interaction.user.id)

        embed = discord.Embed(title=f"{interaction.user.display_name}의 프로필", color=0x9B59B6)

        color_lines = [f"`{n:<7}` {_fmt(secrets['color_secrets'][n])}" for n in COLOR_NAMES]
        shape_lines = [f"`{n:<7}` {_fmt(secrets['shape_secrets'][n])}" for n in SHAPE_NAMES]
        embed.add_field(name="색깔 Secret", value="\n".join(color_lines), inline=True)
        embed.add_field(name="모양 Secret", value="\n".join(shape_lines), inline=True)

        if user["story_essence_count"] > 0:
            embed.add_field(name="이야기의 정수", value=f"{user['story_essence_count']}개", inline=True)

        factory_lines = []
        for f in factories:
            color_prod, shape_prod = _produce(f["arr"], f["width"], f["height"])
            factory_lines.append(
                f"**{f['slot'] + 1}번 공장** ({f['width']}×{f['height']})\n"
                f"　색 {_fmt(sum(color_prod.values()))}  /  모양 {_fmt(sum(shape_prod.values()))}"
            )
        embed.add_field(name="보유 공장", value="\n".join(factory_lines), inline=False)

        item_lines = [f"{i + 1}번: {_item_label(item)}" for i, item in enumerate(items)]
        embed.add_field(name="보유 아이템", value="\n".join(item_lines), inline=False)

        await interaction.followup.send(embed=embed)

    @game.command(name="생산", description="세 공장을 모두 가동하여 오늘의 Secret을 생산합니다. 하루 1회 제한.")
    async def produce(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user = await get_user(self.bot.db, interaction.user.id)
        if not user:
            await interaction.followup.send("`/게임 등록`으로 먼저 등록해 주세요.")
            return
        if user["last_produced_date"] == _game_date():
            if user["story_essence_count"] > 0:
                await interaction.followup.send(
                    "오늘은 이미 생산했습니다. 이야기의 정수 1개로 추가 생산하시겠습니까?",
                    view=StoryEssenceProduceView(self.bot, interaction.user.id),
                )
            else:
                await interaction.followup.send(
                    "오늘은 이미 생산했습니다. 매일 오전 6시(KST)에 초기화됩니다."
                )
            return
        result = await produce_all_factories(self.bot.db, interaction.user.id, _game_date())
        await interaction.followup.send(_format_production_result(result))

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
        await interaction.followup.send(embed=_factory_embed(factories[번호 - 1]))

    @game.command(name="아이템", description="보유 아이템을 사용, 강화, 변환, 분해합니다.")
    async def items(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user = await get_user(self.bot.db, interaction.user.id)
        if not user:
            await interaction.followup.send("`/게임 등록`으로 먼저 등록해 주세요.")
            return
        items = await get_items(self.bot.db, interaction.user.id)
        uses_left = item_uses_left(user, _game_date())

        embed = discord.Embed(title=f"{interaction.user.display_name}의 아이템", color=0x9B59B6)
        embed.add_field(
            name="오늘 남은 아이템 사용 횟수",
            value=f"{uses_left} / {ITEM_USES_PER_DAY}",
            inline=False,
        )
        if user["story_essence_count"] > 0:
            embed.add_field(name="이야기의 정수", value=f"{user['story_essence_count']}개", inline=False)
        item_lines = [f"{i + 1}번: {_item_label(item)}" for i, item in enumerate(items)]
        embed.add_field(name="보유 아이템", value="\n".join(item_lines), inline=False)
        await interaction.followup.send(embed=embed, view=ItemActionView(self.bot, interaction.user.id, items))


async def setup(bot):
    await bot.add_cog(Secretae(bot))
