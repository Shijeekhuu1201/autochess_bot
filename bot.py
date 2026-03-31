from __future__ import annotations

import asyncio
from pathlib import Path

import discord
from discord.ext import commands

from config.settings import SETTINGS
from core.db import init_db

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / SETTINGS.db_path

EXTENSIONS = [
    "cogs.admin_cog",
    "cogs.listeners_cog",
    "cogs.profile_cog",
    "cogs.registration_cog",
]


class AutoChessBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(
            command_prefix=SETTINGS.prefix or ".",
            intents=intents,
            help_command=None,
        )

        self.db_path = DB_PATH
        self._tree_synced = False

    async def setup_hook(self) -> None:
        for ext in EXTENSIONS:
            await self.load_extension(ext)

    async def on_ready(self) -> None:
        if not self._tree_synced:
            sync_guilds = []
            if SETTINGS.guild_id:
                guild = discord.Object(id=SETTINGS.guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                sync_guilds.append((SETTINGS.guild_id, len(synced)))
            else:
                for guild in self.guilds:
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    sync_guilds.append((guild.id, len(synced)))
            self._tree_synced = True
            if sync_guilds:
                details = ", ".join(f"{guild_id}:{count}" for guild_id, count in sync_guilds)
                print(f"Synced slash commands -> {details}")
        print(f"Logged in as {self.user} (ID: {self.user.id})")


async def main() -> None:
    if not SETTINGS.token:
        raise RuntimeError("DISCORD_TOKEN олдсонгүй. .env файлаа шалгана уу.")

    await init_db(str(DB_PATH))

    bot = AutoChessBot()
    async with bot:
        await bot.start(SETTINGS.token)


if __name__ == "__main__":
    asyncio.run(main())
