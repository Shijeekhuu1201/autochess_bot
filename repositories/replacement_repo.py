from __future__ import annotations

from typing import Any

from core.db import get_db


def _row_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class ReplacementRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def get_stage_by_key(
        self,
        tournament_id: int,
        stage_key: str,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM stages
                WHERE tournament_id = ? AND stage_key = ?
                LIMIT 1
                """,
                (tournament_id, stage_key),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def count_confirmed_games(self, stage_id: int) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT COUNT(*) AS total
                FROM games
                WHERE stage_id = ? AND status = 'confirmed'
                """,
                (stage_id,),
            )
            row = await cursor.fetchone()
            return int(row["total"] or 0)
        finally:
            await db.close()

    async def get_stage_slot_by_current_user(
        self,
        stage_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    ss.id AS stage_slot_id,
                    ss.stage_id,
                    ss.slot_no,
                    ss.original_entry_id,
                    ss.current_entry_id,
                    ss.inherited_from_slot_id,
                    ss.total_points,
                    ss.final_position,
                    ss.qualified,
                    ss.eliminated,
                    te.id AS entry_id,
                    te.user_id,
                    te.display_name,
                    te.status AS entry_status,
                    te.payment_status
                FROM stage_slots ss
                JOIN tournament_entries te
                  ON te.id = ss.current_entry_id
                WHERE ss.stage_id = ? AND te.user_id = ?
                LIMIT 1
                """,
                (stage_id, user_id),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_entry_by_user(
        self,
        tournament_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournament_entries
                WHERE tournament_id = ? AND user_id = ?
                LIMIT 1
                """,
                (tournament_id, user_id),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_next_register_order(self, tournament_id: int) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT COALESCE(MAX(register_order), 0) + 1 AS next_order
                FROM tournament_entries
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            )
            row = await cursor.fetchone()
            return int(row["next_order"])
        finally:
            await db.close()

    async def create_replacement_entry(
        self,
        tournament_id: int,
        user_id: int,
        display_name: str,
        register_order: int,
        replacement_for_entry_id: int,
    ) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                INSERT INTO tournament_entries (
                    tournament_id,
                    user_id,
                    display_name,
                    register_order,
                    payment_status,
                    status,
                    is_replacement,
                    replacement_for_entry_id,
                    confirmed_at
                )
                VALUES (?, ?, ?, ?, 'confirmed', 'replacement_in', 1, ?, CURRENT_TIMESTAMP)
                """,
                (
                    tournament_id,
                    user_id,
                    display_name,
                    register_order,
                    replacement_for_entry_id,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def update_entry_status(
        self,
        entry_id: int,
        status: str,
        payment_status: str | None = None,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            if payment_status is None:
                await db.execute(
                    """
                    UPDATE tournament_entries
                    SET status = ?
                    WHERE id = ?
                    """,
                    (status, entry_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE tournament_entries
                    SET status = ?,
                        payment_status = ?
                    WHERE id = ?
                    """,
                    (status, payment_status, entry_id),
                )
            await db.commit()
        finally:
            await db.close()

    async def update_stage_slot_current_entry(
        self,
        stage_slot_id: int,
        new_entry_id: int,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE stage_slots
                SET current_entry_id = ?
                WHERE id = ?
                """,
                (new_entry_id, stage_slot_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def create_replacement_log(
        self,
        tournament_id: int,
        stage_id: int,
        stage_slot_id: int,
        out_entry_id: int,
        in_entry_id: int,
        applied_before_game_no: int,
        created_by: int,
        reason: str | None = None,
    ) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                INSERT INTO replacements (
                    tournament_id,
                    stage_id,
                    stage_slot_id,
                    out_entry_id,
                    in_entry_id,
                    applied_before_game_no,
                    reason,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tournament_id,
                    stage_id,
                    stage_slot_id,
                    out_entry_id,
                    in_entry_id,
                    applied_before_game_no,
                    reason,
                    created_by,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def get_stage_slot_snapshot(
        self,
        stage_slot_id: int,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    ss.id AS stage_slot_id,
                    ss.slot_no,
                    ss.total_points,
                    ss.final_position,
                    ss.qualified,
                    ss.eliminated,
                    te.id AS entry_id,
                    te.user_id,
                    te.display_name,
                    te.status AS entry_status
                FROM stage_slots ss
                JOIN tournament_entries te
                  ON te.id = ss.current_entry_id
                WHERE ss.id = ?
                LIMIT 1
                """,
                (stage_slot_id,),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()