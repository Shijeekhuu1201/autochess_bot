from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import sys
import secrets
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_from_directory, session, url_for
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/eNdPM4R7")
DONATE_OWNER_NAME = os.getenv("DONATE_OWNER_NAME", "Шижээхүү")
DONATE_BANK_NAME = os.getenv("DONATE_BANK_NAME", "Хаанбанк")
DONATE_ACCOUNT_NO = os.getenv("DONATE_ACCOUNT_NO", "83000500 5700662225")
DONATE_NOTE = os.getenv(
    "DONATE_NOTE",
    "Платформ дэмжлэг: server hosting, maintenance, Discord bot болон live web хөгжүүлэлт.",
)
DONATE_QR_URL = os.getenv("DONATE_QR_URL", "")
DONATE_QR_FILE = os.getenv("DONATE_QR_FILE", "donate_qr.png")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
CONTACT_FACEBOOK_URL = os.getenv("CONTACT_FACEBOOK_URL", "").strip()
CONTACT_DISCORD_LABEL = os.getenv("CONTACT_DISCORD_LABEL", "Chess Of Mongolia Discord").strip()
CONTACT_SUPPORT_NOTE = os.getenv(
    "CONTACT_SUPPORT_NOTE",
    "Tournament support, check-in coordination, registration help болон platform асуудлуудтай холбоотойгоор moderator багтай доорх сувгаар холбогдоно уу.",
).strip()
CONTACT_MODERATOR_IDS = os.getenv("CONTACT_MODERATOR_IDS", "").strip()
CONTACT_MODERATOR_EMAILS = os.getenv("CONTACT_MODERATOR_EMAILS", "").strip()
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "").strip()
WEB_SESSION_SECRET = os.getenv("WEB_SESSION_SECRET", "").strip()
ADMIN_PANEL_KEY = os.getenv("ADMIN_PANEL_KEY", "").strip()
DISCORD_OAUTH_SCOPE = "identify"

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import SETTINGS
from core.db import init_db

DB_PATH = BASE_DIR / "data" / "bot.db"
ASSETS_DIR = BASE_DIR / "web" / "assets"
PLATFORM_DONATIONS_JSON = BASE_DIR / "data" / "platform_donations.json"

app = Flask(__name__)
app.secret_key = WEB_SESSION_SECRET or "change-this-web-session-secret"


@app.before_request
def capture_public_page_view():
    track_site_page_view()


def money(value: int | None) -> str:
    return f"{int(value or 0):,}₮"


