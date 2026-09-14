"""Shared persistence and confirmation primitives for administrator commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from .localization import text


async def guild_settings(db, guild_id: int):
    """Ensure and return the settings row owned by ``guild_id``."""
    await db.execute(
        "INSERT INTO guild_settings(guild_id) VALUES($1) ON CONFLICT(guild_id) DO NOTHING",
        guild_id,
    )
    return await db.fetchrow("SELECT * FROM guild_settings WHERE guild_id=$1", guild_id)


class ConfirmationView(discord.ui.View):
    """Restrict a consequential confirmation action to its initiating member."""

    def __init__(self, user_id: int, action: Callable[[], Awaitable[str]], locale="en"):
        super().__init__(timeout=60)
        self.user_id, self.action, self.locale = user_id, action, locale
        self.confirm.label = text("admin.confirm.button", locale)
        self.cancel.label = text("admin.cancel.button", locale)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                text("admin.confirm.private_only", self.locale), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.stop()
        for child in self.children:
            child.disabled = True
        try:
            result = await self.action()
        except Exception:
            await interaction.response.edit_message(
                content=text("admin.confirm.failed", self.locale), view=self
            )
            raise
        await interaction.response.edit_message(content=result, view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            content=text("admin.confirm.cancelled", self.locale), view=None
        )
