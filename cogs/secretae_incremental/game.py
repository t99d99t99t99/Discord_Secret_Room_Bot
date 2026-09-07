"""Discord 표시 계층입니다. 모든 상태 변경은 ``.db``에서 처리합니다."""

from __future__ import annotations
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import os
import discord
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
    prune_expired_reward_snapshots,
    due_alert_players,
    record_game_command,
    save_alert_setting,
    synthesize,
)
from .numbers import LayeredDecimal as N, format_amount
from .migration import import_legacy_game_to_guild
from ..game_config import game_enabled, reward_policy_label
from ..localization import interaction_locale, text


def _status_view(state, locale="ko"):
    """구역 사이에 Discord 구분선을 둔 현황 Components v2 레이아웃입니다."""
    sections = [
        "\n".join(
            (
                text("game.status.shards", locale, amount=format_amount(state["shards"])),
                text("game.status.essence", locale, amount=format_amount(state["essence"])),
            )
        ),
        text("game.status.organics", locale) + "\n"
        + " · ".join(
            f"{ORGANIC_SYMBOLS[k]} {format_amount(state['organics'][k])}"
            for k in COLORS
        ),
        text("game.status.colour_secrets", locale) + "\n"
        + " · ".join(
            f"{SYMBOLS[k]} {format_amount(state['secrets'][k])}" for k in COLORS
        ),
        text("game.status.shape_secrets", locale) + "\n"
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


def _amount_table(rows, change_label, locale="ko"):
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
        f"{' ' * (symbol_width + 1)}{text('game.table.result', locale).rjust(width)} "
        f"← {text('game.table.before', locale).rjust(width)} + {change_label.rjust(width)}"
    )
    table = "\n".join(
        f"{symbol.ljust(symbol_width)} {result.rjust(width)} ← {before.rjust(width)} + {gain.rjust(width)}"
        for symbol, result, before, gain in formatted_rows
    )
    return f"{header}\n{table}"


def _production_text(state, production, locale="ko"):
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
            text("game.production.complete", locale),
            f"```\n{_amount_table(rows, text('game.production.change', locale), locale)}\n```",
        )
    )


