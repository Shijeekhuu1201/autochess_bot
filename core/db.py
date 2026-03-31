from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BASE_DIR / "sql" / "schema.sql"


async def get_db(db_path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    return conn


async def _ensure_column(
    db: aiosqlite.Connection,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    existing = {str(row["name"]) for row in rows}
    if column_name not in existing:
        await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


async def _run_lightweight_migrations(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            donor_name TEXT NOT NULL,
            donor_user_id INTEGER,
            amount INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await _ensure_column(
        db,
        "platform_donations",
        "donor_user_id",
        "donor_user_id INTEGER",
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS supporter_memberships (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            donor_tier TEXT,
            donor_expires_at TEXT,
            sponsor_tier TEXT,
            sponsor_expires_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_supporter_memberships_user
        ON supporter_memberships(user_id)
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS role_memberships (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            confirmed_role_expires_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS ranked_queues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL DEFAULT 0,
            queue_type TEXT NOT NULL,
            title TEXT NOT NULL,
            entry_fee INTEGER NOT NULL DEFAULT 0,
            max_players INTEGER NOT NULL DEFAULT 8,
            status TEXT NOT NULL DEFAULT 'open',
            winner_user_id INTEGER,
            winner_user_id_2 INTEGER,
            created_by INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            stopped_at TEXT,
            completed_at TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ranked_queues_guild_type_status
        ON ranked_queues(guild_id, queue_type, status, created_at)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ranked_queues_channel
        ON ranked_queues(channel_id, status, created_at)
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS ranked_queue_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
            confirmed_at TEXT,
            confirm_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (queue_id) REFERENCES ranked_queues(id) ON DELETE CASCADE
        )
        """
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ranked_queue_entries_unique_user
        ON ranked_queue_entries(queue_id, user_id)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ranked_queue_entries_status
        ON ranked_queue_entries(queue_id, status, joined_at)
        """
    )
    await _ensure_column(
        db,
        "player_profiles",
        "avatar_url",
        "avatar_url TEXT",
    )
    await _ensure_column(
        db,
        "player_profiles",
        "phone_number",
        "phone_number TEXT",
    )
    await _ensure_column(
        db,
        "player_profiles",
        "bank_account",
        "bank_account TEXT",
    )
    await _ensure_column(
        db,
        "tournaments",
        "game_key",
        "game_key TEXT NOT NULL DEFAULT 'autochess'",
    )
    await _ensure_column(
        db,
        "tournaments",
        "format_key",
        "format_key TEXT NOT NULL DEFAULT 'solo_32'",
    )
    await _ensure_column(
        db,
        "tournaments",
        "slug",
        "slug TEXT NOT NULL DEFAULT ''",
    )
    await _ensure_column(
        db,
        "tournaments",
        "season_name",
        "season_name TEXT NOT NULL DEFAULT 'Season 1'",
    )
    await _ensure_column(
        db,
        "tournaments",
        "register_channel_id",
        "register_channel_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "tournaments",
        "register_message_id",
        "register_message_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "tournaments",
        "waiting_channel_id",
        "waiting_channel_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "tournaments",
        "waiting_summary_message_id",
        "waiting_summary_message_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "tournaments",
        "confirmed_channel_id",
        "confirmed_channel_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "tournaments",
        "confirmed_summary_message_id",
        "confirmed_summary_message_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "tournament_entries",
        "review_message_id",
        "review_message_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "tournament_entries",
        "source",
        "source TEXT NOT NULL DEFAULT 'discord'",
    )
    await _ensure_column(
        db,
        "sponsors",
        "sponsor_kind",
        "sponsor_kind TEXT NOT NULL DEFAULT 'tournament'",
    )
    await _ensure_column(
        db,
        "sponsors",
        "sponsor_user_id",
        "sponsor_user_id INTEGER",
    )
    await _ensure_column(
        db,
        "sponsors",
        "created_by",
        "created_by INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "sponsors",
        "logo_url",
        "logo_url TEXT",
    )
    await _ensure_column(
        db,
        "sponsors",
        "website_url",
        "website_url TEXT",
    )
    await _ensure_column(
        db,
        "sponsors",
        "display_tier",
        "display_tier TEXT NOT NULL DEFAULT 'sponsor'",
    )
    await _ensure_column(
        db,
        "sponsors",
        "is_active",
        "is_active INTEGER NOT NULL DEFAULT 1",
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL DEFAULT 0,
            tournament_id INTEGER,
            announcement_type TEXT NOT NULL DEFAULT 'tournament',
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            badge TEXT NOT NULL DEFAULT 'Announcement',
            button_text TEXT NOT NULL DEFAULT '',
            button_url TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            target_channel TEXT NOT NULL DEFAULT 'announcements',
            status TEXT NOT NULL DEFAULT 'draft',
            repeat_hours INTEGER NOT NULL DEFAULT 0,
            publish_count INTEGER NOT NULL DEFAULT 0,
            max_publishes INTEGER NOT NULL DEFAULT 1,
            next_publish_at TEXT,
            end_at TEXT,
            published_message_id INTEGER NOT NULL DEFAULT 0,
            published_channel_id INTEGER NOT NULL DEFAULT 0,
            published_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE SET NULL
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS tournament_admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            tournament_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            requested_by INTEGER NOT NULL DEFAULT 0,
            error_text TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            processed_at TEXT,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        )
        """
    )
    await _ensure_column(
        db,
        "announcements",
        "guild_id",
        "guild_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "announcements",
        "announcement_type",
        "announcement_type TEXT NOT NULL DEFAULT 'tournament'",
    )
    await _ensure_column(
        db,
        "announcements",
        "badge",
        "badge TEXT NOT NULL DEFAULT 'Announcement'",
    )
    await _ensure_column(
        db,
        "announcements",
        "button_text",
        "button_text TEXT NOT NULL DEFAULT ''",
    )
    await _ensure_column(
        db,
        "announcements",
        "button_url",
        "button_url TEXT NOT NULL DEFAULT ''",
    )
    await _ensure_column(
        db,
        "announcements",
        "image_url",
        "image_url TEXT NOT NULL DEFAULT ''",
    )
    await _ensure_column(
        db,
        "announcements",
        "target_channel",
        "target_channel TEXT NOT NULL DEFAULT 'announcements'",
    )
    await _ensure_column(
        db,
        "announcements",
        "status",
        "status TEXT NOT NULL DEFAULT 'draft'",
    )
    await _ensure_column(
        db,
        "announcements",
        "repeat_hours",
        "repeat_hours INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "announcements",
        "publish_count",
        "publish_count INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "announcements",
        "max_publishes",
        "max_publishes INTEGER NOT NULL DEFAULT 1",
    )
    await _ensure_column(
        db,
        "announcements",
        "next_publish_at",
        "next_publish_at TEXT",
    )
    await _ensure_column(
        db,
        "announcements",
        "end_at",
        "end_at TEXT",
    )
    await _ensure_column(
        db,
        "announcements",
        "published_message_id",
        "published_message_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "announcements",
        "published_channel_id",
        "published_channel_id INTEGER NOT NULL DEFAULT 0",
    )
    await _ensure_column(
        db,
        "announcements",
        "published_at",
        "published_at TEXT",
    )
    await db.execute(
        """
        UPDATE tournaments
        SET game_key = 'autochess'
        WHERE COALESCE(game_key, '') = ''
        """
    )
    await db.execute(
        """
        UPDATE tournaments
        SET format_key = 'solo_32'
        WHERE COALESCE(format_key, '') = ''
        """
    )


async def init_db(db_path: str) -> None:
    os.makedirs(Path(db_path).parent, exist_ok=True)

    db = await get_db(db_path)
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        await db.executescript(schema_sql)
        await _run_lightweight_migrations(db)
        await db.commit()
    finally:
        await db.close()
