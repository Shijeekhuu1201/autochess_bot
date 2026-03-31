from __future__ import annotations

from typing import Any

from core.db import get_db


def _row_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class StatsRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def payout_exists_for_tournament(self, tournament_id: int) -> bool:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT 1
                FROM payouts
                WHERE tournament_id = ?
                LIMIT 1
                """,
                (tournament_id,),
            )
            row = await cursor.fetchone()
            return row is not None
        finally:
            await db.close()

    async def tournament_results_exist(self, tournament_id: int) -> bool:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT 1
                FROM player_tournament_results
                WHERE tournament_id = ?
                LIMIT 1
                """,
                (tournament_id,),
            )
            row = await cursor.fetchone()
            return row is not None
        finally:
            await db.close()

    async def create_payout(
        self,
        tournament_id: int,
        entry_id: int,
        final_rank: int,
        amount: int,
    ) -> int:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                INSERT INTO payouts (
                    tournament_id,
                    entry_id,
                    final_rank,
                    amount
                )
                VALUES (?, ?, ?, ?)
                """,
                (tournament_id, entry_id, final_rank, amount),
            )
            await db.commit()
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def upsert_player_profile(
        self,
        user_id: int,
        display_name: str,
        avatar_url: str | None = None,
        phone_number: str | None = None,
        bank_account: str | None = None,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                INSERT INTO player_profiles (
                    user_id,
                    display_name,
                    avatar_url,
                    phone_number,
                    bank_account
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    avatar_url = COALESCE(excluded.avatar_url, player_profiles.avatar_url),
                    phone_number = COALESCE(excluded.phone_number, player_profiles.phone_number),
                    bank_account = COALESCE(excluded.bank_account, player_profiles.bank_account),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, display_name, avatar_url, phone_number, bank_account),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_player_contact(
        self,
        user_id: int,
        display_name: str,
        *,
        phone_number: str | None = None,
        bank_account: str | None = None,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                INSERT INTO player_profiles (
                    user_id,
                    display_name,
                    phone_number,
                    bank_account
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    phone_number = COALESCE(excluded.phone_number, player_profiles.phone_number),
                    bank_account = COALESCE(excluded.bank_account, player_profiles.bank_account),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, display_name, phone_number, bank_account),
            )
            await db.commit()
        finally:
            await db.close()

    async def ensure_player_stats_row(self, user_id: int) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                INSERT INTO player_stats (user_id)
                VALUES (?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id,),
            )
            await db.commit()
        finally:
            await db.close()

    async def increment_player_stats(
        self,
        user_id: int,
        tournament_type: str,
        final_rank: int,
        prize_amount: int,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            weekly_inc = 1 if tournament_type == "weekly" else 0
            special_inc = 1 if tournament_type == "special" else 0
            monthly_inc = 1 if tournament_type == "monthly" else 0

            champ_inc = 1 if final_rank == 1 else 0
            runner_inc = 1 if final_rank == 2 else 0
            third_inc = 1 if final_rank == 3 else 0
            podium_inc = 1 if final_rank in (1, 2, 3) else 0
            wins_inc = 1 if final_rank == 1 else 0

            await db.execute(
                """
                UPDATE player_stats
                SET tournaments_played = tournaments_played + 1,
                    weekly_played = weekly_played + ?,
                    special_played = special_played + ?,
                    monthly_played = monthly_played + ?,
                    championships = championships + ?,
                    runner_ups = runner_ups + ?,
                    third_places = third_places + ?,
                    podiums = podiums + ?,
                    total_wins = total_wins + ?,
                    total_prize_money = total_prize_money + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (
                    weekly_inc,
                    special_inc,
                    monthly_inc,
                    champ_inc,
                    runner_inc,
                    third_inc,
                    podium_inc,
                    wins_inc,
                    int(prize_amount),
                    user_id,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def save_player_tournament_result(
        self,
        tournament_id: int,
        user_id: int,
        display_name: str,
        season_name: str,
        tournament_title: str,
        final_rank: int,
        prize_amount: int,
        total_points: int,
    ) -> None:
        db = await get_db(self.db_path)
        try:
            await db.execute(
                """
                INSERT INTO player_tournament_results (
                    tournament_id,
                    user_id,
                    display_name,
                    season_name,
                    tournament_title,
                    final_rank,
                    prize_amount,
                    total_points
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tournament_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    season_name = excluded.season_name,
                    tournament_title = excluded.tournament_title,
                    final_rank = excluded.final_rank,
                    prize_amount = excluded.prize_amount,
                    total_points = excluded.total_points
                """,
                (
                    int(tournament_id),
                    int(user_id),
                    display_name,
                    season_name,
                    tournament_title,
                    int(final_rank),
                    int(prize_amount),
                    int(total_points),
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_top_leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    pp.user_id,
                    pp.display_name,
                    pp.avatar_url,
                    pp.phone_number,
                    pp.bank_account,
                    ps.tournaments_played,
                    ps.weekly_played,
                    ps.special_played,
                    ps.monthly_played,
                    ps.championships,
                    ps.runner_ups,
                    ps.third_places,
                    ps.podiums,
                    ps.total_wins,
                    ps.total_prize_money
                FROM player_stats ps
                JOIN player_profiles pp
                  ON pp.user_id = ps.user_id
                ORDER BY
                    ps.championships DESC,
                    ps.podiums DESC,
                    ps.total_prize_money DESC,
                    pp.display_name ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def get_player_stats(self, user_id: int) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    pp.user_id,
                    pp.display_name,
                    pp.avatar_url,
                    pp.phone_number,
                    pp.bank_account,
                    ps.tournaments_played,
                    ps.weekly_played,
                    ps.special_played,
                    ps.monthly_played,
                    ps.championships,
                    ps.runner_ups,
                    ps.third_places,
                    ps.podiums,
                    ps.total_wins,
                    ps.total_prize_money
                FROM player_profiles pp
                LEFT JOIN player_stats ps
                  ON ps.user_id = pp.user_id
                WHERE pp.user_id = ?
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_player_history(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    tournament_id,
                    season_name,
                    tournament_title,
                    final_rank,
                    prize_amount,
                    total_points,
                    recorded_at
                FROM player_tournament_results
                WHERE user_id = ?
                ORDER BY tournament_id DESC, final_rank ASC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def get_player_support_status(
        self,
        user_id: int,
        guild_id: int | None = None,
    ) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            if guild_id is None:
                cursor = await db.execute(
                    """
                    SELECT
                        donor_tier,
                        donor_expires_at,
                        sponsor_tier,
                        sponsor_expires_at
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
                    SELECT
                        donor_tier,
                        donor_expires_at,
                        sponsor_tier,
                        sponsor_expires_at
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

    async def get_latest_completed_weekly(self, guild_id: int) -> dict[str, Any] | None:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM tournaments
                WHERE guild_id = ?
                  AND type = 'weekly'
                  AND status = 'completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (guild_id,),
            )
            row = await cursor.fetchone()
            return _row_to_dict(row)
        finally:
            await db.close()

    async def get_tournament_podium(self, tournament_id: int) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    p.final_rank,
                    p.amount,
                    te.user_id,
                    te.display_name
                FROM payouts p
                JOIN tournament_entries te
                  ON te.id = p.entry_id
                WHERE p.tournament_id = ?
                ORDER BY p.final_rank ASC
                """,
                (tournament_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def get_final_stage_scoreboard(self, tournament_id: int) -> list[dict[str, Any]]:
        db = await get_db(self.db_path)
        try:
            cursor = await db.execute(
                """
                SELECT
                    ss.final_position,
                    ss.total_points,
                    te.user_id,
                    te.display_name
                FROM stage_slots ss
                JOIN stages s
                  ON s.id = ss.stage_id
                JOIN tournament_entries te
                  ON te.id = ss.current_entry_id
                WHERE s.tournament_id = ?
                  AND s.stage_type = 'final'
                ORDER BY ss.final_position ASC, ss.slot_no ASC
                """,
                (tournament_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()
