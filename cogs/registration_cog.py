from __future__ import annotations

import random
from typing import Any

import discord
from discord.ext import commands

from services.registration_service import RegistrationService
from services.role_sync_service import RoleSyncService

ROLL_THUMB_URL = "https://media.discordapp.net/attachments/1484376187211092098/1484558344206159942/145b1eda-6bcf-4777-9f07-56db26931803.png?ex=69beaa33&is=69bd58b3&hm=20e6f5d506db173495bb16d450be32a03b7d63d4a35f11f094951042a67d9859&=&format=webp&quality=lossless&width=960&height=960"
ROLL_SITE_URL = "https://www.chessofmongolia.site/"
ROLL_TITLE = "🎲・ᴄʜᴇss-ᴏꜰ-ᴍᴏɴɢᴏʟɪᴀ"
ROLL_CLOSED_TITLE = "🛑・ᴄʜᴇss-ᴏꜰ-ᴍᴏɴɢᴏʟɪᴀ"

DUO_TITLE = "👑・ᴅᴜᴏ-ʀᴇɢɪsᴛʀᴀᴛɪᴏɴ"
DUO_CLOSED_TITLE = "🛑・ᴅᴜᴏ-ʀᴇɢɪsᴛʀᴀᴛɪᴏɴ"
DUO_THUMB_URL = ROLL_THUMB_URL
DUO_SITE_URL = ROLL_SITE_URL


