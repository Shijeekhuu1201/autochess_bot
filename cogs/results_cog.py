from __future__ import annotations

import re

import discord
from discord.ext import commands

from services.result_service import ResultService

WEEKLY_CHAMPION_ROLE_NAME = "Weekly Champion"


class ResultsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.result_service = ResultService(bot.db_path)

    def _build_scoreboard_embed(
        self,
        title: str,
        result: dict,
        color: discord.Color,
    ) -> discord.Embed:
        scoreboard = result["scoreboard"]

        lines: list[str] = []
        for index, row in enumerate(scoreboard, start=1):
            suffix = ""
            if result["stage_finished"] and result["qualify_count"] > 0:
                suffix = " ✅" if index <= result["qualify_count"] else " ❌"

            lines.append(
                f"**{index}.** <@{row['user_id']}> "
                f"- Total **{row['total_points']}** "
                f"(G1: {row['game1_points']}, G2: {row['game2_points']}){suffix}"
            )

        if not lines:
            lines = ["Одоогоор result алга."]

        embed = discord.Embed(
            title=title,
            color=color,
            description=(
                f"Stage: **{result['stage_key']}**\n"
                f"Games confirmed: **{result['confirmed_games']}/{result['game_count']}**\n"
                f"Finished: **{'Yes' if result['stage_finished'] else 'No'}**"
            ),
        )
        embed.add_field(
            name="Standings",
            value="\n".join(lines),
            inline=False,
        )

        if result["stage_finished"] and result["qualify_count"] > 0:
            qualified = [
                f"<@{row['user_id']}>"
                for i, row in enumerate(scoreboard, start=1)
                if i <= result["qualify_count"]
            ]
            embed.add_field(
                name="Qualified",
                value="\n".join(qualified) if qualified else "-",
                inline=False,
            )

        return embed

    def _build_final_podium_embed(self, result: dict) -> discord.Embed:
        scoreboard = result["scoreboard"]

        first = scoreboard[0] if len(scoreboard) >= 1 else None
        second = scoreboard[1] if len(scoreboard) >= 2 else None
        third = scoreboard[2] if len(scoreboard) >= 3 else None

        embed = discord.Embed(
            title="🏆 GRAND FINAL RESULT",
            description="Weekly Auto Chess Cup ялагчид тодорлоо!",
            color=discord.Color.gold(),
        )

        if first:
            embed.add_field(
                name="🥇 1-р байр",
                value=(
                    f"<@{first['user_id']}>\n"
                    f"Total: **{first['total_points']}** "
                    f"(G1: {first['game1_points']}, G2: {first['game2_points']})"
                ),
                inline=False,
            )

        if second:
            embed.add_field(
                name="🥈 2-р байр",
                value=(
                    f"<@{second['user_id']}>\n"
                    f"Total: **{second['total_points']}** "
                    f"(G1: {second['game1_points']}, G2: {second['game2_points']})"
                ),
                inline=False,
            )

        if third:
            embed.add_field(
                name="🥉 3-р байр",
                value=(
                    f"<@{third['user_id']}>\n"
                    f"Total: **{third['total_points']}** "
                    f"(G1: {third['game1_points']}, G2: {third['game2_points']})"
                ),
                inline=False,
            )

        top8_lines = []
        for index, row in enumerate(scoreboard[:8], start=1):
            medal = ""
            if index == 1:
                medal = "🥇 "
            elif index == 2:
                medal = "🥈 "
            elif index == 3:
                medal = "🥉 "

            top8_lines.append(
                f"{medal}**{index}.** <@{row['user_id']}> - **{row['total_points']}**"
            )

        if top8_lines:
            embed.add_field(
                name="Final Standings",
                value="\n".join(top8_lines),
                inline=False,
            )

        if first:
            embed.set_footer(text=f"Champion: {first['display_name']}")

        return embed

    async def _assign_weekly_champion_role(
        self,
        guild: discord.Guild,
        winner_user_id: int,
    ) -> str | None:
        role = discord.utils.get(guild.roles, name=WEEKLY_CHAMPION_ROLE_NAME)
        if role is None:
            return f"ℹ️ `{WEEKLY_CHAMPION_ROLE_NAME}` role олдсонгүй."

        for member in list(role.members):
            try:
                await member.remove_roles(role, reason="New weekly champion selected")
            except discord.HTTPException:
                pass

        winner_member = guild.get_member(int(winner_user_id))
        if winner_member is None:
            return "ℹ️ Winner member guild дотор олдсонгүй."

        try:
            await winner_member.add_roles(role, reason="Weekly champion")
            return f"👑 {winner_member.mention} → `{WEEKLY_CHAMPION_ROLE_NAME}` role авлаа."
        except discord.HTTPException:
            return "ℹ️ Champion role өгөх үед алдаа гарлаа."

    @commands.command(name="set_result")
    @commands.has_permissions(administrator=True)
    async def set_result(
        self,
        ctx: commands.Context,
        stage_key: str,
        *args: str,
    ) -> None:
        if ctx.guild is None:
            await ctx.send("Энэ command зөвхөн server дотор ажиллана.")
            return

        explicit_game_no: int | None = None
        if args and re.fullmatch(r"\d+", args[0]):
            explicit_game_no = int(args[0])

        ordered_user_ids = [int(user_id) for user_id in ctx.message.raw_mentions]

        if len(ordered_user_ids) != 8:
            await ctx.send(
                f"❌ Яг 8 тоглогч mention хий. Одоо {len(ordered_user_ids)} байна."
            )
            return

        try:
            result = await self.result_service.submit_stage_result(
                guild_id=ctx.guild.id,
                stage_key=stage_key.lower(),
                game_no=explicit_game_no,
                ordered_user_ids=ordered_user_ids,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        embed = self._build_scoreboard_embed(
            title=f"{stage_key.lower()} - Game {result['game_no']} saved",
            result=result,
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

        if result["stage_type"] == "final" and result["stage_finished"]:
            podium_embed = self._build_final_podium_embed(result)
            await ctx.send(embed=podium_embed)

            if result["scoreboard"]:
                role_message = await self._assign_weekly_champion_role(
                    ctx.guild,
                    int(result["scoreboard"][0]["user_id"]),
                )
                if role_message:
                    await ctx.send(role_message)

    @commands.command(name="stage_results")
    @commands.has_permissions(administrator=True)
    async def stage_results(
        self,
        ctx: commands.Context,
        stage_key: str,
    ) -> None:
        if ctx.guild is None:
            await ctx.send("Энэ command зөвхөн server дотор ажиллана.")
            return

        try:
            result = await self.result_service.get_stage_results(
                guild_id=ctx.guild.id,
                stage_key=stage_key.lower(),
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        embed = self._build_scoreboard_embed(
            title=f"{stage_key.lower()} - Current standings",
            result=result,
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

        if result["stage_type"] == "final" and result["stage_finished"]:
            podium_embed = self._build_final_podium_embed(result)
            await ctx.send(embed=podium_embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ResultsCog(bot))