def _concentration_text(before, gain, after, title, locale="ko"):
    """농축 전후의 이야기 정수를 간결한 변화 행으로 렌더링합니다."""
    values = [format_amount(value) for value in (after, before, gain)]
    width = max(len(value) for value in values)
    return "\n".join(
        (
            text("game.concentration.title", locale, title=title),
            "```",
            text("game.concentration.line", locale, after=values[0].rjust(width), before=values[1].rjust(width), gain=values[2].rjust(width)),
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


def _price_text(state, locale="ko"):
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
        text("game.price.title", locale),
        text("game.price.shards", locale, amount=format_amount(state["shards"])),
        text("game.price.discount", locale, discount=f"{discount * 100:.0f}", multiplier=f"{square_multiplier.mag:.3f}"),
        "\n" + text("game.price.next", locale),
    ]
    for key in SECRETS:
        cost = quote_synthesis(state, key, N.of(1))
        mark = text("game.price.available" if state["shards"].is_affordable(cost) else "game.price.unavailable", locale)
        lines.append(
            text("game.price.secret", locale, symbol=SYMBOLS[key], owned=format_amount(state["secrets"][key]), cost=format_amount(cost), availability=mark)
        )
    lines.extend(
        [
            "\n" + text("game.price.concentration", locale),
            text("game.price.essence", locale, base=format_amount(base_gain), multiplier=format_amount(heart_multiplier)),
            text("game.price.gain", locale, gain=format_amount(gain), current=format_amount(state["essence"]), after=format_amount(state["essence"] + gain)),
            text("game.price.reset", locale),
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


def _game_help_text(template, game_name):
    """Render the guild-configured brand without mutating shared help text."""
    return template.format(game_name=game_name or "Essence Foundry")


class SecretaeIncremental(commands.Cog):
    """시크리타이 인크리멘탈 Discord 명령 그룹입니다."""

    game = app_commands.Group(name="게임", description="이 서버의 증분 게임")

    def __init__(self, bot):
        """도메인 계층에서 사용하는 봇 참조를 보관합니다."""
        self.bot = bot
        self._commands_synced = False
        self.send_game_alerts.start()

    def cog_unload(self):
        """코그가 내려갈 때 시간별 알림 스케줄러를 중지합니다."""
        self.send_game_alerts.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        """현재 명령 트리를 서버에 동기화해 기존 게임 명령을 교체합니다."""
        if self._commands_synced:
            return

        # Preserve the existing server's game as enabled under its configured
        # legacy name; every other guild starts disabled.
        legacy_guild = os.getenv("SECRET_ROOM_SERVER_ID")
        if legacy_guild and legacy_guild.isdecimal():
            await self.bot.db.execute(
                "INSERT INTO guild_settings(guild_id,game_enabled,game_name) VALUES($1,TRUE,'Secretae Incremental') "
                "ON CONFLICT(guild_id) DO UPDATE SET game_enabled=TRUE,game_name='Secretae Incremental',updated_at=NOW()",
                int(legacy_guild),
            )
            await import_legacy_game_to_guild(self.bot.db, int(legacy_guild))
        self._commands_synced = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """모든 /게임 호출을 일일 게임 활동으로 처리합니다."""
        if interaction.guild is None or not await game_enabled(self.bot.db, interaction.guild.id):
            await interaction.response.send_message(
                text("game.disabled", await interaction_locale(interaction)), ephemeral=True
            )
            return False
        await record_game_command(self.bot.db, interaction.guild.id, interaction.user.id)
        return True

    @tasks.loop(
        time=[time(hour, tzinfo=timezone(timedelta(hours=9))) for hour in range(24)]
    )
    async def send_game_alerts(self):
        """활동이 없을 때만 선택한 KST 시각에 플레이어에게 DM을 보냅니다."""
        await prune_expired_reward_snapshots(self.bot.db)
        now = datetime.now(timezone.utc)
        kst_now = now.astimezone(timezone(timedelta(hours=9)))
        is_concentration_deadline = (kst_now.weekday() == 6 and kst_now.hour >= 5) or (
            kst_now.weekday() == 0 and kst_now.hour < 5
        )
        guild_ids = await self.bot.db.fetch("SELECT guild_id,game_name,locale FROM guild_settings WHERE game_enabled=TRUE")
        for guild in guild_ids:
            for row in await due_alert_players(
                self.bot.db, guild["guild_id"], kst_now.hour, is_concentration_deadline, now
            ):
                try:
                    user = self.bot.get_user(
                        row["discord_id"]
                    ) or await self.bot.fetch_user(row["discord_id"])
                    messages = []
                    if row["needs_game"]:
                        messages.append(text("game.alert.daily", guild["locale"], game_name=guild["game_name"]))
                    if row["needs_concentration"]:
                        messages.append(text("game.alert.concentration", guild["locale"]))
                    await user.send(text("game.alert.prefix", guild["locale"], messages="\n".join(messages)))
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

    @game.command(name="도움말", description="이 서버의 게임 안내를 봅니다.")
    async def help(self, interaction: discord.Interaction):
        """저장된 게임 안내를 호출자에게만 보여 줍니다."""
        settings = await self.bot.db.fetchrow(
            "SELECT game_name,locale FROM guild_settings WHERE guild_id=$1", interaction.guild.id
        )
        locale = await interaction_locale(interaction)
        await interaction.response.send_message(
            text("game.help", locale, game_name=settings["game_name"] if settings else "Essence Foundry"),
            ephemeral=True,
        )

    @game.command(name="보상", description="이 서버의 공지·릴레이 게임 보상을 확인합니다.")
    async def rewards(self, interaction: discord.Interaction):
        """Show configured community rewards only when a member explicitly asks."""
        assert interaction.guild is not None
        rows = await self.bot.db.fetch(
            "SELECT 'notice_queue' AS feature,display_name,reward_policy FROM notice_queues "
            "WHERE guild_id=$1 AND state='active' AND reward_policy->>'type' <> 'none' "
            "UNION ALL SELECT 'relay_story',display_name,reward_policy FROM relay_stories "
            "WHERE guild_id=$1 AND state='active' AND reward_policy->>'type' <> 'none' "
            "ORDER BY feature,display_name",
            interaction.guild.id,
        )
        if not rows:
            await interaction.response.send_message(text("game.rewards.none", await interaction_locale(interaction)), ephemeral=True)
            return
        locale = await interaction_locale(interaction)
        lines = [text("game.rewards.title", locale)]
        lines.extend(
            text("game.rewards.line", locale, feature=text(f"game.feature.{'queue' if row['feature'] == 'notice_queue' else 'relay'}", locale), name=row['display_name'], policy=reward_policy_label(row['reward_policy'], locale))
            for row in rows
        )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @game.command(name="알림", description="게임 알림을 설정합니다.")
    async def alert(self, interaction: discord.Interaction):
        """호출자 전용 알림 유형 및 KST 시각 선택 절차를 엽니다."""
        await interaction.response.send_message(
            text("game.alert.prompt", await interaction_locale(interaction)),
            ephemeral=True,
            view=AlertTypeView(self, interaction.guild.id, interaction.user.id, await interaction_locale(interaction)),
        )

    @game.command(name="현황", description="현재 게임 현황을 봅니다.")
    async def status(self, interaction: discord.Interaction):
        """호출자의 현재 게임 자원을 보여 줍니다."""
        state = await get_status(self.bot.db, interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(view=_status_view(state, await interaction_locale(interaction)))
        legacy_guild = os.getenv("SECRET_ROOM_SERVER_ID")
        report = (
            await get_unacknowledged_migration_report(self.bot.db, interaction.user.id)
            if legacy_guild and legacy_guild.isdecimal() and interaction.guild.id == int(legacy_guild)
            else None
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
            state, summary = await produce(self.bot.db, interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(_production_text(state, summary, await interaction_locale(interaction)))
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
                self.bot.db, interaction.guild.id, interaction.user.id, key, amount
            )
            await interaction.response.send_message(
                text("game.synthesis.complete", await interaction_locale(interaction), symbol=SYMBOLS[key], amount=format_amount(N.of(amount)), cost=format_amount(cost))
            )
        except ValueError as error:
            await self._error(interaction, error)

    @game.command(name="가격", description="현재 비밀 합성 가격을 확인합니다.")
    async def price(self, interaction: discord.Interaction):
        """게임 상태를 바꾸지 않고 모든 비밀의 한 개당 합성 가격을 보여 줍니다."""
        state = await get_status(self.bot.db, interaction.guild.id, interaction.user.id)
        locale = await interaction_locale(interaction)
        rendered = _price_text(state, locale)
        if len(rendered) <= 2_000:
            await interaction.response.send_message(rendered, ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=discord.Embed(title=text("game.price.title", locale).strip("*"), description=rendered[:4_000]),
                ephemeral=True,
            )

    @game.command(
        name="최대합성", description="우선순위에 따라 가능한 비밀을 최대 합성합니다."
    )
    async def maximum_synthesis(self, interaction: discord.Interaction):
        """비밀 우선순위에 따라 파편을 가능한 만큼 사용합니다."""
        try:
            state, summary = await max_synthesize(self.bot.db, interaction.guild.id, interaction.user.id)
            rows = [
                (SYMBOLS[key], state["secrets"][key], *summary["secrets"][key])
                for key in SECRETS
                if summary["secrets"][key][1].sign
            ]
            locale = await interaction_locale(interaction)
            await interaction.response.send_message(
                "\n".join(
                    (
                        text("game.max_synthesis.complete", locale),
                        f"```\n{_amount_table(rows, text('game.max_synthesis.change', locale), locale)}\n```",
                        text("game.max_synthesis.shards", locale, symbol=SHARD_SYMBOL, spent=format_amount(summary["shards"][1]), remaining=format_amount(state["shards"])),
                    )
                )
            )
        except ValueError as error:
            await self._error(interaction, error)

    @game.command(name="농축", description="초기화하고 이야기의 정수를 얻습니다.")
    async def concentration(self, interaction: discord.Interaction):
        """개인용 농축 확인창을 보여 줍니다."""
        state = await get_status(self.bot.db, interaction.guild.id, interaction.user.id)
        if not concentration_available(state):
            return await self._error(
                interaction,
                ValueError(text("game.concentration.unavailable", await interaction_locale(interaction))),
            )
        gain = concentration_gain(state)
        if not gain.sign:
            return await self._error(
                interaction, ValueError(text("game.concentration.no_gain", await interaction_locale(interaction)))
            )

        locale = await interaction_locale(interaction)
        view = ConcentrationView(self, interaction.guild.id, interaction.user.id, locale)
        await interaction.response.send_message(
            f"{_concentration_text(state['essence'], gain, state['essence'] + gain, text('game.concentration.scheduled', locale), locale)}\n"
            + text("game.concentration.reset", locale),
            ephemeral=True,
            view=view,
        )
        view.message = await interaction.original_response()


class ConcentrationView(discord.ui.View):
    """되돌릴 수 없는 농축을 위한 개인용 확인 컨트롤입니다."""

    def __init__(self, cog, guild_id, user_id, locale="ko"):
        """1분 후 만료되는 확인 뷰를 생성합니다."""
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.message = None
        self.confirm.label = text("game.concentration.confirm_button", locale)
        self.cancel.label = text("game.concentration.cancel_button", locale)

    async def interaction_check(self, interaction):
        """이 확인창을 연 사용자만 버튼을 누를 수 있게 제한합니다."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                text("game.concentration.private_only", self.locale), ephemeral=True
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
                    content=text("game.concentration.expired", self.locale), view=self
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="농축하기", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        """사용자 확인 후 농축을 다시 계산하고 커밋합니다."""
        try:
            await interaction.response.defer()
            await concentrate(self.cog.bot.db, self.guild_id, interaction.user.id)
            self.stop()
            await interaction.followup.send(
                text("game.concentration.complete", self.locale, name=interaction.user.display_name)
            )
        except ValueError as error:
            if interaction.response.is_done():
                await interaction.followup.send(str(error), ephemeral=True)
            else:
                await interaction.response.send_message(str(error), ephemeral=True)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        """상태를 바꾸지 않고 확인창을 닫습니다."""
        self.stop()
        await interaction.response.edit_message(
            content=text("game.concentration.cancelled", self.locale), view=None
        )


class _PrivateAlertView(discord.ui.View):
    """다른 구성원이 사용자의 알림을 바꾸지 못하도록 하는 기반 클래스입니다."""

    def __init__(self, cog, guild_id, user_id, locale="en"):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                text("game.alert.private_only", self.locale), ephemeral=True
            )
            return False
        return True


class AlertTypeView(_PrivateAlertView):
    """게임 알림 설정 절차의 첫 번째 개인용 단계입니다."""

    async def _choose(self, interaction, alert_type):
        self.stop()
        if alert_type == "disabled":
            await save_alert_setting(self.cog.bot.db, self.guild_id, self.user_id, "disabled")
            await interaction.response.edit_message(
                content=text("game.alert.disabled", self.locale),
                view=None,
            )
            return

        description = text(
            "game.alert.concentration_chosen" if alert_type == "concentration" else "game.alert.daily_chosen",
            self.locale,
        )
        await interaction.response.edit_message(
            content=description, view=None
        )
        await interaction.followup.send(
            text("game.alert.choose_hour", self.locale),
            ephemeral=True,
            view=AlertHourView(self.cog, self.guild_id, self.user_id, alert_type, self.locale),
        )

    def __init__(self, cog, guild_id, user_id, locale="en"):
        super().__init__(cog, guild_id, user_id, locale)
        self.disable.label = text("game.alert.off_button", locale)
        self.concentration.label = text("game.alert.concentration_button", locale)
        self.game.label = text("game.alert.daily_button", locale)

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

    def __init__(self, alert_type, locale):
        super().__init__(
            placeholder=text("game.alert.hour_placeholder", locale),
            options=[
                discord.SelectOption(label=text("game.alert.hour_label", locale, hour=hour), value=str(hour))
                for hour in range(24)
            ],
        )
        self.alert_type = alert_type

    async def callback(self, interaction):
        view = self.view
        hour = int(self.values[0])
        await save_alert_setting(view.cog.bot.db, view.guild_id, view.user_id, self.alert_type, hour)
        view.stop()
        await interaction.response.edit_message(
            content=text("game.alert.hour_set", view.locale, hour=hour), view=None
        )
        await interaction.followup.send(
            text("game.alert.complete", view.locale), ephemeral=True
        )


class AlertHourView(_PrivateAlertView):
    """선택한 알림의 시각 선택기를 담은 개인용 두 번째 단계입니다."""

    def __init__(self, cog, guild_id, user_id, alert_type, locale="en"):
        super().__init__(cog, guild_id, user_id, locale)
        self.add_item(AlertHourSelect(alert_type, locale))


async def setup(bot):
    """인크리멘탈 게임 코그를 등록합니다."""
    await bot.add_cog(SecretaeIncremental(bot))
