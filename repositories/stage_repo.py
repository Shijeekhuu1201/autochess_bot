from __future__ import annotations

from typing import Any

from core.db import get_db


def _row_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class StageRepo:
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

    async def list_stages(
        self,
        tournament_id: int,
        stage_type: str | None = None,
    ) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            if stage_type:
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM stages
                    WHERE tournament_id = ? AND stage_type = ?
                    ORDER BY round_order ASC, id ASC
                    """,
                    (tournament_id, stage_type),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM stages
                    WHERE tournament_id = ?
                    ORDER BY round_order ASC, id ASC
                    """,
                    (tournament_id,),
                )

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def create_stage(
        self,
        tournament_id: int,
        stage_key: str,
        stage_type: str,
        round_order: int,
        lobby_password: str,
        host_user_id: int,
        game_count: int = 2,
        status: str = "ready",
    ) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                INSERT INTO stages (
                    tournament_id,
                    stage_key,
                    stage_type,
                    round_order,
                    lobby_password,
                    host_user_id,
                    game_count,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tournament_id,
                    stage_key,
                    stage_type,
                    round_order,
                    lobby_password,
                    host_user_id,
                    game_count,
                    status,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def add_stage_slot(
        self,
        stage_id: int,
        slot_no: int,
        original_entry_id: int,
        current_entry_id: int,
        inherited_from_slot_id: int | None = None,
    ) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                INSERT INTO stage_slots (
                    stage_id,
                    slot_no,
                    original_entry_id,
                    current_entry_id,
                    inherited_from_slot_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    stage_id,
                    slot_no,
                    original_entry_id,
                    current_entry_id,
                    inherited_from_slot_id,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def create_game(
        self,
        stage_id: int,
        game_no: int,
        status: str = "pending",
    ) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                INSERT INTO games (stage_id, game_no, status)
                VALUES (?, ?, ?)
                """,
                (stage_id, game_no, status),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def list_stage_slots_with_entries(
        self,
        stage_id: int,
    ) -> list[dict[str, Any]]:
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
                    te.user_id,
                    te.display_name,
                    te.status AS entry_status,
                    te.payment_status
                FROM stage_slots ss
                JOIN tournament_entries te
                  ON te.id = ss.current_entry_id
                WHERE ss.stage_id = ?
                ORDER BY ss.slot_no ASC
                """,
                (stage_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def list_qualified_slots_by_stage_type(
        self,
        tournament_id: int,
        stage_type: str,
    ) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    ss.id AS source_stage_slot_id,
                    ss.stage_id,
                    ss.original_entry_id,
                    ss.current_entry_id,
                    ss.total_points,
                    ss.final_position,
                    s.stage_key,
                    s.stage_type,
                    s.status AS stage_status,
                    te.user_id,
                    te.display_name
                FROM stage_slots ss
                JOIN stages s
                  ON s.id = ss.stage_id
                JOIN tournament_entries te
                  ON te.id = ss.current_entry_id
                WHERE s.tournament_id = ?
                  AND s.stage_type = ?
                  AND s.status = 'finished'
                  AND ss.qualified = 1
                ORDER BY s.stage_key ASC, ss.final_position ASC, ss.slot_no ASC
                """,
                (tournament_id, stage_type),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()