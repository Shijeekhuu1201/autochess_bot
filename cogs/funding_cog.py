import sqlite3
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "bot.db"


def money(value: int) -> str:
    return f"{int(value):,}₮"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables() -> None:
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donor_name TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        db.commit()


def get_active_weekly_tournament() -> Optional[dict]:
    with get_db() as db:
        row = db.execute(
            """
            SELECT *
            FROM tournaments
            WHERE type = 'weekly'
              AND status NOT IN ('completed', 'cancelled')
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None


def parse_name_and_note(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "", ""

    if "|" in text:
        name, note = text.split("|", 1)
        return name.strip(), note.strip()

    return text, ""


def add_platform_donation(donor_name: str, amount: int, note: str = "") -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO platform_donations (donor_name, amount, note)
            VALUES (?, ?, ?)
            """,
            (donor_name, amount, note),
        )
        db.commit()


def get_platform_donations(limit: Optional[int] = None) -> list[dict]:
    with get_db() as db:
        sql = """
            SELECT donor_name, amount, note, created_at
            FROM platform_donations
            ORDER BY amount DESC, id ASC
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)

        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_platform_donation_total() -> int:
    with get_db() as db:
        row = db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM platform_donations
            """
        ).fetchone()
        return int(row["total"] or 0)


def clear_platform_donations() -> int:
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) AS total FROM platform_donations").fetchone()
        count = int(row["total"] or 0)
        db.execute("DELETE FROM platform_donations")
        db.commit()
        return count


def add_tournament_sponsor(tournament_id: int, sponsor_name: str, amount: int, note: str = "") -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO sponsors (tournament_id, sponsor_name, amount, note)
            VALUES (?, ?, ?, ?)
            """,
            (tournament_id, sponsor_name, amount, note),
        )
        db.commit()


def get_tournament_sponsors(tournament_id: int, limit: Optional[int] = None) -> list[dict]:
    with get_db() as db:
        sql = """
            SELECT sponsor_name, amount, note
            FROM sponsors
            WHERE tournament_id = ?
            ORDER BY amount DESC, id ASC
        """
        params: tuple = (tournament_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (tournament_id, limit)

        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_tournament_sponsor_total(tournament_id: int) -> int:
    with get_db() as db:
        row = db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM sponsors
            WHERE tournament_id = ?
            """,
            (tournament_id,),
        ).fetchone()
        return int(row["total"] or 0)