def is_supported_announcement_url(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    return parsed.scheme in {"http", "https", "discord"} and bool(parsed.netloc or parsed.path)


def build_prize_display(tournament: dict[str, Any] | None, sponsor_total: int | None) -> dict[str, int]:
    base_pool = int((tournament or {}).get("prize_total") or 0)
    base_1 = int((tournament or {}).get("prize_1") or 0)
    base_2 = int((tournament or {}).get("prize_2") or 0)
    base_3 = int((tournament or {}).get("prize_3") or 0)
    sponsor_amount = int(sponsor_total or 0)
    current_total_pool = base_pool + sponsor_amount
    fee_base = round(base_pool * 0.10)
    fee_sponsor = round(sponsor_amount * 0.10)
    organizer_fee = fee_base + fee_sponsor
    base_final_pool = base_pool - fee_base
    sponsor_final_pool = sponsor_amount - fee_sponsor
    final_pool = base_final_pool + sponsor_final_pool
    base_total = max(base_1 + base_2 + base_3, 1)
    ratio_1 = base_1 / base_total
    ratio_2 = base_2 / base_total
    ratio_3 = base_3 / base_total
    prize_1_base = round(base_final_pool * ratio_1)
    prize_2_base = round(base_final_pool * ratio_2)
    prize_3_base = base_final_pool - prize_1_base - prize_2_base
    prize_1_sponsor = round(sponsor_final_pool * ratio_1)
    prize_2_sponsor = round(sponsor_final_pool * ratio_2)
    prize_3_sponsor = sponsor_final_pool - prize_1_sponsor - prize_2_sponsor
    prize_1_total = prize_1_base + prize_1_sponsor
    prize_2_total = prize_2_base + prize_2_sponsor
    prize_3_total = prize_3_base + prize_3_sponsor
    return {
        "base_pool": base_pool,
        "sponsor_total": sponsor_amount,
        "current_total_pool": current_total_pool,
        "fee_base": int(fee_base),
        "fee_sponsor": int(fee_sponsor),
        "organizer_fee": int(organizer_fee),
        "base_final_pool": int(base_final_pool),
        "sponsor_final_pool": int(sponsor_final_pool),
        "final_pool": int(final_pool),
        "prize_1_base": int(prize_1_base),
        "prize_2_base": int(prize_2_base),
        "prize_3_base": int(prize_3_base),
        "prize_1_sponsor": int(prize_1_sponsor),
        "prize_2_sponsor": int(prize_2_sponsor),
        "prize_3_sponsor": int(prize_3_sponsor),
        "prize_1_total": int(prize_1_total),
        "prize_2_total": int(prize_2_total),
        "prize_3_total": int(prize_3_total),
    }


def get_tournament_payout_map(tournament_id: int) -> dict[int, int]:
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        return {}
    sponsor_total = get_sponsor_total(tournament_id)
    prize_display = build_prize_display(tournament, sponsor_total)
    return {
        1: int(prize_display["prize_1_total"]),
        2: int(prize_display["prize_2_total"]),
        3: int(prize_display["prize_3_total"]),
    }


def apply_sponsor_bonus_to_final_standings(
    final_standings: list[dict[str, Any]], sponsor_total: int | None
) -> list[dict[str, Any]]:
    if not final_standings:
        return []
    first = next((int(item.get("prize_amount") or 0) for item in final_standings if int(item.get("final_position") or 0) == 1), 0)
    second = next((int(item.get("prize_amount") or 0) for item in final_standings if int(item.get("final_position") or 0) == 2), 0)
    third = next((int(item.get("prize_amount") or 0) for item in final_standings if int(item.get("final_position") or 0) == 3), 0)
    prize_display = build_prize_display(
        {"prize_1": first, "prize_2": second, "prize_3": third, "prize_total": first + second + third},
        sponsor_total,
    )
    payout_map = {
        1: int(prize_display["prize_1_total"]),
        2: int(prize_display["prize_2_total"]),
        3: int(prize_display["prize_3_total"]),
    }
    enriched: list[dict[str, Any]] = []
    for item in final_standings:
        row = dict(item)
        position = int(row.get("final_position") or 0)
        row["display_prize_amount"] = payout_map.get(position, int(row.get("prize_amount") or 0))
        enriched.append(row)
    return enriched


def support_badge_label(support: dict[str, Any] | None) -> str:
    if not support:
        return "Inactive"

    now = datetime.now()
    day_values: list[int] = []
    for key in ("donor_expires_at", "sponsor_expires_at"):
        raw = support.get(key)
        if not raw:
            continue
        try:
            expiry = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if expiry.tzinfo is not None:
            expiry = expiry.astimezone().replace(tzinfo=None)
        day_values.append(max((expiry.date() - now.date()).days, 0))

    if not day_values:
        return "Inactive"
    days_left = max(day_values)
    if days_left <= 3:
        return "Expires Soon"
    return f"{days_left} Days Left"


LEGACY_SCHEDULE_VALUES = {
    "Бямба гараг бүр 13:00",
    "Бямба гараг бүр 12:30",
    "Saturday 13:00",
    "Saturday 12:30",
}


def schedule_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in LEGACY_SCHEDULE_VALUES:
        return "-"
    return text


def parse_contact_moderator_ids(raw_value: str) -> list[int]:
    values: list[int] = []
    for chunk in re.split(r"[\s,;]+", str(raw_value or "").strip()):
        if not chunk:
            continue
        try:
            values.append(int(chunk))
        except ValueError:
            continue
    return values


def parse_contact_moderator_emails(raw_value: str) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for chunk in re.split(r"[;\n]+", str(raw_value or "").strip()):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        user_id_raw, email_raw = item.split("=", 1)
        user_id_raw = user_id_raw.strip()
        email = email_raw.strip()
        if not user_id_raw or not email:
            continue
        try:
            mapping[int(user_id_raw)] = email
        except ValueError:
            continue
    return mapping


def registration_status_label(value: Any) -> str:
    status = str(value or "").strip().lower()
    mapping = {
        "registered": "Хүсэлт илгээгдсэн",
        "confirmed": "Баталгаажсан",
        "waitlist": "Хүлээлгийн жагсаалт",
        "rejected": "Татгалзсан",
        "replacement_in": "Орлуулж орсон",
    }
    return mapping.get(status, status.upper() if status else "-")


def payment_status_label(value: Any) -> str:
    status = str(value or "").strip().lower()
    mapping = {
        "unpaid": "Төлөөгүй",
        "confirmed": "Баталгаажсан",
        "rejected": "Татгалзсан",
    }
    return mapping.get(status, status.title() if status else "-")


def registration_source_label(value: Any) -> str:
    source = str(value or "").strip().lower()
    mapping = {
        "web": "Вэб хүсэлт",
        "discord": "Discord",
    }
    return mapping.get(source, source or "-")


def tournament_status_label(value: Any) -> str:
    status = str(value or "").strip().lower()
    mapping = {
        "draft": "Ноорог",
        "registration_open": "Бүртгэл нээлттэй",
        "registration_locked": "Бүртгэл хаагдсан",
        "zones_created": "Zone үүссэн",
        "zones_running": "Zone явагдаж байна",
        "semis_created": "Semi үүссэн",
        "semis_running": "Semi явагдаж байна",
        "final_created": "Final үүссэн",
        "final_running": "Final явагдаж байна",
        "completed": "Дууссан",
        "cancelled": "Цуцлагдсан",
    }
    return mapping.get(status, status or "-")


DONOR_ROLE_NAMES = ["💎 Donator", "👑 Elite Donator", "🌟 Legend Donator"]
SPONSOR_ROLE_NAMES = ["💎 Sponsor", "👑 Elite Sponsor", "🌟 Legend Sponsor"]


def support_role_chain(role_name: str | None, all_role_names: list[str]) -> list[str]:
    if not role_name or role_name not in all_role_names:
        return []
    index = all_role_names.index(role_name)
    return all_role_names[: index + 1]


GLOBAL_RULES_TEXT = """Weekly Auto Chess Cup - Журам

1. Оролцоо
Зөвхөн баталгаажсан 32 тоглогч оролцоно.

2. Формат
Zone 4x8 → Semi Final 2x8 → Grand Final 1x8
Бүх шат BO2 форматаар явагдана.

3. Шалгаралт
Zone-оос Top 16, Semi Final-аас Top 8 шалгарна.

4. Онооны систем
Stage бүр 2 game-ийн нийлбэр оноогоор шийдэгдэнэ.
Final standings нь Grand Final-ийн нийт оноогоор гарна.

5. Lobby журам
Тоглогчид зөв lobby-д, цагтаа орсон байх ёстой.
Host болон password-г admin зарлана.

6. Replacement
Tournament эхэлсний дараа тоглох боломжгүй болсон тохиолдолд
admin replace хийх эрхтэй.

7. Fair Play
Cheat, collusion, account sharing, bug abuse болон
спортын бус үйлдэл хатуу хориотой.

8. Техникийн нөхцөл
Disconnect, remake, тусгай нөхцөлийг зөвхөн admin шийднэ.

9. Шагналын сан
Tournament sponsor нь prize pool дээр нэмэгдэнэ.
Platform donation нь tournament prize-д нөлөөлөхгүй.

10. Эцсийн шийдвэр
Admin шийдвэр final байна.
"""

BASE_CSS = """
:root{
  --bg:#050c18;
  --bg2:#0a1430;
  --bg3:#0e1b3f;
  --card:#101b36;
  --card2:#0b152c;
  --line:#223a67;
  --line-2:#365d9d;
  --text:#eef4ff;
  --muted:#97abd5;
  --blue:#66a7ff;
  --cyan:#67e6ff;
  --purple:#9c7bff;
  --gold:#ffcc66;
  --silver:#d8e3f0;
  --bronze:#ffab73;
  --green:#7ee787;
  --red:#ff7b72;
  --orange:#ff9b52;
  --shadow:0 24px 70px rgba(0,0,0,.34);
  --radius:28px;
}

*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0;
  font-family:Segoe UI, Inter, Arial, sans-serif;
  color:var(--text);
  background:
    radial-gradient(circle at top left, rgba(102,167,255,.18), transparent 22%),
    radial-gradient(circle at top right, rgba(156,123,255,.14), transparent 26%),
    radial-gradient(circle at bottom center, rgba(103,230,255,.08), transparent 30%),
    linear-gradient(180deg, #040914, #09132a 48%, #050c18 100%);
}

.wrap{
  max-width:1420px;
  margin:0 auto;
  padding:24px;
}

.hero{
  position:relative;
  overflow:hidden;
  border:1px solid var(--line);
  border-radius:34px;
  padding:34px;
  background:
    linear-gradient(135deg, rgba(14,28,60,.98), rgba(8,16,34,.98)),
    linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,.01));
  box-shadow:var(--shadow);
  margin-bottom:22px;
  isolation:isolate;
}
.hero::before{
  content:"";
  position:absolute;
  width:360px;
  height:360px;
  top:-150px;
  right:-120px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(156,123,255,.18), transparent 70%);
  pointer-events:none;
  z-index:-1;
}
.hero::after{
  content:"";
  position:absolute;
  width:320px;
  height:320px;
  left:-140px;
  bottom:-150px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(102,167,255,.20), transparent 70%);
  pointer-events:none;
  z-index:-1;
}

.hero-grid{
  display:grid;
  grid-template-columns:minmax(0,1.45fr) minmax(320px,.82fr);
  gap:20px;
  align-items:stretch;
}

.eyebrow{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:8px 12px;
  border-radius:999px;
  border:1px solid rgba(255,204,102,.24);
  background:rgba(255,204,102,.10);
  color:#ffd98a;
  font-size:12px;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.7px;
  margin-bottom:14px;
}

.hero h1{
  margin:0 0 10px;
  font-size:46px;
  line-height:1.03;
  letter-spacing:-1px;
  max-width:920px;
  text-shadow:0 0 20px rgba(255,255,255,.03);
}

.subtitle{
  color:var(--muted);
  line-height:1.68;
  font-size:16px;
  max-width:940px;
}

.status-row{
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  margin:20px 0 18px;
}

.hero .status-row .badge{
  padding:9px 14px;
  border-radius:999px;
  font-weight:900;
  letter-spacing:.25px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.04);
}

.hero .status-row .badge.gold{
  background:linear-gradient(180deg, rgba(255,204,102,.16), rgba(255,204,102,.08));
}

.hero .status-row .badge.green{
  background:linear-gradient(180deg, rgba(126,231,135,.16), rgba(54,133,87,.18));
}

.hero .status-row .badge.purple{
  background:linear-gradient(180deg, rgba(156,123,255,.14), rgba(93,73,174,.16));
}

.hero .status-row .badge.blue,
.hero .status-row .badge.cyan{
  background:linear-gradient(180deg, rgba(102,167,255,.14), rgba(58,106,182,.16));
}

.spotlight-marquee{
  position:relative;
  overflow:hidden;
  border:1px solid rgba(255,255,255,.08);
  border-radius:20px;
  background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02));
  padding:12px 0;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
}

.spotlight-marquee::before,
.spotlight-marquee::after{
  content:"";
  position:absolute;
  top:0;
  bottom:0;
  width:56px;
  z-index:2;
  pointer-events:none;
}

.spotlight-marquee::before{
  left:0;
  background:linear-gradient(90deg, rgba(11,20,45,.96), rgba(11,20,45,0));
}

.spotlight-marquee::after{
  right:0;
  background:linear-gradient(270deg, rgba(11,20,45,.96), rgba(11,20,45,0));
}

.spotlight-track{
  display:flex;
  align-items:center;
  gap:12px;
  width:max-content;
  padding-left:12px;
  animation:spotlight-scroll 34s linear infinite;
}

.spotlight-marquee:hover .spotlight-track{
  animation-play-state:paused;
}

.spotlight-pill{
  display:inline-flex;
  align-items:center;
  gap:10px;
  padding:10px 14px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.05);
  white-space:nowrap;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
}

.spotlight-pill.gold{
  border-color:rgba(255,204,102,.22);
  background:rgba(255,204,102,.10);
}

.spotlight-pill.green{
  border-color:rgba(126,231,135,.18);
  background:rgba(126,231,135,.10);
}

.spotlight-pill.blue{
  border-color:rgba(102,167,255,.18);
  background:rgba(102,167,255,.10);
}

.spotlight-kicker{
  color:var(--muted);
  font-size:11px;
  font-weight:800;
  letter-spacing:.6px;
  text-transform:uppercase;
}

.spotlight-value{
  color:var(--text);
  font-size:13px;
  font-weight:800;
}

@keyframes spotlight-scroll{
  0%{transform:translateX(0)}
  100%{transform:translateX(-50%)}
}

.badge{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:8px 12px;
  border-radius:999px;
  border:1px solid var(--line);
  background:rgba(255,255,255,.04);
  color:var(--muted);
  font-size:12px;
  font-weight:700;
  letter-spacing:.3px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
}
.badge.gold{background:rgba(255,204,102,.14);border-color:rgba(255,204,102,.22);color:#ffdb92}
.badge.green{background:rgba(126,231,135,.14);border-color:rgba(126,231,135,.20);color:#a2efaa}
.badge.purple{background:rgba(156,123,255,.14);border-color:rgba(156,123,255,.20);color:#d1c1ff}
.badge.blue{background:rgba(102,167,255,.14);border-color:rgba(102,167,255,.20);color:#bfd9ff}
.badge.cyan{background:rgba(103,230,255,.14);border-color:rgba(103,230,255,.20);color:#b9f6ff}
.badge.orange{background:rgba(255,155,82,.14);border-color:rgba(255,155,82,.20);color:#ffbe96}

.hero-stats{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:14px;
  margin-top:8px;
}

.identity-row{
  display:flex;
  align-items:center;
  gap:22px;
  flex-wrap:wrap;
  margin-top:22px;
  margin-bottom:18px;
}

.avatar-shell{
  position:relative;
  width:124px;
  height:124px;
  border-radius:32px;
  padding:3px;
  background:linear-gradient(135deg, rgba(255,204,102,.55), rgba(102,167,255,.28), rgba(156,123,255,.34));
  box-shadow:0 16px 50px rgba(0,0,0,.28);
}

.avatar-shell::after{
  content:"";
  position:absolute;
  inset:-10px;
  border-radius:38px;
  background:radial-gradient(circle, rgba(255,204,102,.16), transparent 64%);
  z-index:-1;
}

.avatar-img{
  width:100%;
  height:100%;
  border-radius:29px;
  object-fit:cover;
  display:block;
  border:1px solid rgba(255,255,255,.06);
  background:linear-gradient(180deg, rgba(27,42,82,.92), rgba(13,20,39,.98));
}

.avatar-fallback{
  width:100%;
  height:100%;
  border-radius:29px;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size:40px;
  font-weight:900;
  color:#ffd98a;
  border:1px solid rgba(255,255,255,.06);
  background:linear-gradient(180deg, rgba(27,42,82,.92), rgba(13,20,39,.98));
}

.status-dot{
  position:absolute;
  right:10px;
  bottom:10px;
  width:18px;
  height:18px;
  border-radius:50%;
  background:var(--green);
  border:3px solid rgba(8,16,34,.96);
  box-shadow:0 0 0 3px rgba(126,231,135,.18);
}

.identity-copy{
  min-width:0;
  flex:1 1 320px;
}

.badge-row{
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  margin-top:16px;
}

.rank-chip{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:9px 14px;
  border-radius:999px;
  border:1px solid var(--line);
  background:rgba(255,255,255,.04);
  color:var(--text);
  font-size:13px;
  font-weight:700;
}

.rank-chip.legend{background:rgba(255,204,102,.14);border-color:rgba(255,204,102,.24);color:#ffd98a}
.rank-chip.elite{background:rgba(156,123,255,.14);border-color:rgba(156,123,255,.22);color:#d9cbff}
.rank-chip.support{background:rgba(103,230,255,.12);border-color:rgba(103,230,255,.20);color:#baf7ff}
.rank-chip.default{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.09);color:#d7e5ff}

.participant-main{
  display:flex;
  align-items:center;
  gap:12px;
  min-width:0;
}

.participant-copy{
  min-width:0;
  flex:1 1 auto;
}

.participant-name{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
  font-weight:800;
}

.participant-sub{
  display:flex;
  flex-wrap:wrap;
  gap:6px;
  margin-top:8px;
}

.mini-avatar{
  width:42px;
  height:42px;
  border-radius:14px;
  overflow:hidden;
  flex:0 0 auto;
  border:1px solid rgba(255,255,255,.10);
  background:linear-gradient(180deg, rgba(27,42,82,.92), rgba(13,20,39,.98));
  box-shadow:0 8px 18px rgba(0,0,0,.22);
}

.mini-avatar img{
  width:100%;
  height:100%;
  object-fit:cover;
  display:block;
}

.mini-avatar-fallback{
  width:100%;
  height:100%;
  display:flex;
  align-items:center;
  justify-content:center;
  color:#ffd98a;
  font-weight:900;
  font-size:16px;
}

.micro-chip{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:5px 10px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.04);
  color:var(--text);
  font-size:11px;
  font-weight:700;
  white-space:nowrap;
}

.micro-chip.legend{background:rgba(255,204,102,.14);border-color:rgba(255,204,102,.24);color:#ffd98a}
.micro-chip.elite{background:rgba(156,123,255,.14);border-color:rgba(156,123,255,.22);color:#d9cbff}
.micro-chip.support{background:rgba(103,230,255,.12);border-color:rgba(103,230,255,.20);color:#baf7ff}
.micro-chip.runner{background:rgba(216,227,240,.14);border-color:rgba(216,227,240,.24);color:#edf5ff}
.micro-chip.bronze{background:rgba(255,171,115,.14);border-color:rgba(255,171,115,.24);color:#ffd4bd}

.mini{
  min-height:98px;
  border:1px solid var(--line);
  border-radius:22px;
  padding:16px 18px;
  background:
    linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.03));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
  position:relative;
  overflow:hidden;
}
.mini::after{
  content:"";
  position:absolute;
  inset:auto -20px -32px auto;
  width:96px;
  height:96px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(102,167,255,.12), transparent 72%);
  pointer-events:none;
}
.mini .label{
  color:var(--muted);
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:.6px;
  margin-bottom:8px;
}
.mini .value{
  font-size:28px;
  font-weight:800;
  line-height:1.15;
}

.champion-card{
  position:relative;
  border:1px solid rgba(255,204,102,.20);
  border-radius:28px;
  padding:22px;
  background:
    linear-gradient(135deg, rgba(255,204,102,.09), rgba(102,167,255,.05) 38%, transparent 64%),
    radial-gradient(circle at top center, rgba(255,204,102,.10), transparent 58%),
    linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.03));
  box-shadow:var(--shadow);
  min-height:100%;
  overflow:hidden;
}
.champion-card::after{
  content:"";
  position:absolute;
  inset:auto -60px -70px auto;
  width:180px;
  height:180px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(255,204,102,.12), transparent 70%);
  pointer-events:none;
}

.champion-card::before{
  content:"";
  position:absolute;
  inset:0;
  background:
    linear-gradient(115deg, rgba(255,255,255,.06), transparent 24%),
    repeating-linear-gradient(135deg, transparent 0 14px, rgba(255,255,255,.025) 14px 15px);
  opacity:.55;
  pointer-events:none;
}
.champion-label{
  display:inline-flex;
  padding:7px 12px;
  border-radius:999px;
  background:rgba(255,204,102,.16);
  color:#ffdb92;
  font-size:12px;
  font-weight:800;
  letter-spacing:.6px;
  text-transform:uppercase;
  margin-bottom:14px;
  border:1px solid rgba(255,204,102,.22);
}
.champion-name{
  font-size:32px;
  font-weight:900;
  line-height:1.08;
  margin-bottom:8px;
}
.champion-meta{
  color:var(--muted);
  font-size:15px;
  margin-bottom:16px;
}
.champion-prize{
  font-size:30px;
  font-weight:900;
  color:var(--gold);
  margin-bottom:10px;
  white-space:nowrap;
  text-shadow:0 0 24px rgba(255,204,102,.18);
}
.champion-sub{
  color:var(--muted);
  line-height:1.58;
}

.featured-slider{
  position:relative;
  min-height:100%;
}
.featured-slider::after{
  content:"";
  position:absolute;
  top:-30px;
  right:-30px;
  width:180px;
  height:180px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(255,204,102,.16), transparent 70%);
  filter:blur(2px);
  pointer-events:none;
}
.featured-slider::before{
  content:"";
  position:absolute;
  left:-20%;
  top:18%;
  width:180%;
  height:1px;
  background:linear-gradient(90deg, transparent, rgba(255,255,255,.12), transparent);
  opacity:.55;
  transform:rotate(-10deg);
  pointer-events:none;
}
.featured-slide{
  display:none;
}
.featured-slide.active{
  display:block;
  animation:featured-fade .32s ease;
}
.featured-slide-head{
  display:flex;
  justify-content:space-between;
  gap:10px;
  align-items:flex-start;
  flex-wrap:wrap;
}
.featured-slide-meta{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin-bottom:14px;
}
.featured-slide-chip{
  display:inline-flex;
  align-items:center;
  padding:6px 10px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.05);
  color:#edf4ff;
  font-size:12px;
  font-weight:800;
  backdrop-filter:blur(10px);
}
.featured-slide-stats{
  display:grid;
  grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:10px;
  margin-top:16px;
}
.featured-slide-stat{
  padding:12px 14px;
  border-radius:16px;
  border:1px solid rgba(255,255,255,.08);
  background:linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.025));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.025);
}
.featured-slide-stat .label{
  color:#a7b7da;
  font-size:11px;
  font-weight:800;
  letter-spacing:.5px;
  text-transform:uppercase;
  margin-bottom:6px;
}
.featured-slide-stat .value{
  color:var(--text);
  font-size:18px;
  font-weight:900;
  line-height:1.1;
}
.featured-slider-controls{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-top:18px;
  flex-wrap:wrap;
}
.featured-slider-dots{
  display:flex;
  align-items:center;
  gap:8px;
}
.featured-slider-left{
  display:flex;
  align-items:center;
  gap:10px;
  flex-wrap:wrap;
}
.featured-slider-dot{
  width:10px;
  height:10px;
  border-radius:50%;
  border:0;
  padding:0;
  background:rgba(255,255,255,.18);
  cursor:pointer;
  transition:transform .16s ease, background .16s ease;
}

.featured-slider-dot:hover{
  transform:scale(1.12);
}
.featured-slider-dot.active{
  background:#ffd98a;
  box-shadow:0 0 0 4px rgba(255,217,138,.12);
}
.featured-progress{
  position:relative;
  margin-top:14px;
  height:4px;
  border-radius:999px;
  overflow:hidden;
  background:rgba(255,255,255,.08);
}
.featured-progress-bar{
  width:0;
  height:100%;
  border-radius:999px;
  background:linear-gradient(90deg, rgba(255,204,102,.92), rgba(102,167,255,.85));
  box-shadow:0 0 18px rgba(255,204,102,.18);
}
.featured-progress-bar.animating{
  animation:featured-progress-run 4.2s linear forwards;
}
@keyframes featured-progress-run{
  from{width:0}
  to{width:100%}
}

.featured-arrow{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  width:34px;
  height:34px;
  border-radius:50%;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.05);
  color:#edf4ff;
  cursor:pointer;
  transition:transform .16s ease, border-color .16s ease, background .16s ease;
}

.featured-arrow:hover{
  transform:translateY(-1px);
  border-color:rgba(255,204,102,.24);
  background:rgba(255,204,102,.08);
}

.featured-slide-cta{
  margin-top:16px;
  display:flex;
  justify-content:flex-start;
}
@keyframes featured-fade{
  from{opacity:.45;transform:translateY(4px)}
  to{opacity:1;transform:translateY(0)}
}

.nav{
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  margin:18px 0 24px;
  position:sticky;
  top:0;
  z-index:4;
  padding:10px 0;
  backdrop-filter:blur(10px);
}
.nav a{
  text-decoration:none;
  color:var(--text);
  padding:11px 14px;
  border-radius:14px;
  border:1px solid var(--line);
  background:rgba(255,255,255,.05);
  font-size:14px;
  transition:.18s ease;
}
.nav a:hover{
  transform:translateY(-1px);
  border-color:#4f7fd3;
  background:rgba(255,255,255,.08);
  box-shadow:0 0 0 1px rgba(79,127,211,.15) inset;
}

.grid{
  display:grid;
  grid-template-columns:repeat(12,minmax(0,1fr));
  gap:18px;
}

.card{
  grid-column:span 12;
  border:1px solid var(--line);
  border-radius:28px;
  padding:22px;
  background:
    linear-gradient(180deg, rgba(16,27,54,.98), rgba(9,18,35,.98));
  box-shadow:var(--shadow);
  position:relative;
  overflow:hidden;
}
.card::before{
  content:"";
  position:absolute;
  inset:0;
  pointer-events:none;
  background:linear-gradient(180deg, rgba(255,255,255,.02), transparent 25%);
}

.cols-8{grid-column:span 8}
.cols-6{grid-column:span 6}
.cols-4{grid-column:span 4}

.section-head{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:14px;
  flex-wrap:wrap;
  margin-bottom:16px;
  position:relative;
  z-index:1;
}

h2{
  margin:0;
  font-size:26px;
  letter-spacing:.2px;
}
h3{
  margin:0;
  font-size:18px;
}

.muted{color:var(--muted)}
.list{
  display:grid;
  gap:10px;
  position:relative;
  z-index:1;
}
.row{
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:12px;
  padding:13px 15px;
  border:1px solid var(--line);
  border-radius:18px;
  background:rgba(255,255,255,.03);
  transition:.16s ease;
}
.row:hover{
  border-color:#375894;
  background:rgba(255,255,255,.045);
  transform:translateY(-1px);
}
.row.support-donor{
  border-color:rgba(103,230,255,.22);
  background:linear-gradient(90deg, rgba(103,230,255,.10), rgba(255,255,255,.03));
}
.row.support-sponsor{
  border-color:rgba(156,123,255,.24);
  background:linear-gradient(90deg, rgba(156,123,255,.12), rgba(255,255,255,.03));
}
.row.support-expiry{
  border-color:rgba(255,204,102,.18);
  background:linear-gradient(90deg, rgba(255,204,102,.08), rgba(255,255,255,.03));
}
.row.support-donor > div:last-child{
  color:#baf7ff;
  font-weight:700;
}
.row.support-sponsor > div:last-child{
  color:#dccbff;
  font-weight:700;
}
.row.support-expiry > div:last-child{
  color:#ffd98a;
  font-weight:700;
}
.row strong{font-size:16px}
.final-standings-wall{
  display:grid;
  gap:14px;
  position:relative;
  z-index:1;
}
.final-standing-card{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:16px;
  padding:16px 18px;
  border-radius:22px;
  border:1px solid var(--line);
  background:
    linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.03));
  transition:.18s ease;
}
.final-standing-card:hover{
  transform:translateY(-2px);
  border-color:#4d74bc;
  background:
    linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.04));
}
.final-standing-card.podium-1{
  border-color:rgba(255,204,102,.34);
  box-shadow:0 0 0 1px rgba(255,204,102,.08) inset, 0 18px 38px rgba(255,204,102,.08);
}
.final-standing-card.podium-2{
  border-color:rgba(216,227,240,.28);
  box-shadow:0 0 0 1px rgba(216,227,240,.07) inset;
}
.final-standing-card.podium-3{
  border-color:rgba(255,171,115,.28);
  box-shadow:0 0 0 1px rgba(255,171,115,.07) inset;
}
.final-standing-left{
  display:flex;
  align-items:flex-start;
  gap:14px;
  min-width:0;
  flex:1 1 auto;
}
.final-standing-avatar{
  width:52px;
  height:52px;
  border-radius:16px;
  overflow:hidden;
  flex:0 0 auto;
  border:1px solid rgba(255,255,255,.10);
  background:linear-gradient(180deg, rgba(27,42,82,.92), rgba(13,20,39,.98));
  box-shadow:0 10px 24px rgba(0,0,0,.22);
}
.final-standing-avatar img{
  width:100%;
  height:100%;
  object-fit:cover;
  display:block;
}
.final-standing-rank{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
  margin-bottom:6px;
}
.final-standing-medal{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  width:34px;
  height:34px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.05);
  font-size:18px;
}
.final-standing-name{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
  font-size:16px;
  font-weight:900;
  margin-bottom:0;
}
.final-standing-sub{
  display:flex;
  flex-wrap:wrap;
  gap:6px;
  margin-top:8px;
}
.final-standing-right{
  display:grid;
  gap:8px;
  justify-items:end;
  text-align:right;
  flex:0 0 auto;
}
.final-standing-prize{
  color:var(--gold);
  font-size:20px;
  font-weight:900;
  line-height:1;
}
.final-standing-points{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:7px 12px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.05);
  color:#dfe9ff;
  font-size:11px;
  font-weight:800;
  letter-spacing:.2px;
}
.final-standing-copy{
  min-width:0;
  flex:1 1 auto;
}

.stage-grid{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:14px;
}
.bracket-visual{
  position:relative;
  display:grid;
  grid-template-columns:1.1fr .12fr .9fr 1fr .9fr .12fr 1.1fr;
  gap:12px;
  align-items:center;
  border:1px solid rgba(255,204,102,.18);
  border-radius:28px;
  padding:24px;
  margin-bottom:18px;
  background:
    radial-gradient(circle at top center, rgba(255,204,102,.08), transparent 32%),
    linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02));
  overflow:hidden;
}
.bracket-visual::before,
.bracket-visual::after{
  content:"";
  position:absolute;
  width:220px;
  height:220px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(255,204,102,.10), transparent 68%);
  pointer-events:none;
}
.bracket-visual::before{left:-120px;top:-120px}
.bracket-visual::after{right:-120px;bottom:-120px}
.bracket-column{
  display:flex;
  flex-direction:column;
  gap:16px;
  position:relative;
  z-index:1;
}
.bracket-column.center{
  align-items:center;
  justify-content:center;
}
.bracket-connector{
  display:flex;
  align-items:center;
  justify-content:center;
  color:#ffdb92;
  opacity:.76;
  font-size:28px;
  font-weight:900;
}
.bracket-node{
  border:1px solid rgba(255,255,255,.10);
  border-radius:18px;
  padding:14px 16px;
  min-height:82px;
  background:linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.03));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
}
.bracket-node.compact{min-height:76px}
.bracket-node.final{
  border-color:rgba(255,204,102,.30);
  background:
    radial-gradient(circle at top center, rgba(255,204,102,.12), transparent 58%),
    linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.03));
  min-height:150px;
}
.bracket-node.empty{
  border-style:dashed;
  opacity:.72;
}
.bracket-node-label{
  color:#ffd98a;
  font-size:11px;
  font-weight:800;
  letter-spacing:.8px;
  text-transform:uppercase;
  margin-bottom:8px;
}
.bracket-node-title{
  font-size:18px;
  font-weight:900;
  line-height:1.15;
  margin-bottom:6px;
}
.bracket-node-meta{
  color:var(--muted);
  font-size:13px;
  line-height:1.5;
}
.bracket-player-list{
  display:grid;
  gap:6px;
  margin-top:10px;
}
.bracket-player{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:8px;
  padding:7px 9px;
  border-radius:12px;
  background:rgba(255,255,255,.04);
  border:1px solid rgba(255,255,255,.06);
  font-size:12px;
}
.bracket-player a{
  color:var(--text);
  text-decoration:none;
  font-weight:700;
}
.bracket-player a:hover{
  text-decoration:underline;
}
.bracket-player-rank{
  color:#ffd98a;
  font-weight:800;
  white-space:nowrap;
}
.bracket-player-points{
  color:var(--muted);
  font-weight:700;
  white-space:nowrap;
  font-size:11px;
}
.bracket-empty-list{
  display:grid;
  gap:6px;
  margin-top:10px;
}
.bracket-empty-slot{
  height:28px;
  border-radius:10px;
  border:1px dashed rgba(255,255,255,.10);
  background:rgba(255,255,255,.03);
}
.bracket-crown{
  font-size:42px;
  line-height:1;
  margin-bottom:10px;
  filter:drop-shadow(0 8px 18px rgba(255,204,102,.18));
}
.stage-box{
  border:1px solid var(--line);
  border-radius:22px;
  padding:16px;
  background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02));
}
.stage-title-row{
  display:flex;
  justify-content:space-between;
  gap:12px;
  align-items:center;
  margin-bottom:10px;
}
.stage-meta{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin-bottom:12px;
}

.progress{
  display:grid;
  gap:10px;
  position:relative;
  z-index:1;
}
.progress-step{
  display:flex;
  gap:12px;
  align-items:flex-start;
  padding:12px 14px;
  border:1px solid var(--line);
  border-radius:18px;
  background:rgba(255,255,255,.03);
}
.dot{
  width:14px;
  height:14px;
  border-radius:50%;
  margin-top:3px;
  flex:0 0 14px;
  background:#475e8e;
  box-shadow:0 0 0 4px rgba(71,94,142,.12);
}
.dot.done{background:var(--green);box-shadow:0 0 0 4px rgba(126,231,135,.12)}
.dot.live{background:var(--gold);box-shadow:0 0 0 4px rgba(255,204,102,.12)}

.prize-grid{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:12px;
  position:relative;
  z-index:1;
}
.prize-item{
  min-width:0;
  min-height:110px;
  border:1px solid var(--line);
  border-radius:22px;
  padding:13px 14px;
  background:
    linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.03));
  display:flex;
  flex-direction:column;
  justify-content:space-between;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
}
.prize-item .label{
  color:var(--muted);
  font-size:11px;
  text-transform:uppercase;
  letter-spacing:.6px;
  line-height:1.35;
  margin-bottom:8px;
}
.prize-item .amount{
  font-size:clamp(15px, 1vw, 22px);
  font-weight:500;
  line-height:1.05;
  white-space:nowrap;
  letter-spacing:-.35px;
}

.prize-item .hint{
  margin-top:8px;
  color:var(--muted);
  font-size:11px;
  line-height:1.35;
}

.prize-item .hint.gold{
  color:#ffd98a;
  font-weight:800;
}

.prize-summary{
  margin-top:16px;
  display:grid;
  gap:10px;
  position:relative;
  z-index:1;
}

.prize-note{
  margin-top:12px;
  color:var(--muted);
  line-height:1.6;
  font-size:14px;
  position:relative;
  z-index:1;
}

.rules-box{
  white-space:pre-line;
  line-height:1.74;
  color:#eef4ff;
  border:1px solid var(--line);
  border-radius:20px;
  padding:18px;
  background:rgba(255,255,255,.03);
  min-height:220px;
  position:relative;
  z-index:1;
}

.podium{
  display:grid;
  grid-template-columns:minmax(280px, 1.15fr) minmax(0, .85fr);
  gap:16px;
  margin-bottom:16px;
  position:relative;
  z-index:1;
}
.podium-hero{
  border:1px solid rgba(255,204,102,.24);
  border-radius:28px;
  padding:24px;
  background:
    radial-gradient(circle at top right, rgba(255,204,102,.14), transparent 38%),
    linear-gradient(180deg, rgba(255,204,102,.08), rgba(255,255,255,.03));
  box-shadow:0 0 0 1px rgba(255,204,102,.07) inset, 0 18px 38px rgba(255,204,102,.08);
  display:grid;
  gap:16px;
}
.podium-kicker{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:8px 12px;
  border-radius:999px;
  border:1px solid rgba(255,204,102,.22);
  background:rgba(255,204,102,.08);
  color:var(--gold);
  font-size:12px;
  font-weight:800;
  letter-spacing:.08em;
  text-transform:uppercase;
}
.podium-hero-main{
  display:grid;
  gap:10px;
}
.podium-avatar{
  width:82px;
  height:82px;
  border-radius:24px;
  overflow:hidden;
  border:1px solid rgba(255,255,255,.10);
  background:linear-gradient(180deg, rgba(27,42,82,.92), rgba(13,20,39,.98));
  box-shadow:0 10px 24px rgba(0,0,0,.18);
}
.podium-avatar img{
  width:100%;
  height:100%;
  object-fit:cover;
  display:block;
}
.podium-avatar-fallback{
  width:100%;
  height:100%;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size:28px;
  font-weight:900;
  color:#eef4ff;
}
.podium-medal{
  font-size:38px;
  line-height:1;
}
.podium-name{
  font-size:34px;
  line-height:1.04;
  font-weight:900;
}
.podium-subtitle{
  color:var(--muted);
  font-size:15px;
}
.podium-money{
  color:var(--gold);
  font-weight:900;
  font-size:34px;
  white-space:nowrap;
  line-height:1;
}
.podium-meta{
  display:grid;
  grid-template-columns:repeat(3, minmax(0,1fr));
  gap:10px;
}
.podium-meta-item{
  border:1px solid rgba(255,255,255,.08);
  border-radius:18px;
  padding:12px 14px;
  background:rgba(255,255,255,.03);
}
.podium-meta-label{
  color:var(--muted);
  font-size:11px;
  letter-spacing:.08em;
  text-transform:uppercase;
  margin-bottom:4px;
}
.podium-meta-value{
  font-size:22px;
  font-weight:900;
}
.podium-side{
  display:grid;
  gap:12px;
}
.podium-card{
  border:1px solid var(--line);
  border-radius:24px;
  padding:20px;
  background:
    radial-gradient(circle at top right, rgba(255,255,255,.08), transparent 42%),
    linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.025));
  display:grid;
  gap:12px;
  position:relative;
  overflow:hidden;
}
.podium-card.silver{
  border-color:rgba(216,227,240,.20);
  box-shadow:0 0 0 1px rgba(216,227,240,.05) inset, 0 14px 28px rgba(216,227,240,.06);
}
.podium-card.bronze{
  border-color:rgba(255,171,115,.18);
  box-shadow:0 0 0 1px rgba(255,171,115,.05) inset, 0 14px 28px rgba(255,171,115,.06);
}
.podium-card::after{
  content:"";
  position:absolute;
  inset:auto -40px -50px auto;
  width:130px;
  height:130px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(255,255,255,.08), transparent 68%);
  pointer-events:none;
}
.podium-card-top{
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:10px;
}
.podium-card-header{
  display:flex;
  align-items:center;
  gap:12px;
  min-width:0;
}
.podium-card-avatar{
  width:56px;
  height:56px;
  border-radius:18px;
  overflow:hidden;
  flex:0 0 auto;
  border:1px solid rgba(255,255,255,.10);
  background:linear-gradient(180deg, rgba(27,42,82,.92), rgba(13,20,39,.98));
}
.podium-card-avatar img{
  width:100%;
  height:100%;
  object-fit:cover;
  display:block;
}
.podium-card-avatar-fallback{
  width:100%;
  height:100%;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size:22px;
  font-weight:900;
  color:#eef4ff;
}
.podium-card-title{
  min-width:0;
  display:grid;
  gap:4px;
}
.podium-card-rank{
  display:inline-flex;
  align-items:center;
  gap:8px;
  font-size:13px;
  font-weight:800;
  color:var(--muted);
  letter-spacing:.05em;
  text-transform:uppercase;
}
.podium-card-name{
  font-size:24px;
  font-weight:900;
  line-height:1.08;
}
.podium-card-subtitle{
  color:var(--muted);
  font-size:13px;
  line-height:1.45;
}
.podium-badges{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
}
.podium-badge{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:8px 11px;
  border-radius:999px;
  font-size:12px;
  font-weight:800;
  letter-spacing:.04em;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(255,255,255,.04);
}
.podium-badge.gold{
  border-color:rgba(255,204,102,.24);
  background:rgba(255,204,102,.08);
  color:#ffd98a;
}
.podium-badge.silver{
  border-color:rgba(216,227,240,.18);
  color:#d8e3f0;
}
.podium-badge.bronze{
  border-color:rgba(255,171,115,.18);
  color:#ffba87;
}
.podium-card-money{
  color:var(--gold);
  font-weight:900;
  font-size:24px;
}
.podium-card-meta{
  display:grid;
  grid-template-columns:repeat(3, minmax(0, 1fr));
  gap:8px;
}
.podium-card-meta-box{
  padding:10px 12px;
  border-radius:16px;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(255,255,255,.03);
}
.podium-card-meta-label{
  color:var(--muted);
  font-size:11px;
  text-transform:uppercase;
  letter-spacing:.06em;
  margin-bottom:6px;
}
.podium-card-meta-value{
  font-size:18px;
  font-weight:900;
  color:#eef4ff;
}
.leaderboard-list{
  display:grid;
  gap:12px;
}
.leaderboard-row{
  display:grid;
  grid-template-columns:56px minmax(0, 1fr) auto;
  gap:14px;
  align-items:center;
  padding:16px 18px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:22px;
  background:linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.02));
}
.leaderboard-row.top-10{
  border-color:rgba(79,127,211,.18);
  background:linear-gradient(180deg, rgba(54,87,150,.10), rgba(255,255,255,.02));
  box-shadow:0 0 0 1px rgba(79,127,211,.05) inset;
}
.leaderboard-row.top-3{
  border-color:rgba(255,204,102,.14);
}
.leaderboard-rank{
  width:56px;
  height:56px;
  border-radius:18px;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size:20px;
  font-weight:900;
  color:#eef4ff;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(255,255,255,.04);
}
.leaderboard-rank.top-1{
  color:var(--gold);
  border-color:rgba(255,204,102,.22);
  background:rgba(255,204,102,.08);
}
.leaderboard-rank.top-2{
  border-color:rgba(216,227,240,.20);
}
.leaderboard-rank.top-3{
  border-color:rgba(255,171,115,.18);
}
.leaderboard-main{
  min-width:0;
}
.leaderboard-main-top{
  display:flex;
  align-items:center;
  gap:12px;
  margin-bottom:8px;
  min-width:0;
}
.leaderboard-avatar{
  width:54px;
  height:54px;
  border-radius:16px;
  overflow:hidden;
  flex:0 0 auto;
  border:1px solid rgba(255,255,255,.10);
  background:linear-gradient(180deg, rgba(27,42,82,.92), rgba(13,20,39,.98));
}
.leaderboard-avatar img{
  width:100%;
  height:100%;
  object-fit:cover;
  display:block;
}
.leaderboard-avatar-fallback{
  width:100%;
  height:100%;
  display:flex;
  align-items:center;
  justify-content:center;
  color:#eef4ff;
  font-size:18px;
  font-weight:900;
}
.leaderboard-name{
  font-size:20px;
  font-weight:900;
  line-height:1.08;
}
.leaderboard-name-stack{
  min-width:0;
}
.leaderboard-ribbon{
  display:inline-flex;
  align-items:center;
  gap:6px;
  margin-top:6px;
  padding:6px 10px;
  border-radius:999px;
  font-size:11px;
  font-weight:800;
  letter-spacing:.05em;
  text-transform:uppercase;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(255,255,255,.03);
  color:var(--muted);
}
.leaderboard-ribbon.top-1{
  border-color:rgba(255,204,102,.22);
  background:rgba(255,204,102,.08);
  color:#ffd98a;
}
.leaderboard-ribbon.top-2{
  border-color:rgba(216,227,240,.18);
  color:#d8e3f0;
}
.leaderboard-ribbon.top-3{
  border-color:rgba(255,171,115,.18);
  color:#ffba87;
}
.leaderboard-ribbon.top-10{
  border-color:rgba(79,127,211,.18);
  color:#9cc5ff;
}
.leaderboard-chips{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
}
.leaderboard-chip{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:7px 10px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(255,255,255,.03);
  color:var(--muted);
  font-size:12px;
  font-weight:700;
}
.leaderboard-prize{
  text-align:right;
  display:grid;
  gap:4px;
}
.leaderboard-prize-label{
  color:var(--muted);
  font-size:11px;
  letter-spacing:.08em;
  text-transform:uppercase;
}
.leaderboard-prize-value{
  color:var(--gold);
  font-size:24px;
  font-weight:900;
  white-space:nowrap;
}

.footer{
  text-align:center;
  color:var(--muted);
  font-size:13px;
  padding:28px 0 12px;
}

.page-link,
.copy-btn{
  color:var(--text);
  text-decoration:none;
  border:1px solid var(--line);
  background:rgba(255,255,255,.05);
  padding:10px 13px;
  border-radius:12px;
  font-size:14px;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  cursor:pointer;
  transition:.18s ease;
}
.page-link:hover,
.copy-btn:hover{
  background:rgba(255,255,255,.08);
  border-color:#4f7fd3;
}

.player-link{
  color:var(--text);
  text-decoration:none;
}
.player-link:hover{
  text-decoration:underline;
}

.cta-row{
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  margin-top:18px;
}

.admin-form{
  display:flex;
  flex-direction:column;
  gap:18px;
}

.admin-form-grid{
  display:grid;
  grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:14px;
}

.admin-field{
  display:flex;
  flex-direction:column;
  gap:8px;
}

.admin-field.full{
  grid-column:1 / -1;
}

.admin-label{
  font-size:12px;
  font-weight:800;
  letter-spacing:.08em;
  text-transform:uppercase;
  color:var(--muted);
}

.admin-control,
.admin-select,
.admin-textarea{
  width:100%;
  border-radius:16px;
  border:1px solid var(--line);
  background:rgba(26, 39, 74, .92);
  color:var(--text);
  padding:14px 16px;
  outline:none;
  transition:border-color .2s ease, box-shadow .2s ease;
}

.admin-control::placeholder,
.admin-textarea::placeholder{
  color:rgba(202,216,255,.42);
}

.admin-control:focus,
.admin-select:focus,
.admin-textarea:focus{
  border-color:rgba(120,169,255,.58);
  box-shadow:0 0 0 3px rgba(64,120,255,.14);
}

.admin-select{
  appearance:none;
  background-image:
    linear-gradient(45deg, transparent 50%, rgba(214,227,255,.88) 50%),
    linear-gradient(135deg, rgba(214,227,255,.88) 50%, transparent 50%);
  background-position:
    calc(100% - 18px) calc(50% - 3px),
    calc(100% - 12px) calc(50% - 3px);
  background-size:6px 6px, 6px 6px;
  background-repeat:no-repeat;
  padding-right:38px;
}

.admin-select option{
  color:#0f1833;
}

.admin-textarea{
  min-height:96px;
  resize:vertical;
}

.admin-help{
  font-size:12px;
  line-height:1.5;
  color:var(--muted);
}

.admin-toggle{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:16px;
  padding:14px 16px;
  border-radius:16px;
  border:1px solid var(--line);
  background:rgba(26, 39, 74, .92);
}

.admin-toggle-copy{
  display:flex;
  flex-direction:column;
  gap:4px;
}

.admin-toggle-title{
  font-weight:800;
  color:var(--text);
}

.admin-toggle-sub{
  font-size:12px;
  color:var(--muted);
}

.admin-toggle input{
  width:20px;
  height:20px;
  accent-color:#55d68c;
}

@media (max-width: 900px){
  .admin-form-grid{
    grid-template-columns:1fr;
  }
  .podium{
    grid-template-columns:1fr;
  }
  .podium-meta{
    grid-template-columns:1fr;
  }
  .leaderboard-row{
    grid-template-columns:48px minmax(0,1fr);
  }
  .leaderboard-prize{
    grid-column:2;
    text-align:left;
  }
}

.copy-grid{
  display:grid;
  grid-template-columns:1fr auto;
  gap:10px;
  margin-top:12px;
}
.copy-value{
  display:flex;
  align-items:center;
  min-height:48px;
  padding:12px 14px;
  border-radius:14px;
  border:1px solid var(--line);
  background:rgba(255,255,255,.04);
  font-weight:700;
}

.hall-grid{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:14px;
}
.hall-card{
  border:1px solid var(--line);
  border-radius:22px;
  padding:18px;
  text-align:center;
  background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.03));
}
.hall-rank{
  font-size:32px;
  margin-bottom:8px;
}
.hall-name{
  font-size:18px;
  font-weight:900;
  margin-bottom:6px;
}
.hall-amount{
  color:var(--gold);
  font-weight:900;
  font-size:20px;
  white-space:nowrap;
}

.form-stack{
  display:grid;
  gap:12px;
  position:relative;
  z-index:1;
}

.field{
  display:grid;
  gap:7px;
}

.field label{
  color:var(--muted);
  font-size:12px;
  font-weight:700;
  letter-spacing:.4px;
  text-transform:uppercase;
}

.field input{
  width:100%;
  border-radius:16px;
  border:1px solid var(--line);
  background:rgba(255,255,255,.04);
  color:var(--text);
  padding:13px 14px;
  font-size:14px;
  outline:none;
}

.field input:focus{
  border-color:#4f7fd3;
  box-shadow:0 0 0 3px rgba(79,127,211,.12);
}

.button-primary{
  border:1px solid rgba(126,231,135,.22);
  background:linear-gradient(180deg, rgba(126,231,135,.18), rgba(126,231,135,.10));
  color:#dffff0;
  border-radius:16px;
  padding:13px 16px;
  font-size:14px;
  font-weight:800;
  cursor:pointer;
}

.button-primary:hover{
  transform:translateY(-1px);
  box-shadow:0 10px 24px rgba(126,231,135,.12);
}

.notice{
  margin-top:14px;
  padding:12px 14px;
  border-radius:16px;
  border:1px solid var(--line);
  background:rgba(255,255,255,.04);
  color:var(--text);
  line-height:1.5;
  position:relative;
  z-index:1;
}
.notice.ok{
  border-color:rgba(126,231,135,.24);
  background:rgba(126,231,135,.10);
  color:#dffff0;
}
.notice.error{
  border-color:rgba(255,123,114,.24);
  background:rgba(255,123,114,.10);
  color:#ffe2df;
}

.status-pill{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  padding:6px 10px;
  border-radius:999px;
  border:1px solid var(--line);
  background:rgba(255,255,255,.05);
  font-size:12px;
  font-weight:800;
  letter-spacing:.3px;
  white-space:nowrap;
}

.status-pill.confirmed{
  border-color:rgba(126,231,135,.24);
  background:rgba(126,231,135,.12);
  color:#bff3c5;
}

.status-pill.registered{
  border-color:rgba(102,167,255,.22);
  background:rgba(102,167,255,.12);
  color:#cfe2ff;
}

.status-pill.waitlist{
  border-color:rgba(255,155,82,.22);
  background:rgba(255,155,82,.12);
  color:#ffd1ae;
}

.status-pill.rejected{
  border-color:rgba(255,123,114,.24);
  background:rgba(255,123,114,.12);
  color:#ffd9d6;
}

.status-pill.source{
  border-color:rgba(156,123,255,.24);
  background:rgba(156,123,255,.12);
  color:#ddceff;
}

.history-grid{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:16px;
  position:relative;
  z-index:1;
}

.hub-ops-grid{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:18px;
  margin-top:20px;
  align-items:start;
}

.hub-ops-grid > .card{
  grid-column:auto;
  display:flex;
  flex-direction:column;
}

.hub-ops-grid .section-head{
  margin-bottom:14px;
}

.hub-ops-grid .list{
  flex:1 1 auto;
}

.hub-ops-grid .prize-summary{
  margin-top:16px;
}

.hub-ops-grid .prize-note,
.hub-ops-grid .cta-row{
  margin-top:14px;
}

.hub-ops-grid #sponsors .list{
  flex:0 0 auto;
  align-content:start;
  margin-top:0;
}

.hub-ops-grid #sponsors .row{
  min-height:0;
  padding:12px 14px;
  align-items:center;
}

.hub-ops-grid #sponsors{
  justify-content:flex-start;
}

.hub-ops-grid #sponsors .prize-note{
  margin-top:0;
  margin-bottom:14px;
}

.sponsor-main{
  display:flex;
  align-items:center;
  gap:10px;
  min-width:0;
  flex:1 1 auto;
}

.sponsor-copy{
  min-width:0;
}

.sponsor-name{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
  font-weight:800;
}

.sponsor-note{
  margin-top:4px;
  color:var(--muted);
  font-size:12px;
}

.sponsor-wall{
  display:grid;
  gap:14px;
  position:relative;
  z-index:1;
}

.sponsor-hero{
  position:relative;
  display:flex;
  justify-content:space-between;
  gap:18px;
  padding:20px;
  border-radius:24px;
  border:1px solid rgba(255,204,102,.26);
  background:
    radial-gradient(circle at top right, rgba(255,204,102,.14), transparent 42%),
    linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.03));
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.04),
    0 20px 40px rgba(4,10,22,.24);
  overflow:hidden;
}

.sponsor-hero::after{
  content:"";
  position:absolute;
  width:180px;
  height:180px;
  right:-80px;
  bottom:-90px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(255,204,102,.16), transparent 72%);
  pointer-events:none;
}

.sponsor-hero-main{
  display:flex;
  align-items:flex-start;
  gap:14px;
  min-width:0;
  flex:1 1 auto;
}

.sponsor-hero-avatar{
  width:56px;
  height:56px;
  border-radius:18px;
  overflow:hidden;
  flex:0 0 auto;
  border:1px solid rgba(255,255,255,.10);
  background:linear-gradient(180deg, rgba(27,42,82,.92), rgba(13,20,39,.98));
  box-shadow:0 12px 24px rgba(0,0,0,.22);
}

.sponsor-hero-avatar img{
  width:100%;
  height:100%;
  object-fit:cover;
  display:block;
}

.sponsor-hero-copy{
  min-width:0;
}

.sponsor-hero-label{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:7px 12px;
  border-radius:999px;
  border:1px solid rgba(255,204,102,.22);
  background:rgba(255,204,102,.12);
  color:#ffd98a;
  font-size:11px;
  font-weight:800;
  letter-spacing:.7px;
  text-transform:uppercase;
  margin-bottom:10px;
}

.sponsor-hero-name{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
  font-size:22px;
  font-weight:900;
  line-height:1.12;
  margin-bottom:8px;
}

.sponsor-hero-sub{
  color:var(--muted);
  font-size:13px;
  line-height:1.6;
}

.sponsor-hero-badges{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin-top:12px;
}

.sponsor-hero-right{
  display:grid;
  gap:8px;
  justify-items:end;
  align-content:start;
  text-align:right;
  min-width:138px;
}

.sponsor-hero-amount{
  color:var(--gold);
  font-size:28px;
  font-weight:900;
  line-height:1;
  white-space:nowrap;
}

.sponsor-hero-share{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:8px 12px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.05);
  color:#edf4ff;
  font-size:11px;
  font-weight:800;
  letter-spacing:.2px;
}

.sponsor-stack{
  display:grid;
  gap:10px;
}

.sponsor-rank-row{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  padding:13px 15px;
  border-radius:18px;
  border:1px solid var(--line);
  background:rgba(255,255,255,.03);
  transition:.16s ease;
}

.sponsor-rank-row:hover{
  transform:translateY(-1px);
  border-color:#426aa9;
  background:rgba(255,255,255,.045);
}

.sponsor-rank-left{
  display:flex;
  align-items:center;
  gap:12px;
  min-width:0;
  flex:1 1 auto;
}

.sponsor-rank-pill{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  min-width:34px;
  height:34px;
  padding:0 10px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.05);
  color:#dfe9ff;
  font-size:12px;
  font-weight:900;
}

.sponsor-rank-copy{
  min-width:0;
}

.sponsor-rank-name{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
  font-weight:800;
}

.sponsor-rank-meta{
  margin-top:5px;
  color:var(--muted);
  font-size:12px;
}

.sponsor-rank-right{
  display:grid;
  gap:6px;
  justify-items:end;
  text-align:right;
  flex:0 0 auto;
}

.sponsor-rank-amount{
  font-size:18px;
  font-weight:900;
  color:#eef4ff;
  white-space:nowrap;
}

.sponsor-empty{
  padding:16px 18px;
  border:1px dashed rgba(255,255,255,.10);
  border-radius:18px;
  background:rgba(255,255,255,.025);
  color:var(--muted);
}

.history-card{
  border:1px solid var(--line);
  border-radius:24px;
  padding:18px;
  background:linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.025));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
  transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}

.history-card:hover{
  transform:translateY(-2px);
  border-color:rgba(102,167,255,.30);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.04),
    0 16px 36px rgba(4,10,22,.26);
}

.history-head{
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:14px;
  margin-bottom:14px;
}

.history-season{
  color:#ffd98a;
  font-size:12px;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.7px;
  margin-bottom:6px;
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:6px 10px;
  border-radius:999px;
  border:1px solid rgba(255,204,102,.20);
  background:rgba(255,204,102,.10);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
}

.history-title{
  font-size:22px;
  font-weight:900;
  line-height:1.12;
}

.history-title a{
  color:var(--text);
  text-decoration:none;
}

.history-title a:hover{
  text-decoration:underline;
}

.history-submeta{
  margin-top:8px;
  color:var(--muted);
  font-size:13px;
  font-weight:700;
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  align-items:center;
}

.history-submeta .dot{
  width:4px;
  height:4px;
  border-radius:50%;
  background:rgba(255,255,255,.22);
}

.history-stat-grid{
  display:grid;
  grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:12px;
  margin-top:16px;
}

.history-stat{
  padding:14px 16px;
  border-radius:18px;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(255,255,255,.035);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.02);
}

.history-stat.kpi{
  background:linear-gradient(180deg, rgba(255,204,102,.08), rgba(255,255,255,.03));
  border-color:rgba(255,204,102,.18);
}

.history-stat-label{
  color:#a7b7da;
  font-size:11px;
  font-weight:800;
  letter-spacing:.6px;
  text-transform:uppercase;
  margin-bottom:8px;
}

.history-stat-value{
  color:var(--text);
  font-size:28px;
  font-weight:900;
  line-height:1;
}

.history-stat-sub{
  margin-top:8px;
  color:var(--muted);
  font-size:12px;
  font-weight:700;
}

.history-detail-grid{
  display:grid;
  grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:10px;
  margin-top:14px;
}

.history-detail{
  display:flex;
  justify-content:space-between;
  gap:10px;
  padding:12px 14px;
  border-radius:16px;
  border:1px solid rgba(255,255,255,.07);
  background:rgba(255,255,255,.025);
}

.history-detail-label{
  color:#9fb1d8;
  font-size:12px;
  font-weight:800;
  letter-spacing:.4px;
  text-transform:uppercase;
}

.history-detail-value{
  color:var(--text);
  font-size:14px;
  font-weight:800;
  text-align:right;
}

.history-podium{
  display:grid;
  gap:10px;
}

.history-archive-layout{
  display:grid;
  grid-template-columns:minmax(0, 1.1fr) minmax(300px, .9fr);
  gap:16px;
  margin-top:16px;
}

.history-feature{
  padding:18px;
  border-radius:22px;
  border:1px solid rgba(255,204,102,.16);
  background:linear-gradient(180deg, rgba(255,204,102,.08), rgba(255,255,255,.03));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
}

.history-feature-label{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:6px 10px;
  border-radius:999px;
  border:1px solid rgba(255,204,102,.20);
  background:rgba(255,204,102,.10);
  color:#ffd98a;
  font-size:11px;
  font-weight:900;
  letter-spacing:.6px;
  text-transform:uppercase;
}

.history-feature-name{
  margin-top:12px;
  font-size:28px;
  font-weight:900;
  line-height:1.05;
}

.history-feature-meta{
  margin-top:8px;
  color:var(--muted);
  font-size:13px;
  line-height:1.55;
}

.history-feature-bottom{
  display:flex;
  justify-content:space-between;
  align-items:flex-end;
  gap:14px;
  flex-wrap:wrap;
  margin-top:18px;
}

.history-feature-money{
  color:var(--gold);
  font-size:30px;
  font-weight:900;
  line-height:1;
}

.history-feature-sub{
  margin-top:6px;
  color:var(--muted);
  font-size:12px;
  font-weight:700;
}

.history-side-stack{
  display:grid;
  gap:10px;
}

.history-empty-shell{
  border:1px dashed rgba(102,167,255,.22);
  border-radius:26px;
  padding:18px;
  background:
    radial-gradient(circle at top right, rgba(102,167,255,.09), transparent 32%),
    linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.02));
}

.premium-empty-card{
  position:relative;
  overflow:hidden;
  border:1px solid rgba(102,167,255,.18);
  border-radius:28px;
  padding:24px;
  background:
    radial-gradient(circle at top right, rgba(102,167,255,.10), transparent 28%),
    linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02));
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.03),
    0 18px 40px rgba(4,10,22,.18);
}

.premium-empty-card::before{
  content:"";
  position:absolute;
  inset:0;
  background:
    linear-gradient(120deg, rgba(255,255,255,.05), transparent 22%),
    repeating-linear-gradient(135deg, transparent 0 15px, rgba(255,255,255,.02) 15px 16px);
  pointer-events:none;
  opacity:.45;
}

.premium-empty-head{
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:14px;
  margin-bottom:16px;
  position:relative;
  z-index:1;
}

.premium-empty-kicker{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:8px 12px;
  border-radius:999px;
  border:1px solid rgba(255,204,102,.22);
  background:rgba(255,204,102,.12);
  color:#ffd98a;
  font-size:12px;
  font-weight:800;
  letter-spacing:.6px;
  text-transform:uppercase;
}

.premium-empty-title{
  margin:0;
  font-size:30px;
  line-height:1.08;
  font-weight:900;
  position:relative;
  z-index:1;
}

.premium-empty-copy{
  color:var(--muted);
  line-height:1.72;
  max-width:62ch;
  position:relative;
  z-index:1;
}

.premium-empty-stats{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:12px;
  margin-top:18px;
  position:relative;
  z-index:1;
}

.premium-empty-stat{
  border:1px solid rgba(102,167,255,.16);
  border-radius:18px;
  padding:14px 16px;
  background:rgba(9,19,42,.52);
}

.premium-empty-stat .label{
  color:var(--muted);
  font-size:11px;
  font-weight:800;
  letter-spacing:.65px;
  text-transform:uppercase;
  margin-bottom:8px;
}

.premium-empty-stat .value{
  color:var(--text);
  font-size:24px;
  font-weight:900;
  line-height:1.1;
}

.premium-empty-actions{
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  margin-top:18px;
  position:relative;
  z-index:1;
}

.history-empty-cta{
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  margin-top:18px;
}

.history-empty-stat{
  display:grid;
  gap:6px;
  padding:14px 16px;
  border-radius:18px;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(255,255,255,.03);
}

.history-empty-stat .label{
  color:var(--muted);
  font-size:11px;
  font-weight:800;
  letter-spacing:.08em;
  text-transform:uppercase;
}

.history-empty-stat .value{
  font-size:24px;
  font-weight:900;
  line-height:1;
}

.history-podium-row{
  display:grid;
  grid-template-columns:34px minmax(0,1fr) auto auto;
  gap:10px;
  align-items:center;
  padding:12px 14px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:18px;
  background:rgba(255,255,255,.03);
}

.history-rank{
  display:flex;
  align-items:center;
  justify-content:center;
  width:34px;
  height:34px;
  border-radius:50%;
  background:rgba(255,255,255,.06);
  color:#ffd98a;
  font-weight:900;
}

.history-player{
  min-width:0;
  font-weight:800;
}

.history-points,
.history-prize{
  color:var(--muted);
  font-size:13px;
  font-weight:700;
  white-space:nowrap;
}

.history-foot{
  display:flex;
  justify-content:space-between;
  gap:10px;
  flex-wrap:wrap;
  margin-top:14px;
  align-items:center;
}

.history-meta-list{
  display:grid;
  gap:8px;
  margin-top:14px;
}

.hub-overview-grid{
  display:grid;
  gap:10px;
  margin-top:14px;
}

.hub-overview-main{
  display:grid;
  grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:10px;
}

.hub-overview-stats{
  display:grid;
  grid-template-columns:repeat(3, minmax(0, 1fr));
  gap:10px;
}

.hub-overview-item{
  min-width:0;
  padding:12px 14px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:16px;
  background:rgba(255,255,255,.03);
}

.hub-overview-item .label{
  color:var(--muted);
  font-size:12px;
  font-weight:800;
  letter-spacing:.2px;
  margin-bottom:8px;
}

.hub-overview-item .value{
  color:var(--text);
  font-size:15px;
  font-weight:800;
  line-height:1.2;
  word-break:break-word;
}

.hub-overview-item.stat .value{
  font-size:20px;
  font-weight:900;
}

.admin-hero-grid{
  display:grid;
  grid-template-columns:minmax(0, 1.35fr) minmax(320px, .9fr);
  gap:20px;
  align-items:stretch;
}

.admin-hero-panel{
  position:relative;
  overflow:hidden;
  border:1px solid rgba(255,255,255,.08);
  border-radius:28px;
  padding:22px 24px;
  background:
    radial-gradient(circle at 82% 18%, rgba(255,204,102,.12), transparent 34%),
    linear-gradient(180deg, rgba(255,255,255,.055), rgba(255,255,255,.03));
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.04),
    0 22px 40px rgba(4,10,22,.20);
}

.admin-hero-panel::before{
  content:"";
  position:absolute;
  inset:auto -18% 22% auto;
  width:240px;
  height:240px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(102,167,255,.12), transparent 70%);
  pointer-events:none;
}

.admin-hero-kicker{
  color:#ffd98a;
  font-size:12px;
  font-weight:900;
  letter-spacing:.8px;
  text-transform:uppercase;
}

.admin-hero-title{
  margin:8px 0 10px;
  font-size:34px;
  font-weight:900;
  line-height:1.05;
}

.admin-hero-copy{
  color:#a7b7da;
  font-size:15px;
  line-height:1.7;
  max-width:760px;
}

.admin-hero-stats{
  display:grid;
  grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:12px;
  margin-top:16px;
}

.admin-hero-stat{
  border:1px solid rgba(255,255,255,.08);
  border-radius:18px;
  padding:14px 16px;
  background:linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.025));
}

.admin-hero-stat .label{
  color:#93a8d0;
  font-size:11px;
  font-weight:900;
  letter-spacing:.6px;
  text-transform:uppercase;
  margin-bottom:6px;
}

.admin-hero-stat .value{
  color:var(--text);
  font-size:26px;
  font-weight:900;
  line-height:1;
}

.admin-hero-stat .sub{
  margin-top:8px;
  color:#8ea3cc;
  font-size:12px;
  line-height:1.5;
}

.admin-section{
  position:relative;
  overflow:hidden;
}

.admin-section::before{
  content:"";
  position:absolute;
  top:-40px;
  right:-20px;
  width:180px;
  height:180px;
  border-radius:50%;
  background:radial-gradient(circle, rgba(102,167,255,.08), transparent 72%);
  pointer-events:none;
}

.admin-section .prize-note{
  max-width:900px;
}

.admin-module-grid{
  display:grid;
  grid-template-columns:repeat(3, minmax(0, 1fr));
  gap:18px;
  margin-top:18px;
}

.admin-module-card{
  position:relative;
  overflow:hidden;
  border:1px solid rgba(255,255,255,.08);
  border-radius:24px;
  padding:20px;
  background:
    linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.025)),
    radial-gradient(circle at 100% 0%, rgba(255,204,102,.08), transparent 28%);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
  transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}

.admin-module-card:hover,
.admin-op-card:hover{
  transform:translateY(-2px);
  border-color:rgba(102,167,255,.24);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.04),
    0 16px 34px rgba(4,10,22,.22);
}

.admin-module-kicker{
  color:#ffd98a;
  font-size:12px;
  font-weight:900;
  text-transform:uppercase;
  letter-spacing:.7px;
}

.admin-module-title{
  margin-top:8px;
  font-size:28px;
  font-weight:900;
  line-height:1.05;
}

.admin-module-copy{
  margin-top:10px;
  color:#9cb0d8;
  line-height:1.7;
  min-height:74px;
}

.admin-module-footer{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  margin-top:16px;
  flex-wrap:wrap;
}

.admin-module-grid .page-link{
  min-width:180px;
  justify-content:center;
}

.admin-op-grid{
  display:grid;
  grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:18px;
  margin-top:18px;
}

.admin-op-card{
  position:relative;
  overflow:hidden;
  border:1px solid rgba(255,255,255,.08);
  border-radius:24px;
  padding:20px;
  background:
    linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.025)),
    radial-gradient(circle at 100% 0%, rgba(255,255,255,.05), transparent 30%);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
  transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}

.admin-op-top{
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:14px;
}

.admin-op-title{
  margin-top:6px;
  font-size:28px;
  font-weight:900;
  line-height:1.05;
}

.admin-op-title a{
  color:inherit;
  text-decoration:none;
}

.admin-op-title a:hover{
  color:#ffd98a;
}

.admin-op-meta{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin-top:12px;
}

.admin-op-chip{
  display:inline-flex;
  align-items:center;
  padding:6px 10px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.09);
  background:rgba(255,255,255,.04);
  color:#edf4ff;
  font-size:12px;
  font-weight:800;
}

.admin-op-stats{
  display:grid;
  grid-template-columns:repeat(3, minmax(0, 1fr));
  gap:10px;
  margin-top:16px;
}

.admin-op-stat{
  padding:12px 14px;
  border-radius:18px;
  border:1px solid rgba(255,255,255,.08);
  background:linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.02));
}

.admin-op-stat .label{
  color:#93a8d0;
  font-size:11px;
  font-weight:900;
  letter-spacing:.5px;
  text-transform:uppercase;
  margin-bottom:6px;
}

.admin-op-stat .value{
  font-size:20px;
  font-weight:900;
  line-height:1.2;
  color:var(--text);
}

.analytics-grid{
  display:grid;
  grid-template-columns:1.1fr .9fr;
  gap:18px;
  margin-top:18px;
}

.analytics-panel{
  border:1px solid rgba(255,255,255,.08);
  border-radius:22px;
  background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02));
  padding:20px;
  display:grid;
  gap:14px;
}

.analytics-kicker{
  color:#93a8d0;
  font-size:11px;
  font-weight:900;
  letter-spacing:.7px;
  text-transform:uppercase;
}

.analytics-top{
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:14px;
}

.analytics-title{
  font-size:24px;
  font-weight:900;
  line-height:1.15;
  color:var(--text);
}

.analytics-sub{
  color:var(--muted);
  font-size:14px;
  line-height:1.65;
}

.analytics-duo{
  display:grid;
  grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:12px;
}

.analytics-metric{
  padding:14px 16px;
  border-radius:18px;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(10,19,38,.45);
}

.analytics-metric .label{
  color:#93a8d0;
  font-size:11px;
  font-weight:900;
  letter-spacing:.5px;
  text-transform:uppercase;
  margin-bottom:6px;
}

.analytics-metric .value{
  color:var(--text);
  font-size:26px;
  font-weight:900;
  line-height:1.1;
}

.analytics-metric .hint{
  color:var(--muted);
  font-size:13px;
  margin-top:6px;
}

.analytics-page-list{
  display:grid;
  gap:10px;
}

.analytics-page{
  display:grid;
  grid-template-columns:minmax(0, 1fr) auto;
  gap:12px;
  align-items:center;
  padding:12px 14px;
  border-radius:16px;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(10,19,38,.42);
}

.analytics-page-path{
  color:var(--text);
  font-weight:800;
  word-break:break-word;
}

.analytics-page-meta{
  color:var(--muted);
  font-size:13px;
  text-align:right;
  white-space:nowrap;
}

.admin-op-actions{
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  margin-top:18px;
}

.admin-op-actions .page-link,
.admin-op-actions .button-primary{
  min-width:136px;
  justify-content:center;
}

.admin-op-actions-group{
  display:grid;
  gap:8px;
  margin-top:18px;
}

.admin-op-actions-label{
  color:#93a8d0;
  font-size:11px;
  font-weight:900;
  letter-spacing:.6px;
  text-transform:uppercase;
}

.history-meta-row{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  padding:10px 12px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:14px;
  background:rgba(255,255,255,.03);
  color:var(--text);
  font-size:13px;
}

.history-meta-row .label{
  color:var(--muted);
  font-weight:700;
}

.live-register-link{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  gap:8px;
  min-width:132px;
  padding:10px 14px;
  border-radius:14px;
  border:1px solid rgba(126,231,135,.24);
  background:linear-gradient(180deg, rgba(126,231,135,.16), rgba(54,133,87,.18));
  color:#dfffe5;
  text-decoration:none;
  font-size:13px;
  font-weight:800;
  letter-spacing:.2px;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.04),
    0 10px 24px rgba(48,110,73,.18);
  transition:transform .16s ease, border-color .16s ease, filter .16s ease;
}

.live-register-link:hover{
  transform:translateY(-1px);
  border-color:rgba(126,231,135,.34);
  filter:brightness(1.05);
}

.info-strip{
  border:1px solid var(--line);
  border-radius:20px;
  padding:16px 18px;
  background:rgba(255,255,255,.03);
  color:var(--muted);
  line-height:1.7;
}

.qr-box{
  margin-top:18px;
  border:1px solid var(--line);
  border-radius:22px;
  padding:18px;
  background:rgba(255,255,255,.03);
  text-align:center;
}
.qr-box img{
  max-width:280px;
  width:100%;
  border-radius:18px;
  border:1px solid var(--line);
  display:block;
  margin:0 auto 12px;
}

.bottom-cta{
  margin-top:18px;
  padding:18px;
  border:1px solid var(--line);
  border-radius:22px;
  background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.03));
  display:flex;
  flex-wrap:wrap;
  justify-content:space-between;
  gap:14px;
  align-items:center;
}


@media (max-width: 1180px){
  .hero-grid{grid-template-columns:1fr}
  .admin-hero-grid{grid-template-columns:1fr}
  .admin-module-grid{grid-template-columns:1fr}
  .admin-op-grid{grid-template-columns:1fr}
  .analytics-grid{grid-template-columns:1fr}
  .analytics-duo{grid-template-columns:1fr}
  .hero-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .bracket-visual{grid-template-columns:1fr;gap:12px}
  .bracket-connector{display:none}
  .stage-grid{grid-template-columns:1fr}
  .prize-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .podium{grid-template-columns:1fr}
  .hall-grid{grid-template-columns:1fr}
  .history-grid{grid-template-columns:1fr}
  .history-archive-layout{grid-template-columns:1fr}
  .hub-ops-grid{grid-template-columns:1fr}
  .hub-overview-main,
  .hub-overview-stats{grid-template-columns:1fr}
  .cols-8,.cols-6,.cols-4{grid-column:span 12}
}

@media (max-width: 720px){
  .wrap{padding:14px}
  .hero{padding:20px}
  .hero h1{font-size:32px}
  .hero-stats{grid-template-columns:1fr}
  .prize-grid{grid-template-columns:1fr}
  .nav{position:static}
  .mini .value,.champion-prize{font-size:22px}
  .prize-item .amount{font-size:22px}
  .copy-grid{grid-template-columns:1fr}
  .featured-slide-stats{grid-template-columns:1fr}
  .admin-hero-stats{grid-template-columns:1fr}
  .admin-op-stats{grid-template-columns:1fr}
  .history-podium-row{grid-template-columns:34px minmax(0,1fr)}
  .history-points,.history-prize{grid-column:2}
  .podium-name,.podium-money{font-size:28px}
  .podium-card-name{font-size:20px}
  .leaderboard-name{font-size:18px}
  .leaderboard-prize-value{font-size:22px}
  .leaderboard-main-top{align-items:flex-start}
}
"""

BASE_SCRIPT = """
<script>
function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'absolute';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  ta.setSelectionRange(0, 99999);
  try {
    document.execCommand('copy');
    alert('Copied: ' + text);
  } catch (e) {
    alert('Copy failed. Please copy manually: ' + text);
  }
  document.body.removeChild(ta);
}

async function copyText(text) {
  if (!text) return;

  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      alert('Copied: ' + text);
      return;
    } catch (e) {}
  }

  fallbackCopy(text);
}
</script>
"""

HOME_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>{{ tournament.title if tournament else "Chess Of Mongolia Tournament Platform" }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero" id="home">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Chess Of Mongolia Tournament Platform</div>
          <h1>Chess Of Mongolia Tournament Hub</h1>
          <div class="subtitle">
            {% if tournament_hub %}
              Нэг weekly-ээр хязгаарлагдахгүй tournament platform. Live event, archive, confirmed players, stage progression, sponsor болон champion history-г нэг дороос харуулна.
            {% else %}
              Одоогоор tournament data олдсонгүй.
            {% endif %}
          </div>

          <div class="status-row">
            <span class="badge gold">Tournament Platform</span>
            <span class="badge green">Live {{ live_tournament_count }}</span>
            <span class="badge purple">Archive {{ completed_tournament_count }}</span>
            <span class="badge blue">Events {{ tournament_hub|length }}</span>
            <span class="badge cyan">Listed Prize {{ money(hub_total_prize) }}</span>
          </div>

          <div class="hero-stats">
            <div class="mini">
              <div class="label">Active Tournaments</div>
              <div class="value">{{ live_tournament_count }}</div>
            </div>
            <div class="mini">
              <div class="label">Completed Events</div>
              <div class="value">{{ completed_tournament_count }}</div>
            </div>
            <div class="mini">
              <div class="label">Listed Prize</div>
              <div class="value">{{ money(hub_total_prize) }}</div>
            </div>
            <div class="mini">
              <div class="label">Confirmed Seats</div>
              <div class="value">{{ hub_total_confirmed }}</div>
            </div>
          </div>
        </div>

        <div class="champion-card featured-slider" {% if hub_live and hub_live|length > 1 %}data-featured-slider{% endif %}>
          {% if hub_live %}
            {% for item in hub_live %}
            <div class="featured-slide {% if loop.first %}active{% endif %}">
              <div class="featured-slide-head">
                <div class="champion-label">Featured Tournament</div>
                <span class="badge {% if item.status == 'completed' %}purple{% else %}green{% endif %}">{{ tournament_status_label(item.status) }}</span>
              </div>
              <div class="champion-name">
                <a class="player-link" href="/history/{{ item.id }}">{{ item.title }}</a>
              </div>
              <div class="featured-slide-meta">
                <span class="featured-slide-chip">{{ item.season_name or "-" }}</span>
                <span class="featured-slide-chip">{{ item.type|replace('_', ' ')|title }}</span>
                <span class="featured-slide-chip">{{ schedule_value(item.start_time) }}</span>
              </div>
              <div class="champion-prize">{{ money(item.prize_total) }}</div>
              <div class="champion-sub">
                Нээлттэй event-үүдийг ээлжлэн spotlight хийж байна. Detail page дээр confirmed players, bracket archive, standings болон site registration flow нь харагдана.
              </div>
              <div class="featured-slide-stats">
                <div class="featured-slide-stat">
                  <div class="label">Confirmed</div>
                  <div class="value">{{ item.confirmed_count }}/{{ item.max_players }}</div>
                </div>
                <div class="featured-slide-stat">
                  <div class="label">Waitlist</div>
                  <div class="value">{{ item.waitlist_count }}</div>
                </div>
              </div>
              <div class="featured-slide-cta">
                <a class="live-register-link" href="/history/{{ item.id }}">Бүртгүүлэх</a>
              </div>
            </div>
            {% endfor %}
            {% if hub_live|length > 1 %}
            <div class="featured-slider-controls">
              <div class="featured-slider-left">
                <button class="featured-arrow" type="button" data-featured-prev aria-label="Previous">‹</button>
                {% for item in hub_live %}
                <button class="featured-slider-dot {% if loop.first %}active{% endif %}" type="button" aria-label="Featured {{ loop.index }}"></button>
                {% endfor %}
                <button class="featured-arrow" type="button" data-featured-next aria-label="Next">›</button>
              </div>
              <a class="page-link" href="/tournaments">All Open Tournaments</a>
            </div>
            <div class="featured-progress">
              <div class="featured-progress-bar animating" data-featured-progress></div>
            </div>
            {% endif %}
          {% else %}
            <div class="champion-label">Featured Tournament</div>
            <div class="champion-name">Featured event бэлэн болмогц энд spotlight орж ирнэ.</div>
            <div class="champion-meta">Одоогоор live tournament алга. Шинэ event үүсэхэд hero spotlight автоматаар шинэчлэгдэнэ.</div>
            <div class="featured-slide-stats">
              <div class="featured-slide-stat">
                <div class="label">Live Events</div>
                <div class="value">0</div>
              </div>
              <div class="featured-slide-stat">
                <div class="label">Ready State</div>
                <div class="value">Fresh</div>
              </div>
            </div>
            <div class="featured-slide-cta">
              <a class="page-link" href="/tournaments">Tournament Hub</a>
            </div>
          {% endif %}
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/donate">Donate</a>
      <a href="/contact">Contact</a>
      <a href="/leaderboard">Leaderboard</a>
      <a href="/history">Tournament History</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    {% if admin_notice %}
    <div class="notice {{ 'ok' if admin_ok else 'error' }}">{{ admin_notice }}</div>
    {% endif %}

    {% if admin_debug %}
    <div class="notice {{ 'ok' if admin_enabled else 'error' }}">{{ admin_debug }}</div>
    {% endif %}

    <div class="grid">
      <section class="card">
        <div class="section-head">
          <h2>Tournament Hub</h2>
          <span class="badge blue">{{ tournament_hub|length }} Events</span>
        </div>

        <div class="section-head" style="margin-top:4px">
          <h3 style="margin:0">Live & Open</h3>
          <div style="display:flex; gap:10px; align-items:center">
            {% if admin_panel_enabled %}
            <a class="btn secondary" href="/admin?key={{ admin_panel_key }}">Admin Dashboard</a>
            {% endif %}
            <span class="badge green">{{ live_tournament_count }} Live</span>
          </div>
        </div>
        <div class="history-grid">
          {% for item in hub_live %}
            <article class="history-card">
              <div class="history-head">
                <div>
                  <div class="history-season">{{ item.season_name or "-" }}</div>
                  <div class="history-title"><a href="/history/{{ item.id }}">{{ item.title }}</a></div>
                </div>
                <span class="badge green">{{ tournament_status_label(item.status) }}</span>
              </div>
              <div class="hub-overview-grid">
                <div class="hub-overview-main">
                  <div class="hub-overview-item">
                    <div class="label">Бүртгэлийн хураамж</div>
                    <div class="value">{{ money(item.entry_fee) }}</div>
                  </div>
                  <div class="hub-overview-item">
                    <div class="label">Эхлэх цаг</div>
                    <div class="value">{{ schedule_value(item.start_time) }}</div>
                  </div>
                </div>
                <div class="hub-overview-stats">
                  <div class="hub-overview-item stat">
                    <div class="label">Confirmed</div>
                    <div class="value">{{ item.confirmed_count }}/{{ item.max_players }}</div>
                  </div>
                  <div class="hub-overview-item stat">
                    <div class="label">Registered</div>
                    <div class="value">{{ item.registered_count }}</div>
                  </div>
                  <div class="hub-overview-item stat">
                    <div class="label">Waitlist</div>
                    <div class="value">{{ item.waitlist_count }}</div>
                  </div>
                </div>
              </div>
              <div class="history-foot">
                <span class="badge gold">{{ money(item.prize_total) }}</span>
                <a class="live-register-link" href="/history/{{ item.id }}">Бүртгүүлэх</a>
              </div>
            </article>
          {% endfor %}
          {% if not hub_live %}
            <div class="premium-empty-card">
              <div class="premium-empty-head">
                <div class="premium-empty-kicker">Live Queue Empty</div>
                <span class="badge blue">Fresh Start</span>
              </div>
              <h3 class="premium-empty-title">Tournament хараахан үүсээгүй байна</h3>
              <div class="premium-empty-copy">Энд шинэ tournament үүсмэгц live card болж шууд харагдана. Registration, confirmed players, prize pool болон event detail бүгд автоматаар энэ хэсэгт орж ирнэ.</div>
              <div class="premium-empty-stats">
                <div class="premium-empty-stat">
                  <div class="label">Live Events</div>
                  <div class="value">0</div>
                </div>
                <div class="premium-empty-stat">
                  <div class="label">Hub Status</div>
                  <div class="value">Ready</div>
                </div>
                <div class="premium-empty-stat">
                  <div class="label">Next Step</div>
                  <div class="value">Create</div>
                </div>
              </div>
              {% if admin_panel_enabled %}
              <div class="premium-empty-actions">
                <a class="btn secondary" href="/admin?key={{ admin_panel_key }}">Admin Dashboard</a>
                <a class="page-link" href="/tournaments">Tournament Hub</a>
              </div>
              {% endif %}
            </div>
          {% endif %}
        </div>

      </section>

    </div>

    <div class="bottom-cta">
      <div>
        <strong>Тэмцээнүүдээ hub дээрээс удирдаж, archive дээрээс түүхээ үзнэ.</strong><br>
        <span class="muted">Live event, standings, prize pool болон player profile бүгд нэг платформ дээр төвлөрнө.</span>
      </div>
      <div class="cta-row" style="margin-top:0;">
        <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
        <a class="page-link" href="/donate">Donate</a>
        <a class="page-link" href="/contact">Contact</a>
      </div>
    </div>

    <div class="footer">Community Tournament Platform · Esports Live Web</div>
  </div>
  {{ script|safe }}
  <script>
    (function () {
      const slider = document.querySelector('[data-featured-slider]');
      if (!slider) return;
      const slides = Array.from(slider.querySelectorAll('.featured-slide'));
      const dots = Array.from(slider.querySelectorAll('.featured-slider-dot'));
      const prev = slider.querySelector('[data-featured-prev]');
      const next = slider.querySelector('[data-featured-next]');
      const progress = slider.querySelector('[data-featured-progress]');
      if (slides.length <= 1) return;
      let index = 0;
      let timer = null;

      function render(next) {
        index = (next + slides.length) % slides.length;
        slides.forEach((slide, i) => slide.classList.toggle('active', i === index));
        dots.forEach((dot, i) => dot.classList.toggle('active', i === index));
        if (progress) {
          progress.classList.remove('animating');
          void progress.offsetWidth;
          progress.classList.add('animating');
        }
      }

      function start() {
        stop();
        timer = window.setInterval(() => render(index + 1), 4200);
      }

      function stop() {
        if (timer) window.clearInterval(timer);
      }

      dots.forEach((dot, i) => {
        dot.addEventListener('click', () => {
          render(i);
          start();
        });
      });

      if (prev) {
        prev.addEventListener('click', () => {
          render(index - 1);
          start();
        });
      }

      if (next) {
        next.addEventListener('click', () => {
          render(index + 1);
          start();
        });
      }

      slider.addEventListener('mouseenter', stop);
      slider.addEventListener('mouseleave', start);
      start();
    })();
  </script>
</body>
</html>
"""
LEADERBOARD_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Leaderboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">Chess Of Mongolia Tournament Platform</div>
      <h1>All-Time Leaderboard</h1>
      <div class="subtitle">Weekly champion, podium болон нийт prize won мэдээлэл.</div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/donate">Donate</a>
      <a href="/contact">Contact</a>
      <a href="/leaderboard">Leaderboard</a>
      <a href="/history">Tournament History</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    <div class="grid">
      <section class="card">
        <div class="section-head">
          <h2>Top Players</h2>
          <span class="badge gold">Top {{ leaderboard|length }} Players</span>
        </div>

        {% if leaderboard|length >= 3 %}
          <div class="podium">
            <div class="podium-hero">
              <div class="podium-kicker">All-Time Champion Spotlight</div>
              <div class="podium-hero-main">
                <div class="podium-avatar">
                  {% if leaderboard[0].avatar_url %}
                    <img src="{{ leaderboard[0].avatar_url }}" alt="{{ leaderboard[0].display_name }}">
                  {% else %}
                    <div class="podium-avatar-fallback">{{ (leaderboard[0].display_name[:1] or '?')|upper }}</div>
                  {% endif %}
                </div>
                <div class="podium-medal">🥇</div>
                <div class="podium-name"><a class="player-link" href="/player/{{ leaderboard[0].user_id }}">{{ leaderboard[0].display_name }}</a></div>
                <div class="podium-subtitle">All-time #1 player · Weekly champion hierarchy дээр хамгийн өндөр байртай.</div>
                <div class="podium-money">{{ money(leaderboard[0].total_prize_money) }}</div>
                <div class="podium-badges">
                  <span class="podium-badge gold">🏆 Champion</span>
                  <span class="podium-badge gold">👑 #1 All-Time</span>
                </div>
              </div>
              <div class="podium-meta">
                <div class="podium-meta-item">
                  <div class="podium-meta-label">Championships</div>
                  <div class="podium-meta-value">{{ leaderboard[0].championships }}</div>
                </div>
                <div class="podium-meta-item">
                  <div class="podium-meta-label">Podiums</div>
                  <div class="podium-meta-value">{{ leaderboard[0].podiums }}</div>
                </div>
                <div class="podium-meta-item">
                  <div class="podium-meta-label">Tournaments</div>
                  <div class="podium-meta-value">{{ leaderboard[0].tournaments_played }}</div>
                </div>
              </div>
            </div>
            <div class="podium-side">
              <div class="podium-card silver">
                <div class="podium-card-top">
                  <div class="podium-card-header">
                    <div class="podium-card-avatar">
                      {% if leaderboard[1].avatar_url %}
                        <img src="{{ leaderboard[1].avatar_url }}" alt="{{ leaderboard[1].display_name }}">
                      {% else %}
                        <div class="podium-card-avatar-fallback">{{ (leaderboard[1].display_name[:1] or '?')|upper }}</div>
                      {% endif %}
                    </div>
                    <div class="podium-card-title">
                      <div class="podium-card-rank">🥈 Runner-up</div>
                      <div class="podium-card-name"><a class="player-link" href="/player/{{ leaderboard[1].user_id }}">{{ leaderboard[1].display_name }}</a></div>
                      <div class="podium-card-subtitle">All-time silver tier · тогтмол өндөр амжилттай contender.</div>
                    </div>
                  </div>
                  <div class="podium-card-money">{{ money(leaderboard[1].total_prize_money) }}</div>
                </div>
                <div class="podium-badges">
                  <span class="podium-badge silver">🥈 Runner-up</span>
                  <span class="podium-badge silver">⚔ Elite Contender</span>
                </div>
                <div class="podium-card-meta">
                  <div class="podium-card-meta-box">
                    <div class="podium-card-meta-label">Wins</div>
                    <div class="podium-card-meta-value">{{ leaderboard[1].championships }}</div>
                  </div>
                  <div class="podium-card-meta-box">
                    <div class="podium-card-meta-label">Podiums</div>
                    <div class="podium-card-meta-value">{{ leaderboard[1].podiums }}</div>
                  </div>
                  <div class="podium-card-meta-box">
                    <div class="podium-card-meta-label">Events</div>
                    <div class="podium-card-meta-value">{{ leaderboard[1].tournaments_played }}</div>
                  </div>
                </div>
              </div>
              <div class="podium-card bronze">
                <div class="podium-card-top">
                  <div class="podium-card-header">
                    <div class="podium-card-avatar">
                      {% if leaderboard[2].avatar_url %}
                        <img src="{{ leaderboard[2].avatar_url }}" alt="{{ leaderboard[2].display_name }}">
                      {% else %}
                        <div class="podium-card-avatar-fallback">{{ (leaderboard[2].display_name[:1] or '?')|upper }}</div>
                      {% endif %}
                    </div>
                    <div class="podium-card-title">
                      <div class="podium-card-rank">🥉 Third Place</div>
                      <div class="podium-card-name"><a class="player-link" href="/player/{{ leaderboard[2].user_id }}">{{ leaderboard[2].display_name }}</a></div>
                      <div class="podium-card-subtitle">Bronze podium tier · pressure үед тогтвортой үр дүн гаргадаг player.</div>
                    </div>
                  </div>
                  <div class="podium-card-money">{{ money(leaderboard[2].total_prize_money) }}</div>
                </div>
                <div class="podium-badges">
                  <span class="podium-badge bronze">🥉 Third Place</span>
                  <span class="podium-badge bronze">🔥 Podium Regular</span>
                </div>
                <div class="podium-card-meta">
                  <div class="podium-card-meta-box">
                    <div class="podium-card-meta-label">Wins</div>
                    <div class="podium-card-meta-value">{{ leaderboard[2].championships }}</div>
                  </div>
                  <div class="podium-card-meta-box">
                    <div class="podium-card-meta-label">Podiums</div>
                    <div class="podium-card-meta-value">{{ leaderboard[2].podiums }}</div>
                  </div>
                  <div class="podium-card-meta-box">
                    <div class="podium-card-meta-label">Events</div>
                    <div class="podium-card-meta-value">{{ leaderboard[2].tournaments_played }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        {% endif %}

        <div class="leaderboard-list">
          {% for row in leaderboard %}
            <div class="leaderboard-row {% if loop.index <= 10 %}top-10{% endif %} {% if loop.index <= 3 %}top-3{% endif %}">
              <div class="leaderboard-rank {% if loop.index == 1 %}top-1{% elif loop.index == 2 %}top-2{% elif loop.index == 3 %}top-3{% endif %}">
                #{{ loop.index }}
              </div>
              <div class="leaderboard-main">
                <div class="leaderboard-main-top">
                  <div class="leaderboard-avatar">
                    {% if row.avatar_url %}
                      <img src="{{ row.avatar_url }}" alt="{{ row.display_name }}">
                    {% else %}
                      <div class="leaderboard-avatar-fallback">{{ (row.display_name[:1] or '?')|upper }}</div>
                    {% endif %}
                  </div>
                  <div class="leaderboard-name-stack">
                    <div class="leaderboard-name"><a class="player-link" href="/player/{{ row.user_id }}">{{ row.display_name }}</a></div>
                    {% if loop.index == 1 %}
                      <div class="leaderboard-ribbon top-1">🏆 Champion Tier</div>
                    {% elif loop.index == 2 %}
                      <div class="leaderboard-ribbon top-2">🥈 Runner-up Tier</div>
                    {% elif loop.index == 3 %}
                      <div class="leaderboard-ribbon top-3">🥉 Third Place Tier</div>
                    {% elif loop.index <= 10 %}
                      <div class="leaderboard-ribbon top-10">⭐ Top 10 Player</div>
                    {% endif %}
                  </div>
                </div>
                <div class="leaderboard-chips">
                  <span class="leaderboard-chip">🏆 {{ row.championships }} Wins</span>
                  <span class="leaderboard-chip">🎖 {{ row.podiums }} Podiums</span>
                  <span class="leaderboard-chip">🎯 {{ row.tournaments_played }} Tournaments</span>
                </div>
              </div>
              <div class="leaderboard-prize">
                <div class="leaderboard-prize-label">Prize Won</div>
                <div class="leaderboard-prize-value">{{ money(row.total_prize_money) }}</div>
              </div>
            </div>
          {% endfor %}
          {% if not leaderboard %}
            <div class="muted">Leaderboard-н мэдээлэл хоосон байна.</div>
          {% endif %}
        </div>
      </section>
    </div>

    <div class="bottom-cta">
      <div>
        <strong>Тэмцээнүүдээ hub дээрээс удирдаж, archive дээрээс түүхээ үзнэ.</strong><br>
        <span class="muted">Live event, standings, prize pool болон player profile бүгд нэг платформ дээр төвлөрнө.</span>
      </div>
      <div class="cta-row" style="margin-top:0;">
        <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
        <a class="page-link" href="/donate">Donate</a>
        <a class="page-link" href="/contact">Contact</a>
      </div>
    </div>

    <div class="footer">Community Tournament Platform · Leaderboard</div>
  </div>
  {{ script|safe }}
</body>
</html>
"""

HISTORY_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Tournament History</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Chess Of Mongolia Archive</div>
          <h1>Tournament History</h1>
          <div class="subtitle">Completed болсон weekly cup-уудын season, champion, podium, prize archive.</div>

          <div class="hero-stats">
            <div class="mini">
              <div class="label">Completed Cups</div>
              <div class="value">{{ total_tournaments }}</div>
            </div>
            <div class="mini">
              <div class="label">Champions</div>
              <div class="value">{{ unique_champions }}</div>
            </div>
            <div class="mini">
              <div class="label">Prize Paid</div>
              <div class="value">{{ money(total_prize_paid) }}</div>
            </div>
            <div class="mini">
              <div class="label">Latest Season</div>
              <div class="value">{{ history[0].season_name if history else '-' }}</div>
            </div>
          </div>
        </div>

        <div class="champion-card">
          <div class="eyebrow">Champion Archive</div>
          {% if latest_champion %}
            <div class="champion-name">
              <a class="player-link" href="/player/{{ latest_champion.user_id }}">{{ latest_champion.display_name }}</a>
            </div>
            <div class="champion-meta">
              Latest Champion · {{ latest_champion.tournament_title }} · {{ latest_champion.season_name }}
            </div>
            <div class="champion-prize">{{ money(latest_champion.display_prize_amount if latest_champion.display_prize_amount is defined else latest_champion.prize_amount) }}</div>
            <div class="champion-sub">Tournament archive дээрх хамгийн сүүлийн champion.</div>
          {% else %}
            <div class="champion-name">Champion archive хоосон байна</div>
            <div class="champion-meta">Completed weekly tournament байхгүй байна.</div>
            <div class="champion-prize">{{ money(0) }}</div>
            <div class="champion-sub">Completed weekly tournament үүсмэгц history энд автоматаар орж ирнэ.</div>
          {% endif %}
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/donate">Donate</a>
      <a href="/contact">Contact</a>
      <a href="/leaderboard">Leaderboard</a>
      <a href="/history">Tournament History</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    <div class="grid">
      <section class="card">
        <div class="section-head">
          <h2>Season Archive</h2>
          <span class="badge blue">{{ history|length }} Tournaments</span>
        </div>

        <div class="history-grid">
          {% for item in history %}
            <article class="history-card">
              <div class="history-head">
                <div>
                  <div class="history-season">{{ item.season_name }}</div>
                  <div class="history-title"><a href="/history/{{ item.tournament_id }}">{{ item.tournament_title }}</a></div>
                </div>
                <span class="badge gold">Weekly</span>
              </div>

              <div class="history-archive-layout">
                <div class="history-feature">
                  {% if item.podium and item.podium[0] %}
                    <div class="history-feature-label">🏆 Champion</div>
                    <div class="history-feature-name">
                      <a class="player-link" href="/player/{{ item.podium[0].user_id }}">{{ item.podium[0].display_name }}</a>
                    </div>
                    <div class="history-feature-meta">
                      {{ item.season_name }} season champion · {{ item.tournament_title }}<br>
                      Final score: {{ item.podium[0].total_points }} pts
                    </div>
                    <div class="history-feature-bottom">
                      <div>
                        <div class="history-feature-money">{{ money(item.podium[0].display_prize_amount if item.podium[0].display_prize_amount is defined else item.podium[0].prize_amount) }}</div>
                        <div class="history-feature-sub">Champion payout</div>
                      </div>
                      <a class="page-link" href="/history/{{ item.tournament_id }}">Open Archive</a>
                    </div>
                  {% else %}
                    <div class="history-feature-label">Archive</div>
                    <div class="history-feature-name">Champion data алга</div>
                    <div class="history-feature-meta">Энэ season-ийн podium data бүрэн хадгалагдаагүй байна.</div>
                    <div class="history-feature-bottom">
                      <div>
                        <div class="history-feature-money">{{ money(0) }}</div>
                        <div class="history-feature-sub">Champion payout</div>
                      </div>
                      <a class="page-link" href="/history/{{ item.tournament_id }}">Open Archive</a>
                    </div>
                  {% endif %}
                </div>

                <div class="history-side-stack">
                  {% for p in item.podium %}
                    <div class="history-podium-row">
                      <div class="history-rank">{{ p.final_rank }}</div>
                      <div class="history-player">
                        <a class="player-link" href="/player/{{ p.user_id }}">{{ p.display_name }}</a>
                      </div>
                      <div class="history-points">{{ p.total_points }} pts</div>
                      <div class="history-prize">{{ money(p.display_prize_amount if p.display_prize_amount is defined else p.prize_amount) }}</div>
                    </div>
                  {% endfor %}
                </div>
              </div>

              <div class="history-foot">
                <span class="badge purple">Top 3 Archived</span>
                <span class="badge green">{{ money(item.podium|sum(attribute='display_prize_amount')) }}</span>
              </div>
            </article>
          {% endfor %}

          {% if not history %}
            <div class="muted">Одоогоор history мэдээлэл алга.</div>
          {% endif %}
        </div>
      </section>
    </div>

    <div class="bottom-cta">
      <div>
        <strong>Тэмцээнүүдээ hub дээрээс удирдаж, archive дээрээс түүхээ үзнэ.</strong><br>
        <span class="muted">Live event, standings, prize pool болон player profile бүгд нэг платформ дээр төвлөрнө.</span>
      </div>
      <div class="cta-row" style="margin-top:0;">
        <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
        <a class="page-link" href="/donate">Donate</a>
        <a class="page-link" href="/contact">Contact</a>
      </div>
    </div>

    <div class="footer">Community Tournament Platform · History</div>
  </div>
  {{ script|safe }}
</body>
</html>
"""

TOURNAMENTS_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Tournaments</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Chess Of Mongolia Hub</div>
          <h1>Tournaments</h1>
          <div class="subtitle">Live, upcoming, completed tournament-уудыг нэг дороос харж, detail/archive page руу орно.</div>

          <div class="hero-stats">
            <div class="mini">
              <div class="label">All Tournaments</div>
              <div class="value">{{ tournaments|length }}</div>
            </div>
            <div class="mini">
              <div class="label">Live</div>
              <div class="value">{{ live_count }}</div>
            </div>
            <div class="mini">
              <div class="label">Completed</div>
              <div class="value">{{ completed_count }}</div>
            </div>
            <div class="mini">
              <div class="label">Prize Tracked</div>
              <div class="value">{{ money(total_prize_pool) }}</div>
            </div>
          </div>
        </div>

        <div class="champion-card">
          <div class="eyebrow">Hub Spotlight</div>
          {% if tournaments %}
            <div class="champion-name">{{ tournaments[0].title }}</div>
            <div class="champion-meta">{{ tournaments[0].season_name or "-" }} · {{ tournaments[0].status }}</div>
            <div class="champion-prize">{{ money(tournaments[0].prize_total) }}</div>
            <div class="champion-sub">Хамгийн сүүлийн tournament card. Detail page руу орж bracket, standings, confirmed players-ийг харж болно.</div>
          {% else %}
            <div class="champion-name">Tournament hub бэлэн байна.</div>
            <div class="champion-meta">Анхны event үүсмэгц энд spotlight card гарч ирнэ.</div>
            <div class="featured-slide-stats">
              <div class="featured-slide-stat">
                <div class="label">Live</div>
                <div class="value">0</div>
              </div>
              <div class="featured-slide-stat">
                <div class="label">Archive</div>
                <div class="value">{{ completed_count }}</div>
              </div>
            </div>
            <div class="champion-sub">Одоогоор бүртгэгдсэн live tournament алга. Create хиймэгц энэ хэсэг автоматаар premium event spotlight болж шинэчлэгдэнэ.</div>
          {% endif %}
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/donate">Donate</a>
      <a href="/contact">Contact</a>
      <a href="/leaderboard">Leaderboard</a>
      <a href="/history">Tournament History</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    <div class="grid">
      <section class="card">
        <div class="section-head">
          <h2>Tournament Hub</h2>
          <span class="badge blue">{{ tournaments|length }} Events</span>
        </div>
        <div class="section-head" style="margin-top:4px">
          <h3 style="margin:0">Live & Open</h3>
          <div style="display:flex; gap:10px; align-items:center">
            <span class="badge green">{{ live_tournaments|length }} Live</span>
          </div>
        </div>

        {% if live_tournaments %}
        <div class="history-grid">
          {% for item in live_tournaments %}
            <article class="history-card">
              <div class="history-head">
                <div>
                  <div class="history-season">{{ item.season_name or "-" }}</div>
                  <div class="history-title"><a href="/history/{{ item.id }}">{{ item.title }}</a></div>
                  <div class="history-submeta">
                    <span>{{ tournament_status_label(item.status) }}</span>
                    <span class="dot"></span>
                    <span>{{ item.type|replace('_', ' ')|title }}</span>
                    <span class="dot"></span>
                    <span>{{ schedule_value(item.start_time) }}</span>
                  </div>
                </div>
                <span class="badge {% if item.status == 'completed' %}purple{% else %}green{% endif %}">{{ tournament_status_label(item.status) }}</span>
              </div>

              <div class="history-stat-grid">
                <div class="history-stat">
                  <div class="history-stat-label">Confirmed</div>
                  <div class="history-stat-value">{{ item.confirmed_count }}/{{ item.max_players }}</div>
                  <div class="history-stat-sub">Confirmed seats</div>
                </div>
                <div class="history-stat">
                  <div class="history-stat-label">Waitlist</div>
                  <div class="history-stat-value">{{ item.waitlist_count }}</div>
                  <div class="history-stat-sub">Pending queue</div>
                </div>
                <div class="history-stat kpi" style="grid-column:1 / -1;">
                  <div class="history-stat-label">Prize Pool</div>
                  <div class="history-stat-value">{{ money(item.prize_total) }}</div>
                  <div class="history-stat-sub">Base pool + tournament sponsor bonus</div>
                </div>
              </div>

              <div class="history-detail-grid">
                <div class="history-detail">
                  <div class="history-detail-label">Champion</div>
                  <div class="history-detail-value">{{ item.champion_name or '-' }}</div>
                </div>
                <div class="history-detail">
                  <div class="history-detail-label">Event Type</div>
                  <div class="history-detail-value">{{ item.type|replace('_', ' ')|title }}</div>
                </div>
              </div>

              <div class="history-foot">
                <span class="badge gold">Weekly</span>
                <a class="page-link" href="/history/{{ item.id }}">View Tournament</a>
              </div>
            </article>
          {% endfor %}

        </div>
        {% else %}
        <div class="premium-empty-card">
          <div class="premium-empty-head">
            <div class="premium-empty-kicker">Live Queue Empty</div>
            <span class="badge blue">Fresh Start</span>
          </div>
          <h3 class="premium-empty-title">Tournament хараахан үүсээгүй байна</h3>
          <div class="premium-empty-copy">Энд шинэ tournament үүсмэгц live card болж харагдана. Тэмцээний confirmed, waitlist, prize pool болон detail entry нэг дороос шууд харагдана.</div>
          <div class="premium-empty-stats">
            <div class="premium-empty-stat">
              <div class="label">All Events</div>
              <div class="value">{{ tournaments|length }}</div>
            </div>
            <div class="premium-empty-stat">
              <div class="label">Live</div>
              <div class="value">0</div>
            </div>
            <div class="premium-empty-stat">
              <div class="label">Status</div>
              <div class="value">Ready</div>
            </div>
          </div>
        </div>
        {% endif %}
      </section>
    </div>

    <div class="bottom-cta">
      <div>
        <strong>Тэмцээнүүдээ hub дээрээс удирдаж, archive дээрээс түүхээ үзнэ.</strong><br>
        <span class="muted">Live event, standings, prize pool болон player profile бүгд нэг платформ дээр төвлөрнө.</span>
      </div>
      <div class="cta-row" style="margin-top:0;">
        <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
        <a class="page-link" href="/donate">Donate</a>
        <a class="page-link" href="/contact">Contact</a>
      </div>
    </div>

    <div class="footer">Community Tournament Platform · Tournament Hub</div>
  </div>
  {{ script|safe }}
</body>
</html>
"""

SEASON_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>{{ tournament.title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Season Archive</div>
          <h1>{{ tournament.title }}</h1>
          <div class="subtitle">{{ tournament.season_name }} archive. Confirmed player list, bracket stages, final standings бүгд энэ page дээр хадгалагдана.</div>

          <div class="status-row">
            <span class="badge gold">{{ tournament.season_name }}</span>
            <span class="badge blue">{{ tournament_status_label(tournament.status) }}</span>
            <span class="badge green">{{ confirmed_players|length }} Confirmed</span>
            <span class="badge purple">{{ stages|length }} Stages</span>
          </div>

          <div class="hero-stats">
            <div class="mini">
              <div class="label">Confirmed Players</div>
              <div class="value">{{ confirmed_players|length }}</div>
            </div>
            <div class="mini">
              <div class="label">Bracket Groups</div>
              <div class="value">{{ stages|length }}</div>
            </div>
            <div class="mini">
              <div class="label">Finalists</div>
              <div class="value">{{ final_standings|length }}</div>
            </div>
            <div class="mini">
              <div class="label">Champion</div>
              <div class="value">
                {% if final_standings %}
                  <a class="player-link" href="/player/{{ final_standings[0].user_id }}">{{ final_standings[0].display_name }}</a>
                {% else %}
                  -
                {% endif %}
              </div>
            </div>
          </div>
        </div>

        <div class="champion-card">
          <div class="eyebrow">Final Archive</div>
          {% if final_standings %}
            <div class="champion-name">
              <a class="player-link" href="/player/{{ final_standings[0].user_id }}">{{ final_standings[0].display_name }}</a>
            </div>
            <div class="champion-meta">Season Champion · {{ tournament.season_name }}</div>
            <div class="champion-prize">{{ money(final_standings[0].display_prize_amount if final_standings[0].display_prize_amount is defined else final_standings[0].prize_amount) }}</div>
            <div class="champion-sub">Bracket archive доторх эцсийн дүнгээр champion тодорсон байна.</div>
          {% else %}
            <div class="champion-name">Final standings алга</div>
            <div class="champion-meta">Энэ season-ийн final data бүртгэгдээгүй байна.</div>
            <div class="champion-prize">-</div>
            <div class="champion-sub">Bracket үүссэн бол доорх archive хэсгээс stage мэдээллээ харж болно.</div>
          {% endif %}
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/donate">Donate</a>
      <a href="/contact">Contact</a>
      <a href="/leaderboard">Leaderboard</a>
      <a href="/history">Tournament History</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    <div class="grid">
      {% if tournament.status == "registration_open" %}
      <section class="card cols-6">
        <div class="section-head">
          <h2>Site-р Бүртгүүлэх</h2>
          <span class="badge green">Open</span>
        </div>
        <div class="prize-note" style="margin-bottom:14px">
          Энэ tournament дээрх site registration эндээс явагдана. Discord review card автоматаар очиж, баталгаажсан үед статус нь шууд шинэчлэгдэнэ.
        </div>

        {% if oauth_enabled %}
          {% if discord_user %}
          <div class="row" style="margin-bottom:12px">
            <div>
              <strong>{{ discord_user.display_name }}</strong><br>
              <span class="muted">Discord холбогдсон байна</span>
            </div>
            <div class="cta-row" style="margin-top:0">
              <a class="page-link" href="/player/{{ discord_user.id }}">Profile</a>
              <a class="page-link" href="/logout">Гарах</a>
            </div>
          </div>
          {% if current_user_registration %}
          <div class="list" style="margin-bottom:12px">
            <div class="row">
              <div>Бүртгэлийн төлөв</div>
              <div>
                <span class="status-pill {{ current_user_registration.status|lower }}">
                  {{ registration_status_label(current_user_registration.status) }}
                </span>
              </div>
            </div>
            <div class="row">
              <div>Хүсэлтийн эх сурвалж</div>
              <div>
                <span class="status-pill source">
                  {{ registration_source_label(current_user_registration.source or 'web') }}
                </span>
              </div>
            </div>
            <div class="row">
              <div>Бүртгэлийн дараалал</div>
              <div>Дугаар {{ current_user_registration.register_order }}</div>
            </div>
            <div class="row">
              <div>Төлбөрийн төлөв</div>
              <div>{{ payment_status_label(current_user_registration.payment_status) }}</div>
            </div>
          </div>
          {% else %}
          <form class="form-stack" method="post" action="/register">
            <input type="hidden" name="tournament_id" value="{{ tournament.id }}">
            <div class="field">
              <label for="season_phone_number">Утасны дугаар</label>
              <input id="season_phone_number" name="phone_number" inputmode="tel" placeholder="Жишээ: 99112233" value="{{ current_user_profile.phone_number if current_user_profile and current_user_profile.phone_number else '' }}" required>
            </div>
            <div class="field">
              <label for="season_bank_account">Банкны данс</label>
              <input id="season_bank_account" name="bank_account" placeholder="Жишээ: 540012345678" value="{{ current_user_profile.bank_account if current_user_profile and current_user_profile.bank_account else '' }}" required>
            </div>
            <button class="button-primary" type="submit">Бүртгүүлэх хүсэлт илгээх</button>
          </form>
          {% endif %}
          {% else %}
          <div class="cta-row" style="margin-top:0">
            <a class="page-link" href="/login/discord">Discord-оор нэвтрэх</a>
          </div>
          {% endif %}
        {% else %}
        <form class="form-stack" method="post" action="/register">
          <input type="hidden" name="tournament_id" value="{{ tournament.id }}">
          <div class="field">
            <label for="season_discord_user_id">Discord User ID</label>
            <input id="season_discord_user_id" name="discord_user_id" inputmode="numeric" placeholder="Жишээ: 566213018136870912" required>
          </div>
          <div class="field">
            <label for="season_display_name">Discord Нэр</label>
            <input id="season_display_name" name="display_name" placeholder="Жишээ: ShiJEE" required>
          </div>
          <div class="field">
            <label for="season_phone_number_manual">Утасны дугаар</label>
            <input id="season_phone_number_manual" name="phone_number" inputmode="tel" placeholder="Жишээ: 99112233" required>
          </div>
          <div class="field">
            <label for="season_bank_account_manual">Банкны данс</label>
            <input id="season_bank_account_manual" name="bank_account" placeholder="Жишээ: 540012345678" required>
          </div>
          <button class="button-primary" type="submit">Бүртгүүлэх хүсэлт илгээх</button>
        </form>
        {% endif %}
        {% if register_notice %}
        <div class="notice {{ 'ok' if register_ok else 'error' }}">{{ register_notice }}</div>
        {% endif %}
      </section>

      <section class="card cols-6">
        <div class="section-head">
          <h2>Discord-оор Бүртгүүлэх</h2>
          <span class="badge blue">Discord</span>
        </div>
        <div class="prize-note" style="margin-bottom:14px">
          Discord доторх weekly registration flow ашигламаар байвал эндээс шууд server рүү орж, бүртгэлийн channel дээрх button эсвэл command-ийг ашиглаж болно.
        </div>

        <div class="list" style="margin-bottom:16px">
          <div class="row">
            <div>Алхам 1</div>
            <div>Discord server руу орно</div>
          </div>
          <div class="row">
            <div>Алхам 2</div>
            <div>Бүртгэлийн channel дээрх registration button-ийг дарна</div>
          </div>
          <div class="row">
            <div>Алхам 3</div>
            <div>Payment review баталгаажсаны дараа confirmed болно</div>
          </div>
          <div class="row">
            <div>Хурдан зам</div>
            <div>`/join` эсвэл registration button</div>
          </div>
        </div>

        <div class="cta-row">
          <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord нээх</a>
          {% if discord_user %}
          <a class="page-link" href="/player/{{ discord_user.id }}">Profile</a>
          {% endif %}
        </div>
      </section>

      <section class="card cols-12" id="payment-info">
        <div class="section-head">
          <h2>Төлбөрийн мэдээлэл</h2>
          <span class="badge gold">{{ money(tournament.entry_fee or 0) }}</span>
        </div>
        <div class="prize-note" style="margin-bottom:14px">
          Бүртгэлийн хураамжаа доорх данс руу шилжүүлээд, хүсэлтээ илгээнэ. Admin гүйлгээг шалгасны дараа таны бүртгэл confirmed болно.
        </div>

        <div class="list">
          <div class="row"><div>Бүртгэлийн хураамж</div><div>{{ money(tournament.entry_fee or 0) }}</div></div>
          <div class="row"><div>Банк</div><div>{{ payment_bank_name }}</div></div>
          <div class="row">
            <div>Дансны дугаар</div>
            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; justify-content:flex-end;">
              <span>{{ payment_account_no }}</span>
              <button class="copy-btn" onclick='copyText({{ payment_account_no|tojson }})'>Copy</button>
            </div>
          </div>
          <div class="row"><div>Данс эзэмшигч</div><div>{{ payment_owner_name }}</div></div>
          <div class="row">
            <div>Гүйлгээний утга</div>
            <div style="text-align:right">
              Discord нэр эсвэл user ID<br>
              <span class="muted" style="font-size:12px">`💬・ᴄᴏᴍᴍᴜɴɪᴛʏ-ᴄʜᴀᴛ` дээр `.me` командаар авсан user ID</span><br>
              <span class="muted" style="font-size:12px">Жишээ: ShiJEE эсвэл 566213018136870912</span>
            </div>
          </div>
        </div>

        <div class="prize-note">
          {{ payment_note }}
        </div>
      </section>
      {% endif %}

      <section class="card">
        <div class="section-head">
          <h2>Bracket Archive</h2>
          <span class="badge blue">{{ archive_stages|length }} Stages</span>
        </div>

        <div class="bracket-visual">
          <div class="bracket-column">
            {% for stage in zone_stages[:2] %}
            <div class="bracket-node compact">
              <div class="bracket-node-label">{{ stage.title }}</div>
              <div class="bracket-node-title">{{ stage.players|length }} Players</div>
              <div class="bracket-node-meta">{{ stage.players|length }} Players · {{ stage.stage_status|replace("_", " ")|title }}</div>
              {% if stage.players %}
              <div class="bracket-player-list">
                {% for player in stage.players %}
                <div class="bracket-player">
                  <a href="/player/{{ player.user_id }}">{{ player.display_name }}</a>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span class="bracket-player-points">{{ player.extra or '-' }}</span>
                    <span class="bracket-player-rank">#{{ player.slot_no }}</span>
                  </div>
                </div>
                {% endfor %}
              </div>
              {% endif %}
            </div>
            {% endfor %}
            {% if zone_stages|length < 2 %}
            <div class="bracket-node compact empty">
              <div class="bracket-node-label">Zone A</div>
              <div class="bracket-node-title">8 Players</div>
              <div class="bracket-node-meta">Bracket хүлээгдэж байна</div>
              <div class="bracket-empty-list">
                {% for _ in range(8) %}<div class="bracket-empty-slot"></div>{% endfor %}
              </div>
            </div>
            <div class="bracket-node compact empty">
              <div class="bracket-node-label">Zone B</div>
              <div class="bracket-node-title">8 Players</div>
              <div class="bracket-node-meta">Bracket хүлээгдэж байна</div>
              <div class="bracket-empty-list">
                {% for _ in range(8) %}<div class="bracket-empty-slot"></div>{% endfor %}
              </div>
            </div>
            {% endif %}
          </div>

          <div class="bracket-connector">›</div>

          <div class="bracket-column">
            {% for stage in semi_stages[:1] %}
            <div class="bracket-node compact">
              <div class="bracket-node-label">{{ stage.title }}</div>
              <div class="bracket-node-title">{{ stage.players|length }} Players</div>
              <div class="bracket-node-meta">{{ stage.players|length }} Players · {{ stage.stage_status|replace("_", " ")|title }}</div>
              {% if stage.players %}
              <div class="bracket-player-list">
                {% for player in stage.players %}
                <div class="bracket-player">
                  <a href="/player/{{ player.user_id }}">{{ player.display_name }}</a>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span class="bracket-player-points">{{ player.extra or '-' }}</span>
                    <span class="bracket-player-rank">#{{ player.slot_no }}</span>
                  </div>
                </div>
                {% endfor %}
              </div>
              {% endif %}
            </div>
            {% endfor %}
            {% if semi_stages|length < 1 %}
            <div class="bracket-node compact empty">
              <div class="bracket-node-label">Semi A</div>
              <div class="bracket-node-title">8 Players</div>
              <div class="bracket-node-meta">Bracket хүлээгдэж байна</div>
              <div class="bracket-empty-list">
                {% for _ in range(8) %}<div class="bracket-empty-slot"></div>{% endfor %}
              </div>
            </div>
            {% endif %}
          </div>

          <div class="bracket-column center">
            <div class="bracket-node final">
              <div class="bracket-crown">🏆</div>
              <div class="bracket-node-label">Grand Final</div>
              <div class="bracket-node-title">
                {% if grand_final and grand_final.players %}
                {{ grand_final.players|length }} Finalists
                {% elif final_standings %}
                {{ final_standings[0].display_name }}
                {% else %}
                TBD
                {% endif %}
              </div>
              <div class="bracket-node-meta">
                {% if grand_final %}
                {{ grand_final.players|length }} Finalists · {{ grand_final.stage_status|replace("_", " ")|title }}
                {% else %}
                Final bracket хүлээгдэж байна
                {% endif %}
              </div>
              {% if grand_final_display_players %}
              <div class="bracket-player-list">
                {% for player in grand_final_display_players %}
                <div class="bracket-player">
                  <a href="/player/{{ player.user_id }}">{{ player.display_name }}</a>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span class="bracket-player-points">{{ player.stage_points or player.total_points or 0 }} pts</span>
                    <span class="bracket-player-rank">#{{ loop.index }}</span>
                  </div>
                </div>
                {% endfor %}
              </div>
              {% elif final_standings %}
              <div class="bracket-player-list">
                {% for player in final_standings[:8] %}
                <div class="bracket-player">
                  <a href="/player/{{ player.user_id }}">{{ player.display_name }}</a>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span class="bracket-player-points">{{ player.total_points }} pts</span>
                    <span class="bracket-player-rank">#{{ player.final_position }}</span>
                  </div>
                </div>
                {% endfor %}
              </div>
              {% else %}
              <div class="bracket-empty-list">
                {% for _ in range(8) %}<div class="bracket-empty-slot"></div>{% endfor %}
              </div>
              {% endif %}
            </div>
          </div>

          <div class="bracket-column">
            {% for stage in semi_stages[1:2] %}
            <div class="bracket-node compact">
              <div class="bracket-node-label">{{ stage.title }}</div>
              <div class="bracket-node-title">{{ stage.players|length }} Players</div>
              <div class="bracket-node-meta">{{ stage.players|length }} Players · {{ stage.stage_status|replace("_", " ")|title }}</div>
              {% if stage.players %}
              <div class="bracket-player-list">
                {% for player in stage.players %}
                <div class="bracket-player">
                  <a href="/player/{{ player.user_id }}">{{ player.display_name }}</a>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span class="bracket-player-points">{{ player.extra or '-' }}</span>
                    <span class="bracket-player-rank">#{{ player.slot_no }}</span>
                  </div>
                </div>
                {% endfor %}
              </div>
              {% endif %}
            </div>
            {% endfor %}
            {% if semi_stages|length < 2 %}
            <div class="bracket-node compact empty">
              <div class="bracket-node-label">Semi B</div>
              <div class="bracket-node-title">8 Players</div>
              <div class="bracket-node-meta">Bracket хүлээгдэж байна</div>
              <div class="bracket-empty-list">
                {% for _ in range(8) %}<div class="bracket-empty-slot"></div>{% endfor %}
              </div>
            </div>
            {% endif %}
          </div>

          <div class="bracket-connector">‹</div>

          <div class="bracket-column">
            {% for stage in zone_stages[2:4] %}
            <div class="bracket-node compact">
              <div class="bracket-node-label">{{ stage.title }}</div>
              <div class="bracket-node-title">{{ stage.players|length }} Players</div>
              <div class="bracket-node-meta">{{ stage.players|length }} Players · {{ stage.stage_status|replace("_", " ")|title }}</div>
              {% if stage.players %}
              <div class="bracket-player-list">
                {% for player in stage.players %}
                <div class="bracket-player">
                  <a href="/player/{{ player.user_id }}">{{ player.display_name }}</a>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span class="bracket-player-points">{{ player.extra or '-' }}</span>
                    <span class="bracket-player-rank">#{{ player.slot_no }}</span>
                  </div>
                </div>
                {% endfor %}
              </div>
              {% endif %}
            </div>
            {% endfor %}
            {% if zone_stages|length < 4 %}
            <div class="bracket-node compact empty">
              <div class="bracket-node-label">Zone C</div>
              <div class="bracket-node-title">8 Players</div>
              <div class="bracket-node-meta">Bracket хүлээгдэж байна</div>
              <div class="bracket-empty-list">
                {% for _ in range(8) %}<div class="bracket-empty-slot"></div>{% endfor %}
              </div>
            </div>
            <div class="bracket-node compact empty">
              <div class="bracket-node-label">Zone D</div>
              <div class="bracket-node-title">8 Players</div>
              <div class="bracket-node-meta">Bracket хүлээгдэж байна</div>
              <div class="bracket-empty-list">
                {% for _ in range(8) %}<div class="bracket-empty-slot"></div>{% endfor %}
              </div>
            </div>
            {% endif %}
          </div>
        </div>
      </section>

      <section class="card">
        <div class="section-head">
          <h2>Current Event Dashboard</h2>
          <span class="badge blue">4 Cards</span>
        </div>

        <div class="hub-ops-grid">
          <section class="card" id="current-event">
            <div class="section-head">
              <h2>Current Event</h2>
              <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center">
                <span class="badge green">{{ confirmed_players|length }} Confirmed</span>
                <span class="badge blue">Auto Advance ON</span>
              </div>
            </div>
            <div class="list" style="margin-bottom:16px">
              <div class="row"><div>Тэмцээн</div><div><a class="player-link" href="/history/{{ tournament.id }}">{{ tournament.title }}</a></div></div>
              <div class="row"><div>Статус</div><div>{{ tournament_status_label(tournament.status) }}</div></div>
              <div class="row"><div>Одоогийн шат</div><div>{{ tournament_status_label(tournament.status) if tournament.status != "registration_open" else "Registration" }}</div></div>
              <div class="row"><div>Бүртгэлийн хураамж</div><div>{{ money(tournament.entry_fee or 0) }}</div></div>
              <div class="row"><div>Confirmed</div><div>{{ confirmed_players|length }}/{{ tournament.max_players }}</div></div>
              <div class="row"><div>Stage</div><div>{{ archive_stages|length }}</div></div>
            </div>

            <div class="prize-note" style="margin-bottom:14px">
              Тухайн tournament-ийн бүртгэл, bracket archive, confirmed players болон waiting players энэ page дээр төвлөрч харагдана. Zone draw үүссэний дараа semi болон grand final нь result бүрэн ормогц автоматаар үүснэ.
            </div>

              <div class="cta-row">
                <a class="page-link" href="#confirmed-players">Confirmed Players</a>
                <a class="page-link" href="#waiting-players">Waiting Players</a>
              </div>
          </section>

          <section class="card" id="prize">
            <div class="section-head">
              <h2>Шагналын сан</h2>
              <span class="badge gold">Tournament Sponsor Bonus {{ money(sponsor_total) }}</span>
            </div>

            <div class="prize-grid">
              <div class="prize-item">
                <div class="label">Base Prize Pool</div>
                <div class="amount">{{ money(prize_display.base_pool) }}</div>
                <div class="hint gold">+ {{ money(sponsor_total) }}</div>
              </div>
              <div class="prize-item">
                <div class="label">🥇 1-р байр</div>
                <div class="amount">{{ money(prize_display.prize_1_total) }}</div>
                <div class="hint">Base {{ money(prize_display.prize_1_base) }} + Sponsor {{ money(prize_display.prize_1_sponsor) }}</div>
              </div>
              <div class="prize-item">
                <div class="label">🥈 2-р байр</div>
                <div class="amount">{{ money(prize_display.prize_2_total) }}</div>
                <div class="hint">Base {{ money(prize_display.prize_2_base) }} + Sponsor {{ money(prize_display.prize_2_sponsor) }}</div>
              </div>
              <div class="prize-item">
                <div class="label">🥉 3-р байр</div>
                <div class="amount">{{ money(prize_display.prize_3_total) }}</div>
                <div class="hint">Base {{ money(prize_display.prize_3_base) }} + Sponsor {{ money(prize_display.prize_3_sponsor) }}</div>
              </div>
            </div>

            <div class="prize-summary">
              <div class="row">
                <div>Base Prize Pool</div>
                <div>{{ money(prize_display.base_pool) }}</div>
              </div>
              <div class="row">
                <div>Tournament Sponsor Bonus</div>
                <div>+ {{ money(prize_display.sponsor_total) }}</div>
              </div>
              <div class="row">
                <div><strong>Current Total Pool</strong></div>
                <div><strong>{{ money(prize_display.current_total_pool) }}</strong></div>
              </div>
              <div class="row">
                <div>Platform Operations Fee (10%)</div>
                <div>- {{ money(prize_display.organizer_fee) }}</div>
              </div>
              <div class="row">
                <div><strong>Final Prize Pool</strong></div>
                <div><strong>{{ money(prize_display.final_pool) }}</strong></div>
              </div>
            </div>

            <div class="prize-note">
              Tournament sponsor нь base prize pool дээр нэмэгдэнэ. Current Total Pool-оос 10% platform operations fee хасагдаж, энэ нь server hosting, domain, Discord bot, live web platform болон tournament management зардалд ашиглагдана. Үлдсэн Final Prize Pool нь 1/2/3 байрны харьцаагаар бодогдоно.
            </div>
          </section>

          <section class="card" id="rules">
            <div class="section-head">
              <h2>Журам</h2>
              <span class="badge blue">Official</span>
            </div>
            <div class="rules-box">{{ rules_text }}</div>
          </section>

          <section class="card" id="sponsors">
            <div class="section-head">
              <h2>Tournament Sponsors</h2>
              <span class="badge green">{{ money(sponsor_total) }}</span>
            </div>
            <div class="prize-note" style="margin-bottom:14px;">
              Энд харагдаж байгаа sponsor-ууд нь тухайн tournament-ийн prize pool дээр орж байгаа дүн.
            </div>
            <div class="sponsor-wall">
              {% if sponsors %}
                {% set top_sponsor = sponsors[0] %}
                <div class="sponsor-hero">
                  <div class="sponsor-hero-main">
                    <div class="sponsor-hero-avatar">
                      {% if top_sponsor.image_url %}
                        <img src="{{ top_sponsor.image_url }}" alt="{{ top_sponsor.display_name }}">
                      {% else %}
                        <div class="mini-avatar-fallback">{{ top_sponsor.display_name[:1] }}</div>
                      {% endif %}
                    </div>
                    <div class="sponsor-hero-copy">
                      <div class="sponsor-hero-label">Top Sponsor</div>
                      <div class="sponsor-hero-name">
                        {% if top_sponsor.sponsor_user_id %}
                          <a class="player-link" href="/player/{{ top_sponsor.sponsor_user_id }}">{{ top_sponsor.display_name }}</a>
                        {% else %}
                          {{ top_sponsor.display_name }}
                        {% endif %}
                      </div>
                      <div class="sponsor-hero-sub">
                        Энэ tournament-ийн хамгийн өндөр sponsor дэмжлэг. Prize pool-д шууд нэмэгдэж байгаа гол хувь нэмэр.
                      </div>
                      <div class="sponsor-hero-badges">
                        <span class="micro-chip support">Official Sponsor</span>
                        <span class="micro-chip legend">{{ ((top_sponsor.amount / sponsor_total) * 100)|round(0)|int }}% Pool Share</span>
                        {% if top_sponsor.note %}
                          <span class="micro-chip blue">{{ top_sponsor.note }}</span>
                        {% endif %}
                      </div>
                    </div>
                  </div>
                  <div class="sponsor-hero-right">
                    <div class="sponsor-hero-amount">{{ money(top_sponsor.amount) }}</div>
                    <div class="sponsor-hero-share">Top Rank · #1</div>
                  </div>
                </div>

                {% if sponsors|length > 1 %}
                  <div class="sponsor-stack">
                    {% for sponsor in sponsors[1:] %}
                      <div class="sponsor-rank-row">
                        <div class="sponsor-rank-left">
                          <span class="sponsor-rank-pill">#{{ loop.index + 1 }}</span>
                          <div class="mini-avatar">
                            {% if sponsor.image_url %}
                              <img src="{{ sponsor.image_url }}" alt="{{ sponsor.display_name }}">
                            {% else %}
                              <div class="mini-avatar-fallback">{{ sponsor.display_name[:1] }}</div>
                            {% endif %}
                          </div>
                          <div class="sponsor-rank-copy">
                            <div class="sponsor-rank-name">
                              <strong>{% if sponsor.sponsor_user_id %}<a class="player-link" href="/player/{{ sponsor.sponsor_user_id }}">{{ sponsor.display_name }}</a>{% else %}{{ sponsor.display_name }}{% endif %}</strong>
                              <span class="micro-chip support">Sponsor</span>
                            </div>
                            <div class="sponsor-rank-meta">
                              {% if sponsor.note %}
                                {{ sponsor.note }}
                              {% else %}
                                Prize pool-д орсон sponsor contribution
                              {% endif %}
                            </div>
                          </div>
                        </div>
                        <div class="sponsor-rank-right">
                          <div class="sponsor-rank-amount">{{ money(sponsor.amount) }}</div>
                          <span class="micro-chip blue">{{ ((sponsor.amount / sponsor_total) * 100)|round(0)|int }}%</span>
                        </div>
                      </div>
                    {% endfor %}
                  </div>
                {% endif %}
              {% else %}
                <div class="sponsor-empty">Одоогоор tournament sponsor алга. First sponsor орж ирэхэд энэ хэсэг premium sponsor wall болж харагдана.</div>
              {% endif %}
            </div>
          </section>
        </div>
      </section>

      <section class="card cols-6">
        <div id="confirmed-players"></div>
        <div class="section-head">
          <h2>Confirmed Players</h2>
          <span class="badge green">{{ confirmed_players|length }} Players</span>
        </div>

        <div class="list">
          {% for player in confirmed_players %}
            <div class="row">
              <div class="participant-main">
                <div class="mini-avatar">
                  {% if player.avatar_url %}
                    <img src="{{ player.avatar_url }}" alt="{{ player.display_name }}">
                  {% else %}
                    <div class="mini-avatar-fallback">{{ player.display_name[:1] }}</div>
                  {% endif %}
                </div>
                <div class="participant-copy">
                  <div class="participant-name">
                    <a class="player-link" href="/player/{{ player.user_id }}">{{ player.display_name }}</a>
                  </div>
                  <div class="participant-sub">
                    <span class="micro-chip">Order #{{ player.register_order }}</span>
                    {% if player.championships|int > 0 %}
                      <span class="micro-chip legend">Champion</span>
                    {% endif %}
                    {% if player.donor_tier %}
                      <span class="micro-chip support">{{ player.donor_tier }}</span>
                    {% endif %}
                    {% if player.sponsor_tier %}
                      <span class="micro-chip elite">{{ player.sponsor_tier }}</span>
                    {% endif %}
                  </div>
                </div>
              </div>
              {% if admin_panel_enabled %}
              <div class="cta-row" style="margin-top:0; justify-content:flex-end;">
                <form method="post" action="/admin/tournaments/{{ tournament.id }}/entries/{{ player.id }}/unconfirm" style="display:inline-flex; margin:0;">
                  <input type="hidden" name="key" value="{{ admin_panel_key }}">
                  <button class="page-link" type="submit">Waiting руу буцаах</button>
                </form>
                <form method="post" action="/admin/tournaments/{{ tournament.id }}/entries/{{ player.id }}/remove" style="display:inline-flex; margin:0;">
                  <input type="hidden" name="key" value="{{ admin_panel_key }}">
                  <button class="page-link" type="submit">Хасах</button>
                </form>
              </div>
              {% endif %}
            </div>
          {% endfor %}

          {% if not confirmed_players %}
            <div class="muted">Confirmed player archive алга.</div>
          {% endif %}
        </div>
      </section>

      <section class="card cols-6">
        <div id="waiting-players"></div>
        <div class="section-head">
          <h2>Waiting Players</h2>
          <span class="badge gold">{{ waiting_players|length }} Players</span>
        </div>

        <div class="list">
          {% for player in waiting_players %}
            <div class="row">
              <div class="participant-main">
                <div class="mini-avatar">
                  {% if player.avatar_url %}
                    <img src="{{ player.avatar_url }}" alt="{{ player.display_name }}">
                  {% else %}
                    <div class="mini-avatar-fallback">{{ player.display_name[:1] }}</div>
                  {% endif %}
                </div>
                <div class="participant-copy">
                  <div class="participant-name">
                    <a class="player-link" href="/player/{{ player.user_id }}">{{ player.display_name }}</a>
                  </div>
                  <div class="participant-sub">
                    <span class="micro-chip">Order #{{ player.register_order }}</span>
                    <span class="micro-chip {% if player.status == 'waitlist' %}bronze{% else %}support{% endif %}">
                      {{ registration_status_label(player.status) }}
                    </span>
                  </div>
                </div>
              </div>
              {% if admin_panel_enabled %}
              <div class="cta-row" style="margin-top:0; justify-content:flex-end;">
                <form method="post" action="/admin/tournaments/{{ tournament.id }}/entries/{{ player.id }}/confirm" style="display:inline-flex; margin:0;">
                  <input type="hidden" name="key" value="{{ admin_panel_key }}">
                  <button class="button-primary" type="submit">Confirm</button>
                </form>
                <form method="post" action="/admin/tournaments/{{ tournament.id }}/entries/{{ player.id }}/remove" style="display:inline-flex; margin:0;">
                  <input type="hidden" name="key" value="{{ admin_panel_key }}">
                  <button class="page-link" type="submit">Хасах</button>
                </form>
              </div>
              {% endif %}
            </div>
          {% endfor %}

          {% if not waiting_players %}
            <div class="muted">Одоогоор waiting player алга.</div>
          {% endif %}
        </div>
      </section>
    </div>

    <div class="bottom-cta">
      <div>
        <strong>Тэмцээнүүдээ hub дээрээс удирдаж, archive дээрээс түүхээ үзнэ.</strong><br>
        <span class="muted">Live event, standings, prize pool болон player profile бүгд нэг платформ дээр төвлөрнө.</span>
      </div>
      <div class="cta-row" style="margin-top:0;">
        <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
        <a class="page-link" href="/donate">Donate</a>
        <a class="page-link" href="/contact">Contact</a>
      </div>
    </div>

    <div class="footer">Community Tournament Platform · Season Archive</div>
  </div>
  {{ script|safe }}
</body>
</html>
"""

PLAYER_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>{{ profile.display_name }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Player Profile</div>
          <h1>{{ profile.display_name }}</h1>
          <div class="subtitle">All-time weekly stats болон tournament history.</div>
          <div class="identity-row">
            <div class="avatar-shell">
              {% if profile.avatar_url %}
              <img class="avatar-img" src="{{ profile.avatar_url }}" alt="{{ profile.display_name }}">
              {% else %}
              <div class="avatar-fallback">
                {{ (profile.display_name[:1] or '?')|upper }}
              </div>
              {% endif %}
              <div class="status-dot"></div>
            </div>

            <div class="identity-copy">
              <div class="badge-row">
                {% for donor_role in donor_support_roles %}
                <span class="rank-chip support">{{ donor_role }}</span>
                {% endfor %}
                {% for sponsor_role in sponsor_support_roles %}
                <span class="rank-chip elite">{{ sponsor_role }}</span>
                {% endfor %}
                {% if profile.championships and profile.championships > 0 %}
                <span class="rank-chip legend">Champion</span>
                {% elif profile.runner_ups and profile.runner_ups > 0 %}
                <span class="rank-chip runner">Runner-up</span>
                {% elif profile.third_places and profile.third_places > 0 %}
                <span class="rank-chip bronze">Third Place</span>
                {% endif %}
                {% if not donor_support_roles
                      and not sponsor_support_roles
                      and (not profile.championships or profile.championships == 0)
                      and (not profile.runner_ups or profile.runner_ups == 0)
                      and (not profile.third_places or profile.third_places == 0) %}
                <span class="rank-chip default">Unranked</span>
                {% endif %}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="hero-stats">
        <div class="mini">
          <div class="label">Championships</div>
          <div class="value">{{ profile.championships or 0 }}</div>
        </div>
        <div class="mini">
          <div class="label">Podiums</div>
          <div class="value">{{ profile.podiums or 0 }}</div>
        </div>
        <div class="mini">
          <div class="label">Tournaments</div>
          <div class="value">{{ profile.tournaments_played or 0 }}</div>
        </div>
        <div class="mini">
          <div class="label">Prize Won</div>
          <div class="value">{{ money(profile.total_prize_money or 0) }}</div>
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/admin?key={{ admin_panel_key }}">Admin</a>
      <a href="/leaderboard">Leaderboard</a>
      <a href="/history">Tournament History</a>
      <a href="/donate">Donate</a>
      <a href="/contact">Contact</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    <div class="grid">
      <section class="card cols-4">
        <div class="section-head">
          <h2>Товч мэдээлэл</h2>
          <span class="badge gold">Stats</span>
        </div>
        <div class="list">
          <div class="row"><div>Weekly Played</div><div>{{ profile.weekly_played or 0 }}</div></div>
          <div class="row"><div>Championships</div><div>{{ profile.championships or 0 }}</div></div>
          <div class="row"><div>Runner-ups</div><div>{{ profile.runner_ups or 0 }}</div></div>
          <div class="row"><div>Third Places</div><div>{{ profile.third_places or 0 }}</div></div>
          <div class="row"><div>Podiums</div><div>{{ profile.podiums or 0 }}</div></div>
          <div class="row"><div>Total Prize</div><div>{{ money(profile.total_prize_money or 0) }}</div></div>
        </div>
      </section>

      <section class="card cols-4">
        <div class="section-head">
          <h2>Support Status</h2>
          <span class="badge blue">{{ support_badge_label(support) }}</span>
        </div>
        <div class="list">
          {% if donor_support_roles %}
            {% for donor_role in donor_support_roles %}
            <div class="row support-donor"><div>Donator</div><div>{{ donor_role }}</div></div>
            {% endfor %}
          {% else %}
          <div class="row support-donor"><div>Donator</div><div>-</div></div>
          {% endif %}
          <div class="row support-expiry"><div>Donate Expiry</div><div>{{ support.donor_expires_at if support and support.donor_expires_at else '-' }}</div></div>
          {% if sponsor_support_roles %}
            {% for sponsor_role in sponsor_support_roles %}
            <div class="row support-sponsor"><div>Sponsor</div><div>{{ sponsor_role }}</div></div>
            {% endfor %}
          {% else %}
          <div class="row support-sponsor"><div>Sponsor</div><div>-</div></div>
          {% endif %}
          <div class="row support-expiry"><div>Sponsor Expiry</div><div>{{ support.sponsor_expires_at if support and support.sponsor_expires_at else '-' }}</div></div>
        </div>
      </section>

      <section class="card cols-4">
        <div class="section-head">
          <h2>Contact Info</h2>
          <span class="badge gold">Profile</span>
        </div>
        <div class="list">
          <div class="row"><div>Phone</div><div>{{ profile.phone_number or '-' }}</div></div>
          <div class="row"><div>Bank Account</div><div>{{ profile.bank_account or '-' }}</div></div>
        </div>
      </section>

      <section class="card cols-8">
        <div class="section-head">
          <h2>Tournament History</h2>
          <span class="badge blue">{{ history|length }} Results</span>
        </div>
        <div class="list">
          {% for row in history %}
            <div class="row">
              <div>
                <strong><a class="player-link" href="/history/{{ row.tournament_id }}">{{ row.tournament_title }}</a></strong><br>
                <span class="muted">
                  <a class="player-link" href="/history/{{ row.tournament_id }}">{{ row.season_name }}</a>
                  {% if row.has_result %}
                    · Rank {{ row.final_rank }}
                  {% else %}
                    · Participated
                  {% endif %}
                </span>
              </div>
              <div>
                {% if row.has_result %}
                  <div>{{ money(row.prize_amount) }}</div>
                  <div class="muted">Pts {{ row.total_points }}</div>
                {% else %}
                  <div>-</div>
                  <div class="muted">No final placement</div>
                {% endif %}
              </div>
            </div>
          {% endfor %}
          {% if not history %}
            <div class="muted">Одоогоор tournament history алга.</div>
          {% endif %}
        </div>
      </section>
    </div>

    <div class="bottom-cta">
      <div>
        <strong>Тэмцээнүүдээ hub дээрээс удирдаж, archive дээрээс түүхээ үзнэ.</strong><br>
        <span class="muted">Live event, standings, prize pool болон player profile бүгд нэг платформ дээр төвлөрнө.</span>
      </div>
      <div class="cta-row" style="margin-top:0;">
        <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
        <a class="page-link" href="/donate">Donate</a>
        <a class="page-link" href="/contact">Contact</a>
      </div>
    </div>

    <div class="footer">Community Tournament Platform · Player Profile</div>
  </div>
  {{ script|safe }}
</body>
</html>
"""
DONATE_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Платформ Donate</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Support The Platform</div>
          <h1>Platform Donations</h1>
          <div class="subtitle">
            Энд орж байгаа donate нь платформ дэмжлэгт зориулагдана:
            server hosting, maintenance, Discord bot development, live web development.
            Энэ нь tournament sponsor-с тусдаа.
          </div>

          <div class="cta-row">
            <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
            <a class="page-link" href="/">Home рүү буцах</a>
          </div>

          <div class="copy-grid">
            <div class="copy-value">{{ donate_account_no }}</div>
            <button class="copy-btn" onclick='copyText({{ donate_account_no|tojson }})'>Copy Account</button>
          </div>

          <div class="copy-grid">
            <div class="copy-value">{{ donate_owner_name }}</div>
            <button class="copy-btn" onclick='copyText({{ donate_owner_name|tojson }})'>Copy Name</button>
          </div>
        </div>

        <div class="champion-card">
          <div class="champion-label">Donation Info</div>
          <div class="champion-name">{{ donate_owner_name }}</div>
          <div class="champion-meta">{{ donate_bank_name }}</div>
          <div class="champion-prize">{{ donate_account_no }}</div>
          <div class="champion-sub">{{ donate_note }}</div>

          {% if donate_qr_src %}
          <div class="qr-box">
            <img src="{{ donate_qr_src }}" alt="Donate QR">
            <div class="muted">QR scan хийгээд платформ дэмжээрэй</div>
          </div>
          {% endif %}
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/admin?key={{ admin_panel_key }}">Admin</a>
      <a href="/leaderboard">Leaderboard</a>
      <a href="/history">Tournament History</a>
      <a href="/donate">Donate</a>
      <a href="/contact">Contact</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    <div class="grid">
      <section class="card cols-6">
        <div class="section-head">
          <h2>Donate мэдээлэл</h2>
          <span class="badge gold">Platform Support</span>
        </div>

        <div class="list">
          <div class="row"><div>Дансны нэр</div><div>{{ donate_owner_name }}</div></div>
          <div class="row"><div>Банк</div><div>{{ donate_bank_name }}</div></div>
          <div class="row">
            <div>Данс</div>
            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; justify-content:flex-end;">
              <span>{{ donate_account_no }}</span>
              <button class="copy-btn" onclick='copyText({{ donate_account_no|tojson }})'>Copy</button>
            </div>
          </div>
          <div class="row">
            <div>Гүйлгээний утга</div>
            <div style="text-align:right">
              Урд нь заавал <strong>Donate</strong> гэж бичнэ<br>
              Donate + Discord нэр эсвэл user ID<br>
              <span class="muted" style="font-size:12px">`💬・ᴄᴏᴍᴍᴜɴɪᴛʏ-ᴄʜᴀᴛ` дээр `.me` командаар авсан user ID</span><br>
              <div style="display:flex; gap:8px; justify-content:flex-end; flex-wrap:wrap; margin-top:8px;">
                <span class="micro-chip support">Donate ShiJEE</span>
                <span class="micro-chip elite">Donate 566213018136870912</span>
              </div>
            </div>
          </div>
          <div class="row"><div>Зориулалт</div><div>Hosting · Maintenance · Bot · Web</div></div>
        </div>

        <div class="info-strip" style="margin-top:16px;">
          Энд орж байгаа donate нь зөвхөн платформ дэмжлэгт ашиглагдана.
          Tournament sponsor болон prize pool-тэй хольж үзэхгүй.
        </div>
      </section>

      <section class="card cols-6">
        <div class="section-head">
          <h2>Platform Supporters</h2>
          <span class="badge green">{{ money(platform_total) }}</span>
        </div>

        <div class="prize-note" style="margin-bottom:14px;">
          Энд зөвхөн platform donate хийсэн хүмүүс харагдана. Tournament sponsor-ууд энд харагдахгүй.
        </div>

        {% if donors|length >= 3 %}
        <div class="hall-grid" style="margin-bottom:16px;">
          <div class="hall-card">
            <div class="hall-rank">🥈</div>
            <div class="hall-name">{% if donors[1].donor_user_id %}<a class="player-link" href="/player/{{ donors[1].donor_user_id }}">{{ donors[1].display_name }}</a>{% else %}{{ donors[1].display_name }}{% endif %}</div>
            <div class="hall-amount">{{ money(donors[1].amount) }}</div>
          </div>
          <div class="hall-card" style="border-color:rgba(255,204,102,.24); box-shadow:0 0 0 1px rgba(255,204,102,.08) inset;">
            <div class="hall-rank">🥇</div>
            <div class="hall-name">{% if donors[0].donor_user_id %}<a class="player-link" href="/player/{{ donors[0].donor_user_id }}">{{ donors[0].display_name }}</a>{% else %}{{ donors[0].display_name }}{% endif %}</div>
            <div class="hall-amount">{{ money(donors[0].amount) }}</div>
          </div>
          <div class="hall-card">
            <div class="hall-rank">🥉</div>
            <div class="hall-name">{% if donors[2].donor_user_id %}<a class="player-link" href="/player/{{ donors[2].donor_user_id }}">{{ donors[2].display_name }}</a>{% else %}{{ donors[2].display_name }}{% endif %}</div>
            <div class="hall-amount">{{ money(donors[2].amount) }}</div>
          </div>
        </div>
        {% endif %}

        <div class="list">
          {% for donor in donors %}
            <div class="row">
              <div class="sponsor-main">
                <div class="mini-avatar">
                  {% if donor.avatar_url %}
                    <img src="{{ donor.avatar_url }}" alt="{{ donor.display_name }}">
                  {% else %}
                    <div class="mini-avatar-fallback">{{ donor.display_name[:1] }}</div>
                  {% endif %}
                </div>
                <div class="sponsor-copy">
                  <div class="sponsor-name">
                    <strong>{% if donor.donor_user_id %}<a class="player-link" href="/player/{{ donor.donor_user_id }}">{{ donor.display_name }}</a>{% else %}{{ donor.display_name }}{% endif %}</strong>
                    <span class="micro-chip support">Donator</span>
                  </div>
                  {% if donor.note %}
                    <div class="sponsor-note">{{ donor.note }}</div>
                  {% endif %}
                </div>
              </div>
              <div>{{ money(donor.amount) }}</div>
            </div>
          {% endfor %}
          {% if not donors %}
            <div class="muted">Одоогоор platform donor алга.</div>
          {% endif %}
        </div>
      </section>

      <section class="card">
        <div class="section-head">
          <h2>Яагаад Donate вэ?</h2>
          <span class="badge blue">Community</span>
        </div>

        <div class="prize-note" style="margin-bottom:14px;">
          Танай өгч байгаа donate нь зүгээр нэг дэмжлэг биш. Энэ платформыг тасралтгүй ажиллуулах,
          tournament-уудыг найдвартай зохион байгуулах, цаашид улам сайжруулах суурь зардлыг бодитоор дааж өгдөг.
        </div>

        <div class="list">
          <div class="row"><div>Server Hosting</div><div>VPS, uptime, traffic, backup болон тогтвортой ажиллагааны үндсэн зардлыг даана</div></div>
          <div class="row"><div>Domain & SSL</div><div>Domain, certificate, secure access болон public site access-ийг байнга ажиллуулна</div></div>
          <div class="row"><div>Maintenance</div><div>Сервер арчилгаа, шинэчлэлт, bug fix, admin management болон найдвартай ажиллагаанд ашиглагдана</div></div>
          <div class="row"><div>Discord Bot</div><div>Registration, payment review, bracket, result, role sync, notification automation-уудыг хөгжүүлж ажиллуулна</div></div>
          <div class="row"><div>Live Web Platform</div><div>Tournament hub, player profile, archive, bracket visual, prize tracking зэрэг web хэсгүүдийг сайжруулна</div></div>
          <div class="row"><div>Future Growth</div><div>Solo, duo, олон tournament зэрэг явагдах platform expansion болон шинэ feature хөгжүүлэлтэд зориулагдана</div></div>
        </div>

        <div class="info-strip" style="margin-top:16px;">
          Таны support нь платформыг зүгээр онлайн байлгахаас илүү, илүү найдвартай, илүү гоё,
          илүү мэргэжлийн tournament environment болгоход шууд нөлөөлнө.
        </div>
      </section>
    </div>

    <div class="bottom-cta">
      <div>
        <strong>Тэмцээнүүдээ hub дээрээс удирдаж, archive дээрээс түүхээ үзнэ.</strong><br>
        <span class="muted">Live event, standings, prize pool болон player profile бүгд нэг платформ дээр төвлөрнө.</span>
      </div>
      <div class="cta-row" style="margin-top:0;">
        <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
        <a class="page-link" href="/donate">Donate</a>
        <a class="page-link" href="/contact">Contact</a>
      </div>
    </div>

    <div class="footer">Chess Of Mongolia Community Tournament Platform · Donate</div>
  </div>
  {{ script|safe }}
</body>
</html>
"""

CONTACT_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Contact</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    {{ css|safe }}
    .contact-shell {
      display: grid;
      gap: 28px;
      max-width: 1260px;
      margin: 0 auto;
    }
    .contact-wrap {
      position: relative;
      overflow: hidden;
      background:
        radial-gradient(circle at top right, rgba(255, 194, 77, 0.08), transparent 26%),
        linear-gradient(180deg, rgba(24, 39, 73, 0.97), rgba(10, 18, 38, 0.99));
      border: 1px solid rgba(89, 129, 220, 0.20);
      border-radius: 30px;
      padding: 34px;
      box-shadow: 0 24px 60px rgba(4, 10, 24, 0.30);
      display: grid;
      gap: 22px;
    }
    .contact-wrap::after {
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(125deg, rgba(255, 197, 82, 0.05), transparent 42%, rgba(110, 157, 255, 0.05));
      pointer-events: none;
    }
    .contact-banner {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.72fr);
      gap: 20px;
      align-items: stretch;
    }
    .contact-summary {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-top: 22px;
    }
    .contact-summary .stat {
      background: rgba(14, 25, 49, 0.82);
      border: 1px solid rgba(93, 136, 229, 0.16);
      border-radius: 18px;
      padding: 16px 18px;
      min-height: 102px;
    }
    .contact-summary .stat-label {
      font-size: 11px;
      letter-spacing: 0.11em;
      text-transform: uppercase;
      color: rgba(176, 197, 255, 0.82);
      margin-bottom: 10px;
    }
    .contact-summary .stat-value {
      font-size: 30px;
      font-weight: 800;
      color: #fff4d4;
      line-height: 1;
      margin-bottom: 6px;
    }
    .contact-summary .stat-note {
      color: rgba(198, 213, 255, 0.82);
      font-size: 14px;
      line-height: 1.5;
    }
    .contact-panel {
      background: linear-gradient(180deg, rgba(21, 35, 67, 0.88), rgba(11, 20, 40, 0.96));
      border: 1px solid rgba(96, 137, 228, 0.14);
      border-radius: 26px;
      padding: 30px;
      display: grid;
      gap: 18px;
    }
    .contact-side-note {
      background: linear-gradient(180deg, rgba(20, 33, 62, 0.84), rgba(12, 20, 39, 0.95));
      border: 1px solid rgba(96, 137, 228, 0.14);
      border-radius: 22px;
      padding: 18px 22px;
      display: grid;
      gap: 14px;
      align-content: start;
    }
    .contact-side-copy {
      display: grid;
      gap: 10px;
      max-width: 100%;
    }
    .contact-side-title {
      margin: 0;
      font-size: 20px;
      line-height: 1.2;
      color: #ffffff;
      font-weight: 800;
    }
    .contact-side-text {
      margin: 0;
      color: rgba(196, 212, 255, 0.84);
      line-height: 1.7;
      font-size: 15px;
    }
    .contact-side-badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      width: fit-content;
      padding: 8px 14px;
      border-radius: 999px;
      background: rgba(74, 147, 255, 0.10);
      border: 1px solid rgba(96, 137, 228, 0.26);
      color: #d8e6ff;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      font-size: 12px;
    }
    .contact-panel .eyebrow,
    .contact-card .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      width: fit-content;
      padding: 8px 14px;
      border-radius: 999px;
      background: rgba(255, 196, 90, 0.12);
      border: 1px solid rgba(255, 198, 95, 0.34);
      color: #ffd277;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .contact-panel h2 {
      margin: 0;
      font-size: 34px;
      line-height: 1.08;
    }
    .contact-panel .subtitle {
      margin: 0;
      color: rgba(201, 215, 255, 0.9);
      line-height: 1.75;
      font-size: 17px;
      max-width: 60ch;
    }
    .contact-card-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 22px;
      max-width: none;
      margin: 0;
    }
    .contact-card {
      position: relative;
      overflow: hidden;
      background:
        radial-gradient(circle at top right, rgba(255, 193, 79, 0.12), transparent 36%),
        linear-gradient(180deg, rgba(30, 43, 78, 0.98), rgba(13, 22, 44, 0.98));
      border: 1px solid rgba(96, 137, 228, 0.24);
      border-radius: 24px;
      padding: 28px;
      display: grid;
      gap: 18px;
      min-height: 0;
      box-shadow: 0 18px 44px rgba(3, 8, 20, 0.34);
    }
    .contact-card::before {
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(135deg, rgba(255, 194, 77, 0.04), transparent 48%, rgba(108, 161, 255, 0.04));
      pointer-events: none;
    }
    .contact-card-head {
      display: flex;
      justify-content: flex-start;
      gap: 16px;
      align-items: flex-start;
    }
    .contact-identity {
      display: flex;
      gap: 16px;
      align-items: center;
      min-width: 0;
    }
    .contact-avatar {
      width: 72px;
      height: 72px;
      border-radius: 20px;
      overflow: hidden;
      border: 1px solid rgba(255, 205, 107, 0.34);
      background: linear-gradient(180deg, rgba(33, 47, 83, 0.94), rgba(13, 22, 44, 0.98));
      flex-shrink: 0;
      box-shadow: 0 12px 34px rgba(7, 13, 30, 0.38);
    }
    .contact-avatar img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .contact-avatar-fallback {
      width: 100%;
      height: 100%;
      display: grid;
      place-items: center;
      font-size: 30px;
      font-weight: 800;
      color: #fff3c4;
    }
    .contact-name {
      margin: 0;
      font-size: 20px;
      line-height: 1.12;
      word-break: break-word;
    }
    .contact-role {
      color: rgba(187, 207, 255, 0.88);
      margin-top: 6px;
      font-size: 14px;
      line-height: 1.6;
    }
    .contact-meta-grid {
      display: grid;
      gap: 12px;
    }
    .contact-row {
      display: grid;
      grid-template-columns: 110px minmax(0, 1fr);
      gap: 16px;
      align-items: flex-start;
      padding: 15px 18px;
      background: rgba(18, 30, 57, 0.76);
      border: 1px solid rgba(91, 132, 221, 0.16);
      border-radius: 16px;
    }
    .contact-row-label {
      color: rgba(174, 195, 248, 0.78);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      min-width: 86px;
    }
    .contact-row-value {
      text-align: left;
      font-weight: 700;
      word-break: break-word;
      color: #ffffff;
      font-size: 14px;
      line-height: 1.6;
    }
    .contact-card .cta-row {
      margin-top: auto;
    }
    .contact-row-value.discord-id {
      font-size: 13px;
      color: rgba(233, 239, 255, 0.95);
    }
    .contact-empty {
      background: linear-gradient(180deg, rgba(28, 41, 74, 0.9), rgba(13, 22, 44, 0.98));
      border: 1px dashed rgba(98, 139, 228, 0.3);
      border-radius: 24px;
      padding: 28px;
      color: rgba(196, 211, 255, 0.86);
      line-height: 1.8;
    }
    @media (max-width: 1480px) {
      .contact-banner {
        grid-template-columns: 1fr;
      }
      .contact-panel h2 {
        font-size: 34px;
      }
      .contact-card-grid {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 1120px) {
      .contact-card-grid,
      .contact-summary {
        grid-template-columns: 1fr;
      }
      .contact-panel h2 {
        font-size: 30px;
      }
      .contact-wrap,
      .contact-side-note,
      .contact-panel,
      .contact-card {
        padding: 22px;
      }
      .contact-row {
        grid-template-columns: 1fr;
        gap: 8px;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="contact-shell">
      <section class="contact-wrap">
        <div class="contact-banner">
          <div class="contact-panel">
            <div class="eyebrow">Chess Of Mongolia Support</div>
            <h2>Moderator Contact</h2>
            <p class="subtitle">{{ support_note }}</p>
            <div class="contact-summary">
              <div class="stat">
                <div class="stat-label">Moderators</div>
                <div class="stat-value">{{ moderators|length }}</div>
                <div class="stat-note">Холбогдох moderator profile-уудыг доороос шууд харна.</div>
              </div>
              <div class="stat">
                <div class="stat-label">Primary Channel</div>
                <div class="stat-value">Discord</div>
                <div class="stat-note">{{ discord_label }}</div>
              </div>
              <div class="stat">
                <div class="stat-label">Support Style</div>
                <div class="stat-value">Fast</div>
                <div class="stat-note">Tournament help, check-in, player issue, live coordination.</div>
              </div>
            </div>
          </div>
          <aside class="contact-side-note">
            <div class="contact-side-copy">
              <div class="eyebrow">Quick Reach</div>
              <div class="contact-side-title">Discord бол moderator багтай холбогдох хамгийн хурдан суваг.</div>
              <p class="contact-side-text">Registration, payment review, check-in болон support асуудлаар шууд холбогдоно.</p>
            </div>
            <div style="display:grid; gap:12px; justify-items:start;">
              <div class="contact-side-badge">24/7 Support</div>
              <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
              <a class="page-link" href="/donate">Donate</a>
            </div>
          </aside>
        </div>

        <nav class="nav">
          <a href="/">Home</a>
          <a href="/tournaments">Tournaments</a>
          <a href="/donate">Donate</a>
          <a href="/contact">Contact</a>
          <a href="/leaderboard">Leaderboard</a>
          <a href="/history">Tournament History</a>
          <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
        </nav>

        {% if moderators %}
        <section class="contact-card-grid">
          {% for moderator in moderators %}
          <article class="contact-card">
            <div class="contact-card-head">
              <div class="contact-identity">
                <div class="contact-avatar">
                  {% if moderator.avatar_url %}
                  <img src="{{ moderator.avatar_url }}" alt="{{ moderator.display_name }}">
                  {% else %}
                  <div class="contact-avatar-fallback">{{ moderator.display_name[:1] }}</div>
                  {% endif %}
                </div>
                <div>
                  <div class="eyebrow">{{ moderator.label }}</div>
                  <h2 class="contact-name">{{ moderator.display_name }}</h2>
                  <div class="contact-role">Tournament support · Live coordination · Player help</div>
                </div>
              </div>
            </div>
            <div class="contact-meta-grid">
              <div class="contact-row">
                <div class="contact-row-label">Discord</div>
                <div class="contact-row-value discord-id">{{ moderator.display_name }} · ID {{ moderator.user_id }}</div>
              </div>
              <div class="contact-row">
                <div class="contact-row-label">Phone</div>
                <div class="contact-row-value">{{ moderator.contact_phone or "Одоогоор оруулаагүй" }}</div>
              </div>
              <div class="contact-row">
                <div class="contact-row-label">Email</div>
                <div class="contact-row-value">{{ moderator.contact_email or "Discord support ашиглана" }}</div>
              </div>
              <div class="contact-row">
                <div class="contact-row-label">Profile</div>
                <div class="contact-row-value">
                  {% if moderator.profile_ready %}
                  {{ money(moderator.total_prize_money or 0) }} prize · {{ moderator.tournaments_played or 0 }} events
                  {% else %}
                  Profile setup хийгдээгүй
                  {% endif %}
                </div>
              </div>
            </div>
            <div class="cta-row">
              {% if moderator.player_url %}
              <a class="page-link" href="{{ moderator.player_url }}">Profile үзэх</a>
              {% else %}
              <span class="page-link" style="opacity:.65; pointer-events:none;">Profile бэлэн биш</span>
              {% endif %}
              <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
            </div>
          </article>
          {% endfor %}
        </section>
        {% else %}
        <div class="contact-empty">
          <strong>Moderator contact profile хараахан тохируулагдаагүй байна.</strong><br>
          Contact page дээр харуулах moderator user ID-уудыг `CONTACT_MODERATOR_IDS` env дээр оруулмагц энд premium contact card болж гарна.
        </div>
        {% endif %}
      </section>

    <div class="bottom-cta">
      <div>
        <strong>Moderator багийн contact мэдээлэл нэг дор төвлөрлөө.</strong><br>
        <span class="muted">Tournament support, live coordination, profile issue болон registration help-тэй холбоотойгоор Discord нь хамгийн хурдан суваг хэвээр байна.</span>
      </div>
      <div class="cta-row" style="margin-top:0;">
        <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
        <a class="page-link" href="/donate">Donate</a>
        <a class="page-link" href="/contact">Contact</a>
      </div>
    </div>

    <div class="footer">Chess Of Mongolia Community Tournament Platform · Contact</div>
  </div>
  {{ script|safe }}
</body>
</html>
"""

SPONSORS_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Sponsors</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Sponsor Network</div>
          <h1>Chess Of Mongolia Sponsors</h1>
        <div class="subtitle">
          Tournament sponsor нь prize pool-д шууд нэмэгдэж, platform support нь server hosting, domain,
          maintenance, Discord bot болон live web platform-ийг тогтвортой ажиллуулахад зориулагдана.
        </div>

        <div class="status-row">
          <span class="badge gold">Tournament Sponsors {{ money(tournament_sponsor_total) }}</span>
          <span class="badge green">Platform Support {{ money(platform_total) }}</span>
          <span class="badge blue">{{ sponsor_count }} Sponsor</span>
        </div>

          <div class="cta-row">
            {% if tournament %}
            <a class="page-link" href="/history/{{ tournament.id }}">Current Tournament</a>
            {% else %}
            <a class="page-link" href="/tournaments">Browse Tournaments</a>
            {% endif %}
            <a class="page-link" href="/donate">Platform Donate</a>
            {% if admin_panel_enabled %}
            <a class="page-link" href="/admin/sponsors?key={{ admin_panel_key }}">Sponsor Admin</a>
            <a class="page-link" href="/admin/announcements?key={{ admin_panel_key }}">Sponsor Broadcast</a>
            {% endif %}
            <a class="page-link" href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord-д нэгдэх</a>
          </div>
        </div>

        <div class="champion-card">
          <div class="champion-label">Sponsor Impact</div>
          <div class="champion-name">{{ tournament.title if tournament else "Active Tournament байхгүй" }}</div>
          <div class="champion-meta">
            {% if tournament %}
              Current tournament sponsor bonus нь шагналын санд шууд орж байгаа дүн.
            {% else %}
              Active tournament үүсэхэд sponsor impact энд харагдана.
            {% endif %}
          </div>
          <div class="champion-prize">{{ money(tournament_sponsor_total) }}</div>
          <div class="champion-sub">
            Top sponsor-ууд current event-ийн prize pool-ийг өсгөж, platform supporters нь ecosystem-ийг бүхэлд нь дэмжинэ.
          </div>
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/admin?key={{ admin_panel_key }}">Admin</a>
      <a href="/leaderboard">Leaderboard</a>
      <a href="/history">Tournament History</a>
      <a href="/donate">Donate</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    <div class="grid">
      <section class="card cols-6" id="sponsors">
        <div class="section-head">
          <h2>Official Partners</h2>
          <span class="badge gold">{{ partner_sponsors|length }} Partners</span>
        </div>
        <div class="prize-note" style="margin-bottom:14px;">
          Platform, venue, brand collaboration талын partner-ууд энд тусдаа харагдана. Эдгээр нь заавал prize pool дээр орохгүй.
        </div>
        <div class="sponsor-wall" style="margin-bottom:24px;">
          {% if partner_sponsors %}
            {% set top_partner = partner_sponsors[0] %}
            <div class="sponsor-hero">
              <div class="sponsor-hero-main">
                <div class="sponsor-hero-avatar">
                  {% if top_partner.image_url %}
                    <img src="{{ top_partner.image_url }}" alt="{{ top_partner.display_name }}">
                  {% else %}
                    <div class="mini-avatar-fallback">{{ top_partner.display_name[:1] }}</div>
                  {% endif %}
                </div>
                <div class="sponsor-hero-copy">
                  <div class="sponsor-hero-label">Official Partner</div>
                  <div class="sponsor-hero-name">
                    {% if top_partner.website_url %}
                      <a class="player-link" href="{{ top_partner.website_url }}" target="_blank" rel="noopener">{{ top_partner.display_name }}</a>
                    {% else %}
                      {{ top_partner.display_name }}
                    {% endif %}
                  </div>
                  <div class="sponsor-hero-sub">
                    Community, venue, brand visibility болон platform collaboration талын гол partner.
                  </div>
                  <div class="sponsor-hero-badges">
                    <span class="micro-chip support">Partner Sponsor</span>
                    {% if top_partner.note %}
                      <span class="micro-chip blue">{{ top_partner.note }}</span>
                    {% endif %}
                  </div>
                </div>
              </div>
              <div class="sponsor-hero-right">
                <div class="sponsor-hero-amount">{{ money(top_partner.amount) }}</div>
                <div class="sponsor-hero-share">Brand Rank · #1</div>
              </div>
            </div>

            {% if partner_sponsors|length > 1 %}
              <div class="sponsor-stack">
                {% for sponsor in partner_sponsors[1:] %}
                  <div class="sponsor-rank-row">
                    <div class="sponsor-rank-left">
                      <span class="sponsor-rank-pill">#{{ loop.index + 1 }}</span>
                      <div class="mini-avatar">
                        {% if sponsor.image_url %}
                          <img src="{{ sponsor.image_url }}" alt="{{ sponsor.display_name }}">
                        {% else %}
                          <div class="mini-avatar-fallback">{{ sponsor.display_name[:1] }}</div>
                        {% endif %}
                      </div>
                      <div class="sponsor-rank-copy">
                        <div class="sponsor-rank-name">
                          <strong>{% if sponsor.website_url %}<a class="player-link" href="{{ sponsor.website_url }}" target="_blank" rel="noopener">{{ sponsor.display_name }}</a>{% else %}{{ sponsor.display_name }}{% endif %}</strong>
                          <span class="micro-chip support">Partner</span>
                        </div>
                        <div class="sponsor-rank-meta">
                          {% if sponsor.note %}
                            {{ sponsor.note }}
                          {% else %}
                            Brand & community partner
                          {% endif %}
                        </div>
                      </div>
                    </div>
                    <div class="sponsor-rank-right">
                      <div class="sponsor-rank-amount">{{ money(sponsor.amount) }}</div>
                    </div>
                  </div>
                {% endfor %}
              </div>
            {% endif %}
          {% else %}
            <div class="sponsor-empty">Одоогоор partner sponsor алга.</div>
          {% endif %}
        </div>

        <div class="section-head">
          <h2>Tournament Sponsors</h2>
          <span class="badge green">{{ money(tournament_sponsor_total) }}</span>
        </div>
        <div class="prize-note" style="margin-bottom:14px;">
          Current tournament-ийн prize pool дээр шууд орж байгаа sponsor contribution энд харагдана.
        </div>
        <div class="sponsor-wall">
          {% if sponsors %}
            {% set top_sponsor = sponsors[0] %}
            <div class="sponsor-hero">
              <div class="sponsor-hero-main">
                <div class="sponsor-hero-avatar">
                  {% if top_sponsor.image_url %}
                    <img src="{{ top_sponsor.image_url }}" alt="{{ top_sponsor.display_name }}">
                  {% else %}
                    <div class="mini-avatar-fallback">{{ top_sponsor.display_name[:1] }}</div>
                  {% endif %}
                </div>
                <div class="sponsor-hero-copy">
                  <div class="sponsor-hero-label">Top Sponsor</div>
                  <div class="sponsor-hero-name">
                    {% if top_sponsor.sponsor_user_id %}
                      <a class="player-link" href="/player/{{ top_sponsor.sponsor_user_id }}">{{ top_sponsor.display_name }}</a>
                    {% else %}
                      {{ top_sponsor.display_name }}
                    {% endif %}
                  </div>
                  <div class="sponsor-hero-sub">
                    Current event-ийн хамгийн өндөр sponsor дэмжлэг. Prize pool-д орж байгаа гол хувь нэмэр.
                  </div>
                  <div class="sponsor-hero-badges">
                    <span class="micro-chip support">Official Sponsor</span>
                    <span class="micro-chip legend">{{ ((top_sponsor.amount / tournament_sponsor_total) * 100)|round(0)|int }}% Pool Share</span>
                    {% if top_sponsor.note %}
                      <span class="micro-chip blue">{{ top_sponsor.note }}</span>
                    {% endif %}
                  </div>
                </div>
              </div>
              <div class="sponsor-hero-right">
                <div class="sponsor-hero-amount">{{ money(top_sponsor.amount) }}</div>
                <div class="sponsor-hero-share">Top Rank · #1</div>
              </div>
            </div>

            {% if sponsors|length > 1 %}
              <div class="sponsor-stack">
                {% for sponsor in sponsors[1:] %}
                  <div class="sponsor-rank-row">
                    <div class="sponsor-rank-left">
                      <span class="sponsor-rank-pill">#{{ loop.index + 1 }}</span>
                      <div class="mini-avatar">
                        {% if sponsor.image_url %}
                          <img src="{{ sponsor.image_url }}" alt="{{ sponsor.display_name }}">
                        {% else %}
                          <div class="mini-avatar-fallback">{{ sponsor.display_name[:1] }}</div>
                        {% endif %}
                      </div>
                      <div class="sponsor-rank-copy">
                        <div class="sponsor-rank-name">
                          <strong>{% if sponsor.sponsor_user_id %}<a class="player-link" href="/player/{{ sponsor.sponsor_user_id }}">{{ sponsor.display_name }}</a>{% else %}{{ sponsor.display_name }}{% endif %}</strong>
                          <span class="micro-chip support">Sponsor</span>
                        </div>
                        <div class="sponsor-rank-meta">
                          {% if sponsor.note %}
                            {{ sponsor.note }}
                          {% else %}
                            Prize pool-д орсон sponsor contribution
                          {% endif %}
                        </div>
                      </div>
                    </div>
                    <div class="sponsor-rank-right">
                      <div class="sponsor-rank-amount">{{ money(sponsor.amount) }}</div>
                      <span class="micro-chip blue">{{ ((sponsor.amount / tournament_sponsor_total) * 100)|round(0)|int }}%</span>
                    </div>
                  </div>
                {% endfor %}
              </div>
            {% endif %}
          {% else %}
            <div class="sponsor-empty">Одоогоор active/current tournament sponsor алга.</div>
          {% endif %}
        </div>
      </section>

      <section class="card cols-6">
        <div class="section-head">
          <h2>Platform Supporters</h2>
          <span class="badge blue">{{ money(platform_total) }}</span>
        </div>

        <div class="prize-note" style="margin-bottom:14px;">
          Энд харагдаж байгаа donate нь tournament prize pool биш, platform infrastructure-г тогтвортой ажиллуулах дэмжлэг юм.
        </div>

        {% if donors|length >= 3 %}
        <div class="hall-grid" style="margin-bottom:16px;">
          <div class="hall-card">
            <div class="hall-rank">🥈</div>
            <div class="hall-name">{% if donors[1].donor_user_id %}<a class="player-link" href="/player/{{ donors[1].donor_user_id }}">{{ donors[1].display_name }}</a>{% else %}{{ donors[1].display_name }}{% endif %}</div>
            <div class="hall-amount">{{ money(donors[1].amount) }}</div>
          </div>
          <div class="hall-card" style="border-color:rgba(255,204,102,.24); box-shadow:0 0 0 1px rgba(255,204,102,.08) inset;">
            <div class="hall-rank">🥇</div>
            <div class="hall-name">{% if donors[0].donor_user_id %}<a class="player-link" href="/player/{{ donors[0].donor_user_id }}">{{ donors[0].display_name }}</a>{% else %}{{ donors[0].display_name }}{% endif %}</div>
            <div class="hall-amount">{{ money(donors[0].amount) }}</div>
          </div>
          <div class="hall-card">
            <div class="hall-rank">🥉</div>
            <div class="hall-name">{% if donors[2].donor_user_id %}<a class="player-link" href="/player/{{ donors[2].donor_user_id }}">{{ donors[2].display_name }}</a>{% else %}{{ donors[2].display_name }}{% endif %}</div>
            <div class="hall-amount">{{ money(donors[2].amount) }}</div>
          </div>
        </div>
        {% endif %}

        <div class="list">
          {% for donor in donors %}
            <div class="row">
              <div class="sponsor-main">
                <div class="mini-avatar">
                  {% if donor.avatar_url %}
                    <img src="{{ donor.avatar_url }}" alt="{{ donor.display_name }}">
                  {% else %}
                    <div class="mini-avatar-fallback">{{ donor.display_name[:1] }}</div>
                  {% endif %}
                </div>
                <div class="sponsor-copy">
                  <div class="sponsor-name">
                    <strong>{% if donor.donor_user_id %}<a class="player-link" href="/player/{{ donor.donor_user_id }}">{{ donor.display_name }}</a>{% else %}{{ donor.display_name }}{% endif %}</strong>
                    <span class="micro-chip support">Donator</span>
                  </div>
                  {% if donor.note %}
                    <div class="sponsor-note">{{ donor.note }}</div>
                  {% endif %}
                </div>
              </div>
              <div>{{ money(donor.amount) }}</div>
            </div>
          {% endfor %}
          {% if not donors %}
            <div class="muted">Одоогоор platform donor алга.</div>
          {% endif %}
        </div>
      </section>
    </div>

    <div class="footer">Chess Of Mongolia Community Tournament Platform · Sponsors</div>
  </div>
  {{ script|safe }}
</body>
</html>
"""

SPONSOR_ADMIN_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Sponsor Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Admin Panel</div>
          <h1>Sponsor Control</h1>
          <div class="subtitle">
            Tournament sponsor-уудын нэр, дүн, logo, link, tier, active төлөвийг эндээс удирдана.
          </div>

          <div class="status-row">
            <span class="badge gold">Selected {{ selected_tournament.title if selected_tournament else "Tournament" }}</span>
            <span class="badge green">{{ sponsors|length }} Sponsors</span>
            <span class="badge blue">{{ money(total_amount) }}</span>
          </div>
        </div>

        <div class="champion-card">
          <div class="champion-label">Panel Access</div>
          <div class="champion-name">Sponsor Admin</div>
          <div class="champion-meta">Simple secret key access</div>
          <div class="champion-sub">Logo URL, website link, display tier болон active state-ийг web-ээс шууд удирдана.</div>
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/sponsors">Sponsors</a>
      <a href="/admin/announcements?key={{ admin_panel_key }}">Sponsor Broadcast</a>
      <a href="/donate">Donate</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    {% if notice %}
    <section class="card" style="margin-bottom:18px;">
      <div class="badge {% if ok %}green{% else %}orange{% endif %}">{{ notice }}</div>
    </section>
    {% endif %}

    <div class="grid">
      <section class="card cols-4">
        <div class="section-head">
          <h2>Tournament Select</h2>
          <span class="badge blue">{{ tournaments|length }} Events</span>
        </div>
        <div class="list">
          {% for item in tournaments %}
            <a class="row player-link" href="/admin/sponsors?tournament_id={{ item.id }}">
              <div>
                <strong>{{ item.title }}</strong><br>
                <span class="muted">{{ item.season_name }}</span>
              </div>
              <div class="micro-chip {% if selected_tournament and selected_tournament.id == item.id %}legend{% else %}support{% endif %}">
                {{ money(item.prize_total) }}
              </div>
            </a>
          {% endfor %}
        </div>
      </section>

      <section class="card cols-8">
        <div class="section-head">
          <h2>{% if editing_sponsor %}Edit Sponsor{% else %}Add Sponsor{% endif %}</h2>
          <span class="badge purple">{{ selected_tournament.title if selected_tournament else "-" }}</span>
        </div>

        <form method="post" class="admin-form">
          <input type="hidden" name="action" value="save">
          <input type="hidden" name="tournament_id" value="{{ selected_tournament.id if selected_tournament else 0 }}">
          <input type="hidden" name="sponsor_id" value="{{ editing_sponsor.id if editing_sponsor else '' }}">

          <div class="admin-form-grid">
            <label class="admin-field">
              <span class="admin-label">Sponsor Name</span>
              <input class="admin-control" name="sponsor_name" value="{{ editing_sponsor.sponsor_name if editing_sponsor else '' }}" placeholder="Chess Partner">
            </label>

            <label class="admin-field">
              <span class="admin-label">Amount</span>
              <input class="admin-control" name="amount" type="number" min="0" value="{{ editing_sponsor.amount if editing_sponsor else '' }}" placeholder="500000">
            </label>

            <label class="admin-field">
              <span class="admin-label">Sponsor User ID</span>
              <input class="admin-control" name="sponsor_user_id" value="{{ editing_sponsor.sponsor_user_id if editing_sponsor and editing_sponsor.sponsor_user_id else '' }}" placeholder="Optional Discord user ID">
              <span class="admin-help">Discord account-тай холбох бол user ID хийж болно.</span>
            </label>

            <label class="admin-field">
              <span class="admin-label">Sponsor Kind</span>
              {% set kind_value = editing_sponsor.sponsor_kind if editing_sponsor and editing_sponsor.sponsor_kind else 'tournament' %}
              <select class="admin-select" name="sponsor_kind">
                <option value="tournament" {% if kind_value == 'tournament' %}selected{% endif %}>Tournament Sponsor</option>
                <option value="partner" {% if kind_value == 'partner' %}selected{% endif %}>Partner / Brand Sponsor</option>
              </select>
            </label>

            <label class="admin-field">
              <span class="admin-label">Display Tier</span>
              <select class="admin-select" name="display_tier">
                {% set tier_value = editing_sponsor.display_tier if editing_sponsor and editing_sponsor.display_tier else 'sponsor' %}
                <option value="top" {% if tier_value == 'top' %}selected{% endif %}>Top Sponsor</option>
                <option value="official" {% if tier_value == 'official' %}selected{% endif %}>Official Partner</option>
                <option value="partner" {% if tier_value == 'partner' %}selected{% endif %}>Partner</option>
                <option value="community" {% if tier_value == 'community' %}selected{% endif %}>Community Sponsor</option>
                <option value="sponsor" {% if tier_value == 'sponsor' %}selected{% endif %}>Standard Sponsor</option>
              </select>
            </label>

            <label class="admin-field">
              <span class="admin-label">Logo URL</span>
              <input class="admin-control" name="logo_url" value="{{ editing_sponsor.logo_url if editing_sponsor else '' }}" placeholder="https://...">
              <span class="admin-help">Logo эсвэл brand image link. Footer, sponsors page дээр ашиглагдана.</span>
            </label>

            <label class="admin-field">
              <span class="admin-label">Website URL</span>
              <input class="admin-control" name="website_url" value="{{ editing_sponsor.website_url if editing_sponsor else '' }}" placeholder="https://...">
            </label>

            <label class="admin-field full">
              <span class="admin-label">Note</span>
              <textarea class="admin-textarea" name="note" placeholder="Official partner, event collaborator, sponsor campaign...">{{ editing_sponsor.note if editing_sponsor else '' }}</textarea>
            </label>

            <div class="admin-field full">
              <span class="admin-label">Active</span>
              <label class="admin-toggle">
                <span class="admin-toggle-copy">
                  <span class="admin-toggle-title">Visible on website</span>
                  <span class="admin-toggle-sub">Идэвхтэй байвал sponsors page, footer ribbon, tournament sponsor wall дээр гарна.</span>
                </span>
                <input name="is_active" type="checkbox" value="1" {% if not editing_sponsor or editing_sponsor.is_active %}checked{% endif %}>
              </label>
            </div>
          </div>

          <div class="cta-row">
            <button class="page-link" type="submit">{% if editing_sponsor %}Update Sponsor{% else %}Save Sponsor{% endif %}</button>
            {% if editing_sponsor %}
              <a class="page-link" href="/admin/sponsors?tournament_id={{ selected_tournament.id }}">Clear</a>
            {% endif %}
          </div>
        </form>
      </section>

      <section class="card">
        <div class="section-head">
          <h2>Current Sponsors</h2>
          <span class="badge green">{{ money(total_amount) }}</span>
        </div>
        <div class="list">
          {% for sponsor in sponsors %}
            <div class="row">
              <div class="sponsor-main">
                <div class="mini-avatar">
                  {% if sponsor.image_url %}
                    <img src="{{ sponsor.image_url }}" alt="{{ sponsor.sponsor_name }}">
                  {% else %}
                    <div class="mini-avatar-fallback">{{ sponsor.sponsor_name[:1] }}</div>
                  {% endif %}
                </div>
                <div class="sponsor-copy">
                  <div class="sponsor-name">
                    <strong>{{ sponsor.sponsor_name }}</strong>
                    <span class="micro-chip blue">{{ 'Tournament' if sponsor.sponsor_kind == 'tournament' else 'Partner' }}</span>
                    <span class="micro-chip support">{{ sponsor.display_tier }}</span>
                    {% if not sponsor.is_active %}
                      <span class="micro-chip bronze">inactive</span>
                    {% endif %}
                  </div>
                  <div class="sponsor-note">
                    {% if sponsor.website_url %}{{ sponsor.website_url }}{% else %}{{ sponsor.note or 'No note' }}{% endif %}
                  </div>
                </div>
              </div>
              <div style="text-align:right;">
                <div style="font-weight:900;">{{ money(sponsor.amount) }}</div>
                <div class="cta-row" style="justify-content:flex-end; margin-top:8px;">
                  <a class="page-link" href="/admin/sponsors?tournament_id={{ selected_tournament.id }}&edit={{ sponsor.id }}">Edit</a>
                  <form method="post" style="display:inline;">
                    <input type="hidden" name="action" value="delete">
                    <input type="hidden" name="tournament_id" value="{{ selected_tournament.id }}">
                    <input type="hidden" name="sponsor_id" value="{{ sponsor.id }}">
                    <button class="page-link" type="submit">Delete</button>
                  </form>
                </div>
              </div>
            </div>
          {% endfor %}
          {% if not sponsors %}
            <div class="sponsor-empty">Одоогоор энэ tournament дээр sponsor бүртгэгдээгүй байна.</div>
          {% endif %}
        </div>
      </section>
    </div>

    <div class="footer">Chess Of Mongolia Community Tournament Platform · Sponsor Admin</div>
  </div>
</body>
</html>
"""

ANNOUNCEMENT_ADMIN_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Announcement Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Admin Panel</div>
          <h1>Announcements</h1>
          <div class="subtitle">
            Web-ээс sponsor, partner, brand update-аа оруулаад Discord channel руу card байдлаар автоматаар явуулна.
          </div>

          <div class="status-row">
            <span class="badge gold">{{ selected_tournament.title if selected_tournament else "Platform-wide" }}</span>
            <span class="badge green">{{ announcements|length }} Items</span>
            <span class="badge blue">{{ published_count }} Published</span>
          </div>
        </div>

        <div class="champion-card">
          <div class="champion-label">Sponsor Flow</div>
          <div class="champion-name">Brand → Website → Discord</div>
          <div class="champion-meta">Schedule, repeat, publish</div>
          <div class="champion-sub">Sponsor update save хиймэгц queue-д орж, сонгосон 3 / 6 / 9 / 12 цагийн interval-аар Discord дээр card болж гарна.</div>
        </div>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments">Tournaments</a>
      <a href="/sponsors">Sponsors</a>
      <a href="/admin/sponsors?key={{ admin_panel_key }}">Sponsor Admin</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    {% if notice %}
    <section class="card" style="margin-bottom:18px;">
      <div class="badge {% if ok %}green{% else %}orange{% endif %}">{{ notice }}</div>
    </section>
    {% endif %}

    <div class="grid">
      <section class="card cols-4">
        <div class="section-head">
          <h2>Context Select</h2>
          <span class="badge blue">{{ tournaments|length }} Events</span>
        </div>
        <div class="list">
          <a class="row player-link" href="/admin/announcements">
            <div>
              <strong>Platform-wide Announcement</strong><br>
              <span class="muted">Tournament prefix, season field-гүй partner / brand announce</span>
            </div>
            <div class="micro-chip {% if not selected_tournament %}legend{% else %}support{% endif %}">
              Global
            </div>
          </a>
          {% for item in tournaments %}
            <a class="row player-link" href="/admin/announcements?tournament_id={{ item.id }}">
              <div>
                <strong>{{ item.title }}</strong><br>
                <span class="muted">{{ item.season_name }}</span>
              </div>
              <div class="micro-chip {% if selected_tournament and selected_tournament.id == item.id %}legend{% else %}support{% endif %}">
                {{ item.status_label if item.status_label is defined else item.status }}
              </div>
            </a>
          {% endfor %}
        </div>
      </section>

      <section class="card cols-8">
        <div class="section-head">
          <h2>{% if editing_announcement %}Edit Announcement{% else %}Create Announcement{% endif %}</h2>
          <span class="badge purple">{{ selected_tournament.title if selected_tournament else "Platform-wide" }}</span>
        </div>

        <form method="post" class="admin-form">
          <input type="hidden" name="action" value="save">
          <input type="hidden" name="tournament_id" value="{{ selected_tournament.id if selected_tournament else 0 }}">
          <input type="hidden" name="announcement_id" value="{{ editing_announcement.id if editing_announcement else '' }}">

          <div class="admin-form-grid">
            <label class="admin-field">
              <span class="admin-label">Title</span>
              <input class="admin-control" name="title" value="{{ editing_announcement.title if editing_announcement else '' }}" placeholder="TST ESPORT CENTER">
            </label>

            <label class="admin-field">
              <span class="admin-label">Badge</span>
              <input class="admin-control" name="badge" value="{{ editing_announcement.badge if editing_announcement else 'Announcement' }}" placeholder="Announcement">
            </label>

            <label class="admin-field full">
              <span class="admin-label">Body</span>
              <textarea class="admin-textarea" name="body" placeholder="Шинэ partner, sponsor activation, brand update зэрэг announce text...">{{ editing_announcement.body if editing_announcement else '' }}</textarea>
            </label>

            <label class="admin-field">
              <span class="admin-label">Button Text</span>
              <input class="admin-control" name="button_text" value="{{ editing_announcement.button_text if editing_announcement else '' }}" placeholder="View Tournament">
            </label>

            <label class="admin-field">
              <span class="admin-label">Button URL</span>
              <input class="admin-control" name="button_url" value="{{ editing_announcement.button_url if editing_announcement else '' }}" placeholder="https://... or /history/5">
            </label>

            <label class="admin-field">
              <span class="admin-label">Image URL</span>
              <input class="admin-control" name="image_url" value="{{ editing_announcement.image_url if editing_announcement else '' }}" placeholder="https://...">
            </label>

            <label class="admin-field">
              <span class="admin-label">Target Channel</span>
              {% set channel_value = editing_announcement.target_channel if editing_announcement and editing_announcement.target_channel else 'general-chat' %}
              <select class="admin-select" name="target_channel">
                <option value="general-chat" {% if channel_value == 'general-chat' %}selected{% endif %}>💬・ɢᴇɴᴇʀᴀʟ-ᴄʜᴀᴛ</option>
                <option value="announcements" {% if channel_value == 'announcements' %}selected{% endif %}>📢・ᴀɴɴᴏᴜɴᴄᴇᴍᴇɴᴛꜱ</option>
                <option value="weekly-status" {% if channel_value == 'weekly-status' %}selected{% endif %}>Weekly Status</option>
                <option value="match-results" {% if channel_value == 'match-results' %}selected{% endif %}>Match Results</option>
                <option value="waiting-players" {% if channel_value == 'waiting-players' %}selected{% endif %}>Waiting Players</option>
              </select>
            </label>

            <label class="admin-field">
              <span class="admin-label">Repeat Interval</span>
              {% set repeat_value = editing_announcement.repeat_hours if editing_announcement and editing_announcement.repeat_hours is not none else 0 %}
              <select class="admin-select" name="repeat_hours">
                <option value="0" {% if repeat_value|int == 0 %}selected{% endif %}>One-time only</option>
                <option value="3" {% if repeat_value|int == 3 %}selected{% endif %}>Every 3 hours</option>
                <option value="6" {% if repeat_value|int == 6 %}selected{% endif %}>Every 6 hours</option>
                <option value="9" {% if repeat_value|int == 9 %}selected{% endif %}>Every 9 hours</option>
                <option value="12" {% if repeat_value|int == 12 %}selected{% endif %}>Every 12 hours</option>
              </select>
            </label>

            <label class="admin-field">
              <span class="admin-label">Repeat Until</span>
              {% set end_value = editing_announcement.end_at[:10] if editing_announcement and editing_announcement.end_at else '' %}
              <input class="admin-control" type="date" name="end_at" value="{{ end_value }}">
            </label>

            <div class="admin-field full">
              <span class="admin-label">Status</span>
              <label class="admin-toggle">
                <span class="admin-toggle-copy">
                  <span class="admin-toggle-title">Start Sponsor Broadcast</span>
                  <span class="admin-toggle-sub">Checked бол save хийхэд queue-д орж, bot автоматаар Discord card болгож post хийнэ. Interval сонгосон бол тэр зайтайгаар давтан зарлана.</span>
                </span>
                <input name="publish_now" type="checkbox" value="1" {% if not editing_announcement or editing_announcement.status != 'draft' %}checked{% endif %}>
              </label>
            </div>
          </div>

          <div class="cta-row">
            <button class="page-link" type="submit">{% if editing_announcement %}Update Announcement{% else %}Save Announcement{% endif %}</button>
            {% if editing_announcement %}
              <a class="page-link" href="/admin/announcements{% if selected_tournament %}?tournament_id={{ selected_tournament.id }}{% endif %}">Clear</a>
            {% endif %}
          </div>
        </form>
      </section>

      <section class="card">
        <div class="section-head">
          <h2>Sponsor Queue</h2>
          <span class="badge green">{{ published_count }} Published</span>
        </div>
        <div class="list">
          {% for item in announcements %}
            <div class="row">
              <div class="sponsor-main">
                <div class="mini-avatar">
                  <div class="mini-avatar-fallback">{{ item.badge[:1] if item.badge else 'A' }}</div>
                </div>
                <div>
                  <div style="font-weight:800;">{{ item.title }}</div>
                  <div class="muted">Sponsor Update · {{ item.badge }} · {{ item.target_channel }} · {{ item.status|upper }}{% if item.repeat_hours|int > 0 %} · every {{ item.repeat_hours }}h{% endif %}{% if item.end_at %} · until {{ item.end_at[:10] }}{% endif %}</div>
                  {% if item.body %}
                  <div class="muted" style="margin-top:6px;">{{ item.body[:140] }}{% if item.body|length > 140 %}...{% endif %}</div>
                  {% endif %}
                </div>
              </div>
              <div class="sponsor-actions">
                <div class="micro-chip {% if item.status == 'published' %}legend{% elif item.status == 'queued' %}support{% else %}donator{% endif %}">
                  {{ item.status }}
                </div>
                <div class="cta-row" style="margin-top:10px; justify-content:flex-end;">
                  <a class="page-link" href="/admin/announcements{% if selected_tournament %}?tournament_id={{ selected_tournament.id }}&edit={{ item.id }}{% else %}?edit={{ item.id }}{% endif %}">Edit</a>
                  <form method="post" style="display:inline;">
                    <input type="hidden" name="action" value="delete">
                    <input type="hidden" name="tournament_id" value="{{ selected_tournament.id if selected_tournament else 0 }}">
                    <input type="hidden" name="announcement_id" value="{{ item.id }}">
                    <button class="page-link" type="submit">Delete</button>
                  </form>
                </div>
              </div>
            </div>
          {% else %}
            <div class="row"><div class="muted">Announcement алга байна.</div></div>
          {% endfor %}
        </div>
      </section>
    </div>
  </div>
</body>
</html>
"""

TOURNAMENT_ADMIN_TEMPLATE = """
  <!doctype html>
  <html lang="mn">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tournament Admin</title>
  <style>{{ css|safe }}</style>
</head>
<body>
  <main class="shell">
    <section class="card">
      <div class="section-head">
        <h2>Tournament Admin</h2>
        <span class="badge blue">{{ tournaments|length }} Events</span>
      </div>
      <p class="muted">Weekly Auto Chess Cup-ийг web-ээс шууд үүсгэнэ.</p>
      {% if notice %}
      <div class="notice {{ 'ok' if ok else 'error' }}">{{ notice }}</div>
      {% endif %}
      <div class="badge-row" style="margin:16px 0 10px;">
        <span class="badge gold">1. Тэмцээний нэр</span>
        <span class="badge blue">2. Цагийн тохиргоо</span>
        <span class="badge green">3. Шагналын сан</span>
      </div>
      <div class="grid">
        <section class="card cols-6">
          <div class="section-head">
            <h2>Create Tournament</h2>
            <span class="badge gold">Auto Chess</span>
          </div>
          <div class="muted" style="margin:-2px 0 16px;">Form бөглөөд create хийхэд Tournament Hub дээр шууд гарч, bot registration card-аа Discord дээр автоматаар постлоно.</div>
          <form class="admin-form" method="post">
            <input type="hidden" name="key" value="{{ admin_panel_key }}">
            <section class="card" style="padding:18px;">
              <div class="section-head" style="margin-bottom:12px;">
                <h3 style="margin:0;">Basic Info</h3>
                <span class="badge gold">Required</span>
              </div>
              <div class="admin-form-grid">
                <label class="admin-field full" for="title">
                  <span class="admin-label">Tournament Title</span>
                  <input class="admin-control" id="title" name="title" placeholder="Жишээ: Weekly Auto Chess Cup" required>
                  <span class="admin-help">Tournament Hub дээр харагдах гарчиг. Жишээ нь: Season 3, Weekly Cup, Test Event.</span>
                </label>
                <label class="admin-field" for="entry_fee">
                  <span class="admin-label">Entry Fee</span>
                  <input class="admin-control" id="entry_fee" name="entry_fee" inputmode="numeric" value="50000">
                </label>
                <div class="admin-field">
                  <span class="admin-label">Format</span>
                  <div class="admin-toggle" style="min-height:52px;">
                    <span class="admin-toggle-copy">
                      <span class="admin-toggle-title">32 Players · 4 Zones · BO2</span>
                      <span class="admin-toggle-sub">Одоогийн create panel нь Weekly Auto Chess solo format-аар үүсгэнэ.</span>
                    </span>
                  </div>
                </div>
              </div>
            </section>

            <section class="card" style="padding:18px;">
              <div class="section-head" style="margin-bottom:12px;">
                <h3 style="margin:0;">Schedule</h3>
                <span class="badge blue">Optional</span>
              </div>
              <div class="admin-form-grid">
                <label class="admin-field" for="start_time">
                  <span class="admin-label">Start Time</span>
                  <input class="admin-control" id="start_time" name="start_time" type="datetime-local">
                  <span class="admin-help">Тэмцээн яг эхлэх өдөр, цаг.</span>
                </label>
                <label class="admin-field" for="checkin_time">
                  <span class="admin-label">Check-in Time</span>
                  <input class="admin-control" id="checkin_time" name="checkin_time" type="datetime-local">
                  <span class="admin-help">Тоглогчид lobby-д бэлэн байх цаг.</span>
                </label>
              </div>
            </section>

            <section class="card" style="padding:18px;">
              <div class="section-head" style="margin-bottom:12px;">
                <h3 style="margin:0;">Prize Pool</h3>
                <span class="badge green">Tracked</span>
              </div>
              <div class="admin-form-grid">
                <label class="admin-field full" for="prize_total">
                  <span class="admin-label">Base Prize Pool</span>
                  <input class="admin-control" id="prize_total" name="prize_total" inputmode="numeric" value="1600000">
                  <span class="admin-help">Tournament sponsor нэмэгдэхээс өмнөх үндсэн шагналын сан.</span>
                </label>
                <label class="admin-field" for="prize_1">
                  <span class="admin-label">1-р байр</span>
                  <input class="admin-control" id="prize_1" name="prize_1" inputmode="numeric" value="800000">
                </label>
                <label class="admin-field" for="prize_2">
                  <span class="admin-label">2-р байр</span>
                  <input class="admin-control" id="prize_2" name="prize_2" inputmode="numeric" value="500000">
                </label>
                <label class="admin-field full" for="prize_3">
                  <span class="admin-label">3-р байр</span>
                  <input class="admin-control" id="prize_3" name="prize_3" inputmode="numeric" value="300000">
                </label>
              </div>
            </section>

            <div class="cta-row" style="margin-top:4px;">
              <button class="btn" type="submit">Create Tournament</button>
              <a class="page-link" href="/admin?key={{ admin_panel_key }}">Back to Dashboard</a>
              <a class="page-link" href="/tournaments?key={{ admin_panel_key }}">Open Tournament Hub</a>
            </div>
          </form>
        </section>
        <section class="card cols-6">
          <div class="section-head">
            <h2>Recent Tournaments</h2>
            <span class="badge green">{{ tournaments|length }}</span>
          </div>
          <div class="muted" style="margin:-2px 0 16px;">Сүүлд үүссэн tournament-уудаа эндээс шууд нээж, detail page, registration flow болон archive-аа шалгаж болно.</div>
          <div class="history-grid" style="grid-template-columns:1fr; gap:14px;">
            {% for item in tournaments[:8] %}
            <article class="history-card" style="padding:16px 18px;">
              <div class="history-head">
                <div>
                  <div class="history-season">{{ item.season_name or '-' }}</div>
                  <div class="history-title">{{ item.title }}</div>
                </div>
                <span class="badge {{ 'purple' if item.status == 'completed' else 'green' }}">{{ tournament_status_label(item.status) }}</span>
              </div>
              <div class="hub-overview-grid" style="margin-top:12px;">
                <div class="hub-overview-main">
                  <div class="hub-overview-item">
                    <div class="label">Entry Fee</div>
                    <div class="value">{{ money(item.entry_fee) }}</div>
                  </div>
                  <div class="hub-overview-item">
                    <div class="label">Start Time</div>
                    <div class="value">{{ schedule_value(item.start_time) }}</div>
                  </div>
                </div>
                <div class="hub-overview-stats">
                  <div class="hub-overview-item stat">
                    <div class="label">Confirmed</div>
                    <div class="value">{{ item.confirmed_count }}/{{ item.max_players }}</div>
                  </div>
                  <div class="hub-overview-item stat">
                    <div class="label">Waiting</div>
                    <div class="value">{{ item.registered_count + item.waitlist_count }}</div>
                  </div>
                  <div class="hub-overview-item stat">
                    <div class="label">Prize</div>
                    <div class="value">{{ money(item.prize_total) }}</div>
                  </div>
                </div>
              </div>
              <div class="cta-row" style="margin-top:14px;">
                <a class="page-link" href="/history/{{ item.id }}?key={{ admin_panel_key }}">Open Event</a>
                <a class="page-link" href="/tournaments?key={{ admin_panel_key }}">Open Hub</a>
              </div>
            </article>
            {% else %}
            <div class="card" style="border-style:dashed; background:rgba(255,255,255,.025);">
              <div class="section-head">
                <h3 style="margin:0;">Tournament хараахан үүсээгүй байна</h3>
                <span class="badge blue">Fresh Start</span>
              </div>
              <div class="muted" style="margin-top:6px;">Зүүн талын form-оос анхны tournament-оо үүсгэхэд энд шууд card болж харагдана.</div>
            </div>
            {% endfor %}
          </div>
        </section>
      </div>
    </section>
  </main>
</body>
</html>
"""

ADMIN_DASHBOARD_TEMPLATE = """
<!doctype html>
<html lang="mn">
<head>
  <meta charset="utf-8">
  <title>Admin Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{{ css|safe }}</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="admin-hero-grid">
        <div>
          <div class="eyebrow">Chess Of Mongolia Control Center</div>
          <h1>Admin Dashboard</h1>
          <div class="subtitle">Tournament, Sponsors, Announcements болон дараа нэмэгдэх бүх operational tool-ийг нэг дороос удирдана.</div>

          <div class="chips" style="margin-top:18px;">
            <span class="badge blue">{{ tournament_count }} Tournaments</span>
            <span class="badge green">{{ live_count }} Live/Open</span>
            <span class="badge gold">{{ sponsor_count }} Sponsors</span>
            <span class="badge blue">{{ announcement_count }} Announcements</span>
          </div>
        </div>
        <aside class="admin-hero-panel">
          <div class="admin-hero-kicker">Operations Snapshot</div>
          <div class="admin-hero-title">1 place to run the whole platform</div>
          <div class="admin-hero-copy">Tournament lifecycle, sponsor visibility, Discord broadcast, bracket actions болон дараагийн шинэ module-уудыг энэ hub дээрээс удирдана.</div>
          <div class="admin-hero-stats">
            <div class="admin-hero-stat">
              <div class="label">Live/Open</div>
              <div class="value">{{ live_count }}</div>
              <div class="sub">Registration, waiting review, bracket flow нь идэвхтэй яваа event-үүд.</div>
            </div>
            <div class="admin-hero-stat">
              <div class="label">Broadcast Queue</div>
              <div class="value">{{ announcement_count }}</div>
              <div class="sub">Discord announce, sponsor update болон publish-ready card-ууд.</div>
            </div>
            <div class="admin-hero-stat">
              <div class="label">Today Reach</div>
              <div class="value">{{ analytics.today_views }}</div>
              <div class="sub">{{ analytics.today_unique }} unique visitor today.</div>
            </div>
            <div class="admin-hero-stat">
              <div class="label">7-Day Reach</div>
              <div class="value">{{ analytics.week_views }}</div>
              <div class="sub">{{ analytics.week_unique }} unique visitor in the last 7 days.</div>
            </div>
          </div>
        </aside>
      </div>
    </section>

    <nav class="nav">
      <a href="/">Home</a>
      <a href="/tournaments?key={{ admin_panel_key }}">Tournaments</a>
      <a href="/sponsors?key={{ admin_panel_key }}">Sponsors</a>
      <a href="/history">Tournament History</a>
      <a href="{{ discord_invite_url }}" target="_blank" rel="noopener">Discord</a>
    </nav>

    {% if notice %}
    <div class="notice {{ 'ok' if ok else 'error' }}">{{ notice }}</div>
    {% endif %}

    <div class="grid">
      <section class="card cols-12 admin-section">
        <div class="section-head">
          <h2>Site Reach</h2>
          <span class="badge gold">{{ analytics.total_views }} Total Views</span>
        </div>
        <div class="prize-note">Энэ analytics зөвхөн admin panel дээр харагдана. Public page view, unique visitor болон хамгийн их үзсэн page-үүдийг эндээс шалгана.</div>
        <div class="analytics-grid">
          <div class="analytics-panel">
            <div class="analytics-top">
              <div>
                <div class="analytics-kicker">Reach Summary</div>
                <div class="analytics-title">Public site traffic</div>
              </div>
              <span class="badge green">{{ analytics.total_unique }} Unique</span>
            </div>
            <div class="analytics-sub">Home, tournaments, donate, contact болон бусад public page-үүдийн view-ийг нэг дороос харуулна.</div>
            <div class="analytics-duo">
              <div class="analytics-metric">
                <div class="label">Today Views</div>
                <div class="value">{{ analytics.today_views }}</div>
                <div class="hint">{{ analytics.today_unique }} unique today</div>
              </div>
              <div class="analytics-metric">
                <div class="label">7-Day Views</div>
                <div class="value">{{ analytics.week_views }}</div>
                <div class="hint">{{ analytics.week_unique }} unique this week</div>
              </div>
              <div class="analytics-metric">
                <div class="label">All-Time Views</div>
                <div class="value">{{ analytics.total_views }}</div>
                <div class="hint">Public pages only</div>
              </div>
              <div class="analytics-metric">
                <div class="label">Tracked Pages</div>
                <div class="value">{{ analytics.top_pages|length }}</div>
                <div class="hint">Most viewed routes below</div>
              </div>
            </div>
          </div>
          <div class="analytics-panel">
            <div class="analytics-top">
              <div>
                <div class="analytics-kicker">Top Pages</div>
                <div class="analytics-title">Most viewed routes</div>
              </div>
              <span class="badge blue">{{ analytics.top_pages|length }} Pages</span>
            </div>
            {% if analytics.top_pages %}
            <div class="analytics-page-list">
              {% for page in analytics.top_pages %}
              <div class="analytics-page">
                <div class="analytics-page-path">{{ page.path }}</div>
                <div class="analytics-page-meta">{{ page.views }} views · {{ page.unique_visitors }} unique</div>
              </div>
              {% endfor %}
            </div>
            {% else %}
            <div class="muted">Одоогоор analytics data цуглараагүй байна.</div>
            {% endif %}
          </div>
        </div>
      </section>

      <section class="card cols-12 admin-section">
        <div class="section-head">
          <h2>Core Modules</h2>
          <span class="badge blue">1 Place Control</span>
        </div>
        <div class="prize-note">Tournament, sponsor, broadcast болон event management-ийг доорх module-уудаар нэг дороос удирдана.</div>
        <div class="admin-module-grid">
          <article class="admin-module-card">
            <div class="admin-module-kicker">Tournament Setup</div>
            <div class="history-head" style="margin-top:8px; margin-bottom:0;">
              <div class="admin-module-title">Create Tournament</div>
              <span class="badge gold">Auto Chess</span>
            </div>
            <div class="admin-module-copy">Шинэ tournament үүсгэж, register UI, Discord registration card болон announcement flow-г автоматаар эхлүүлнэ.</div>
            <div class="admin-module-footer">
              <a class="page-link" href="/admin/tournaments?key={{ admin_panel_key }}">Open Tournament Admin</a>
            </div>
          </article>
          <article class="admin-module-card">
            <div class="admin-module-kicker">Sponsor & Partners</div>
            <div class="history-head" style="margin-top:8px; margin-bottom:0;">
              <div class="admin-module-title">Sponsor Panel</div>
              <span class="badge blue">{{ sponsor_count }} Total</span>
            </div>
            <div class="admin-module-copy">Tournament sponsor, partner branding, logo, link, tier болон display visibility-г нэг дороос удирдана.</div>
            <div class="admin-module-footer">
              <a class="page-link" href="/admin/sponsors?key={{ admin_panel_key }}">Open Sponsor Admin</a>
            </div>
          </article>
          <article class="admin-module-card">
            <div class="admin-module-kicker">Discord Broadcast</div>
            <div class="history-head" style="margin-top:8px; margin-bottom:0;">
              <div class="admin-module-title">Announcements</div>
              <span class="badge green">{{ announcement_count }} Saved</span>
            </div>
            <div class="admin-module-copy">Sponsor update, partner post, platform card болон campaign broadcast-уудыг web-ээс бэлдээд Discord руу publish хийнэ.</div>
            <div class="admin-module-footer">
              <a class="page-link" href="/admin/announcements?key={{ admin_panel_key }}">Open Announcement Admin</a>
            </div>
          </article>
        </div>
      </section>

      <section class="card cols-12 admin-section">
        <div class="section-head">
          <h2>Event Operations</h2>
          <span class="badge green">{{ live_count }} Live/Open</span>
        </div>
        <div class="prize-note">Tournament бүрийн detail page, sponsor panel, broadcast panel болон hub view рүү доороос шууд орно.</div>
        {% if tournaments %}
        <div class="admin-op-grid">
          {% for item in tournaments %}
          <article class="admin-op-card">
            <div class="admin-op-top">
              <div>
                <div class="history-season">{{ item.season_name or "-" }}</div>
                <div class="admin-op-title"><a href="/history/{{ item.id }}?key={{ admin_panel_key }}">{{ item.title }}</a></div>
                <div class="admin-op-meta">
                  <span class="admin-op-chip">{{ schedule_value(item.start_time) }}</span>
                  <span class="admin-op-chip">{{ money(item.entry_fee) }} entry</span>
                  <span class="admin-op-chip">{{ item.type|replace('_', ' ')|title }}</span>
                </div>
              </div>
              <span class="badge {{ 'green' if item.status != 'completed' else 'blue' }}">{{ tournament_status_label(item.status) }}</span>
            </div>
            <div class="admin-op-stats">
              <div class="admin-op-stat">
                <div class="label">Confirmed</div>
                <div class="value">{{ item.confirmed_count }}/{{ item.max_players }}</div>
              </div>
              <div class="admin-op-stat">
                <div class="label">Waiting</div>
                <div class="value">{{ item.registered_count + item.waitlist_count }}</div>
              </div>
              <div class="admin-op-stat">
                <div class="label">Prize</div>
                <div class="value">{{ money(item.prize_total) }}</div>
              </div>
            </div>
            <div class="admin-op-actions-group">
              <div class="admin-op-actions-label">View & Broadcast</div>
              <div class="admin-op-actions">
                <a class="page-link" href="/history/{{ item.id }}?key={{ admin_panel_key }}">Event Detail Page</a>
                <a class="page-link" href="/admin/sponsors?key={{ admin_panel_key }}&tournament_id={{ item.id }}">Sponsor Settings</a>
                <a class="page-link" href="/admin/announcements?key={{ admin_panel_key }}&tournament_id={{ item.id }}">Announcement Panel</a>
                <a class="page-link" href="/tournaments?key={{ admin_panel_key }}">Tournament Hub</a>
              </div>
            </div>
            <div class="admin-op-actions-group">
              <div class="admin-op-actions-label">Operational Controls</div>
              <div class="admin-op-actions">
                {% if item.status == 'registration_open' %}
                <form method="post" action="/admin/tournaments/{{ item.id }}/actions" style="display:inline-flex; margin:0;">
                  <input type="hidden" name="action" value="close_registration">
                  <input type="hidden" name="key" value="{{ admin_panel_key }}">
                  <button class="button-primary" type="submit">Close Player Registration</button>
                </form>
                {% endif %}
                <form method="post" action="/admin/tournaments/{{ item.id }}/actions" style="display:inline-flex; margin:0;">
                  <input type="hidden" name="action" value="republish_registration_ui">
                  <input type="hidden" name="key" value="{{ admin_panel_key }}">
                  <button class="page-link" type="submit">Republish Discord Cards</button>
                </form>
                {% if item.status in ['registration_open', 'registration_locked'] and item.confirmed_count >= item.max_players %}
                <form method="post" action="/admin/tournaments/{{ item.id }}/actions" style="display:inline-flex; margin:0;">
                  <input type="hidden" name="action" value="generate_zones">
                  <input type="hidden" name="key" value="{{ admin_panel_key }}">
                  <button class="button-primary" type="submit">Generate Zone Draw</button>
                </form>
                {% endif %}
                {% if item.status != 'completed' %}
                <form method="post" action="/admin/tournaments/{{ item.id }}/actions" style="display:inline-flex; margin:0;">
                  <input type="hidden" name="action" value="complete_tournament">
                  <input type="hidden" name="key" value="{{ admin_panel_key }}">
                  <button class="page-link" type="submit">Mark Tournament Completed</button>
                </form>
                {% endif %}
              </div>
            </div>
          </article>
          {% endfor %}
        </div>
        {% else %}
        <div class="card" style="border-style:dashed; background:rgba(255,255,255,.025);">
          <div class="section-head">
            <h3 style="margin:0;">Tournament байхгүй байна</h3>
            <span class="badge blue">Fresh Start</span>
          </div>
          <div class="muted" style="margin-top:6px;">Шинэ tournament үүсгээд hub болон bracket flow-оо эндээс удирдана.</div>
          <div class="cta-row" style="margin-top:14px;">
            <a class="btn secondary" href="/admin/tournaments?key={{ admin_panel_key }}">Create Tournament</a>
          </div>
        </div>
        {% endif %}
      </section>
    </div>

    <div class="footer">Chess Of Mongolia · Unified Admin Dashboard</div>
  </div>
</body>
</html>
"""
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def table_exists(table_name: str) -> bool:
    with get_db() as db:
        row = db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
        return row is not None


def column_exists(table_name: str, column_name: str) -> bool:
    with get_db() as db:
        rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(str(r["name"]) == column_name for r in rows)


def ensure_site_analytics_table() -> None:
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS site_page_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                visitor_key TEXT NOT NULL,
                user_agent TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        db.commit()


def get_request_visitor_key() -> str:
    forwarded = str(request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return str(request.remote_addr or "unknown").strip() or "unknown"


def track_site_page_view() -> None:
    if request.method != "GET":
        return
    path = str(request.path or "").strip() or "/"
    if (
        path.startswith("/admin")
        or path.startswith("/auth/")
        or path.startswith("/static/")
        or path == "/favicon.ico"
    ):
        return
    ensure_site_analytics_table()
    visitor_key = get_request_visitor_key()
    user_agent = str(request.headers.get("User-Agent") or "")[:240]
    with get_db() as db:
        db.execute(
            """
            INSERT INTO site_page_views (path, visitor_key, user_agent)
            VALUES (?, ?, ?)
            """,
            (path, visitor_key, user_agent),
        )
        db.commit()


def get_site_analytics_summary() -> dict[str, Any]:
    ensure_site_analytics_table()
    with get_db() as db:
        summary_row = db.execute(
            """
            SELECT
              COUNT(*) AS total_views,
              COUNT(DISTINCT visitor_key) AS total_unique,
              SUM(CASE WHEN date(created_at, 'localtime') = date('now', 'localtime') THEN 1 ELSE 0 END) AS today_views,
              COUNT(DISTINCT CASE WHEN date(created_at, 'localtime') = date('now', 'localtime') THEN visitor_key END) AS today_unique,
              SUM(CASE WHEN datetime(created_at) >= datetime('now', '-7 days') THEN 1 ELSE 0 END) AS week_views,
              COUNT(DISTINCT CASE WHEN datetime(created_at) >= datetime('now', '-7 days') THEN visitor_key END) AS week_unique
            FROM site_page_views
            """
        ).fetchone()
        top_pages = db.execute(
            """
            SELECT
              path,
              COUNT(*) AS views,
              COUNT(DISTINCT visitor_key) AS unique_visitors
            FROM site_page_views
            GROUP BY path
            ORDER BY views DESC, path ASC
            LIMIT 5
            """
        ).fetchall()

    return {
        "total_views": int((summary_row["total_views"] or 0) if summary_row else 0),
        "total_unique": int((summary_row["total_unique"] or 0) if summary_row else 0),
        "today_views": int((summary_row["today_views"] or 0) if summary_row else 0),
        "today_unique": int((summary_row["today_unique"] or 0) if summary_row else 0),
        "week_views": int((summary_row["week_views"] or 0) if summary_row else 0),
        "week_unique": int((summary_row["week_unique"] or 0) if summary_row else 0),
        "top_pages": [dict(row) for row in top_pages],
    }


def get_current_tournament() -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute(
            """
            SELECT *
            FROM tournaments
            WHERE type = 'weekly'
              AND status NOT IN ('cancelled')
            ORDER BY
              CASE WHEN status != 'completed' THEN 0 ELSE 1 END,
              id DESC
            LIMIT 1
            """
        ).fetchone()
        return row_to_dict(row)


def get_tournament_hub(limit: int = 12) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              t.*,
              (
                SELECT COUNT(*)
                FROM tournament_entries te
                WHERE te.tournament_id = t.id
                  AND te.status IN ('confirmed', 'replacement_in')
              ) AS confirmed_count,
              (
                SELECT COUNT(*)
                FROM tournament_entries te
                WHERE te.tournament_id = t.id
                  AND te.status = 'registered'
              ) AS registered_count,
              (
                SELECT COUNT(*)
                FROM tournament_entries te
                WHERE te.tournament_id = t.id
                  AND te.status = 'waitlist'
              ) AS waitlist_count,
              (
                SELECT ptr.display_name
                FROM player_tournament_results ptr
                WHERE ptr.tournament_id = t.id
                  AND ptr.final_rank = 1
                ORDER BY ptr.id DESC
                LIMIT 1
              ) AS champion_name
            FROM tournaments t
            WHERE t.status != 'cancelled'
            ORDER BY
              CASE WHEN t.status != 'completed' THEN 0 ELSE 1 END,
              t.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_count_by_status(tournament_id: int, status: str) -> int:
    with get_db() as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total
            FROM tournament_entries
            WHERE tournament_id = ?
              AND status = ?
            """,
            (tournament_id, status),
        ).fetchone()
        return int(row["total"] or 0)


def get_confirmed_players(tournament_id: int) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              te.*,
              pp.avatar_url,
              COALESCE(ps.championships, 0) AS championships,
              (
                SELECT sm.donor_tier
                FROM supporter_memberships sm
                WHERE sm.user_id = te.user_id
                  AND sm.donor_expires_at IS NOT NULL
                  AND sm.donor_expires_at > CURRENT_TIMESTAMP
                ORDER BY sm.updated_at DESC
                LIMIT 1
              ) AS donor_tier,
              (
                SELECT sm.sponsor_tier
                FROM supporter_memberships sm
                WHERE sm.user_id = te.user_id
                  AND sm.sponsor_expires_at IS NOT NULL
                  AND sm.sponsor_expires_at > CURRENT_TIMESTAMP
                ORDER BY sm.updated_at DESC
                LIMIT 1
              ) AS sponsor_tier
            FROM tournament_entries te
            LEFT JOIN player_profiles pp
              ON pp.user_id = te.user_id
            LEFT JOIN player_stats ps
              ON ps.user_id = te.user_id
            WHERE tournament_id = ?
              AND status IN ('confirmed', 'replacement_in')
            ORDER BY register_order ASC
            """,
            (tournament_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_sponsors(tournament_id: int, sponsor_kind: str = "tournament") -> list[dict[str, Any]]:
    if not table_exists("sponsors"):
        return []

    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              s.id,
              s.tournament_id,
              COALESCE(s.sponsor_kind, 'tournament') AS sponsor_kind,
              s.sponsor_name AS display_name,
              s.sponsor_user_id,
              s.amount,
              s.note,
              s.logo_url,
              s.website_url,
              s.display_tier,
              s.is_active,
              s.created_by,
              s.created_at,
              pp.avatar_url
            FROM sponsors s
            LEFT JOIN player_profiles pp
              ON pp.user_id = s.sponsor_user_id
            WHERE s.tournament_id = ?
              AND COALESCE(s.sponsor_kind, 'tournament') = ?
              AND COALESCE(s.amount, 0) > 0
              AND COALESCE(s.is_active, 1) = 1
            ORDER BY s.amount DESC, s.id ASC
            """,
            (tournament_id, sponsor_kind),
        ).fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            item["image_url"] = item.get("logo_url") or item.get("avatar_url")
        return items


def get_partner_sponsors(tournament_id: int) -> list[dict[str, Any]]:
    return get_sponsors(tournament_id, sponsor_kind="partner")


def get_all_sponsors_admin(tournament_id: int) -> list[dict[str, Any]]:
    if not table_exists("sponsors"):
        return []

    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              s.id,
              s.tournament_id,
              COALESCE(s.sponsor_kind, 'tournament') AS sponsor_kind,
              s.sponsor_name,
              s.sponsor_user_id,
              s.amount,
              s.note,
              s.logo_url,
              s.website_url,
              s.display_tier,
              COALESCE(s.is_active, 1) AS is_active,
              s.created_by,
              s.created_at,
              pp.avatar_url
            FROM sponsors s
            LEFT JOIN player_profiles pp
              ON pp.user_id = s.sponsor_user_id
            WHERE s.tournament_id = ?
            ORDER BY COALESCE(s.is_active, 1) DESC, s.amount DESC, s.id ASC
            """,
            (tournament_id,),
        ).fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            item["image_url"] = item.get("logo_url") or item.get("avatar_url")
        return items


def save_sponsor_admin(
    sponsor_id: int | None,
    tournament_id: int,
    sponsor_kind: str,
    sponsor_name: str,
    amount: int,
    note: str,
    sponsor_user_id: int | None,
    logo_url: str,
    website_url: str,
    display_tier: str,
    is_active: bool,
) -> None:
    with get_db() as db:
        if sponsor_id:
            db.execute(
                """
                UPDATE sponsors
                SET sponsor_name = ?,
                    sponsor_kind = ?,
                    sponsor_user_id = ?,
                    amount = ?,
                    note = ?,
                    logo_url = ?,
                    website_url = ?,
                    display_tier = ?,
                    is_active = ?
                WHERE id = ?
                """,
                (
                    sponsor_name,
                    sponsor_kind,
                    sponsor_user_id,
                    amount,
                    note,
                    logo_url,
                    website_url,
                    display_tier,
                    1 if is_active else 0,
                    sponsor_id,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO sponsors (
                    tournament_id,
                    sponsor_kind,
                    sponsor_name,
                    sponsor_user_id,
                    amount,
                    note,
                    logo_url,
                    website_url,
                    display_tier,
                    is_active,
                    created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    tournament_id,
                    sponsor_kind,
                    sponsor_name,
                    sponsor_user_id,
                    amount,
                    note,
                    logo_url,
                    website_url,
                    display_tier,
                    1 if is_active else 0,
                ),
            )
        db.commit()


def delete_sponsor_admin(sponsor_id: int) -> None:
    with get_db() as db:
        db.execute("DELETE FROM sponsors WHERE id = ?", (sponsor_id,))
        db.commit()


def get_all_announcements_admin(tournament_id: int | None) -> list[dict[str, Any]]:
    if not table_exists("announcements"):
        return []

    with get_db() as db:
        if tournament_id is None:
            rows = db.execute(
                """
                SELECT
                  a.*,
                  t.title AS tournament_title
                FROM announcements a
                LEFT JOIN tournaments t
                  ON t.id = a.tournament_id
                WHERE a.announcement_type = 'sponsor'
                ORDER BY
                  CASE
                    WHEN a.status = 'scheduled' THEN 0
                    WHEN a.status = 'queued' THEN 1
                    WHEN a.status = 'published' THEN 2
                    ELSE 3
                  END,
                  a.id DESC
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT
                  a.*,
                  t.title AS tournament_title
                FROM announcements a
                LEFT JOIN tournaments t
                  ON t.id = a.tournament_id
                WHERE COALESCE(a.tournament_id, 0) = ?
                ORDER BY
                  CASE
                    WHEN a.status = 'scheduled' THEN 0
                    WHEN a.status = 'queued' THEN 1
                    WHEN a.status = 'published' THEN 2
                    ELSE 3
                  END,
                  a.id DESC
                """,
                (tournament_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def save_announcement_admin(
    announcement_id: int | None,
    guild_id: int,
    tournament_id: int | None,
    title: str,
    body: str,
    badge: str,
    button_text: str,
    button_url: str,
    image_url: str,
    target_channel: str,
    repeat_hours: int,
    end_at: str,
    publish_now: bool,
) -> None:
    status = "queued" if publish_now else "draft"
    target_channel = str(target_channel or "general-chat").strip() or "general-chat"
    next_publish_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if publish_now else None
    normalized_end_at = f"{end_at} 23:59:59" if end_at else None
    with get_db() as db:
        if announcement_id:
            row = db.execute(
                "SELECT status, published_message_id, published_channel_id, published_at, publish_count FROM announcements WHERE id = ?",
                (announcement_id,),
            ).fetchone()
            db.execute(
                """
                UPDATE announcements
                SET guild_id = ?,
                    tournament_id = ?,
                    announcement_type = 'sponsor',
                    title = ?,
                    body = ?,
                    badge = ?,
                    button_text = ?,
                    button_url = ?,
                    image_url = ?,
                    target_channel = ?,
                    status = ?,
                    repeat_hours = ?,
                    max_publishes = 0,
                    next_publish_at = ?,
                    end_at = ?,
                    publish_count = CASE WHEN ? = 'queued' THEN 0 ELSE COALESCE(publish_count, 0) END
                WHERE id = ?
                """,
                (
                    guild_id,
                    tournament_id,
                    title,
                    body,
                    badge,
                    button_text,
                    button_url,
                    image_url,
                    target_channel,
                    status,
                    repeat_hours,
                    next_publish_at,
                    normalized_end_at,
                    status,
                    announcement_id,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO announcements (
                    guild_id,
                    tournament_id,
                    announcement_type,
                    title,
                    body,
                    badge,
                    button_text,
                    button_url,
                    image_url,
                    target_channel,
                    status,
                    repeat_hours,
                    publish_count,
                    max_publishes,
                    next_publish_at,
                    end_at
                )
                VALUES (?, ?, 'sponsor', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    guild_id,
                    tournament_id,
                    title,
                    body,
                    badge,
                    button_text,
                    button_url,
                    image_url,
                    target_channel,
                    status,
                    repeat_hours,
                    next_publish_at,
                    normalized_end_at,
                ),
            )
        db.commit()


def delete_announcement_admin(announcement_id: int) -> None:
    with get_db() as db:
        db.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
        db.commit()


def queue_tournament_admin_action(
    *,
    guild_id: int,
    tournament_id: int,
    action: str,
    requested_by: int = 0,
) -> None:
    with get_db() as db:
        existing = db.execute(
            """
            SELECT id
            FROM tournament_admin_actions
            WHERE guild_id = ?
              AND tournament_id = ?
              AND action = ?
              AND status IN ('queued', 'processing')
            ORDER BY id DESC
            LIMIT 1
            """,
            (guild_id, tournament_id, action),
        ).fetchone()
        if existing is not None:
            return

        db.execute(
            """
            INSERT INTO tournament_admin_actions (
                guild_id,
                tournament_id,
                action,
                status,
                requested_by
            )
            VALUES (?, ?, ?, 'queued', ?)
            """,
            (guild_id, tournament_id, action, requested_by),
        )
        db.commit()


def queue_registration_ui_refresh_for_tournament(tournament_id: int) -> None:
    tournament = get_tournament_by_id(int(tournament_id))
    if tournament is None:
        return
    guild_id = int(tournament.get("guild_id") or 0)
    if guild_id <= 0:
        return
    queue_tournament_admin_action(
        guild_id=guild_id,
        tournament_id=int(tournament_id),
        action="refresh_registration_ui",
        requested_by=0,
    )


def get_tournament_entry_by_id(entry_id: int) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute(
            """
            SELECT *
            FROM tournament_entries
            WHERE id = ?
            LIMIT 1
            """,
            (entry_id,),
        ).fetchone()
        return row_to_dict(row)


def is_registration_stage_status(status: str) -> bool:
    return str(status or "").strip().lower() in {"registration_open", "registration_locked"}


def tournament_has_bracket(tournament_id: int) -> bool:
    with get_db() as db:
        row = db.execute(
            """
            SELECT 1
            FROM stages
            WHERE tournament_id = ?
            LIMIT 1
            """,
            (tournament_id,),
        ).fetchone()
        return row is not None


def admin_confirm_tournament_entry(tournament_id: int, entry_id: int) -> tuple[bool, str]:
    tournament = get_tournament_by_id(int(tournament_id))
    if tournament is None:
        return False, "Tournament олдсонгүй."
    if not is_registration_stage_status(str(tournament.get("status") or "")):
        return False, "Registration stage биш тул confirm хийх боломжгүй."
    if tournament_has_bracket(int(tournament_id)):
        return False, "Bracket үүссэн тул web дээрээс confirm хийхгүй. Discord replace flow ашиглана."

    entry = get_tournament_entry_by_id(int(entry_id))
    if entry is None or int(entry.get("tournament_id") or 0) != int(tournament_id):
        return False, "Entry олдсонгүй."
    if str(entry.get("status") or "") not in {"registered", "waitlist"}:
        return False, "Энэ entry-г confirm хийх боломжгүй төлөвтэй байна."

    with get_db() as db:
        confirmed_row = db.execute(
            """
            SELECT COUNT(*) AS total
            FROM tournament_entries
            WHERE tournament_id = ?
              AND status = 'confirmed'
            """,
            (tournament_id,),
        ).fetchone()
        confirmed_count = int((confirmed_row["total"] or 0) if confirmed_row else 0)
        max_players = int(tournament.get("max_players") or 32)
        if confirmed_count >= max_players:
            return False, "Confirmed суудал дүүрсэн байна. Эхлээд confirmed player хасна уу."

        db.execute(
            """
            UPDATE tournament_entries
            SET payment_status = 'confirmed',
                status = 'confirmed',
                confirmed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (entry_id,),
        )
        db.commit()

    queue_registration_ui_refresh_for_tournament(int(tournament_id))
    return True, "Player confirmed боллоо."


def admin_unconfirm_tournament_entry(tournament_id: int, entry_id: int) -> tuple[bool, str]:
    tournament = get_tournament_by_id(int(tournament_id))
    if tournament is None:
        return False, "Tournament олдсонгүй."
    if not is_registration_stage_status(str(tournament.get("status") or "")):
        return False, "Registration stage биш тул confirmed-ээс буцаах боломжгүй."
    if tournament_has_bracket(int(tournament_id)):
        return False, "Bracket үүссэн тул web дээрээс confirmed-ээс буцаахгүй."

    entry = get_tournament_entry_by_id(int(entry_id))
    if entry is None or int(entry.get("tournament_id") or 0) != int(tournament_id):
        return False, "Entry олдсонгүй."
    if str(entry.get("status") or "") != "confirmed":
        return False, "Энэ entry confirmed төлөвтэй биш байна."

    with get_db() as db:
        db.execute(
            """
            UPDATE tournament_entries
            SET status = 'waitlist'
            WHERE id = ?
            """,
            (entry_id,),
        )
        db.commit()

    queue_registration_ui_refresh_for_tournament(int(tournament_id))
    return True, "Confirmed player waiting list рүү буцлаа."


def admin_remove_tournament_entry(tournament_id: int, entry_id: int) -> tuple[bool, str]:
    tournament = get_tournament_by_id(int(tournament_id))
    if tournament is None:
        return False, "Tournament олдсонгүй."
    if not is_registration_stage_status(str(tournament.get("status") or "")):
        return False, "Registration stage биш тул remove хийх боломжгүй."
    if tournament_has_bracket(int(tournament_id)):
        return False, "Bracket үүссэн тул web дээрээс remove хийхгүй."

    entry = get_tournament_entry_by_id(int(entry_id))
    if entry is None or int(entry.get("tournament_id") or 0) != int(tournament_id):
        return False, "Entry олдсонгүй."
    if str(entry.get("status") or "") not in {"registered", "waitlist", "confirmed", "replacement_in"}:
        return False, "Энэ entry-г remove хийх боломжгүй төлөвтэй байна."

    with get_db() as db:
        db.execute("DELETE FROM tournament_entries WHERE id = ?", (entry_id,))
        db.commit()

    queue_registration_ui_refresh_for_tournament(int(tournament_id))
    return True, "Player tournament-оос хасагдлаа."


def get_waiting_players(tournament_id: int) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              te.*,
              pp.avatar_url
            FROM tournament_entries te
            LEFT JOIN player_profiles pp
              ON pp.user_id = te.user_id
            WHERE tournament_id = ?
              AND status IN ('registered', 'waitlist')
            ORDER BY register_order ASC, id ASC
            """,
            (tournament_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_sponsor_total(tournament_id: int) -> int:
    if not table_exists("sponsors"):
        return 0

    with get_db() as db:
        row = db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM sponsors
            WHERE tournament_id = ?
              AND COALESCE(sponsor_kind, 'tournament') = 'tournament'
            """,
            (tournament_id,),
        ).fetchone()
        return int(row["total"] or 0)


