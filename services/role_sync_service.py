from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import discord

from core.db import get_db
from repositories.entry_repo import EntryRepo

REGISTERED_ROLE_NAME = "Registered"
CONFIRMED_ROLE_NAME = "Confirmed"
WAITLIST_ROLE_NAME = "Waitlist"
SEMI_FINALIST_ROLE_NAME = "Semi Finalist"
GRAND_FINALIST_ROLE_NAME = "Grand Finalist"
CONFIRMED_ROLE_DURATION_DAYS = 10

TRANSIENT_ROLE_NAMES = (
    REGISTERED_ROLE_NAME,
    WAITLIST_ROLE_NAME,
    SEMI_FINALIST_ROLE_NAME,
    GRAND_FINALIST_ROLE_NAME,
)


class RoleSyncService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.entry_repo = EntryRepo(db_path)

    def _parse_expiry(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    async def extend_confirmed_role_expiry(
        self,
        guild_id: int,
        user_id: int,
        *,
        days: int = CONFIRMED_ROLE_DURATION_DAYS,
    ) -> str:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT confirmed_role_expires_at
                FROM role_memberships
                WHERE guild_id = ? AND user_id = ?
                LIMIT 1
                """,
                (int(guild_id), int(user_id)),
            )
            row = await cursor.fetchone()

            now = datetime.now(timezone.utc)
            current_expiry = self._parse_expiry(
                str(row["confirmed_role_expires_at"]) if row and row["confirmed_role_expires_at"] else None
            )
            base_time = current_expiry if current_expiry and current_expiry > now else now
            new_expiry = (base_time + timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")

            await db.execute(
                """
                INSERT INTO role_memberships (
                    guild_id,
                    user_id,
                    confirmed_role_expires_at,
                    updated_at
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    confirmed_role_expires_at = excluded.confirmed_role_expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (int(guild_id), int(user_id), new_expiry),
            )
            await db.commit()
            return new_expiry
        finally:
            await db.close()

    async def list_active_confirmed_role_user_ids(self, guild_id: int) -> set[int]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT user_id
                FROM role_memberships
                WHERE guild_id = ?
                  AND confirmed_role_expires_at IS NOT NULL
                  AND confirmed_role_expires_at > CURRENT_TIMESTAMP
                """,
                (int(guild_id),),
            )
            rows = await cursor.fetchall()
            return {int(row["user_id"]) for row in rows}
        finally:
            await db.close()

    async def sync_confirmed_role_members(self, guild: discord.Guild) -> None:
        active_user_ids = await self.list_active_confirmed_role_user_ids(int(guild.id))
        await self._sync_named_role_members(
            guild,
            CONFIRMED_ROLE_NAME,
            active_user_ids,
            color=discord.Color.green(),
            reason="Confirmed role expiry sync",
        )

    async def _ensure_role(
        self,
        guild: discord.Guild,
        role_name: str,
        *,
        color: discord.Color | None = None,
    ) -> discord.Role | None:
        role = discord.utils.get(guild.roles, name=role_name)
        if role is not None:
            return role

        try:
            return await guild.create_role(
                name=role_name,
                color=color or discord.Color.default(),
                mentionable=False,
                reason="Auto-created for tournament role sync",
            )
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _sync_named_role_members(
        self,
        guild: discord.Guild,
        role_name: str,
        target_user_ids: set[int],
        *,
        color: discord.Color | None = None,
        reason: str,
    ) -> None:
        role = await self._ensure_role(guild, role_name, color=color)
        if role is None:
            return

        for member in list(role.members):
            if int(member.id) in target_user_ids:
                continue
            try:
                await member.remove_roles(role, reason=reason)
            except (discord.Forbidden, discord.HTTPException):
                continue

        for user_id in sorted(target_user_ids):
            member = guild.get_member(int(user_id))
            if member is None or role in member.roles:
                continue
            try:
                await member.add_roles(role, reason=reason)
            except (discord.Forbidden, discord.HTTPException):
                continue

    async def sync_registration_roles_for_tournament(
        self,
        guild: discord.Guild,
        tournament_id: int,
    ) -> None:
        registered = await self.entry_repo.list_entries(int(tournament_id), statuses=["registered"])
        waitlist = await self.entry_repo.list_entries(int(tournament_id), statuses=["waitlist"])

        await self._sync_named_role_members(
            guild,
            REGISTERED_ROLE_NAME,
            {int(item["user_id"]) for item in registered},
            color=discord.Color.blurple(),
            reason="Weekly registration sync",
        )
        await self.sync_confirmed_role_members(guild)
        await self._sync_named_role_members(
            guild,
            WAITLIST_ROLE_NAME,
            {int(item["user_id"]) for item in waitlist},
            color=discord.Color.orange(),
            reason="Weekly registration sync",
        )

    async def sync_semi_finalists(
        self,
        guild: discord.Guild,
        user_ids: list[int] | set[int],
    ) -> None:
        await self._sync_named_role_members(
            guild,
            SEMI_FINALIST_ROLE_NAME,
            {int(user_id) for user_id in user_ids},
            color=discord.Color.dark_teal(),
            reason="Semifinal role sync",
        )

    async def sync_grand_finalists(
        self,
        guild: discord.Guild,
        user_ids: list[int] | set[int],
    ) -> None:
        await self._sync_named_role_members(
            guild,
            GRAND_FINALIST_ROLE_NAME,
            {int(user_id) for user_id in user_ids},
            color=discord.Color.red(),
            reason="Grand finalist role sync",
        )

    async def clear_transient_roles(self, guild: discord.Guild) -> None:
        for role_name in TRANSIENT_ROLE_NAMES:
            await self._sync_named_role_members(
                guild,
                role_name,
                set(),
                reason="Weekly tournament ended",
            )

    def build_season_champion_role_name(self, season_name: str | None) -> str:
        season_text = (season_name or "").strip()
        match = re.fullmatch(r"Season\s+(\d+)", season_text, flags=re.IGNORECASE)
        if match:
            return f"Weekly Season #{match.group(1)} Champ"
        if season_text:
            return f"Weekly {season_text} Champ"
        return "Weekly Champion"

    def _build_season_result_role_name(self, season_name: str | None, suffix: str) -> str:
        season_text = (season_name or "").strip()
        match = re.fullmatch(r"Season\s+(\d+)", season_text, flags=re.IGNORECASE)
        if match:
            return f"Weekly Season #{match.group(1)} {suffix}"
        if season_text:
            return f"Weekly {season_text} {suffix}"
        return f"Weekly {suffix}"

    async def assign_season_champion_badge(
        self,
        guild: discord.Guild,
        winner_user_id: int,
        season_name: str | None,
    ) -> str:
        role_name = self.build_season_champion_role_name(season_name)
        role = await self._ensure_role(guild, role_name, color=discord.Color.gold())
        if role is None:
            return f"ℹ️ `{role_name}` role үүсгэж/олж чадсангүй."

        winner_member = guild.get_member(int(winner_user_id))
        if winner_member is None:
            return "ℹ️ Winner member guild дотор олдсонгүй."

        if role in winner_member.roles:
            return f"👑 {winner_member.mention} аль хэдийн `{role_name}` badge-тэй байна."

        try:
            await winner_member.add_roles(role, reason="Season champion badge")
            return f"👑 {winner_member.mention} → `{role_name}` badge авлаа."
        except (discord.Forbidden, discord.HTTPException):
            return f"ℹ️ `{role_name}` badge өгөх үед алдаа гарлаа."

    async def assign_season_podium_badges(
        self,
        guild: discord.Guild,
        scoreboard: list[dict],
        season_name: str | None,
    ) -> list[str]:
        podium_specs = (
            (2, "Runner-up", discord.Color.light_grey(), "🥈"),
            (3, "Third Place", discord.Color.orange(), "🥉"),
        )
        messages: list[str] = []

        for target_position, suffix, color, prefix in podium_specs:
            player = next(
                (
                    item
                    for item in scoreboard
                    if int(item.get("final_position") or 0) == target_position
                ),
                None,
            )
            if player is None:
                continue

            role_name = self._build_season_result_role_name(season_name, suffix)
            role = await self._ensure_role(guild, role_name, color=color)
            if role is None:
                messages.append(f"ℹ️ `{role_name}` role үүсгэж/олж чадсангүй.")
                continue

            member = guild.get_member(int(player["user_id"]))
            if member is None:
                messages.append(f"ℹ️ {suffix} member guild дотор олдсонгүй.")
                continue

            if role in member.roles:
                messages.append(f"{prefix} {member.mention} аль хэдийн `{role_name}` badge-тэй байна.")
                continue

            try:
                await member.add_roles(role, reason=f"Season {suffix.lower()} badge")
                messages.append(f"{prefix} {member.mention} → `{role_name}` badge авлаа.")
            except (discord.Forbidden, discord.HTTPException):
                messages.append(f"ℹ️ `{role_name}` badge өгөх үед алдаа гарлаа.")

        return messages
