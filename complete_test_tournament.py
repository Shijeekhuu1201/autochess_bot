from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from config.constants import FINAL_KEY, SEMI_KEYS, ZONE_KEYS
from config.settings import SETTINGS
from core.db import init_db
from models.enums import StageType, TournamentFormatKey, TournamentGameKey, TournamentType
from repositories.result_repo import ResultRepo
from repositories.stage_repo import StageRepo
from repositories.tournament_repo import TournamentRepo
from services.bracket_service import BracketService
from services.result_service import ResultService

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / SETTINGS.db_path


def build_stage_order(slots: list[dict], game_no: int) -> list[int]:
    ordered = sorted(slots, key=lambda item: int(item["slot_no"]))
    if game_no % 2 == 0:
        # Slightly shuffle the second game while keeping the same 8 players.
        ordered = ordered[1:] + ordered[:1]
    return [int(item["user_id"]) for item in ordered]


async def ensure_zones(
    guild_id: int,
    tournament: dict,
    bracket_service: BracketService,
    stage_repo: StageRepo,
) -> list[dict]:
    zones = await stage_repo.list_stages(int(tournament["id"]), stage_type=StageType.ZONE.value)
    if zones:
        return zones

    result = await bracket_service.create_weekly_zones(guild_id)
    return [item["stage"] for item in result["zones"]]


async def ensure_semis(
    guild_id: int,
    tournament: dict,
    bracket_service: BracketService,
    stage_repo: StageRepo,
) -> list[dict]:
    semis = await stage_repo.list_stages(int(tournament["id"]), stage_type=StageType.SEMI.value)
    if semis:
        return semis

    result = await bracket_service.create_weekly_semis(guild_id)
    return [item["stage"] for item in result["semis"]]


async def ensure_final(
    guild_id: int,
    tournament: dict,
    bracket_service: BracketService,
    stage_repo: StageRepo,
) -> dict:
    final_stage = await stage_repo.get_stage_by_key(int(tournament["id"]), FINAL_KEY)
    if final_stage is not None:
        return final_stage

    result = await bracket_service.create_weekly_final(guild_id)
    return result["final"]["stage"]


async def complete_stage_if_needed(
    guild_id: int,
    tournament_id: int,
    stage_key: str,
    result_service: ResultService,
    result_repo: ResultRepo,
) -> dict:
    stage = await result_repo.get_stage_by_key(int(tournament_id), stage_key)
    if stage is None:
        raise RuntimeError(f"Stage not found: {stage_key}")

    slots = await result_repo.list_stage_slots_with_entries(int(stage["id"]))
    if len(slots) != 8:
        raise RuntimeError(f"{stage_key} дээр 8 slot бүрдээгүй байна.")

    for game_no in range(1, int(stage["game_count"]) + 1):
        game = await result_repo.get_game(int(stage["id"]), game_no)
        if game is None:
            raise RuntimeError(f"{stage_key} game {game_no} олдсонгүй.")
        if str(game["status"]) == "confirmed":
            continue

        ordered_user_ids = build_stage_order(slots, game_no)
        await result_service.submit_stage_result(
            guild_id=guild_id,
            stage_key=stage_key,
            game_no=game_no,
            ordered_user_ids=ordered_user_ids,
        )

    return await result_service.get_stage_results(guild_id, stage_key)


async def complete_tournament(guild_id: int) -> None:
    await init_db(str(DB_PATH))

    tournament_repo = TournamentRepo(str(DB_PATH))
    stage_repo = StageRepo(str(DB_PATH))
    result_repo = ResultRepo(str(DB_PATH))
    bracket_service = BracketService(str(DB_PATH))
    result_service = ResultService(str(DB_PATH))

    tournament = await tournament_repo.get_latest_active_by_scope(
        guild_id,
        TournamentType.WEEKLY.value,
        game_key=TournamentGameKey.AUTOCHESS.value,
        format_key=TournamentFormatKey.SOLO_32.value,
    )
    if tournament is None:
        raise RuntimeError("Active weekly tournament алга. Эхлээд seed_test_tournament.py ажиллуул.")

    print(f"[INFO] Tournament #{tournament['id']} | {tournament['title']} | {tournament['status']}")

    await ensure_zones(guild_id, tournament, bracket_service, stage_repo)
    print("[OK] Zones ready")

    for stage_key in ZONE_KEYS:
        result = await complete_stage_if_needed(
            guild_id,
            int(tournament["id"]),
            stage_key,
            result_service,
            result_repo,
        )
        leader = result["scoreboard"][0]["display_name"] if result["scoreboard"] else "-"
        print(f"[OK] {stage_key} finished | leader: {leader}")

    await ensure_semis(guild_id, tournament, bracket_service, stage_repo)
    print("[OK] Semis ready")

    for stage_key in SEMI_KEYS:
        result = await complete_stage_if_needed(
            guild_id,
            int(tournament["id"]),
            stage_key,
            result_service,
            result_repo,
        )
        leader = result["scoreboard"][0]["display_name"] if result["scoreboard"] else "-"
        print(f"[OK] {stage_key} finished | leader: {leader}")

    await ensure_final(guild_id, tournament, bracket_service, stage_repo)
    print("[OK] Grand final ready")

    final_result = await complete_stage_if_needed(
        guild_id,
        int(tournament["id"]),
        FINAL_KEY,
        result_service,
        result_repo,
    )
    print("[OK] Grand final finished")

    refreshed = await tournament_repo.get_latest_by_scope(
        guild_id,
        TournamentType.WEEKLY.value,
        game_key=TournamentGameKey.AUTOCHESS.value,
        format_key=TournamentFormatKey.SOLO_32.value,
    )
    winner = final_result["scoreboard"][0] if final_result["scoreboard"] else None
    print("")
    print(f"Status: {refreshed['status'] if refreshed else 'unknown'}")
    if winner is not None:
        print(
            f"Winner: {winner['display_name']} | "
            f"Points: {winner['total_points']} | "
            f"Season: {refreshed.get('season_name') if refreshed else '-'}"
        )
    print("Check in Discord:")
    print(".winner")
    print(".leaderboard")
    print(".profile @TestPlayer01")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-complete the latest active weekly test tournament.",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=SETTINGS.guild_id,
        help="Discord guild id. Defaults to GUILD_ID from .env",
    )
    args = parser.parse_args()

    if args.guild_id <= 0:
        raise SystemExit("GUILD_ID алга. --guild-id өг эсвэл .env дээр GUILD_ID тохируул.")

    asyncio.run(complete_tournament(args.guild_id))


if __name__ == "__main__":
    main()
