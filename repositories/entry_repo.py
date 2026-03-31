from __future__ import annotations

from typing import Any, Iterable

from core.db import get_db


def _row_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class EntryRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

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

    async def get_entry_by_id(self, entry_id: int) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournament_entries
                WHERE id = ?
                LIMIT 1
                """,
                (entry_id,),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_entry_by_review_message_id(
        self,
        review_message_id: int,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournament_entries
                WHERE review_message_id = ?
                LIMIT 1
                """,
                (review_message_id,),
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

    async def add_entry(
        self,
        tournament_id: int,
        user_id: int,
        display_name: str,
        register_order: int,
        status: str = "registered",
        payment_status: str = "unpaid",
        is_replacement: int = 0,
        replacement_for_entry_id: int | None = None,
        source: str = "discord",
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
                    source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tournament_id,
                    user_id,
                    display_name,
                    register_order,
                    payment_status,
                    status,
                    is_replacement,
                    replacement_for_entry_id,
                    source,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def get_entry_by_user_and_tournament(
        self,
        tournament_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        return await self.get_entry_by_user(tournament_id, user_id)

    async def delete_entry(self, entry_id: int) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                "DELETE FROM tournament_entries WHERE id = ?",
                (entry_id,),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_status(self, entry_id: int, status: str) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournament_entries
                SET status = ?
                WHERE id = ?
                """,
                (status, entry_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_payment_status(self, entry_id: int, payment_status: str) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournament_entries
                SET payment_status = ?
                WHERE id = ?
                """,
                (payment_status, entry_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_review_message_id(self, entry_id: int, review_message_id: int) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournament_entries
                SET review_message_id = ?
                WHERE id = ?
                """,
                (int(review_message_id), entry_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def confirm_entry(self, entry_id: int, waitlist: bool = False) -> None:
        new_status = "waitlist" if waitlist else "confirmed"
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournament_entries
                SET payment_status = 'confirmed',
                    status = ?,
                    confirmed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_status, entry_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def reject_payment(self, entry_id: int) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE tournament_entries
                SET payment_status = 'rejected'
                WHERE id = ?
                """,
                (entry_id,),
            )
            await db.commit()
        finally:
            await db.close()

    async def count_by_status(self, tournament_id: int, status: str) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT COUNT(*) AS total
                FROM tournament_entries
                WHERE tournament_id = ? AND status = ?
                """,
                (tournament_id, status),
            )
            row = await cursor.fetchone()
            return int(row["total"])
        finally:
            await db.close()

    async def count_total(self, tournament_id: int) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT COUNT(*) AS total
                FROM tournament_entries
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            )
            row = await cursor.fetchone()
            return int(row["total"])
        finally:
            await db.close()

    async def list_entries(
        self,
        tournament_id: int,
        statuses: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            if statuses:
                statuses = list(statuses)
                placeholders = ",".join("?" for _ in statuses)
                query = f"""
                    SELECT *
                    FROM tournament_entries
                    WHERE tournament_id = ?
                      AND status IN ({placeholders})
                    ORDER BY register_order ASC
                """
                params = [tournament_id, *statuses]
                cursor = await db.execute(query, params)
            else:
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM tournament_entries
                    WHERE tournament_id = ?
                    ORDER BY register_order ASC
                    """,
                    (tournament_id,),
                )

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def get_summary_counts(self, tournament_id: int) -> dict[str, int]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'registered' THEN 1 ELSE 0 END) AS registered_count,
                    SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed_count,
                    SUM(CASE WHEN status = 'waitlist' THEN 1 ELSE 0 END) AS waitlist_count
                FROM tournament_entries
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            )
            row = await cursor.fetchone()
            return {
                "total": int(row["total"] or 0),
                "registered_count": int(row["registered_count"] or 0),
                "confirmed_count": int(row["confirmed_count"] or 0),
                "waitlist_count": int(row["waitlist_count"] or 0),
            }
        finally:
            await db.close()

    async def list_pending_review_entries(self, tournament_id: int) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournament_entries
                WHERE tournament_id = ?
                  AND status IN ('registered', 'waitlist')
                  AND payment_status = 'unpaid'
                  AND COALESCE(review_message_id, 0) = 0
                ORDER BY register_order ASC, id ASC
                """,
                (tournament_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()
