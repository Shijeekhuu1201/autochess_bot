from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from config.settings import SETTINGS
from core.db import SCHEMA_PATH

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / SETTINGS.db_path
BACKUP_DIR = BASE_DIR / "data" / "backups"


def reset_db_file(db_path: Path) -> Path | None:
    backup_path: Path | None = None
    if db_path.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = BACKUP_DIR / f"bot-reset-backup-{stamp}.db"
        shutil.copy2(db_path, backup_path)
        try:
            db_path.unlink()
        except PermissionError as exc:
            raise SystemExit(
                f"Database file is in use: {db_path}. Stop bot/web processes first, then run reset again."
            ) from exc

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        db.executescript(schema_sql)
        db.commit()
    finally:
        db.close()

    return backup_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backup current database and reset all tournament/platform data for a fresh start.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run reset immediately without interactive confirmation.",
    )
    args = parser.parse_args()

    if not args.yes:
        print("This will delete all current DB data and recreate a fresh database.")
        print(f"Database: {DB_PATH}")
        answer = input("Type RESET to continue: ").strip()
        if answer != "RESET":
            raise SystemExit("Reset cancelled.")

    backup_path = reset_db_file(DB_PATH)
    print("[OK] Database reset completed")
    if backup_path:
        print(f"Backup saved: {backup_path}")
    else:
        print("No existing database was found. Fresh database created.")


if __name__ == "__main__":
    main()
