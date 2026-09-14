"""Guild-scoped free-form relay stories."""

from __future__ import annotations

import os

import discord
from discord.ext import commands, tasks
from .game_config import game_enabled
from .localization import guild_locale, text


class RelayStoryService(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.prune_retention.start()

    def cog_unload(self):
        self.prune_retention.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        await self._import_legacy_story()

    async def _import_legacy_story(self):
        guild_id, channel_id = os.getenv("SECRET_ROOM_SERVER_ID"), os.getenv(
            "RELAY_STORY_ID"
        )
        if not (
            guild_id and channel_id and guild_id.isdecimal() and channel_id.isdecimal()
        ):
            return
        story = await self.bot.db.fetchrow(
            """INSERT INTO relay_stories(guild_id,story_key,display_name,channel_id,state,rules_text,posting_rules,reward_policy,provenance)
               VALUES($1,'legacy-relay-v1','이야기잇기',$2,'active',NULL,$3,$4,$5)
               ON CONFLICT(guild_id,story_key) DO UPDATE SET updated_at=NOW() RETURNING id""",
            int(guild_id),
            int(channel_id),
            {"require_intervening_contributor": True, "max_length": 200},
            {"type": "legacy_secret_multiplier", "multiplier": 1.05},
            {"migration_key": "legacy-relay-v1"},
        )
        # The legacy reward ledger is the authoritative historical source. It
        # supplies enough metadata to keep awarded message IDs from ever being
        # rewarded again after the generalized listener takes over.
        for reward in await self.bot.db.fetch(
            "SELECT message_id,discord_id,awarded_at FROM si_relay_rewards"
        ):
            await self.bot.db.execute(
                """INSERT INTO relay_contributions(story_id,message_id,author_id,status,contributed_at,reward_state,rewarded_at)
                   VALUES($1,$2,$3,'eligible',$4,'granted',$4)
                   ON CONFLICT(message_id) DO NOTHING""",
                story["id"],
                reward["message_id"],
                reward["discord_id"],
                reward["awarded_at"],
            )

    async def create_story(
        self, guild_id: int, name: str, channel_id: int, rules_text: str | None = None
    ):
        key = "story-" + name.lower().replace(" ", "-")[:40]
        return await self.bot.db.fetchrow(
            """INSERT INTO relay_stories(guild_id,story_key,display_name,channel_id,state,rules_text)
               VALUES($1,$2,$3,$4,'active',$5) RETURNING *""",
            guild_id,
            key,
            name,
            channel_id,
            rules_text,
        )

    async def validate_story_channel(self, guild_id: int, channel_id: int) -> None:
        """Ensure a story can be operated before storing its channel ID."""
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(
            channel_id
        )
        if getattr(
            getattr(channel, "guild", None), "id", None
        ) != guild_id or not hasattr(channel, "permissions_for"):
            raise ValueError("Relay story channel must belong to this server.")
        permissions = channel.permissions_for(channel.guild.me)
        if not (
            permissions.view_channel
            and permissions.send_messages
            and permissions.read_message_history
        ):
            raise ValueError(
                "The bot needs view, send, and message-history permissions in the relay story channel."
            )

    async def legacy_mismatches(self, guild_id: int) -> list[str]:
        """Compare the legacy relay import and its reward evidence."""
        configured_guild, configured_channel = os.getenv(
            "SECRET_ROOM_SERVER_ID"
        ), os.getenv("RELAY_STORY_ID")
        if not (
            configured_guild
            and configured_channel
            and configured_guild.isdecimal()
            and configured_channel.isdecimal()
        ):
            return []
        if int(configured_guild) != guild_id:
            return []
        story = await self.bot.db.fetchrow(
            "SELECT id,channel_id FROM relay_stories WHERE guild_id=$1 AND story_key='legacy-relay-v1'",
            guild_id,
        )
        if story is None:
            return ["relay: 가져온 이야기 없음"]
        mismatches = []
        if story["channel_id"] != int(configured_channel):
            mismatches.append("relay: 채널 불일치")
        legacy_count = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM si_relay_rewards"
        )
        imported_count = await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM relay_contributions WHERE story_id=$1 AND reward_state='granted'",
            story["id"],
        )
        if legacy_count != imported_count:
            mismatches.append(
                f"relay: 보상 기록 수 불일치 ({imported_count}/{legacy_count})"
            )
        return mismatches

    async def pin_rules(self, story_id: int):
        story = await self.bot.db.fetchrow(
            "SELECT * FROM relay_stories WHERE id=$1", story_id
        )
        if story is None or not story["rules_text"]:
            raise ValueError("고정할 이야기 규칙이 없습니다.")
        channel = self.bot.get_channel(
            story["channel_id"]
        ) or await self.bot.fetch_channel(story["channel_id"])
        message = await channel.send(story["rules_text"])
        await message.pin(reason="Relay story rules")
        return message

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        # Reply moderation is parsed by Admin; it is not story content and must
        # not receive an intervening-turn slot or a reward before deletion.
        if message.reference and message.content.lstrip().lower().startswith(
            "!moderate"
        ):
            return
        story = await self.bot.db.fetchrow(
            "SELECT * FROM relay_stories WHERE guild_id=$1 AND channel_id=$2",
            message.guild.id,
            message.channel.id,
        )
        if story is None or story["state"] != "active":
            return
        rules = story["posting_rules"]
        reason = None
        if rules.get("max_length") and len(message.content) > rules["max_length"]:
            locale = await guild_locale(
                self.bot.db, message.guild.id, message.guild.preferred_locale
            )
            reason = text("relay.reason.too_long", locale, length=rules["max_length"])
        if reason is None and rules.get("require_intervening_contributor", True):
            last_author = await self.bot.db.fetchval(
                "SELECT author_id FROM relay_contributions WHERE story_id=$1 AND status='eligible' ORDER BY contributed_at DESC, id DESC LIMIT 1",
                story["id"],
            )
            if last_author == message.author.id:
                locale = await guild_locale(
                    self.bot.db, message.guild.id, message.guild.preferred_locale
                )
                reason = text("relay.reason.consecutive", locale)
        if reason:
            await self._reject_message(message, story, reason)
            return
        contribution = await self.bot.db.fetchrow(
            "INSERT INTO relay_contributions(story_id,message_id,author_id,reward_state) VALUES($1,$2,$3,$4) ON CONFLICT(message_id) DO NOTHING RETURNING *",
            story["id"],
            message.id,
            message.author.id,
            (
                "pending"
                if story["reward_policy"].get("type") != "none"
                else "not_applicable"
            ),
        )
        if contribution:
            await self._issue_legacy_reward(story, contribution, message)

    async def _reject_message(self, message: discord.Message, story, reason: str):
        try:
            await message.delete()
        except discord.HTTPException:
            return
        try:
            locale = await guild_locale(
                self.bot.db, story["guild_id"], message.guild.preferred_locale
            )
            await message.author.send(
                text(
                    "relay.contribution_deleted",
                    locale,
                    name=story["display_name"],
                    reason=reason,
                )
            )
        except discord.HTTPException:
            pass
        await self.bot.db.execute(
            "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) VALUES($1,$2,'relay_contribution_rejected','relay_story',$3,$4)",
            story["guild_id"],
            message.author.id,
            str(story["id"]),
            {"message_id": message.id, "reason": reason},
        )

    async def _issue_legacy_reward(self, story, contribution, message):
        if not await game_enabled(self.bot.db, story["guild_id"]):
            await self.bot.db.execute(
                "UPDATE relay_contributions SET reward_state='not_applicable' WHERE id=$1",
                contribution["id"],
            )
            return
        policy_type = story["reward_policy"].get("type")
        if policy_type == "none":
            return
        try:
            if policy_type == "legacy_secret_multiplier":
                from .secretae_incremental.constants import SECRETS, SYMBOLS
                from .secretae_incremental.db import grant_relay_secret_reward
                from .secretae_incremental.numbers import LayeredDecimal, format_amount

                reward = await grant_relay_secret_reward(
                    self.bot.db, story["guild_id"], message.author.id, message.id
                )
            else:
                from .secretae_incremental.db import grant_configured_reward

                reward = await grant_configured_reward(
                    self.bot.db,
                    story["guild_id"],
                    "relay_story",
                    story["id"],
                    message.id,
                    message.author.id,
                    story["reward_policy"],
                )
            state = (
                "granted"
                if reward.get("awarded") or reward.get("already_awarded")
                else ("not_applicable" if reward.get("cap_reached") else "failed")
            )
            await self.bot.db.execute(
                "UPDATE relay_contributions SET reward_state=$2,rewarded_at=NOW() WHERE id=$1",
                contribution["id"],
                state,
            )
            if reward.get("awarded") and policy_type == "legacy_secret_multiplier":
                locale = await guild_locale(
                    self.bot.db, story["guild_id"], message.guild.preferred_locale
                )
                lines = [text("relay.legacy_reward.title", locale)]
                for key in SECRETS:
                    lines.append(
                        text(
                            "relay.legacy_reward.line",
                            locale,
                            symbol=SYMBOLS[key],
                            before=format_amount(
                                LayeredDecimal.from_json(reward["before"][key])
                            ),
                            after=format_amount(
                                LayeredDecimal.from_json(reward["after"][key])
                            ),
                        )
                    )
                try:
                    await message.author.send("\n".join(lines))
                except discord.HTTPException:
                    pass
        except Exception as exc:
            await self.bot.db.execute(
                "UPDATE relay_contributions SET reward_state='failed' WHERE id=$1",
                contribution["id"],
            )
            await self.bot.db.execute(
                "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) VALUES($1,0,'relay_reward_failed','relay_contribution',$2,$3)",
                story["guild_id"],
                str(contribution["id"]),
                {"error": type(exc).__name__},
            )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        row = await self.bot.db.fetchrow(
            "UPDATE relay_contributions SET status='withdrawn',moderated_at=NOW(),moderation_reason='source message deleted' WHERE message_id=$1 AND status='eligible' RETURNING story_id,author_id",
            payload.message_id,
        )
        if row:
            story = await self.bot.db.fetchrow(
                "SELECT guild_id FROM relay_stories WHERE id=$1", row["story_id"]
            )
            from .secretae_incremental.db import register_reward_reconciliation

            reconciliation = await register_reward_reconciliation(
                self.bot.db,
                story["guild_id"],
                "relay_story",
                payload.message_id,
                "source message deleted",
            )
            await self.bot.db.execute(
                "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) VALUES($1,$2,'relay_contribution_withdrawn','relay_contribution',$3,$4)",
                story["guild_id"],
                row["author_id"],
                str(payload.message_id),
                {"reason": "source message deleted"},
            )
            if reconciliation:
                await self.bot.db.execute(
                    "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,target_id,after_state) VALUES($1,0,'reward_reconciliation_required','relay_contribution',$2,$3)",
                    story["guild_id"],
                    str(payload.message_id),
                    reconciliation,
                )

    @tasks.loop(hours=24)
    async def prune_retention(self):
        await self.bot.db.execute(
            "DELETE FROM relay_contributions WHERE status IN ('disqualified','withdrawn') "
            "AND COALESCE(moderated_at,contributed_at) < NOW() - INTERVAL '90 days' "
            "AND reward_state IN ('not_applicable','revoked')"
        )

    @prune_retention.before_loop
    async def before_prune_retention(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(RelayStoryService(bot))
