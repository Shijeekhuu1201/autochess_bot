from __future__ import annotations

from typing import Any

from core.db import get_db


def _row_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class SupporterRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def upsert_donor_membership(
        self,
        guild_id: int,
        user_id: int,
        tier_name: str,
        expires_at: str,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                INSERT INTO supporter_memberships (
                    guild_id,
                    user_id,
                    donor_tier,
                    donor_expires_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    donor_tier = excluded.donor_tier,
                    donor_expires_at = excluded.donor_expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (int(guild_id), int(user_id), tier_name, expires_at),
            )
            await db.commit()
        finally:
            await db.close()

    async def upsert_sponsor_membership(
        self,
        guild_id: int,
        user_id: int,
        tier_name: str,
        expires_at: str,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                INSERT INTO supporter_memberships (
                    guild_id,
                    user_id,
                    sponsor_tier,
                    sponsor_expires_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    sponsor_tier = excluded.sponsor_tier,
                    sponsor_expires_at = excluded.sponsor_expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (int(guild_id), int(user_id), tier_name, expires_at),
            )
            await db.commit()
        finally:
            await db.close()

    async def clear_expired_memberships(self, guild_id: int) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                UPDATE supporter_memberships
                SET donor_tier = CASE
                        WHEN donor_expires_at IS NOT NULL AND donor_expires_at <= CURRENT_TIMESTAMP THEN NULL
                        ELSE donor_tier
                    END,
                    donor_expires_at = CASE
                        WHEN donor_expires_at IS NOT NULL AND donor_expires_at <= CURRENT_TIMESTAMP THEN NULL
                        ELSE donor_expires_at
                    END,
                    sponsor_tier = CASE
                        WHEN sponsor_expires_at IS NOT NULL AND sponsor_expires_at <= CURRENT_TIMESTAMP THEN NULL
                        ELSE sponsor_tier
                    END,
                    sponsor_expires_at = CASE
                        WHEN sponsor_expires_at IS NOT NULL AND sponsor_expires_at <= CURRENT_TIMESTAMP THEN NULL
                        ELSE sponsor_expires_at
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                """,
                (int(guild_id),),
            )
            await db.execute(
                """
                DELETE FROM supporter_memberships
                WHERE guild_id = ?
                  AND donor_tier IS NULL
                  AND sponsor_tier IS NULL
                """,
                (int(guild_id),),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_membership(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM supporter_memberships
                WHERE guild_id = ? AND user_id = ?
                LIMIT 1
                """,
                (int(guild_id), int(user_id)),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def list_guild_memberships(self, guild_id: int) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM supporter_memberships
                WHERE guild_id = ?
                ORDER BY updated_at DESC, user_id ASC
                """,
                (int(guild_id),),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def get_active_support_status(self, user_id: int, guild_id: int | None = None) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            if guild_id is None:
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM supporter_memberships
                    WHERE user_id = ?
                      AND (
                        (donor_expires_at IS NOT NULL AND donor_expires_at > CURRENT_TIMESTAMP)
                        OR
                        (sponsor_expires_at IS NOT NULL AND sponsor_expires_at > CURRENT_TIMESTAMP)
                      )
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (int(user_id),),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM supporter_memberships
                    WHERE guild_id = ?
                      AND user_id = ?
                      AND (
                        (donor_expires_at IS NOT NULL AND donor_expires_at > CURRENT_TIMESTAMP)
                        OR
                        (sponsor_expires_at IS NOT NULL AND sponsor_expires_at > CURRENT_TIMESTAMP)
                      )
                    LIMIT 1
                    """,
                    (int(guild_id), int(user_id)),
                )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()
