from __future__ import annotations

from typing import Any

from config.constants import BO_COUNT, FINAL_KEY, LOBBY_SIZE, SEMI_KEYS, ZONE_KEYS
from models.enums import StageType, TournamentFormatKey, TournamentGameKey, TournamentStatus, TournamentType
from repositories.entry_repo import EntryRepo
from repositories.stage_repo import StageRepo
from repositories.tournament_repo import TournamentRepo
from utils.randoms import generate_lobby_password, shuffled


class BracketService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.tournament_repo = TournamentRepo(db_path)
        self.entry_repo = EntryRepo(db_path)
        self.stage_repo = StageRepo(db_path)

    async def _get_active_autochess_weekly(self, guild_id: int) -> dict[str, Any] | None:
        tournaments = await self.tournament_repo.list_active_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )
        if not tournaments:
            return None

        status_priority = {
            TournamentStatus.FINAL_RUNNING.value: 90,
            TournamentStatus.FINAL_CREATED.value: 80,
            TournamentStatus.SEMIS_RUNNING.value: 70,
            TournamentStatus.SEMIS_CREATED.value: 60,
            TournamentStatus.ZONES_RUNNING.value: 50,
            TournamentStatus.ZONES_CREATED.value: 40,
            TournamentStatus.REGISTRATION_LOCKED.value: 30,
            TournamentStatus.REGISTRATION_OPEN.value: 20,
            TournamentStatus.DRAFT.value: 10,
        }
        return max(
            tournaments,
            key=lambda item: (
                status_priority.get(str(item.get("status") or ""), 0),
                int(item["id"]),
            ),
        )

    async def _pick_autochess_weekly_by_statuses(
        self,
        guild_id: int,
        statuses: tuple[str, ...],
        *,
        empty_message: str,
        ambiguous_message: str,
    ) -> dict[str, Any]:
        tournaments = await self.tournament_repo.list_active_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )
        candidates = [item for item in tournaments if str(item.get("status") or "") in statuses]
        if not candidates:
            raise ValueError(empty_message)
        if len(candidates) > 1:
            names = ", ".join(f"#{int(item['id'])} {item['title']}" for item in candidates[:5])
            raise ValueError(f"{ambiguous_message} Active: {names}")
        return candidates[0]

    async def _get_autochess_tournament_or_raise(self, tournament_id: int) -> dict[str, Any]:
        tournament = await self.tournament_repo.get_by_id(tournament_id)
        if tournament is None:
            raise ValueError("Tournament олдсонгүй.")
        if str(tournament.get("type") or "") != TournamentType.WEEKLY.value:
            raise ValueError("Зөвхөн weekly tournament дэмжинэ.")
        if str(tournament.get("game_key") or "") != TournamentGameKey.AUTOCHESS.value:
            raise ValueError("Зөвхөн Auto Chess tournament дэмжинэ.")
        if str(tournament.get("format_key") or "") != TournamentFormatKey.SOLO_32.value:
            raise ValueError("Зөвхөн 32-player solo tournament дэмжинэ.")
        return tournament

    async def _create_zones_for_tournament(self, tournament: dict[str, Any]) -> dict[str, Any]:
        if tournament["status"] not in (
            TournamentStatus.REGISTRATION_OPEN.value,
            TournamentStatus.REGISTRATION_LOCKED.value,
            TournamentStatus.ZONES_CREATED.value,
        ):
            raise ValueError(
                "Zone үүсгэхийн тулд tournament registration_open эсвэл registration_locked байх ёстой."
            )

        existing_zones = await self.stage_repo.list_stages(
            tournament["id"],
            stage_type=StageType.ZONE.value,
        )
        if existing_zones:
            raise ValueError("Zone-ууд өмнө нь үүссэн байна.")

        confirmed_entries = await self.entry_repo.list_entries(
            tournament["id"],
            statuses=["confirmed"],
        )

        max_players = int(tournament["max_players"])
        lobby_size = int(tournament["lobby_size"])
        if len(confirmed_entries) != max_players:
            raise ValueError(
                f"Zone үүсгэхийн тулд яг {max_players} confirmed player хэрэгтэй. "
                f"Одоо {len(confirmed_entries)} байна."
            )

        randomized_entries = shuffled(confirmed_entries)
        zones: list[dict[str, Any]] = []

        for zone_index, stage_key in enumerate(ZONE_KEYS):
            start = zone_index * lobby_size
            end = start + lobby_size
            zone_entries = randomized_entries[start:end]
            if len(zone_entries) != LOBBY_SIZE:
                raise ValueError(f"{stage_key} дээр 8 хүн дутуу байна.")

            host_entry = shuffled(zone_entries)[0]
            lobby_password = generate_lobby_password(4)

            stage_id = await self.stage_repo.create_stage(
                tournament_id=tournament["id"],
                stage_key=stage_key,
                stage_type=StageType.ZONE.value,
                round_order=1,
                lobby_password=lobby_password,
                host_user_id=int(host_entry["user_id"]),
                game_count=BO_COUNT,
                status="ready",
            )

            for slot_no, entry in enumerate(zone_entries, start=1):
                await self.stage_repo.add_stage_slot(
                    stage_id=stage_id,
                    slot_no=slot_no,
                    original_entry_id=int(entry["id"]),
                    current_entry_id=int(entry["id"]),
                )

            for game_no in range(1, BO_COUNT + 1):
                await self.stage_repo.create_game(stage_id=stage_id, game_no=game_no, status="pending")

            stage = await self.stage_repo.get_stage_by_key(tournament["id"], stage_key)
            slots = await self.stage_repo.list_stage_slots_with_entries(stage_id)
            zones.append(
                {
                    "stage": stage,
                    "slots": slots,
                    "host_user_id": int(host_entry["user_id"]),
                    "password": lobby_password,
                }
            )

        await self.tournament_repo.update_status(tournament["id"], TournamentStatus.ZONES_CREATED.value)
        updated_tournament = await self.tournament_repo.get_by_id(tournament["id"])
        return {"tournament": updated_tournament, "zones": zones}

    async def _create_semis_for_tournament(self, tournament: dict[str, Any]) -> dict[str, Any]:
        existing_semis = await self.stage_repo.list_stages(
            tournament["id"],
            stage_type=StageType.SEMI.value,
        )
        if existing_semis:
            raise ValueError("Semifinal stage өмнө нь үүссэн байна.")

        zone_stages = await self.stage_repo.list_stages(
            tournament["id"],
            stage_type=StageType.ZONE.value,
        )
        if len(zone_stages) != 4:
            raise ValueError("Эхлээд 4 zone үүссэн байх ёстой.")

        unfinished_zones = [item for item in zone_stages if item["status"] != "finished"]
        if unfinished_zones:
            names = ", ".join(item["stage_key"] for item in unfinished_zones)
            raise ValueError(f"Эдгээр zone дуусаагүй байна: {names}")

        qualified_slots = await self.stage_repo.list_qualified_slots_by_stage_type(
            tournament["id"],
            StageType.ZONE.value,
        )
        if len(qualified_slots) != 16:
            raise ValueError(
                f"Semi үүсгэхийн тулд zone-оос яг 16 qualified хэрэгтэй. "
                f"Одоо {len(qualified_slots)} байна."
            )

        randomized_slots = shuffled(qualified_slots)
        semis: list[dict[str, Any]] = []

        for semi_index, stage_key in enumerate(SEMI_KEYS):
            start = semi_index * LOBBY_SIZE
            end = start + LOBBY_SIZE
            semi_entries = randomized_slots[start:end]
            if len(semi_entries) != 8:
                raise ValueError(f"{stage_key} дээр 8 qualified дутуу байна.")

            host_entry = shuffled(semi_entries)[0]
            lobby_password = generate_lobby_password(4)

            stage_id = await self.stage_repo.create_stage(
                tournament_id=tournament["id"],
                stage_key=stage_key,
                stage_type=StageType.SEMI.value,
                round_order=2,
                lobby_password=lobby_password,
                host_user_id=int(host_entry["user_id"]),
                game_count=BO_COUNT,
                status="ready",
            )

            for slot_no, entry in enumerate(semi_entries, start=1):
                await self.stage_repo.add_stage_slot(
                    stage_id=stage_id,
                    slot_no=slot_no,
                    original_entry_id=int(entry["original_entry_id"]),
                    current_entry_id=int(entry["current_entry_id"]),
                    inherited_from_slot_id=int(entry["source_stage_slot_id"]),
                )

            for game_no in range(1, BO_COUNT + 1):
                await self.stage_repo.create_game(stage_id=stage_id, game_no=game_no, status="pending")

            stage = await self.stage_repo.get_stage_by_key(tournament["id"], stage_key)
            slots = await self.stage_repo.list_stage_slots_with_entries(stage_id)
            semis.append(
                {
                    "stage": stage,
                    "slots": slots,
                    "host_user_id": int(host_entry["user_id"]),
                    "password": lobby_password,
                }
            )

        await self.tournament_repo.update_status(tournament["id"], TournamentStatus.SEMIS_CREATED.value)
        updated_tournament = await self.tournament_repo.get_by_id(tournament["id"])
        return {"tournament": updated_tournament, "semis": semis}

    async def _create_final_for_tournament(self, tournament: dict[str, Any]) -> dict[str, Any]:
        existing_final = await self.stage_repo.get_stage_by_key(tournament["id"], FINAL_KEY)
        if existing_final:
            raise ValueError("Grand final өмнө нь үүссэн байна.")

        semi_stages = await self.stage_repo.list_stages(
            tournament["id"],
            stage_type=StageType.SEMI.value,
        )
        if len(semi_stages) != 2:
            raise ValueError("Эхлээд 2 semifinal stage үүссэн байх ёстой.")

        unfinished_semis = [item for item in semi_stages if item["status"] != "finished"]
        if unfinished_semis:
            names = ", ".join(item["stage_key"] for item in unfinished_semis)
            raise ValueError(f"Эдгээр semifinal дуусаагүй байна: {names}")

        qualified_slots = await self.stage_repo.list_qualified_slots_by_stage_type(
            tournament["id"],
            StageType.SEMI.value,
        )
        if len(qualified_slots) != 8:
            raise ValueError(
                f"Final үүсгэхийн тулд semi-оос яг 8 qualified хэрэгтэй. "
                f"Одоо {len(qualified_slots)} байна."
            )

        final_entries = shuffled(qualified_slots)
        host_entry = shuffled(final_entries)[0]
        lobby_password = generate_lobby_password(4)

        stage_id = await self.stage_repo.create_stage(
            tournament_id=tournament["id"],
            stage_key=FINAL_KEY,
            stage_type=StageType.FINAL.value,
            round_order=3,
            lobby_password=lobby_password,
            host_user_id=int(host_entry["user_id"]),
            game_count=BO_COUNT,
            status="ready",
        )

        for slot_no, entry in enumerate(final_entries, start=1):
            await self.stage_repo.add_stage_slot(
                stage_id=stage_id,
                slot_no=slot_no,
                original_entry_id=int(entry["original_entry_id"]),
                current_entry_id=int(entry["current_entry_id"]),
                inherited_from_slot_id=int(entry["source_stage_slot_id"]),
            )

        for game_no in range(1, BO_COUNT + 1):
            await self.stage_repo.create_game(stage_id=stage_id, game_no=game_no, status="pending")

        stage = await self.stage_repo.get_stage_by_key(tournament["id"], FINAL_KEY)
        slots = await self.stage_repo.list_stage_slots_with_entries(stage_id)

        await self.tournament_repo.update_status(tournament["id"], TournamentStatus.FINAL_CREATED.value)
        updated_tournament = await self.tournament_repo.get_by_id(tournament["id"])
        return {
            "tournament": updated_tournament,
            "final": {
                "stage": stage,
                "slots": slots,
                "host_user_id": int(host_entry["user_id"]),
                "password": lobby_password,
            },
        }

    async def create_weekly_zones(self, guild_id: int) -> dict[str, Any]:
        tournament = await self._get_active_autochess_weekly(guild_id)
        if tournament is None:
            raise ValueError("Идэвхтэй weekly tournament алга.")
        return await self._create_zones_for_tournament(tournament)

    async def create_weekly_semis(self, guild_id: int) -> dict[str, Any]:
        tournament = await self._get_active_autochess_weekly(guild_id)
        if tournament is None:
            raise ValueError("Идэвхтэй weekly tournament алга.")
        return await self._create_semis_for_tournament(tournament)

    async def create_weekly_final(self, guild_id: int) -> dict[str, Any]:
        tournament = await self._get_active_autochess_weekly(guild_id)
        if tournament is None:
            raise ValueError("Идэвхтэй weekly tournament алга.")
        return await self._create_final_for_tournament(tournament)

    async def create_weekly_zones_for_tournament(self, tournament_id: int) -> dict[str, Any]:
        tournament = await self._get_autochess_tournament_or_raise(tournament_id)
        return await self._create_zones_for_tournament(tournament)

    async def create_weekly_semis_for_tournament(self, tournament_id: int) -> dict[str, Any]:
        tournament = await self._get_autochess_tournament_or_raise(tournament_id)
        return await self._create_semis_for_tournament(tournament)

    async def create_weekly_final_for_tournament(self, tournament_id: int) -> dict[str, Any]:
        tournament = await self._get_autochess_tournament_or_raise(tournament_id)
        return await self._create_final_for_tournament(tournament)
