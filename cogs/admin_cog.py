from __future__ import annotations

import random
import sqlite3
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import discord
from discord.ext import commands, tasks

from config.constants import FINAL_KEY, SEMI_KEYS, ZONE_KEYS
from repositories.stage_repo import StageRepo
from services.bracket_service import BracketService
from services.registration_service import RegistrationService
from services.role_sync_service import RoleSyncService
from services.replacement_service import ReplacementService
from services.result_service import ResultService
from services.stats_service import StatsService
from services.supporter_service import SupporterService
from services.tournament_service import TournamentService

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "bot.db"

WEEKLY_THUMB_URL = "https://media.discordapp.net/attachments/1484376187211092098/1484558344206159942/145b1eda-6bcf-4777-9f07-56db26931803.png?ex=69beaa33&is=69bd58b3&hm=20e6f5d506db173495bb16d450be32a03b7d63d4a35f11f094951042a67d9859&=&format=webp&quality=lossless&width=960&height=960"
WEEKLY_POSTER_URL = "https://media.discordapp.net/attachments/1484376187211092098/1484558300350779574/content.png?ex=69beaa28&is=69bd58a8&hm=7f4af158b178a0e15ed2b0601c550f8e5b71ef440ca7148c36c81fe0b082a3a6&=&format=webp&quality=lossless&width=640&height=960"
WEEKLY_SITE_URL = "https://www.chessofmongolia.site/"
WEEKLY_CHAMPION_ROLE_NAME = "Weekly Champion"
WEEKLY_FOOTER_TEXT = "Chess Of Mongolia - Weekly Auto Chess Cup"
GENERAL_CHAT_CHANNEL_ID = 1487128079087177920
CHANNEL_NAME_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("\u1d00", "a"),
    ("\u0299", "b"),
    ("\u1d04", "c"),
    ("\u1d05", "d"),
    ("\u1d07", "e"),
    ("\ua730", "f"),
    ("\u0262", "g"),
    ("\u029c", "h"),
    ("\u026a", "i"),
    ("\u1d0a", "j"),
    ("\u1d0b", "k"),
    ("\u029f", "l"),
    ("\u1d0d", "m"),
    ("\u0274", "n"),
    ("\u1d0f", "o"),
    ("\u1d18", "p"),
    ("\u01eb", "q"),
    ("\u0280", "r"),
    ("\ua731", "s"),
    ("\u1d1b", "t"),
    ("\u1d1c", "u"),
    ("\u1d20", "v"),
    ("\u1d21", "w"),
    ("\u028f", "y"),
    ("\u1d22", "z"),
    ("\u2081", "1"),
    ("\u2082", "2"),
    ("\u2083", "3"),
    ("\u2084", "4"),
)


