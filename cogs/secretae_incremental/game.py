"""Discord 표시 계층입니다. 모든 상태 변경은 ``.db``에서 처리합니다."""

from __future__ import annotations
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import os
import discord
import yaml
from discord import app_commands
from discord.ext import commands, tasks
from .constants import *
from .db import (
    concentrate,
    concentration_available,
    concentration_gain,
    acknowledge_migration_report,
    get_status,
    get_unacknowledged_migration_report,
    max_synthesize,
    produce,
    quote_synthesis,
    due_alert_players,
    record_game_command,
    save_alert_setting,
    synthesize,
)
from .numbers import LayeredDecimal as N, format_amount


def _status_view(state):
    """구역 사이에 Discord 구분선을 둔 현황 Components v2 레이아웃입니다."""
    sections = [
        "\n".join(
            (
                f"**비밀 파편**: {format_amount(state['shards'])}",
                f"**이야기의 정수**: {format_amount(state['essence'])}",
            )
        ),
        "**비밀 유기체**\n"
        + " · ".join(
            f"{ORGANIC_SYMBOLS[k]} {format_amount(state['organics'][k])}"
            for k in COLORS
        ),
        "**색 비밀**\n"
        + " · ".join(
            f"{SYMBOLS[k]} {format_amount(state['secrets'][k])}" for k in COLORS
        ),
        "**형태 비밀**\n"
        + " · ".join(
            f"{SYMBOLS[k]} {format_amount(state['secrets'][k])}" for k in SHAPES
        ),
    ]
    view = discord.ui.LayoutView(timeout=None)
    for index, section in enumerate(sections):
        if index:
            view.add_item(discord.ui.Separator())
        view.add_item(discord.ui.TextDisplay(section))
    return view


def _amount_table(rows, change_label):
    """결과·기존·변화량 열을 이모지와 숫자 폭에 맞춰 정렬합니다."""
    formatted_rows = [
        (symbol, *(format_amount(value) for value in values))
        for symbol, *values in rows
    ]
    width = max(
        len(value)
        for _, result, before, gain in formatted_rows
        for value in (result, before, gain)
    )
    symbol_width = max(len(symbol) for symbol, *_ in formatted_rows)
    header = (
        f"{' ' * (symbol_width + 1)}{'결과'.rjust(width)} "
        f"← {'기존'.rjust(width)} + {change_label.rjust(width)}"
    )
    table = "\n".join(
        f"{symbol.ljust(symbol_width)} {result.rjust(width)} ← {before.rjust(width)} + {gain.rjust(width)}"
        for symbol, result, before, gain in formatted_rows
    )
    return f"{header}\n{table}"


def _production_text(state, production):
    """생산 전후 및 획득량을 숫자 열이 맞춰진 결과표로 렌더링합니다."""
    rows = [(SHARD_SYMBOL, state["shards"], *production["shards"])]
    rows.extend(
        (
            ORGANIC_SYMBOLS[color],
            state["organics"][color],
            *production["organics"][color],
        )
        for color in COLORS
    )
    return "\n".join(
        (
            "**파편 및 유기체 생산을 완료했습니다!**",
            f"```\n{_amount_table(rows, '생산')}\n```",
        )
    )


def _concentration_text(before, gain, after, title):
    """농축 전후의 이야기 정수를 간결한 변화 행으로 렌더링합니다."""
    values = [format_amount(value) for value in (after, before, gain)]
    width = max(len(value) for value in values)
    return "\n".join(
        (
            f"**{title}**",
            "```",
            f"이야기의 정수 {values[0].rjust(width)} ← {values[1].rjust(width)} + {values[2].rjust(width)}",
            "```",
        )
    )


def _key(value):
    """표시 기호, 한국어 이름 또는 저장 키를 비밀 키로 해석합니다."""
    value = value.strip()
    for key, symbol in SYMBOLS.items():
        if value in (key, symbol, KOREAN_NAMES[key]):
            return key
    raise ValueError(
        """알 수 없는 비밀입니다. 기호의 이름("빨강", "주황", "노랑", "초록", "파랑", "보라", "하양", "검정", "원형", "사각형", "하트 모양")을 정확히 입력하세요."""
    )


