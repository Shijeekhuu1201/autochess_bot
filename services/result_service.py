from __future__ import annotations

from collections import Counter
from typing import Any

from config.constants import SCORE_MAP_8
from models.enums import TournamentFormatKey, TournamentGameKey, TournamentType
from repositories.result_repo import ResultRepo
from repositories.tournament_repo import TournamentRepo
from services.stats_service import StatsService


class ResultService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.tournament_repo = TournamentRepo(db_path)
        self.result_repo = ResultRepo(db_path)
        self.stats_service = StatsService(db_path)

    async def _get_active_autochess_weekly(self, guild_id: int) -> dict[str, Any] | None:
        return await self.tournament_repo.get_latest_active_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )

    async def _get_latest_autochess_weekly(self, guild_id: int) -> dict[str, Any] | None:
        return await self.tournament_repo.get_latest_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )

    def _sort_scoreboard(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda x: (
                -int(x["total_points"]),
                -max(int(x["game1_points"]), int(x["game2_points"])),
                -int(x["game2_points"]),
                int(x["slot_no"]),
            ),
        )

    async def _resolve_tournament_for_stage(
        self,
        guild_id: int,
        stage_key: str,
    ) -> dict[str, Any]:
        active = await self._get_active_autochess_weekly(guild_id)

        if active is not None:
            stage = await self.result_repo.get_stage_by_key(
                int(active["id"]),
                stage_key,
            )
            if stage is not None:
                return active

            raise ValueError(
                f"Идэвхтэй weekly tournament байна, гэхдээ `{stage_key}` stage хараахан үүсээгүй байна."
            )

        by_stage = await self.tournament_repo.get_latest_by_stage_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            stage_key,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )
        if by_stage is not None:
            return by_stage

        latest = await self._get_latest_autochess_weekly(guild_id)
        if latest is not None:
            stage = await self.result_repo.get_stage_by_key(
                int(latest["id"]),
                stage_key,
            )
            if stage is not None:
                return latest

            raise ValueError(f"Weekly tournament олдсон ч `{stage_key}` stage алга.")

        raise ValueError("Идэвхтэй weekly tournament алга.")

    async def _resolve_game_no(
        self,
        stage: dict[str, Any],
        game_no: int | None,
    ) -> int:
        if game_no is not None:
            if game_no < 1 or game_no > int(stage["game_count"]):
                raise ValueError(
                    f"Game number 1-с {int(stage['game_count'])} хүртэл байх ёстой."
                )
            return int(game_no)

        confirmed_games = await self.result_repo.count_confirmed_games(int(stage["id"]))
        next_game = confirmed_games + 1
        if next_game > int(stage["game_count"]):
            raise ValueError(f"`{stage['stage_key']}` stage-ийн бүх game аль хэдийн хадгалагдсан байна.")
        return next_game

    async def submit_stage_result(
        self,
        guild_id: int,
        stage_key: str,
        game_no: int | None,
        ordered_user_ids: list[int],
    ) -> dict[str, Any]:
        tournament = await self._resolve_tournament_for_stage(
            guild_id,
            stage_key,
        )

        stage = await self.result_repo.get_stage_by_key(
            int(tournament["id"]),
            stage_key,
        )
        if stage is None:
            raise ValueError(f"`{stage_key}` stage олдсонгүй.")

        if stage["status"] == "finished":
            raise ValueError(f"`{stage_key}` stage аль хэдийн дууссан байна.")

        actual_game_no = await self._resolve_game_no(stage, game_no)

        if len(ordered_user_ids) != 8:
            raise ValueError(f"Яг 8 тоглогч mention хийх ёстой. Одоо {len(ordered_user_ids)} байна.")

        counts = Counter(int(x) for x in ordered_user_ids)
        duplicate_ids = [user_id for user_id, count in counts.items() if count > 1]
        if duplicate_ids:
            duplicate_mentions = ", ".join(f"<@{user_id}>" for user_id in duplicate_ids)
            raise ValueError(f"Давхардсан тоглогч байна: {duplicate_mentions}")

        slots = await self.result_repo.list_stage_slots_with_entries(int(stage["id"]))
        if len(slots) != 8:
            raise ValueError("Энэ stage дээр 8 slot бүрдээгүй байна.")

        stage_user_ids = {int(slot["user_id"]) for slot in slots}
        input_user_ids = set(int(x) for x in ordered_user_ids)

        if input_user_ids != stage_user_ids:
            missing = sorted(stage_user_ids - input_user_ids)
            extra = sorted(input_user_ids - stage_user_ids)

            parts: list[str] = []
            if missing:
                parts.append("Дутуу: " + ", ".join(f"<@{user_id}>" for user_id in missing))
            if extra:
                parts.append("Илүү: " + ", ".join(f"<@{user_id}>" for user_id in extra))

            extra_text = " | ".join(parts)
            raise ValueError(
                f"Оруулсан 8 тоглогч энэ stage-ийн яг тоглогчидтой таарахгүй байна. {extra_text}"
            )

        slot_by_user_id = {
            int(slot["user_id"]): int(slot["stage_slot_id"])
            for slot in slots
        }
        ordered_stage_slot_ids = [slot_by_user_id[int(user_id)] for user_id in ordered_user_ids]

        game = await self.result_repo.get_game(int(stage["id"]), actual_game_no)
        if game is None:
            raise ValueError(f"{stage_key} stage дээр game {actual_game_no} олдсонгүй.")

        await self.result_repo.replace_game_results(
            int(game["id"]),
            ordered_stage_slot_ids,
            SCORE_MAP_8,
        )
        await self.result_repo.recalculate_stage_totals(int(stage["id"]))

        confirmed_games = await self.result_repo.count_confirmed_games(int(stage["id"]))
        scoreboard = await self.result_repo.list_stage_scoreboard(int(stage["id"]))
        ordered_scoreboard = self._sort_scoreboard(scoreboard)

        stage_finished = confirmed_games >= int(stage["game_count"])
        qualify_count = 0 if stage["stage_type"] == "final" else 4

        if stage_finished:
            await self.result_repo.update_stage_rankings(
                int(stage["id"]),
                [int(x["stage_slot_id"]) for x in ordered_scoreboard],
                qualify_count=qualify_count,
            )
            await self.result_repo.update_stage_status(int(stage["id"]), "finished")

            if stage["stage_type"] == "final":
                await self.tournament_repo.update_status(
                    int(tournament["id"]),
                    "completed",
                )
                tournament = await self.tournament_repo.get_by_id(int(tournament["id"])) or tournament

                refreshed_scoreboard = await self.result_repo.list_stage_scoreboard(int(stage["id"]))
                ordered_scoreboard = self._sort_scoreboard(refreshed_scoreboard)

                await self.stats_service.apply_final_payouts_and_stats(
                    tournament=tournament,
                    final_scoreboard=ordered_scoreboard,
                )

            scoreboard = await self.result_repo.list_stage_scoreboard(int(stage["id"]))
            ordered_scoreboard = self._sort_scoreboard(scoreboard)
        else:
            await self.result_repo.update_stage_status(int(stage["id"]), "running")

        return {
            "tournament": tournament,
            "stage_key": stage_key,
            "stage_type": stage["stage_type"],
            "game_no": int(actual_game_no),
            "confirmed_games": confirmed_games,
            "game_count": int(stage["game_count"]),
            "stage_finished": stage_finished,
            "qualify_count": qualify_count,
            "scoreboard": ordered_scoreboard,
        }

    async def get_stage_results(
        self,
        guild_id: int,
        stage_key: str,
    ) -> dict[str, Any]:
        tournament = await self._resolve_tournament_for_stage(
            guild_id,
            stage_key,
        )

        stage = await self.result_repo.get_stage_by_key(
            int(tournament["id"]),
            stage_key,
        )
        if stage is None:
            raise ValueError(f"`{stage_key}` stage олдсонгүй.")

        scoreboard = await self.result_repo.list_stage_scoreboard(int(stage["id"]))
        ordered_scoreboard = self._sort_scoreboard(scoreboard)
        confirmed_games = await self.result_repo.count_confirmed_games(int(stage["id"]))

        return {
            "tournament": tournament,
            "stage_key": stage_key,
            "stage_type": stage["stage_type"],
            "confirmed_games": confirmed_games,
            "game_count": int(stage["game_count"]),
            "stage_finished": stage["status"] == "finished",
            "qualify_count": 0 if stage["stage_type"] == "final" else 4,
            "scoreboard": ordered_scoreboard,
        }
