"""Administrator-triggered and scheduled database backup uploads."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timezone
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database.backup import create_backup
from .localization import command_name, guild_locale, interaction_locale, text

logger = logging.getLogger(__name__)


class BackupCommands(commands.Cog):
    """Upload PostgreSQL archives to each guild's configured audit channel."""

    backup = app_commands.Group(
        name=command_name("backup"),
        description="Back up this bot database.",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.run_schedules.start()

    def cog_unload(self):
        self.run_schedules.cancel()

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        """Limit backup operations to server owners and Manage Server admins."""
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.send_message(
                text("backup.server_only", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return False
        settings = await self.bot.db.fetchrow(
            "SELECT admin_role_id FROM guild_settings WHERE guild_id=$1",
            interaction.guild.id,
        )
        has_admin_role = bool(
            settings
            and settings["admin_role_id"]
            and interaction.user.get_role(settings["admin_role_id"])
        )
        if (
            interaction.guild.owner_id != interaction.user.id
            and not interaction.user.guild_permissions.manage_guild
            and not has_admin_role
        ):
            await interaction.response.send_message(
                text("backup.denied", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return False
        return True

    async def _upload(
        self, guild_id: int, *, scheduled_for: datetime | None = None
    ) -> None:
        """Create one archive and send it to the configured audit channel."""
        row = await self.bot.db.fetchrow(
            "SELECT audit_channel_id FROM guild_settings WHERE guild_id=$1", guild_id
        )
        if not row or not row["audit_channel_id"]:
            locale = await guild_locale(self.bot.db, guild_id)
            raise RuntimeError(text("backup.audit_required", locale))
        channel = self.bot.get_channel(row["audit_channel_id"])
        if channel is None:
            channel = await self.bot.fetch_channel(row["audit_channel_id"])
        if not hasattr(channel, "send"):
            raise RuntimeError(
                text(
                    "backup.channel_invalid", await guild_locale(self.bot.db, guild_id)
                )
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with tempfile.TemporaryDirectory(prefix="secretroombot-backup-") as directory:
            archive = Path(directory) / f"secretroombot-{timestamp}.dump"
            # pg_dump is blocking; offload it so it cannot stall Discord heartbeats.
            await asyncio.to_thread(
                create_backup, os.getenv("DATABASE_URL", ""), archive
            )
            locale = await guild_locale(self.bot.db, guild_id)
            label_key = (
                "backup.label_scheduled" if scheduled_for else "backup.label_manual"
            )
            with archive.open("rb") as stream:
                await channel.send(
                    content=text(label_key, locale, timestamp=timestamp),
                    file=discord.File(stream, filename=archive.name),
                )

    @backup.command(
        name=command_name("upload"),
        description="Create and upload a backup to the audit channel.",
    )
    async def upload(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self._upload(interaction.guild_id)
        except Exception as error:
            logger.exception(
                "Manual backup upload failed for guild %s", interaction.guild_id
            )
            await interaction.followup.send(
                text(
                    "backup.upload_failed",
                    await interaction_locale(interaction),
                    error=error,
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            text("backup.uploaded", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @backup.command(
        name=command_name("schedule"),
        description="Set a daily or weekly audit-channel backup.",
    )
    @app_commands.choices(
        rate=[
            app_commands.Choice(name="Daily", value="daily"),
            app_commands.Choice(name="Weekly", value="weekly"),
        ]
    )
    @app_commands.describe(
        schedule_time="Local time in HH:MM format",
        timezone_name="IANA timezone, such as Asia/Seoul",
        weekday="For weekly schedules: 0=Monday through 6=Sunday",
    )
    @app_commands.rename(
        rate=command_name("rate"),
        schedule_time=command_name("time"),
        timezone_name=command_name("timezone"),
        weekday=command_name("weekday"),
    )
    async def schedule(
        self,
        interaction: discord.Interaction,
        rate: app_commands.Choice[str],
        schedule_time: str,
        timezone_name: str = "UTC",
        weekday: app_commands.Range[int, 0, 6] | None = None,
    ):
        if not await self._authorized(interaction):
            return
        try:
            if len(schedule_time) != 5 or schedule_time[2] != ":":
                raise ValueError("Time must use HH:MM format.")
            hour, minute = map(int, schedule_time.split(":"))
            parsed_time = time(hour, minute)
            ZoneInfo(timezone_name)
            if rate.value == "weekly" and weekday is None:
                raise ValueError(
                    "Weekly schedules require weekday (0=Monday through 6=Sunday)."
                )
            if rate.value == "daily":
                weekday = None
        except (ValueError, ZoneInfoNotFoundError):
            await interaction.response.send_message(
                text("backup.schedule_invalid", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        settings = await self.bot.db.fetchrow(
            "SELECT audit_channel_id FROM guild_settings WHERE guild_id=$1",
            interaction.guild_id,
        )
        if not settings or not settings["audit_channel_id"]:
            await interaction.response.send_message(
                text("backup.audit_required", await interaction_locale(interaction)),
                ephemeral=True,
            )
            return
        old = await self.bot.db.fetchrow(
            "SELECT cadence,schedule_time,schedule_weekday,timezone,enabled FROM backup_schedules WHERE guild_id=$1",
            interaction.guild_id,
        )
        await self.bot.db.execute(
            "INSERT INTO backup_schedules(guild_id,cadence,schedule_time,schedule_weekday,timezone,enabled) VALUES($1,$2,$3,$4,$5,TRUE) ON CONFLICT(guild_id) DO UPDATE SET cadence=EXCLUDED.cadence,schedule_time=EXCLUDED.schedule_time,schedule_weekday=EXCLUDED.schedule_weekday,timezone=EXCLUDED.timezone,enabled=TRUE,updated_at=NOW()",
            interaction.guild_id,
            rate.value,
            parsed_time,
            weekday,
            timezone_name,
        )
        before_state = {}
        if old:
            before_state = {
                **dict(old),
                "schedule_time": old["schedule_time"].strftime("%H:%M"),
            }
        await self.bot.db.execute(
            "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,before_state,after_state) VALUES($1,$2,'backup_schedule_set','backup_schedule',$3,$4)",
            interaction.guild_id,
            interaction.user.id,
            before_state,
            {
                "cadence": rate.value,
                "schedule_time": schedule_time,
                "timezone": timezone_name,
                "weekday": weekday,
            },
        )
        locale = await interaction_locale(interaction)
        response = text(
            "backup.schedule_set",
            locale,
            rate=text(f"backup.rate.{rate.value}", locale),
            schedule_time=schedule_time,
            timezone=timezone_name,
        )
        if weekday is not None:
            response += " " + text("backup.weekday", locale, weekday=weekday)
        await interaction.response.send_message(response, ephemeral=True)

    @backup.command(
        name=command_name("disable"),
        description="Disable scheduled database backups for this server.",
    )
    async def disable(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        old = await self.bot.db.fetchrow(
            "SELECT cadence,schedule_time,schedule_weekday,timezone,enabled FROM backup_schedules WHERE guild_id=$1",
            interaction.guild_id,
        )
        await self.bot.db.execute(
            "UPDATE backup_schedules SET enabled=FALSE,updated_at=NOW() WHERE guild_id=$1",
            interaction.guild_id,
        )
        if old:
            await self.bot.db.execute(
                "INSERT INTO admin_audit_log(guild_id,actor_id,action,target_type,before_state,after_state) VALUES($1,$2,'backup_schedule_disabled','backup_schedule',$3,$4)",
                interaction.guild_id,
                interaction.user.id,
                {
                    **dict(old),
                    "schedule_time": old["schedule_time"].strftime("%H:%M"),
                },
                {"enabled": False},
            )
        await interaction.response.send_message(
            text("backup.disabled", await interaction_locale(interaction)),
            ephemeral=True,
        )

    @tasks.loop(seconds=60)
    async def run_schedules(self):
        """Claim each due minute once, then upload and record its result."""
        now = datetime.now(timezone.utc)
        schedules = await self.bot.db.fetch(
            "SELECT * FROM backup_schedules WHERE enabled=TRUE"
        )
        for schedule in schedules:
            try:
                local_now = now.astimezone(ZoneInfo(schedule["timezone"]))
                if (local_now.hour, local_now.minute) != (
                    schedule["schedule_time"].hour,
                    schedule["schedule_time"].minute,
                ):
                    continue
                if (
                    schedule["cadence"] == "weekly"
                    and local_now.weekday() != schedule["schedule_weekday"]
                ):
                    continue
            except ZoneInfoNotFoundError:
                logger.error(
                    "Invalid timezone in backup schedule for guild %s",
                    schedule["guild_id"],
                )
                continue
            scheduled_for = local_now.replace(second=0, microsecond=0).astimezone(
                timezone.utc
            )
            async with self.bot.db.acquire() as conn:
                async with conn.transaction():
                    claimed = await conn.fetchval(
                        "INSERT INTO backup_jobs(guild_id,scheduled_for,state) VALUES($1,$2,'running') ON CONFLICT DO NOTHING RETURNING TRUE",
                        schedule["guild_id"],
                        scheduled_for,
                    )
            if not claimed:
                continue
            try:
                await self._upload(schedule["guild_id"], scheduled_for=scheduled_for)
                await self.bot.db.execute(
                    "UPDATE backup_jobs SET state='completed',finished_at=NOW() WHERE guild_id=$1 AND scheduled_for=$2",
                    schedule["guild_id"],
                    scheduled_for,
                )
            except Exception as error:
                logger.exception(
                    "Scheduled backup failed for guild %s", schedule["guild_id"]
                )
                await self.bot.db.execute(
                    "UPDATE backup_jobs SET state='failed',finished_at=NOW(),last_error=$3 WHERE guild_id=$1 AND scheduled_for=$2",
                    schedule["guild_id"],
                    scheduled_for,
                    str(error)[:1000],
                )
                await self._notify_failure(schedule["guild_id"])

    async def _notify_failure(self, guild_id: int) -> None:
        """Leave an operational notice when a scheduled archive could not upload."""
        try:
            settings = await self.bot.db.fetchrow(
                "SELECT audit_channel_id FROM guild_settings WHERE guild_id=$1",
                guild_id,
            )
            if not settings or not settings["audit_channel_id"]:
                return
            channel = self.bot.get_channel(settings["audit_channel_id"])
            if channel is None:
                channel = await self.bot.fetch_channel(settings["audit_channel_id"])
            await channel.send(
                text(
                    "backup.scheduled_failed", await guild_locale(self.bot.db, guild_id)
                )
            )
        except Exception:
            logger.exception(
                "Could not send scheduled backup failure notice for guild %s", guild_id
            )

    @run_schedules.before_loop
    async def before_run_schedules(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCommands(bot))