def _price_text(state):
    """개인용 합성 및 농축 견적 전체를 렌더링합니다."""
    gap = state["highest"] - state["essence"]
    discount = (
        min(Decimal(".5"), gap.mag / 100)
        if gap.sign > 0 and gap.layer == "0"
        else Decimal(".5")
    )
    square_multiplier = N.of(1) / (
        N.of(1) + (state["secrets"][SQUARE] + N.of(1)).ln() / N.of(17)
    )
    gain = concentration_gain(state)
    heart_multiplier = N.of(1) + (state["secrets"][HEART] / N.of(100)).ceil()
    base_gain = gain / heart_multiplier if gain.sign else N.of(0)
    lines = [
        "**현재 합성 가격**",
        f"비밀 파편: {format_amount(state['shards'])}",
        f"정수 따라잡기 할인: {discount * 100:.0f}% / □ 비용 배율: {square_multiplier.mag:.3f}",
        "\n**다음 비밀 1개 합성 비용**",
    ]
    for key in SECRETS:
        cost = quote_synthesis(state, key, N.of(1))
        mark = "구매 가능" if state["shards"].is_affordable(cost) else "파편 부족"
        lines.append(
            f"{SYMBOLS[key]} 보유 {format_amount(state['secrets'][key])}개 / "
            f"비용: {format_amount(cost)} 비밀 파편 ({mark})"
        )
    lines.extend(
        [
            "\n**농축 예상**",
            f"기본 정수: {format_amount(base_gain)} / ♡ 배율: {format_amount(heart_multiplier)}",
            f"획득 정수: {format_amount(gain)} / 현재 정수: {format_amount(state['essence'])} → {format_amount(state['essence'] + gain)}",
            "농축을 실행하면 비밀 파편, 모든 유기체, 모든 비밀이 초기값으로 초기화됩니다.",
        ]
    )
    return "\n".join(lines)


def _migration_report_text(report):
    """변경 불가능한 기존 데이터 전환의 일회성 한국어 요약을 렌더링합니다."""
    essence = format_amount(N.from_json(report["legacy_essence"]))
    final = report["final_secrets"]
    lines = ["**기존 시크리타이 전환 완료**", f"이관된 이야기의 정수: {essence}"]
    lines.append(
        "최종 비밀: "
        + " · ".join(
            f"{SYMBOLS[key]} {format_amount(N.from_json(final[key]))}"
            for key in SECRETS
        )
    )
    lines.append(
        "기존 공장과 아이템은 비밀 보상으로 전환되었으며 게임에서 복구할 수 없습니다."
    )
    return "\n".join(lines)