class WeeklyRegisterView(discord.ui.View):
    def __init__(self, cog: "AdminCog", disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(
            discord.ui.Button(
                label="ChessOfMongolia.Site",
                style=discord.ButtonStyle.link,
                url=WEEKLY_SITE_URL,
            )
        )
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.style is not discord.ButtonStyle.link:
                child.disabled = disabled

    @discord.ui.button(
        label="Бүртгүүлэх",
        style=discord.ButtonStyle.success,
        custom_id="weekly_register_join",
    )
    async def join_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message("Guild дотор ашиглана.", ephemeral=True)
            return

        tournament_id = self.cog._resolve_registration_tournament_id_from_message(
            interaction.guild.id,
            int(interaction.message.id) if interaction.message is not None else 0,
        )
        if tournament_id is None:
            await interaction.response.send_message("❌ Энэ register card-тай холбоотой tournament олдсонгүй.", ephemeral=True)
            return

        try:
            result = await self.cog.registration_service.join_tournament_button(
                tournament_id=tournament_id,
                user_id=interaction.user.id,
                display_name=interaction.user.display_name,
            )
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        tournament = result["tournament"]
        entry = result["entry"]

        if entry is None:
            await interaction.response.send_message("❌ Entry үүссэнгүй.", ephemeral=True)
            return

        await self.cog._post_waiting_review_card(
            guild=interaction.guild,
            tournament=tournament,
            entry=entry,
        )
        await self.cog.role_sync_service.sync_registration_roles_for_tournament(
            interaction.guild,
            int(tournament["id"]),
        )
        await self.cog._refresh_registration_ui(
            guild=interaction.guild,
            tournament_id=int(tournament["id"]),
        )

        await interaction.response.send_message(
            "✅ Таны бүртгэл waiting list-д орлоо. Төлбөр баталгаажмагц admin confirm хийнэ.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Бүртгэлээс хасах",
        style=discord.ButtonStyle.danger,
        custom_id="weekly_register_leave",
    )
    async def leave_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message("Guild дотор ашиглана.", ephemeral=True)
            return

        tournament_id = self.cog._resolve_registration_tournament_id_from_message(
            interaction.guild.id,
            int(interaction.message.id) if interaction.message is not None else 0,
        )
        if tournament_id is None:
            await interaction.response.send_message("❌ Энэ register card-тай холбоотой tournament олдсонгүй.", ephemeral=True)
            return

        try:
            result = await self.cog.registration_service.leave_tournament_button(
                tournament_id=tournament_id,
                user_id=interaction.user.id,
            )
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        tournament = result["tournament"]
        removed_entry = result["removed_entry"]

        await self.cog._mark_review_message_processed(
            guild=interaction.guild,
            tournament=tournament,
            entry=removed_entry,
            final_state="Removed by player",
            color=discord.Color.dark_grey(),
        )
        await self.cog.role_sync_service.sync_registration_roles_for_tournament(
            interaction.guild,
            int(tournament["id"]),
        )
        await self.cog._refresh_registration_ui(
            guild=interaction.guild,
            tournament_id=int(tournament["id"]),
        )

        await interaction.response.send_message(
            "✅ Таны бүртгэл waiting list-ээс хасагдлаа.",
            ephemeral=True,
        )


class WaitingReviewView(discord.ui.View):
    def __init__(self, cog: "AdminCog", disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = disabled

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return (
            interaction.user is not None
            and isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        )

    @discord.ui.button(
        label="Confirm",
        style=discord.ButtonStyle.success,
        custom_id="weekly_review_confirm",
    )
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Guild дотор ашиглана.", ephemeral=True)
            return

        if not self._is_admin(interaction):
            await interaction.response.send_message("❌ Зөвхөн admin confirm хийж чадна.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            result = await self.cog.registration_service.approve_entry_by_review_message(
                guild_id=interaction.guild.id,
                review_message_id=interaction.message.id,
            )
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        tournament = result["tournament"]
        entry = result["entry"]
        state_text = "CONFIRMED" if entry and entry["status"] == "confirmed" else "WAITLIST"

        if entry is not None:
            await self.cog._mark_review_message_processed(
                guild=interaction.guild,
                tournament=tournament,
                entry=entry,
                final_state=state_text,
                color=discord.Color.green() if state_text == "CONFIRMED" else discord.Color.orange(),
                target_message=interaction.message,
            )
            if entry["status"] == "confirmed":
                await self.cog.role_sync_service.extend_confirmed_role_expiry(
                    int(interaction.guild.id),
                    int(entry["user_id"]),
                )

        await self.cog.role_sync_service.sync_registration_roles_for_tournament(
            interaction.guild,
            int(tournament["id"]),
        )
        await self.cog._refresh_registration_ui(
            guild=interaction.guild,
            tournament_id=int(tournament["id"]),
        )
        await self.cog._send_registration_dm(
            user_id=int(entry["user_id"]),
            title="Tournament Registration Updated",
            lines=[
                f"Tournament: **{tournament['title']}**",
                f"Status: **{state_text}**",
                "Таны хүсэлтийг admin баталгаажууллаа.",
            ],
            color=discord.Color.green() if state_text == "CONFIRMED" else discord.Color.orange(),
        )
        await interaction.followup.send(f"✅ Entry {state_text} боллоо.", ephemeral=True)

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.secondary,
        custom_id="weekly_review_reject",
    )
    async def reject_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Guild дотор ашиглана.", ephemeral=True)
            return

        if not self._is_admin(interaction):
            await interaction.response.send_message("❌ Зөвхөн admin reject хийж чадна.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            result = await self.cog.registration_service.reject_entry_by_review_message(
                guild_id=interaction.guild.id,
                review_message_id=interaction.message.id,
            )
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        tournament = result["tournament"]
        entry = result["entry"]

        if entry is not None:
            await self.cog._mark_review_message_processed(
                guild=interaction.guild,
                tournament=tournament,
                entry=entry,
                final_state="REJECTED",
                color=discord.Color.red(),
                target_message=interaction.message,
            )

        await self.cog.role_sync_service.sync_registration_roles_for_tournament(
            interaction.guild,
            int(tournament["id"]),
        )
        await self.cog._refresh_registration_ui(
            guild=interaction.guild,
            tournament_id=int(tournament["id"]),
        )
        await self.cog._send_registration_dm(
            user_id=int(entry["user_id"]),
            title="Tournament Registration Updated",
            lines=[
                f"Tournament: **{tournament['title']}**",
                "Status: **REJECTED**",
                "Таны хүсэлтийг admin татгалзлаа.",
            ],
            color=discord.Color.red(),
        )
        await interaction.followup.send("✅ Entry rejected боллоо.", ephemeral=True)

    @discord.ui.button(
        label="Remove",
        style=discord.ButtonStyle.danger,
        custom_id="weekly_review_remove",
    )
    async def remove_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Guild дотор ашиглана.", ephemeral=True)
            return

        if not self._is_admin(interaction):
            await interaction.response.send_message("❌ Зөвхөн admin remove хийж чадна.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            result = await self.cog.registration_service.remove_entry_by_review_message(
                guild_id=interaction.guild.id,
                review_message_id=interaction.message.id,
            )
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        tournament = result["tournament"]
        removed_entry = result["removed_entry"]

        await self.cog._mark_review_message_processed(
            guild=interaction.guild,
            tournament=tournament,
            entry=removed_entry,
            final_state="REMOVED",
            color=discord.Color.dark_grey(),
            target_message=interaction.message,
        )

        await self.cog.role_sync_service.sync_registration_roles_for_tournament(
            interaction.guild,
            int(tournament["id"]),
        )
        await self.cog._refresh_registration_ui(
            guild=interaction.guild,
            tournament_id=int(tournament["id"]),
        )
        await self.cog._send_registration_dm(
            user_id=int(removed_entry["user_id"]),
            title="Tournament Registration Updated",
            lines=[
                f"Tournament: **{tournament['title']}**",
                "Status: **REMOVED**",
                "Таны бүртгэлийг waiting list-ээс хаслаа.",
            ],
            color=discord.Color.dark_grey(),
        )
        await interaction.followup.send("✅ Entry waiting list-ээс хасагдлаа.", ephemeral=True)


class RankedQueueView(discord.ui.View):
    def __init__(self, cog: "AdminCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="+",
        style=discord.ButtonStyle.success,
        custom_id="ranked_queue_join",
    )
    async def join_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or interaction.user is None or interaction.message is None:
            await interaction.response.send_message("Guild дотор ашиглана.", ephemeral=True)
            return

        queue = self.cog._get_ranked_queue_by_message_id(interaction.guild.id, int(interaction.message.id))
        if queue is None:
            await interaction.response.send_message("❌ Энэ ranked card-тай холбоотой queue олдсонгүй.", ephemeral=True)
            return

        try:
            self.cog._ranked_join_queue(
                queue_id=int(queue["id"]),
                user_id=int(interaction.user.id),
                display_name=interaction.user.display_name,
            )
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        await self.cog._refresh_ranked_queue_message(interaction.guild, int(queue["id"]))
        await interaction.response.send_message("✅ Ranked queue-д бүртгэгдлээ.", ephemeral=True)

    @discord.ui.button(
        label="-",
        style=discord.ButtonStyle.danger,
        custom_id="ranked_queue_leave",
    )
    async def leave_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or interaction.user is None or interaction.message is None:
            await interaction.response.send_message("Guild дотор ашиглана.", ephemeral=True)
            return

        queue = self.cog._get_ranked_queue_by_message_id(interaction.guild.id, int(interaction.message.id))
        if queue is None:
            await interaction.response.send_message("❌ Энэ ranked card-тай холбоотой queue олдсонгүй.", ephemeral=True)
            return

        try:
            self.cog._ranked_leave_queue(
                queue_id=int(queue["id"]),
                user_id=int(interaction.user.id),
            )
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        await self.cog._refresh_ranked_queue_message(interaction.guild, int(queue["id"]))
        await interaction.response.send_message("✅ Ranked queue-с хасагдлаа.", ephemeral=True)


class AnnouncementLinkView(discord.ui.View):
    def __init__(self, label: str, url: str) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label=label[:80] if label else "Open",
                style=discord.ButtonStyle.link,
                url=url,
            )
        )


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db_path = Path(getattr(bot, "db_path", DB_PATH))
        self.stage_repo = StageRepo(self.db_path)
        self.registration_service = RegistrationService(self.db_path)
        self.role_sync_service = RoleSyncService(self.db_path)
        self.supporter_service = SupporterService(self.db_path)
        self.bracket_service = BracketService(self.db_path)
        self.replacement_service = ReplacementService(self.db_path)
        self.result_service = ResultService(self.db_path)
        self.stats_service = StatsService(self.db_path)
        self.tournament_service = TournamentService(self.db_path)
        self._ensure_platform_donations_table()
        self._ensure_sponsors_table()
        self._ensure_announcements_table()
        self._ensure_ranked_tables()

    async def cog_load(self) -> None:
        self.bot.add_view(WeeklyRegisterView(self))
        self.bot.add_view(WaitingReviewView(self))
        if not self.support_expiry_loop.is_running():
            self.support_expiry_loop.start()
        if not self.web_registration_poll_loop.is_running():
            self.web_registration_poll_loop.start()
        if not self.announcement_publish_loop.is_running():
            self.announcement_publish_loop.start()
        if not self.tournament_action_loop.is_running():
            self.tournament_action_loop.start()

    def cog_unload(self) -> None:
        if self.support_expiry_loop.is_running():
            self.support_expiry_loop.cancel()
        if self.web_registration_poll_loop.is_running():
            self.web_registration_poll_loop.cancel()
        if self.announcement_publish_loop.is_running():
            self.announcement_publish_loop.cancel()
        if self.tournament_action_loop.is_running():
            self.tournament_action_loop.cancel()

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        if ctx.guild is None or ctx.command is None:
            return

        if ctx.command.name == "weekly_end":
            await self.role_sync_service.clear_transient_roles(ctx.guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or message.author.bot
            or not isinstance(message.channel, discord.TextChannel)
        ):
            return

        text = (message.content or "").strip()
        if text not in {"+", "-"}:
            return

        queue = self._get_latest_active_ranked_queue_for_channel(message.guild.id, message.channel.id)
        if queue is None:
            return

        try:
            if text == "+":
                self._ranked_join_queue(
                    queue_id=int(queue["id"]),
                    user_id=int(message.author.id),
                    display_name=getattr(message.author, "display_name", message.author.name),
                )
            else:
                self._ranked_leave_queue(
                    queue_id=int(queue["id"]),
                    user_id=int(message.author.id),
                )
        except ValueError as e:
            await message.channel.send(f"❌ {e}", delete_after=6)
            await self._delete_message_quietly(message)
            return

        await self._refresh_ranked_queue_message(message.guild, int(queue["id"]))
        await self._delete_message_quietly(message)

    @tasks.loop(hours=1)
    async def support_expiry_loop(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self.supporter_service.sync_all_guild_roles(guild)
            await self.role_sync_service.sync_confirmed_role_members(guild)

    @tasks.loop(seconds=20)
    async def announcement_publish_loop(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            announcements = self._list_queued_announcements(guild.id)
            for item in announcements:
                channel = self._resolve_announcement_channel(guild, str(item.get("target_channel") or "announcements"))
                if not isinstance(channel, discord.TextChannel):
                    continue

                embed = self._build_announcement_embed(item)
                view = None
                button_url = str(item.get("button_url") or "").strip()
                button_text = str(item.get("button_text") or "").strip()
                if self._is_supported_url(button_url):
                    view = AnnouncementLinkView(button_text or "Open", button_url)
                message = await channel.send(embed=embed, view=view)
                self._mark_announcement_published(
                    int(item["id"]),
                    channel_id=channel.id,
                    message_id=message.id,
                    item=item,
                )

    @tasks.loop(seconds=10)
    async def tournament_action_loop(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            actions = self._list_queued_tournament_admin_actions(guild.id)
            for item in actions:
                action_id = int(item["id"])
                self._mark_tournament_admin_action_status(action_id, "processing")
                try:
                    await self._process_tournament_admin_action(guild, item)
                except Exception as e:
                    self._mark_tournament_admin_action_status(action_id, "failed", error_text=str(e))
                else:
                    self._mark_tournament_admin_action_status(action_id, "done")

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _has_ranked_mod_access(self, ctx: commands.Context) -> bool:
        author = getattr(ctx, "author", None)
        return isinstance(author, discord.Member) and (
            author.guild_permissions.administrator or author.guild_permissions.manage_messages
        )

    def _is_supported_url(self, value: str) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return False
        parsed = urlparse(raw)
        return parsed.scheme in {"http", "https", "discord"} and bool(parsed.netloc or parsed.path)

    def _ensure_ranked_tables(self) -> None:
        with self._get_db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS ranked_queues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL DEFAULT 0,
                    queue_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    entry_fee INTEGER NOT NULL DEFAULT 0,
                    max_players INTEGER NOT NULL DEFAULT 8,
                    status TEXT NOT NULL DEFAULT 'open',
                    winner_user_id INTEGER,
                    winner_user_id_2 INTEGER,
                    created_by INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    stopped_at TEXT,
                    completed_at TEXT
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ranked_queues_guild_type_status
                ON ranked_queues(guild_id, queue_type, status, created_at)
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ranked_queues_channel
                ON ranked_queues(channel_id, status, created_at)
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS ranked_queue_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    queue_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    confirmed_at TEXT,
                    confirm_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (queue_id) REFERENCES ranked_queues(id) ON DELETE CASCADE
                )
                """
            )
            db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ranked_queue_entries_unique_user
                ON ranked_queue_entries(queue_id, user_id)
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ranked_queue_entries_status
                ON ranked_queue_entries(queue_id, status, joined_at)
                """
            )
            db.commit()

    def _ranked_queue_type_from_channel(self, channel: discord.abc.GuildChannel | None) -> str | None:
        if channel is None:
            return None
        normalized = self._normalize_channel_name(getattr(channel, "name", ""))
        if "soloranked" in normalized:
            return "solo"
        if "duoranked" in normalized:
            return "duo"
        return None

    def _get_ranked_queue_by_id(self, queue_id: int) -> sqlite3.Row | None:
        with self._get_db() as db:
            return db.execute(
                "SELECT * FROM ranked_queues WHERE id = ?",
                (int(queue_id),),
            ).fetchone()

    def _get_ranked_queue_by_message_id(self, guild_id: int, message_id: int) -> sqlite3.Row | None:
        with self._get_db() as db:
            return db.execute(
                """
                SELECT * FROM ranked_queues
                WHERE guild_id = ? AND message_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(guild_id), int(message_id)),
            ).fetchone()

    def _get_latest_active_ranked_queue_for_channel(self, guild_id: int, channel_id: int) -> sqlite3.Row | None:
        with self._get_db() as db:
            return db.execute(
                """
                SELECT * FROM ranked_queues
                WHERE guild_id = ? AND channel_id = ? AND status != 'completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(guild_id), int(channel_id)),
            ).fetchone()

    def _get_latest_unfinished_ranked_queue_by_type(self, guild_id: int, queue_type: str) -> sqlite3.Row | None:
        with self._get_db() as db:
            return db.execute(
                """
                SELECT * FROM ranked_queues
                WHERE guild_id = ? AND queue_type = ? AND status != 'completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(guild_id), str(queue_type)),
            ).fetchone()

    def _list_ranked_queue_entries(self, queue_id: int) -> list[sqlite3.Row]:
        with self._get_db() as db:
            return db.execute(
                """
                SELECT *
                FROM ranked_queue_entries
                WHERE queue_id = ?
                ORDER BY
                    CASE status
                        WHEN 'confirmed' THEN 0
                        WHEN 'queued' THEN 1
                        ELSE 2
                    END,
                    CASE WHEN confirm_order > 0 THEN confirm_order ELSE 99999 END,
                    joined_at ASC,
                    id ASC
                """,
                (int(queue_id),),
            ).fetchall()

    def _create_ranked_queue(
        self,
        guild_id: int,
        channel_id: int,
        queue_type: str,
        created_by: int,
    ) -> sqlite3.Row:
        title = "Ranked Solo Queue" if queue_type == "solo" else "Ranked Duo Queue"
        with self._get_db() as db:
            cursor = db.execute(
                """
                INSERT INTO ranked_queues (
                    guild_id, channel_id, queue_type, title, created_by
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (int(guild_id), int(channel_id), str(queue_type), title, int(created_by)),
            )
            queue_id = int(cursor.lastrowid)
            db.commit()
        queue = self._get_ranked_queue_by_id(queue_id)
        if queue is None:
            raise ValueError("Ranked queue үүсгэж чадсангүй.")
        return queue

    def _set_ranked_queue_message_id(self, queue_id: int, message_id: int) -> None:
        with self._get_db() as db:
            db.execute(
                "UPDATE ranked_queues SET message_id = ? WHERE id = ?",
                (int(message_id), int(queue_id)),
            )
            db.commit()

    def _ranked_join_queue(self, queue_id: int, user_id: int, display_name: str) -> None:
        queue = self._get_ranked_queue_by_id(queue_id)
        if queue is None:
            raise ValueError("Ranked queue олдсонгүй.")
        if str(queue["status"]) != "open":
            raise ValueError("Queue нээлттэй биш байна.")

        with self._get_db() as db:
            existing = db.execute(
                "SELECT * FROM ranked_queue_entries WHERE queue_id = ? AND user_id = ?",
                (int(queue_id), int(user_id)),
            ).fetchone()
            if existing is not None and str(existing["status"]) in {"queued", "confirmed"}:
                raise ValueError("Та энэ queue-д аль хэдийн бүртгүүлсэн байна.")
            if existing is None:
                db.execute(
                    """
                    INSERT INTO ranked_queue_entries (queue_id, user_id, display_name, status)
                    VALUES (?, ?, ?, 'queued')
                    """,
                    (int(queue_id), int(user_id), display_name),
                )
            else:
                db.execute(
                    """
                    UPDATE ranked_queue_entries
                    SET display_name = ?, status = 'queued', confirmed_at = NULL, confirm_order = 0
                    WHERE id = ?
                    """,
                    (display_name, int(existing["id"])),
                )
            db.commit()

    def _ranked_leave_queue(self, queue_id: int, user_id: int) -> None:
        queue = self._get_ranked_queue_by_id(queue_id)
        if queue is None:
            raise ValueError("Ranked queue олдсонгүй.")
        if str(queue["status"]) != "open":
            raise ValueError("Queue хаагдсан тул moderator-оор хасуулна.")

        with self._get_db() as db:
            existing = db.execute(
                "SELECT * FROM ranked_queue_entries WHERE queue_id = ? AND user_id = ?",
                (int(queue_id), int(user_id)),
            ).fetchone()
            if existing is None or str(existing["status"]) == "removed":
                raise ValueError("Та энэ queue-д бүртгэлгүй байна.")
            if str(existing["status"]) == "confirmed":
                raise ValueError("Та confirmed болсон байна. Moderator-оор хасуулна.")

            db.execute(
                """
                UPDATE ranked_queue_entries
                SET status = 'removed', confirmed_at = NULL, confirm_order = 0
                WHERE id = ?
                """,
                (int(existing["id"]),),
            )
            db.commit()

    def _ranked_confirm_member(self, queue_id: int, member: discord.Member) -> tuple[sqlite3.Row, sqlite3.Row]:
        queue = self._get_ranked_queue_by_id(queue_id)
        if queue is None:
            raise ValueError("Ranked queue олдсонгүй.")
        if str(queue["status"]) == "completed":
            raise ValueError("Энэ queue дууссан байна.")
        if str(queue["status"]) == "open":
            raise ValueError("Эхлээд `.stop` хийж queue-г хаана.")

        with self._get_db() as db:
            entry = db.execute(
                """
                SELECT * FROM ranked_queue_entries
                WHERE queue_id = ? AND user_id = ?
                """,
                (int(queue_id), int(member.id)),
            ).fetchone()
            if entry is None:
                raise ValueError("Энэ тоглогч queue-д бүртгэгдээгүй байна.")
            if str(entry["status"]) == "removed":
                raise ValueError("Энэ тоглогч queue-с хасагдсан байна.")
            if str(entry["status"]) == "confirmed":
                raise ValueError("Энэ тоглогч аль хэдийн confirmed болсон байна.")

            confirmed_count = db.execute(
                """
                SELECT COUNT(*) AS c
                FROM ranked_queue_entries
                WHERE queue_id = ? AND status = 'confirmed'
                """,
                (int(queue_id),),
            ).fetchone()["c"]
            if int(confirmed_count) >= int(queue["max_players"]):
                raise ValueError(f"Confirmed {int(queue['max_players'])}/{int(queue['max_players'])} болсон байна.")

            confirm_order = int(confirmed_count) + 1
            db.execute(
                """
                UPDATE ranked_queue_entries
                SET display_name = ?, status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP, confirm_order = ?
                WHERE id = ?
                """,
                (member.display_name, confirm_order, int(entry["id"])),
            )

            next_status = "ready" if confirm_order >= int(queue["max_players"]) else "locked"
            db.execute(
                "UPDATE ranked_queues SET status = ? WHERE id = ?",
                (next_status, int(queue_id)),
            )
            db.commit()

        fresh_queue = self._get_ranked_queue_by_id(queue_id)
        fresh_entry = None
        for row in self._list_ranked_queue_entries(queue_id):
            if int(row["user_id"]) == int(member.id):
                fresh_entry = row
                break
        if fresh_queue is None or fresh_entry is None:
            raise ValueError("Confirmed update хийж чадсангүй.")
        return fresh_queue, fresh_entry

    def _ranked_stop_queue(self, queue_id: int) -> sqlite3.Row:
        queue = self._get_ranked_queue_by_id(queue_id)
        if queue is None:
            raise ValueError("Ranked queue олдсонгүй.")
        if str(queue["status"]) == "completed":
            raise ValueError("Queue аль хэдийн дууссан байна.")

        with self._get_db() as db:
            db.execute(
                """
                UPDATE ranked_queues
                SET status = 'locked', stopped_at = COALESCE(stopped_at, CURRENT_TIMESTAMP)
                WHERE id = ?
                """,
                (int(queue_id),),
            )
            db.commit()
        fresh_queue = self._get_ranked_queue_by_id(queue_id)
        if fresh_queue is None:
            raise ValueError("Queue update хийж чадсангүй.")
        return fresh_queue

    def _ranked_complete_with_winners(self, queue_id: int, winner_ids: list[int]) -> sqlite3.Row:
        queue = self._get_ranked_queue_by_id(queue_id)
        if queue is None:
            raise ValueError("Ranked queue олдсонгүй.")
        if str(queue["status"]) == "open":
            raise ValueError("Эхлээд `.stop` хийж queue-г хаана.")
        if str(queue["status"]) == "completed":
            raise ValueError("Winner аль хэдийн тодорсон байна.")

        entries = self._list_ranked_queue_entries(queue_id)
        confirmed_ids = {int(row["user_id"]) for row in entries if str(row["status"]) == "confirmed"}
        for winner_id in winner_ids:
            if int(winner_id) not in confirmed_ids:
                raise ValueError("Winner нь confirmed болсон тоглогч байх ёстой.")

        with self._get_db() as db:
            db.execute(
                """
                UPDATE ranked_queues
                SET status = 'completed',
                    winner_user_id = ?,
                    winner_user_id_2 = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(winner_ids[0]),
                    int(winner_ids[1]) if len(winner_ids) > 1 else None,
                    int(queue_id),
                ),
            )
            db.commit()

        fresh_queue = self._get_ranked_queue_by_id(queue_id)
        if fresh_queue is None:
            raise ValueError("Winner update хийж чадсангүй.")
        return fresh_queue

    def _ranked_status_label(self, status: str) -> str:
        return {
            "open": "Open",
            "locked": "Locked",
            "ready": "Ready",
            "completed": "Completed",
        }.get(status, status.title())

    def _build_ranked_queue_embed(self, queue: sqlite3.Row, guild: discord.Guild | None = None) -> discord.Embed:
        queue_type = str(queue["queue_type"])
        entries = self._list_ranked_queue_entries(int(queue["id"]))
        queued = [row for row in entries if str(row["status"]) == "queued"]
        confirmed = [row for row in entries if str(row["status"]) == "confirmed"]

        title = "Ranked Solo" if queue_type == "solo" else "Ranked Duo"
        color = discord.Color.gold() if queue_type == "solo" else discord.Color.blurple()
        embed = discord.Embed(
            title=f"{title} Queue",
            description=(
                f"**Status:** {self._ranked_status_label(str(queue['status']))}\n"
                f"**Confirmed:** {len(confirmed)}/{int(queue['max_players'])}\n"
                f"**Queued:** {len(queued)}"
            ),
            color=color,
        )
        embed.set_footer(text=WEEKLY_FOOTER_TEXT)
        if WEEKLY_THUMB_URL:
            embed.set_thumbnail(url=WEEKLY_THUMB_URL)

        def _render(rows: list[sqlite3.Row], *, confirmed_rows: bool) -> str:
            if not rows:
                return "—"
            lines: list[str] = []
            for idx, row in enumerate(rows, start=1):
                prefix = f"`{int(row['confirm_order'])}`" if confirmed_rows and int(row["confirm_order"] or 0) > 0 else f"`{idx}`"
                member = guild.get_member(int(row["user_id"])) if guild is not None else None
                label = member.mention if member is not None else str(row["display_name"])
                lines.append(f"{prefix} {label}")
            return "\n".join(lines[:16])

        embed.add_field(name="Confirmed", value=_render(confirmed, confirmed_rows=True), inline=False)
        embed.add_field(name="Queue", value=_render(queued, confirmed_rows=False), inline=False)

        winner_lines: list[str] = []
        if queue["winner_user_id"]:
            member = guild.get_member(int(queue["winner_user_id"])) if guild is not None else None
            winner_lines.append(member.mention if member is not None else f"<@{int(queue['winner_user_id'])}>")
        if queue["winner_user_id_2"]:
            member = guild.get_member(int(queue["winner_user_id_2"])) if guild is not None else None
            winner_lines.append(member.mention if member is not None else f"<@{int(queue['winner_user_id_2'])}>")
        if winner_lines:
            embed.add_field(name="Winner", value=" + ".join(winner_lines), inline=False)

        embed.add_field(
            name="Player Flow",
            value="Channel дээр `+` бичээд бүртгүүлнэ.\nChannel дээр `-` бичээд бүртгэлээс гарна.",
            inline=False,
        )
        embed.add_field(
            name="Moderator Flow",
            value=(
                "`.list` Queue list\n"
                "`.stop` Close queue\n"
                "`.add @user` Confirm payment\n"
                "`.win @user` / `.duowin @u1 @u2`"
            ),
            inline=False,
        )
        return embed

    async def _refresh_ranked_queue_message(self, guild: discord.Guild, queue_id: int) -> None:
        queue = self._get_ranked_queue_by_id(queue_id)
        if queue is None:
            return

        channel = guild.get_channel(int(queue["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return

        embed = self._build_ranked_queue_embed(queue, guild)
        message = await self._fetch_message_safe(channel, int(queue["message_id"] or 0))
        if message is None:
            message = await channel.send(embed=embed)
            self._set_ranked_queue_message_id(int(queue["id"]), int(message.id))
            return

        await message.edit(embed=embed, view=None)

    def _ensure_platform_donations_table(self) -> None:
        with self._get_db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    donor_name TEXT NOT NULL,
                    donor_user_id INTEGER,
                    amount INTEGER NOT NULL DEFAULT 0,
                    note TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(platform_donations)").fetchall()
            }
            if "donor_user_id" not in columns:
                db.execute("ALTER TABLE platform_donations ADD COLUMN donor_user_id INTEGER")
            db.commit()

    def _ensure_announcements_table(self) -> None:
        with self._get_db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS announcements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL DEFAULT 0,
                    tournament_id INTEGER,
                    announcement_type TEXT NOT NULL DEFAULT 'tournament',
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    badge TEXT NOT NULL DEFAULT 'Announcement',
                    button_text TEXT NOT NULL DEFAULT '',
                    button_url TEXT NOT NULL DEFAULT '',
                    image_url TEXT NOT NULL DEFAULT '',
                    target_channel TEXT NOT NULL DEFAULT 'announcements',
                    status TEXT NOT NULL DEFAULT 'draft',
                    repeat_hours INTEGER NOT NULL DEFAULT 0,
                    publish_count INTEGER NOT NULL DEFAULT 0,
                    max_publishes INTEGER NOT NULL DEFAULT 1,
                    next_publish_at TEXT,
                    end_at TEXT,
                    published_message_id INTEGER NOT NULL DEFAULT 0,
                    published_channel_id INTEGER NOT NULL DEFAULT 0,
                    published_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(announcements)").fetchall()
            }
            if "badge" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN badge TEXT NOT NULL DEFAULT 'Announcement'")
            if "guild_id" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN guild_id INTEGER NOT NULL DEFAULT 0")
            if "announcement_type" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN announcement_type TEXT NOT NULL DEFAULT 'tournament'")
            if "button_text" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN button_text TEXT NOT NULL DEFAULT ''")
            if "button_url" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN button_url TEXT NOT NULL DEFAULT ''")
            if "image_url" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
            if "target_channel" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN target_channel TEXT NOT NULL DEFAULT 'announcements'")
            if "status" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
            if "repeat_hours" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN repeat_hours INTEGER NOT NULL DEFAULT 0")
            if "publish_count" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN publish_count INTEGER NOT NULL DEFAULT 0")
            if "max_publishes" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN max_publishes INTEGER NOT NULL DEFAULT 1")
            if "next_publish_at" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN next_publish_at TEXT")
            if "end_at" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN end_at TEXT")
            if "published_message_id" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN published_message_id INTEGER NOT NULL DEFAULT 0")
            if "published_channel_id" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN published_channel_id INTEGER NOT NULL DEFAULT 0")
            if "published_at" not in columns:
                db.execute("ALTER TABLE announcements ADD COLUMN published_at TEXT")
            db.commit()

    def _utc_now_sql(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _future_sql(self, *, hours: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    def _ensure_sponsors_table(self) -> None:
        with self._get_db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS sponsors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tournament_id INTEGER NOT NULL,
                    sponsor_kind TEXT NOT NULL DEFAULT 'tournament',
                    sponsor_name TEXT NOT NULL,
                    sponsor_user_id INTEGER,
                    amount INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    created_by INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(sponsors)").fetchall()
            }
            if "sponsor_user_id" not in columns:
                db.execute("ALTER TABLE sponsors ADD COLUMN sponsor_user_id INTEGER")
            if "sponsor_kind" not in columns:
                db.execute("ALTER TABLE sponsors ADD COLUMN sponsor_kind TEXT NOT NULL DEFAULT 'tournament'")
            if "created_by" not in columns:
                db.execute("ALTER TABLE sponsors ADD COLUMN created_by INTEGER NOT NULL DEFAULT 0")
            db.commit()

    def _get_active_weekly_tournament(self, guild_id: int) -> dict | None:
        with self._get_db() as db:
            row = db.execute(
                """
                SELECT *
                FROM tournaments
                WHERE guild_id = ?
                  AND type = 'weekly'
                  AND status NOT IN ('completed', 'cancelled')
                ORDER BY id DESC
                LIMIT 1
                """,
                (guild_id,),
            ).fetchone()
            return dict(row) if row else None

    def _resolve_member_from_target(
        self,
        ctx: commands.Context,
        target: str,
    ) -> discord.Member | None:
        if ctx.guild is None:
            return None

        mentions = list(getattr(ctx.message, "mentions", []) or [])
        if mentions:
            return mentions[0]

        text = (target or "").strip()
        if not text:
            return None

        ids = re.findall(r"\d{5,20}", text)
        if len(ids) == 1:
            member = ctx.guild.get_member(int(ids[0]))
            if member is not None:
                return member

        lowered = text.lower()
        for member in ctx.guild.members:
            if member.display_name.lower() == lowered or member.name.lower() == lowered:
                return member
        return None

    async def _submit_random_stage_results(self, guild_id: int, stage_key: str) -> dict:
        tournament = await self.result_service._resolve_tournament_for_stage(guild_id, stage_key)
        stage = await self.result_service.result_repo.get_stage_by_key(int(tournament["id"]), stage_key)
        if stage is None:
            raise ValueError(f"Stage not found: {stage_key}")

        slots = await self.result_service.result_repo.list_stage_slots_with_entries(int(stage["id"]))
        if len(slots) != 8:
            raise ValueError(f"{stage_key} дээр яг 8 player оруулах ёстой.")

        ordered_user_ids = [int(slot["user_id"]) for slot in sorted(slots, key=lambda item: int(item["slot_no"]))]
        for game_no in range(1, int(stage["game_count"]) + 1):
            shuffled = ordered_user_ids[:]
            random.shuffle(shuffled)
            await self.result_service.submit_stage_result(
                guild_id=guild_id,
                stage_key=stage_key,
                game_no=game_no,
                ordered_user_ids=shuffled,
            )

        return await self.result_service.get_stage_results(guild_id, stage_key)

    def _update_tournament_field(self, tournament_id: int, field_name: str, value) -> None:
        allowed = {"entry_fee", "start_time", "checkin_time"}
        if field_name not in allowed:
            raise ValueError("Invalid field")

        with self._get_db() as db:
            db.execute(
                f"UPDATE tournaments SET {field_name} = ? WHERE id = ?",
                (value, tournament_id),
            )
            db.commit()

    def _resolve_registration_tournament_id_from_message(self, guild_id: int, message_id: int) -> int | None:
        if not message_id:
            return None
        with self._get_db() as db:
            row = db.execute(
                """
                SELECT id
                FROM tournaments
                WHERE guild_id = ?
                  AND register_message_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(guild_id), int(message_id)),
            ).fetchone()
            return int(row["id"]) if row else None

    def _parse_name_and_note(self, raw: str) -> tuple[str, str]:
        text = (raw or "").strip()
        if not text:
            return "", ""

        if "|" in text:
            name, note = text.split("|", 1)
            return name.strip(), note.strip()

        return text, ""

    def _normalize_member_label(self, ctx: commands.Context, raw_name: str) -> str:
        text = (raw_name or "").strip()
        if not text:
            return ""

        mentions = list(getattr(ctx.message, "mentions", []) or [])
        if len(mentions) == 1 and text.startswith("<@") and text.endswith(">"):
            return mentions[0].display_name

        return text

    def _extract_support_target(
        self,
        ctx: commands.Context,
        raw_name: str,
        member: discord.Member | None = None,
    ) -> tuple[str, int | None]:
        if member is not None:
            return member.display_name, int(member.id)
        text = (raw_name or "").strip()
        matched_member = self._resolve_member_from_target(ctx, text)
        if matched_member is not None:
            return matched_member.display_name, int(matched_member.id)
        mentions = list(getattr(ctx.message, "mentions", []) or [])
        if len(mentions) == 1 and text.startswith("<@") and text.endswith(">"):
            member = mentions[0]
            return member.display_name, int(member.id)
        mention_ids = re.findall(r"\d{5,20}", text)
        if len(mention_ids) == 1 and ctx.guild is not None:
            matched_member = ctx.guild.get_member(int(mention_ids[0]))
            if matched_member is not None:
                return matched_member.display_name, int(matched_member.id)
        return text, None

    def _extract_ordered_user_ids(self, ctx: commands.Context, placements: str = "") -> list[int]:
        if getattr(ctx, "message", None) is not None and getattr(ctx.message, "raw_mentions", None):
            return [int(user_id) for user_id in ctx.message.raw_mentions]
        return [int(user_id) for user_id in re.findall(r"\d{5,20}", placements or "")]

    def _platform_add_donation(
        self,
        donor_name: str,
        amount: int,
        note: str = "",
        donor_user_id: int | None = None,
    ) -> None:
        with self._get_db() as db:
            db.execute(
                """
                INSERT INTO platform_donations (donor_name, donor_user_id, amount, note)
                VALUES (?, ?, ?, ?)
                """,
                (donor_name, donor_user_id, amount, note),
            )
            db.commit()

    def _reset_tournament_stages_for_test(self, tournament_id: int) -> None:
        with self._get_db() as db:
            db.execute(
                """
                DELETE FROM replacements
                WHERE tournament_id = ?
                """,
                (int(tournament_id),),
            )
            db.execute(
                """
                DELETE FROM stages
                WHERE tournament_id = ?
                """,
                (int(tournament_id),),
            )
            db.execute(
                """
                DELETE FROM tournament_admin_actions
                WHERE tournament_id = ?
                """,
                (int(tournament_id),),
            )
            db.execute(
                """
                UPDATE tournaments
                SET status = 'registration_locked'
                WHERE id = ?
                """,
                (int(tournament_id),),
            )
            db.commit()

    def _platform_get_donations(self) -> list[dict]:
        with self._get_db() as db:
            rows = db.execute(
                """
                SELECT donor_name, amount, note, created_at
                FROM platform_donations
                ORDER BY amount DESC, id ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def _upsert_player_profile(self, user_id: int, display_name: str, avatar_url: str | None = None) -> None:
        with self._get_db() as db:
            db.execute(
                """
                INSERT INTO player_profiles (user_id, display_name, avatar_url)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    avatar_url = COALESCE(excluded.avatar_url, player_profiles.avatar_url),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (int(user_id), display_name, avatar_url),
            )
            db.commit()

    @commands.hybrid_command(name="sync_all_profiles")
    @commands.has_permissions(administrator=True)
    async def sync_all_profiles(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("Server дотор ашиглана.")
            return

        synced = 0
        for member in ctx.guild.members:
            if member.bot:
                continue
            self._upsert_player_profile(
                member.id,
                member.display_name,
                member.display_avatar.url,
            )
            synced += 1

        await ctx.send(f"✅ Synced profiles: **{synced}**")

    def _platform_get_total(self) -> int:
        with self._get_db() as db:
            row = db.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM platform_donations
                """
            ).fetchone()
            return int(row["total"] or 0)

    def _platform_clear_all(self) -> int:
        with self._get_db() as db:
            row = db.execute("SELECT COUNT(*) AS total FROM platform_donations").fetchone()
            count = int(row["total"] or 0)
            db.execute("DELETE FROM platform_donations")
            db.commit()
            return count

    def _tournament_add_sponsor(
        self,
        tournament_id: int,
        sponsor_name: str,
        amount: int,
        created_by: int,
        note: str = "",
        sponsor_user_id: int | None = None,
    ) -> None:
        with self._get_db() as db:
            db.execute(
                """
                INSERT INTO sponsors (
                    tournament_id,
                    sponsor_kind,
                    sponsor_name,
                    sponsor_user_id,
                    amount,
                    note,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tournament_id, "tournament", sponsor_name, sponsor_user_id, amount, note, created_by),
            )
            db.commit()

    def _tournament_get_sponsors(self, tournament_id: int) -> list[dict]:
        with self._get_db() as db:
            rows = db.execute(
                """
                SELECT sponsor_name, amount, note, created_at
                FROM sponsors
                WHERE tournament_id = ?
                  AND COALESCE(sponsor_kind, 'tournament') = 'tournament'
                ORDER BY amount DESC, id ASC
                """,
                (tournament_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _tournament_get_sponsor_total(self, tournament_id: int) -> int:
        with self._get_db() as db:
            row = db.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM sponsors
                WHERE tournament_id = ?
                  AND COALESCE(sponsor_kind, 'tournament') = 'tournament'
                """,
                (tournament_id,),
            ).fetchone()
            return int(row["total"] or 0)

    def _tournament_clear_sponsors(self, tournament_id: int) -> int:
        with self._get_db() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM sponsors
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            ).fetchone()
            count = int(row["total"] or 0)
            db.execute(
                """
                DELETE FROM sponsors
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            )
            db.commit()
            return count

    def _find_text_channel_any(
        self,
        guild: discord.Guild,
        names: list[str],
    ) -> discord.TextChannel | None:
        for name in names:
            channel = discord.utils.get(guild.text_channels, name=name)
            if isinstance(channel, discord.TextChannel):
                return channel

        target_names = {
            normalized
            for normalized in (self._normalize_channel_name(name) for name in names)
            if normalized
        }

        for channel in guild.text_channels:
            normalized = self._normalize_channel_name(channel.name)
            if normalized in target_names:
                return channel

        for channel in guild.text_channels:
            normalized = self._normalize_channel_name(channel.name)
            if any(target in normalized or normalized in target for target in target_names):
                return channel
        return None

    def _resolve_waiting_review_channel(
        self,
        guild: discord.Guild,
        tournament: dict,
    ) -> discord.TextChannel | None:
        preferred = self._find_text_channel_any(
            guild,
            ["waiting-players", "🕰️・waiting-players"],
        )
        if isinstance(preferred, discord.TextChannel):
            return preferred

        waiting_channel = None
        if int(tournament.get("waiting_channel_id") or 0):
            waiting_channel = guild.get_channel(int(tournament["waiting_channel_id"]))
        if isinstance(waiting_channel, discord.TextChannel):
            return waiting_channel

        return self._find_text_channel_any(
            guild,
            ["weekly-status", "🧠・weekly-status"],
        )

    def _resolve_match_results_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        preferred = self._find_text_channel_any(
            guild,
            ["match-results", "📊・match-results"],
        )
        if isinstance(preferred, discord.TextChannel):
            return preferred

        for channel in guild.text_channels:
            normalized = self._normalize_channel_name(channel.name)
            if "matchresults" in normalized or ("match" in normalized and "results" in normalized):
                return channel

        return None

    def _resolve_announcement_channel(
        self,
        guild: discord.Guild,
        target_channel: str,
    ) -> discord.TextChannel | None:
        desired = str(target_channel or "announcements").strip().lower()
        if desired == "general-chat":
            by_id = guild.get_channel(GENERAL_CHAT_CHANNEL_ID)
            if isinstance(by_id, discord.TextChannel):
                return by_id
            preferred = self._find_text_channel_any(
                guild,
                ["general-chat", "💬・general-chat", "💬・ɢᴇɴᴇʀᴀʟ-ᴄʜᴀᴛ"],
            )
            if isinstance(preferred, discord.TextChannel):
                return preferred
            for channel in guild.text_channels:
                normalized = self._normalize_channel_name(channel.name)
                if "generalchat" in normalized or ("general" in normalized and "chat" in normalized):
                    return channel
            return None
        if desired == "weekly-status":
            return self._find_text_channel_any(guild, ["weekly-status", "🧠・weekly-status"])
        if desired == "match-results":
            return self._resolve_match_results_channel(guild)
        if desired == "waiting-players":
            return self._find_text_channel_any(guild, ["waiting-players", "🕰️・waiting-players"])

        preferred = self._find_text_channel_any(
            guild,
            ["announcements", "news", "updates", "📣・announcements"],
        )
        if isinstance(preferred, discord.TextChannel):
            return preferred
        return self._find_text_channel_any(guild, [desired])

    def _list_queued_announcements(self, guild_id: int) -> list[dict]:
        with self._get_db() as db:
            rows = db.execute(
                """
                SELECT
                  a.*,
                  t.guild_id,
                  t.title AS tournament_title,
                  t.season_name
                FROM announcements a
                LEFT JOIN tournaments t
                  ON t.id = a.tournament_id
                WHERE COALESCE(a.guild_id, COALESCE(t.guild_id, 0)) = ?
                  AND (
                    a.status = 'queued'
                    OR (
                      a.status = 'scheduled'
                      AND COALESCE(a.next_publish_at, '') != ''
                      AND a.next_publish_at <= ?
                      AND (a.end_at IS NULL OR a.end_at = '' OR a.next_publish_at <= a.end_at)
                    )
                  )
                ORDER BY a.id ASC
                """,
                (guild_id, self._utc_now_sql()),
            ).fetchall()
            return [dict(row) for row in rows]

    def _mark_announcement_published(self, announcement_id: int, *, channel_id: int, message_id: int, item: dict) -> None:
        with self._get_db() as db:
            next_count = int(item.get("publish_count") or 0) + 1
            repeat_hours = int(item.get("repeat_hours") or 0)
            max_publishes = int(item.get("max_publishes") or 1)
            end_at_raw = str(item.get("end_at") or "").strip()
            next_publish_at = self._future_sql(hours=repeat_hours) if repeat_hours > 0 else None
            within_end_date = not end_at_raw or (next_publish_at is not None and next_publish_at <= end_at_raw)
            should_repeat = repeat_hours > 0 and within_end_date and (max_publishes <= 0 or next_count < max_publishes)
            next_status = "scheduled" if should_repeat else "published"
            next_publish_at = next_publish_at if should_repeat else None
            db.execute(
                """
                UPDATE announcements
                SET status = ?,
                    publish_count = ?,
                    next_publish_at = ?,
                    published_channel_id = ?,
                    published_message_id = ?,
                    published_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (next_status, next_count, next_publish_at, channel_id, message_id, announcement_id),
            )
            db.commit()

    def _list_queued_tournament_admin_actions(self, guild_id: int) -> list[dict]:
        with self._get_db() as db:
            rows = db.execute(
                """
                SELECT *
                FROM tournament_admin_actions
                WHERE guild_id = ?
                  AND status = 'queued'
                ORDER BY id ASC
                """,
                (guild_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def _mark_tournament_admin_action_status(
        self,
        action_id: int,
        status: str,
        *,
        error_text: str = "",
    ) -> None:
        with self._get_db() as db:
            db.execute(
                """
                UPDATE tournament_admin_actions
                SET status = ?,
                    error_text = ?,
                    processed_at = CASE WHEN ? IN ('done', 'failed') THEN CURRENT_TIMESTAMP ELSE processed_at END
                WHERE id = ?
                """,
                (status, error_text, status, action_id),
            )
            db.commit()

    async def _publish_stage_group(
        self,
        guild: discord.Guild,
        *,
        tournament: dict,
        title: str,
        description: str,
        groups: list[dict],
        color: discord.Color,
        stage_color: discord.Color,
    ) -> None:
        header = discord.Embed(
            title=title,
            description=description,
            color=color,
        )
        branded_header = self._apply_weekly_branding(header)
        match_results_channel = self._resolve_match_results_channel(guild)
        if isinstance(match_results_channel, discord.TextChannel):
            await match_results_channel.send(embed=branded_header)

        for item in groups:
            stage = item["stage"]
            embed = self._build_stage_embed(
                stage_title=stage["stage_key"].replace("_", " ").upper(),
                host_user_id=int(item["host_user_id"]),
                password=str(item["password"]),
                slots=item["slots"],
                color=stage_color,
            )
            if isinstance(match_results_channel, discord.TextChannel):
                await match_results_channel.send(embed=embed)
            await self._send_stage_assignment_dms(
                guild,
                stage_title=stage["stage_key"].replace("_", " ").upper(),
                host_user_id=int(item["host_user_id"]),
                password=str(item["password"]),
                slots=item["slots"],
                color=stage_color,
            )

    async def _send_stage_assignment_dms(
        self,
        guild: discord.Guild,
        *,
        stage_title: str,
        host_user_id: int,
        password: str,
        slots: list[dict],
        color: discord.Color,
    ) -> None:
        embed = self._build_stage_embed(
            stage_title=stage_title,
            host_user_id=host_user_id,
            password=password,
            slots=slots,
            color=color,
        )
        for slot in slots:
            member = guild.get_member(int(slot["user_id"]))
            if member is None:
                continue
            try:
                await member.send(embed=embed)
            except discord.HTTPException:
                continue

    async def _process_tournament_admin_action(self, guild: discord.Guild, item: dict) -> None:
        action = str(item.get("action") or "").strip().lower()
        tournament_id = int(item["tournament_id"])
        if action == "generate_zones":
            result = await self.bracket_service.create_weekly_zones_for_tournament(tournament_id)
            tournament = result["tournament"]
            zones = result["zones"]
            await self._publish_stage_group(
                guild,
                tournament=tournament,
                title=f"{tournament['title']} - Zone Draw Complete",
                description=(
                    f"Status: **{tournament['status']}**\n"
                    f"Total Zones: **{len(zones)}**\n"
                    f"Format: **8 players x 4 zones / BO2**"
                ),
                groups=zones,
                color=discord.Color.orange(),
                stage_color=discord.Color.blurple(),
            )
            return

        if action == "publish_registration_ui":
            await self._publish_registration_ui_for_tournament(guild, tournament_id)
            return

        if action == "refresh_registration_ui":
            await self.role_sync_service.sync_registration_roles_for_tournament(
                guild,
                int(tournament_id),
            )
            await self._refresh_registration_ui(guild, tournament_id)
            return

        if action == "queue_registration_announcement":
            tournament = await self.registration_service.tournament_repo.get_by_id(int(tournament_id))
            if tournament is None:
                raise ValueError("Tournament олдсонгүй.")
            self._queue_weekly_registration_announcement(tournament)
            return

        raise ValueError(f"Unsupported admin action: {action}")

    async def _publish_registration_ui_for_tournament(self, guild: discord.Guild, tournament_id: int) -> None:
        register_channel = self._find_text_channel_any(guild, ["✅・weekly-register", "weekly-register"])
        waiting_channel = self._find_text_channel_any(
            guild,
            ["🕰️・waiting-players", "🧠・weekly-status", "waiting-players", "weekly-status"],
        )
        confirmed_channel = self._find_text_channel_any(guild, ["✅・confirmed-players", "confirmed-players"])

        if not isinstance(register_channel, discord.TextChannel):
            raise ValueError("`weekly-register` channel олдсонгүй.")

        tournament = await self.registration_service.tournament_repo.get_by_id(int(tournament_id))
        if tournament is None:
            raise ValueError("Tournament олдсонгүй.")
        snapshot = await self.registration_service.get_snapshot_by_tournament_id(int(tournament["id"]))

        register_msg = await register_channel.send(
            embed=self._build_register_embed(tournament, snapshot),
            view=WeeklyRegisterView(self),
        )
        waiting_summary_msg = None
        confirmed_summary_msg = None

        if isinstance(waiting_channel, discord.TextChannel):
            waiting_summary_msg = await waiting_channel.send(
                embed=self._build_waiting_summary_embed(tournament, snapshot),
            )
        if isinstance(confirmed_channel, discord.TextChannel):
            confirmed_summary_msg = await confirmed_channel.send(
                embed=self._build_confirmed_summary_embed(tournament, snapshot),
            )

        await self.registration_service.tournament_repo.update_registration_ui_state(
            tournament_id=int(tournament["id"]),
            register_channel_id=int(register_channel.id),
            register_message_id=int(register_msg.id),
            waiting_channel_id=int(waiting_channel.id) if isinstance(waiting_channel, discord.TextChannel) else 0,
            waiting_summary_message_id=int(waiting_summary_msg.id) if waiting_summary_msg is not None else 0,
            confirmed_channel_id=int(confirmed_channel.id) if isinstance(confirmed_channel, discord.TextChannel) else 0,
            confirmed_summary_message_id=int(confirmed_summary_msg.id) if confirmed_summary_msg is not None else 0,
        )

    def _queue_weekly_registration_announcement(self, tournament: dict) -> None:
        tournament_id = int(tournament["id"])
        site_url = f"{WEEKLY_SITE_URL.rstrip('/')}/history/{tournament_id}" if WEEKLY_SITE_URL.strip() else ""
        season_name = str(tournament.get("season_name") or "").strip()
        entry_fee = int(tournament.get("entry_fee") or 0)
        body_lines = [
            "Weekly Auto Chess Cup-ийн шинэ season бүртгэл эхэллээ.",
            "",
            f"Season: {season_name or '-'}",
            f"Бүртгэлийн хураамж: {entry_fee:,}₮",
            "Confirmed 32 тоглогч бүрдмэгц zone хуваарилалт автоматаар эхэлнэ.",
            "",
            "ChessOfMongolia.Site дээрээс бүртгэл, төлбөр, confirmed status-аа шууд хянах боломжтой.",
        ]
        with self._get_db() as db:
            db.execute(
                """
                INSERT INTO announcements (
                    guild_id,
                    tournament_id,
                    announcement_type,
                    title,
                    body,
                    badge,
                    button_text,
                    button_url,
                    target_channel,
                    status,
                    repeat_hours,
                    publish_count,
                    max_publishes,
                    next_publish_at
                )
                VALUES (?, ?, 'tournament', ?, ?, ?, ?, ?, 'general-chat', 'queued', 12, 0, 2, ?)
                """,
                (
                    int(tournament["guild_id"]),
                    tournament_id,
                    "Бүртгэл нээлттэй",
                    "\n".join(body_lines),
                    "Registration Open",
                    "Бүртгүүлэх",
                    site_url,
                    self._utc_now_sql(),
                ),
            )
            db.commit()

    def _build_announcement_embed(self, item: dict) -> discord.Embed:
        announcement_type = str(item.get("announcement_type") or "tournament").strip().lower()
        badge = str(item.get("badge") or "Announcement").strip()
        title = str(item.get("title") or "Announcement").strip()
        body = str(item.get("body") or "").strip()
        tournament_title = str(item.get("tournament_title") or "Chess Of Mongolia").strip()
        season_name = str(item.get("season_name") or "").strip()
        embed_title = title if announcement_type == "sponsor" else f"{tournament_title} - {title}"

        embed = discord.Embed(
            title=embed_title,
            description=body or "Шинэ announcement бэлэн байна.",
            color=discord.Color.gold() if announcement_type == "sponsor" else discord.Color.blurple(),
        )
        embed.add_field(
            name="Type",
            value="Sponsor Update" if announcement_type == "sponsor" else "Tournament Update",
            inline=True,
        )
        embed.add_field(name="Badge", value=badge, inline=True)
        if season_name and announcement_type != "sponsor":
            embed.add_field(name="Season", value=season_name, inline=True)
        elif announcement_type == "sponsor":
            embed.add_field(name="Scope", value="Platform / Partner", inline=True)
        button_url = str(item.get("button_url") or "").strip()
        button_text = str(item.get("button_text") or "").strip()
        if button_url:
            embed.add_field(
                name="Action",
                value=f"[{button_text or 'Open'}]({button_url})",
                inline=False,
            )
        image_url = str(item.get("image_url") or "").strip()
        if image_url:
            embed.set_image(url=image_url)
        return self._apply_weekly_branding(embed)

    def _normalize_channel_name(self, value: str) -> str:
        if not value:
            return ""
        translated = value
        for source, target in CHANNEL_NAME_REPLACEMENTS:
            translated = translated.replace(source, target)
        translated = translated.casefold()
        normalized = unicodedata.normalize("NFKD", translated)
        return "".join(ch for ch in normalized if ch.isalnum())

    def _apply_weekly_branding(self, embed: discord.Embed, *, with_poster: bool = False) -> discord.Embed:
        if WEEKLY_THUMB_URL.strip():
            embed.set_thumbnail(url=WEEKLY_THUMB_URL.strip())
        if with_poster and WEEKLY_POSTER_URL.strip():
            embed.set_image(url=WEEKLY_POSTER_URL.strip())
        if WEEKLY_FOOTER_TEXT.strip():
            embed.set_footer(text=WEEKLY_FOOTER_TEXT.strip())
        if WEEKLY_SITE_URL.strip() and not any(field.name == "Site" for field in embed.fields):
            embed.add_field(
                name="Site",
                value=f"[ChessOfMongolia.Site]({WEEKLY_SITE_URL.strip()})",
                inline=False,
            )
        return embed

    def _build_stage_embed(
        self,
        stage_title: str,
        host_user_id: int,
        password: str,
        slots: list[dict],
        color: discord.Color,
    ) -> discord.Embed:
        player_lines = [f"{slot['slot_no']}. <@{slot['user_id']}>" for slot in slots]

        embed = discord.Embed(
            title=stage_title,
            color=color,
        )
        embed.add_field(name="Host", value=f"<@{host_user_id}>", inline=True)
        embed.add_field(name="Password", value=f"`{password}`", inline=True)
        embed.add_field(name="Games", value="BO2", inline=True)
        embed.add_field(
            name="Players",
            value="\n".join(player_lines) if player_lines else "-",
            inline=False,
        )
        return self._apply_weekly_branding(embed)

    def _build_prize_embed(self, tournament: dict) -> discord.Embed:
        embed = discord.Embed(
            title=f"{tournament['title']} - Prize Pool",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Total Prize Pool", value=f"**{tournament['prize_total']:,}₮**", inline=False)
        embed.add_field(name="🥇 1-р байр", value=f"{tournament['prize_1']:,}₮", inline=True)
        embed.add_field(name="🥈 2-р байр", value=f"{tournament['prize_2']:,}₮", inline=True)
        embed.add_field(name="🥉 3-р байр", value=f"{tournament['prize_3']:,}₮", inline=True)
        return self._apply_weekly_branding(embed)

    def _build_rules_embed(self, tournament: dict) -> discord.Embed:
        rules_text = (tournament.get("rules_text") or "").strip()
        if not rules_text:
            rules_text = "Rules хараахан ороогүй байна."

        embed = discord.Embed(
            title=f"{tournament['title']} - Rules",
            description=rules_text[:4096],
            color=discord.Color.blue(),
        )
        return self._apply_weekly_branding(embed)

    def _build_tournament_sponsors_embed(self, tournament: dict, sponsors: list[dict], sponsor_total: int) -> discord.Embed:
        embed = discord.Embed(
            title=f"{tournament['title']} - Tournament Sponsors",
            color=discord.Color.green(),
            description=f"Total Sponsored: **{sponsor_total:,}₮**",
        )

        if not sponsors:
            embed.add_field(name="Sponsors", value="Одоогоор tournament sponsor алга.", inline=False)
            return self._apply_weekly_branding(embed)

        lines = []
        for index, sponsor in enumerate(sponsors[:20], start=1):
            note = f" - {sponsor['note']}" if sponsor["note"] else ""
            lines.append(
                f"**{index}.** {sponsor['sponsor_name']} - **{sponsor['amount']:,}₮**{note}"
            )

        if len(sponsors) > 20:
            lines.append(f"... +{len(sponsors) - 20} more")

        embed.add_field(name="Sponsors", value="\n".join(lines), inline=False)
        return self._apply_weekly_branding(embed)

    def _build_platform_donations_embed(self) -> discord.Embed:
        donors = self._platform_get_donations()
        total = self._platform_get_total()

        embed = discord.Embed(
            title="Platform Supporters",
            color=discord.Color.blue(),
            description=f"Total Platform Support: **{total:,}₮**",
        )

        if not donors:
            embed.add_field(name="Supporters", value="Одоогоор platform donation алга.", inline=False)
            return self._apply_weekly_branding(embed)

        lines = []
        for index, donor in enumerate(donors[:20], start=1):
            note = f" - {donor['note']}" if donor["note"] else ""
            lines.append(
                f"**{index}.** {donor['donor_name']} - **{donor['amount']:,}₮**{note}"
            )

        if len(donors) > 20:
            lines.append(f"... +{len(donors) - 20} more")

        embed.add_field(name="Supporters", value="\n".join(lines), inline=False)
        return self._apply_weekly_branding(embed)

    def _build_scoreboard_embed(
        self,
        title: str,
        result: dict,
        color: discord.Color,
    ) -> discord.Embed:
        scoreboard = result["scoreboard"]

        lines: list[str] = []
        for index, row in enumerate(scoreboard, start=1):
            suffix = ""
            if result["stage_finished"] and result["qualify_count"] > 0:
                suffix = " ✅" if index <= result["qualify_count"] else " ❌"

            lines.append(
                f"**{index}.** <@{row['user_id']}> "
                f"- Total **{row['total_points']}** "
                f"(G1: {row['game1_points']}, G2: {row['game2_points']}){suffix}"
            )

        if not lines:
            lines = ["Одоогоор result алга."]

        embed = discord.Embed(
            title=title,
            color=color,
            description=(
                f"Stage: **{result['stage_key']}**\n"
                f"Games confirmed: **{result['confirmed_games']}/{result['game_count']}**\n"
                f"Finished: **{'Yes' if result['stage_finished'] else 'No'}**"
            ),
        )
        embed.add_field(name="Standings", value="\n".join(lines), inline=False)

        if result["stage_finished"] and result["qualify_count"] > 0:
            qualified = [
                f"<@{row['user_id']}>"
                for i, row in enumerate(scoreboard, start=1)
                if i <= result["qualify_count"]
            ]
            embed.add_field(
                name="Qualified",
                value="\n".join(qualified) if qualified else "-",
                inline=False,
            )

        return self._apply_weekly_branding(embed)

    def _build_final_podium_embed(self, result: dict) -> discord.Embed:
        scoreboard = result["scoreboard"]

        first = scoreboard[0] if len(scoreboard) >= 1 else None
        second = scoreboard[1] if len(scoreboard) >= 2 else None
        third = scoreboard[2] if len(scoreboard) >= 3 else None

        embed = discord.Embed(
            title="🏆 GRAND FINAL RESULT",
            description="Weekly Auto Chess Cup-ийн эцсийн үр дүн.",
            color=discord.Color.gold(),
        )

        if first:
            embed.add_field(
                name="🥇 1-р байр",
                value=(
                    f"<@{first['user_id']}>\n"
                    f"Total: **{first['total_points']}** "
                    f"(G1: {first['game1_points']}, G2: {first['game2_points']})"
                ),
                inline=False,
            )

        if second:
            embed.add_field(
                name="🥈 2-р байр",
                value=(
                    f"<@{second['user_id']}>\n"
                    f"Total: **{second['total_points']}** "
                    f"(G1: {second['game1_points']}, G2: {second['game2_points']})"
                ),
                inline=False,
            )

        if third:
            embed.add_field(
                name="🥉 3-р байр",
                value=(
                    f"<@{third['user_id']}>\n"
                    f"Total: **{third['total_points']}** "
                    f"(G1: {third['game1_points']}, G2: {third['game2_points']})"
                ),
                inline=False,
            )

        top8_lines = []
        for index, row in enumerate(scoreboard[:8], start=1):
            medal = ""
            if index == 1:
                medal = "🥇 "
            elif index == 2:
                medal = "🥈 "
            elif index == 3:
                medal = "🥉 "

            top8_lines.append(
                f"{medal}**{index}.** <@{row['user_id']}> - **{row['total_points']}**"
            )

        if top8_lines:
            embed.add_field(name="Final Standings", value="\n".join(top8_lines), inline=False)

        if first:
            embed.set_footer(text=f"Champion: {first['display_name']}")

        return self._apply_weekly_branding(embed)

    def _build_register_embed(self, tournament: dict, snapshot: dict) -> discord.Embed:
        summary = snapshot["summary"]
        waiting_total = int(summary["registered_count"]) + int(summary["waitlist_count"])

        embed = discord.Embed(
            title=f"📝 {tournament['title']}",
            description=(
                "Доорх button-уудаар weekly cup-д бүртгүүлнэ.\n\n"
                "• Confirmed болсон тоглогчид bracket-д орно\n"
                "• Waiting list-д орсон хүмүүсийг admin confirm хийнэ\n"
                "• Prize pool болон tournament info-г site дээрээс харна"
            ),
            color=discord.Color.gold(),
        )

        embed.add_field(name="📌 Status", value=f"`{tournament['status']}`", inline=True)
        embed.add_field(
            name="✅ Confirmed",
            value=f"**{summary['confirmed_count']} / {tournament['max_players']}**",
            inline=True,
        )
        embed.add_field(
            name="🕒 Waiting",
            value=f"**{waiting_total}**",
            inline=True,
        )

        embed.add_field(
            name="🏆 Prize Pool",
            value=f"**{int(tournament.get('prize_total') or 0):,}₮**",
            inline=True,
        )
        embed.add_field(name="🎮 Format", value="**32 Players • BO2**", inline=True)
        embed.add_field(name="🎯 Entry", value="**Confirmed players only**", inline=True)

        embed.add_field(name="➕ Join", value="`Бүртгүүлэх` button дээр дарна", inline=True)
        embed.add_field(name="➖ Leave", value="`Бүртгэлээс хасах` button дээр дарна", inline=True)
        embed.add_field(
            name="💳 Note",
            value="Admin payment confirm хийсний дараа confirmed болно",
            inline=True,
        )

        return self._apply_weekly_branding(embed, with_poster=True)

    def _build_waiting_summary_embed(self, tournament: dict, snapshot: dict) -> discord.Embed:
        waiting_entries = list(snapshot["registered"]) + list(snapshot["waitlist"])
        summary = snapshot["summary"]
        waiting_total = int(summary["registered_count"]) + int(summary["waitlist_count"])

        embed = discord.Embed(
            title=f"🕒 {tournament['title']} - Waiting Players",
            color=discord.Color.orange(),
            description=(
                f"**Waiting:** {waiting_total}\n"
                f"**Confirmed:** {summary['confirmed_count']} / {tournament['max_players']}\n\n"
                "Waiting players-ийг admin confirm хийнэ."
            ),
        )

        if waiting_entries:
            lines = [f"`#{item['register_order']}` <@{item['user_id']}>" for item in waiting_entries[:20]]
            if len(waiting_entries) > 20:
                lines.append(f"... +{len(waiting_entries) - 20} more")
            embed.add_field(name="🕒 Waiting List", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="🕒 Waiting List", value="Одоогоор waiting player алга.", inline=False)

        return self._apply_weekly_branding(embed)

    def _build_confirmed_summary_embed(self, tournament: dict, snapshot: dict) -> discord.Embed:
        confirmed_entries = snapshot["confirmed"]
        waitlist_entries = snapshot["waitlist"]
        summary = snapshot["summary"]

        embed = discord.Embed(
            title=f"✅ {tournament['title']} - Confirmed Players",
            color=discord.Color.green(),
            description=(
                f"**Confirmed:** {summary['confirmed_count']} / {tournament['max_players']}\n"
                f"**Waitlist:** {summary['waitlist_count']}"
            ),
        )

        if confirmed_entries:
            confirmed_lines = [f"`#{item['register_order']}` <@{item['user_id']}>" for item in confirmed_entries[:32]]
            embed.add_field(name="✅ Confirmed", value="\n".join(confirmed_lines), inline=False)
        else:
            embed.add_field(name="✅ Confirmed", value="Одоогоор confirmed player алга.", inline=False)

        if waitlist_entries:
            waitlist_lines = [f"`#{item['register_order']}` <@{item['user_id']}>" for item in waitlist_entries[:20]]
            embed.add_field(name="🕒 Waitlist", value="\n".join(waitlist_lines), inline=False)

        return self._apply_weekly_branding(embed)

    def _build_waiting_player_embed(
        self,
        tournament: dict,
        entry: dict,
        snapshot: dict | None = None,
    ) -> discord.Embed:
        snapshot = snapshot or {}
        profile = snapshot.get("profile")
        support = snapshot.get("support")
        history = snapshot.get("history") or []

        tournaments_played = int((profile or {}).get("tournaments_played") or 0)
        championships = int((profile or {}).get("championships") or 0)
        podiums = int((profile or {}).get("podiums") or 0)
        total_prize_money = int((profile or {}).get("total_prize_money") or 0)
        phone_number = str((profile or {}).get("phone_number") or "").strip()
        bank_account = str((profile or {}).get("bank_account") or "").strip()
        best_finish = "-"
        if championships > 0:
            best_finish = "Champion"
        elif int((profile or {}).get("runner_ups") or 0) > 0:
            best_finish = "Runner-up"
        elif int((profile or {}).get("third_places") or 0) > 0:
            best_finish = "3rd Place"

        badges: list[str] = []
        if support and support.get("donor_tier"):
            badges.append(str(support["donor_tier"]))
        if support and support.get("sponsor_tier"):
            badges.append(str(support["sponsor_tier"]))
        latest_result = history[0] if history else None
        source = str(entry.get("source") or "discord").lower()
        is_web_request = source == "web"

        embed = discord.Embed(
            title=f"{tournament['title']} - {'Web Registration Review' if is_web_request else 'Payment Review'}",
            color=discord.Color.orange(),
            description=(
                f"**<@{entry['user_id']}>**\n"
                f"Status: **{'PENDING APPROVAL' if is_web_request else 'WAITING PAYMENT'}**\n"
                f"Queue Order: **#{entry['register_order']}**"
            ),
        )
        embed.add_field(
            name="Review Status",
            value=(
                f"Source: **{'Website' if is_web_request else 'Discord'}**\n"
                f"Payment: **{str(entry.get('payment_status') or 'unpaid').title()}**\n"
                f"Entry ID: **{entry['id']}**\n"
                f"Decision: **Confirm / Reject / Remove**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Player Snapshot",
            value=(
                f"Tournaments: **{tournaments_played}**\n"
                f"Best Finish: **{best_finish}**\n"
                f"Podiums: **{podiums}**\n"
                f"Prize Won: **{total_prize_money:,}?**\n"
                f"Phone: **{phone_number or '-'}**\n"
                f"Bank: **{bank_account or '-'}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Badges",
            value=" • ".join(badges) if badges else "Unranked",
            inline=True,
        )
        if latest_result is not None:
            embed.add_field(
                name="Latest Run",
                value=(
                    f"Season: **{latest_result['season_name']}**\n"
                    f"Rank: **#{int(latest_result.get('final_rank') or 0)}**\n"
                    f"Points: **{int(latest_result.get('total_points') or 0)}**"
                ),
                inline=False,
            )
        if support and (support.get("donor_expires_at") or support.get("sponsor_expires_at")):
            support_lines = []
            if support.get("donor_expires_at"):
                support_lines.append(f"Donate expires: **{support['donor_expires_at']}**")
            if support.get("sponsor_expires_at"):
                support_lines.append(f"Sponsor expires: **{support['sponsor_expires_at']}**")
            embed.add_field(name="Support Window", value="\n".join(support_lines), inline=False)
        embed.set_thumbnail(url=WEEKLY_THUMB_URL)
        return self._apply_weekly_branding(embed)

    def _build_processed_review_embed(
        self,
        tournament: dict,
        entry: dict,
        final_state: str,
        color: discord.Color,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"{tournament['title']} - Review Closed",
            color=color,
        )
        embed.add_field(name="Player", value=f"<@{entry['user_id']}>", inline=True)
        embed.add_field(name="Final Status", value=final_state, inline=True)
        embed.add_field(name="Order", value=f"#{entry.get('register_order', '-')}", inline=True)
        embed.add_field(name="Payment", value=str(entry.get("payment_status") or "-").title(), inline=True)
        embed.add_field(name="Entry ID", value=str(entry.get("id", "-")), inline=True)
        embed.add_field(name="Note", value="This review card is locked.", inline=True)
        return self._apply_weekly_branding(embed)

    def _build_stage_players_embed(
        self,
        tournament: dict,
        stage: dict,
        slots: list[dict],
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"{tournament['title']} - {stage['stage_key'].upper()} Players",
            color=discord.Color.blurple(),
            description=(
                f"Stage Type: **{stage['stage_type']}**\n"
                f"Status: **{stage['status']}**\n"
                f"Games: **{stage['game_count']}**"
            ),
        )

        if not slots:
            embed.add_field(name="Players", value="Одоогоор player алга.", inline=False)
            return self._apply_weekly_branding(embed)

        lines = []
        for slot in sorted(slots, key=lambda x: int(x["slot_no"])):
            user_id = int(slot["user_id"])
            total_points = int(slot.get("total_points") or 0)
            register_order = slot.get("register_order")
            register_text = f" | Reg #{register_order}" if register_order else ""

            lines.append(
                f"**{slot['slot_no']}.** <@{user_id}>"
                f" | Total: **{total_points}**{register_text}"
            )

        embed.add_field(name="Players", value="\n".join(lines), inline=False)
        return self._apply_weekly_branding(embed)

    async def _send_existing_weekly_zones(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        zones: list[dict] = []
        for stage_key in ZONE_KEYS:
            stage = await self.stage_repo.get_stage_by_key(int(tournament["id"]), stage_key)
            if stage is None:
                continue

            slots = await self.stage_repo.list_stage_slots_with_entries(int(stage["id"]))
            zones.append(
                {
                    "stage": stage,
                    "slots": slots,
                    "host_user_id": int(stage.get("host_user_id") or 0),
                    "password": str(stage.get("lobby_password") or "-"),
                }
            )

        if not zones:
            await ctx.send("❌ Zone хуваарилалт одоогоор үүсээгүй байна.")
            return

        header = discord.Embed(
            title=f"{tournament['title']} - Current Zone Draw",
            description=(
                f"Status: **{tournament['status']}**\n"
                f"Total Zones: **{len(zones)}**\n"
                f"Format: **8 players x 4 zones / BO2**"
            ),
            color=discord.Color.orange(),
        )
        branded_header = self._apply_weekly_branding(header)
        await ctx.send(embed=branded_header)
        if isinstance(match_results_channel, discord.TextChannel):
            await match_results_channel.send(embed=branded_header)

        for zone in zones:
            stage = zone["stage"]
            embed = self._build_stage_embed(
                stage_title=stage["stage_key"].replace("_", " ").upper(),
                host_user_id=zone["host_user_id"],
                password=zone["password"],
                slots=zone["slots"],
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)
            if isinstance(match_results_channel, discord.TextChannel):
                await match_results_channel.send(embed=embed)

    async def _send_existing_stage_group(
        self,
        ctx: commands.Context,
        *,
        stage_keys: list[str],
        title_suffix: str,
        empty_message: str,
        total_label: str,
        format_text: str,
        header_color: discord.Color,
        stage_color: discord.Color,
    ) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        items: list[dict] = []
        for stage_key in stage_keys:
            stage = await self.stage_repo.get_stage_by_key(int(tournament["id"]), stage_key)
            if stage is None:
                continue

            slots = await self.stage_repo.list_stage_slots_with_entries(int(stage["id"]))
            items.append(
                {
                    "stage": stage,
                    "slots": slots,
                    "host_user_id": int(stage.get("host_user_id") or 0),
                    "password": str(stage.get("lobby_password") or "-"),
                }
            )

        if not items:
            await ctx.send(empty_message)
            return

        header = discord.Embed(
            title=f"{tournament['title']} - {title_suffix}",
            description=(
                f"Status: **{tournament['status']}**\n"
                f"{total_label}: **{len(items)}**\n"
                f"Format: **{format_text}**"
            ),
            color=header_color,
        )
        await ctx.send(embed=self._apply_weekly_branding(header))

        for item in items:
            stage = item["stage"]
            embed = self._build_stage_embed(
                stage_title=stage["stage_key"].replace("_", " ").upper(),
                host_user_id=item["host_user_id"],
                password=item["password"],
                slots=item["slots"],
                color=stage_color,
            )
            await ctx.send(embed=embed)

    async def _assign_weekly_champion_role(
        self,
        guild: discord.Guild,
        winner_user_id: int,
    ) -> str | None:
        role = discord.utils.get(guild.roles, name=WEEKLY_CHAMPION_ROLE_NAME)
        if role is None:
            return f"❌ `{WEEKLY_CHAMPION_ROLE_NAME}` role олдсонгүй."

        for member in list(role.members):
            try:
                await member.remove_roles(role, reason="New weekly champion selected")
            except discord.HTTPException:
                pass

        winner_member = guild.get_member(int(winner_user_id))
        if winner_member is None:
            return "❌ Winner member guild дотор олдсонгүй."

        try:
            await winner_member.add_roles(role, reason="Weekly champion")
            return f"🏆 {winner_member.mention} -> `{WEEKLY_CHAMPION_ROLE_NAME}` role өглөө."
        except discord.HTTPException:
            return "❌ Champion role өгөх үед алдаа гарлаа."

    async def _fetch_message_safe(
        self,
        channel: discord.TextChannel | None,
        message_id: int,
    ) -> discord.Message | None:
        if channel is None or not message_id:
            return None
        try:
            return await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _delete_message_quietly(self, message: discord.Message | None) -> None:
        if message is None:
            return
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    async def _send_registration_dm(
        self,
        user_id: int,
        title: str,
        lines: list[str],
        color: discord.Color,
    ) -> None:
        user: discord.abc.User | None = self.bot.get_user(int(user_id))
        if user is None:
            try:
                user = await self.bot.fetch_user(int(user_id))
            except discord.HTTPException:
                return

        embed = discord.Embed(title=title, color=color, description="\n".join(lines))
        embed.set_footer(text=WEEKLY_FOOTER_TEXT)
        try:
            await user.send(embed=embed)
        except discord.HTTPException as exc:
            print(f"Failed to DM user {user_id}: {exc}")
            return

    async def _post_waiting_review_card(
        self,
        guild: discord.Guild,
        tournament: dict,
        entry: dict,
    ) -> None:
        waiting_channel = self._resolve_waiting_review_channel(guild, tournament)

        if not isinstance(waiting_channel, discord.TextChannel):
            return

        profile = await self.stats_service.get_player_profile(int(entry["user_id"]))
        history = await self.stats_service.get_player_history(int(entry["user_id"]), limit=3)
        support = await self.stats_service.get_player_support_status(
            int(entry["user_id"]),
            guild_id=int(guild.id),
        )

        review_msg = await waiting_channel.send(
            embed=self._build_waiting_player_embed(
                tournament,
                entry,
                snapshot={
                    "profile": profile,
                    "history": history,
                    "support": support,
                },
            ),
            view=WaitingReviewView(self),
        )
        await self.registration_service.entry_repo.update_review_message_id(
            int(entry["id"]),
            int(review_msg.id),
        )
        await self._send_registration_dm(
            user_id=int(entry["user_id"]),
            title="Tournament Registration Request",
            lines=[
                f"Tournament: **{tournament['title']}**",
                f"Status: **Pending Review**",
                "Таны хүсэлт Discord дээр admin review руу илгээгдлээ.",
            ],
            color=discord.Color.orange(),
        )

    async def _mark_review_message_processed(
        self,
        guild: discord.Guild,
        tournament: dict,
        entry: dict,
        final_state: str,
        color: discord.Color,
        target_message: discord.Message | None = None,
    ) -> None:
        message = target_message
        if message is None:
            waiting_channel = self._resolve_waiting_review_channel(guild, tournament)

            review_message_id = int(entry.get("review_message_id") or 0)
            message = await self._fetch_message_safe(waiting_channel, review_message_id)

        if message is None:
            return

        await message.edit(
            embed=self._build_processed_review_embed(tournament, entry, final_state, color),
            view=WaitingReviewView(self, disabled=True),
        )

    async def _refresh_registration_ui(
        self,
        guild: discord.Guild,
        tournament_id: int,
    ) -> None:
        tournament = await self.registration_service.tournament_repo.get_by_id(int(tournament_id))
        if tournament is None:
            return

        snapshot = await self.registration_service.get_snapshot_by_tournament_id(int(tournament_id))

        register_channel = guild.get_channel(int(tournament.get("register_channel_id") or 0))
        waiting_channel = guild.get_channel(int(tournament.get("waiting_channel_id") or 0))
        confirmed_channel = guild.get_channel(int(tournament.get("confirmed_channel_id") or 0))

        if not isinstance(register_channel, discord.TextChannel):
            register_channel = self._find_text_channel_any(guild, ["weekly-register", "✅・weekly-register"])
        if not isinstance(waiting_channel, discord.TextChannel):
            waiting_channel = self._find_text_channel_any(
                guild,
                ["🕰️・waiting-players", "🧠・weekly-status", "waiting-players", "weekly-status"],
            )
        if not isinstance(confirmed_channel, discord.TextChannel):
            confirmed_channel = self._find_text_channel_any(guild, ["✅・confirmed-players", "confirmed-players"])

        register_msg = await self._fetch_message_safe(
            register_channel,
            int(tournament.get("register_message_id") or 0),
        )
        waiting_summary_msg = await self._fetch_message_safe(
            waiting_channel,
            int(tournament.get("waiting_summary_message_id") or 0),
        )
        confirmed_summary_msg = await self._fetch_message_safe(
            confirmed_channel,
            int(tournament.get("confirmed_summary_message_id") or 0),
        )

        register_disabled = tournament["status"] != "registration_open"

        if register_msg is not None:
            await register_msg.edit(
                embed=self._build_register_embed(tournament, snapshot),
                view=WeeklyRegisterView(self, disabled=register_disabled),
            )

        if waiting_summary_msg is not None:
            await waiting_summary_msg.edit(
                embed=self._build_waiting_summary_embed(tournament, snapshot),
            )

        if confirmed_summary_msg is not None:
            await confirmed_summary_msg.edit(
                embed=self._build_confirmed_summary_embed(tournament, snapshot),
            )

    async def _submit_and_send_stage_result_from_mentions(
        self,
        ctx: commands.Context,
        stage_key: str,
        explicit_game_no: int | None,
        placements: str = "",
    ) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        ordered_user_ids = self._extract_ordered_user_ids(ctx, placements)

        if len(ordered_user_ids) != 8:
            await ctx.send(
                f"❌ Яг 8 тоглогч оруул. Slash дээр `<@id>` эсвэл user id-уудыг дарааллаар нь оруулна. Одоо {len(ordered_user_ids)} байна."
            )
            return

        missing_members = [user_id for user_id in ordered_user_ids if ctx.guild.get_member(user_id) is None]
        if missing_members:
            missing_text = ", ".join(f"<@{user_id}>" for user_id in missing_members)
            await ctx.send(f"❌ Server дотор олдоогүй player байна: {missing_text}")
            return

        try:
            result = await self.result_service.submit_stage_result(
                guild_id=ctx.guild.id,
                stage_key=stage_key.lower(),
                game_no=explicit_game_no,
                ordered_user_ids=ordered_user_ids,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        embed = self._build_scoreboard_embed(
            title=f"{stage_key.lower()} - Game {result['game_no']} saved",
            result=result,
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

        if result["stage_finished"] and result["stage_type"] == "zone":
            try:
                semi_result = await self.bracket_service.create_weekly_semis_for_tournament(
                    int(result["tournament"]["id"])
                )
            except ValueError:
                semi_result = None
            if semi_result is not None:
                semi_user_ids = [int(slot["user_id"]) for semi in semi_result["semis"] for slot in semi["slots"]]
                await self.role_sync_service.sync_semi_finalists(ctx.guild, semi_user_ids)
                await self._publish_stage_group(
                    ctx.guild,
                    tournament=semi_result["tournament"],
                    title=f"{semi_result['tournament']['title']} - Semifinals Created",
                    description=(
                        f"Status: **{semi_result['tournament']['status']}**\n"
                        f"Total Semis: **{len(semi_result['semis'])}**\n"
                        f"Format: **8 players x 2 semis / BO2**"
                    ),
                    groups=semi_result["semis"],
                    color=discord.Color.dark_gold(),
                    stage_color=discord.Color.dark_teal(),
                )
                await ctx.send("✅ Zone result бүрэн орлоо. Semifinal автоматаар үүслээ.")

        if result["stage_finished"] and result["stage_type"] == "semi":
            try:
                final_result = await self.bracket_service.create_weekly_final_for_tournament(
                    int(result["tournament"]["id"])
                )
            except ValueError:
                final_result = None
            if final_result is not None:
                final_user_ids = [int(slot["user_id"]) for slot in final_result["final"]["slots"]]
                await self.role_sync_service.sync_grand_finalists(ctx.guild, final_user_ids)
                await self._publish_stage_group(
                    ctx.guild,
                    tournament=final_result["tournament"],
                    title=f"{final_result['tournament']['title']} - Grand Final Created",
                    description=(
                        f"Status: **{final_result['tournament']['status']}**\n"
                        f"Format: **Final 8 / BO2**"
                    ),
                    groups=[final_result["final"]],
                    color=discord.Color.red(),
                    stage_color=discord.Color.red(),
                )
                await ctx.send("✅ Semifinal result бүрэн орлоо. Grand Final автоматаар үүслээ.")

        if result["stage_type"] == "final" and result["stage_finished"]:
            podium_embed = self._build_final_podium_embed(result)
            await ctx.send(embed=podium_embed)

            if result["scoreboard"]:
                role_message = await self.role_sync_service.assign_season_champion_badge(
                    guild=ctx.guild,
                    winner_user_id=int(result["scoreboard"][0]["user_id"]),
                    season_name=result["tournament"].get("season_name"),
                )
                if role_message:
                    await ctx.send(role_message)
                podium_messages = await self.role_sync_service.assign_season_podium_badges(
                    guild=ctx.guild,
                    scoreboard=result["scoreboard"],
                    season_name=result["tournament"].get("season_name"),
                )
                for message in podium_messages:
                    await ctx.send(message)

    @commands.hybrid_command(name="ping")
    @commands.has_permissions(administrator=True)
    async def ping(self, ctx: commands.Context) -> None:
        await ctx.send("pong")

    @commands.hybrid_command(name="set_entry_fee")
    @commands.has_permissions(administrator=True)
    async def set_entry_fee(self, ctx: commands.Context, amount: int) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        if amount < 0:
            await ctx.send("❌ Entry fee 0 эсвэл 0-ээс их байх ёстой.")
            return

        self._update_tournament_field(int(tournament["id"]), "entry_fee", amount)
        await ctx.send(f"✅ Entry fee шинэчлэгдлээ: **{amount:,}₮**")

    @commands.hybrid_command(name="set_start_time")
    @commands.has_permissions(administrator=True)
    async def set_start_time(self, ctx: commands.Context, *, value: str) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        self._update_tournament_field(int(tournament["id"]), "start_time", value.strip())
        await ctx.send(f"✅ Эхлэх цаг шинэчлэгдлээ: **{value.strip()}**")

    @commands.hybrid_command(name="set_checkin_time")
    @commands.has_permissions(administrator=True)
    async def set_checkin_time(self, ctx: commands.Context, *, value: str) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        self._update_tournament_field(int(tournament["id"]), "checkin_time", value.strip())
        await ctx.send(f"✅ Check-in цаг шинэчлэгдлээ: **{value.strip()}**")

    @commands.hybrid_command(name="weekly_create")
    @commands.has_permissions(administrator=True)
    async def weekly_create(self, ctx: commands.Context, *, title: str = "Weekly Auto Chess Cup") -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        register_channel = self._find_text_channel_any(ctx.guild, ["✅・weekly-register", "weekly-register"])
        waiting_channel = self._find_text_channel_any(
            ctx.guild,
            ["🕰️・waiting-players", "🧠・weekly-status", "waiting-players", "weekly-status"],
        )
        confirmed_channel = self._find_text_channel_any(ctx.guild, ["✅・confirmed-players", "confirmed-players"])

        if not isinstance(register_channel, discord.TextChannel):
            await ctx.send("❌ `weekly-register` channel олдсонгүй.")
            return
        if not isinstance(waiting_channel, discord.TextChannel):
            await ctx.send("❌ `waiting-players` эсвэл `weekly-status` channel олдсонгүй.")
            return
        if not isinstance(confirmed_channel, discord.TextChannel):
            await ctx.send("❌ `confirmed-players` channel олдсонгүй.")
            return

        try:
            tournament = await self.registration_service.create_weekly_tournament(
                guild_id=ctx.guild.id,
                created_by=ctx.author.id,
                title=title,
                entry_fee=0,
            )
        except ValueError as e:
            message = str(e)
            lowered = message.lower()
            if "active" in lowered and "weekly" in lowered:
                message = "Идэвхтэй weekly tournament үүсгэхэд алдаа гарлаа."
            await ctx.send(f"❌ {message}")
            return

        snapshot = await self.registration_service.get_snapshot_by_tournament_id(int(tournament["id"]))

        register_msg = await register_channel.send(
            embed=self._build_register_embed(tournament, snapshot),
            view=WeeklyRegisterView(self),
        )
        waiting_summary_msg = await waiting_channel.send(
            embed=self._build_waiting_summary_embed(tournament, snapshot),
        )
        confirmed_summary_msg = await confirmed_channel.send(
            embed=self._build_confirmed_summary_embed(tournament, snapshot),
        )

        await self.registration_service.tournament_repo.update_registration_ui_state(
            tournament_id=int(tournament["id"]),
            register_channel_id=int(register_channel.id),
            register_message_id=int(register_msg.id),
            waiting_channel_id=int(waiting_channel.id),
            waiting_summary_message_id=int(waiting_summary_msg.id),
            confirmed_channel_id=int(confirmed_channel.id),
            confirmed_summary_message_id=int(confirmed_summary_msg.id),
        )
        self._queue_weekly_registration_announcement(tournament)

        await ctx.send(
            f"✅ `{tournament['title']}` registration UI үүслээ.\n"
            f"Register: {register_channel.mention}\n"
            f"Waiting: {waiting_channel.mention}\n"
            f"Confirmed: {confirmed_channel.mention}"
        )

    @commands.hybrid_command(name="add")
    @commands.has_permissions(manage_messages=True)
    async def add_player(self, ctx: commands.Context, *, target: str) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        ranked_queue = self._get_latest_active_ranked_queue_for_channel(ctx.guild.id, ctx.channel.id)
        if ranked_queue is not None:
            if not self._has_ranked_mod_access(ctx):
                await ctx.send("❌ Ranked queue-г зөвхөн moderator удирдана.")
                return

            member = self._resolve_member_from_target(ctx, (target or "").strip())
            if member is None:
                await ctx.send("❌ Confirm хийх member mention хийнэ үү.")
                return

            try:
                fresh_queue, fresh_entry = self._ranked_confirm_member(int(ranked_queue["id"]), member)
            except ValueError as e:
                await ctx.send(f"❌ {e}")
                return

            await self._refresh_ranked_queue_message(ctx.guild, int(fresh_queue["id"]))
            await ctx.send(
                f"✅ {member.mention} confirmed боллоо.\n"
                f"Queue: **{self._ranked_status_label(str(fresh_queue['status']))}**\n"
                f"Confirm Order: **{int(fresh_entry['confirm_order'] or 0)}**"
            )
            return

        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Tournament `.add` командыг зөвхөн admin ашиглана.")
            return

        raw = (target or "").strip()
        if raw.isdigit():
            count = int(raw)
            if count <= 0:
                await ctx.send("❌ Count 0-ээс их байх ёстой.")
                return

            snapshot = await self.registration_service.get_weekly_snapshot(ctx.guild.id)
            existing_ids = {
                int(entry["user_id"])
                for group in ("registered", "confirmed", "waitlist")
                for entry in snapshot.get(group, [])
            }
            eligible_members = [member for member in ctx.guild.members if not member.bot and member.id not in existing_ids]
            random.shuffle(eligible_members)

            if len(eligible_members) < count:
                await ctx.send(
                    f"❌ Discord server дээр хангалттай player алга. Requested: **{count}**, Available: **{len(eligible_members)}**"
                )
                return

            added = 0
            waitlisted = 0
            tournament = None
            summary = None

            for member in eligible_members[:count]:
                try:
                    result = await self.registration_service.admin_add_confirmed_user(
                        guild_id=ctx.guild.id,
                        user_id=member.id,
                        display_name=member.display_name,
                    )
                except ValueError:
                    continue

                entry = result["entry"]
                tournament = result["tournament"]
                summary = result["summary"]
                added += 1
                if entry["status"] == "confirmed":
                    await self.role_sync_service.extend_confirmed_role_expiry(
                        int(ctx.guild.id),
                        int(entry["user_id"]),
                    )
                if entry["status"] != "confirmed":
                    waitlisted += 1

            if tournament is None or summary is None:
                await ctx.send("❌ Test add хийхэд алдаа гарлаа.")
                return

            await self.role_sync_service.sync_registration_roles_for_tournament(
                ctx.guild,
                int(tournament["id"]),
            )
            await self._refresh_registration_ui(ctx.guild, int(tournament["id"]))

            await ctx.send(
                f"✅ Random force add completed: **{added}** хүн.\n"
                f"Confirmed: **{summary['confirmed_count']}/32**\n"
                f"Registered: **{summary['registered_count']}**\n"
                f"Waitlist: **{summary['waitlist_count']}**\n"
                f"Waitlisted from batch: **{waitlisted}**"
            )
            return

        member = self._resolve_member_from_target(ctx, raw)
        if member is None:
            await ctx.send("❌ Member олдсонгүй. Mention хий эсвэл `.add 32` гэж batch add ашигла.")
            return

        try:
            result = await self.registration_service.admin_add_confirmed_user(
                guild_id=ctx.guild.id,
                user_id=member.id,
                display_name=member.display_name,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        entry = result["entry"]
        summary = result["summary"]
        tournament = result["tournament"]
        if entry["status"] == "confirmed":
            await self.role_sync_service.extend_confirmed_role_expiry(
                int(ctx.guild.id),
                int(entry["user_id"]),
            )

        await self.role_sync_service.sync_registration_roles_for_tournament(
            ctx.guild,
            int(tournament["id"]),
        )
        await self._refresh_registration_ui(ctx.guild, int(tournament["id"]))

        if entry["status"] == "confirmed":
            await ctx.send(
                f"✅ {member.mention} force confirmed боллоо.\n"
                f"Confirmed: **{summary['confirmed_count']}/32**\n"
                f"Registered: **{summary['registered_count']}**\n"
                f"Waitlist: **{summary['waitlist_count']}**"
            )
        else:
            await ctx.send(
                f"🕒 {member.mention} force add хийгдсэн ч waitlist руу орлоо.\n"
                f"Confirmed: **{summary['confirmed_count']}/32**\n"
                f"Waitlist: **{summary['waitlist_count']}**"
            )

    @commands.hybrid_command(name="pay_confirm")
    @commands.has_permissions(administrator=True)
    async def pay_confirm(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            result = await self.registration_service.confirm_payment_for_user(
                guild_id=ctx.guild.id,
                user_id=member.id,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        entry = result["entry"]
        summary = result["summary"]
        tournament = result["tournament"]

        if entry is not None:
            state_text = "CONFIRMED" if entry["status"] == "confirmed" else "WAITLIST"
            await self._mark_review_message_processed(
                guild=ctx.guild,
                tournament=tournament,
                entry=entry,
                final_state=state_text,
                color=discord.Color.green() if state_text == "CONFIRMED" else discord.Color.orange(),
            )
            if entry["status"] == "confirmed":
                await self.role_sync_service.extend_confirmed_role_expiry(
                    int(ctx.guild.id),
                    int(entry["user_id"]),
                )

        await self.role_sync_service.sync_registration_roles_for_tournament(
            ctx.guild,
            int(tournament["id"]),
        )
        await self._refresh_registration_ui(ctx.guild, int(tournament["id"]))

        if entry["status"] == "confirmed":
            await ctx.send(
                f"✅ {member.mention} албан ёсоор confirmed боллоо. "
                f"Confirmed: {summary['confirmed_count']}/32"
            )
        else:
            await ctx.send(
                f"🕒 {member.mention} waitlist руу орлоо. "
                f"Waitlist: {summary['waitlist_count']}"
            )

    @commands.hybrid_command(name="remove_confirmed")
    @commands.has_permissions(administrator=True)
    async def remove_confirmed(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer()

        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            result = await self.registration_service.admin_remove_user(
                guild_id=ctx.guild.id,
                user_id=member.id,
            )
        except ValueError as e:
            message = str(e)
            if "weekly tournament" in message.lower():
                message = "Тухайн хүн идэвхтэй weekly tournament-д бүртгэлгүй байна."
            elif "Bracket" in message and "replace" in message:
                message = "Bracket үүссэн тул remove хийхгүй. Оронд нь replace ашигла."
            await ctx.send(f"❌ {message}")
            return

        tournament = result["tournament"]
        summary = result["summary"]

        await self.role_sync_service.sync_registration_roles_for_tournament(
            ctx.guild,
            int(tournament["id"]),
        )
        await self._refresh_registration_ui(ctx.guild, int(tournament["id"]))

        await ctx.send(
            f"✅ {member.mention} weekly tournament-оос хасагдлаа.\n"
            f"Registered: **{summary['registered_count']}**\n"
            f"Confirmed: **{summary['confirmed_count']}/32**\n"
            f"Waitlist: **{summary['waitlist_count']}**"
        )

    @commands.hybrid_command(name="unconfirm")
    @commands.has_permissions(administrator=True)
    async def unconfirm(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer()

        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            result = await self.registration_service.admin_revert_confirmed_user(
                guild_id=ctx.guild.id,
                user_id=member.id,
            )
        except ValueError as e:
            message = str(e)
            if "Bracket" in message and "replace" in message:
                message = "Bracket үүссэн тул confirmed-ээс буцаахгүй. Оронд нь replace ашигла."
            await ctx.send(f"❌ {message}")
            return

        tournament = result["tournament"]
        summary = result["summary"]

        await self.role_sync_service.sync_registration_roles_for_tournament(
            ctx.guild,
            int(tournament["id"]),
        )
        await self._refresh_registration_ui(ctx.guild, int(tournament["id"]))

        await ctx.send(
            f"🕒 {member.mention} confirmed-ээс waiting руу буцлаа.\n"
            f"Registered: **{summary['registered_count']}**\n"
            f"Confirmed: **{summary['confirmed_count']}/32**\n"
            f"Waitlist: **{summary['waitlist_count']}**"
        )

    @commands.hybrid_command(name="weekly_status")
    @commands.has_permissions(administrator=True)
    async def weekly_status(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            snapshot = await self.registration_service.get_weekly_snapshot(ctx.guild.id)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        tournament = snapshot["tournament"]
        registered = snapshot["registered"]
        confirmed = snapshot["confirmed"]
        waitlist = snapshot["waitlist"]
        summary = snapshot["summary"]

        embed = discord.Embed(
            title=tournament["title"],
            color=discord.Color.green(),
            description=(
                f"Status: **{tournament['status']}**\n"
                f"Registered: **{summary['registered_count']}**\n"
                f"Confirmed: **{summary['confirmed_count']} / {tournament['max_players']}**\n"
                f"Waitlist: **{summary['waitlist_count']}**"
            ),
        )

        def fmt(entries: list[dict], empty_text: str) -> str:
            if not entries:
                return empty_text
            lines = []
            for item in entries[:20]:
                lines.append(f"{item['register_order']}. <@{item['user_id']}>")
            if len(entries) > 20:
                lines.append(f"... +{len(entries) - 20} more")
            return "\n".join(lines)

        embed.add_field(name="Registered", value=fmt(registered, "No registered players"), inline=False)
        embed.add_field(name="Confirmed", value=fmt(confirmed, "No confirmed players"), inline=False)
        embed.add_field(name="Waitlist", value=fmt(waitlist, "No waitlist"), inline=False)

        await ctx.send(embed=self._apply_weekly_branding(embed))

    @commands.hybrid_command(name="set_prize_total")
    @commands.has_permissions(administrator=True)
    async def set_prize_total(self, ctx: commands.Context, amount: int) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            tournament = await self.tournament_service.set_prize_total(
                guild_id=ctx.guild.id,
                amount=amount,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        await ctx.send(embed=self._build_prize_embed(tournament))

    @commands.hybrid_command(name="set_prize")
    @commands.has_permissions(administrator=True)
    async def set_prize(self, ctx: commands.Context, rank: int, amount: int) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            tournament = await self.tournament_service.set_prize_rank(
                guild_id=ctx.guild.id,
                rank=rank,
                amount=amount,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        await ctx.send(embed=self._build_prize_embed(tournament))

    @commands.hybrid_command(name="prize_show")
    @commands.has_permissions(administrator=True)
    async def prize_show(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            tournament = await self.tournament_service.get_prize_snapshot(ctx.guild.id)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        await ctx.send(embed=self._build_prize_embed(tournament))

    @commands.hybrid_command(name="set_rules")
    @commands.has_permissions(administrator=True)
    async def set_rules(self, ctx: commands.Context, *, rules_text: str) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            tournament = await self.tournament_service.set_rules(
                guild_id=ctx.guild.id,
                rules_text=rules_text,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        await ctx.send(embed=self._build_rules_embed(tournament))

    @commands.hybrid_command(name="rules_show")
    @commands.has_permissions(administrator=True)
    async def rules_show(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            tournament = await self.tournament_service.get_rules(ctx.guild.id)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        await ctx.send(embed=self._build_rules_embed(tournament))

    @commands.hybrid_command(name="donate_add")
    @commands.has_permissions(administrator=True)
    async def donate_add(
        self,
        ctx: commands.Context,
        donor_name: str = "",
        amount: int = 0,
        member: discord.Member | None = None,
        note: str = "",
    ) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        if amount <= 0:
            await ctx.send("❌ Amount 0-ээс их байх ёстой.")
            return

        donor_name = (donor_name or "").strip()
        donor_name, donor_user_id = self._extract_support_target(ctx, donor_name, member)
        if not donor_name:
            await ctx.send("❌ Donor name хэрэгтэй. Жишээ: `/donate_add donor_name:Ideree amount:500000 note:bayrllaa`")
            return

        self._platform_add_donation(donor_name, amount, note, donor_user_id=donor_user_id)
        role_message = ""
        if donor_user_id is not None:
            tier = await self.supporter_service.apply_donation_support(ctx.guild.id, donor_user_id, amount)
            member = ctx.guild.get_member(donor_user_id)
            if member is not None:
                self._upsert_player_profile(member.id, member.display_name, member.display_avatar.url)
                await self.supporter_service.sync_member_roles(ctx.guild, member)
            if tier is not None:
                role_message = f"\nSupport Role: **{tier.role_name}** until **{tier.expires_at}**"
        await ctx.send(
            f"✅ Donate added: **{donor_name}** - **{amount:,}₮**{role_message}",
            embed=self._build_platform_donations_embed(),
        )

    @commands.hybrid_command(name="donate_list")
    async def donate_list(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        await ctx.send(embed=self._build_platform_donations_embed())

    @commands.hybrid_command(name="donate_clear")
    @commands.has_permissions(administrator=True)
    async def donate_clear(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        count = self._platform_clear_all()
        await ctx.send(f"✅ Platform donations cleared. Removed: **{count}**")

    @commands.hybrid_command(name="sponsor_add")
    @commands.has_permissions(administrator=True)
    async def sponsor_add(
        self,
        ctx: commands.Context,
        sponsor_name: str = "",
        amount: int = 0,
        member: discord.Member | None = None,
        note: str = "",
    ) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        if amount <= 0:
            await ctx.send("❌ Amount 0-ээс их байх ёстой.")
            return

        sponsor_name = (sponsor_name or "").strip()
        sponsor_name, sponsor_user_id = self._extract_support_target(ctx, sponsor_name, member)
        if not sponsor_name:
            await ctx.send("❌ Sponsor name хэрэгтэй. Жишээ: `/sponsor_add sponsor_name:Stake amount:300000 note:main sponsor`")
            return

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        self._tournament_add_sponsor(
            tournament_id=int(tournament["id"]),
            sponsor_name=sponsor_name,
            amount=amount,
            created_by=ctx.author.id,
            note=note,
            sponsor_user_id=sponsor_user_id,
        )

        role_message = ""
        if sponsor_user_id is not None:
            tier = await self.supporter_service.apply_sponsor_support(ctx.guild.id, sponsor_user_id, amount)
            member = ctx.guild.get_member(sponsor_user_id)
            if member is not None:
                self._upsert_player_profile(member.id, member.display_name, member.display_avatar.url)
                await self.supporter_service.sync_member_roles(ctx.guild, member)
            if tier is not None:
                role_message = f"\nSupport Role: **{tier.role_name}** until **{tier.expires_at}**"

        sponsors = self._tournament_get_sponsors(int(tournament["id"]))
        sponsor_total = self._tournament_get_sponsor_total(int(tournament["id"]))

        await ctx.send(
            f"✅ Sponsor added: **{sponsor_name}** - **{amount:,}₮**{role_message}",
            embed=self._build_tournament_sponsors_embed(
                tournament,
                sponsors,
                sponsor_total,
            )
        )

    @commands.hybrid_command(name="sponsor_list")
    async def sponsor_list(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        sponsors = self._tournament_get_sponsors(int(tournament["id"]))
        sponsor_total = self._tournament_get_sponsor_total(int(tournament["id"]))

        await ctx.send(
            embed=self._build_tournament_sponsors_embed(
                tournament,
                sponsors,
                sponsor_total,
            )
        )

    @commands.hybrid_command(name="sponsor_clear")
    @commands.has_permissions(administrator=True)
    async def sponsor_clear(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        count = self._tournament_clear_sponsors(int(tournament["id"]))
        await ctx.send(f"✅ `{tournament['title']}` tournament sponsors cleared. Removed: **{count}**")

    @commands.hybrid_command(name="set_result")
    @commands.has_permissions(administrator=True)
    async def set_result(
        self,
        ctx: commands.Context,
        stage_key: str,
        *,
        placements: str = "",
    ) -> None:
        await self._submit_and_send_stage_result_from_mentions(
            ctx=ctx,
            stage_key=stage_key,
            explicit_game_no=None,
            placements=placements,
        )

    @commands.hybrid_command(name="set_result_game")
    @commands.has_permissions(administrator=True)
    async def set_result_game(
        self,
        ctx: commands.Context,
        stage_key: str,
        game_no: int,
        *,
        placements: str = "",
    ) -> None:
        await self._submit_and_send_stage_result_from_mentions(
            ctx=ctx,
            stage_key=stage_key,
            explicit_game_no=game_no,
            placements=placements,
        )

    @commands.hybrid_command(name="stage_results")
    @commands.has_permissions(administrator=True)
    async def stage_results(self, ctx: commands.Context, stage_key: str) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            result = await self.result_service.get_stage_results(
                guild_id=ctx.guild.id,
                stage_key=stage_key.lower(),
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        embed = self._build_scoreboard_embed(
            title=f"{stage_key.lower()} - Current standings",
            result=result,
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

        if result["stage_type"] == "final" and result["stage_finished"]:
            podium_embed = self._build_final_podium_embed(result)
            await ctx.send(embed=podium_embed)

    @commands.hybrid_command(name="stage_players")
    @commands.has_permissions(administrator=True)
    async def stage_players(self, ctx: commands.Context, stage_key: str) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            tournament = await self.result_service._resolve_tournament_for_stage(
                ctx.guild.id,
                stage_key.lower(),
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        stage = await self.result_service.result_repo.get_stage_by_key(
            int(tournament["id"]),
            stage_key.lower(),
        )
        if stage is None:
            await ctx.send(f"❌ `{stage_key}` stage олдсонгүй.")
            return

        slots = await self.result_service.result_repo.list_stage_slots_with_entries(int(stage["id"]))

        await ctx.send(
            embed=self._build_stage_players_embed(
                tournament=tournament,
                stage=stage,
                slots=slots,
            )
        )

    @commands.hybrid_command(name="final_players")
    @commands.has_permissions(administrator=True)
    async def final_players(self, ctx: commands.Context) -> None:
        await self.stage_players(ctx, "final")

    @set_result.error
    @set_result_game.error
    async def set_result_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Энэ command зөвхөн admin хэрэглэнэ.")
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "❌ Дутуу байна.\n"
                "Жишээ:\n"
                "`.set_result final @p1 @p2 @p3 @p4 @p5 @p6 @p7 @p8`\n"
                "эсвэл\n"
                "`.set_result_game final 1 @p1 @p2 @p3 @p4 @p5 @p6 @p7 @p8`"
            )
            return

        if isinstance(error, commands.BadArgument):
            await ctx.send("❌ Game number буруу байна.")
            return

        raise error

    @commands.hybrid_command(name="weekly_make_zones")
    @commands.has_permissions(administrator=True)
    async def weekly_make_zones(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            result = await self.bracket_service.create_weekly_zones(ctx.guild.id)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        tournament = result["tournament"]
        zones = result["zones"]
        await ctx.send("✅ Zone draw хийгдлээ. Match results дээр post орж, тоглогчид руу DM оролдоно.")
        await self._publish_stage_group(
            ctx.guild,
            tournament=tournament,
            title=f"{tournament['title']} - Zone Draw Complete",
            description=(
                f"Status: **{tournament['status']}**\n"
                f"Total Zones: **{len(zones)}**\n"
                f"Format: **8 players x 4 zones / BO2**"
            ),
            groups=zones,
            color=discord.Color.orange(),
            stage_color=discord.Color.blurple(),
        )

    @commands.hybrid_command(name="weekly_make_test")
    @commands.has_permissions(administrator=True)
    async def weekly_make_test(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer()

        tournament = self._get_active_weekly_tournament(ctx.guild.id)
        if tournament is None:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        self._reset_tournament_stages_for_test(int(tournament["id"]))
        await self.role_sync_service.clear_transient_roles(ctx.guild)

        try:
            zone_result = await self.bracket_service.create_weekly_zones(ctx.guild.id)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        tournament = zone_result["tournament"]
        zones = zone_result["zones"]
        match_results_channel = self._resolve_match_results_channel(ctx.guild)

        header = discord.Embed(
            title=f"{tournament['title']} - Test Zone Draw",
            description=(
                f"Status: **{tournament['status']}**\n"
                f"Total Zones: **{len(zones)}**\n"
                f"Format: **8 players x 4 zones / BO2**"
            ),
            color=discord.Color.orange(),
        )
        branded_header = self._apply_weekly_branding(header)
        await ctx.send(embed=branded_header)
        if isinstance(match_results_channel, discord.TextChannel):
            await match_results_channel.send(embed=branded_header)

        for zone in zones:
            stage = zone["stage"]
            embed = self._build_stage_embed(
                stage_title=stage["stage_key"].replace("_", " ").upper(),
                host_user_id=zone["host_user_id"],
                password=zone["password"],
                slots=zone["slots"],
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)
            if isinstance(match_results_channel, discord.TextChannel):
                await match_results_channel.send(embed=embed)

        try:
            for stage_key in ZONE_KEYS:
                await self._submit_random_stage_results(ctx.guild.id, stage_key)

            semi_result = await self.bracket_service.create_weekly_semis(ctx.guild.id)
            for semi in semi_result["semis"]:
                stage = semi["stage"]
                embed = self._build_stage_embed(
                    stage_title=stage["stage_key"].replace("_", " ").upper(),
                    host_user_id=semi["host_user_id"],
                    password=semi["password"],
                    slots=semi["slots"],
                    color=discord.Color.dark_teal(),
                )
                await ctx.send(embed=embed)
                if isinstance(match_results_channel, discord.TextChannel):
                    await match_results_channel.send(embed=embed)

            for stage_key in SEMI_KEYS:
                await self._submit_random_stage_results(ctx.guild.id, stage_key)

            final_result = await self.bracket_service.create_weekly_final(ctx.guild.id)
            final_stage = final_result["final"]
            final_embed = self._build_stage_embed(
                stage_title=final_stage["stage"]["stage_key"].replace("_", " ").upper(),
                host_user_id=final_stage["host_user_id"],
                password=final_stage["password"],
                slots=final_stage["slots"],
                color=discord.Color.dark_gold(),
            )
            await ctx.send(embed=final_embed)
            if isinstance(match_results_channel, discord.TextChannel):
                await match_results_channel.send(embed=final_embed)

            final_scoreboard = await self._submit_random_stage_results(ctx.guild.id, FINAL_KEY)
        except ValueError as e:
            await ctx.send(f"❌ Test bracket үүсгэх үед алдаа гарлаа: {e}")
            return

        await ctx.send(embed=self._build_final_podium_embed(final_scoreboard))

    @commands.hybrid_command(name="weekly_zone")
    @commands.has_permissions(administrator=True)
    async def weekly_zone(self, ctx: commands.Context) -> None:
        await self._send_existing_weekly_zones(ctx)

    @commands.hybrid_command(name="weekly_semi")
    @commands.has_permissions(administrator=True)
    async def weekly_semi(self, ctx: commands.Context) -> None:
        await self._send_existing_stage_group(
            ctx,
            stage_keys=SEMI_KEYS,
            title_suffix="Current Semifinals",
            empty_message="❌ Semifinal хуваарилалт одоогоор үүсээгүй байна.",
            total_label="Total Semis",
            format_text="8 players x 2 semis / BO2",
            header_color=discord.Color.dark_gold(),
            stage_color=discord.Color.dark_teal(),
        )

    @commands.hybrid_command(name="weekly_final")
    @commands.has_permissions(administrator=True)
    async def weekly_final(self, ctx: commands.Context) -> None:
        await self._send_existing_stage_group(
            ctx,
            stage_keys=[FINAL_KEY],
            title_suffix="Current Grand Final",
            empty_message="❌ Grand Final одоогоор үүсээгүй байна.",
            total_label="Total Finals",
            format_text="Final 8 / BO2",
            header_color=discord.Color.red(),
            stage_color=discord.Color.red(),
        )

    @commands.hybrid_command(name="weekly_make_semis")
    @commands.has_permissions(administrator=True)
    async def weekly_make_semis(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            result = await self.bracket_service.create_weekly_semis(ctx.guild.id)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        tournament = result["tournament"]
        semis = result["semis"]
        semi_user_ids = [int(slot["user_id"]) for semi in semis for slot in semi["slots"]]
        await self.role_sync_service.sync_semi_finalists(ctx.guild, semi_user_ids)
        await ctx.send("✅ Semifinal үүслээ. Match results дээр post орж, тоглогчид руу DM оролдоно.")
        await self._publish_stage_group(
            ctx.guild,
            tournament=tournament,
            title=f"{tournament['title']} - Semifinals Created",
            description=(
                f"Status: **{tournament['status']}**\n"
                f"Total Semis: **{len(semis)}**\n"
                f"Format: **8 players x 2 semis / BO2**"
            ),
            groups=semis,
            color=discord.Color.dark_gold(),
            stage_color=discord.Color.dark_teal(),
        )

    @commands.hybrid_command(name="weekly_make_final")
    @commands.has_permissions(administrator=True)
    async def weekly_make_final(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            result = await self.bracket_service.create_weekly_final(ctx.guild.id)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        tournament = result["tournament"]
        final_data = result["final"]
        final_user_ids = [int(slot["user_id"]) for slot in final_data["slots"]]
        await self.role_sync_service.sync_grand_finalists(ctx.guild, final_user_ids)
        await ctx.send("✅ Grand Final үүслээ. Match results дээр post орж, тоглогчид руу DM оролдоно.")
        await self._publish_stage_group(
            ctx.guild,
            tournament=tournament,
            title=f"{tournament['title']} - Grand Final Created",
            description=(
                f"Status: **{tournament['status']}**\n"
                f"Format: **Final 8 / BO2**"
            ),
            groups=[final_data],
            color=discord.Color.red(),
            stage_color=discord.Color.red(),
        )

    @commands.hybrid_command(name="debug_commands")
    @commands.has_permissions(administrator=True)
    async def debug_commands(self, ctx: commands.Context) -> None:
        names = sorted(self.bot.commands, key=lambda c: c.name)
        text = ", ".join(cmd.name for cmd in names)
        await ctx.send(f"Loaded commands:\n{text[:1900]}")

    @commands.hybrid_command(name="replace")
    @commands.has_permissions(administrator=True)
    async def replace_player(
        self,
        ctx: commands.Context,
        stage_key: str,
        old_member: discord.Member,
        new_member: discord.Member,
        *,
        reason: str = "",
    ) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            result = await self.replacement_service.replace_player(
                guild_id=ctx.guild.id,
                stage_key=stage_key.lower(),
                old_user_id=old_member.id,
                new_user_id=new_member.id,
                new_display_name=new_member.display_name,
                created_by=ctx.author.id,
                reason=reason.strip() or None,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        slot = result["slot"]
        stage = result["stage"]

        embed = discord.Embed(
            title=f"{stage['stage_key'].replace('_', ' ').upper()} - Player Replaced",
            color=discord.Color.purple(),
            description=(
                f"**Out:** {old_member.mention}\n"
                f"**In:** {new_member.mention}\n"
                f"**Slot:** {slot['slot_no']}\n"
                f"**Inherited points:** {slot['total_points']}\n"
                f"**Applied before game:** {result['applied_before_game_no']}"
            ),
        )

        if result["reason"]:
            embed.add_field(name="Reason", value=result["reason"], inline=False)

        if result["stage_finished"]:
            embed.add_field(
                name="Note",
                value="Энэ stage дууссан тул replacement нь дараагийн шатны inheritance дээр хүчинтэй болно.",
                inline=False,
            )
        else:
            embed.add_field(
                name="Note",
                value="Stage үргэлжилж байгаа тул шинэ хүн энэ slot-оор дараагийн тоглолтоос үргэлжилнэ.",
                inline=False,
            )

        await ctx.send(embed=self._apply_weekly_branding(embed))


    @commands.hybrid_command(name="solo", aliases=["rankedsolo"])
    @commands.has_permissions(manage_messages=True)
    async def ranked_solo(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return
        if self._ranked_queue_type_from_channel(ctx.channel) != "solo":
            await ctx.send("❌ `.solo` командыг `🎯・ꜱᴏʟᴏ-ʀᴀɴᴋᴇᴅ` channel дээр ашиглана.")
            return
        if not self._has_ranked_mod_access(ctx):
            await ctx.send("❌ Ranked Solo queue-г moderator нээнэ.")
            return

        previous = self._get_latest_unfinished_ranked_queue_by_type(ctx.guild.id, "solo")
        if previous is not None:
            await ctx.send(
                f"❌ Өмнөх Ranked Solo queue дуусаагүй байна. "
                f"Queue #{int(previous['id'])} -> **{self._ranked_status_label(str(previous['status']))}**"
            )
            return

        queue = self._create_ranked_queue(
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            queue_type="solo",
            created_by=ctx.author.id,
        )
        message = await ctx.send(embed=self._build_ranked_queue_embed(queue, ctx.guild))
        self._set_ranked_queue_message_id(int(queue["id"]), int(message.id))
        await ctx.send("✅ Ranked Solo queue нээгдлээ. Тоглогчид `+` гэж бичиж бүртгүүлнэ.")

    @commands.hybrid_command(name="duo", aliases=["dua", "rankedduo"])
    @commands.has_permissions(manage_messages=True)
    async def ranked_duo(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return
        if self._ranked_queue_type_from_channel(ctx.channel) != "duo":
            await ctx.send("❌ `.duo` командыг `🤝・ᴅᴜᴏ-ʀᴀɴᴋᴇᴅ` channel дээр ашиглана.")
            return
        if not self._has_ranked_mod_access(ctx):
            await ctx.send("❌ Ranked Duo queue-г moderator нээнэ.")
            return

        previous = self._get_latest_unfinished_ranked_queue_by_type(ctx.guild.id, "duo")
        if previous is not None:
            await ctx.send(
                f"❌ Өмнөх Ranked Duo queue дуусаагүй байна. "
                f"Queue #{int(previous['id'])} -> **{self._ranked_status_label(str(previous['status']))}**"
            )
            return

        queue = self._create_ranked_queue(
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            queue_type="duo",
            created_by=ctx.author.id,
        )
        message = await ctx.send(embed=self._build_ranked_queue_embed(queue, ctx.guild))
        self._set_ranked_queue_message_id(int(queue["id"]), int(message.id))
        await ctx.send("✅ Ranked Duo queue нээгдлээ. Тоглогчид `+` гэж бичиж бүртгүүлнэ.")

    @commands.hybrid_command(name="list")
    async def ranked_list(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return
        queue = self._get_latest_active_ranked_queue_for_channel(ctx.guild.id, ctx.channel.id)
        if queue is None:
            await ctx.send("❌ Энэ channel дээр active ranked queue алга.")
            return
        await ctx.send(embed=self._build_ranked_queue_embed(queue, ctx.guild))

    @commands.hybrid_command(name="stop")
    @commands.has_permissions(manage_messages=True)
    async def ranked_stop(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return
        queue = self._get_latest_active_ranked_queue_for_channel(ctx.guild.id, ctx.channel.id)
        if queue is None:
            await ctx.send("❌ Энэ channel дээр active ranked queue алга.")
            return
        if not self._has_ranked_mod_access(ctx):
            await ctx.send("❌ Queue-г зөвхөн moderator хаана.")
            return

        try:
            fresh_queue = self._ranked_stop_queue(int(queue["id"]))
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return
        await self._refresh_ranked_queue_message(ctx.guild, int(fresh_queue["id"]))
        await ctx.send(f"✅ {fresh_queue['title']} бүртгэл хаагдлаа. Одоо `.add @user` хийж баталгаажуулна.")

    @commands.hybrid_command(name="win")
    @commands.has_permissions(manage_messages=True)
    async def ranked_win(self, ctx: commands.Context, member: discord.Member) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return
        queue = self._get_latest_active_ranked_queue_for_channel(ctx.guild.id, ctx.channel.id)
        if queue is None or str(queue["queue_type"]) != "solo":
            await ctx.send("❌ Энэ channel дээр active Ranked Solo queue алга.")
            return
        if not self._has_ranked_mod_access(ctx):
            await ctx.send("❌ Winner-г зөвхөн moderator батална.")
            return

        try:
            fresh_queue = self._ranked_complete_with_winners(int(queue["id"]), [int(member.id)])
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return
        await self._refresh_ranked_queue_message(ctx.guild, int(fresh_queue["id"]))
        await ctx.send(f"🏆 Ranked Solo winner: {member.mention}. Дараагийн `.solo` хийх эрх нээгдлээ.")

    @commands.hybrid_command(name="duowin")
    @commands.has_permissions(manage_messages=True)
    async def ranked_duowin(
        self,
        ctx: commands.Context,
        member_one: discord.Member,
        member_two: discord.Member,
    ) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return
        queue = self._get_latest_active_ranked_queue_for_channel(ctx.guild.id, ctx.channel.id)
        if queue is None or str(queue["queue_type"]) != "duo":
            await ctx.send("❌ Энэ channel дээр active Ranked Duo queue алга.")
            return
        if not self._has_ranked_mod_access(ctx):
            await ctx.send("❌ Winner-г зөвхөн moderator батална.")
            return
        if int(member_one.id) == int(member_two.id):
            await ctx.send("❌ Duo winner 2 өөр тоглогч байх ёстой.")
            return

        try:
            fresh_queue = self._ranked_complete_with_winners(
                int(queue["id"]),
                [int(member_one.id), int(member_two.id)],
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return
        await self._refresh_ranked_queue_message(ctx.guild, int(fresh_queue["id"]))
        await ctx.send(
            f"🏆 Ranked Duo winners: {member_one.mention} + {member_two.mention}. "
            f"Дараагийн `.duo` хийх эрх нээгдлээ."
        )


    @commands.hybrid_command(name="member_test")
    @commands.has_permissions(administrator=True)
    async def member_test(self, ctx: commands.Context, member: discord.Member) -> None:
        await ctx.send(f"✅ OK: {member.display_name} | ID: {member.id}")

    @commands.hybrid_command(name="weekly_end")
    @commands.has_permissions(administrator=True)
    async def weekly_end(self, ctx: commands.Context, target: str | None = None) -> None:
        if ctx.guild is None:
            await ctx.send("❌ Энэ command зөвхөн server дотор ашиглагдана.")
            return

        try:
            tournament = await self.registration_service.end_weekly(ctx.guild.id, target)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        await ctx.send(
            f"✅ Weekly tournament дууслаа.\n"
            f"**{tournament['title']}** ({tournament.get('season_name') or '-'}) -> status: **{tournament['status']}**"
        )

    @tasks.loop(seconds=20)
    async def web_registration_poll_loop(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            tournaments = await self.registration_service.list_registration_weeklies(guild.id)
            if not tournaments:
                continue

            for tournament in tournaments:
                pending_entries = await self.registration_service.entry_repo.list_pending_review_entries(
                    int(tournament["id"])
                )
                for entry in pending_entries:
                    await self._post_waiting_review_card(guild=guild, tournament=tournament, entry=entry)

                if pending_entries or int(tournament.get("register_message_id") or 0) > 0:
                    await self._refresh_registration_ui(guild=guild, tournament_id=int(tournament["id"]))

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))