def clear_tournament_sponsors(tournament_id: int) -> int:
    with get_db() as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total
            FROM sponsors
            WHERE tournament_id = ?
            """,
            (tournament_id,),
        ).fetchone()
        count = int(row["total"] or 0)

        db.execute(
            """
            DELETE FROM sponsors
            WHERE tournament_id = ?
            """,
            (tournament_id,),
        )
        db.commit()
        return count


class FundingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_tables()

    def build_platform_embed(self, title: str = "Platform Supporters") -> discord.Embed:
        total = get_platform_donation_total()
        donors = get_platform_donations(limit=20)

        lines = []
        for i, donor in enumerate(donors, start=1):
            extra = f" — {donor['note']}" if donor.get("note") else ""
            lines.append(f"{i}. {donor['donor_name']} — {money(int(donor['amount']))}{extra}")

        description = (
            f"**Total Platform Support:** {money(total)}\n\n"
            f"**Supporters**\n"
            f"{chr(10).join(lines) if lines else 'No platform donors yet.'}"
        )

        return discord.Embed(
            title=title,
            description=description,
            color=discord.Color.blue(),
        )

    def build_sponsor_embed(self, tournament: dict, title_suffix: str = "Tournament Sponsors") -> discord.Embed:
        tournament_id = int(tournament["id"])
        total = get_tournament_sponsor_total(tournament_id)
        sponsors = get_tournament_sponsors(tournament_id, limit=20)

        lines = []
        for i, sponsor in enumerate(sponsors, start=1):
            extra = f" — {sponsor['note']}" if sponsor.get("note") else ""
            lines.append(f"{i}. {sponsor['sponsor_name']} — {money(int(sponsor['amount']))}{extra}")

        description = (
            f"**Tournament:** {tournament['title']}\n"
            f"**Total Sponsored:** {money(total)}\n\n"
            f"**Sponsors**\n"
            f"{chr(10).join(lines) if lines else 'No sponsors yet.'}"
        )

        return discord.Embed(
            title=f"{tournament['title']} - {title_suffix}",
            description=description,
            color=discord.Color.green(),
        )

    @commands.command(name="donate_add")
    @commands.has_permissions(administrator=True)
    async def donate_add(self, ctx: commands.Context, amount: int, *, donor_and_note: str = ""):
        """
        Usage:
        .donate_add 500000 Ideree
        .donate_add 500000 Ideree | bayrllaa
        """
        if amount <= 0:
            await ctx.send("❌ Amount 0-ээс их байх ёстой.")
            return

        donor_name, note = parse_name_and_note(donor_and_note)
        if not donor_name:
            await ctx.send("❌ Нэрээ оруул. Жишээ: `.donate_add 500000 Ideree | bayrllaa`")
            return

        add_platform_donation(donor_name=donor_name, amount=amount, note=note)
        await ctx.send(embed=self.build_platform_embed("Platform Supporters"))

    @commands.command(name="donate_list")
    async def donate_list(self, ctx: commands.Context):
        await ctx.send(embed=self.build_platform_embed("Platform Supporters"))

    @commands.command(name="donate_clear")
    @commands.has_permissions(administrator=True)
    async def donate_clear(self, ctx: commands.Context):
        count = clear_platform_donations()
        await ctx.send(f"✅ Platform donations cleared. Removed: **{count}**")

    @commands.command(name="sponsor_add")
    @commands.has_permissions(administrator=True)
    async def sponsor_add(self, ctx: commands.Context, amount: int, *, sponsor_and_note: str = ""):
        """
        Usage:
        .sponsor_add 300000 Stake
        .sponsor_add 300000 Stake | main sponsor
        """
        if amount <= 0:
            await ctx.send("❌ Amount 0-ээс их байх ёстой.")
            return

        sponsor_name, note = parse_name_and_note(sponsor_and_note)
        if not sponsor_name:
            await ctx.send("❌ Sponsor нэрээ оруул. Жишээ: `.sponsor_add 300000 Stake | main sponsor`")
            return

        tournament = get_active_weekly_tournament()
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        add_tournament_sponsor(
            tournament_id=int(tournament["id"]),
            sponsor_name=sponsor_name,
            amount=amount,
            note=note,
        )
        await ctx.send(embed=self.build_sponsor_embed(tournament, "Tournament Sponsors"))

    @commands.command(name="sponsor_list")
    async def sponsor_list(self, ctx: commands.Context):
        tournament = get_active_weekly_tournament()
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        await ctx.send(embed=self.build_sponsor_embed(tournament, "Tournament Sponsors"))

    @commands.command(name="sponsor_clear")
    @commands.has_permissions(administrator=True)
    async def sponsor_clear(self, ctx: commands.Context):
        tournament = get_active_weekly_tournament()
        if not tournament:
            await ctx.send("❌ Идэвхтэй weekly tournament алга.")
            return

        count = clear_tournament_sponsors(int(tournament["id"]))
        await ctx.send(f"✅ `{tournament['title']}` tournament sponsors cleared. Removed: **{count}**")

    @donate_add.error
    @sponsor_add.error
    async def funding_add_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Энэ command зөвхөн admin хэрэглэнэ.")
            return

        if isinstance(error, commands.BadArgument):
            await ctx.send("❌ Amount буруу байна. Жишээ: `.donate_add 500000 Ideree | bayrllaa`")
            return

        raise error

    @donate_clear.error
    @sponsor_clear.error
    async def funding_admin_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Энэ command зөвхөн admin хэрэглэнэ.")
            return

        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(FundingCog(bot))