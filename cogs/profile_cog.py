from __future__ import annotations

import discord
from discord.ext import commands

from services.stats_service import StatsService

DONOR_ROLE_NAMES = ["💎 Donator", "👑 Elite Donator", "🌟 Legend Donator"]
SPONSOR_ROLE_NAMES = ["💎 Sponsor", "👑 Elite Sponsor", "🌟 Legend Sponsor"]


def _rank_label(rank: int) -> str:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    suffix = "st" if rank == 1 else "nd" if rank == 2 else "rd" if rank == 3 else "th"
    return f"{medals.get(rank, '•')} {rank}{suffix}"


def _best_finish_label(championships: int, runner_ups: int, third_places: int) -> str:
    if championships > 0:
        return _rank_label(1)
    if runner_ups > 0:
        return _rank_label(2)
    if third_places > 0:
        return _rank_label(3)
    return "-"


PROFILE_ACCESS_ROLE_NAMES = ["Confirmed", "✅ Confirmed"]


def _resolve_support_role_chain(role_name: str | None, all_role_names: list[str]) -> list[str]:
    if not role_name or role_name not in all_role_names:
        return []
    index = all_role_names.index(role_name)
    return all_role_names[: index + 1]


def _has_profile_access(member: discord.Member) -> bool:
    allowed_role_names = set(DONOR_ROLE_NAMES + SPONSOR_ROLE_NAMES + PROFILE_ACCESS_ROLE_NAMES)
    return any(role.name in allowed_role_names for role in member.roles)


PROFILE_SITE_URL = "https://www.chessofmongolia.site"


class ProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.stats_service = StatsService(bot.db_path)

    @commands.hybrid_command(name="profile")
    async def profile(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        if ctx.guild is None:
            await ctx.send("Энэ command зөвхөн server дотор ажиллана.")
            return

        if not _has_profile_access(ctx.author):
            await ctx.send("❌ `.profile` командыг зөвхөн Donate эсвэл Sponsor support-той хэрэглэгч ашиглана.")
            return

        target = member or ctx.author
        profile = await self.stats_service.get_player_profile(target.id)
        if profile is None:
            await ctx.send(f"ℹ️ {target.mention} дээр хадгалагдсан profile/stat одоогоор алга.")
            return

        history = await self.stats_service.get_player_history(target.id, limit=10)
        support = await self.stats_service.get_player_support_status(target.id, guild_id=ctx.guild.id)

        tournaments_played = int(profile.get("tournaments_played") or 0)
        championships = int(profile.get("championships") or 0)
        runner_ups = int(profile.get("runner_ups") or 0)
        third_places = int(profile.get("third_places") or 0)
        podiums = int(profile.get("podiums") or 0)
        total_prize_money = int(profile.get("total_prize_money") or 0)
        weekly_played = int(profile.get("weekly_played") or 0)
        special_played = int(profile.get("special_played") or 0)
        monthly_played = int(profile.get("monthly_played") or 0)

        final_ranks = [int(row.get("final_rank") or 0) for row in history if int(row.get("final_rank") or 0) > 0]
        avg_finish = (sum(final_ranks) / len(final_ranks)) if final_ranks else 0.0
        win_rate = (championships / tournaments_played * 100.0) if tournaments_played else 0.0
        podium_rate = (podiums / tournaments_played * 100.0) if tournaments_played else 0.0

        champion_seasons = [str(row["season_name"]) for row in history if int(row.get("final_rank") or 0) == 1]
        recent_seasons: list[str] = []
        for row in history:
            season_name = str(row.get("season_name") or "-")
            if season_name not in recent_seasons:
                recent_seasons.append(season_name)

        latest_result = history[0] if history else None
        color = discord.Color.gold() if championships > 0 else discord.Color.blurple()

        embed = discord.Embed(
            title=f"{profile['display_name']} - Player Card",
            color=color,
            description=(
                f"{target.mention}\n"
                f"Best Finish: **{_best_finish_label(championships, runner_ups, third_places)}**\n"
                f"Win Rate: **{win_rate:.1f}%** | Podium Rate: **{podium_rate:.1f}%**"
            ),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        discord_roles = [
            role.mention
            for role in sorted(target.roles, key=lambda role: role.position, reverse=True)
            if role != ctx.guild.default_role
        ]
        embed.add_field(
            name="Discord Status",
            value=" ".join(discord_roles[:12]) if discord_roles else "Unranked",
            inline=False,
        )

        season_badges = "  ".join(f"`{season}`" for season in recent_seasons[:5]) if recent_seasons else "`No seasons`"
        embed.add_field(name="Season Badges", value=season_badges, inline=False)

        if support is not None:
            support_lines: list[str] = []
            if support.get("donor_tier"):
                donor_mentions: list[str] = []
                for donor_role_name in _resolve_support_role_chain(str(support["donor_tier"]), DONOR_ROLE_NAMES):
                    donor_role = discord.utils.get(ctx.guild.roles, name=donor_role_name)
                    donor_mentions.append(donor_role.mention if donor_role is not None else donor_role_name)
                support_lines.append(f"{' '.join(donor_mentions)} until {support['donor_expires_at']}")
            if support.get("sponsor_tier"):
                sponsor_mentions: list[str] = []
                for sponsor_role_name in _resolve_support_role_chain(str(support["sponsor_tier"]), SPONSOR_ROLE_NAMES):
                    sponsor_role = discord.utils.get(ctx.guild.roles, name=sponsor_role_name)
                    sponsor_mentions.append(sponsor_role.mention if sponsor_role is not None else sponsor_role_name)
                support_lines.append(f"{' '.join(sponsor_mentions)} until {support['sponsor_expires_at']}")
            if support_lines:
                embed.add_field(name="Support Status", value="\n".join(support_lines), inline=False)

        if tournaments_played:
            career_value = (
                f"Played: **{tournaments_played}**\n"
                f"Weekly / Special / Monthly: **{weekly_played} / {special_played} / {monthly_played}**\n"
                f"Average Finish: **{avg_finish:.2f}**"
            )
        else:
            career_value = "Played: **0**\nWeekly / Special / Monthly: **0 / 0 / 0**\nAverage Finish: **-**"

        embed.add_field(name="Career Snapshot", value=career_value, inline=True)
        embed.add_field(
            name="Trophy Cabinet",
            value=(
                f"Championships: **{championships}**\n"
                f"Runner-ups: **{runner_ups}**\n"
                f"Third Places: **{third_places}**\n"
                f"Podiums: **{podiums}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Earnings",
            value=(
                f"Total Prize: **{total_prize_money:,}₮**\n"
                f"Champion Seasons: **{', '.join(champion_seasons[:4]) if champion_seasons else '-'}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Contact Info",
            value=(
                f"Phone: **{profile.get('phone_number') or '-'}**\n"
                f"Bank Account: **{profile.get('bank_account') or '-'}**"
            ),
            inline=False,
        )

        if latest_result is not None:
            latest_rank = int(latest_result.get("final_rank") or 0)
            latest_points = int(latest_result.get("total_points") or 0)
            latest_prize = int(latest_result.get("prize_amount") or 0)
            embed.add_field(
                name="Latest Run",
                value=(
                    f"Season: **{latest_result['season_name']}**\n"
                    f"Finish: **{_rank_label(latest_rank)}**\n"
                    f"Points: **{latest_points}**\n"
                    f"Prize: **{latest_prize:,}₮**"
                ),
                inline=False,
            )

        if history:
            recent_lines = []
            for row in history[:5]:
                rank = int(row.get("final_rank") or 0)
                prize = int(row.get("prize_amount") or 0)
                points = int(row.get("total_points") or 0)
                recent_lines.append(
                    f"**{row['season_name']}**  {_rank_label(rank)}  {points} pts  {prize:,}₮"
                )
            embed.add_field(name="Recent Tournaments", value="\n".join(recent_lines), inline=False)

            title_runs = [row for row in history if int(row.get("final_rank") or 0) == 1][:3]
            if title_runs:
                win_lines = [
                    f"**{row['season_name']}** - {row['tournament_title']} - {int(row.get('prize_amount') or 0):,}₮"
                    for row in title_runs
                ]
                embed.add_field(name="Title Runs", value="\n".join(win_lines), inline=False)

        footer_text = "Chess Of Mongolia Player Profile"
        if championships > 0:
            footer_text += " • Champion"
        embed.set_footer(text=footer_text)

        profile_url = f"{PROFILE_SITE_URL}/player/{target.id}"
        embed.add_field(name="Site Profile", value=f"[Open Player Profile]({profile_url})", inline=False)

        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Site Profile",
                style=discord.ButtonStyle.link,
                url=profile_url,
            )
        )

        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="set_phone")
    async def set_phone(self, ctx: commands.Context, *, phone_number: str) -> None:
        if ctx.guild is None:
            await ctx.send("Энэ command зөвхөн server дотор ажиллана.")
            return

        cleaned = phone_number.strip()
        if not cleaned:
            await ctx.send("❌ Утасны дугаар хоосон байж болохгүй.")
            return

        await self.stats_service.update_player_contact(
            ctx.author.id,
            ctx.author.display_name,
            phone_number=cleaned,
        )
        await ctx.send(f"✅ Утасны дугаар хадгалагдлаа: **{cleaned}**")

    @commands.hybrid_command(name="set_account")
    async def set_account(self, ctx: commands.Context, *, bank_account: str) -> None:
        if ctx.guild is None:
            await ctx.send("Энэ command зөвхөн server дотор ажиллана.")
            return

        cleaned = bank_account.strip()
        if not cleaned:
            await ctx.send("❌ Дансны дугаар хоосон байж болохгүй.")
            return

        await self.stats_service.update_player_contact(
            ctx.author.id,
            ctx.author.display_name,
            bank_account=cleaned,
        )
        await ctx.send(f"✅ Дансны дугаар хадгалагдлаа: **{cleaned}**")

    @commands.hybrid_command(name="leaderboard")
    async def leaderboard(self, ctx: commands.Context) -> None:
        leaderboard_rows = await self.stats_service.get_leaderboard(limit=10)
        if not leaderboard_rows:
            await ctx.send("ℹ️ Leaderboard одоогоор хоосон байна.")
            return

        lines = []
        for index, row in enumerate(leaderboard_rows, start=1):
            lines.append(
                f"**{index}.** <@{row['user_id']}> - "
                f"Wins {row['championships']} | "
                f"Podiums {row['podiums']} | "
                f"Prize {int(row['total_prize_money']):,}₮"
            )

        embed = discord.Embed(
            title="Community Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="winner")
    async def winner(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("Энэ command зөвхөн server дотор ажиллана.")
            return

        try:
            snapshot = await self.stats_service.get_latest_weekly_winner_snapshot(ctx.guild.id)
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        tournament = snapshot["tournament"]
        podium = snapshot["podium"]
        standings = snapshot["standings"]

        embed = discord.Embed(
            title=f"{tournament['title']} - Winner",
            color=discord.Color.gold(),
        )

        if podium:
            for item in podium:
                rank = int(item["final_rank"])
                medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉"
                embed.add_field(
                    name=f"{medal} {rank}-р байр",
                    value=f"<@{item['user_id']}> - **{int(item['amount']):,}₮**",
                    inline=False,
                )

        if standings:
            lines = []
            for row in standings[:8]:
                lines.append(
                    f"**{row['final_position']}.** <@{row['user_id']}> - **{row['total_points']}**"
                )
            embed.add_field(name="Final Standings", value="\n".join(lines), inline=False)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot))