class SecretaeIncremental(commands.Cog):
    """시크리타이 인크리멘탈 Discord 명령 그룹입니다."""

    game = app_commands.Group(name="게임", description="시크리타이 인크리멘탈")

    def __init__(self, bot):
        """도메인 계층에서 사용하는 봇 참조를 보관합니다."""
        self.bot = bot
        self._commands_synced = False
        with open("assets/message.yaml", encoding="utf-8") as message_file:
            self._help_message = yaml.safe_load(message_file)["help"][
                "secretae_incremental"
            ]
        self.send_game_alerts.start()

    def cog_unload(self):
        """코그가 내려갈 때 시간별 알림 스케줄러를 중지합니다."""
        self.send_game_alerts.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        """현재 명령 트리를 서버에 동기화해 기존 게임 명령을 교체합니다."""
        if self._commands_synced:
            return

        guild = discord.Object(id=int(os.environ["SECRET_ROOM_SERVER_ID"]))
        self.bot.tree.copy_global_to(guild=guild)
        await self.bot.tree.sync(guild=guild)
        self._commands_synced = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """모든 /게임 호출을 일일 게임 활동으로 처리합니다."""
        await record_game_command(self.bot.db, interaction.user.id)
        return True

    @tasks.loop(
        time=[time(hour, tzinfo=timezone(timedelta(hours=9))) for hour in range(24)]
    )
    async def send_game_alerts(self):
        """활동이 없을 때만 선택한 KST 시각에 플레이어에게 DM을 보냅니다."""
        now = datetime.now(timezone.utc)
        kst_now = now.astimezone(timezone(timedelta(hours=9)))
        is_concentration_deadline = (kst_now.weekday() == 6 and kst_now.hour >= 5) or (
            kst_now.weekday() == 0 and kst_now.hour < 5
        )
        for row in await due_alert_players(
            self.bot.db,
            kst_now.hour,
            is_concentration_deadline,
            now,
        ):
            try:
                user = self.bot.get_user(
                    row["discord_id"]
                ) or await self.bot.fetch_user(row["discord_id"])
                messages = []
                if row["needs_game"]:
                    messages.append(
                        "오늘 아직 시크리타이 인크리멘탈 게임을 플레이하지 않았어요."
                    )
                if row["needs_concentration"]:
                    messages.append(
                        "농축 가능 횟수가 월요일 오전 5시(KST)에 초기화됩니다. 아직 유효한 `/게임 농축`을 완료하지 않았어요."
                    )
                await user.send("게임 알림: " + "\n".join(messages))
            except discord.HTTPException:
                # 사용자가 DM을 차단할 수 있으므로 나중에 허용할 수 있게 설정은 보존합니다.
                pass

    @send_game_alerts.before_loop
    async def before_send_game_alerts(self):
        await self.bot.wait_until_ready()

    async def _error(self, interaction, error):
        """실패한 명령 결과를 호출자에게만 보냅니다."""
        if interaction.response.is_done():
            await interaction.followup.send(str(error), ephemeral=True)
        else:
            await interaction.response.send_message(str(error), ephemeral=True)

    @game.command(name="도움말", description="시크리타이 인크리멘탈 안내를 봅니다.")
    async def help(self, interaction: discord.Interaction):
        """저장된 게임 안내를 호출자에게만 보여 줍니다."""
        await interaction.response.send_message(self._help_message, ephemeral=True)

    @game.command(name="알림", description="게임 알림을 설정합니다.")
    async def alert(self, interaction: discord.Interaction):
        """호출자 전용 알림 유형 및 KST 시각 선택 절차를 엽니다."""
        await interaction.response.send_message(
            "게임 알림을 선택하세요. 알림은 기본적으로 꺼져 있으며, 선택한 설정은 본인에게만 적용됩니다.",
            ephemeral=True,
            view=AlertTypeView(self, interaction.user.id),
        )

    @game.command(name="현황", description="현재 게임 현황을 봅니다.")
    async def status(self, interaction: discord.Interaction):
        """호출자의 현재 게임 자원을 보여 줍니다."""
        state = await get_status(self.bot.db, interaction.user.id)
        await interaction.response.send_message(view=_status_view(state))
        report = await get_unacknowledged_migration_report(
            self.bot.db, interaction.user.id
        )
        if report:
            await interaction.followup.send(
                _migration_report_text(report), ephemeral=True
            )
            await acknowledge_migration_report(self.bot.db, interaction.user.id)

    @game.command(name="생산", description="오늘의 비밀 유기체 생산을 실행합니다.")
    async def production(self, interaction: discord.Interaction):
        """하루 생산을 한 번 실행합니다."""
        try:
            state, summary = await produce(self.bot.db, interaction.user.id)
            await interaction.response.send_message(_production_text(state, summary))
        except ValueError as error:
            await self._error(interaction, error)

    @game.command(name="합성", description="비밀 파편으로 비밀을 합성합니다.")
    @app_commands.describe(secret="비밀 기호 또는 이름", amount="합성할 수량")
    async def synthesis(
        self, interaction: discord.Interaction, secret: str, amount: str
    ):
        """선택한 비밀을 요청한 수량만큼 구매합니다."""
        try:
            key = _key(secret)
            cost, state = await synthesize(
                self.bot.db, interaction.user.id, key, amount
            )
            await interaction.response.send_message(
                f"{SYMBOLS[key]} 비밀 {format_amount(N.of(amount))}개를 합성했습니다. (비용 {format_amount(cost)})"
            )
        except ValueError as error:
            await self._error(interaction, error)

    @game.command(name="가격", description="현재 비밀 합성 가격을 확인합니다.")
    async def price(self, interaction: discord.Interaction):
        """게임 상태를 바꾸지 않고 모든 비밀의 한 개당 합성 가격을 보여 줍니다."""
        state = await get_status(self.bot.db, interaction.user.id)
        text = _price_text(state)
        if len(text) <= 2_000:
            await interaction.response.send_message(text, ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=discord.Embed(title="현재 합성 가격", description=text[:4_000]),
                ephemeral=True,
            )

    @game.command(
        name="최대합성", description="우선순위에 따라 가능한 비밀을 최대 합성합니다."
    )
    async def maximum_synthesis(self, interaction: discord.Interaction):
        """비밀 우선순위에 따라 파편을 가능한 만큼 사용합니다."""
        try:
            state, summary = await max_synthesize(self.bot.db, interaction.user.id)
            rows = [
                (SYMBOLS[key], state["secrets"][key], *summary["secrets"][key])
                for key in SECRETS
                if summary["secrets"][key][1].sign
            ]
            await interaction.response.send_message(
                "\n".join(
                    (
                        "**최대 합성을 완료했습니다!**",
                        f"```\n{_amount_table(rows, '합성')}\n```",
                        f"{SHARD_SYMBOL} 사용: {format_amount(summary['shards'][1])} / "
                        f"남음: {format_amount(state['shards'])}",
                    )
                )
            )
        except ValueError as error:
            await self._error(interaction, error)

    @game.command(name="농축", description="초기화하고 이야기의 정수를 얻습니다.")
    async def concentration(self, interaction: discord.Interaction):
        """개인용 농축 확인창을 보여 줍니다."""
        state = await get_status(self.bot.db, interaction.user.id)
        if not concentration_available(state):
            return await self._error(
                interaction,
                ValueError("농축은 매주 월요일 오전 5시(KST)에 초기화됩니다."),
            )
        gain = concentration_gain(state)
        if not gain.sign:
            return await self._error(
                interaction, ValueError("농축으로 얻을 정수가 없습니다.")
            )

        view = ConcentrationView(self, interaction.user.id)
        await interaction.response.send_message(
            f"{_concentration_text(state['essence'], gain, state['essence'] + gain, '농축 예정')}\n"
            "비밀 파편, 유기체, 모든 비밀이 초기화됩니다.",
            ephemeral=True,
            view=view,
        )
        view.message = await interaction.original_response()


