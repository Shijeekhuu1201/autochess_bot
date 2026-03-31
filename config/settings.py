from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _to_int(name: str, default: int = 0) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    token: str
    prefix: str
    guild_id: int
    db_path: str
    timezone: str


SETTINGS = Settings(
    token=os.getenv("DISCORD_TOKEN", "").strip(),
    prefix=os.getenv("BOT_PREFIX", ".").strip() or ".",
    guild_id=_to_int("GUILD_ID", 0),
    db_path=os.getenv("DB_PATH", "data/bot.db").strip(),
    timezone=os.getenv("TIMEZONE", "Asia/Ulaanbaatar").strip(),
)