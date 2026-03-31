from __future__ import annotations

from typing import Any

from core.db import get_db


def _row_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class ResultRepo:
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

    async def get_game(
        self,
        stage_id: int,
        game_no: int,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM games
                WHERE stage_id = ? AND game_no = ?
                LIMIT 1
                """,
                (stage_id, game_no),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
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

    async def replace_game_results(
        self,
        game_id: int,
        ordered_stage_slot_ids: list[int],
        score_map: dict[int, int],
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                "DELETE FROM game_results WHERE game_id = ?",
                (game_id,),
            )

            for placement, stage_slot_id in enumerate(ordered_stage_slot_ids, start=1):
                points = int(score_map[placement])
                await db.execute(
                    """
                    INSERT INTO game_results (
                        game_id,
                        stage_slot_id,
                        placement,
                        points
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (game_id, stage_slot_id, placement, points),
                )

            await db.execute(
                """
                UPDATE games
                SET status = 'confirmed'
                WHERE id = ?
                """,
                (game_id,),
            )
            await db.commit()
        finally:
            await db.close()

    async def recalculate_stage_totals(self, stage_id: int) -> None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    ss.id AS stage_slot_id,
                    COALESCE(SUM(gr.points), 0) AS total_points
                FROM stage_slots ss
                LEFT JOIN game_results gr
                  ON gr.stage_slot_id = ss.id
                WHERE ss.stage_id = ?
                GROUP BY ss.id
                """,
                (stage_id,),
            )
            rows = await cursor.fetchall()

            for row in rows:
                await db.execute(
                    """
                    UPDATE stage_slots
                    SET total_points = ?
                    WHERE id = ?
                    """,
                    (int(row["total_points"]), int(row["stage_slot_id"])),
                )

            await db.commit()
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
            return int(row["total"])
        finally:
            await db.close()

    async def update_stage_status(self, stage_id: int, status: str) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE stages
                SET status = ?
                WHERE id = ?
                """,
                (status, stage_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def list_stage_scoreboard(
        self,
        stage_id: int,
    ) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    ss.id AS stage_slot_id,
                    ss.slot_no,
                    ss.current_entry_id AS entry_id,
                    ss.total_points,
                    ss.final_position,
                    ss.qualified,
                    ss.eliminated,
                    te.user_id,
                    te.display_name,
                    COALESCE(MAX(CASE WHEN g.game_no = 1 THEN gr.points END), 0) AS game1_points,
                    COALESCE(MAX(CASE WHEN g.game_no = 2 THEN gr.points END), 0) AS game2_points,
                    COALESCE(MAX(CASE WHEN g.game_no = 1 THEN gr.placement END), 0) AS game1_placement,
                    COALESCE(MAX(CASE WHEN g.game_no = 2 THEN gr.placement END), 0) AS game2_placement
                FROM stage_slots ss
                JOIN tournament_entries te
                  ON te.id = ss.current_entry_id
                LEFT JOIN game_results gr
                  ON gr.stage_slot_id = ss.id
                LEFT JOIN games g
                  ON g.id = gr.game_id
                WHERE ss.stage_id = ?
                GROUP BY
                    ss.id,
                    ss.slot_no,
                    ss.current_entry_id,
                    ss.total_points,
                    ss.final_position,
                    ss.qualified,
                    ss.eliminated,
                    te.user_id,
                    te.display_name
                """,
                (stage_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def update_stage_rankings(
        self,
        stage_id: int,
        ordered_stage_slot_ids: list[int],
        qualify_count: int,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE stage_slots
                SET final_position = NULL,
                    qualified = 0,
                    eliminated = 0
                WHERE stage_id = ?
                """,
                (stage_id,),
            )

            for index, stage_slot_id in enumerate(ordered_stage_slot_ids, start=1):
                qualified = 1 if qualify_count > 0 and index <= qualify_count else 0
                eliminated = 1 if qualify_count > 0 and index > qualify_count else 0

                await db.execute(
                    """
                    UPDATE stage_slots
                    SET final_position = ?,
                        qualified = ?,
                        eliminated = ?
                    WHERE id = ?
                    """,
                    (index, qualified, eliminated, stage_slot_id),
                )

            await db.commit()
        finally:
            await db.close()