class ConcentrationView(discord.ui.View):
    """되돌릴 수 없는 농축을 위한 개인용 확인 컨트롤입니다."""

    def __init__(self, cog, user_id):
        """1분 후 만료되는 확인 뷰를 생성합니다."""
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.message = None

    async def interaction_check(self, interaction):
        """이 확인창을 연 사용자만 버튼을 누를 수 있게 제한합니다."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "명령을 실행한 사람만 확인할 수 있습니다.", ephemeral=True
            )
            return False

        return True

    async def on_timeout(self):
        """게임 상태를 바꾸지 않고 만료된 개인 확인창을 비활성화합니다."""
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    content="농축 확인 시간이 만료되었습니다.", view=self
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="농축하기", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        """사용자 확인 후 농축을 다시 계산하고 커밋합니다."""
        try:
            gain, before, after = await concentrate(self.cog.bot.db, interaction.user.id)
            self.stop()
            await interaction.response.send_message(
                _concentration_text(before, gain, after, "농축을 완료했습니다!"),
                ephemeral=False,
            )
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        """상태를 바꾸지 않고 확인창을 닫습니다."""
        self.stop()
        await interaction.response.edit_message(
            content="농축을 취소했습니다.", view=None
        )


class _PrivateAlertView(discord.ui.View):
    """다른 구성원이 사용자의 알림을 바꾸지 못하도록 하는 기반 클래스입니다."""

    def __init__(self, cog, user_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "명령을 실행한 사람만 설정할 수 있습니다.", ephemeral=True
            )
            return False
        return True


class AlertTypeView(_PrivateAlertView):
    """게임 알림 설정 절차의 첫 번째 개인용 단계입니다."""

    async def _choose(self, interaction, alert_type):
        self.stop()
        if alert_type == "disabled":
            await save_alert_setting(self.cog.bot.db, self.user_id, "disabled")
            await interaction.response.edit_message(
                content="게임 알림 설정이 완료되었습니다. 알림이 꺼져 있습니다.",
                view=None,
            )
            return

        description = "농축 마감" if alert_type == "concentration" else "매일"
        await interaction.response.edit_message(
            content=f"{description} 알림을 선택했습니다.", view=None
        )
        await interaction.followup.send(
            "알림을 받을 시각(KST)을 선택하세요.",
            ephemeral=True,
            view=AlertHourView(self.cog, self.user_id, alert_type),
        )

    @discord.ui.button(label="알림 끄기", style=discord.ButtonStyle.secondary)
    async def disable(self, interaction, button):
        await self._choose(interaction, "disabled")

    @discord.ui.button(label="농축 마감 알림", style=discord.ButtonStyle.primary)
    async def concentration(self, interaction, button):
        await self._choose(interaction, "concentration")

    @discord.ui.button(label="매일 게임 알림", style=discord.ButtonStyle.success)
    async def game(self, interaction, button):
        await self._choose(interaction, "game")


class AlertHourSelect(discord.ui.Select):
    """한 알림 유형에 사용할 간결한 24시간 KST 선택기입니다."""

    def __init__(self, alert_type):
        super().__init__(
            placeholder="시각 선택 (KST)",
            options=[
                discord.SelectOption(label=f"{hour}시", value=str(hour))
                for hour in range(24)
            ],
        )
        self.alert_type = alert_type

    async def callback(self, interaction):
        view = self.view
        hour = int(self.values[0])
        await save_alert_setting(view.cog.bot.db, view.user_id, self.alert_type, hour)
        view.stop()
        await interaction.response.edit_message(
            content=f"매일 {hour}시(KST)에 알림을 받도록 설정했습니다.", view=None
        )
        await interaction.followup.send(
            "게임 알림 설정이 완료되었습니다.", ephemeral=True
        )


class AlertHourView(_PrivateAlertView):
    """선택한 알림의 시각 선택기를 담은 개인용 두 번째 단계입니다."""

    def __init__(self, cog, user_id, alert_type):
        super().__init__(cog, user_id)
        self.add_item(AlertHourSelect(alert_type))


async def setup(bot):
    """인크리멘탈 게임 코그를 등록합니다."""
    await bot.add_cog(SecretaeIncremental(bot))
