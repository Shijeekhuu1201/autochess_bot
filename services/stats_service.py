from __future__ import annotations

from typing import Any

from repositories.stats_repo import StatsRepo
from repositories.tournament_repo import TournamentRepo


class StatsService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.stats_repo = StatsRepo(db_path)
        self.tournament_repo = TournamentRepo(db_path)

    async def apply_final_payouts_and_stats(
        self,
        tournament: dict[str, Any],
        final_scoreboard: list[dict[str, Any]],
    ) -> None:
        tournament_id = int(tournament["id"])

        already_done = await self.stats_repo.tournament_results_exist(tournament_id)
        if already_done:
            return

        prize_map = {
            1: int(tournament.get("prize_1", 0) or 0),
            2: int(tournament.get("prize_2", 0) or 0),
            3: int(tournament.get("prize_3", 0) or 0),
        }

        season_name = str(tournament.get("season_name") or "Season 1")
        tournament_title = str(tournament.get("title") or f"Tournament #{tournament_id}")

        for index, row in enumerate(final_scoreboard, start=1):
            user_id = int(row["user_id"])
            display_name = str(row["display_name"])
            entry_id = int(row["entry_id"])
            total_points = int(row["total_points"])
            prize_amount = prize_map.get(index, 0)

            await self.stats_repo.upsert_player_profile(user_id, display_name)
            await self.stats_repo.ensure_player_stats_row(user_id)
            await self.stats_repo.increment_player_stats(
                user_id=user_id,
                tournament_type=str(tournament["type"]),
                final_rank=index,
                prize_amount=prize_amount,
            )

            if prize_amount > 0:
                await self.stats_repo.create_payout(
                    tournament_id=tournament_id,
                    entry_id=entry_id,
                    final_rank=index,
                    amount=prize_amount,
                )

            await self.stats_repo.save_player_tournament_result(
                tournament_id=tournament_id,
                user_id=user_id,
                display_name=display_name,
                season_name=season_name,
                tournament_title=tournament_title,
                final_rank=index,
                prize_amount=prize_amount,
                total_points=total_points,
            )

    async def get_leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self.stats_repo.get_top_leaderboard(limit=limit)

    async def get_player_profile(self, user_id: int) -> dict[str, Any] | None:
        return await self.stats_repo.get_player_stats(user_id)

    async def update_player_contact(
        self,
        user_id: int,
        display_name: str,
        *,
        phone_number: str | None = None,
        bank_account: str | None = None,
    ) -> None:
        await self.stats_repo.update_player_contact(
            user_id,
            display_name,
            phone_number=phone_number,
            bank_account=bank_account,
        )

    async def get_player_history(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        return await self.stats_repo.get_player_history(user_id, limit=limit)

    async def get_player_support_status(
        self,
        user_id: int,
        guild_id: int | None = None,
    ) -> dict[str, Any] | None:
        return await self.stats_repo.get_player_support_status(user_id, guild_id=guild_id)

    async def get_latest_weekly_winner_snapshot(self, guild_id: int) -> dict[str, Any]:
        tournament = await self.stats_repo.get_latest_completed_weekly(guild_id)
        if tournament is None:
            raise ValueError("Completed weekly tournament олдсонгүй.")

        podium = await self.stats_repo.get_tournament_podium(int(tournament["id"]))
        standings = await self.stats_repo.get_final_stage_scoreboard(int(tournament["id"]))

        return {
            "tournament": tournament,
            "podium": podium,
            "standings": standings,
        }
