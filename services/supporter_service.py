from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord

from repositories.supporter_repo import SupporterRepo

SUPPORT_DURATION_DAYS = 30

DONOR_TIERS = (
    (100000, "🌟 Legend Donator"),
    (50000, "👑 Elite Donator"),
    (20000, "💎 Donator"),
)

SPONSOR_TIERS = (
    (100000, "🌟 Legend Sponsor"),
    (50000, "👑 Elite Sponsor"),
    (20000, "💎 Sponsor"),
)


@dataclass(frozen=True)
class SupportTierResult:
    role_name: str
    expires_at: str


class SupporterService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.supporter_repo = SupporterRepo(db_path)

    def _pick_tier(self, amount: int, tiers: tuple[tuple[int, str], ...]) -> SupportTierResult | None:
        for minimum, role_name in tiers:
            if int(amount) >= int(minimum):
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(days=SUPPORT_DURATION_DAYS)
                ).strftime("%Y-%m-%d %H:%M:%S")
                return SupportTierResult(role_name=role_name, expires_at=expires_at)
        return None

    def _tier_rank(self, role_name: str | None, tiers: tuple[tuple[int, str], ...]) -> int:
        if not role_name:
            return -1
        for index, (_, tier_role_name) in enumerate(tiers):
            if tier_role_name == role_name:
                return index
        return -1

    def _extend_expiry_from(self, current_expiry: str | None) -> str:
        now = datetime.now(timezone.utc)
        base = now
        if current_expiry:
            try:
                parsed = datetime.strptime(current_expiry, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if parsed > now:
                    base = parsed
            except ValueError:
                pass
        return (base + timedelta(days=SUPPORT_DURATION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

    def _resolve_membership_update(
        self,
        amount: int,
        tiers: tuple[tuple[int, str], ...],
        current_tier: str | None,
        current_expiry: str | None,
    ) -> SupportTierResult | None:
        new_tier = self._pick_tier(amount, tiers)
        if new_tier is None:
            return None

        current_rank = self._tier_rank(current_tier, tiers)
        new_rank = self._tier_rank(new_tier.role_name, tiers)

        if current_rank != -1 and current_rank <= new_rank:
            chosen_tier = str(current_tier)
        else:
            chosen_tier = new_tier.role_name

        return SupportTierResult(
            role_name=chosen_tier,
            expires_at=self._extend_expiry_from(current_expiry),
        )

    def _resolve_role_names_for_tier(
        self,
        target_role_name: str | None,
        tiers: tuple[tuple[int, str], ...],
    ) -> set[str]:
        if not target_role_name:
            return set()

        role_names = [role_name for _, role_name in tiers]
        if target_role_name not in role_names:
            return {target_role_name}

        start_index = role_names.index(target_role_name)
        return set(role_names[start_index:])

    async def apply_donation_support(
        self,
        guild_id: int,
        user_id: int | None,
        amount: int,
    ) -> SupportTierResult | None:
        if user_id is None:
            return self._pick_tier(amount, DONOR_TIERS)

        membership = await self.supporter_repo.get_membership(int(guild_id), int(user_id))
        result = self._resolve_membership_update(
            amount,
            DONOR_TIERS,
            membership.get("donor_tier") if membership else None,
            membership.get("donor_expires_at") if membership else None,
        )
        if result is None:
            return None

        await self.supporter_repo.upsert_donor_membership(
            guild_id=guild_id,
            user_id=int(user_id),
            tier_name=result.role_name,
            expires_at=result.expires_at,
        )
        return result

    async def apply_sponsor_support(
        self,
        guild_id: int,
        user_id: int | None,
        amount: int,
    ) -> SupportTierResult | None:
        if user_id is None:
            return self._pick_tier(amount, SPONSOR_TIERS)

        membership = await self.supporter_repo.get_membership(int(guild_id), int(user_id))
        result = self._resolve_membership_update(
            amount,
            SPONSOR_TIERS,
            membership.get("sponsor_tier") if membership else None,
            membership.get("sponsor_expires_at") if membership else None,
        )
        if result is None:
            return None

        await self.supporter_repo.upsert_sponsor_membership(
            guild_id=guild_id,
            user_id=int(user_id),
            tier_name=result.role_name,
            expires_at=result.expires_at,
        )
        return result

    async def sync_member_roles(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> None:
        await self.supporter_repo.clear_expired_memberships(int(guild.id))
        membership = await self.supporter_repo.get_membership(int(guild.id), int(member.id))

        donor_target = membership.get("donor_tier") if membership else None
        sponsor_target = membership.get("sponsor_tier") if membership else None

        await self._sync_role_group(guild, member, DONOR_TIERS, donor_target)
        await self._sync_role_group(guild, member, SPONSOR_TIERS, sponsor_target)

    async def sync_all_guild_roles(self, guild: discord.Guild) -> None:
        await self.supporter_repo.clear_expired_memberships(int(guild.id))
        memberships = await self.supporter_repo.list_guild_memberships(int(guild.id))
        user_ids = {int(item["user_id"]) for item in memberships}
        support_role_names = {role for _, role in DONOR_TIERS} | {role for _, role in SPONSOR_TIERS}

        for member in guild.members:
            if any(role.name in support_role_names for role in member.roles):
                user_ids.add(int(member.id))

        for user_id in sorted(user_ids):
            member = guild.get_member(int(user_id))
            if member is None:
                continue
            await self.sync_member_roles(guild, member)

    async def _sync_role_group(
        self,
        guild: discord.Guild,
        member: discord.Member,
        tiers: tuple[tuple[int, str], ...],
        target_role_name: str | None,
    ) -> None:
        role_names = [role_name for _, role_name in tiers]
        expected_role_names = self._resolve_role_names_for_tier(target_role_name, tiers)
        roles = [await self._ensure_role(guild, name) for name in role_names]
        roles = [role for role in roles if role is not None]

        for role in roles:
            if role.name not in expected_role_names and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Support tier sync")
                except (discord.Forbidden, discord.HTTPException):
                    continue

        if not expected_role_names:
            return

        for role_name in role_names:
            if role_name not in expected_role_names:
                continue
            role = next((item for item in roles if item.name == role_name), None)
            if role is None:
                role = await self._ensure_role(guild, role_name)
            if role is None or role in member.roles:
                continue
            try:
                await member.add_roles(role, reason="Support tier sync")
            except (discord.Forbidden, discord.HTTPException):
                continue

    async def _ensure_role(self, guild: discord.Guild, role_name: str) -> discord.Role | None:
        role = discord.utils.get(guild.roles, name=role_name)
        if role is not None:
            return role
        try:
            return await guild.create_role(
                name=role_name,
                mentionable=False,
                reason="Auto-created for support status system",
            )
        except (discord.Forbidden, discord.HTTPException):
            return None
