from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from config.settings import SETTINGS
from core.db import SCHEMA_PATH

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / SETTINGS.db_path

FAKE_USER_ID_START = 990_000_000_000_000_000
TEST_TITLE_PREFIX = "[TEST]"


def init_db(db: sqlite3.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    db.executescript(schema_sql)


def get_next_season_name(db: sqlite3.Connection, guild_id: int) -> str:
    row = db.execute(
        """
        SELECT season_name
        FROM tournaments
        WHERE guild_id = ? AND type = 'weekly'
          AND game_key = 'autochess'
          AND format_key = 'solo_32'
        ORDER BY id DESC
        LIMIT 1
        """,
        (guild_id,),
    ).fetchone()
    if row is None or not row["season_name"]:
        return "Season 1"

    season_name = str(row["season_name"]).strip()
    if season_name.lower().startswith("season "):
        suffix = season_name[7:].strip()
        if suffix.isdigit():
            return f"Season {int(suffix) + 1}"
    return "Season 1"


def ensure_no_active_weekly(db: sqlite3.Connection, guild_id: int) -> None:
    row = db.execute(
        """
        SELECT id, title, status
        FROM tournaments
        WHERE guild_id = ?
          AND type = 'weekly'
          AND game_key = 'autochess'
          AND format_key = 'solo_32'
          AND status NOT IN ('completed', 'cancelled')
        ORDER BY id DESC
        LIMIT 1
        """,
        (guild_id,),
    ).fetchone()
    if row is not None:
        raise RuntimeError(
            f"Active weekly байна: #{row['id']} {row['title']} [{row['status']}]. "
            "Эхлээд дуусгах эсвэл --reset-test-data ашигла."
        )


def reset_old_test_data(db: sqlite3.Connection, guild_id: int) -> int:
    rows = db.execute(
        """
        SELECT id
        FROM tournaments
        WHERE guild_id = ?
          AND type = 'weekly'
          AND game_key = 'autochess'
          AND format_key = 'solo_32'
          AND title LIKE ?
        """,
        (guild_id, f"{TEST_TITLE_PREFIX}%"),
    ).fetchall()

    removed = 0
    for row in rows:
        db.execute("DELETE FROM tournaments WHERE id = ?", (int(row["id"]),))
        removed += 1

    db.execute(
        """
        DELETE FROM player_tournament_results
        WHERE user_id >= ?
        """,
        (FAKE_USER_ID_START,),
    )
    db.execute(
        """
        DELETE FROM payouts
        WHERE entry_id IN (
            SELECT id FROM tournament_entries WHERE user_id >= ?
        )
        """,
        (FAKE_USER_ID_START,),
    )
    db.execute("DELETE FROM player_stats WHERE user_id >= ?", (FAKE_USER_ID_START,))
    db.execute("DELETE FROM player_profiles WHERE user_id >= ?", (FAKE_USER_ID_START,))
    return removed


def create_test_tournament(
    db: sqlite3.Connection,
    guild_id: int,
    player_count: int,
    title: str,
) -> tuple[int, str]:
    season_name = get_next_season_name(db, guild_id)
    cursor = db.execute(
        """
        INSERT INTO tournaments (
            guild_id,
            type,
            game_key,
            format_key,
            slug,
            title,
            season_name,
            entry_fee,
            max_players,
            lobby_size,
            bo_count,
            prize_total,
            prize_1,
            prize_2,
            prize_3,
            start_time,
            checkin_time,
            rules_text,
            status,
            created_by
        )
        VALUES (?, 'weekly', 'autochess', 'solo_32', ?, ?, ?, ?, ?, 8, 2, ?, ?, ?, ?, ?, ?, ?, 'registration_locked', ?)
        """,
        (
            guild_id,
            f"autochess-{season_name.lower().replace(' ', '-')}-{title.lower().replace(' ', '-')}",
            title,
            season_name,
            5000,
            player_count,
            500000,
            250000,
            150000,
            100000,
            "Saturday 20:00",
            "Saturday 19:30",
            "Seeded test tournament for bracket/profile/leaderboard flow.",
            guild_id or 1,
        ),
    )
    tournament_id = int(cursor.lastrowid)

    for idx in range(1, player_count + 1):
        user_id = FAKE_USER_ID_START + idx
        display_name = f"TestPlayer{idx:02d}"
        db.execute(
            """
            INSERT INTO player_profiles (user_id, display_name)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name = excluded.display_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, display_name),
        )
        db.execute(
            """
            INSERT INTO tournament_entries (
                tournament_id,
                user_id,
                display_name,
                register_order,
                payment_status,
                status,
                confirmed_at
            )
            VALUES (?, ?, ?, ?, 'confirmed', 'confirmed', CURRENT_TIMESTAMP)
            """,
            (tournament_id, user_id, display_name, idx),
        )

    return tournament_id, season_name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a seeded 32-player weekly tournament for testing.",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=SETTINGS.guild_id,
        help="Discord guild id. Defaults to GUILD_ID from .env",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=32,
        help="Number of fake confirmed players to create. Defaults to 32.",
    )
    parser.add_argument(
        "--title",
        default=f"{TEST_TITLE_PREFIX} Weekly Auto Chess Cup",
        help="Tournament title.",
    )
    parser.add_argument(
        "--reset-test-data",
        action="store_true",
        help="Delete older [TEST] weekly tournaments and fake profiles first.",
    )
    args = parser.parse_args()

    if args.guild_id <= 0:
        raise SystemExit("GUILD_ID алга. --guild-id өг эсвэл .env дээр GUILD_ID тохируул.")
    if args.players <= 0 or args.players % 8 != 0:
        raise SystemExit("--players нь 8-аар хуваагддаг эерэг тоо байх ёстой.")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON;")

    try:
        init_db(db)

        removed = 0
        if args.reset_test_data:
            removed = reset_old_test_data(db, args.guild_id)

        ensure_no_active_weekly(db, args.guild_id)
        tournament_id, season_name = create_test_tournament(
            db,
            guild_id=args.guild_id,
            player_count=args.players,
            title=args.title,
        )
        db.commit()

        print(f"[OK] Seeded weekly tournament #{tournament_id}")
        print(f"Title: {args.title}")
        print(f"Season: {season_name}")
        print(f"Guild: {args.guild_id}")
        print(f"Players: {args.players} confirmed")
        print("Status: registration_locked")
        if removed:
            print(f"Removed old [TEST] tournaments: {removed}")
        print("")
        print("Next Discord commands:")
        print(".weekly_status")
        print(".weekly_make_zones")
        print(".weekly_zone")
    finally:
        db.close()


if __name__ == "__main__":
    main()