def get_leaderboard(limit: int = 10) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              pp.display_name,
              pp.user_id,
              pp.avatar_url,
              COALESCE(ps.tournaments_played, 0) AS tournaments_played,
              COALESCE(ps.weekly_played, 0) AS weekly_played,
              COALESCE(ps.championships, 0) AS championships,
              COALESCE(ps.runner_ups, 0) AS runner_ups,
              COALESCE(ps.third_places, 0) AS third_places,
              COALESCE(ps.podiums, 0) AS podiums
            FROM player_profiles pp
            LEFT JOIN player_stats ps
              ON ps.user_id = pp.user_id
            ORDER BY
              COALESCE(ps.championships, 0) DESC,
              COALESCE(ps.podiums, 0) DESC,
              pp.display_name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        payout_rows = db.execute(
            """
            SELECT tournament_id, user_id, final_rank
            FROM player_tournament_results
            WHERE final_rank BETWEEN 1 AND 3
            """
        ).fetchall()

    result_rows = [dict(r) for r in rows]
    payout_cache: dict[int, dict[int, int]] = {}
    total_prize_by_user: dict[int, int] = {}
    for payout_row in payout_rows:
        tournament_id = int(payout_row["tournament_id"] or 0)
        user_id = int(payout_row["user_id"] or 0)
        final_rank = int(payout_row["final_rank"] or 0)
        if tournament_id <= 0 or user_id <= 0 or final_rank not in (1, 2, 3):
            continue
        payout_map = payout_cache.setdefault(tournament_id, get_tournament_payout_map(tournament_id))
        total_prize_by_user[user_id] = total_prize_by_user.get(user_id, 0) + int(payout_map.get(final_rank, 0))

    for row in result_rows:
        row["total_prize_money"] = int(total_prize_by_user.get(int(row.get("user_id") or 0), 0))

    result_rows.sort(
        key=lambda row: (
            -int(row.get("championships") or 0),
            -int(row.get("podiums") or 0),
            -int(row.get("total_prize_money") or 0),
            str(row.get("display_name") or "").lower(),
        )
    )
    return result_rows[:limit]


