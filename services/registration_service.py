from __future__ import annotations

import re
from typing import Any

from config.constants import MAX_PLAYERS
from models.enums import (
    EntryStatus,
    PaymentStatus,
    TournamentFormatKey,
    TournamentGameKey,
    TournamentStatus,
    TournamentType,
)
from repositories.entry_repo import EntryRepo
from repositories.stage_repo import StageRepo
from repositories.tournament_repo import TournamentRepo


class RegistrationService:
    DEFAULT_WEEKLY_ENTRY_FEE = 50_000
    DEFAULT_WEEKLY_PRIZE_TOTAL = 1_600_000
    DEFAULT_WEEKLY_PRIZE_1 = 800_000
    DEFAULT_WEEKLY_PRIZE_2 = 500_000
    DEFAULT_WEEKLY_PRIZE_3 = 300_000

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.tournament_repo = TournamentRepo(db_path)
        self.entry_repo = EntryRepo(db_path)
        self.stage_repo = StageRepo(db_path)

    def _slugify(self, value: str) -> str:
        lowered = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
        return lowered.strip("-")

    def _format_tournament_choices(self, tournaments: list[dict[str, Any]]) -> str:
        return ", ".join(
            f"#{int(item['id'])} {str(item.get('title') or 'Untitled').strip()}"
            for item in tournaments[:5]
        )

    def _is_registration_stage(self, tournament: dict[str, Any]) -> bool:
        return str(tournament.get("status") or "") in {
            TournamentStatus.REGISTRATION_OPEN.value,
            TournamentStatus.REGISTRATION_LOCKED.value,
        }

    async def _get_active_autochess_weekly(self, guild_id: int) -> dict[str, Any] | None:
        return await self.tournament_repo.get_latest_active_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )

    async def _list_active_autochess_weeklies(self, guild_id: int) -> list[dict[str, Any]]:
        return await self.tournament_repo.list_active_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )

    async def _list_registration_autochess_weeklies(self, guild_id: int) -> list[dict[str, Any]]:
        tournaments = await self._list_active_autochess_weeklies(guild_id)
        filtered = [item for item in tournaments if self._is_registration_stage(item)]
        return sorted(filtered, key=lambda item: int(item["id"]), reverse=True)

    async def _get_open_autochess_weekly(self, guild_id: int) -> dict[str, Any] | None:
        return await self.tournament_repo.get_latest_open_registration_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )

    async def _get_default_registration_weekly(self, guild_id: int) -> dict[str, Any]:
        tournaments = await self._list_registration_autochess_weeklies(guild_id)
        if not tournaments:
            raise ValueError("Идэвхтэй registration-stage Chess tournament алга.")
        return tournaments[0]

    async def _require_single_active_autochess_weekly(self, guild_id: int) -> dict[str, Any]:
        tournaments = await self._list_active_autochess_weeklies(guild_id)
        if not tournaments:
            raise ValueError("Идэвхтэй Auto Chess weekly tournament алга.")
        if len(tournaments) > 1:
            raise ValueError(
                "Одоогоор 2+ active Chess tournament байна. "
                f"Active: {self._format_tournament_choices(tournaments)}"
            )
        return tournaments[0]

    async def _sync_registration_status(self, tournament_id: int) -> dict[str, Any]:
        tournament = await self.tournament_repo.get_by_id(int(tournament_id))
        if tournament is None:
            raise ValueError("Tournament олдсонгүй.")

        if not self._is_registration_stage(tournament):
            return tournament

        summary = await self.entry_repo.get_summary_counts(int(tournament_id))
        max_players = int(tournament.get("max_players") or MAX_PLAYERS)
        desired_status = (
            TournamentStatus.REGISTRATION_LOCKED.value
            if summary["confirmed_count"] >= max_players
            else TournamentStatus.REGISTRATION_OPEN.value
        )

        if str(tournament.get("status") or "") != desired_status:
            await self.tournament_repo.update_status(int(tournament_id), desired_status)
            tournament = await self.tournament_repo.get_by_id(int(tournament_id)) or tournament

        return tournament

    async def create_weekly_tournament(
        self,
        guild_id: int,
        created_by: int,
        title: str,
        entry_fee: int = 0,
    ) -> dict[str, Any]:
        season_name = await self.tournament_repo.get_next_season_name(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )

        tournament_id = await self.tournament_repo.create_tournament(
            guild_id=guild_id,
            tournament_type=TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
            title=title,
            created_by=created_by,
            season_name=season_name,
            entry_fee=self.DEFAULT_WEEKLY_ENTRY_FEE if entry_fee == 0 else entry_fee,
            max_players=MAX_PLAYERS,
            lobby_size=8,
            bo_count=2,
            prize_total=self.DEFAULT_WEEKLY_PRIZE_TOTAL,
            prize_1=self.DEFAULT_WEEKLY_PRIZE_1,
            prize_2=self.DEFAULT_WEEKLY_PRIZE_2,
            prize_3=self.DEFAULT_WEEKLY_PRIZE_3,
            status=TournamentStatus.REGISTRATION_OPEN.value,
            slug=self._slugify(f"{TournamentGameKey.AUTOCHESS.value}-{season_name}-{title}"),
        )
        tournament = await self.tournament_repo.get_by_id(tournament_id)
        if tournament is None:
            raise ValueError("Tournament үүсгэхэд алдаа гарлаа.")
        return tournament

    async def list_registration_weeklies(self, guild_id: int) -> list[dict[str, Any]]:
        return await self._list_registration_autochess_weeklies(guild_id)

    async def get_active_weekly(self, guild_id: int) -> dict[str, Any] | None:
        return await self._get_active_autochess_weekly(guild_id)

    async def get_snapshot_by_tournament_id(self, tournament_id: int) -> dict[str, Any]:
        tournament = await self.tournament_repo.get_by_id(int(tournament_id))
        if tournament is None:
            raise ValueError("Tournament олдсонгүй.")

        registered = await self.entry_repo.list_entries(
            int(tournament_id),
            statuses=[EntryStatus.REGISTERED.value],
        )
        confirmed = await self.entry_repo.list_entries(
            int(tournament_id),
            statuses=[EntryStatus.CONFIRMED.value, EntryStatus.REPLACEMENT_IN.value],
        )
        waitlist = await self.entry_repo.list_entries(
            int(tournament_id),
            statuses=[EntryStatus.WAITLIST.value],
        )
        summary = await self.entry_repo.get_summary_counts(int(tournament_id))

        return {
            "tournament": tournament,
            "registered": registered,
            "confirmed": confirmed,
            "waitlist": waitlist,
            "summary": summary,
        }

    async def get_weekly_snapshot(self, guild_id: int) -> dict[str, Any]:
        tournament = await self._get_default_registration_weekly(guild_id)
        return await self.get_snapshot_by_tournament_id(int(tournament["id"]))

    async def join_weekly_button(
        self,
        guild_id: int,
        user_id: int,
        display_name: str,
    ) -> dict[str, Any]:
        tournament = await self._get_default_registration_weekly(guild_id)
        return await self.join_tournament_button(int(tournament["id"]), user_id, display_name)

    async def join_weekly(
        self,
        guild_id: int,
        user_id: int,
        display_name: str,
    ) -> dict[str, Any]:
        return await self.join_weekly_button(guild_id=guild_id, user_id=user_id, display_name=display_name)

    async def join_tournament_button(
        self,
        tournament_id: int,
        user_id: int,
        display_name: str,
    ) -> dict[str, Any]:
        tournament = await self.tournament_repo.get_by_id(int(tournament_id))
        if tournament is None:
            raise ValueError("Tournament олдсонгүй.")
        if str(tournament.get("status") or "") != TournamentStatus.REGISTRATION_OPEN.value:
            if str(tournament.get("status") or "") == TournamentStatus.REGISTRATION_LOCKED.value:
                raise ValueError("Registration дүүрсэн эсвэл хаагдсан байна.")
            raise ValueError("Энэ tournament дээр бүртгэл нээлттэй биш байна.")

        existing = await self.entry_repo.get_entry_by_user(int(tournament["id"]), int(user_id))
        if existing is not None and str(existing.get("status") or "") in {
            EntryStatus.REGISTERED.value,
            EntryStatus.CONFIRMED.value,
            EntryStatus.WAITLIST.value,
            EntryStatus.REPLACEMENT_IN.value,
        }:
            raise ValueError("Та энэ tournament-д аль хэдийн бүртгүүлсэн байна.")

        register_order = await self.entry_repo.get_next_register_order(int(tournament["id"]))
        entry_id = await self.entry_repo.add_entry(
            tournament_id=int(tournament["id"]),
            user_id=int(user_id),
            display_name=display_name,
            register_order=register_order,
            status=EntryStatus.WAITLIST.value,
            payment_status=PaymentStatus.UNPAID.value,
        )

        entry = await self.entry_repo.get_entry_by_id(int(entry_id))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "entry": entry, "summary": summary}

    async def leave_weekly_button(
        self,
        guild_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        tournament = await self._get_default_registration_weekly(guild_id)
        return await self.leave_tournament_button(int(tournament["id"]), user_id)

    async def leave_weekly(
        self,
        guild_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        result = await self.leave_weekly_button(guild_id=guild_id, user_id=user_id)
        return {
            "tournament": result["tournament"],
            "entry": result["removed_entry"],
            "summary": result["summary"],
        }

    async def leave_tournament_button(
        self,
        tournament_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        tournament = await self.tournament_repo.get_by_id(int(tournament_id))
        if tournament is None:
            raise ValueError("Tournament олдсонгүй.")
        if str(tournament.get("status") or "") != TournamentStatus.REGISTRATION_OPEN.value:
            raise ValueError("Registration хаагдсан тул leave хийх боломжгүй.")

        entry = await self.entry_repo.get_entry_by_user(int(tournament["id"]), int(user_id))
        if entry is None:
            raise ValueError("Та энэ tournament-д бүртгэлгүй байна.")
        if str(entry.get("status") or "") not in {
            EntryStatus.REGISTERED.value,
            EntryStatus.WAITLIST.value,
        }:
            raise ValueError("Зөвхөн waiting/registered төлөвтэй үед leave хийж болно.")

        await self.entry_repo.delete_entry(int(entry["id"]))
        tournament = await self._sync_registration_status(int(tournament["id"]))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "removed_entry": entry, "summary": summary}

    async def confirm_payment_for_user(
        self,
        guild_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        tournament = await self._get_default_registration_weekly(guild_id)
        entry = await self.entry_repo.get_entry_by_user(int(tournament["id"]), int(user_id))
        if entry is None:
            raise ValueError("Тэр хэрэглэгч tournament-д бүртгэлгүй байна.")

        if str(entry.get("payment_status") or "") == PaymentStatus.CONFIRMED.value and str(
            entry.get("status") or ""
        ) in {EntryStatus.CONFIRMED.value, EntryStatus.WAITLIST.value}:
            raise ValueError("Энэ хэрэглэгчийн төлбөр өмнө нь баталгаажсан байна.")

        confirmed_count = await self.entry_repo.count_by_status(
            int(tournament["id"]),
            EntryStatus.CONFIRMED.value,
        )
        should_waitlist = confirmed_count >= int(tournament.get("max_players") or MAX_PLAYERS)

        await self.entry_repo.confirm_entry(int(entry["id"]), waitlist=should_waitlist)
        updated_entry = await self.entry_repo.get_entry_by_id(int(entry["id"]))
        tournament = await self._sync_registration_status(int(tournament["id"]))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "entry": updated_entry, "summary": summary}

    async def admin_add_confirmed_user(
        self,
        guild_id: int,
        user_id: int,
        display_name: str,
    ) -> dict[str, Any]:
        tournament = await self._get_default_registration_weekly(guild_id)

        stages = await self.stage_repo.list_stages(int(tournament["id"]))
        if stages:
            raise ValueError("Bracket үүссэн тул add хийх боломжгүй. Энэ үед replace ашиглана.")

        existing = await self.entry_repo.get_entry_by_user(int(tournament["id"]), int(user_id))
        if existing is None:
            register_order = await self.entry_repo.get_next_register_order(int(tournament["id"]))
            entry_id = await self.entry_repo.add_entry(
                tournament_id=int(tournament["id"]),
                user_id=int(user_id),
                display_name=display_name,
                register_order=register_order,
                status=EntryStatus.REGISTERED.value,
                payment_status=PaymentStatus.UNPAID.value,
                source="admin",
            )
            existing = await self.entry_repo.get_entry_by_id(int(entry_id))

        if existing is None:
            raise ValueError("Entry үүсгэж чадсангүй.")

        if str(existing.get("payment_status") or "") == PaymentStatus.CONFIRMED.value and str(
            existing.get("status") or ""
        ) == EntryStatus.CONFIRMED.value:
            raise ValueError("Энэ хэрэглэгч аль хэдийн confirmed байна.")

        confirmed_count = await self.entry_repo.count_by_status(
            int(tournament["id"]),
            EntryStatus.CONFIRMED.value,
        )
        should_waitlist = confirmed_count >= int(tournament.get("max_players") or MAX_PLAYERS)
        await self.entry_repo.confirm_entry(int(existing["id"]), waitlist=should_waitlist)

        entry = await self.entry_repo.get_entry_by_id(int(existing["id"]))
        tournament = await self._sync_registration_status(int(tournament["id"]))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "entry": entry, "summary": summary}

    async def admin_remove_user(
        self,
        guild_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        tournament = await self._get_default_registration_weekly(guild_id)

        stages = await self.stage_repo.list_stages(int(tournament["id"]))
        if stages:
            raise ValueError("Bracket үүссэн тул remove хийхгүй. Энэ үед replace ашиглана.")

        entry = await self.entry_repo.get_entry_by_user(int(tournament["id"]), int(user_id))
        if entry is None:
            raise ValueError("Энэ хэрэглэгч идэвхтэй weekly tournament-д алга.")
        if str(entry.get("status") or "") not in {
            EntryStatus.REGISTERED.value,
            EntryStatus.CONFIRMED.value,
            EntryStatus.WAITLIST.value,
            EntryStatus.REPLACEMENT_IN.value,
        }:
            raise ValueError("Энэ entry-г remove хийх боломжгүй төлөвтэй байна.")

        removed_entry = dict(entry)
        await self.entry_repo.delete_entry(int(entry["id"]))
        tournament = await self._sync_registration_status(int(tournament["id"]))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "removed_entry": removed_entry, "summary": summary}

    async def admin_revert_confirmed_user(
        self,
        guild_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        tournament = await self._get_default_registration_weekly(guild_id)

        stages = await self.stage_repo.list_stages(int(tournament["id"]))
        if stages:
            raise ValueError("Bracket үүссэн тул confirmed-оос буцаахгүй. Энэ үед replace ашиглана.")

        entry = await self.entry_repo.get_entry_by_user(int(tournament["id"]), int(user_id))
        if entry is None:
            raise ValueError("Энэ хэрэглэгч идэвхтэй weekly tournament-д алга.")
        if str(entry.get("status") or "") != EntryStatus.CONFIRMED.value:
            raise ValueError("Энэ хэрэглэгч confirmed төлөвтэй биш байна.")

        await self.entry_repo.update_status(int(entry["id"]), EntryStatus.REGISTERED.value)
        updated_entry = await self.entry_repo.get_entry_by_id(int(entry["id"]))
        tournament = await self._sync_registration_status(int(tournament["id"]))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "entry": updated_entry, "summary": summary}

    async def get_entry_for_review_message(self, review_message_id: int) -> dict[str, Any] | None:
        return await self.entry_repo.get_entry_by_review_message_id(int(review_message_id))

    async def approve_entry_by_review_message(
        self,
        guild_id: int,
        review_message_id: int,
    ) -> dict[str, Any]:
        entry = await self.entry_repo.get_entry_by_review_message_id(int(review_message_id))
        if entry is None:
            raise ValueError("Тэр review card-д холбоотой entry олдсонгүй.")

        tournament = await self.tournament_repo.get_by_id(int(entry["tournament_id"]))
        if tournament is None or int(tournament["guild_id"]) != int(guild_id):
            raise ValueError("Tournament олдсонгүй.")

        if str(entry.get("status") or "") not in {
            EntryStatus.REGISTERED.value,
            EntryStatus.WAITLIST.value,
        } or str(entry.get("payment_status") or "") != PaymentStatus.UNPAID.value:
            raise ValueError("Энэ entry аль хэдийн шийдэгдсэн байна.")

        confirmed_count = await self.entry_repo.count_by_status(
            int(tournament["id"]),
            EntryStatus.CONFIRMED.value,
        )
        should_waitlist = confirmed_count >= int(tournament.get("max_players") or MAX_PLAYERS)
        await self.entry_repo.confirm_entry(int(entry["id"]), waitlist=should_waitlist)

        updated_entry = await self.entry_repo.get_entry_by_id(int(entry["id"]))
        tournament = await self._sync_registration_status(int(tournament["id"]))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "entry": updated_entry, "summary": summary}

    async def reject_entry_by_review_message(
        self,
        guild_id: int,
        review_message_id: int,
    ) -> dict[str, Any]:
        entry = await self.entry_repo.get_entry_by_review_message_id(int(review_message_id))
        if entry is None:
            raise ValueError("Тэр review card-д холбоотой entry олдсонгүй.")

        tournament = await self.tournament_repo.get_by_id(int(entry["tournament_id"]))
        if tournament is None or int(tournament["guild_id"]) != int(guild_id):
            raise ValueError("Tournament олдсонгүй.")

        if str(entry.get("status") or "") not in {
            EntryStatus.REGISTERED.value,
            EntryStatus.WAITLIST.value,
        } or str(entry.get("payment_status") or "") != PaymentStatus.UNPAID.value:
            raise ValueError("Энэ entry аль хэдийн шийдэгдсэн байна.")

        await self.entry_repo.update_status(int(entry["id"]), "rejected")
        await self.entry_repo.update_payment_status(int(entry["id"]), PaymentStatus.REJECTED.value)

        updated_entry = await self.entry_repo.get_entry_by_id(int(entry["id"]))
        tournament = await self._sync_registration_status(int(tournament["id"]))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "entry": updated_entry, "summary": summary}

    async def remove_entry_by_review_message(
        self,
        guild_id: int,
        review_message_id: int,
    ) -> dict[str, Any]:
        entry = await self.entry_repo.get_entry_by_review_message_id(int(review_message_id))
        if entry is None:
            raise ValueError("Тэр review card-д холбоотой entry олдсонгүй.")

        tournament = await self.tournament_repo.get_by_id(int(entry["tournament_id"]))
        if tournament is None or int(tournament["guild_id"]) != int(guild_id):
            raise ValueError("Tournament олдсонгүй.")

        if str(entry.get("status") or "") not in {
            EntryStatus.REGISTERED.value,
            EntryStatus.WAITLIST.value,
        } or str(entry.get("payment_status") or "") != PaymentStatus.UNPAID.value:
            raise ValueError("Энэ entry аль хэдийн шийдэгдсэн байна.")

        removed_entry = dict(entry)
        await self.entry_repo.delete_entry(int(entry["id"]))
        tournament = await self._sync_registration_status(int(tournament["id"]))
        summary = await self.entry_repo.get_summary_counts(int(tournament["id"]))
        return {"tournament": tournament, "removed_entry": removed_entry, "summary": summary}

    async def end_active_weekly(self, guild_id: int) -> dict[str, Any]:
        return await self.end_weekly(guild_id)

    async def end_weekly(self, guild_id: int, selector: str | None = None) -> dict[str, Any]:
        tournaments = await self._list_active_autochess_weeklies(guild_id)
        if not tournaments:
            raise ValueError("Идэвхтэй weekly tournament алга.")

        selector_text = str(selector or "").strip()
        tournament: dict[str, Any] | None = None

        if selector_text:
            normalized = selector_text.lower().replace("season", "").replace("#", "").strip()
            for item in tournaments:
                item_id = int(item["id"])
                season_name = str(item.get("season_name") or "").strip().lower()
                if normalized == str(item_id):
                    tournament = item
                    break
                if season_name:
                    season_digits = re.sub(r"[^0-9]+", "", season_name)
                    if normalized == season_name or (season_digits and normalized == season_digits):
                        tournament = item
                        break

            if tournament is None:
                raise ValueError(
                    "Тэр weekly tournament олдсонгүй. "
                    f"Идэвхтэй: {self._format_tournament_choices(tournaments)}"
                )
        else:
            tournament = sorted(tournaments, key=lambda item: int(item["id"]), reverse=True)[0]

        await self.tournament_repo.update_status(
            int(tournament["id"]),
            TournamentStatus.COMPLETED.value,
        )
        updated = await self.tournament_repo.get_by_id(int(tournament["id"]))
        if updated is None:
            raise ValueError("Tournament update хийсний дараа олдсонгүй.")
        return updated
