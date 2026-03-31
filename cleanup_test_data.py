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


def count_rows(db: sqlite3.Connection, query: str, params: tuple[object, ...]) -> int:
    row = db.execute(query, params).fetchone()
    return int(row[0] or 0) if row is not None else 0


def cleanup_test_data(db: sqlite3.Connection, guild_id: int) -> dict[str, int]:
    summary: dict[str, int] = {}

    summary["test_tournaments"] = count_rows(
        db,
        """
        SELECT COUNT(*)
        FROM tournaments
        WHERE guild_id = ?
          AND type = 'weekly'
          AND title LIKE ?
        """,
        (guild_id, f"{TEST_TITLE_PREFIX}%"),
    )

    summary["fake_profiles"] = count_rows(
        db,
        "SELECT COUNT(*) FROM player_profiles WHERE user_id >= ?",
        (FAKE_USER_ID_START,),
    )
    summary["fake_stats"] = count_rows(
        db,
        "SELECT COUNT(*) FROM player_stats WHERE user_id >= ?",
        (FAKE_USER_ID_START,),
    )
    summary["fake_results"] = count_rows(
        db,
        "SELECT COUNT(*) FROM player_tournament_results WHERE user_id >= ?",
        (FAKE_USER_ID_START,),
    )
    summary["fake_entries"] = count_rows(
        db,
        "SELECT COUNT(*) FROM tournament_entries WHERE user_id >= ?",
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
    db.execute(
        """
        DELETE FROM player_tournament_results
        WHERE user_id >= ?
        """,
        (FAKE_USER_ID_START,),
    )
    db.execute(
        """
        DELETE FROM tournaments
        WHERE guild_id = ?
          AND type = 'weekly'
          AND title LIKE ?
        """,
        (guild_id, f"{TEST_TITLE_PREFIX}%"),
    )
    db.execute("DELETE FROM player_stats WHERE user_id >= ?", (FAKE_USER_ID_START,))
    db.execute("DELETE FROM player_profiles WHERE user_id >= ?", (FAKE_USER_ID_START,))

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove fake/test tournament data without wiping real profiles.",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=SETTINGS.guild_id,
        help="Discord guild id. Defaults to GUILD_ID from .env",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show how much test data would be removed without deleting it.",
    )
    args = parser.parse_args()

    if args.guild_id <= 0:
        raise SystemExit("GUILD_ID алга. --guild-id өг эсвэл .env дээр GUILD_ID тохируул.")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON;")

    try:
        init_db(db)
        summary = cleanup_test_data(db, args.guild_id) if not args.dry_run else cleanup_test_data_preview(db, args.guild_id)
        if args.dry_run:
            print("[DRY RUN] Cleanup preview")
        else:
            db.commit()
            print("[OK] Test data cleanup completed")

        print(f"Test tournaments: {summary['test_tournaments']}")
        print(f"Fake entries: {summary['fake_entries']}")
        print(f"Fake profiles: {summary['fake_profiles']}")
        print(f"Fake stats: {summary['fake_stats']}")
        print(f"Fake results: {summary['fake_results']}")
    finally:
        db.close()


def cleanup_test_data_preview(db: sqlite3.Connection, guild_id: int) -> dict[str, int]:
    return {
        "test_tournaments": count_rows(
            db,
            """
            SELECT COUNT(*)
            FROM tournaments
            WHERE guild_id = ?
              AND type = 'weekly'
              AND title LIKE ?
            """,
            (guild_id, f"{TEST_TITLE_PREFIX}%"),
        ),
        "fake_profiles": count_rows(
            db,
            "SELECT COUNT(*) FROM player_profiles WHERE user_id >= ?",
            (FAKE_USER_ID_START,),
        ),
        "fake_stats": count_rows(
            db,
            "SELECT COUNT(*) FROM player_stats WHERE user_id >= ?",
            (FAKE_USER_ID_START,),
        ),
        "fake_results": count_rows(
            db,
            "SELECT COUNT(*) FROM player_tournament_results WHERE user_id >= ?",
            (FAKE_USER_ID_START,),
        ),
        "fake_entries": count_rows(
            db,
            "SELECT COUNT(*) FROM tournament_entries WHERE user_id >= ?",
            (FAKE_USER_ID_START,),
        ),
    }


if __name__ == "__main__":
    main()
