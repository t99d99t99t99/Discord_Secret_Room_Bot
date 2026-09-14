"""Guild-scoped administrator slash commands and setup state.

This cog intentionally keeps the slash-command declarations together: Discord
registers them as one ``/admin`` tree and their decorators are easiest to audit
beside their authorization checks.  Reusable persistence and confirmation
mechanics live in :mod:`cogs.admin_support` instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from .secretae_incremental.db import get_submission_reward
from .game_config import REWARD_TYPES, reward_policy, reward_policy_label
from .localization import guild_locale, interaction_locale, text
from .admin_support import ConfirmationView, guild_settings as _settings


class Admin(commands.Cog):
    """The administrative surface for every guild."""

    admin = app_commands.Group(
        name="admin",
        description="Configure and operate CommunityNoticeBot.",
        default_permissions=discord.Permissions(manage_guild=True),
    )
    setup = app_commands.Group(
        name="setup", description="Guided server setup.", parent=admin
    )
    queue = app_commands.Group(
        name="queue", description="Operate imported notice queues.", parent=admin
    )
    relay = app_commands.Group(
        name="relay", description="릴레이 이야기를 관리합니다.", parent=admin
    )
    game = app_commands.Group(
        name="game", description="Essence Foundry 게임을 관리합니다.", parent=admin
    )
    diagnostics = app_commands.Group(
        name="diagnostics",
        description="Inspect configuration and legacy migration state.",
        parent=admin,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False

    @staticmethod
    def _label(prefix: str, value: str | None, locale: str) -> str:
        """Render stored enum values without exposing database identifiers."""
        try:
            return text(f"admin.label.{prefix}.{value or 'none'}", locale)
        except KeyError:
            return str(value or "")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._synced:
            await self.bot.tree.sync()
            self._synced = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Authorize administrators, with a narrow moderator exception."""
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.send_message(
                text("admin.server_only", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return False
        # Ensure a first-use guild has settings before evaluating configured roles.
        row = await _settings(self.bot.db, interaction.guild.id)
        member = interaction.user
        allowed = (
            interaction.guild.owner_id == member.id
            or member.guild_permissions.manage_guild
            or (
                row["admin_role_id"] is not None
                and member.get_role(row["admin_role_id"]) is not None
            )
        )
        if (
            not allowed
            and interaction.command
            and interaction.command.name == "moderate"
        ):
            # Moderation commands may be delegated without granting full admin access.
            policy = row["moderator_policy"]
            allowed = (
                policy == "manage_messages" and member.guild_permissions.manage_messages
            ) or (
                policy == "role"
                and row["moderator_role_id"] is not None
                and member.get_role(row["moderator_role_id"]) is not None
            )
            message_id = getattr(interaction.namespace, "message_id", None)
            if not allowed and isinstance(message_id, str) and message_id.isdecimal():
                # A relay can further delegate its own moderation policy.
                story_policy = await self.bot.db.fetchrow(
                    "SELECT s.moderator_policy,s.moderator_role_id FROM relay_contributions c JOIN relay_stories s ON s.id=c.story_id WHERE s.guild_id=$1 AND c.message_id=$2",
                    interaction.guild.id,
                    int(message_id),
                )
                if story_policy:
                    allowed = (
                        story_policy["moderator_policy"] == "manage_messages"
                        and member.guild_permissions.manage_messages
                    ) or (
                        story_policy["moderator_policy"] == "role"
                        and story_policy["moderator_role_id"] is not None
                        and member.get_role(story_policy["moderator_role_id"])
                        is not None
                    )
        if not allowed:
            await interaction.response.send_message(
                text("admin.denied", await interaction_locale(interaction)),
                ephemeral=True,
            )
        return allowed

    async def _audit(
        self,
        interaction: discord.Interaction,
        action: str,
        target: str,
        before: dict,
        after: dict,
    ):
        assert interaction.guild is not None
        # Persist audit evidence first; Discord delivery is best effort only.
        await self.bot.db.execute(
            "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,before_state,after_state) VALUES($1,$2,$3,$4,$5,$6)",
            interaction.guild.id,
            interaction.user.id,
            action,
            target,
            before,
            after,
        )
        audit_id = after.get("audit_channel_id")
        if audit_id is None:
            # Most changes do not replace the audit channel, so use stored settings.
            settings = await _settings(self.bot.db, interaction.guild.id)
            audit_id = settings["audit_channel_id"]
        if audit_id:
            channel = interaction.guild.get_channel(audit_id)
            if channel and hasattr(channel, "send"):
                try:
                    await channel.send(
                        f"[CommunityNoticeBot audit] {action} by {interaction.user.mention}"
                    )
                except discord.HTTPException:
                    pass

    async def _notify_moderators(
        self, guild: discord.Guild, actor: discord.Member, content: str
    ):
        """Send an operational outcome to the audit destination, or to moderators.

        The reply command deletes its own evidence from the public channel, so a
        successful action must still leave a visible operational result elsewhere.
        """
        settings = await _settings(self.bot.db, guild.id)
        audit_id = settings["audit_channel_id"]
        if audit_id:
            channel = guild.get_channel(audit_id) or self.bot.get_channel(audit_id)
            if channel and hasattr(channel, "send"):
                try:
                    await channel.send(content)
                    return
                except discord.HTTPException:
                    pass

        # If no audit channel is available, notify every applicable moderator once.
        recipients = {actor.id: actor}
        for member in guild.members:
            if member.bot:
                continue
            if member.id == guild.owner_id or member.guild_permissions.manage_guild:
                recipients[member.id] = member
            elif settings["admin_role_id"] and member.get_role(
                settings["admin_role_id"]
            ):
                recipients[member.id] = member
            elif (
                settings["moderator_policy"] == "manage_messages"
                and member.guild_permissions.manage_messages
            ):
                recipients[member.id] = member
            elif (
                settings["moderator_policy"] == "role"
                and settings["moderator_role_id"]
                and member.get_role(settings["moderator_role_id"])
            ):
                recipients[member.id] = member
        for member in recipients.values():
            try:
                await member.send(content)
            except discord.HTTPException:
                pass

    async def _message_moderator_allowed(
        self, member: discord.Member, relay_row=None
    ) -> bool:
        settings = await _settings(self.bot.db, member.guild.id)
        allowed = (
            member.guild.owner_id == member.id
            or member.guild_permissions.manage_guild
            or (
                settings["admin_role_id"] is not None
                and member.get_role(settings["admin_role_id"]) is not None
            )
            or (
                settings["moderator_policy"] == "manage_messages"
                and member.guild_permissions.manage_messages
            )
            or (
                settings["moderator_policy"] == "role"
                and settings["moderator_role_id"] is not None
                and member.get_role(settings["moderator_role_id"]) is not None
            )
        )
        if allowed or relay_row is None:
            return allowed
        return (
            relay_row["moderator_policy"] == "manage_messages"
            and member.guild_permissions.manage_messages
        ) or (
            relay_row["moderator_policy"] == "role"
            and relay_row["moderator_role_id"] is not None
            and member.get_role(relay_row["moderator_role_id"]) is not None
        )

    async def _register_reconciliation(
        self, guild_id: int, message_id: int, reason: str
    ):
        from .secretae_incremental.db import register_reward_reconciliation

        case = await register_reward_reconciliation(
            self.bot.db, guild_id, "relay_story", message_id, reason
        )
        if case:
            await self.bot.db.execute(
                "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) "
                "VALUES($1,0,'reward_reconciliation_required','relay_contribution',$2,$3)",
                guild_id,
                str(message_id),
                case,
            )
        return case

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Reply-based moderation is the sole supported legacy ``!`` command.

        Discord does not expose a replied-to message to slash commands, so this
        is intentionally parsed here rather than restored as a general prefix
        command subsystem.
        """
        if (
            message.author.bot
            or message.guild is None
            or not isinstance(message.author, discord.Member)
        ):
            return
        parts = message.content.strip().split(maxsplit=2)
        if not parts or parts[0].lower() != "!moderate":
            return
        if message.reference is None or message.reference.message_id is None:
            await message.reply(
                text(
                    "admin.reply.no_target",
                    await guild_locale(
                        self.bot.db, message.guild.id, message.guild.preferred_locale
                    ),
                ),
                mention_author=False,
                delete_after=15,
            )
            return
        if len(parts) < 2 or parts[1].lower() not in {"reject", "disqualify", "skip"}:
            await message.reply(
                text(
                    "admin.reply.usage",
                    await guild_locale(
                        self.bot.db, message.guild.id, message.guild.preferred_locale
                    ),
                ),
                mention_author=False,
                delete_after=15,
            )
            return
        target_id = message.reference.message_id
        action = parts[1].lower()
        reason = parts[2] if len(parts) == 3 else None
        queue_row = await self.bot.db.fetchrow(
            "SELECT s.id,q.id AS queue_id,q.display_name FROM notice_submissions s JOIN notice_queues q ON q.id=s.queue_id WHERE q.guild_id=$1 AND s.source_message_id=$2 AND s.status='eligible'",
            message.guild.id,
            target_id,
        )
        relay_row = None
        if queue_row is None:
            relay_row = await self.bot.db.fetchrow(
                "SELECT c.id,s.id AS story_id,s.display_name,s.channel_id,s.moderator_policy,s.moderator_role_id FROM relay_contributions c JOIN relay_stories s ON s.id=c.story_id WHERE s.guild_id=$1 AND c.message_id=$2 AND c.status='eligible'",
                message.guild.id,
                target_id,
            )
        if queue_row is None and relay_row is None:
            await message.reply(
                text(
                    "admin.reply.not_eligible",
                    await guild_locale(
                        self.bot.db, message.guild.id, message.guild.preferred_locale
                    ),
                ),
                mention_author=False,
                delete_after=15,
            )
            return
        if not await self._message_moderator_allowed(message.author, relay_row):
            await message.reply(
                text(
                    "admin.reply.denied",
                    await guild_locale(
                        self.bot.db, message.guild.id, message.guild.preferred_locale
                    ),
                ),
                mention_author=False,
                delete_after=15,
            )
            return
        if queue_row:
            status = "rejected" if action == "reject" else "invalid"
            await self.bot.db.execute(
                "UPDATE notice_submissions SET status=$2,moderated_at=NOW(),moderator_id=$3,moderation_reason=$4 WHERE id=$1",
                queue_row["id"],
                status,
                message.author.id,
                reason,
            )
            target_name, target_kind = queue_row["display_name"], "공지 대기열 신청"
            audit_target = "notice_submission"
        else:
            resolved = message.reference.resolved
            try:
                if isinstance(resolved, discord.Message):
                    await resolved.delete()
            except discord.HTTPException:
                pass
            await self.bot.db.execute(
                "UPDATE relay_contributions SET status='disqualified',moderated_at=NOW(),moderator_id=$2,moderation_reason=$3 WHERE id=$1",
                relay_row["id"],
                message.author.id,
                reason,
            )
            await self._register_reconciliation(
                message.guild.id, target_id, reason or "reply moderation"
            )
            target_name, target_kind = relay_row["display_name"], "릴레이 기여"
            audit_target = "relay_contribution"
        await self.bot.db.execute(
            "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) VALUES($1,$2,'reply_moderation',$3,$4,$5)",
            message.guild.id,
            message.author.id,
            audit_target,
            str(target_id),
            {"action": action, "reason": reason},
        )
        await message.delete()
        await self._notify_moderators(
            message.guild,
            message.author,
            f"[CommunityNoticeBot] {message.author.mention}님이 **{target_name}**의 {target_kind} (`{target_id}`)를 `{action}` 처리했습니다."
            + (f" 사유: {reason}" if reason else ""),
        )

    async def _confirm(
        self,
        interaction: discord.Interaction,
        prompt: str,
        action: Callable[[], Awaitable[str]],
    ):
        await interaction.response.send_message(
            prompt,
            ephemeral=True,
            view=ConfirmationView(
                interaction.user.id, action, await interaction_locale(interaction)
            ),
        )

    @setup.command(name="start", description="Start or resume guided setup.")
    async def setup_start(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        previous = await _settings(self.bot.db, interaction.guild.id)
        locale = (
            "ko"
            if (
                interaction.guild.preferred_locale
                and interaction.guild.preferred_locale.value.startswith("ko")
            )
            else "en"
        )
        await self.bot.db.execute(
            "UPDATE guild_settings SET setup_state='in_progress', locale=$2, updated_at=NOW() WHERE guild_id=$1",
            interaction.guild.id,
            locale,
        )
        await self._audit(
            interaction,
            "setup_started",
            "guild_settings",
            {"setup_state": previous["setup_state"]},
            {"setup_state": "in_progress"},
        )
        language = text(f"admin.setup.language.{locale}", locale)
        await interaction.response.send_message(
            text("admin.setup.started", locale, language=language), ephemeral=True
        )

    @setup.command(
        name="status", description="Show setup progress and disabled optional features."
    )
    async def setup_status(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        row = await _settings(self.bot.db, interaction.guild.id)
        locale = await interaction_locale(interaction)
        missing = []
        if not row["audit_channel_id"]:
            missing.append(text("admin.setup.audit_missing", locale))
        if not row["admin_role_id"]:
            missing.append(text("admin.setup.role_missing", locale))
        await interaction.response.send_message(
            text(
                "admin.setup.status",
                locale,
                state=row["setup_state"],
                language=text(f"admin.setup.language.{row['locale']}", locale),
                game=text(
                    (
                        "admin.setup.game_enabled"
                        if row["game_enabled"]
                        else "admin.setup.game_disabled"
                    ),
                    locale,
                ),
                moderation=row["moderator_policy"],
                missing=(
                    ", ".join(missing)
                    if missing
                    else text("admin.setup.nothing_missing", locale)
                ),
            ),
            ephemeral=True,
        )

    @setup.command(
        name="set-language", description="Set the server's default language."
    )
    @app_commands.choices(
        locale=[
            app_commands.Choice(name="English", value="en"),
            app_commands.Choice(name="한국어", value="ko"),
        ]
    )
    async def set_language(
        self, interaction: discord.Interaction, locale: app_commands.Choice[str]
    ):
        assert interaction.guild is not None
        old = await _settings(self.bot.db, interaction.guild.id)
        await self.bot.db.execute(
            "UPDATE guild_settings SET locale=$2, updated_at=NOW() WHERE guild_id=$1",
            interaction.guild.id,
            locale.value,
        )
        await self._audit(
            interaction,
            "locale_changed",
            "guild_settings",
            {"locale": old["locale"]},
            {"locale": locale.value},
        )
        await interaction.response.send_message(
            text(
                "admin.setup.language_set",
                locale.value,
                language=text(f"admin.setup.language.{locale.value}", locale.value),
            ),
            ephemeral=True,
        )

    @setup.command(
        name="set-admin-role", description="Allow a role to administer the bot."
    )
    async def set_admin_role(
        self, interaction: discord.Interaction, role: discord.Role | None = None
    ):
        assert interaction.guild is not None
        old = await _settings(self.bot.db, interaction.guild.id)
        await self.bot.db.execute(
            "UPDATE guild_settings SET admin_role_id=$2, updated_at=NOW() WHERE guild_id=$1",
            interaction.guild.id,
            role.id if role else None,
        )
        await self._audit(
            interaction,
            "admin_role_changed",
            "guild_settings",
            {"admin_role_id": old["admin_role_id"]},
            {"admin_role_id": role.id if role else None},
        )
        await interaction.response.send_message(
            text(
                (
                    "admin.setup.admin_role_set"
                    if role
                    else "admin.setup.admin_role_removed"
                ),
                await interaction_locale(interaction),
            ),
            ephemeral=True,
        )

    @setup.command(
        name="set-audit-channel",
        description="Set the optional administrator audit destination.",
    )
    async def set_audit_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        assert interaction.guild is not None
        old = await _settings(self.bot.db, interaction.guild.id)
        channel_id = channel.id if channel else None
        await self.bot.db.execute(
            "UPDATE guild_settings SET audit_channel_id=$2, updated_at=NOW() WHERE guild_id=$1",
            interaction.guild.id,
            channel_id,
        )
        await self._audit(
            interaction,
            "audit_channel_changed",
            "guild_settings",
            {"audit_channel_id": old["audit_channel_id"]},
            {"audit_channel_id": channel_id},
        )
        await interaction.response.send_message(
            text(
                "admin.setup.audit_set" if channel else "admin.setup.audit_removed",
                await interaction_locale(interaction),
            ),
            ephemeral=True,
        )

    @setup.command(
        name="set-moderators", description="Configure queue/story moderation authority."
    )
    @app_commands.choices(
        policy=[
            app_commands.Choice(name="Disabled", value="disabled"),
            app_commands.Choice(
                name="Manage Messages permission", value="manage_messages"
            ),
            app_commands.Choice(name="One role", value="role"),
        ]
    )
    async def set_moderators(
        self,
        interaction: discord.Interaction,
        policy: app_commands.Choice[str],
        role: discord.Role | None = None,
    ):
        if policy.value == "role" and role is None:
            await interaction.response.send_message(
                text(
                    "admin.setup.moderator_role_required",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        assert interaction.guild is not None
        old = await _settings(self.bot.db, interaction.guild.id)
        await self.bot.db.execute(
            "UPDATE guild_settings SET moderator_policy=$2, moderator_role_id=$3, updated_at=NOW() WHERE guild_id=$1",
            interaction.guild.id,
            policy.value,
            role.id if policy.value == "role" else None,
        )
        await self._audit(
            interaction,
            "moderator_policy_changed",
            "guild_settings",
            {"policy": old["moderator_policy"], "role": old["moderator_role_id"]},
            {"policy": policy.value, "role": role.id if role else None},
        )
        await interaction.response.send_message(
            text("admin.setup.moderators_set", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @game.command(
        name="status", description="게임 활성화 상태와 구성된 보상을 확인합니다."
    )
    async def game_status(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        settings = await _settings(self.bot.db, interaction.guild.id)
        rewards = await self.bot.db.fetch(
            "SELECT '공지' AS feature,display_name,reward_policy FROM notice_queues "
            "WHERE guild_id=$1 AND reward_policy->>'type' <> 'none' "
            "UNION ALL SELECT '릴레이',display_name,reward_policy FROM relay_stories "
            "WHERE guild_id=$1 AND reward_policy->>'type' <> 'none' ORDER BY feature,display_name",
            interaction.guild.id,
        )
        locale = await interaction_locale(interaction)
        details = "\n".join(
            text(
                "admin.game.reward_line",
                locale,
                feature=text(
                    (
                        "game.feature.queue"
                        if row["feature"] == "공지"
                        else "game.feature.relay"
                    ),
                    locale,
                ),
                name=row["display_name"],
                policy=reward_policy_label(row["reward_policy"], locale),
            )
            for row in rewards
        )
        await interaction.response.send_message(
            text(
                "admin.game.status",
                locale,
                name=settings["game_name"],
                status=text(
                    (
                        "admin.game.enabled"
                        if settings["game_enabled"]
                        else "admin.game.disabled"
                    ),
                    locale,
                ),
                rewards="" if rewards else text("admin.game.no_rewards", locale),
                details=details,
            ),
            ephemeral=True,
        )

    @game.command(name="enable", description="이 서버의 게임을 활성화합니다.")
    async def game_enable(self, interaction: discord.Interaction):
        assert interaction.guild is not None

        async def apply():
            await self.bot.db.execute(
                "UPDATE guild_settings SET game_enabled=TRUE,updated_at=NOW() WHERE guild_id=$1",
                interaction.guild.id,
            )
            await self._audit(
                interaction,
                "game_enabled",
                "guild_settings",
                {},
                {"game_enabled": True},
            )
            return text("admin.game.enable.done", await interaction_locale(interaction))

        await self._confirm(
            interaction,
            text("admin.game.enable.prompt", await interaction_locale(interaction)),
            apply,
        )

    @game.command(name="disable", description="이 서버의 게임을 비활성화합니다.")
    async def game_disable(self, interaction: discord.Interaction):
        assert interaction.guild is not None

        async def apply():
            await self.bot.db.execute(
                "UPDATE guild_settings SET game_enabled=FALSE,updated_at=NOW() WHERE guild_id=$1",
                interaction.guild.id,
            )
            await self._audit(
                interaction,
                "game_disabled",
                "guild_settings",
                {},
                {"game_enabled": False},
            )
            return text(
                "admin.game.disable.done", await interaction_locale(interaction)
            )

        await self._confirm(
            interaction,
            text("admin.game.disable.prompt", await interaction_locale(interaction)),
            apply,
        )

    @game.command(
        name="set-name", description="서버에서 표시할 게임 이름을 설정합니다."
    )
    async def game_set_name(self, interaction: discord.Interaction, name: str):
        assert interaction.guild is not None
        if not name.strip() or len(name) > 100:
            await interaction.response.send_message(
                text("admin.game.name.invalid", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        old = await _settings(self.bot.db, interaction.guild.id)
        await self.bot.db.execute(
            "UPDATE guild_settings SET game_name=$2,updated_at=NOW() WHERE guild_id=$1",
            interaction.guild.id,
            name.strip(),
        )
        await self._audit(
            interaction,
            "game_name_changed",
            "guild_settings",
            {"game_name": old["game_name"]},
            {"game_name": name.strip()},
        )
        await interaction.response.send_message(
            text("admin.game.name.done", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @game.command(
        name="reward-policy",
        description="공지 대기열 또는 릴레이 이야기의 보상 정책을 설정합니다.",
    )
    @app_commands.choices(
        target_type=[
            app_commands.Choice(name="공지 대기열", value="notice_queue"),
            app_commands.Choice(name="릴레이 이야기", value="relay_story"),
        ],
        policy_type=[
            app_commands.Choice(name="보상 없음", value="none"),
            app_commands.Choice(name="정수 배율", value="essence_multiplier"),
            app_commands.Choice(name="모든 비밀 배율", value="each_secret_multiplier"),
            app_commands.Choice(name="생산 배율", value="production_multiplier"),
            app_commands.Choice(name="고정 정수", value="fixed_essence"),
        ],
    )
    async def game_reward_policy(
        self,
        interaction: discord.Interaction,
        target_type: app_commands.Choice[str],
        target_id: int,
        policy_type: app_commands.Choice[str],
        amount: str | None = None,
        first_grant_amount: str | None = None,
        weekly_cap: int | None = None,
    ):
        assert interaction.guild is not None
        try:
            policy = reward_policy(
                policy_type.value, amount, first_grant_amount, weekly_cap
            )
            table = (
                "notice_queues"
                if target_type.value == "notice_queue"
                else "relay_stories"
            )
            row = await self.bot.db.fetchrow(
                f"SELECT id,reward_policy FROM {table} WHERE guild_id=$1 AND id=$2",
                interaction.guild.id,
                target_id,
            )
            if row is None:
                raise ValueError("admin.reward_policy.target_missing")
        except ValueError as exc:
            await interaction.response.send_message(
                text(str(exc), await interaction_locale(interaction)), ephemeral=True
            )
            return

        async def apply():
            await self.bot.db.execute(
                f"UPDATE {table} SET reward_policy=$2,config_version=config_version+1,updated_at=NOW() WHERE id=$1",
                target_id,
                policy,
            )
            await self._audit(
                interaction,
                "game_reward_policy_changed",
                target_type.value,
                {"policy": row["reward_policy"]},
                {"target_id": target_id, "policy": policy},
            )
            return text("admin.game.policy.done", await interaction_locale(interaction))

        await self._confirm(
            interaction,
            text("admin.game.policy.prompt", await interaction_locale(interaction)),
            apply,
        )

    @game.command(
        name="reward-rollback",
        description="지급 직전 스냅샷으로 플레이어 상태를 전체 되돌립니다.",
    )
    async def game_reward_rollback(
        self, interaction: discord.Interaction, ledger_id: int
    ):
        assert interaction.guild is not None
        ledger = await self.bot.db.fetchrow(
            "SELECT recipient_id,policy,state FROM game_reward_ledger WHERE id=$1 AND guild_id=$2",
            ledger_id,
            interaction.guild.id,
        )
        if ledger is None:
            await interaction.response.send_message(
                text("admin.reward.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return

        async def apply():
            from .secretae_incremental.db import rollback_configured_reward

            result = await rollback_configured_reward(
                self.bot.db, interaction.guild.id, ledger_id
            )
            await self.bot.db.execute(
                "UPDATE reward_reconciliation_cases SET state='resolved',resolution_note='configured reward rolled back',resolved_at=NOW() WHERE guild_id=$1 AND reward_ledger_id=$2 AND state='pending'",
                interaction.guild.id,
                ledger_id,
            )
            await self._audit(
                interaction,
                "game_reward_rolled_back",
                "game_reward_ledger",
                {"state": ledger["state"]},
                {"ledger_id": ledger_id, "recipient_id": result["recipient_id"]},
            )
            return text(
                "admin.reward.rollback.done",
                await interaction_locale(interaction),
                recipient=result["recipient_id"],
            )

        await self._confirm(
            interaction,
            text("admin.reward.rollback.prompt", await interaction_locale(interaction)),
            apply,
        )

    @game.command(
        name="reward-review", description="지급·스냅샷·조정 상태를 검토합니다."
    )
    async def game_reward_review(
        self, interaction: discord.Interaction, ledger_id: int
    ):
        """Give administrators the evidence needed before retrying or rolling back."""
        assert interaction.guild is not None
        row = await self.bot.db.fetchrow(
            "SELECT ledger.id,ledger.source_type,ledger.source_id,ledger.contribution_message_id,"
            "ledger.recipient_id,ledger.policy,ledger.state,ledger.details,ledger.created_at,"
            "snapshot.expires_at,snapshot.pruned_at "
            "FROM game_reward_ledger ledger LEFT JOIN game_state_snapshots snapshot "
            "ON snapshot.reward_ledger_id=ledger.id "
            "WHERE ledger.id=$1 AND ledger.guild_id=$2",
            ledger_id,
            interaction.guild.id,
        )
        if row is None:
            await interaction.response.send_message(
                text("admin.reward.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        locale = await interaction_locale(interaction)
        snapshot = (
            text("admin.reward.review.snapshot_none", locale)
            if row["expires_at"] is None
            else (
                text("admin.reward.review.snapshot_pruned", locale)
                if row["pruned_at"]
                else text(
                    "admin.reward.review.snapshot_retained",
                    locale,
                    expires=row["expires_at"].isoformat(),
                )
            )
        )
        source = text(
            (
                "admin.reward.source.queue"
                if row["source_type"] == "notice_queue"
                else "admin.reward.source.relay"
            ),
            locale,
        )
        await interaction.response.send_message(
            text(
                "admin.reward.review",
                locale,
                id=row["id"],
                state=row["state"],
                recipient=row["recipient_id"],
                source=source,
                source_id=row["source_id"],
                message_id=row["contribution_message_id"],
                policy=reward_policy_label(row["policy"], locale),
                snapshot=snapshot,
            ),
            ephemeral=True,
        )

    @game.command(
        name="reward-reconciliations",
        description="삭제·부적격 기여의 보상 검토 대기 목록을 봅니다.",
    )
    async def game_reward_reconciliations(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        rows = await self.bot.db.fetch(
            "SELECT id,source_type,contribution_message_id,reward_kind,reward_ledger_id,reason,created_at "
            "FROM reward_reconciliation_cases WHERE guild_id=$1 AND state='pending' ORDER BY created_at DESC LIMIT 25",
            interaction.guild.id,
        )
        if not rows:
            await interaction.response.send_message(
                text(
                    "admin.reward.reconciliations.none",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        locale = await interaction_locale(interaction)
        lines = [text("admin.reward.reconciliations.intro", locale)]
        for row in rows:
            reference = (
                text(
                    "admin.reward.reconciliations.ledger",
                    locale,
                    ledger=row["reward_ledger_id"],
                )
                if row["reward_ledger_id"]
                else text("admin.reward.reconciliations.legacy", locale)
            )
            source = text(
                (
                    "admin.reward.source.queue"
                    if row["source_type"] == "notice_queue"
                    else "admin.reward.source.relay"
                ),
                locale,
            )
            lines.append(
                text(
                    "admin.reward.reconciliations.line",
                    locale,
                    id=row["id"],
                    source=source,
                    message_id=row["contribution_message_id"],
                    reference=reference,
                    reason=row["reason"],
                )
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @game.command(
        name="resolve-reconciliation",
        description="외부/수동 보상 조정을 감사 기록으로 완료 처리합니다.",
    )
    async def game_resolve_reconciliation(
        self, interaction: discord.Interaction, case_id: int, note: str
    ):
        assert interaction.guild is not None
        if not note.strip() or len(note) > 500:
            await interaction.response.send_message(
                text(
                    "admin.reward.reconciliation.note_invalid",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        case = await self.bot.db.fetchrow(
            "SELECT * FROM reward_reconciliation_cases WHERE id=$1 AND guild_id=$2 AND state='pending'",
            case_id,
            interaction.guild.id,
        )
        if case is None:
            await interaction.response.send_message(
                text(
                    "admin.reward.reconciliation.not_found",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return

        async def apply():
            await self.bot.db.execute(
                "UPDATE reward_reconciliation_cases SET state='resolved',resolution_note=$2,resolved_at=NOW() WHERE id=$1",
                case_id,
                note.strip(),
            )
            await self._audit(
                interaction,
                "reward_reconciliation_resolved",
                "reward_reconciliation_case",
                {},
                {
                    "case_id": case_id,
                    "note": note.strip(),
                    "ledger_id": case["reward_ledger_id"],
                },
            )
            return text(
                "admin.reward.reconciliation.done",
                await interaction_locale(interaction),
            )

        await self._confirm(
            interaction,
            text(
                "admin.reward.reconciliation.prompt",
                await interaction_locale(interaction),
            ),
            apply,
        )

    async def _queue(self, guild_id: int, queue_id: int):
        row = await self.bot.db.fetchrow(
            "SELECT * FROM notice_queues WHERE guild_id=$1 AND id=$2",
            guild_id,
            queue_id,
        )
        if row is None:
            raise ValueError("queue_not_found")
        return row

    @queue.command(
        name="create", description="Create an active notice queue with safe defaults."
    )
    @app_commands.choices(
        cadence=[
            app_commands.Choice(name="Daily", value="daily"),
            app_commands.Choice(name="Weekly", value="weekly"),
            app_commands.Choice(name="Manual only", value="manual"),
        ]
    )
    async def queue_create(
        self,
        interaction: discord.Interaction,
        name: str,
        submission_channel: discord.TextChannel,
        publication_channel: discord.TextChannel,
        cadence: app_commands.Choice[str] = None,
        schedule_time: str = "09:00",
        timezone_name: str = "UTC",
        weekday: int | None = None,
        description: str | None = None,
    ):
        assert interaction.guild is not None
        if (
            submission_channel.guild.id != interaction.guild.id
            or publication_channel.guild.id != interaction.guild.id
        ):
            await interaction.response.send_message(
                text(
                    "admin.queue.channels_wrong_guild",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        try:
            hour, minute = map(int, schedule_time.split(":"))
            from datetime import time

            scheduled = time(hour, minute)
            service = self.bot.get_cog("NoticeQueueService")
            row = await service.create_queue(
                interaction.guild.id,
                name,
                submission_channel.id,
                publication_channel.id,
                cadence.value if cadence else "daily",
                scheduled,
                timezone_name,
                weekday,
                description,
            )
        except (ValueError, AttributeError):
            await interaction.response.send_message(
                text(
                    "admin.queue.invalid_config", await interaction_locale(interaction)
                ),
                ephemeral=True,
            )
            return
        # A working first queue is the only required setup outcome. Audit and
        # optional features remain optional, so do not force server-specific
        # choices before setup can be considered complete.
        await self.bot.db.execute(
            "UPDATE guild_settings SET setup_state='complete',updated_at=NOW() "
            "WHERE guild_id=$1 AND setup_state <> 'complete'",
            interaction.guild.id,
        )
        await self._audit(
            interaction,
            "notice_queue_created",
            "notice_queue",
            {},
            {"queue_id": row["id"], "name": name},
        )
        await interaction.response.send_message(
            text(
                "admin.queue.created",
                await interaction_locale(interaction),
                name=name,
                id=row["id"],
            ),
            ephemeral=True,
        )

    @queue.command(name="list", description="List this server's notice queues.")
    async def queue_list(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        rows = await self.bot.db.fetch(
            "SELECT id,display_name,state,accepting_submissions,cadence FROM notice_queues WHERE guild_id=$1 ORDER BY id",
            interaction.guild.id,
        )
        locale = await interaction_locale(interaction)
        rendered = (
            text("admin.queue.list.none", locale)
            if not rows
            else "\n".join(
                text(
                    "admin.queue.list.line",
                    locale,
                    id=row["id"],
                    name=row["display_name"],
                    state=self._label("state", row["state"], locale),
                    accepting=text(
                        (
                            "admin.queue.accepting"
                            if row["accepting_submissions"]
                            else "admin.queue.closed"
                        ),
                        locale,
                    ),
                    cadence=self._label("cadence", row["cadence"], locale),
                )
                for row in rows
            )
        )
        await interaction.response.send_message(rendered, ephemeral=True)

    @queue.command(
        name="view", description="View notice queue configuration and health."
    )
    async def queue_view(self, interaction: discord.Interaction, queue_id: int):
        assert interaction.guild is not None
        try:
            row = await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        eligible = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM notice_submissions WHERE queue_id=$1 AND status='eligible'",
            queue_id,
        )
        last = await self.bot.db.fetchrow(
            "SELECT published_at,status FROM notice_submissions WHERE queue_id=$1 AND status IN ('published','failed') ORDER BY published_at DESC NULLS LAST LIMIT 1",
            queue_id,
        )
        locale = await interaction_locale(interaction)
        period = (
            text(
                "admin.queue.view.period_hours",
                locale,
                hours=row["submission_period_hours"],
            )
            if row["submission_period_hours"]
            else text("admin.queue.view.period_open", locale)
        )
        await interaction.response.send_message(
            text(
                "admin.queue.view",
                locale,
                name=row["display_name"],
                id=queue_id,
                state=self._label("state", row["state"], locale),
                accepting=text(
                    (
                        "admin.queue.accepting"
                        if row["accepting_submissions"]
                        else "admin.queue.closed"
                    ),
                    locale,
                ),
                eligible=eligible,
                cadence=self._label("cadence", row["cadence"], locale),
                time=row["schedule_time"],
                timezone=row["timezone"],
                period=period,
                submission_channel=row["submission_channel_id"],
                publication_channel=row["publication_channel_id"],
                selection=self._label("selection", row["selection_policy"], locale),
                reward=reward_policy_label(row["reward_policy"], locale),
                title=row["title_template"],
                content=row["content_template"],
                last=self._label("state", last["status"] if last else None, locale),
            ),
            ephemeral=True,
        )

    @queue.command(
        name="edit", description="Edit queue name, description, or accepting state."
    )
    async def queue_edit(
        self,
        interaction: discord.Interaction,
        queue_id: int,
        name: str | None = None,
        description: str | None = None,
        accepting: bool | None = None,
    ):
        assert interaction.guild is not None
        try:
            old = await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE notice_queues SET display_name=COALESCE($2,display_name), description=COALESCE($3,description), accepting_submissions=COALESCE($4,accepting_submissions), config_version=config_version+1,updated_at=NOW() WHERE id=$1",
            queue_id,
            name,
            description,
            accepting,
        )
        await self._audit(
            interaction,
            "notice_queue_edited",
            "notice_queue",
            {"name": old["display_name"]},
            {"queue_id": queue_id, "name": name or old["display_name"]},
        )
        await interaction.response.send_message(
            text("admin.queue.edited", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @queue.command(
        name="configure",
        description="Configure selection, schedule, limits, attribution, and template.",
    )
    @app_commands.choices(
        selection=[
            app_commands.Choice(name="Oldest eligible", value="oldest"),
            app_commands.Choice(name="Fair rotation", value="fair"),
            app_commands.Choice(name="Random", value="random"),
        ],
        attribution=[
            app_commands.Choice(name="Display name", value="display_name"),
            app_commands.Choice(name="No attribution", value="none"),
        ],
    )
    async def queue_configure(
        self,
        interaction: discord.Interaction,
        queue_id: int,
        selection: app_commands.Choice[str] | None = None,
        per_member_limit: int | None = None,
        schedule_time: str | None = None,
        timezone_name: str | None = None,
        weekday: int | None = None,
        attribution: app_commands.Choice[str] | None = None,
        title_template: str | None = None,
        content_template: str | None = None,
    ):
        assert interaction.guild is not None
        try:
            old = await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        if per_member_limit is not None and per_member_limit < 1:
            await interaction.response.send_message(
                text(
                    "admin.queue.limit_invalid", await interaction_locale(interaction)
                ),
                ephemeral=True,
            )
            return
        try:
            if schedule_time:
                hour, minute = map(int, schedule_time.split(":"))
                from datetime import time

                parsed_time = time(hour, minute)
            else:
                parsed_time = old["schedule_time"]
            if timezone_name:
                from .notice_queues import valid_timezone

                if not valid_timezone(timezone_name):
                    raise ValueError
            if (
                old["cadence"] == "weekly"
                and weekday is not None
                and not 0 <= weekday <= 6
            ):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                text(
                    "admin.queue.schedule_invalid",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        if title_template is not None and (
            not title_template.strip() or len(title_template) > 100
        ):
            await interaction.response.send_message(
                text(
                    "admin.queue.title_invalid", await interaction_locale(interaction)
                ),
                ephemeral=True,
            )
            return
        if content_template is not None and (
            not content_template.strip() or len(content_template) > 1_900
        ):
            await interaction.response.send_message(
                text(
                    "admin.queue.content_invalid", await interaction_locale(interaction)
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE notice_queues SET selection_policy=COALESCE($2,selection_policy),per_member_limit=COALESCE($3,per_member_limit),schedule_time=$4,timezone=COALESCE($5,timezone),schedule_weekday=COALESCE($6,schedule_weekday),attribution_policy=COALESCE($7,attribution_policy),title_template=COALESCE($8,title_template),content_template=COALESCE($9,content_template),config_version=config_version+1,updated_at=NOW() WHERE id=$1",
            queue_id,
            selection.value if selection else None,
            per_member_limit,
            parsed_time,
            timezone_name,
            weekday,
            attribution.value if attribution else None,
            title_template,
            content_template,
        )
        await self._audit(
            interaction,
            "notice_queue_configured",
            "notice_queue",
            {"version": old["config_version"]},
            {"queue_id": queue_id, "version": old["config_version"] + 1},
        )
        await interaction.response.send_message(
            text("admin.queue.configured", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @queue.command(
        name="set-channels",
        description="Redirect submission/publication channels after confirmation.",
    )
    async def queue_set_channels(
        self,
        interaction: discord.Interaction,
        queue_id: int,
        submission_channel: discord.TextChannel,
        publication_channel: discord.TextChannel,
    ):
        assert interaction.guild is not None
        try:
            old = await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        if submission_channel.id == publication_channel.id:
            await interaction.response.send_message(
                text("admin.queue.same_channel", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        if (
            submission_channel.guild.id != interaction.guild.id
            or publication_channel.guild.id != interaction.guild.id
        ):
            await interaction.response.send_message(
                text(
                    "admin.queue.channels_wrong_guild",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return

        async def apply():
            service = self.bot.get_cog("NoticeQueueService")
            await service._validate_channel(
                interaction.guild.id, submission_channel.id, "submission"
            )
            await service._validate_channel(
                interaction.guild.id, publication_channel.id, "publication"
            )
            await self.bot.db.execute(
                "UPDATE notice_queues SET submission_channel_id=$2,publication_channel_id=$3,active_submission_thread_id=NULL,accepting_submissions=FALSE,config_version=config_version+1,updated_at=NOW() WHERE id=$1",
                queue_id,
                submission_channel.id,
                publication_channel.id,
            )
            await self._audit(
                interaction,
                "notice_queue_channels_changed",
                "notice_queue",
                {
                    "submission": old["submission_channel_id"],
                    "publication": old["publication_channel_id"],
                },
                {
                    "queue_id": queue_id,
                    "submission": submission_channel.id,
                    "publication": publication_channel.id,
                },
            )
            return text(
                "admin.queue.channels_changed", await interaction_locale(interaction)
            )

        await self._confirm(
            interaction,
            text("admin.queue.channels_prompt", await interaction_locale(interaction)),
            apply,
        )

    @queue.command(
        name="configure-submissions",
        description="신청 내용, 수정, 첨부파일, 링크 정책을 설정합니다.",
    )
    @app_commands.choices(
        no_entry_action=[
            app_commands.Choice(
                name="운영진 알림 및 새 스레드", value="alert_and_reopen"
            ),
            app_commands.Choice(name="운영진 알림만", value="alert_only"),
            app_commands.Choice(name="건너뛰기", value="skip"),
        ]
    )
    async def queue_configure_submissions(
        self,
        interaction: discord.Interaction,
        queue_id: int,
        minimum_characters: int | None = None,
        maximum_characters: int | None = None,
        submission_period_hours: int | None = None,
        allow_attachments: bool | None = None,
        allow_links: bool | None = None,
        allow_edits: bool | None = None,
        no_entry_action: app_commands.Choice[str] | None = None,
        clear_content_limits: bool = False,
        clear_submission_period: bool = False,
    ):
        assert interaction.guild is not None
        try:
            queue = await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        minimum = (
            None
            if clear_content_limits
            else (
                minimum_characters
                if minimum_characters is not None
                else queue["min_content_length"]
            )
        )
        maximum = (
            None
            if clear_content_limits
            else (
                maximum_characters
                if maximum_characters is not None
                else queue["max_content_length"]
            )
        )
        period = (
            None
            if clear_submission_period
            else (
                submission_period_hours
                if submission_period_hours is not None
                else queue["submission_period_hours"]
            )
        )
        if (
            minimum is not None
            and minimum < 0
            or maximum is not None
            and maximum < 0
            or minimum is not None
            and maximum is not None
            and minimum > maximum
            or period is not None
            and period < 1
        ):
            await interaction.response.send_message(
                text(
                    "admin.queue.submission_policy_invalid",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        locale = await interaction_locale(interaction)

        async def apply():
            await self.bot.db.execute(
                "UPDATE notice_queues SET min_content_length=$2,max_content_length=$3,submission_period_hours=$4,allow_attachments=COALESCE($5,allow_attachments),allow_links=COALESCE($6,allow_links),allow_edits=COALESCE($7,allow_edits),no_entry_action=COALESCE($8,no_entry_action),config_version=config_version+1,updated_at=NOW() WHERE id=$1",
                queue_id,
                minimum,
                maximum,
                period,
                allow_attachments,
                allow_links,
                allow_edits,
                no_entry_action.value if no_entry_action else None,
            )
            await self._audit(
                interaction,
                "notice_submission_policy_changed",
                "notice_queue",
                {"queue_id": queue_id},
                {
                    "minimum": minimum,
                    "maximum": maximum,
                    "period_hours": period,
                    "attachments": allow_attachments,
                    "links": allow_links,
                    "edits": allow_edits,
                },
            )
            return text("admin.queue.submission_policy_done", locale)

        if maximum_characters is not None and maximum_characters > 2_000:
            await self._confirm(
                interaction,
                text(
                    "admin.queue.submission_policy_long_prompt", locale, maximum=maximum
                ),
                apply,
            )
            return
        await apply()
        await interaction.response.send_message(
            text("admin.queue.submission_policy_done", locale), ephemeral=True
        )

    @queue.command(name="pause", description="Pause a notice queue.")
    async def queue_pause(self, interaction: discord.Interaction, queue_id: int):
        assert interaction.guild is not None
        try:
            await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE notice_queues SET state='paused',accepting_submissions=FALSE,updated_at=NOW() WHERE id=$1",
            queue_id,
        )
        await self._audit(
            interaction,
            "notice_queue_paused",
            "notice_queue",
            {},
            {"queue_id": queue_id},
        )
        await interaction.response.send_message(
            text("admin.queue.paused", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @queue.command(name="resume", description="Resume a paused notice queue.")
    async def queue_resume(self, interaction: discord.Interaction, queue_id: int):
        assert interaction.guild is not None
        try:
            await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE notice_queues SET state='active',updated_at=NOW() WHERE id=$1",
            queue_id,
        )
        await self._audit(
            interaction,
            "notice_queue_resumed",
            "notice_queue",
            {},
            {"queue_id": queue_id},
        )
        await interaction.response.send_message(
            text("admin.queue.resumed", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @queue.command(
        name="archive",
        description="Archive a queue after confirmation; submissions are retained.",
    )
    async def queue_archive(self, interaction: discord.Interaction, queue_id: int):
        assert interaction.guild is not None
        try:
            await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return

        async def apply():
            await self.bot.db.execute(
                "UPDATE notice_queues SET state='archived',accepting_submissions=FALSE,updated_at=NOW() WHERE id=$1",
                queue_id,
            )
            await self._audit(
                interaction,
                "notice_queue_archived",
                "notice_queue",
                {},
                {"queue_id": queue_id},
            )
            return text("admin.queue.archived", await interaction_locale(interaction))

        await self._confirm(
            interaction,
            text("admin.queue.archive_prompt", await interaction_locale(interaction)),
            apply,
        )

    @queue.command(name="open-submissions", description="Open a new submission thread.")
    async def queue_open_submissions(
        self, interaction: discord.Interaction, queue_id: int
    ):
        assert interaction.guild is not None
        try:
            await self._queue(interaction.guild.id, queue_id)
            thread = await self.bot.get_cog("NoticeQueueService").open_submissions(
                queue_id
            )
        except (ValueError, discord.HTTPException) as exc:
            await interaction.response.send_message(
                text(
                    "admin.queue.open_failed",
                    await interaction_locale(interaction),
                    error=exc,
                ),
                ephemeral=True,
            )
            return
        await self._audit(
            interaction,
            "notice_submission_thread_opened",
            "notice_queue",
            {},
            {"queue_id": queue_id, "thread_id": thread.id},
        )
        await interaction.response.send_message(
            text(
                "admin.queue.opened",
                await interaction_locale(interaction),
                thread=thread.mention,
            ),
            ephemeral=True,
        )

    @queue.command(
        name="publish-now",
        description="Publish the oldest eligible entry now (confirmation required).",
    )
    async def queue_publish_now(self, interaction: discord.Interaction, queue_id: int):
        assert interaction.guild is not None
        try:
            await self._queue(interaction.guild.id, queue_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.queue.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return

        async def apply():
            destination = await self.bot.get_cog("NoticeQueueService").publish(queue_id)
            await self._audit(
                interaction,
                "notice_queue_published_now",
                "notice_queue",
                {},
                {"queue_id": queue_id, "destination": destination},
            )
            return text(
                "admin.queue.published",
                await interaction_locale(interaction),
                destination=destination,
            )

        await self._confirm(
            interaction,
            text("admin.queue.publish_prompt", await interaction_locale(interaction)),
            apply,
        )

    @queue.command(
        name="retry",
        description="Return a failed/selected submission to eligibility for safe retry.",
    )
    async def queue_retry(self, interaction: discord.Interaction, message_id: str):
        if not message_id.isdecimal() or interaction.guild is None:
            await interaction.response.send_message(
                text("admin.queue.id_invalid", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        row = await self.bot.db.fetchrow(
            "SELECT s.id,q.guild_id FROM notice_submissions s JOIN notice_queues q ON q.id=s.queue_id WHERE s.source_message_id=$1 AND s.status IN ('selected','failed')",
            int(message_id),
        )
        if row is None or row["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(
                text(
                    "admin.queue.retry_missing", await interaction_locale(interaction)
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE notice_submissions SET status='eligible', moderation_reason=NULL WHERE id=$1",
            row["id"],
        )
        await self._audit(
            interaction,
            "notice_submission_retried",
            "notice_submission",
            {},
            {"message_id": message_id},
        )
        await interaction.response.send_message(
            text("admin.queue.retried", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @queue.command(
        name="moderate",
        description="Reject or remove an eligible submission; moderators may use this.",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Reject", value="rejected"),
            app_commands.Choice(name="Disqualify", value="invalid"),
            app_commands.Choice(name="Skip", value="invalid"),
        ]
    )
    async def queue_moderate(
        self,
        interaction: discord.Interaction,
        message_id: str,
        action: app_commands.Choice[str],
        reason: str | None = None,
    ):
        if not message_id.isdecimal():
            await interaction.response.send_message(
                text("admin.queue.id_invalid", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        row = await self.bot.db.fetchrow(
            "SELECT s.id,q.guild_id FROM notice_submissions s JOIN notice_queues q ON q.id=s.queue_id WHERE s.source_message_id=$1 AND s.status='eligible'",
            int(message_id),
        )
        if (
            row is None
            or interaction.guild is None
            or row["guild_id"] != interaction.guild.id
        ):
            await interaction.response.send_message(
                text(
                    "admin.queue.moderation_missing",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE notice_submissions SET status=$2,moderated_at=NOW(),moderator_id=$3,moderation_reason=$4 WHERE id=$1",
            row["id"],
            action.value,
            interaction.user.id,
            reason,
        )
        await self._audit(
            interaction,
            "notice_submission_moderated",
            "notice_submission",
            {},
            {"message_id": message_id, "action": action.value},
        )
        await interaction.response.send_message(
            text("admin.queue.moderated", await interaction_locale(interaction)),
            ephemeral=True,
        )

    async def _story(self, guild_id: int, story_id: int):
        story = await self.bot.db.fetchrow(
            "SELECT * FROM relay_stories WHERE guild_id=$1 AND id=$2",
            guild_id,
            story_id,
        )
        if story is None:
            raise ValueError("relay_not_found")
        return story

    @relay.command(name="create", description="새 릴레이 이야기를 만듭니다.")
    async def relay_create(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel | discord.Thread,
        rules: str | None = None,
    ):
        assert interaction.guild is not None
        if channel.guild.id != interaction.guild.id:
            await interaction.response.send_message(
                text(
                    "admin.relay.channel_wrong_guild",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        try:
            service = self.bot.get_cog("RelayStoryService")
            await service.validate_story_channel(interaction.guild.id, channel.id)
            story = await service.create_story(
                interaction.guild.id, name, channel.id, rules
            )
        except (ValueError, discord.HTTPException):
            await interaction.response.send_message(
                text("admin.relay.duplicate", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        await self._audit(
            interaction,
            "relay_story_created",
            "relay_story",
            {},
            {"story_id": story["id"], "name": name, "channel_id": channel.id},
        )
        await interaction.response.send_message(
            text(
                "admin.relay.created",
                await interaction_locale(interaction),
                name=name,
                id=story["id"],
            ),
            ephemeral=True,
        )

    @relay.command(name="list", description="이 서버의 릴레이 이야기를 표시합니다.")
    async def relay_list(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        rows = await self.bot.db.fetch(
            "SELECT id,display_name,channel_id,state FROM relay_stories WHERE guild_id=$1 ORDER BY id",
            interaction.guild.id,
        )
        locale = await interaction_locale(interaction)
        rendered = (
            text("admin.relay.list.none", locale)
            if not rows
            else "\n".join(
                text(
                    "admin.relay.list.line",
                    locale,
                    id=row["id"],
                    name=row["display_name"],
                    channel=row["channel_id"],
                    state=self._label("state", row["state"], locale),
                )
                for row in rows
            )
        )
        await interaction.response.send_message(rendered, ephemeral=True)

    @relay.command(name="view", description="릴레이 이야기 설정과 상태를 확인합니다.")
    async def relay_view(self, interaction: discord.Interaction, story_id: int):
        assert interaction.guild is not None
        try:
            story = await self._story(interaction.guild.id, story_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.relay.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        eligible = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM relay_contributions WHERE story_id=$1 AND status='eligible'",
            story_id,
        )
        last = await self.bot.db.fetchrow(
            "SELECT author_id,contributed_at FROM relay_contributions WHERE story_id=$1 AND status='eligible' ORDER BY contributed_at DESC,id DESC LIMIT 1",
            story_id,
        )
        locale = await interaction_locale(interaction)
        await interaction.response.send_message(
            text(
                "admin.relay.view",
                locale,
                name=story["display_name"],
                id=story_id,
                state=self._label("state", story["state"], locale),
                channel=story["channel_id"],
                eligible=eligible,
                last=(
                    f"<@{last['author_id']}>"
                    if last
                    else text("admin.relay.last_none", locale)
                ),
                rules=story["rules_text"] or text("admin.relay.default_rules", locale),
                reward=reward_policy_label(story["reward_policy"], locale),
                moderators=self._label(
                    "moderator", story["moderator_policy"] or "inherit", locale
                ),
            ),
            ephemeral=True,
        )

    @relay.command(
        name="edit", description="릴레이 이야기 이름, 채널 또는 규칙을 변경합니다."
    )
    async def relay_edit(
        self,
        interaction: discord.Interaction,
        story_id: int,
        name: str | None = None,
        channel: discord.TextChannel | discord.Thread | None = None,
        rules: str | None = None,
    ):
        assert interaction.guild is not None
        try:
            old = await self._story(interaction.guild.id, story_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.relay.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        if channel and channel.guild.id != interaction.guild.id:
            await interaction.response.send_message(
                text(
                    "admin.relay.channel_wrong_guild",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        if channel and channel.id != old["channel_id"]:

            async def apply():
                await self.bot.get_cog("RelayStoryService").validate_story_channel(
                    interaction.guild.id, channel.id
                )
                await self.bot.db.execute(
                    "UPDATE relay_stories SET display_name=COALESCE($2,display_name),channel_id=$3,rules_text=COALESCE($4,rules_text),config_version=config_version+1,updated_at=NOW() WHERE id=$1",
                    story_id,
                    name,
                    channel.id,
                    rules,
                )
                await self._audit(
                    interaction,
                    "relay_story_edited",
                    "relay_story",
                    {"channel_id": old["channel_id"]},
                    {"story_id": story_id, "channel_id": channel.id},
                )
                return text(
                    "admin.relay.channel_changed", await interaction_locale(interaction)
                )

            await self._confirm(
                interaction,
                text(
                    "admin.relay.channel_prompt", await interaction_locale(interaction)
                ),
                apply,
            )
            return
        await self.bot.db.execute(
            "UPDATE relay_stories SET display_name=COALESCE($2,display_name),rules_text=COALESCE($3,rules_text),config_version=config_version+1,updated_at=NOW() WHERE id=$1",
            story_id,
            name,
            rules,
        )
        await self._audit(
            interaction, "relay_story_edited", "relay_story", {}, {"story_id": story_id}
        )
        await interaction.response.send_message(
            text("admin.relay.edited", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @relay.command(
        name="pin-rules", description="관리자가 제공한 이야기 규칙을 채널에 고정합니다."
    )
    async def relay_pin_rules(self, interaction: discord.Interaction, story_id: int):
        assert interaction.guild is not None
        try:
            await self._story(interaction.guild.id, story_id)
            message = await self.bot.get_cog("RelayStoryService").pin_rules(story_id)
        except (ValueError, discord.HTTPException) as exc:
            await interaction.response.send_message(
                text(
                    "admin.relay.pin_failed",
                    await interaction_locale(interaction),
                    error=exc,
                ),
                ephemeral=True,
            )
            return
        await self._audit(
            interaction,
            "relay_rules_pinned",
            "relay_story",
            {},
            {"story_id": story_id, "message_id": message.id},
        )
        await interaction.response.send_message(
            text("admin.relay.pinned", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @relay.command(
        name="configure-posting",
        description="기여 순서와 선택적 글자 수 제한을 설정합니다.",
    )
    async def relay_configure_posting(
        self,
        interaction: discord.Interaction,
        story_id: int,
        require_intervening_contributor: bool = True,
        maximum_characters: int | None = None,
    ):
        assert interaction.guild is not None
        try:
            story = await self._story(interaction.guild.id, story_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.relay.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        if maximum_characters is not None and maximum_characters < 1:
            await interaction.response.send_message(
                text(
                    "admin.relay.maximum_invalid", await interaction_locale(interaction)
                ),
                ephemeral=True,
            )
            return
        rules = dict(story["posting_rules"])
        rules["require_intervening_contributor"] = require_intervening_contributor
        if maximum_characters is None:
            rules.pop("max_length", None)
        else:
            rules["max_length"] = maximum_characters
        await self.bot.db.execute(
            "UPDATE relay_stories SET posting_rules=$2,config_version=config_version+1,updated_at=NOW() WHERE id=$1",
            story_id,
            rules,
        )
        await self._audit(
            interaction,
            "relay_posting_rules_changed",
            "relay_story",
            {"posting_rules": story["posting_rules"]},
            {"story_id": story_id, "posting_rules": rules},
        )
        await interaction.response.send_message(
            text(
                "admin.relay.posting_configured", await interaction_locale(interaction)
            ),
            ephemeral=True,
        )

    @relay.command(
        name="set-moderators", description="이 이야기 전용 운영진 정책을 설정합니다."
    )
    @app_commands.choices(
        policy=[
            app_commands.Choice(name="서버 기본 정책", value="inherit"),
            app_commands.Choice(name="메시지 관리 권한", value="manage_messages"),
            app_commands.Choice(name="역할 하나", value="role"),
        ]
    )
    async def relay_set_moderators(
        self,
        interaction: discord.Interaction,
        story_id: int,
        policy: app_commands.Choice[str],
        role: discord.Role | None = None,
    ):
        assert interaction.guild is not None
        try:
            story = await self._story(interaction.guild.id, story_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.relay.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        if policy.value == "role" and role is None:
            await interaction.response.send_message(
                text(
                    "admin.relay.role_required", await interaction_locale(interaction)
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE relay_stories SET moderator_policy=$2,moderator_role_id=$3,config_version=config_version+1,updated_at=NOW() WHERE id=$1",
            story_id,
            None if policy.value == "inherit" else policy.value,
            role.id if policy.value == "role" else None,
        )
        await self._audit(
            interaction,
            "relay_moderator_policy_changed",
            "relay_story",
            {"policy": story["moderator_policy"]},
            {
                "story_id": story_id,
                "policy": policy.value,
                "role": role.id if role else None,
            },
        )
        await interaction.response.send_message(
            text(
                "admin.relay.moderators_configured",
                await interaction_locale(interaction),
            ),
            ephemeral=True,
        )

    @relay.command(name="pause", description="릴레이 이야기를 일시 정지합니다.")
    async def relay_pause(self, interaction: discord.Interaction, story_id: int):
        assert interaction.guild is not None
        try:
            await self._story(interaction.guild.id, story_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.relay.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE relay_stories SET state='paused',updated_at=NOW() WHERE id=$1",
            story_id,
        )
        await self._audit(
            interaction, "relay_story_paused", "relay_story", {}, {"story_id": story_id}
        )
        await interaction.response.send_message(
            text("admin.relay.paused", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @relay.command(name="resume", description="릴레이 이야기를 다시 시작합니다.")
    async def relay_resume(self, interaction: discord.Interaction, story_id: int):
        assert interaction.guild is not None
        try:
            await self._story(interaction.guild.id, story_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.relay.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE relay_stories SET state='active',updated_at=NOW() WHERE id=$1",
            story_id,
        )
        await self._audit(
            interaction,
            "relay_story_resumed",
            "relay_story",
            {},
            {"story_id": story_id},
        )
        await interaction.response.send_message(
            text("admin.relay.resumed", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @relay.command(
        name="remove",
        description="릴레이 이야기를 보관 처리합니다. 기여 기록은 삭제하지 않습니다.",
    )
    async def relay_remove(self, interaction: discord.Interaction, story_id: int):
        assert interaction.guild is not None
        try:
            await self._story(interaction.guild.id, story_id)
        except ValueError:
            await interaction.response.send_message(
                text("admin.relay.not_found", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return

        async def apply():
            await self.bot.db.execute(
                "UPDATE relay_stories SET state='archived',updated_at=NOW() WHERE id=$1",
                story_id,
            )
            await self._audit(
                interaction,
                "relay_story_archived",
                "relay_story",
                {},
                {"story_id": story_id},
            )
            return text("admin.relay.archived", await interaction_locale(interaction))

        await self._confirm(
            interaction,
            text("admin.relay.archive_prompt", await interaction_locale(interaction)),
            apply,
        )

    @relay.command(
        name="moderate", description="릴레이 기여를 삭제하고 부적격 처리합니다."
    )
    async def relay_moderate(
        self,
        interaction: discord.Interaction,
        message_id: str,
        reason: str | None = None,
    ):
        if not message_id.isdecimal() or interaction.guild is None:
            await interaction.response.send_message(
                text("admin.relay.id_invalid", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        contribution = await self.bot.db.fetchrow(
            "SELECT c.id,c.story_id,s.guild_id,s.channel_id FROM relay_contributions c JOIN relay_stories s ON s.id=c.story_id WHERE c.message_id=$1 AND c.status='eligible'",
            int(message_id),
        )
        if contribution is None or contribution["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(
                text(
                    "admin.relay.moderation_missing",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        try:
            channel = self.bot.get_channel(
                contribution["channel_id"]
            ) or await self.bot.fetch_channel(contribution["channel_id"])
            message = await channel.fetch_message(int(message_id))
            await message.delete()
        except (discord.HTTPException, discord.NotFound):
            pass
        await self.bot.db.execute(
            "UPDATE relay_contributions SET status='disqualified',moderated_at=NOW(),moderator_id=$2,moderation_reason=$3 WHERE id=$1",
            contribution["id"],
            interaction.user.id,
            reason,
        )
        await self._register_reconciliation(
            interaction.guild.id, int(message_id), reason or "slash moderation"
        )
        await self._audit(
            interaction,
            "relay_contribution_moderated",
            "relay_contribution",
            {},
            {"message_id": message_id, "reason": reason},
        )
        await interaction.response.send_message(
            text("admin.relay.moderated", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @diagnostics.command(
        name="reward",
        description="Look up an imported notice reward by submission message ID.",
    )
    async def reward_diagnostic(
        self, interaction: discord.Interaction, message_id: str
    ):
        if not message_id.isdecimal():
            await interaction.response.send_message(
                text(
                    "admin.diagnostics.reward.invalid_id",
                    await interaction_locale(interaction),
                ),
                ephemeral=True,
            )
            return
        reward = await get_submission_reward(self.bot.db, int(message_id))
        locale = await interaction_locale(interaction)
        await interaction.response.send_message(
            (
                text("admin.diagnostics.reward.none", locale)
                if reward is None
                else text(
                    "admin.diagnostics.reward.found",
                    locale,
                    reward_type=reward["reward_type"],
                    recipient=reward["discord_id"],
                    thread=reward["posted_thread_id"],
                    recorded=reward["posted_at"],
                )
            ),
            ephemeral=True,
        )

    @diagnostics.command(
        name="migration",
        description="가져온 공지 대기열과 릴레이 이야기 상태를 확인합니다.",
    )
    async def migration_diagnostic(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        queues = await self.bot.db.fetch(
            "SELECT display_name,state,provenance FROM notice_queues WHERE guild_id=$1 ORDER BY id",
            interaction.guild.id,
        )
        stories = await self.bot.db.fetch(
            "SELECT display_name,state,provenance FROM relay_stories WHERE guild_id=$1 ORDER BY id",
            interaction.guild.id,
        )
        imported_queues = [row for row in queues if row["provenance"]]
        imported_stories = [row for row in stories if row["provenance"]]
        mismatches = await self.bot.get_cog("NoticeQueueService").legacy_mismatches(
            interaction.guild.id
        )
        mismatches.extend(
            await self.bot.get_cog("RelayStoryService").legacy_mismatches(
                interaction.guild.id
            )
        )
        text = (
            f"가져온 공지 대기열: {len(imported_queues)}개 / 전체 {len(queues)}개\n가져온 릴레이 이야기: {len(imported_stories)}개 / 전체 {len(stories)}개\n상태: "
            + ", ".join(
                f"{row['display_name']}={row['state']}"
                for row in [*imported_queues, *imported_stories]
            )
        )
        text += "\n레거시 비교: " + (
            "일치" if not mismatches else "; ".join(mismatches)
        )
        await interaction.response.send_message(
            (
                text
                if imported_queues or imported_stories
                else "가져온 레거시 기능이 없습니다."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
