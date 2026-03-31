from __future__ import annotations

import re
from typing import Any

from core.db import get_db


def _row_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class TournamentRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _slugify(self, value: str) -> str:
        lowered = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
        return lowered.strip("-")

    async def get_next_season_name(
        self,
        guild_id: int,
        tournament_type: str,
        *,
        game_key: str | None = None,
        format_key: str | None = None,
    ) -> str:
        db = await get_db(self.db_path)
        try:
            query = [
                "SELECT season_name",
                "FROM tournaments",
                "WHERE guild_id = ?",
                "  AND type = ?",
            ]
            params: list[Any] = [guild_id, tournament_type]
            if game_key is not None:
                query.append("  AND game_key = ?")
                params.append(game_key)
            if format_key is not None:
                query.append("  AND format_key = ?")
                params.append(format_key)
            query.extend(["ORDER BY id DESC", "LIMIT 1"])
            cursor = await db.execute("\n".join(query), tuple(params))
            row = await cursor.fetchone()
            if row is None:
                return "Season 1"

            season_name = str(row["season_name"] or "").strip()
            if season_name.lower().startswith("season "):
                suffix = season_name[7:].strip()
                if suffix.isdigit():
                    return f"Season {int(suffix) + 1}"

            return "Season 1"
        finally:
            await db.close()

    async def create_tournament(
        self,
        guild_id: int,
        tournament_type: str,
        game_key: str,
        format_key: str,
        title: str,
        created_by: int,
        season_name: str = "Season 1",
        entry_fee: int = 0,
        max_players: int = 32,
        lobby_size: int = 8,
        bo_count: int = 2,
        start_time: str | None = None,
        checkin_time: str | None = None,
        prize_total: int = 0,
        prize_1: int = 0,
        prize_2: int = 0,
        prize_3: int = 0,
        status: str = "registration_open",
        slug: str = "",
    ) -> int:
        db = await get_db(self.db_path)
        try:
            resolved_slug = slug or self._slugify(f"{game_key}-{tournament_type}-{season_name}-{title}")
            cursor = await db.execute(
                """
                INSERT INTO tournaments (
                    guild_id, type, game_key, format_key, slug, title, season_name, entry_fee, max_players,
                    lobby_size, bo_count, start_time, checkin_time,
                    prize_total, prize_1, prize_2, prize_3,
                    status, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    tournament_type,
                    game_key,
                    format_key,
                    resolved_slug,
                    title,
                    season_name,
                    entry_fee,
                    max_players,
                    lobby_size,
                    bo_count,
                    start_time,
                    checkin_time,
                    prize_total,
                    prize_1,
                    prize_2,
                    prize_3,
                    status,
                    created_by,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def get_by_id(self, tournament_id: int) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                "SELECT * FROM tournaments WHERE id = ?",
                (tournament_id,),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_latest_active_by_type(
        self,
        guild_id: int,
        tournament_type: str,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournaments
                WHERE guild_id = ?
                  AND type = ?
                  AND status NOT IN ('completed', 'cancelled')
                ORDER BY id DESC
                LIMIT 1
                """,
                (guild_id, tournament_type),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_latest_active_by_scope(
        self,
        guild_id: int,
        tournament_type: str,
        *,
        game_key: str | None = None,
        format_key: str | None = None,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            query = [
                "SELECT *",
                "FROM tournaments",
                "WHERE guild_id = ?",
                "  AND type = ?",
                "  AND status NOT IN ('completed', 'cancelled')",
            ]
            params: list[Any] = [guild_id, tournament_type]
            if game_key is not None:
                query.append("  AND game_key = ?")
                params.append(game_key)
            if format_key is not None:
                query.append("  AND format_key = ?")
                params.append(format_key)
            query.extend(["ORDER BY id DESC", "LIMIT 1"])
            cursor = await db.execute("\n".join(query), tuple(params))
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def list_active_by_guild(self, guild_id: int) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournaments
                WHERE guild_id = ?
                  AND status NOT IN ('completed', 'cancelled')
                ORDER BY id DESC
                """,
                (guild_id,),
            )
            rows = await cursor.fetchall()
            return [_row_to_dict(row) for row in rows if row is not None]
        finally:
            await db.close()

    async def list_active_by_scope(
        self,
        guild_id: int,
        tournament_type: str,
        *,
        game_key: str | None = None,
        format_key: str | None = None,
    ) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            query = [
                "SELECT *",
                "FROM tournaments",
                "WHERE guild_id = ?",
                "  AND type = ?",
                "  AND status NOT IN ('completed', 'cancelled')",
            ]
            params: list[Any] = [guild_id, tournament_type]
            if game_key is not None:
                query.append("  AND game_key = ?")
                params.append(game_key)
            if format_key is not None:
                query.append("  AND format_key = ?")
                params.append(format_key)
            query.append("ORDER BY id DESC")
            cursor = await db.execute("\n".join(query), tuple(params))
            rows = await cursor.fetchall()
            return [_row_to_dict(row) for row in rows if row is not None]
        finally:
            await db.close()

    async def get_latest_open_registration_by_type(
        self,
        guild_id: int,
        tournament_type: str,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournaments
                WHERE guild_id = ?
                  AND type = ?
                  AND status = 'registration_open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (guild_id, tournament_type),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_latest_open_registration_by_scope(
        self,
        guild_id: int,
        tournament_type: str,
        *,
        game_key: str | None = None,
        format_key: str | None = None,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            query = [
                "SELECT *",
                "FROM tournaments",
                "WHERE guild_id = ?",
                "  AND type = ?",
                "  AND status = 'registration_open'",
            ]
            params: list[Any] = [guild_id, tournament_type]
            if game_key is not None:
                query.append("  AND game_key = ?")
                params.append(game_key)
            if format_key is not None:
                query.append("  AND format_key = ?")
                params.append(format_key)
            query.extend(["ORDER BY id DESC", "LIMIT 1"])
            cursor = await db.execute("\n".join(query), tuple(params))
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_latest_by_type(
        self,
        guild_id: int,
        tournament_type: str,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournaments
                WHERE guild_id = ?
                  AND type = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (guild_id, tournament_type),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_latest_by_scope(
        self,
        guild_id: int,
        tournament_type: str,
        *,
        game_key: str | None = None,
        format_key: str | None = None,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            query = [
                "SELECT *",
                "FROM tournaments",
                "WHERE guild_id = ?",
                "  AND type = ?",
            ]
            params: list[Any] = [guild_id, tournament_type]
            if game_key is not None:
                query.append("  AND game_key = ?")
                params.append(game_key)
            if format_key is not None:
                query.append("  AND format_key = ?")
                params.append(format_key)
            query.extend(["ORDER BY id DESC", "LIMIT 1"])
            cursor = await db.execute("\n".join(query), tuple(params))
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_latest_by_stage_key(
        self,
        guild_id: int,
        tournament_type: str,
        stage_key: str,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT t.*
                FROM tournaments t
                JOIN stages s
                  ON s.tournament_id = t.id
                WHERE t.guild_id = ?
                  AND t.type = ?
                  AND s.stage_key = ?
                ORDER BY t.id DESC
                LIMIT 1
                """,
                (guild_id, tournament_type, stage_key),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_latest_by_stage_scope(
        self,
        guild_id: int,
        tournament_type: str,
        stage_key: str,
        *,
        game_key: str | None = None,
        format_key: str | None = None,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            query = [
                "SELECT t.*",
                "FROM tournaments t",
                "JOIN stages s",
                "  ON s.tournament_id = t.id",
                "WHERE t.guild_id = ?",
                "  AND t.type = ?",
                "  AND s.stage_key = ?",
            ]
            params: list[Any] = [guild_id, tournament_type, stage_key]
            if game_key is not None:
                query.append("  AND t.game_key = ?")
                params.append(game_key)
            if format_key is not None:
                query.append("  AND t.format_key = ?")
                params.append(format_key)
            query.extend(["ORDER BY t.id DESC", "LIMIT 1"])
            cursor = await db.execute("\n".join(query), tuple(params))
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def update_status(self, tournament_id: int, status: str) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournaments
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, tournament_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def set_announcement_message(
        self,
        tournament_id: int,
        channel_id: int,
        message_id: int,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournaments
                SET announcement_channel_id = ?,
                    announcement_message_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (channel_id, message_id, tournament_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_registration_ui_state(
        self,
        tournament_id: int,
        register_channel_id: int,
        register_message_id: int,
        waiting_channel_id: int,
        waiting_summary_message_id: int,
        confirmed_channel_id: int,
        confirmed_summary_message_id: int,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournaments
                SET register_channel_id = ?,
                    register_message_id = ?,
                    waiting_channel_id = ?,
                    waiting_summary_message_id = ?,
                    confirmed_channel_id = ?,
                    confirmed_summary_message_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(register_channel_id),
                    int(register_message_id),
                    int(waiting_channel_id),
                    int(waiting_summary_message_id),
                    int(confirmed_channel_id),
                    int(confirmed_summary_message_id),
                    int(tournament_id),
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_prize_total(self, tournament_id: int, amount: int) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournaments
                SET prize_total = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(amount), tournament_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_prize_rank(self, tournament_id: int, rank: int, amount: int) -> None:
        if rank not in (1, 2, 3):
            raise ValueError("Prize rank зөвхөн 1, 2, 3 байж болно.")

        column = f"prize_{rank}"

        db = await get_db(self.db_path)
        try:
            await db.execute(
                f"""
                UPDATE tournaments
                SET {column} = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(amount), tournament_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_rules_text(self, tournament_id: int, rules_text: str) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournaments
                SET rules_text = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (rules_text, tournament_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def add_sponsor(
        self,
        tournament_id: int,
        sponsor_name: str,
        sponsor_user_id: int | None,
        amount: int,
        note: str,
        created_by: int,
    ) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                INSERT INTO sponsors (
                    tournament_id,
                    sponsor_name,
                    sponsor_user_id,
                    amount,
                    note,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tournament_id,
                    sponsor_name,
                    sponsor_user_id,
                    int(amount),
                    note,
                    created_by,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def list_sponsors(self, tournament_id: int) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM sponsors
                WHERE tournament_id = ?
                ORDER BY amount DESC, id ASC
                """,
                (tournament_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def get_sponsor_total(self, tournament_id: int) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM sponsors
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            )
            row = await cursor.fetchone()
            return int(row["total"] or 0)
        finally:
            await db.close()