class DuoRegisterView(discord.ui.View):
    def __init__(self, cog: "RegistrationCog", channel_id: int, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.add_item(discord.ui.Button(label="ChessOfMongolia.Site", style=discord.ButtonStyle.link, url=DUO_SITE_URL))

        if disabled:
            for item in self.children:
                item.disabled = True

    @discord.ui.button(label="Join Duo", style=discord.ButtonStyle.success)
    async def join_duo(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.cog.handle_duo_join(interaction, self.channel_id)

    @discord.ui.button(label="Leave Duo", style=discord.ButtonStyle.secondary)
    async def leave_duo(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.cog.handle_duo_leave(interaction, self.channel_id)

class RegistrationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.registration_service = RegistrationService(bot.db_path)
        self.role_sync_service = RoleSyncService(bot.db_path)
        self.roll_sessions: dict[int, dict[str, Any]] = {}
        self.duo_sessions: dict[int, dict[str, Any]] = {}

    def _is_moderator(self, member: discord.Member) -> bool:
        perms = member.guild_permissions
        return any(
            (
                perms.administrator,
                perms.manage_guild,
                perms.manage_channels,
                perms.manage_messages,
                perms.moderate_members,
            )
        )

    async def _ensure_guild_context(self, ctx: commands.Context) -> bool:
        if ctx.guild is not None:
            return True
        await ctx.send("Энэ command зөвхөн server дотор ажиллана.")
        return False

    async def _ensure_moderator(self, ctx: commands.Context) -> bool:
        if not await self._ensure_guild_context(ctx):
            return False
        assert ctx.guild is not None
        if isinstance(ctx.author, discord.Member) and self._is_moderator(ctx.author):
            return True
        await ctx.send("❌ Энэ command-г зөвхөн moderator эрхтэй хүн ашиглана.")
        return False

    def _get_roll_session(self, channel_id: int) -> dict[str, Any]:
        session = self.roll_sessions.get(channel_id)
        if session is None:
            session = {
                "scores": {},
                "message_id": None,
            }
            self.roll_sessions[channel_id] = session
        return session

    def _rank_rolls(self, scores: dict[int, int]) -> list[tuple[int, int]]:
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    def _build_roll_embed(self, ranked: list[tuple[int, int]], *, closed: bool = False) -> discord.Embed:
        embed = discord.Embed(
            title=ROLL_CLOSED_TITLE if closed else ROLL_TITLE,
            description="Roll session хаагдлаа." if closed else None,
            color=discord.Color.gold() if not closed else discord.Color.dark_gold(),
        )
        embed.set_thumbnail(url=ROLL_THUMB_URL)

        if ranked:
            lines = [f"**{index}.** <@{user_id}> — **{score}**" for index, (user_id, score) in enumerate(ranked, start=1)]
            embed.add_field(name="Players", value="\n".join(lines[:25]), inline=False)
        else:
            embed.add_field(name="Players", value="Одоогоор roll хийсэн хүн алга.", inline=False)

        embed.add_field(name="Site", value=f"[ChessOfMongolia.Site]({ROLL_SITE_URL})", inline=False)
        embed.set_footer(text="Chess Of Mongolia • Roll")
        return embed

    async def _upsert_roll_message(
        self,
        ctx: commands.Context,
        session: dict[str, Any],
        embed: discord.Embed,
    ) -> None:
        message_id = int(session.get("message_id") or 0)
        message: discord.Message | None = None

        if message_id:
            try:
                message = await ctx.channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        if message is None:
            message = await ctx.send(embed=embed)
            session["message_id"] = int(message.id)
            return

        await message.edit(embed=embed)

    def _get_duo_session(self, channel_id: int) -> dict[str, Any]:
        session = self.duo_sessions.get(channel_id)
        if session is None:
            session = {
                "registrants": [],
                "confirmed": set(),
                "teams": [],
                "message_id": None,
                "closed": False,
                "winners": [],
            }
            self.duo_sessions[channel_id] = session
        return session

    def _build_duo_embed(self, session: dict[str, Any]) -> discord.Embed:
        closed = bool(session.get("closed"))
        registrants = list(session.get("registrants") or [])
        confirmed = set(session.get("confirmed") or set())
        teams = list(session.get("teams") or [])
        winners = list(session.get("winners") or [])

        embed = discord.Embed(
            title=DUO_CLOSED_TITLE if closed else DUO_TITLE,
            description="Moderator зөвлөсөн тэмдэг тавьсан хүмүүс л official оролцогчид гэж тооцогдоно.",
            color=discord.Color.blurple() if not closed else discord.Color.dark_blue(),
        )
        embed.set_thumbnail(url=DUO_THUMB_URL)

        lines: list[str] = []
        for index, user_id in enumerate(registrants, start=1):
            marker = "✅ " if user_id in confirmed else ""
            lines.append(f"**{index}.** {marker}<@{user_id}>")

        embed.add_field(
            name="Players",
            value="\n".join(lines[:50]) if lines else "Одоогоор duo бүртгэлд хүн алга.",
            inline=False,
        )

        embed.add_field(name="Registered", value=str(len(registrants)), inline=True)
        embed.add_field(name="Confirmed", value=str(len(confirmed)), inline=True)
        embed.add_field(name="Teams", value=str(len(teams)), inline=True)
        embed.add_field(name="Status", value="Closed" if closed else "Open", inline=True)

        if teams:
            team_lines = [
                f"**{index}.** <@{team[0]}> + <@{team[1]}>"
                for index, team in enumerate(teams, start=1)
                if len(team) == 2
            ]
            embed.add_field(name="Teams", value="\n".join(team_lines[:25]), inline=False)

        if winners:
            winner_mentions = " + ".join(f"<@{user_id}>" for user_id in winners)
            losses = max(len(confirmed) - len(winners), 0)
            embed.add_field(
                name="Result",
                value=f"Winner: {winner_mentions}\nLosses accepted: **{losses}**",
                inline=False,
            )

        embed.add_field(name="Site", value=f"[ChessOfMongolia.Site]({DUO_SITE_URL})", inline=False)
        embed.set_footer(text="Chess Of Mongolia • Duo")
        return embed

    async def _upsert_duo_message(
        self,
        channel: discord.abc.Messageable,
        session: dict[str, Any],
        *,
        disabled: bool | None = None,
    ) -> discord.Message:
        embed = self._build_duo_embed(session)
        disable_buttons = bool(session.get("closed")) if disabled is None else disabled
        view = DuoRegisterView(self, int(getattr(channel, "id")), disabled=disable_buttons)

        message_id = int(session.get("message_id") or 0)
        message: discord.Message | None = None

        if message_id and hasattr(channel, "fetch_message"):
            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        if message is None:
            message = await channel.send(embed=embed, view=view)
            session["message_id"] = int(message.id)
            return message

        await message.edit(embed=embed, view=view)
        return message

    async def handle_duo_join(self, interaction: discord.Interaction, channel_id: int) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Энэ button зөвхөн server дотор ажиллана.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Guild member олдсонгүй.", ephemeral=True)
            return

        session = self._get_duo_session(channel_id)
        if session.get("closed"):
            await interaction.response.send_message("❌ Duo бүртгэл хаагдсан байна.", ephemeral=True)
            return

        registrants = session["registrants"]
        assert isinstance(registrants, list)
        if interaction.user.id in registrants:
            await interaction.response.send_message("ℹ️ Та энэ duo session дээр аль хэдийн бүртгүүлсэн байна.", ephemeral=True)
            return

        registrants.append(interaction.user.id)
        await self._upsert_duo_message(interaction.channel, session)
        await interaction.response.send_message("✅ Duo бүртгэлд орлоо.", ephemeral=True)

    async def handle_duo_leave(self, interaction: discord.Interaction, channel_id: int) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Энэ button зөвхөн server дотор ажиллана.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Guild member олдсонгүй.", ephemeral=True)
            return

        session = self._get_duo_session(channel_id)
        if session.get("closed"):
            await interaction.response.send_message("❌ Duo бүртгэл хаагдсан байна.", ephemeral=True)
            return

        registrants = session["registrants"]
        confirmed = session["confirmed"]
        teams = session["teams"]
        assert isinstance(registrants, list)
        assert isinstance(confirmed, set)
        assert isinstance(teams, list)

        if interaction.user.id not in registrants:
            await interaction.response.send_message("ℹ️ Та duo бүртгэл дотор алга.", ephemeral=True)
            return

        registrants.remove(interaction.user.id)
        confirmed.discard(interaction.user.id)
        session["teams"] = [team for team in teams if interaction.user.id not in team]
        await self._upsert_duo_message(interaction.channel, session)
        await interaction.response.send_message("✅ Duo бүртгэлээс гарлаа.", ephemeral=True)

    @commands.hybrid_command(name="me")
    async def me(self, ctx: commands.Context) -> None:
        await ctx.send(f"Таны user id: `{ctx.author.id}`")

    @commands.hybrid_command(name="join")
    async def join(self, ctx: commands.Context) -> None:
        if not await self._ensure_guild_context(ctx):
            return
        assert ctx.guild is not None

        try:
            result = await self.registration_service.join_weekly(
                guild_id=ctx.guild.id,
                user_id=ctx.author.id,
                display_name=ctx.author.display_name,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        entry = result["entry"]
        summary = result["summary"]
        tournament = result["tournament"]

        await self.role_sync_service.sync_registration_roles_for_tournament(
            ctx.guild,
            int(tournament["id"]),
        )

        await ctx.send(
            f"✅ {ctx.author.mention} амжилттай бүртгэгдлээ.\n"
            f"Бүртгэлийн дараалал: **{entry['register_order']}**\n"
            f"Registered: **{summary['registered_count']}**\n"
            f"Confirmed: **{summary['confirmed_count']}/32**\n"
            f"Waitlist: **{summary['waitlist_count']}**"
        )

    @commands.hybrid_command(name="leave")
    async def leave(self, ctx: commands.Context) -> None:
        if not await self._ensure_guild_context(ctx):
            return
        assert ctx.guild is not None

        try:
            result = await self.registration_service.leave_weekly(
                guild_id=ctx.guild.id,
                user_id=ctx.author.id,
            )
        except ValueError as e:
            await ctx.send(f"❌ {e}")
            return

        summary = result["summary"]
        tournament = result["tournament"]

        await self.role_sync_service.sync_registration_roles_for_tournament(
            ctx.guild,
            int(tournament["id"]),
        )

        await ctx.send(
            f"✅ {ctx.author.mention} tournament-оос хасагдлаа.\n"
            f"Registered: **{summary['registered_count']}**\n"
            f"Confirmed: **{summary['confirmed_count']}/32**\n"
            f"Waitlist: **{summary['waitlist_count']}**"
        )

    @commands.hybrid_command(name="roll")
    async def roll(self, ctx: commands.Context) -> None:
        if not await self._ensure_guild_context(ctx):
            return

        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer()

        session = self._get_roll_session(ctx.channel.id)
        scores = session["scores"]
        assert isinstance(scores, dict)

        if ctx.author.id in scores:
            await ctx.send("❌ Та энэ session дээр аль хэдийн roll хийсэн байна. Дахин эхлүүлэх бол `.stoproll` хийнэ.")
            return

        scores[ctx.author.id] = random.randint(1, 100)
        ranked = self._rank_rolls(scores)
        await self._upsert_roll_message(ctx, session, self._build_roll_embed(ranked))

    @commands.hybrid_command(name="showroll")
    async def showroll(self, ctx: commands.Context) -> None:
        if not await self._ensure_guild_context(ctx):
            return

        session = self.roll_sessions.get(ctx.channel.id)
        scores = (session or {}).get("scores", {})
        if not isinstance(scores, dict) or not scores:
            await ctx.send("ℹ️ Одоогоор roll хийсэн хүн алга.")
            return

        await self._upsert_roll_message(ctx, self._get_roll_session(ctx.channel.id), self._build_roll_embed(self._rank_rolls(scores)))

    @commands.hybrid_command(name="stoproll")
    async def stoproll(self, ctx: commands.Context) -> None:
        if not await self._ensure_guild_context(ctx):
            return

        session = self.roll_sessions.get(ctx.channel.id)
        scores = (session or {}).get("scores", {})
        if not isinstance(scores, dict) or not scores:
            await ctx.send("ℹ️ Зогсоох roll session алга.")
            return

        await self._upsert_roll_message(ctx, self._get_roll_session(ctx.channel.id), self._build_roll_embed(self._rank_rolls(scores), closed=True))
        self.roll_sessions.pop(ctx.channel.id, None)
        await ctx.send("🛑 Roll session хаагдлаа.")

    @commands.hybrid_command(name="duo_start")
    async def duo_start(self, ctx: commands.Context) -> None:
        if not await self._ensure_moderator(ctx):
            return
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer()

        session = {
            "registrants": [],
            "confirmed": set(),
            "teams": [],
            "message_id": None,
            "closed": False,
            "winners": [],
        }
        self.duo_sessions[ctx.channel.id] = session
        await self._upsert_duo_message(ctx.channel, session)
        await ctx.send("✅ Duo бүртгэл эхэллээ.")

    @commands.hybrid_command(name="legacy_duo")
    async def duo(
        self,
        ctx: commands.Context,
        member_one: discord.Member,
        member_two: discord.Member | None = None,
    ) -> None:
        if not await self._ensure_guild_context(ctx):
            return
        assert ctx.guild is not None

        session = self.duo_sessions.get(ctx.channel.id)
        if session is None:
            await ctx.send("Энэ channel дээр duo session алга. Эхлээд `.duo_start` хий.")
            return
        if session.get("closed"):
            await ctx.send("❌ Duo бүртгэл хаагдсан байна.")
            return

        if member_two is None:
            if not isinstance(ctx.author, discord.Member):
                await ctx.send("❌ Guild member олдсонгүй.")
                return
            player_one = ctx.author
            player_two = member_one
        else:
            if not await self._ensure_moderator(ctx):
                return
            player_one = member_one
            player_two = member_two

        if player_one.id == player_two.id:
            await ctx.send("❌ Duo team-д 2 өөр хүн байх ёстой.")
            return

        confirmed = session["confirmed"]
        teams = session["teams"]
        assert isinstance(confirmed, set)
        assert isinstance(teams, list)

        if player_one.id not in confirmed or player_two.id not in confirmed:
            await ctx.send("❌ `.duo` хийх 2 хүн хоёулаа official participant (`✅`) байх ёстой.")
            return

        paired_ids = {user_id for team in teams for user_id in team}
        if player_one.id in paired_ids or player_two.id in paired_ids:
            await ctx.send("❌ Эдгээр хүмүүсийн нэг нь duo team-д аль хэдийн орсон байна.")
            return

        teams.append((player_one.id, player_two.id))
        await self._upsert_duo_message(ctx.channel, session)
        await ctx.send(f"✅ Duo team үүслээ: {player_one.mention} + {player_two.mention}")

    @commands.hybrid_command(name="duo_show")
    async def duo_show(self, ctx: commands.Context) -> None:
        if not await self._ensure_guild_context(ctx):
            return

        session = self.duo_sessions.get(ctx.channel.id)
        if session is None:
            await ctx.send("ℹ️ Энэ channel дээр duo session алга.")
            return

        await self._upsert_duo_message(ctx.channel, session)

    @commands.hybrid_command(name="duo_confirm")
    async def duo_confirm(self, ctx: commands.Context, member: discord.Member) -> None:
        if not await self._ensure_moderator(ctx):
            return

        session = self.duo_sessions.get(ctx.channel.id)
        if session is None:
            await ctx.send("ℹ️ Энэ channel дээр duo session алга.")
            return

        registrants = session["registrants"]
        confirmed = session["confirmed"]
        assert isinstance(registrants, list)
        assert isinstance(confirmed, set)

        if member.id not in registrants:
            await ctx.send("❌ Энэ хэрэглэгч duo бүртгэл дотор алга.")
            return

        confirmed.add(member.id)
        await self._upsert_duo_message(ctx.channel, session)
        await ctx.send(f"✅ {member.mention} official participant боллоо.")

    @commands.hybrid_command(name="duo_unconfirm")
    async def duo_unconfirm(self, ctx: commands.Context, member: discord.Member) -> None:
        if not await self._ensure_moderator(ctx):
            return

        session = self.duo_sessions.get(ctx.channel.id)
        if session is None:
            await ctx.send("ℹ️ Энэ channel дээр duo session алга.")
            return

        confirmed = session["confirmed"]
        teams = session["teams"]
        assert isinstance(confirmed, set)
        assert isinstance(teams, list)
        if member.id not in confirmed:
            await ctx.send("ℹ️ Энэ хэрэглэгч дээр official тэмдэг байхгүй байна.")
            return

        confirmed.discard(member.id)
        session["teams"] = [team for team in teams if member.id not in team]
        await self._upsert_duo_message(ctx.channel, session)
        await ctx.send(f"✅ {member.mention} official participant-оос хасагдлаа.")

    @commands.hybrid_command(name="duo_stop")
    async def duo_stop(self, ctx: commands.Context) -> None:
        if not await self._ensure_moderator(ctx):
            return

        session = self.duo_sessions.get(ctx.channel.id)
        if session is None:
            await ctx.send("ℹ️ Энэ channel дээр duo session алга.")
            return

        session["closed"] = True
        await self._upsert_duo_message(ctx.channel, session, disabled=True)
        await ctx.send("🛑 Duo бүртгэл хаагдлаа.")

    @commands.hybrid_command(name="legacy_duo_win")
    async def win(self, ctx: commands.Context, member_one: discord.Member, member_two: discord.Member) -> None:
        if not await self._ensure_moderator(ctx):
            return
        if member_one.id == member_two.id:
            await ctx.send("❌ Ялагчид 2 өөр хүн байх ёстой.")
            return

        session = self.duo_sessions.get(ctx.channel.id)
        if session is None:
            await ctx.send("ℹ️ Энэ channel дээр duo session алга.")
            return

        confirmed = session["confirmed"]
        teams = session["teams"]
        assert isinstance(confirmed, set)
        assert isinstance(teams, list)
        winner_ids = {member_one.id, member_two.id}

        if not winner_ids.issubset(confirmed):
            await ctx.send("❌ `/win` хийх хүмүүс duo дээр ✅ тэмдэгтэй official participant байх ёстой.")
            return

        if teams and not any(winner_ids == set(team) for team in teams):
            await ctx.send("❌ Winner болох 2 хүн нэг duo team дотор байх ёстой.")
            return

        session["winners"] = [member_one.id, member_two.id]
        session["closed"] = True

        await self._upsert_duo_message(ctx.channel, session, disabled=True)

        losses = sorted(user_id for user_id in confirmed if user_id not in winner_ids)
        loss_mentions = ", ".join(f"<@{user_id}>" for user_id in losses) if losses else "-"
        await ctx.send(
            f"🏆 Winner: {member_one.mention} + {member_two.mention}\n"
            f"Loss accepted: **{len(losses)}**\n"
            f"Players: {loss_mentions}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RegistrationCog(bot))
