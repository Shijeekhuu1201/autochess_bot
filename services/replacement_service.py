from __future__ import annotations

from typing import Any

from models.enums import TournamentFormatKey, TournamentGameKey, TournamentType
from repositories.replacement_repo import ReplacementRepo
from repositories.tournament_repo import TournamentRepo


class ReplacementService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.tournament_repo = TournamentRepo(db_path)
        self.replacement_repo = ReplacementRepo(db_path)

    async def _get_active_autochess_weekly(self, guild_id: int) -> dict[str, Any] | None:
        return await self.tournament_repo.get_latest_active_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )

    async def replace_player(
        self,
        guild_id: int,
        stage_key: str,
        old_user_id: int,
        new_user_id: int,
        new_display_name: str,
        created_by: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        tournament = await self._get_active_autochess_weekly(guild_id)
        if tournament is None:
            raise ValueError("Идэвхтэй weekly tournament алга.")

        stage = await self.replacement_repo.get_stage_by_key(
            int(tournament["id"]),
            stage_key.lower(),
        )
        if stage is None:
            raise ValueError(f"`{stage_key}` stage олдсонгүй.")

        if old_user_id == new_user_id:
            raise ValueError("Солигдох хүн, орж ирэх хүн ижил байж болохгүй.")

        old_slot = await self.replacement_repo.get_stage_slot_by_current_user(
            int(stage["id"]),
            int(old_user_id),
        )
        if old_slot is None:
            raise ValueError("Тэр тоглогч энэ stage дээр одоогийн slot эзэмшиж алга.")

        existing_new_entry = await self.replacement_repo.get_entry_by_user(
            int(tournament["id"]),
            int(new_user_id),
        )
        if existing_new_entry is not None:
            raise ValueError(
                "Шинэ орж ирэх хүн энэ tournament-д аль хэдийн бүртгэлтэй байна."
            )

        confirmed_games = await self.replacement_repo.count_confirmed_games(int(stage["id"]))
        next_game_no = confirmed_games + 1

        if stage["status"] == "finished":
            # Stage дууссан бол replacement нь дараагийн inheritance-д хүчинтэй.
            applied_before_game_no = int(stage["game_count"]) + 1
        else:
            if confirmed_games >= int(stage["game_count"]):
                raise ValueError("Энэ stage бүх тоглолтоо дуусгасан байна.")
            applied_before_game_no = next_game_no

        register_order = await self.replacement_repo.get_next_register_order(
            int(tournament["id"])
        )

        new_entry_id = await self.replacement_repo.create_replacement_entry(
            tournament_id=int(tournament["id"]),
            user_id=int(new_user_id),
            display_name=new_display_name,
            register_order=register_order,
            replacement_for_entry_id=int(old_slot["entry_id"]),
        )

        await self.replacement_repo.update_entry_status(
            entry_id=int(old_slot["entry_id"]),
            status="replaced_out",
            payment_status="confirmed",
        )

        await self.replacement_repo.update_stage_slot_current_entry(
            stage_slot_id=int(old_slot["stage_slot_id"]),
            new_entry_id=int(new_entry_id),
        )

        await self.replacement_repo.create_replacement_log(
            tournament_id=int(tournament["id"]),
            stage_id=int(stage["id"]),
            stage_slot_id=int(old_slot["stage_slot_id"]),
            out_entry_id=int(old_slot["entry_id"]),
            in_entry_id=int(new_entry_id),
            applied_before_game_no=int(applied_before_game_no),
            created_by=int(created_by),
            reason=reason,
        )

        updated_slot = await self.replacement_repo.get_stage_slot_snapshot(
            int(old_slot["stage_slot_id"])
        )
        if updated_slot is None:
            raise ValueError("Replacement хийсний дараах slot мэдээлэл олдсонгүй.")

        return {
            "tournament": tournament,
            "stage": stage,
            "old_user_id": int(old_user_id),
            "new_user_id": int(new_user_id),
            "applied_before_game_no": int(applied_before_game_no),
            "slot": updated_slot,
            "stage_finished": stage["status"] == "finished",
            "reason": reason or "",
        }

    async def replace_player(
        self,
        guild_id: int,
        stage_key: str,
        old_user_id: int,
        new_user_id: int,
        new_display_name: str,
        created_by: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        tournament = await self.tournament_repo.get_latest_by_stage_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            stage_key.lower(),
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )
        if tournament is None:
            raise ValueError("Тухайн stage-тэй идэвхтэй Chess tournament алга.")

        stage = await self.replacement_repo.get_stage_by_key(
            int(tournament["id"]),
            stage_key.lower(),
        )
        if stage is None:
            raise ValueError(f"`{stage_key}` stage олдсонгүй.")
        if old_user_id == new_user_id:
            raise ValueError("Солигдох хүн, орж ирэх хүн ижил байж болохгүй.")

        old_slot = await self.replacement_repo.get_stage_slot_by_current_user(
            int(stage["id"]),
            int(old_user_id),
        )
        if old_slot is None:
            raise ValueError("Тэр тоглогч энэ stage дээр одоогийн slot эзэмшиж алга.")

        existing_new_entry = await self.replacement_repo.get_entry_by_user(
            int(tournament["id"]),
            int(new_user_id),
        )
        if existing_new_entry is not None:
            raise ValueError("Шинэ орж ирэх хүн энэ tournament-д аль хэдийн бүртгэлтэй байна.")

        confirmed_games = await self.replacement_repo.count_confirmed_games(int(stage["id"]))
        next_game_no = confirmed_games + 1
        if stage["status"] == "finished":
            applied_before_game_no = int(stage["game_count"]) + 1
        else:
            if confirmed_games >= int(stage["game_count"]):
                raise ValueError("Энэ stage бүх тоглолтоо дуусгасан байна.")
            applied_before_game_no = next_game_no

        register_order = await self.replacement_repo.get_next_register_order(int(tournament["id"]))
        new_entry_id = await self.replacement_repo.create_replacement_entry(
            tournament_id=int(tournament["id"]),
            user_id=int(new_user_id),
            display_name=new_display_name,
            register_order=register_order,
            replacement_for_entry_id=int(old_slot["entry_id"]),
        )

        await self.replacement_repo.update_entry_status(
            entry_id=int(old_slot["entry_id"]),
            status="replaced_out",
            payment_status="confirmed",
        )
        await self.replacement_repo.update_stage_slot_current_entry(
            stage_slot_id=int(old_slot["stage_slot_id"]),
            new_entry_id=int(new_entry_id),
        )
        await self.replacement_repo.create_replacement_log(
            tournament_id=int(tournament["id"]),
            stage_id=int(stage["id"]),
            stage_slot_id=int(old_slot["stage_slot_id"]),
            out_entry_id=int(old_slot["entry_id"]),
            in_entry_id=int(new_entry_id),
            applied_before_game_no=int(applied_before_game_no),
            created_by=int(created_by),
            reason=reason,
        )

        updated_slot = await self.replacement_repo.get_stage_slot_snapshot(int(old_slot["stage_slot_id"]))
        if updated_slot is None:
            raise ValueError("Replacement хийсний дараах slot мэдээлэл олдсонгүй.")

        return {
            "tournament": tournament,
            "stage": stage,
            "old_user_id": int(old_user_id),
            "new_user_id": int(new_user_id),
            "applied_before_game_no": int(applied_before_game_no),
            "slot": updated_slot,
            "stage_finished": stage["status"] == "finished",
            "reason": reason or "",
        }
