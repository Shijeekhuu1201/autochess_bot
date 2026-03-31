from __future__ import annotations

import discord
from discord.ext import commands

UNRANKED_ROLE_NAME = "Unranked"


class ListenersCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        for guild in self.bot.guilds:
            await self._sync_unranked_role(guild)

    async def _ensure_unranked_role(self, guild: discord.Guild) -> discord.Role | None:
        role = discord.utils.get(guild.roles, name=UNRANKED_ROLE_NAME)
        if role is not None:
            return role

        try:
            return await guild.create_role(
                name=UNRANKED_ROLE_NAME,
                mentionable=False,
                reason="Default rank role",
            )
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _sync_unranked_role(self, guild: discord.Guild) -> None:
        role = await self._ensure_unranked_role(guild)
        if role is None:
            return

        for member in guild.members:
            if member.bot or role in member.roles:
                continue
            try:
                await member.add_roles(role, reason="Default unranked role")
            except (discord.Forbidden, discord.HTTPException):
                continue

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._sync_unranked_role(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        role = await self._ensure_unranked_role(member.guild)
        if role is None or role in member.roles:
            return

        try:
            await member.add_roles(role, reason="Member joined - default unranked role")
        except (discord.Forbidden, discord.HTTPException):
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ListenersCog(bot))
