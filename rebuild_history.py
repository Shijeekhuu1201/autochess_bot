from __future__ import annotations

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "bot.db"


def ensure_table_and_columns(db: sqlite3.Connection) -> None:
    db.execute("""
    CREATE TABLE IF NOT EXISTS player_tournament_results (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id     INTEGER NOT NULL,
        user_id           INTEGER NOT NULL,
        display_name      TEXT NOT NULL,
        season_name       TEXT NOT NULL,
        tournament_title  TEXT NOT NULL,
        final_rank        INTEGER NOT NULL,
        prize_amount      INTEGER NOT NULL DEFAULT 0,
        total_points      INTEGER NOT NULL DEFAULT 0,
        recorded_at       TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    db.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_ptr_unique_tournament_user
    ON player_tournament_results(tournament_id, user_id)
    """)

    columns = {row[1] for row in db.execute("PRAGMA table_info(tournaments)").fetchall()}
    if "season_name" not in columns:
        db.execute("ALTER TABLE tournaments ADD COLUMN season_name TEXT NOT NULL DEFAULT 'Season 1'")


def rebuild_all(db: sqlite3.Connection) -> None:
    # reset derived tables only
    db.execute("DELETE FROM payouts")
    db.execute("DELETE FROM player_tournament_results")
    db.execute("DELETE FROM player_stats")
    db.execute("DELETE FROM player_profiles")

    tournaments = db.execute("""
        SELECT id, type, title, season_name, prize_1, prize_2, prize_3
        FROM tournaments
        WHERE status = 'completed'
        ORDER BY id ASC
    """).fetchall()

    for t in tournaments:
        tournament_id = int(t["id"])
        prize_map = {
            1: int(t["prize_1"] or 0),
            2: int(t["prize_2"] or 0),
            3: int(t["prize_3"] or 0),
        }

        standings = db.execute("""
            SELECT
                te.id AS entry_id,
                te.user_id,
                te.display_name,
                ss.final_position,
                ss.total_points
            FROM stage_slots ss
            JOIN stages s
              ON s.id = ss.stage_id
            JOIN tournament_entries te
              ON te.id = ss.current_entry_id
            WHERE s.tournament_id = ?
              AND s.stage_type = 'final'
              AND ss.final_position IS NOT NULL
            ORDER BY ss.final_position ASC, ss.slot_no ASC
        """, (tournament_id,)).fetchall()

        if not standings:
            print(f"[SKIP] tournament #{tournament_id} has no final standings")
            continue

        for row in standings:
            user_id = int(row["user_id"])
            display_name = str(row["display_name"])
            final_rank = int(row["final_position"])
            total_points = int(row["total_points"] or 0)
            prize_amount = prize_map.get(final_rank, 0)

            db.execute("""
                INSERT INTO player_profiles (user_id, display_name)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, display_name))

            db.execute("""
                INSERT INTO player_stats (user_id)
                VALUES (?)
                ON CONFLICT(user_id) DO NOTHING
            """, (user_id,))

            weekly_inc = 1 if t["type"] == "weekly" else 0
            special_inc = 1 if t["type"] == "special" else 0
            monthly_inc = 1 if t["type"] == "monthly" else 0
            champ_inc = 1 if final_rank == 1 else 0
            runner_inc = 1 if final_rank == 2 else 0
            third_inc = 1 if final_rank == 3 else 0
            podium_inc = 1 if final_rank in (1, 2, 3) else 0
            wins_inc = 1 if final_rank == 1 else 0

            db.execute("""
                UPDATE player_stats
                SET tournaments_played = tournaments_played + 1,
                    weekly_played = weekly_played + ?,
                    special_played = special_played + ?,
                    monthly_played = monthly_played + ?,
                    championships = championships + ?,
                    runner_ups = runner_ups + ?,
                    third_places = third_places + ?,
                    podiums = podiums + ?,
                    total_wins = total_wins + ?,
                    total_prize_money = total_prize_money + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (
                weekly_inc,
                special_inc,
                monthly_inc,
                champ_inc,
                runner_inc,
                third_inc,
                podium_inc,
                wins_inc,
                prize_amount,
                user_id,
            ))

            if prize_amount > 0:
                db.execute("""
                    INSERT INTO payouts (tournament_id, entry_id, final_rank, amount)
                    VALUES (?, ?, ?, ?)
                """, (
                    tournament_id,
                    int(row["entry_id"]),
                    final_rank,
                    prize_amount,
                ))

            db.execute("""
                INSERT INTO player_tournament_results (
                    tournament_id,
                    user_id,
                    display_name,
                    season_name,
                    tournament_title,
                    final_rank,
                    prize_amount,
                    total_points
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tournament_id,
                user_id,
                display_name,
                str(t["season_name"] or "Season 1"),
                str(t["title"]),
                final_rank,
                prize_amount,
                total_points,
            ))

        print(f"[OK] rebuilt tournament #{tournament_id} - {t['title']}")


def main() -> None:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        ensure_table_and_columns(db)
        rebuild_all(db)
        db.commit()
        print("DONE: history + leaderboard rebuilt successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    main()