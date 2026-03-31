from __future__ import annotations

from typing import Any

from models.enums import TournamentFormatKey, TournamentGameKey, TournamentType
from repositories.tournament_repo import TournamentRepo


class TournamentService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.tournament_repo = TournamentRepo(db_path)

    async def _get_active_weekly(self, guild_id: int) -> dict[str, Any]:
        tournament = await self.tournament_repo.get_latest_active_by_scope(
            guild_id,
            TournamentType.WEEKLY.value,
            game_key=TournamentGameKey.AUTOCHESS.value,
            format_key=TournamentFormatKey.SOLO_32.value,
        )
        if tournament is None:
            raise ValueError("Идэвхтэй weekly tournament алга.")
        return tournament

    async def set_prize_total(
        self,
        guild_id: int,
        amount: int,
    ) -> dict[str, Any]:
        if amount < 0:
            raise ValueError("Prize total 0-ээс бага байж болохгүй.")

        tournament = await self._get_active_weekly(guild_id)
        await self.tournament_repo.update_prize_total(int(tournament["id"]), int(amount))
        updated = await self.tournament_repo.get_by_id(int(tournament["id"]))
        if updated is None:
            raise ValueError("Tournament update хийсний дараа олдсонгүй.")
        return updated

    async def set_prize_rank(
        self,
        guild_id: int,
        rank: int,
        amount: int,
    ) -> dict[str, Any]:
        if rank not in (1, 2, 3):
            raise ValueError("Prize rank зөвхөн 1, 2, 3 байж болно.")
        if amount < 0:
            raise ValueError("Prize amount 0-ээс бага байж болохгүй.")

        tournament = await self._get_active_weekly(guild_id)
        await self.tournament_repo.update_prize_rank(
            int(tournament["id"]),
            int(rank),
            int(amount),
        )
        updated = await self.tournament_repo.get_by_id(int(tournament["id"]))
        if updated is None:
            raise ValueError("Tournament update хийсний дараа олдсонгүй.")
        return updated

    async def get_prize_snapshot(self, guild_id: int) -> dict[str, Any]:
        return await self._get_active_weekly(guild_id)

    async def set_rules(
        self,
        guild_id: int,
        rules_text: str,
    ) -> dict[str, Any]:
        rules_text = rules_text.strip()
        if not rules_text:
            raise ValueError("Rules хоосон байж болохгүй.")

        tournament = await self._get_active_weekly(guild_id)
        await self.tournament_repo.update_rules_text(
            int(tournament["id"]),
            rules_text,
        )
        updated = await self.tournament_repo.get_by_id(int(tournament["id"]))
        if updated is None:
            raise ValueError("Tournament update хийсний дараа олдсонгүй.")
        return updated

    async def get_rules(self, guild_id: int) -> dict[str, Any]:
        return await self._get_active_weekly(guild_id)

    async def add_donation_from_user(
        self,
        guild_id: int,
        member_user_id: int,
        member_display_name: str,
        amount: int,
        created_by: int,
        note: str = "",
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("Donation amount 0-ээс их байх ёстой.")

        tournament = await self._get_active_weekly(guild_id)

        await self.tournament_repo.add_sponsor(
            tournament_id=int(tournament["id"]),
            sponsor_name=member_display_name,
            sponsor_user_id=int(member_user_id),
            amount=int(amount),
            note=note.strip(),
            created_by=int(created_by),
        )

        sponsors = await self.tournament_repo.list_sponsors(int(tournament["id"]))
        sponsor_total = await self.tournament_repo.get_sponsor_total(int(tournament["id"]))

        return {
            "tournament": tournament,
            "sponsors": sponsors,
            "sponsor_total": sponsor_total,
        }

    async def add_named_sponsor(
        self,
        guild_id: int,
        sponsor_name: str,
        amount: int,
        created_by: int,
        note: str = "",
    ) -> dict[str, Any]:
        sponsor_name = sponsor_name.strip()
        if not sponsor_name:
            raise ValueError("Sponsor name хоосон байж болохгүй.")
        if amount <= 0:
            raise ValueError("Sponsor amount 0-ээс их байх ёстой.")

        tournament = await self._get_active_weekly(guild_id)

        await self.tournament_repo.add_sponsor(
            tournament_id=int(tournament["id"]),
            sponsor_name=sponsor_name,
            sponsor_user_id=None,
            amount=int(amount),
            note=note.strip(),
            created_by=int(created_by),
        )

        sponsors = await self.tournament_repo.list_sponsors(int(tournament["id"]))
        sponsor_total = await self.tournament_repo.get_sponsor_total(int(tournament["id"]))

        return {
            "tournament": tournament,
            "sponsors": sponsors,
            "sponsor_total": sponsor_total,
        }

    async def get_donations(self, guild_id: int) -> dict[str, Any]:
        tournament = await self._get_active_weekly(guild_id)
        sponsors = await self.tournament_repo.list_sponsors(int(tournament["id"]))
        sponsor_total = await self.tournament_repo.get_sponsor_total(int(tournament["id"]))

        return {
            "tournament": tournament,
            "sponsors": sponsors,
            "sponsor_total": sponsor_total,
        }