def get_stage_groups(tournament_id: int) -> list[dict[str, Any]]:
    with get_db() as db:
        stage_rows = db.execute(
            """
            SELECT *
            FROM stages
            WHERE tournament_id = ?
            ORDER BY round_order ASC, id ASC
            """,
            (tournament_id,),
        ).fetchall()

        groups: list[dict[str, Any]] = []

        for stage in stage_rows:
            stage_dict = dict(stage)

            host_name = "-"
            if stage_dict.get("host_user_id"):
                host_row = db.execute(
                    """
                    SELECT display_name
                    FROM tournament_entries
                    WHERE tournament_id = ? AND user_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (tournament_id, stage_dict["host_user_id"]),
                ).fetchone()
                if host_row:
                    host_name = str(host_row["display_name"])

            slot_rows = db.execute(
                """
                SELECT
                  ss.slot_no,
                  ss.total_points,
                  ss.final_position,
                  te.display_name,
                  te.user_id,
                  pp.avatar_url,
                  COALESCE(ps.championships, 0) AS championships,
                  (
                    SELECT sm.donor_tier
                    FROM supporter_memberships sm
                    WHERE sm.user_id = te.user_id
                      AND sm.donor_expires_at IS NOT NULL
                      AND sm.donor_expires_at > CURRENT_TIMESTAMP
                    ORDER BY sm.updated_at DESC
                    LIMIT 1
                  ) AS donor_tier,
                  (
                    SELECT sm.sponsor_tier
                    FROM supporter_memberships sm
                    WHERE sm.user_id = te.user_id
                      AND sm.sponsor_expires_at IS NOT NULL
                      AND sm.sponsor_expires_at > CURRENT_TIMESTAMP
                    ORDER BY sm.updated_at DESC
                    LIMIT 1
                  ) AS sponsor_tier
                FROM stage_slots ss
                JOIN tournament_entries te
                  ON te.id = ss.current_entry_id
                LEFT JOIN player_profiles pp
                  ON pp.user_id = te.user_id
                LEFT JOIN player_stats ps
                  ON ps.user_id = te.user_id
                WHERE ss.stage_id = ?
                ORDER BY ss.slot_no ASC
                """,
                (stage_dict["id"],),
            ).fetchall()

            players = []
            for row in slot_rows:
                row = dict(row)
                extra = f"Total {row['total_points']}"
                if row["final_position"]:
                    extra += f" · Rank {row['final_position']}"
                players.append(
                    {
                        "slot_no": row["slot_no"],
                        "display_name": row["display_name"],
                        "user_id": row["user_id"],
                        "avatar_url": row["avatar_url"],
                        "total_points": row["total_points"],
                        "final_position": row["final_position"],
                        "championships": row["championships"],
                        "donor_tier": row["donor_tier"],
                        "sponsor_tier": row["sponsor_tier"],
                        "extra": extra,
                    }
                )

            groups.append(
                {
                    "title": stage_dict["stage_key"].replace("_", " ").upper(),
                    "password": stage_dict.get("lobby_password") or "-",
                    "host_name": host_name,
                    "players": players,
                    "stage_type": stage_dict["stage_type"],
                    "stage_status": stage_dict["status"],
                    "round_order": stage_dict["round_order"],
                }
            )

    return groups


def sort_stage_players_for_display(stage: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not stage:
        return []
    players = list(stage.get("players") or [])
    if not players:
        return []

    stage_status = str(stage.get("stage_status") or "")
    if stage_status == "finished":
        return sorted(
            players,
            key=lambda player: (
                -(int(player.get("total_points") or 0)),
                int(player.get("slot_no") or 999),
            ),
        )
    return players


def get_current_stage_label(stage_groups: list[dict[str, Any]], tournament: dict[str, Any]) -> str:
    if not stage_groups:
        if tournament["status"] == "registration_open":
            return "Registration"
        if tournament["status"] == "registration_locked":
            return "Ready for Zones"
        return str(tournament["status"]).replace("_", " ").title()

    active = [g for g in stage_groups if g["stage_status"] in ("ready", "running")]
    if active:
        return active[-1]["title"]

    finished = [g for g in stage_groups if g["stage_status"] == "finished"]
    if finished:
        return finished[-1]["title"]

    return str(tournament["status"]).replace("_", " ").title()


def get_final_standings(tournament_id: int) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              ss.final_position,
              ss.total_points,
              te.display_name,
              te.user_id,
              COALESCE(ptr.prize_amount, 0) AS prize_amount,
              pp.avatar_url,
              COALESCE(ps.championships, 0) AS championships,
              (
                SELECT sm.donor_tier
                FROM supporter_memberships sm
                WHERE sm.user_id = te.user_id
                  AND sm.donor_expires_at IS NOT NULL
                  AND sm.donor_expires_at > CURRENT_TIMESTAMP
                ORDER BY sm.updated_at DESC
                LIMIT 1
              ) AS donor_tier,
              (
                SELECT sm.sponsor_tier
                FROM supporter_memberships sm
                WHERE sm.user_id = te.user_id
                  AND sm.sponsor_expires_at IS NOT NULL
                  AND sm.sponsor_expires_at > CURRENT_TIMESTAMP
                ORDER BY sm.updated_at DESC
                LIMIT 1
              ) AS sponsor_tier
            FROM stage_slots ss
            JOIN stages s
              ON s.id = ss.stage_id
            JOIN tournament_entries te
              ON te.id = ss.current_entry_id
            LEFT JOIN player_profiles pp
              ON pp.user_id = te.user_id
            LEFT JOIN player_stats ps
              ON ps.user_id = te.user_id
            LEFT JOIN player_tournament_results ptr
              ON ptr.tournament_id = s.tournament_id
             AND ptr.user_id = te.user_id
            WHERE s.tournament_id = ?
              AND s.stage_type = 'final'
              AND ss.final_position IS NOT NULL
            ORDER BY ss.final_position ASC, ss.slot_no ASC
            """,
            (tournament_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_champion_from_history() -> dict[str, Any] | None:
    if not table_exists("player_tournament_results"):
        return None

    with get_db() as db:
        row = db.execute(
            """
            SELECT
              tournament_id,
              user_id,
              display_name,
              season_name,
              tournament_title,
              final_rank,
              prize_amount,
              total_points,
              recorded_at
            FROM player_tournament_results
            WHERE final_rank = 1
            ORDER BY tournament_id DESC
            LIMIT 1
            """
        ).fetchone()
        return row_to_dict(row)


def get_tournament_history(limit: int = 30) -> list[dict[str, Any]]:
    if not table_exists("player_tournament_results"):
        return []

    season_enabled = column_exists("tournaments", "season_name")

    with get_db() as db:
        if season_enabled:
            tournaments = db.execute(
                """
                SELECT id, title, season_name
                FROM tournaments
                WHERE type = 'weekly' AND status = 'completed'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            tournaments = db.execute(
                """
                SELECT id, title, 'Season 1' AS season_name
                FROM tournaments
                WHERE type = 'weekly' AND status = 'completed'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        items: list[dict[str, Any]] = []
        for t in tournaments:
            payout_map = get_tournament_payout_map(int(t["id"]))
            podium_rows = db.execute(
                """
                SELECT
                  user_id,
                  display_name,
                  final_rank,
                  prize_amount,
                  total_points
                FROM player_tournament_results
                WHERE tournament_id = ?
                  AND final_rank <= 3
                ORDER BY final_rank ASC
                """,
                (int(t["id"]),),
            ).fetchall()

            podium: list[dict[str, Any]] = []
            for row in podium_rows:
                item_row = dict(row)
                rank = int(item_row.get("final_rank") or 0)
                item_row["display_prize_amount"] = int(payout_map.get(rank, int(item_row.get("prize_amount") or 0)))
                podium.append(item_row)

            items.append(
                {
                    "tournament_id": int(t["id"]),
                    "tournament_title": str(t["title"]),
                    "season_name": str(t["season_name"]),
                    "podium": podium,
                }
            )
        return items


def get_player_profile(user_id: int) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute(
            """
            SELECT
              pp.user_id,
              pp.display_name,
              pp.avatar_url,
              pp.phone_number,
              pp.bank_account,
              COALESCE(ps.tournaments_played, 0) AS tournaments_played,
              COALESCE(ps.weekly_played, 0) AS weekly_played,
              COALESCE(ps.championships, 0) AS championships,
              COALESCE(ps.runner_ups, 0) AS runner_ups,
              COALESCE(ps.third_places, 0) AS third_places,
              COALESCE(ps.podiums, 0) AS podiums
            FROM player_profiles pp
            LEFT JOIN player_stats ps
              ON ps.user_id = pp.user_id
            WHERE pp.user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        payout_rows = db.execute(
            """
            SELECT tournament_id, final_rank
            FROM player_tournament_results
            WHERE user_id = ?
              AND final_rank BETWEEN 1 AND 3
            """,
            (user_id,),
        ).fetchall()

    data = row_to_dict(row)
    if not data:
        return None

    payout_cache: dict[int, dict[int, int]] = {}
    total_prize_money = 0
    for payout_row in payout_rows:
        tournament_id = int(payout_row["tournament_id"] or 0)
        final_rank = int(payout_row["final_rank"] or 0)
        if tournament_id <= 0 or final_rank not in (1, 2, 3):
            continue
        payout_map = payout_cache.setdefault(tournament_id, get_tournament_payout_map(tournament_id))
        total_prize_money += int(payout_map.get(final_rank, 0))

    data["total_prize_money"] = int(total_prize_money)
    return data


def get_contact_moderators() -> list[dict[str, Any]]:
    moderator_ids = parse_contact_moderator_ids(CONTACT_MODERATOR_IDS)
    moderator_emails = parse_contact_moderator_emails(CONTACT_MODERATOR_EMAILS)
    moderators: list[dict[str, Any]] = []
    for index, user_id in enumerate(moderator_ids, start=1):
        profile = get_player_profile(user_id)
        row = dict(profile) if profile is not None else {}
        display_name = str(row.get("display_name") or "").strip() or f"Moderator {index}"
        row["user_id"] = user_id
        row["display_name"] = display_name
        row["avatar_url"] = str(row.get("avatar_url") or "").strip()
        row["contact_email"] = moderator_emails.get(user_id) or CONTACT_EMAIL
        row["contact_phone"] = str(row.get("phone_number") or "").strip()
        row["player_url"] = f"/player/{user_id}" if profile is not None else ""
        row["label"] = "Moderator"
        row["profile_ready"] = profile is not None
        moderators.append(row)
    return moderators


def get_tournament_by_id(tournament_id: int) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute(
            """
            SELECT *
            FROM tournaments
            WHERE id = ?
            LIMIT 1
            """,
            (tournament_id,),
        ).fetchone()
        data = row_to_dict(row)
        if data:
            payout_map = get_tournament_payout_map(int(data.get("tournament_id") or 0))
            data["display_prize_amount"] = int(payout_map.get(1, int(data.get("prize_amount") or 0)))
        return data


def _slugify(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return lowered.strip("-")


def _get_next_admin_weekly_season_name(guild_id: int) -> str:
    with get_db() as db:
        row = db.execute(
            """
            SELECT season_name
            FROM tournaments
            WHERE guild_id = ?
              AND type = 'weekly'
              AND COALESCE(game_key, 'autochess') = 'autochess'
              AND COALESCE(format_key, 'solo_32') = 'solo_32'
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


def format_admin_datetime_local(raw_value: str | None) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return ""


def normalize_admin_schedule_value(raw_value: str | None) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return value


def get_env_guild_id() -> int:
    raw_value = str(os.getenv("GUILD_ID", "0")).strip()
    try:
        return int(raw_value)
    except ValueError:
        return 0


def create_tournament_admin(
    *,
    guild_id: int,
    title: str,
    entry_fee: int,
    start_time: str,
    checkin_time: str,
    prize_total: int,
    prize_1: int,
    prize_2: int,
    prize_3: int,
) -> int:
    season_name = _get_next_admin_weekly_season_name(guild_id)
    slug = _slugify(f"autochess-{season_name}-{title}")
    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO tournaments (
                guild_id, type, game_key, format_key, slug, title, season_name,
                entry_fee, max_players, lobby_size, bo_count, start_time, checkin_time,
                prize_total, prize_1, prize_2, prize_3, status, created_by
            )
            VALUES (?, 'weekly', 'autochess', 'solo_32', ?, ?, ?, ?, 32, 8, 2, ?, ?, ?, ?, ?, ?, 'registration_open', 0)
            """,
            (
                guild_id,
                slug,
                title,
                season_name,
                entry_fee,
                start_time or None,
                checkin_time or None,
                prize_total,
                prize_1,
                prize_2,
                prize_3,
            ),
        )
        db.commit()
        return int(cursor.lastrowid)


def create_web_registration_request(
    tournament_id: int,
    discord_user_id: int,
    display_name: str,
    phone_number: str | None = None,
    bank_account: str | None = None,
) -> tuple[bool, str]:
    display_name = str(display_name or "").strip()
    if not display_name:
        return False, "Discord нэрээ оруулна уу."

    if discord_user_id <= 0:
        return False, "Discord user ID буруу байна."

    with get_db() as db:
        profile = db.execute(
            """
            SELECT phone_number, bank_account
            FROM player_profiles
            WHERE user_id = ?
            LIMIT 1
            """,
            (discord_user_id,),
        ).fetchone()

        clean_phone = re.sub(
            r"[^\d+]",
            "",
            str(phone_number or (profile["phone_number"] if profile else "") or "").strip(),
        )
        if not clean_phone:
            return False, "Утасны дугаараа оруулна уу."

        clean_account = str(
            bank_account or (profile["bank_account"] if profile else "") or ""
        ).strip()
        if not clean_account:
            return False, "Дансны дугаараа оруулна уу."

        tournament = db.execute(
            """
            SELECT *
            FROM tournaments
            WHERE id = ?
            LIMIT 1
            """,
            (tournament_id,),
        ).fetchone()

        if tournament is None:
            return False, "Tournament олдсонгүй."

        if str(tournament["status"]) != "registration_open":
            return False, "Бүртгэл одоогоор хаалттай байна."

        existing = db.execute(
            """
            SELECT status
            FROM tournament_entries
            WHERE tournament_id = ? AND user_id = ?
            LIMIT 1
            """,
            (tournament_id, discord_user_id),
        ).fetchone()
        if existing is not None:
            return False, "Та энэ tournament дээр аль хэдийн хүсэлт илгээсэн байна."

        next_order_row = db.execute(
            """
            SELECT COALESCE(MAX(register_order), 0) + 1 AS next_order
            FROM tournament_entries
            WHERE tournament_id = ?
            """,
            (tournament_id,),
        ).fetchone()
        next_order = int(next_order_row["next_order"] or 1)

        db.execute(
            """
            INSERT INTO tournament_entries (
                tournament_id,
                user_id,
                display_name,
                register_order,
                payment_status,
                status,
                source
            )
            VALUES (?, ?, ?, ?, 'unpaid', 'waitlist', 'web')
            """,
            (tournament_id, discord_user_id, display_name, next_order),
        )
        db.execute(
            """
            INSERT INTO player_profiles (user_id, display_name, phone_number, bank_account)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name = excluded.display_name,
                phone_number = excluded.phone_number,
                bank_account = excluded.bank_account,
                updated_at = CURRENT_TIMESTAMP
            """,
            (discord_user_id, display_name, clean_phone, clean_account),
        )
        db.commit()

    queue_registration_ui_refresh_for_tournament(int(tournament_id))
    return True, "Хүсэлт амжилттай илгээгдлээ. Discord дээр waiting/registration мэдээлэл шинэчлэгдэнэ."


def discord_oauth_enabled() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI)


def get_logged_in_discord_user() -> dict[str, Any] | None:
    user = session.get("discord_user")
    return user if isinstance(user, dict) else None


def upsert_player_profile_basic(user_id: int, display_name: str, avatar_url: str | None) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO player_profiles (user_id, display_name, avatar_url)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name = excluded.display_name,
                avatar_url = COALESCE(excluded.avatar_url, player_profiles.avatar_url),
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(user_id), display_name, avatar_url),
        )
        db.commit()


def get_discord_oauth_authorize_url() -> str:
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": DISCORD_OAUTH_SCOPE,
        "prompt": "none",
        "state": session.get("discord_oauth_state", ""),
    }
    return f"https://discord.com/oauth2/authorize?{urlencode(params)}"


def exchange_discord_code_for_token(code: str) -> dict[str, Any]:
    payload = urlencode(
        {
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
        }
    ).encode("utf-8")
    req = Request(
        "https://discord.com/api/oauth2/token",
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "ChessOfMongoliaBot/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_discord_identity(access_token: str) -> dict[str, Any]:
    req = Request(
        "https://discord.com/api/users/@me",
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "ChessOfMongoliaBot/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def build_discord_avatar_url(user_data: dict[str, Any]) -> str | None:
    user_id = str(user_data.get("id") or "").strip()
    avatar = str(user_data.get("avatar") or "").strip()
    if not user_id or not avatar:
        return None
    ext = "gif" if avatar.startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=256"


def get_player_support_status(user_id: int) -> dict[str, Any] | None:
    if not table_exists("supporter_memberships"):
        return None

    with get_db() as db:
        row = db.execute(
            """
            SELECT
              donor_tier,
              donor_expires_at,
              sponsor_tier,
              sponsor_expires_at
            FROM supporter_memberships
            WHERE user_id = ?
              AND (
                (donor_expires_at IS NOT NULL AND donor_expires_at > CURRENT_TIMESTAMP)
                OR
                (sponsor_expires_at IS NOT NULL AND sponsor_expires_at > CURRENT_TIMESTAMP)
              )
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row is not None:
            return row_to_dict(row)

        fallback = db.execute(
            """
            SELECT
              ? AS user_id,
              COALESCE(
                (
                  SELECT te.display_name
                  FROM tournament_entries te
                  WHERE te.user_id = ?
                  ORDER BY te.id DESC
                  LIMIT 1
                ),
                (
                  SELECT ptr.display_name
                  FROM player_tournament_results ptr
                  WHERE ptr.user_id = ?
                  ORDER BY ptr.id DESC
                  LIMIT 1
                ),
                (
                  SELECT pd.donor_name
                  FROM platform_donations pd
                  WHERE pd.donor_user_id = ?
                  ORDER BY pd.id DESC
                  LIMIT 1
                ),
                (
                  SELECT s.sponsor_name
                  FROM sponsors s
                  WHERE s.sponsor_user_id = ?
                  ORDER BY s.id DESC
                  LIMIT 1
                )
              ) AS display_name,
              NULL AS avatar_url,
              NULL AS phone_number,
              NULL AS bank_account,
              COALESCE(ps.tournaments_played, 0) AS tournaments_played,
              COALESCE(ps.weekly_played, 0) AS weekly_played,
              COALESCE(ps.championships, 0) AS championships,
              COALESCE(ps.runner_ups, 0) AS runner_ups,
              COALESCE(ps.third_places, 0) AS third_places,
              COALESCE(ps.podiums, 0) AS podiums,
              COALESCE(ps.total_prize_money, 0) AS total_prize_money
            FROM player_stats ps
            WHERE ps.user_id = ?
            LIMIT 1
            """,
            (user_id, user_id, user_id, user_id, user_id, user_id),
        ).fetchone()

        if fallback is not None and fallback["display_name"]:
            return row_to_dict(fallback)

        fallback_name_only = db.execute(
            """
            SELECT
              ? AS user_id,
              COALESCE(
                (
                  SELECT te.display_name
                  FROM tournament_entries te
                  WHERE te.user_id = ?
                  ORDER BY te.id DESC
                  LIMIT 1
                ),
                (
                  SELECT ptr.display_name
                  FROM player_tournament_results ptr
                  WHERE ptr.user_id = ?
                  ORDER BY ptr.id DESC
                  LIMIT 1
                ),
                (
                  SELECT pd.donor_name
                  FROM platform_donations pd
                  WHERE pd.donor_user_id = ?
                  ORDER BY pd.id DESC
                  LIMIT 1
                ),
                (
                  SELECT s.sponsor_name
                  FROM sponsors s
                  WHERE s.sponsor_user_id = ?
                  ORDER BY s.id DESC
                  LIMIT 1
                )
              ) AS display_name
            """,
            (user_id, user_id, user_id, user_id, user_id),
        ).fetchone()

        if fallback_name_only is None or not fallback_name_only["display_name"]:
            return None

        return {
            "user_id": user_id,
            "display_name": str(fallback_name_only["display_name"]),
            "avatar_url": None,
            "phone_number": None,
            "bank_account": None,
            "tournaments_played": 0,
            "weekly_played": 0,
            "championships": 0,
            "runner_ups": 0,
            "third_places": 0,
            "podiums": 0,
            "total_prize_money": 0,
        }


def get_player_history(user_id: int) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              t.id AS tournament_id,
              COALESCE(ptr.season_name, t.season_name, 'Season 1') AS season_name,
              COALESCE(ptr.tournament_title, t.title) AS tournament_title,
              ptr.final_rank,
              COALESCE(ptr.prize_amount, 0) AS prize_amount,
              COALESCE(ptr.total_points, 0) AS total_points,
              COALESCE(ptr.recorded_at, te.joined_at, t.created_at) AS recorded_at,
              CASE WHEN ptr.id IS NULL THEN 0 ELSE 1 END AS has_result
            FROM tournament_entries te
            JOIN tournaments t
              ON t.id = te.tournament_id
            LEFT JOIN player_tournament_results ptr
              ON ptr.tournament_id = te.tournament_id
             AND ptr.user_id = te.user_id
            WHERE te.user_id = ?
              AND t.type = 'weekly'
            GROUP BY
              t.id,
              COALESCE(ptr.season_name, t.season_name, 'Season 1'),
              COALESCE(ptr.tournament_title, t.title),
              ptr.final_rank,
              ptr.prize_amount,
              ptr.total_points,
              COALESCE(ptr.recorded_at, te.joined_at, t.created_at),
              CASE WHEN ptr.id IS NULL THEN 0 ELSE 1 END
            ORDER BY t.id DESC, has_result DESC, COALESCE(ptr.final_rank, 999) ASC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_platform_donors() -> list[dict[str, Any]]:
    donors: list[dict[str, Any]] = []

    if table_exists("platform_donations"):
        with get_db() as db:
            rows = db.execute(
                """
                SELECT
                  COALESCE(donor_name, 'Anonymous') AS display_name,
                  donor_user_id,
                  COALESCE(amount, 0) AS amount,
                  COALESCE(note, '') AS note,
                  COALESCE(pd.created_at, '') AS created_at,
                  pp.avatar_url
                FROM platform_donations pd
                LEFT JOIN player_profiles pp
                  ON pp.user_id = pd.donor_user_id
                ORDER BY amount DESC, id ASC
                """
            ).fetchall()
            donors = [dict(r) for r in rows]
            for donor in donors:
                display_name = str(donor.get("display_name") or "").strip()
                match = re.fullmatch(r"<@!?(\d+)>", display_name)
                if match:
                    donor["display_name"] = f"Supporter #{match.group(1)[-4:]}"

    elif PLATFORM_DONATIONS_JSON.exists():
        try:
            raw = json.loads(PLATFORM_DONATIONS_JSON.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    donors.append(
                        {
                            "display_name": (
                                f"Supporter #{re.fullmatch(r'<@!?(\\d+)>', str(item.get('display_name') or item.get('name') or '').strip()).group(1)[-4:]}"
                                if re.fullmatch(r"<@!?(\d+)>", str(item.get("display_name") or item.get("name") or "").strip())
                                else str(item.get("display_name") or item.get("name") or "Anonymous")
                            ),
                            "donor_user_id": int(item.get("donor_user_id") or 0),
                            "amount": int(item.get("amount") or 0),
                            "note": str(item.get("note") or ""),
                            "created_at": str(item.get("created_at") or ""),
                        }
                    )
        except (json.JSONDecodeError, OSError, ValueError):
            donors = []

    donors.sort(key=lambda x: (-int(x.get("amount", 0)), x.get("display_name", "")))
    return donors


def get_current_user_registration_status(
    tournament_id: int,
    user_id: int,
) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute(
            """
            SELECT
              id,
              status,
              payment_status,
              register_order,
              source,
              joined_at,
              confirmed_at
            FROM tournament_entries
            WHERE tournament_id = ?
              AND user_id = ?
            LIMIT 1
            """,
            (tournament_id, user_id),
        ).fetchone()
        return row_to_dict(row)


def get_platform_donation_total() -> int:
    return sum(int(x.get("amount", 0)) for x in get_platform_donors())


def get_footer_partners(limit: int = 8) -> list[dict[str, Any]]:
    partners: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    tournament = get_current_tournament()
    if tournament is not None:
        for sponsor in get_partner_sponsors(int(tournament["id"]))[:limit]:
            name = str(sponsor.get("display_name") or sponsor.get("sponsor_name") or "").strip()
            if not name:
                continue
            key = ("partner", name.lower())
            if key in seen:
                continue
            seen.add(key)
            partners.append(
                {
                    "name": name,
                    "avatar_url": sponsor.get("image_url") or sponsor.get("avatar_url"),
                    "kind": "Official Partner",
                    "tone": "gold",
                }
            )
        for sponsor in get_sponsors(int(tournament["id"]))[:limit]:
            name = str(sponsor.get("display_name") or sponsor.get("sponsor_name") or "").strip()
            if not name:
                continue
            key = ("sponsor", name.lower())
            if key in seen:
                continue
            seen.add(key)
            partners.append(
                {
                    "name": name,
                    "avatar_url": sponsor.get("image_url") or sponsor.get("avatar_url"),
                    "kind": "Tournament Sponsor",
                    "tone": "gold",
                }
            )

    if len(partners) < limit:
        for donor in get_platform_donors():
            name = str(donor.get("display_name") or "").strip()
            if not name:
                continue
            key = ("donor", name.lower())
            if key in seen:
                continue
            seen.add(key)
            partners.append(
                {
                    "name": name,
                    "avatar_url": donor.get("avatar_url"),
                    "kind": "Platform Supporter",
                    "tone": "blue",
                }
            )
            if len(partners) >= limit:
                break

    return partners[:limit]


@app.context_processor
def inject_global_template_context():
    return {
        "footer_partners": get_footer_partners(),
    }


def admin_panel_authorized() -> bool:
    if not ADMIN_PANEL_KEY:
        return False
    supplied_key = (
        str(request.args.get("key") or "").strip()
        or str(request.form.get("key") or "").strip()
        or str(session.get("admin_panel_key") or "").strip()
    )
    if supplied_key and secrets.compare_digest(supplied_key, ADMIN_PANEL_KEY):
        session["admin_panel_key"] = supplied_key
        return True
    return False


def resolve_admin_panel_access() -> bool:
    if not ADMIN_PANEL_KEY:
        return False
    supplied_key = (
        str(request.args.get("key") or "").strip()
        or str(request.form.get("key") or "").strip()
        or str(session.get("admin_panel_key") or "").strip()
    )
    if supplied_key and secrets.compare_digest(supplied_key, ADMIN_PANEL_KEY):
        session["admin_panel_key"] = supplied_key
        return True
    return False


def resolve_donate_qr_src() -> str:
    if DONATE_QR_URL:
        return DONATE_QR_URL

    local_path = ASSETS_DIR / DONATE_QR_FILE
    if local_path.exists():
        return f"/media/{DONATE_QR_FILE}"

    return ""


def build_progress_steps(tournament: dict[str, Any], stage_groups: list[dict[str, Any]]) -> list[dict[str, str]]:
    status = str(tournament["status"])
    stage_types = {g["stage_type"]: g["stage_status"] for g in stage_groups}

    return [
        {
            "title": "Registration",
            "subtitle": "Players join and payment gets confirmed",
            "state": "done" if status not in ("registration_open",) else "live",
        },
        {
            "title": "Zones",
            "subtitle": "32 players split into 4 zones of 8",
            "state": "done" if stage_types.get("zone") == "finished" else "live" if stage_types.get("zone") in ("ready", "running") or status == "zones_created" else "",
        },
        {
            "title": "Semifinals",
            "subtitle": "Top 16 continue into 2 semifinal lobbies",
            "state": "done" if stage_types.get("semi") == "finished" else "live" if stage_types.get("semi") in ("ready", "running") or status == "semis_created" else "",
        },
        {
            "title": "Grand Final",
            "subtitle": "Final 8 fight for the trophy",
            "state": "done" if stage_types.get("final") == "finished" or status == "completed" else "live" if stage_types.get("final") in ("ready", "running") or status == "final_created" else "",
        },
        {
            "title": "Completed",
            "subtitle": "Podium decided and leaderboard updated",
            "state": "done" if status == "completed" else "",
        },
    ]


@app.route("/media/<path:filename>")
def media_file(filename: str):
    return send_from_directory(ASSETS_DIR, filename)


@app.route("/")
def index():
    tournament = get_current_tournament()
    tournament_hub = get_tournament_hub(limit=6)
    hub_live = [item for item in tournament_hub if str(item.get("status") or "") != "completed"]
    hub_completed = [item for item in tournament_hub if str(item.get("status") or "") == "completed"]
    live_tournament_count = len(hub_live)
    completed_tournament_count = len(hub_completed)
    hub_total_prize = sum(int(item.get("prize_total") or 0) for item in tournament_hub)
    hub_total_confirmed = sum(int(item.get("confirmed_count") or 0) for item in tournament_hub)
    register_notice = request.args.get("register_notice", "").strip()
    register_ok = request.args.get("register_ok", "").strip() == "1"
    discord_user = get_logged_in_discord_user()
    oauth_enabled = discord_oauth_enabled()

    if tournament is None:
        empty = {
            "title": "Community Tournament Platform",
            "status": "no_tournament",
            "max_players": 32,
            "prize_total": 0,
            "prize_1": 0,
            "prize_2": 0,
            "prize_3": 0,
            "entry_fee": 0,
            "type": "weekly",
            "start_time": None,
            "checkin_time": None,
        }
        return render_template_string(
            HOME_TEMPLATE,
            page_title="Community Tournament Platform",
            css=BASE_CSS,
            script=BASE_SCRIPT,
            tournament=empty,
            confirmed_players=[],
            registered_count=0,
            confirmed_count=0,
            waitlist_count=0,
            sponsors=[],
            sponsor_total=0,
            leaderboard=get_leaderboard(limit=10),
            rules_text=GLOBAL_RULES_TEXT,
            stage_groups=[],
            final_standings=[],
            current_stage="-",
            progress_steps=[],
            champion=get_latest_champion_from_history(),
            champion_prize=0,
            prize_display=build_prize_display(empty, 0),
            tournament_hub=tournament_hub,
            hub_live=hub_live,
            hub_completed=hub_completed,
            live_tournament_count=live_tournament_count,
            completed_tournament_count=completed_tournament_count,
            hub_total_prize=hub_total_prize,
            hub_total_confirmed=hub_total_confirmed,
            register_notice=register_notice,
            register_ok=register_ok,
            discord_user=discord_user,
            current_user_profile=None,
            current_user_registration=None,
            oauth_enabled=oauth_enabled,
            discord_invite_url=DISCORD_INVITE_URL,
            money=money,
            schedule_value=schedule_value,
            tournament_status_label=tournament_status_label,
            registration_status_label=registration_status_label,
            registration_source_label=registration_source_label,
            payment_status_label=payment_status_label,
        )

    tournament_id = int(tournament["id"])
    confirmed_players = get_confirmed_players(tournament_id)
    registered_count = get_count_by_status(tournament_id, "registered")
    waitlist_count = get_count_by_status(tournament_id, "waitlist")
    sponsors = get_sponsors(tournament_id)
    sponsor_total = get_sponsor_total(tournament_id)
    leaderboard = get_leaderboard(limit=10)
    stage_groups = get_stage_groups(tournament_id)
    final_standings = apply_sponsor_bonus_to_final_standings(
        get_final_standings(tournament_id), sponsor_total
    )
    rules_text = GLOBAL_RULES_TEXT
    current_stage = get_current_stage_label(stage_groups, tournament)
    progress_steps = build_progress_steps(tournament, stage_groups)
    current_user_profile = None
    current_user_registration = None
    if discord_user and str(discord_user.get("id") or "").isdigit():
        current_user_profile = get_player_profile(int(discord_user["id"]))
        current_user_registration = get_current_user_registration_status(
            tournament_id,
            int(discord_user["id"]),
        )

    history_champion = get_latest_champion_from_history()
    champion = history_champion if history_champion is not None else (final_standings[0] if final_standings else None)

    if history_champion is not None:
        champion_prize = int(history_champion.get("display_prize_amount") or history_champion.get("prize_amount") or 0)
    else:
        champion_prize = int(final_standings[0].get("display_prize_amount") or final_standings[0].get("prize_amount") or 0) if final_standings else 0

    return render_template_string(
        HOME_TEMPLATE,
        page_title=tournament["title"],
        css=BASE_CSS,
        script=BASE_SCRIPT,
        tournament=tournament,
        confirmed_players=confirmed_players,
        registered_count=registered_count,
        confirmed_count=len(confirmed_players),
        waitlist_count=waitlist_count,
        sponsors=sponsors,
        sponsor_total=sponsor_total,
        leaderboard=leaderboard,
        rules_text=rules_text,
        stage_groups=stage_groups,
        final_standings=final_standings,
        current_stage=current_stage,
        progress_steps=progress_steps,
        champion=champion,
        champion_prize=champion_prize,
        prize_display=build_prize_display(tournament, sponsor_total),
        tournament_hub=tournament_hub,
        hub_live=hub_live,
        hub_completed=hub_completed,
        live_tournament_count=live_tournament_count,
        completed_tournament_count=completed_tournament_count,
        hub_total_prize=hub_total_prize,
        hub_total_confirmed=hub_total_confirmed,
        register_notice=register_notice,
        register_ok=register_ok,
        discord_user=discord_user,
        current_user_profile=current_user_profile,
        current_user_registration=current_user_registration,
        oauth_enabled=oauth_enabled,
        discord_invite_url=DISCORD_INVITE_URL,
        payment_owner_name=DONATE_OWNER_NAME,
        payment_bank_name=DONATE_BANK_NAME,
        payment_account_no=DONATE_ACCOUNT_NO,
        payment_note=DONATE_NOTE,
        money=money,
        schedule_value=schedule_value,
        tournament_status_label=tournament_status_label,
        registration_status_label=registration_status_label,
        registration_source_label=registration_source_label,
        payment_status_label=payment_status_label,
    )


@app.route("/register", methods=["POST"])
def register_request():
    tournament_id_raw = str(request.form.get("tournament_id", "")).strip()
    target_tournament_id = int(tournament_id_raw) if tournament_id_raw.isdigit() else None
    tournament = get_tournament_by_id(target_tournament_id) if target_tournament_id else get_current_tournament()
    if tournament is None:
        return redirect(url_for("index", register_ok=0, register_notice="Идэвхтэй tournament алга."))

    def _redirect_after(ok: bool, message: str):
        if target_tournament_id:
            return redirect(
                url_for(
                    "season_page",
                    tournament_id=target_tournament_id,
                    register_ok=1 if ok else 0,
                    register_notice=message,
                )
            )
        return redirect(url_for("index", register_ok=1 if ok else 0, register_notice=message))

    phone_number = str(request.form.get("phone_number", "")).strip()
    bank_account = str(request.form.get("bank_account", "")).strip()

    if discord_oauth_enabled():
        discord_user = get_logged_in_discord_user()
        if not discord_user:
            return _redirect_after(False, "Эхлээд Discord-оор нэвтэрнэ үү.")
        discord_user_id_raw = str(discord_user.get("id", "")).strip()
        display_name = str(discord_user.get("display_name") or discord_user.get("username") or "").strip()
    else:
        discord_user_id_raw = str(request.form.get("discord_user_id", "")).strip()
        display_name = str(request.form.get("display_name", "")).strip()

        if not discord_user_id_raw.isdigit():
            return _redirect_after(False, "Discord user ID зөв тоогоор оруулна уу.")

    ok, message = create_web_registration_request(
        int(tournament["id"]),
        int(discord_user_id_raw),
        display_name,
        phone_number,
        bank_account,
    )
    return _redirect_after(ok, message)


@app.route("/login/discord")
def login_discord():
    if not discord_oauth_enabled():
        return redirect(url_for("index", register_ok=0, register_notice="Discord OAuth тохируулагдаагүй байна."))
    state = secrets.token_urlsafe(24)
    session["discord_oauth_state"] = state
    return redirect(get_discord_oauth_authorize_url())


@app.route("/auth/discord/callback")
def discord_oauth_callback():
    if not discord_oauth_enabled():
        return redirect(url_for("index", register_ok=0, register_notice="Discord OAuth тохируулагдаагүй байна."))

    error = str(request.args.get("error", "")).strip()
    if error:
        return redirect(url_for("index", register_ok=0, register_notice="Discord нэвтрэлт цуцлагдлаа."))

    state = str(request.args.get("state", "")).strip()
    expected_state = str(session.get("discord_oauth_state", "")).strip()
    if not state or state != expected_state:
        return redirect(url_for("index", register_ok=0, register_notice="OAuth state таарсангүй."))

    code = str(request.args.get("code", "")).strip()
    if not code:
        return redirect(url_for("index", register_ok=0, register_notice="Discord code олдсонгүй."))

    try:
        token_data = exchange_discord_code_for_token(code)
        access_token = str(token_data.get("access_token") or "").strip()
        if not access_token:
            raise ValueError(f"access_token missing: {token_data}")
        identity = fetch_discord_identity(access_token)
    except HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            error_body = ""
        print("Discord OAuth callback HTTPError:", exc.code, error_body)
        traceback.print_exc()
        return redirect(
            url_for(
                "index",
                register_ok=0,
                register_notice=f"Discord нэвтрэлт амжилтгүй боллоо: HTTP {exc.code}",
            )
        )
    except Exception as exc:
        print("Discord OAuth callback failed:", repr(exc))
        traceback.print_exc()
        return redirect(
            url_for(
                "index",
                register_ok=0,
                register_notice=f"Discord нэвтрэлт амжилтгүй боллоо: {type(exc).__name__}",
            )
        )

    discord_user = {
        "id": str(identity.get("id") or ""),
        "username": str(identity.get("username") or ""),
        "display_name": str(identity.get("global_name") or identity.get("username") or ""),
        "avatar_url": build_discord_avatar_url(identity),
    }
    session["discord_user"] = discord_user
    session.pop("discord_oauth_state", None)

    if str(discord_user.get("id") or "").isdigit():
        upsert_player_profile_basic(
            int(discord_user["id"]),
            discord_user["display_name"],
            discord_user.get("avatar_url"),
        )

    return redirect(url_for("index", register_ok=1, register_notice="Discord амжилттай холбогдлоо."))


@app.route("/logout")
def logout():
    session.pop("discord_user", None)
    session.pop("discord_oauth_state", None)
    return redirect(url_for("index", register_ok=1, register_notice="Discord session гарлаа."))


@app.route("/tournaments")
def tournaments_page():
    tournaments = get_tournament_hub(limit=24)
    live_tournaments = [item for item in tournaments if str(item.get("status") or "") != "completed"]
    featured_tournament = tournaments[0] if tournaments else None
    admin_enabled = resolve_admin_panel_access()
    return render_template_string(
        TOURNAMENTS_TEMPLATE,
        css=BASE_CSS,
        script=BASE_SCRIPT,
        tournaments=tournaments,
        live_tournaments=live_tournaments,
        featured_tournament=featured_tournament,
        live_count=sum(1 for item in tournaments if item.get("status") != "completed"),
        completed_count=sum(1 for item in tournaments if item.get("status") == "completed"),
        total_prize_pool=sum(int(item.get("prize_total") or 0) for item in tournaments),
        discord_invite_url=DISCORD_INVITE_URL,
        money=money,
        schedule_value=schedule_value,
        tournament_status_label=tournament_status_label,
        admin_panel_enabled=admin_enabled,
        admin_panel_key=ADMIN_PANEL_KEY,
    )


@app.route("/sponsors")
def sponsors_page():
    if admin_panel_authorized():
        return redirect(url_for("admin_dashboard_page", key=ADMIN_PANEL_KEY))
    return redirect(url_for("donate_page"))


@app.route("/admin/sponsors", methods=["GET", "POST"])
def sponsor_admin_page():
    if not admin_panel_authorized():
        return (
            """
            <!doctype html><html lang="mn"><head><meta charset="utf-8"><title>Sponsor Broadcast Access</title></head>
            <body style="font-family:Segoe UI, Arial, sans-serif; background:#09132a; color:white; padding:40px;">
              <div style="max-width:480px; margin:80px auto; padding:24px; border:1px solid #223a67; border-radius:18px; background:#101b36;">
                <h2 style="margin-top:0;">Sponsor Admin Access</h2>
                <p style="color:#97abd5;">Admin key оруулж sponsor panel-ийг нээнэ үү.</p>
                <form method="get">
                  <input name="key" type="password" placeholder="ADMIN_PANEL_KEY" style="width:100%; padding:12px 14px; border-radius:12px; border:1px solid #365d9d; background:#0b152c; color:white; margin-bottom:12px;">
                  <button type="submit" style="padding:10px 14px; border-radius:12px; border:1px solid #365d9d; background:#16345f; color:white;">Open Panel</button>
                </form>
              </div>
            </body></html>
            """,
            403,
        )

    tournaments = get_tournament_hub(limit=100)
    default_guild_id = int(tournaments[0]["guild_id"]) if tournaments and tournaments[0].get("guild_id") is not None else 0
    selected_tournament_id_raw = str(request.values.get("tournament_id") or "").strip()
    selected_tournament = None
    if selected_tournament_id_raw.isdigit():
        selected_tournament = get_tournament_by_id(int(selected_tournament_id_raw))
    if selected_tournament is None:
        current = get_current_tournament()
        selected_tournament = current if current is not None else (tournaments[0] if tournaments else None)

    if selected_tournament is None:
        return "Tournament олдсонгүй.", 404

    notice = ""
    ok = True

    if request.method == "POST":
        action = str(request.form.get("action") or "").strip().lower()
        sponsor_id_raw = str(request.form.get("sponsor_id") or "").strip()
        sponsor_id = int(sponsor_id_raw) if sponsor_id_raw.isdigit() else None
        selected_tournament = get_tournament_by_id(int(str(request.form.get("tournament_id") or selected_tournament["id"])))

        if action == "delete" and sponsor_id:
            delete_sponsor_admin(sponsor_id)
            notice = "Sponsor deleted."
        elif action == "save":
            sponsor_name = str(request.form.get("sponsor_name") or "").strip()
            sponsor_kind = str(request.form.get("sponsor_kind") or "tournament").strip().lower() or "tournament"
            amount_raw = str(request.form.get("amount") or "").strip()
            sponsor_user_id_raw = str(request.form.get("sponsor_user_id") or "").strip()
            amount = int(amount_raw) if amount_raw.isdigit() else 0
            sponsor_user_id = int(sponsor_user_id_raw) if sponsor_user_id_raw.isdigit() else None
            logo_url = str(request.form.get("logo_url") or "").strip()
            website_url = str(request.form.get("website_url") or "").strip()
            display_tier = str(request.form.get("display_tier") or "sponsor").strip().lower()
            note = str(request.form.get("note") or "").strip()
            is_active = str(request.form.get("is_active") or "").strip() == "1"

            if not sponsor_name:
                ok = False
                notice = "Sponsor name required."
            else:
                save_sponsor_admin(
                    sponsor_id=sponsor_id,
                    tournament_id=int(selected_tournament["id"]),
                    sponsor_kind=sponsor_kind,
                    sponsor_name=sponsor_name,
                    amount=amount,
                    note=note,
                    sponsor_user_id=sponsor_user_id,
                    logo_url=logo_url,
                    website_url=website_url,
                    display_tier=display_tier,
                    is_active=is_active,
                )
                notice = "Sponsor saved."

    sponsors = get_all_sponsors_admin(int(selected_tournament["id"]))
    edit_id_raw = str(request.args.get("edit") or "").strip()
    edit_id = int(edit_id_raw) if edit_id_raw.isdigit() else None
    editing_sponsor = next((item for item in sponsors if int(item["id"]) == edit_id), None)
    total_amount = sum(int(item.get("amount") or 0) for item in sponsors if int(item.get("is_active") or 0) == 1)

    return render_template_string(
        SPONSOR_ADMIN_TEMPLATE,
        css=BASE_CSS,
        tournaments=tournaments,
        selected_tournament=selected_tournament,
        sponsors=sponsors,
        editing_sponsor=editing_sponsor,
        total_amount=total_amount,
        notice=notice,
        ok=ok,
        money=money,
        discord_invite_url=DISCORD_INVITE_URL,
        admin_panel_key=ADMIN_PANEL_KEY,
    )


@app.route("/admin/announcements", methods=["GET", "POST"])
def announcement_admin_page():
    if not admin_panel_authorized():
        return (
            """
            <!doctype html><html lang="mn"><head><meta charset="utf-8"><title>Admin Access</title></head>
            <body style="font-family:Segoe UI, Arial, sans-serif; background:#09132a; color:white; padding:40px;">
              <div style="max-width:480px; margin:80px auto; padding:24px; border:1px solid #223a67; border-radius:18px; background:#101b36;">
                <h2 style="margin-top:0;">Sponsor Broadcast Access</h2>
                <p style="color:#97abd5;">Admin key оруулж sponsor broadcast panel-ийг нээнэ үү.</p>
                <form method="get">
                  <input name="key" type="password" placeholder="ADMIN_PANEL_KEY" style="width:100%; padding:12px 14px; border-radius:12px; border:1px solid #365d9d; background:#0b152c; color:white; margin-bottom:12px;">
                  <button type="submit" style="padding:10px 14px; border-radius:12px; border:1px solid #365d9d; background:#16345f; color:white;">Open Panel</button>
                </form>
              </div>
            </body></html>
            """,
            403,
        )

    tournaments = get_tournament_hub(limit=100)
    default_guild_id = int(tournaments[0]["guild_id"]) if tournaments and tournaments[0].get("guild_id") is not None else 0
    selected_tournament_id_raw = str(request.values.get("tournament_id") or "").strip()
    selected_tournament = None
    if selected_tournament_id_raw.isdigit():
        selected_tournament = get_tournament_by_id(int(selected_tournament_id_raw))

    notice = ""
    ok = True

    if request.method == "POST":
        action = str(request.form.get("action") or "").strip().lower()
        announcement_id_raw = str(request.form.get("announcement_id") or "").strip()
        announcement_id = int(announcement_id_raw) if announcement_id_raw.isdigit() else None
        posted_tournament_raw = str(request.form.get("tournament_id") or "").strip()
        selected_tournament = get_tournament_by_id(int(posted_tournament_raw)) if posted_tournament_raw.isdigit() and int(posted_tournament_raw) > 0 else None

        if action == "delete" and announcement_id:
            delete_announcement_admin(announcement_id)
            notice = "Announcement deleted."
        elif action == "save":
            title = str(request.form.get("title") or "").strip()
            body = str(request.form.get("body") or "").strip()
            badge = str(request.form.get("badge") or "Announcement").strip() or "Announcement"
            button_text = str(request.form.get("button_text") or "").strip()
            button_url = str(request.form.get("button_url") or "").strip()
            image_url = str(request.form.get("image_url") or "").strip()
            target_channel = str(request.form.get("target_channel") or "general-chat").strip() or "general-chat"
            repeat_hours_raw = str(request.form.get("repeat_hours") or "0").strip()
            repeat_hours = int(repeat_hours_raw) if repeat_hours_raw.isdigit() else 0
            end_at = str(request.form.get("end_at") or "").strip()
            publish_now = str(request.form.get("publish_now") or "").strip() == "1"

            if not title:
                ok = False
                notice = "Announcement title required."
            elif button_url and not is_supported_announcement_url(button_url):
                ok = False
                notice = "Button URL нь зөвхөн http://, https://, эсвэл discord:// хэлбэртэй байна."
            else:
                save_announcement_admin(
                    announcement_id=announcement_id,
                    guild_id=int(selected_tournament["guild_id"]) if selected_tournament and selected_tournament.get("guild_id") is not None else default_guild_id,
                    tournament_id=int(selected_tournament["id"]) if selected_tournament else None,
                    title=title,
                    body=body,
                    badge=badge,
                    button_text=button_text,
                    button_url=button_url,
                    image_url=image_url,
                    target_channel=target_channel,
                    repeat_hours=repeat_hours,
                    end_at=end_at,
                    publish_now=publish_now,
                )
                notice = "Announcement saved."

    announcements = get_all_announcements_admin(int(selected_tournament["id"])) if selected_tournament else get_all_announcements_admin(None)
    edit_id_raw = str(request.args.get("edit") or "").strip()
    edit_id = int(edit_id_raw) if edit_id_raw.isdigit() else None
    editing_announcement = next((item for item in announcements if int(item["id"]) == edit_id), None)
    if editing_announcement and selected_tournament is None:
        edit_tournament_id = editing_announcement.get("tournament_id")
        if edit_tournament_id:
            selected_tournament = get_tournament_by_id(int(edit_tournament_id))
    published_count = sum(1 for item in announcements if str(item.get("status") or "") == "published")

    return render_template_string(
        ANNOUNCEMENT_ADMIN_TEMPLATE,
        css=BASE_CSS,
        tournaments=tournaments,
        selected_tournament=selected_tournament,
        announcements=announcements,
        editing_announcement=editing_announcement,
        published_count=published_count,
        notice=notice,
        ok=ok,
        money=money,
        discord_invite_url=DISCORD_INVITE_URL,
        admin_panel_key=ADMIN_PANEL_KEY,
    )


@app.route("/admin")
def admin_dashboard_page():
    if not admin_panel_authorized():
        return (
            """
            <!doctype html><html lang="mn"><head><meta charset="utf-8"><title>Admin Access</title></head>
            <body style="font-family:Segoe UI, Arial, sans-serif; background:#09132a; color:white; padding:40px;">
              <div style="max-width:480px; margin:80px auto; padding:24px; border:1px solid #223a67; border-radius:18px; background:#101b36;">
                <h2 style="margin-top:0;">Admin Dashboard Access</h2>
                <p style="color:#97abd5;">Admin key оруулж unified dashboard-оо нээнэ үү.</p>
                <form method="get">
                  <input name="key" type="password" placeholder="ADMIN_PANEL_KEY" style="width:100%; padding:12px 14px; border-radius:12px; border:1px solid #365d9d; background:#0b152c; color:white; margin-bottom:12px;">
                  <button type="submit" style="padding:10px 14px; border-radius:12px; border:1px solid #365d9d; background:#16345f; color:white;">Open Dashboard</button>
                </form>
              </div>
            </body></html>
            """,
            403,
        )

    tournaments = get_tournament_hub(limit=100)
    current_tournament = get_current_tournament()
    sponsor_count = len(get_sponsors(int(current_tournament["id"]))) if current_tournament is not None else 0
    sponsor_count += len(get_partner_sponsors(int(current_tournament["id"]))) if current_tournament is not None else 0
    announcement_count = len(get_all_announcements_admin(None))
    analytics = get_site_analytics_summary()

    return render_template_string(
        ADMIN_DASHBOARD_TEMPLATE,
        css=BASE_CSS,
        admin_panel_key=ADMIN_PANEL_KEY,
        tournaments=tournaments,
        tournament_count=len(tournaments),
        live_count=sum(1 for item in tournaments if str(item.get("status") or "") != "completed"),
        sponsor_count=sponsor_count,
        announcement_count=announcement_count,
        analytics=analytics,
        notice=str(request.args.get("notice") or "").strip(),
        ok=str(request.args.get("ok") or "1").strip() != "0",
        money=money,
        schedule_value=schedule_value,
        tournament_status_label=tournament_status_label,
        discord_invite_url=DISCORD_INVITE_URL,
    )


@app.route("/admin/tournaments", methods=["GET", "POST"])
def tournament_admin_page():
    if not admin_panel_authorized():
        return (
            """
            <!doctype html><html lang="mn"><head><meta charset="utf-8"><title>Admin Access</title></head>
            <body style="font-family:Segoe UI, Arial, sans-serif; background:#09132a; color:white; padding:40px;">
              <div style="max-width:480px; margin:80px auto; padding:24px; border:1px solid #223a67; border-radius:18px; background:#101b36;">
                <h2 style="margin-top:0;">Tournament Admin Access</h2>
                <p style="color:#97abd5;">Admin key оруулж tournament panel-ийг нээнэ үү.</p>
                <form method="get">
                  <input name="key" type="password" placeholder="ADMIN_PANEL_KEY" style="width:100%; padding:12px 14px; border-radius:12px; border:1px solid #365d9d; background:#0b152c; color:white; margin-bottom:12px;">
                  <button type="submit" style="padding:10px 14px; border-radius:12px; border:1px solid #365d9d; background:#16345f; color:white;">Open Panel</button>
                </form>
              </div>
            </body></html>
            """,
            403,
        )

    tournaments = get_tournament_hub(limit=100)
    default_guild_id = get_env_guild_id() or (
        int(tournaments[0]["guild_id"]) if tournaments and tournaments[0].get("guild_id") is not None else 0
    )
    notice = ""
    ok = True

    if request.method == "POST":
        title = str(request.form.get("title") or "").strip()
        entry_fee_raw = str(request.form.get("entry_fee") or "50000").strip()
        start_time = normalize_admin_schedule_value(request.form.get("start_time"))
        checkin_time = normalize_admin_schedule_value(request.form.get("checkin_time"))
        prize_total_raw = str(request.form.get("prize_total") or "1600000").strip()
        prize_1_raw = str(request.form.get("prize_1") or "800000").strip()
        prize_2_raw = str(request.form.get("prize_2") or "500000").strip()
        prize_3_raw = str(request.form.get("prize_3") or "300000").strip()

        entry_fee = int(entry_fee_raw) if entry_fee_raw.isdigit() else 50000
        prize_total = int(prize_total_raw) if prize_total_raw.isdigit() else 1600000
        prize_1 = int(prize_1_raw) if prize_1_raw.isdigit() else 800000
        prize_2 = int(prize_2_raw) if prize_2_raw.isdigit() else 500000
        prize_3 = int(prize_3_raw) if prize_3_raw.isdigit() else 300000
        guild_id = default_guild_id

        if not title:
            ok = False
            notice = "Tournament title required."
        elif guild_id <= 0:
            ok = False
            notice = "Guild context олдсонгүй."
        else:
            tournament_id = create_tournament_admin(
                guild_id=guild_id,
                title=title,
                entry_fee=entry_fee,
                start_time=start_time,
                checkin_time=checkin_time,
                prize_total=prize_total,
                prize_1=prize_1,
                prize_2=prize_2,
                prize_3=prize_3,
            )
            queue_tournament_admin_action(
                guild_id=guild_id,
                tournament_id=tournament_id,
                action="publish_registration_ui",
                requested_by=0,
            )
            queue_tournament_admin_action(
                guild_id=guild_id,
                tournament_id=tournament_id,
                action="queue_registration_announcement",
                requested_by=0,
            )
            return redirect(url_for("season_page", tournament_id=tournament_id, admin_ok=1, admin_notice="Tournament web-ээс амжилттай үүслээ."))

    return render_template_string(
        TOURNAMENT_ADMIN_TEMPLATE,
        css=BASE_CSS,
        tournaments=tournaments,
        notice=notice,
        ok=ok,
        admin_panel_key=ADMIN_PANEL_KEY,
        money=money,
        schedule_value=schedule_value,
        tournament_status_label=tournament_status_label,
    )


@app.route("/admin/tournaments/<int:tournament_id>/actions", methods=["POST"])
def tournament_admin_action_route(tournament_id: int):
    submitted_key = str(request.form.get("key") or request.args.get("key") or "").strip()
    route_authorized = admin_panel_authorized() or (
        bool(ADMIN_PANEL_KEY) and secrets.compare_digest(submitted_key, ADMIN_PANEL_KEY)
    )
    if not route_authorized:
        return "Unauthorized", 403

    tournament = get_tournament_by_id(tournament_id)
    if tournament is None:
        abort(404)

    action = str(request.form.get("action") or "").strip().lower()
    if action not in {"generate_zones", "close_registration", "complete_tournament", "republish_registration_ui"}:
        return redirect(
            url_for(
                "admin_dashboard_page",
                key=ADMIN_PANEL_KEY,
                ok=0,
                notice="Unknown admin action.",
            )
        )

    if action == "close_registration":
        with get_db() as db:
            db.execute(
                """
                UPDATE tournaments
                SET status = 'registration_locked'
                WHERE id = ?
                """,
                (tournament_id,),
            )
            db.commit()
        queue_tournament_admin_action(
            guild_id=int(tournament["guild_id"]),
            tournament_id=tournament_id,
            action="refresh_registration_ui",
            requested_by=0,
        )
        return redirect(
            url_for(
                "admin_dashboard_page",
                key=ADMIN_PANEL_KEY,
                ok=1,
                notice=f"{str(tournament.get('season_name') or tournament.get('title') or 'Tournament')} registration хаагдлаа.",
            )
        )

    if action == "complete_tournament":
        with get_db() as db:
            db.execute(
                """
                UPDATE tournaments
                SET status = 'completed'
                WHERE id = ?
                """,
                (tournament_id,),
            )
            db.commit()
        queue_tournament_admin_action(
            guild_id=int(tournament["guild_id"]),
            tournament_id=tournament_id,
            action="refresh_registration_ui",
            requested_by=0,
        )
        return redirect(
            url_for(
                "admin_dashboard_page",
                key=ADMIN_PANEL_KEY,
                ok=1,
                notice=f"{str(tournament.get('season_name') or tournament.get('title') or 'Tournament')} completed боллоо.",
            )
        )

    if action == "republish_registration_ui":
        queue_tournament_admin_action(
            guild_id=int(tournament["guild_id"]),
            tournament_id=tournament_id,
            action="publish_registration_ui",
            requested_by=0,
        )
        return redirect(
            url_for(
                "admin_dashboard_page",
                key=ADMIN_PANEL_KEY,
                ok=1,
                notice=f"{str(tournament.get('season_name') or tournament.get('title') or 'Tournament')} Discord UI дахин publish queue-д орлоо.",
            )
        )

    confirmed_players = get_confirmed_players(tournament_id)
    zone_stages = [stage for stage in get_stage_groups(tournament_id) if stage.get("stage_type") == "zone"]
    if zone_stages:
        return redirect(
            url_for(
                "admin_dashboard_page",
                key=ADMIN_PANEL_KEY,
                ok=0,
                notice="Zone draw аль хэдийн үүссэн байна.",
            )
        )
    if len(confirmed_players) < int(tournament.get("max_players") or 32):
        return redirect(
            url_for(
                "admin_dashboard_page",
                key=ADMIN_PANEL_KEY,
                ok=0,
                notice="32 confirmed бүрдсэний дараа zone draw хийнэ.",
            )
        )

    queue_tournament_admin_action(
        guild_id=int(tournament["guild_id"]),
        tournament_id=tournament_id,
        action="generate_zones",
        requested_by=0,
    )
    return redirect(
        url_for(
            "admin_dashboard_page",
            key=ADMIN_PANEL_KEY,
            ok=1,
            notice="Zone draw queue-д орлоо. Bot match-results дээр Zone A/B/C/D-ийг удахгүй үүсгэнэ.",
        )
    )


@app.route("/admin/tournaments/<int:tournament_id>/entries/<int:entry_id>/confirm", methods=["POST"])
def tournament_admin_confirm_entry_route(tournament_id: int, entry_id: int):
    if not admin_panel_authorized():
        return "Unauthorized", 403
    ok, notice = admin_confirm_tournament_entry(tournament_id, entry_id)
    return redirect(
        url_for(
            "season_page",
            tournament_id=tournament_id,
            key=ADMIN_PANEL_KEY,
            admin_ok=1 if ok else 0,
            admin_notice=notice,
        )
    )


@app.route("/admin/tournaments/<int:tournament_id>/entries/<int:entry_id>/unconfirm", methods=["POST"])
def tournament_admin_unconfirm_entry_route(tournament_id: int, entry_id: int):
    if not admin_panel_authorized():
        return "Unauthorized", 403
    ok, notice = admin_unconfirm_tournament_entry(tournament_id, entry_id)
    return redirect(
        url_for(
            "season_page",
            tournament_id=tournament_id,
            key=ADMIN_PANEL_KEY,
            admin_ok=1 if ok else 0,
            admin_notice=notice,
        )
    )


@app.route("/admin/tournaments/<int:tournament_id>/entries/<int:entry_id>/remove", methods=["POST"])
def tournament_admin_remove_entry_route(tournament_id: int, entry_id: int):
    if not admin_panel_authorized():
        return "Unauthorized", 403
    ok, notice = admin_remove_tournament_entry(tournament_id, entry_id)
    return redirect(
        url_for(
            "season_page",
            tournament_id=tournament_id,
            key=ADMIN_PANEL_KEY,
            admin_ok=1 if ok else 0,
            admin_notice=notice,
        )
    )


@app.route("/leaderboard")
def leaderboard_page():
    return render_template_string(
        LEADERBOARD_TEMPLATE,
        css=BASE_CSS,
        script=BASE_SCRIPT,
        leaderboard=get_leaderboard(limit=20),
        discord_invite_url=DISCORD_INVITE_URL,
        money=money,
    )


@app.route("/history")
def history_page():
    history = get_tournament_history(limit=100)
    unique_champions = len(
        {
            int(item["podium"][0]["user_id"])
            for item in history
            if item.get("podium")
        }
    )
    total_prize_paid = sum(
        sum(int(p.get("prize_amount") or 0) for p in item.get("podium", []))
        for item in history
    )

    return render_template_string(
        HISTORY_TEMPLATE,
        css=BASE_CSS,
        script=BASE_SCRIPT,
        history=history,
        latest_champion=get_latest_champion_from_history(),
        total_tournaments=len(history),
        unique_champions=unique_champions,
        total_prize_paid=total_prize_paid,
        discord_invite_url=DISCORD_INVITE_URL,
        money=money,
    )


@app.route("/history/<int:tournament_id>")
def season_page(tournament_id: int):
    tournament = get_tournament_by_id(tournament_id)
    if tournament is None:
        abort(404)

    register_notice = request.args.get("register_notice", "").strip()
    register_ok = request.args.get("register_ok", "").strip() == "1"
    admin_notice = request.args.get("admin_notice", "").strip()
    admin_ok = request.args.get("admin_ok", "").strip() == "1"
    discord_user = get_logged_in_discord_user()
    oauth_enabled = discord_oauth_enabled()
    stages = get_stage_groups(tournament_id)
    grand_final = next((stage for stage in stages if stage.get("stage_type") == "final"), None)
    grand_final_display_players = sort_stage_players_for_display(grand_final)
    archive_stages = [stage for stage in stages if stage.get("stage_type") != "final"]
    zone_stages = [
        {**stage, "players": sort_stage_players_for_display(stage)}
        for stage in archive_stages
        if stage.get("stage_type") == "zone"
    ]
    semi_stages = [
        {**stage, "players": sort_stage_players_for_display(stage)}
        for stage in archive_stages
        if stage.get("stage_type") == "semi"
    ]
    current_user_profile = None
    current_user_registration = None
    if discord_user and str(discord_user.get("id") or "").isdigit():
        current_user_profile = get_player_profile(int(discord_user["id"]))
        current_user_registration = get_current_user_registration_status(
            tournament_id,
            int(discord_user["id"]),
        )
    sponsor_total = get_sponsor_total(tournament_id)
    sponsors = get_sponsors(tournament_id)
    waiting_players = get_waiting_players(tournament_id)
    final_standings = apply_sponsor_bonus_to_final_standings(
        get_final_standings(tournament_id), sponsor_total
    )
    rules_text = GLOBAL_RULES_TEXT
    confirmed_players = get_confirmed_players(tournament_id)
    admin_enabled = resolve_admin_panel_access()
    show_generate_zones_admin = (
        str(tournament.get("status") or "") in {"registration_open", "registration_locked"}
        and len(confirmed_players) >= int(tournament.get("max_players") or 0)
        and len(zone_stages) == 0
    )
    admin_debug = ""
    if str(request.args.get("key") or "").strip():
        admin_debug = (
            f"Admin={admin_enabled} | "
            f"Status={tournament.get('status')} | "
            f"Confirmed={len(confirmed_players)}/{int(tournament.get('max_players') or 0)} | "
            f"Zones={len(zone_stages)} | "
            f"ShowButton={show_generate_zones_admin}"
        )

    return render_template_string(
        SEASON_TEMPLATE,
        css=BASE_CSS,
        script=BASE_SCRIPT,
        tournament=tournament,
        confirmed_players=confirmed_players,
        stages=stages,
        grand_final=grand_final,
        grand_final_display_players=grand_final_display_players,
        archive_stages=archive_stages,
        zone_stages=zone_stages,
        semi_stages=semi_stages,
        final_standings=final_standings,
        waiting_players=waiting_players,
        sponsors=sponsors,
        sponsor_total=sponsor_total,
        prize_display=build_prize_display(tournament, sponsor_total),
        rules_text=rules_text,
        register_notice=register_notice,
        register_ok=register_ok,
        admin_notice=admin_notice,
        admin_ok=admin_ok,
        discord_user=discord_user,
        current_user_profile=current_user_profile,
        current_user_registration=current_user_registration,
        oauth_enabled=oauth_enabled,
        admin_panel_enabled=admin_enabled,
        show_generate_zones_admin=show_generate_zones_admin,
        admin_enabled=admin_enabled,
        admin_debug=admin_debug,
        discord_invite_url=DISCORD_INVITE_URL,
        payment_owner_name=DONATE_OWNER_NAME,
        payment_bank_name=DONATE_BANK_NAME,
        payment_account_no=DONATE_ACCOUNT_NO,
        payment_note=DONATE_NOTE,
        money=money,
        schedule_value=schedule_value,
        tournament_status_label=tournament_status_label,
        registration_status_label=registration_status_label,
        registration_source_label=registration_source_label,
        payment_status_label=payment_status_label,
    )


@app.route("/player/<int:user_id>")
def player_page(user_id: int):
    profile = get_player_profile(user_id)
    if profile is None:
        abort(404)

    support = get_player_support_status(user_id)
    donor_support_roles = support_role_chain(
        support.get("donor_tier") if support else None,
        DONOR_ROLE_NAMES,
    )
    sponsor_support_roles = support_role_chain(
        support.get("sponsor_tier") if support else None,
        SPONSOR_ROLE_NAMES,
    )

    return render_template_string(
        PLAYER_TEMPLATE,
        css=BASE_CSS,
        script=BASE_SCRIPT,
        profile=profile,
        support=support,
        donor_support_roles=donor_support_roles,
        sponsor_support_roles=sponsor_support_roles,
        support_badge_label=support_badge_label,
        history=get_player_history(user_id),
        discord_invite_url=DISCORD_INVITE_URL,
        money=money,
    )


@app.route("/donate")
def donate_page():
    donors = get_platform_donors()
    platform_total = get_platform_donation_total()

    return render_template_string(
        DONATE_TEMPLATE,
        css=BASE_CSS,
        script=BASE_SCRIPT,
        donors=donors,
        platform_total=platform_total,
        donate_owner_name=DONATE_OWNER_NAME,
        donate_bank_name=DONATE_BANK_NAME,
        donate_account_no=DONATE_ACCOUNT_NO,
        donate_note=DONATE_NOTE,
        donate_qr_src=resolve_donate_qr_src(),
        discord_invite_url=DISCORD_INVITE_URL,
        money=money,
    )


@app.route("/contact")
def contact_page():
    moderators = get_contact_moderators()
    return render_template_string(
        CONTACT_TEMPLATE,
        css=BASE_CSS,
        script=BASE_SCRIPT,
        discord_invite_url=DISCORD_INVITE_URL,
        discord_label=CONTACT_DISCORD_LABEL,
        contact_email=CONTACT_EMAIL,
        support_note=CONTACT_SUPPORT_NOTE,
        moderators=moderators,
        money=money,
    )


@app.route("/api/current")
def api_current():
    tournament = get_current_tournament()
    if tournament is None:
        return jsonify({"ok": False, "error": "No tournament found"}), 404

    tournament_id = int(tournament["id"])
    return jsonify(
        {
            "ok": True,
            "tournament": tournament,
            "registered_count": get_count_by_status(tournament_id, "registered"),
            "waitlist_count": get_count_by_status(tournament_id, "waitlist"),
            "confirmed_players": get_confirmed_players(tournament_id),
            "sponsors": get_sponsors(tournament_id),
            "sponsor_total": get_sponsor_total(tournament_id),
            "leaderboard": get_leaderboard(limit=10),
            "stage_groups": get_stage_groups(tournament_id),
            "final_standings": get_final_standings(tournament_id),
            "history_preview": get_tournament_history(limit=5),
            "latest_champion": get_latest_champion_from_history(),
            "platform_donors": get_platform_donors(),
            "platform_total": get_platform_donation_total(),
        }
    )


if __name__ == "__main__":
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8086"))
    asyncio.run(init_db(str(DB_PATH)))
    app.run(host=host, port=port, debug=True)



