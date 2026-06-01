import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from io import BytesIO
from contextlib import contextmanager
from dataclasses import dataclass
import tempfile
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import psutil
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram import MessageEntity
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from features.log_export import build_logs_keyboard, humanize_log_text
from features.inline_menu import build_inline_menu_admin_kb, paged_rows, CB as IMCB
from features.inline_actions import ACTION_REGISTRY
from features.inline_callback import parse as parse_cb, is_valid_im_callback
from features.market_engine import MARKET_SERVICE, cache_status, market_help_text, merge_market_settings, normalize_asset_list, parse_market_intent, render_market_response, validate_market_api_key
from features.market_cards import COLOR_PALETTES, CARD_THEMES, ENGLISH_FONT_CHOICES, PERSIAN_FONT_CHOICES, WATERMARK_POSITIONS, merge_branding_settings, render_market_card

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "bot.db"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "bot.log"
load_dotenv(ENV_PATH)

START_TIME = int(time.time())
SOFT_BAN_SECONDS = 20 * 60
CALLBACK_COOLDOWN_SECONDS = 20 * 60
CALLBACK_ALLOWED_INTERACTIONS = 5
CALLBACK_RATE_BUCKET = "callback_button"
WELCOME_COOLDOWN_SECONDS = 24 * 60 * 60
BUSINESS_UPDATE_FRESHNESS_SECONDS = 120

ADMIN_FEATURES = ["admin_panel_access", "admin_broadcast", "admin_reports", "admin_edit_texts", "admin_toggle_features", "admin_user_stats", "admin_system_info", "admin_public_ip", "admin_export_users", "admin_bold_mode"]
USER_FEATURES = ["user_auto_reply", "user_services", "user_hours", "user_location", "user_faq", "user_contact", "user_request_callback", "user_feedback", "user_join_channel", "user_business_test_reply"]
TEXT_KEYS = {
    "offline_message": "پیام آفلاین", "service_text": "متن خدمات", "hours_text": "ساعات کاری", "location_text": "متن آدرس",
    "faq_text": "متن پرسش‌های پرتکرار", "contact_text": "متن تماس", "feedback_prompt_text": "متن درخواست بازخورد", "feedback_success_text": "متن موفقیت بازخورد",
}

@dataclass
class State:
    flow: str | None = None
    step: str | None = None
    admin_id: int | None = None
    message_id: int | None = None
    temp_shortcuts: dict[str, str] | None = None
    pending_key: str | None = None

STATE = State()

class DB:
    def __init__(self, path: Path): self.path = path
    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    def init(self):
        with self.conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            c.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, phone TEXT, is_channel_joined INTEGER DEFAULT 0,
                last_seen_at INTEGER, first_seen_at INTEGER, source TEXT, soft_ban_until INTEGER DEFAULT 0, spam_score INTEGER DEFAULT 0, last_message TEXT
            )""")
            c.execute("CREATE TABLE IF NOT EXISTS shortcuts (name TEXT PRIMARY KEY, response TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS feedbacks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, full_name TEXT, message TEXT, created_at INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS watch_settings (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            c.execute("""CREATE TABLE IF NOT EXISTS keyword_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, user_id INTEGER, username TEXT, full_name TEXT,
                chat_id INTEGER, chat_title TEXT, text TEXT, created_at INTEGER
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS menus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT UNIQUE NOT NULL,
                preview_text TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS menu_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                menu_id INTEGER NOT NULL,
                button_text TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                action_type TEXT NOT NULL,
                action_payload TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(menu_id) REFERENCES menus(id) ON DELETE CASCADE
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS admin_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER UNIQUE NOT NULL,
                state TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id INTEGER,
                old_value TEXT,
                new_value TEXT,
                created_at INTEGER NOT NULL
            )""")
            self._migrate_schema(c)

    def _table_columns(self, c: sqlite3.Connection, table: str) -> set[str]:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}

    def _add_missing_columns(self, c: sqlite3.Connection, table: str, migrations: dict[str, str]) -> None:
        cols = self._table_columns(c, table)
        for col, sql in migrations.items():
            if col not in cols:
                logging.info("db_migration_add_column table=%s column=%s", table, col)
                c.execute(sql)

    def _migrate_schema(self, c: sqlite3.Connection) -> None:
        self._add_missing_columns(c, "kv", {
            "k": "ALTER TABLE kv ADD COLUMN k TEXT",
            "v": "ALTER TABLE kv ADD COLUMN v TEXT NOT NULL DEFAULT ''",
        })
        self._migrate_users(c)
        self._add_missing_columns(c, "shortcuts", {
            "name": "ALTER TABLE shortcuts ADD COLUMN name TEXT",
            "response": "ALTER TABLE shortcuts ADD COLUMN response TEXT NOT NULL DEFAULT ''",
        })
        self._add_missing_columns(c, "feedbacks", {
            "user_id": "ALTER TABLE feedbacks ADD COLUMN user_id INTEGER",
            "username": "ALTER TABLE feedbacks ADD COLUMN username TEXT",
            "full_name": "ALTER TABLE feedbacks ADD COLUMN full_name TEXT",
            "message": "ALTER TABLE feedbacks ADD COLUMN message TEXT",
            "created_at": "ALTER TABLE feedbacks ADD COLUMN created_at INTEGER DEFAULT 0",
        })
        self._add_missing_columns(c, "watch_settings", {
            "k": "ALTER TABLE watch_settings ADD COLUMN k TEXT",
            "v": "ALTER TABLE watch_settings ADD COLUMN v TEXT NOT NULL DEFAULT ''",
        })
        self._add_missing_columns(c, "keyword_hits", {
            "keyword": "ALTER TABLE keyword_hits ADD COLUMN keyword TEXT NOT NULL DEFAULT ''",
            "user_id": "ALTER TABLE keyword_hits ADD COLUMN user_id INTEGER",
            "username": "ALTER TABLE keyword_hits ADD COLUMN username TEXT",
            "full_name": "ALTER TABLE keyword_hits ADD COLUMN full_name TEXT",
            "chat_id": "ALTER TABLE keyword_hits ADD COLUMN chat_id INTEGER DEFAULT 0",
            "chat_title": "ALTER TABLE keyword_hits ADD COLUMN chat_title TEXT",
            "text": "ALTER TABLE keyword_hits ADD COLUMN text TEXT",
            "created_at": "ALTER TABLE keyword_hits ADD COLUMN created_at INTEGER DEFAULT 0",
        })
        self._add_missing_columns(c, "menus", {
            "command": "ALTER TABLE menus ADD COLUMN command TEXT NOT NULL DEFAULT ''",
            "preview_text": "ALTER TABLE menus ADD COLUMN preview_text TEXT NOT NULL DEFAULT ''",
            "is_active": "ALTER TABLE menus ADD COLUMN is_active INTEGER DEFAULT 1",
            "created_at": "ALTER TABLE menus ADD COLUMN created_at INTEGER DEFAULT 0",
            "updated_at": "ALTER TABLE menus ADD COLUMN updated_at INTEGER DEFAULT 0",
        })
        self._add_missing_columns(c, "menu_buttons", {
            "menu_id": "ALTER TABLE menu_buttons ADD COLUMN menu_id INTEGER DEFAULT 0",
            "button_text": "ALTER TABLE menu_buttons ADD COLUMN button_text TEXT NOT NULL DEFAULT ''",
            "sort_order": "ALTER TABLE menu_buttons ADD COLUMN sort_order INTEGER DEFAULT 0",
            "action_type": "ALTER TABLE menu_buttons ADD COLUMN action_type TEXT NOT NULL DEFAULT 'just_text'",
            "action_payload": "ALTER TABLE menu_buttons ADD COLUMN action_payload TEXT NOT NULL DEFAULT ''",
            "is_active": "ALTER TABLE menu_buttons ADD COLUMN is_active INTEGER DEFAULT 1",
            "created_at": "ALTER TABLE menu_buttons ADD COLUMN created_at INTEGER DEFAULT 0",
            "updated_at": "ALTER TABLE menu_buttons ADD COLUMN updated_at INTEGER DEFAULT 0",
        })
        self._add_missing_columns(c, "admin_states", {
            "admin_id": "ALTER TABLE admin_states ADD COLUMN admin_id INTEGER DEFAULT 0",
            "state": "ALTER TABLE admin_states ADD COLUMN state TEXT NOT NULL DEFAULT ''",
            "payload": "ALTER TABLE admin_states ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'",
            "updated_at": "ALTER TABLE admin_states ADD COLUMN updated_at INTEGER DEFAULT 0",
        })
        self._add_missing_columns(c, "admin_logs", {
            "admin_id": "ALTER TABLE admin_logs ADD COLUMN admin_id INTEGER DEFAULT 0",
            "action": "ALTER TABLE admin_logs ADD COLUMN action TEXT NOT NULL DEFAULT ''",
            "target_type": "ALTER TABLE admin_logs ADD COLUMN target_type TEXT NOT NULL DEFAULT ''",
            "target_id": "ALTER TABLE admin_logs ADD COLUMN target_id INTEGER",
            "old_value": "ALTER TABLE admin_logs ADD COLUMN old_value TEXT",
            "new_value": "ALTER TABLE admin_logs ADD COLUMN new_value TEXT",
            "created_at": "ALTER TABLE admin_logs ADD COLUMN created_at INTEGER DEFAULT 0",
        })

    def _migrate_users(self, c: sqlite3.Connection) -> None:
        migrations = {
            "username": "ALTER TABLE users ADD COLUMN username TEXT",
            "full_name": "ALTER TABLE users ADD COLUMN full_name TEXT",
            "phone": "ALTER TABLE users ADD COLUMN phone TEXT",
            "is_channel_joined": "ALTER TABLE users ADD COLUMN is_channel_joined INTEGER DEFAULT 0",
            "last_seen_at": "ALTER TABLE users ADD COLUMN last_seen_at INTEGER",
            "first_seen_at": "ALTER TABLE users ADD COLUMN first_seen_at INTEGER",
            "source": "ALTER TABLE users ADD COLUMN source TEXT",
            "soft_ban_until": "ALTER TABLE users ADD COLUMN soft_ban_until INTEGER DEFAULT 0",
            "spam_score": "ALTER TABLE users ADD COLUMN spam_score INTEGER DEFAULT 0",
            "last_message": "ALTER TABLE users ADD COLUMN last_message TEXT",
        }
        self._add_missing_columns(c, "users", migrations)
    def get_json(self, key: str, default: Any) -> Any:
        with self.conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row["v"])
            except Exception as exc:
                logging.warning("kv_json_decode_failed key=%s reason=%s", key, exc)
                return default
    def set_json(self, key: str, value: Any):
        with self.conn() as c:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, json.dumps(value, ensure_ascii=False)))
    def get_kv_raw(self, key: str) -> str | None:
        with self.conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
            return None if not row else str(row["v"])
    def upsert_user(self, user_id: int, username: str, full_name: str, phone: str | None, joined: bool, source: str, last_message: str | None):
        now = int(time.time())
        with self.conn() as c:
            c.execute("""INSERT INTO users(user_id,username,full_name,phone,is_channel_joined,last_seen_at,first_seen_at,source,last_message)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,full_name=excluded.full_name,phone=COALESCE(excluded.phone, users.phone),
                is_channel_joined=excluded.is_channel_joined,last_seen_at=excluded.last_seen_at,source=excluded.source,last_message=COALESCE(excluded.last_message, users.last_message)
            """, (user_id, username, full_name, phone, 1 if joined else 0, now, now, source, last_message))
    def get_user(self, user_id: int):
        with self.conn() as c: return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    def count_users(self) -> int:
        with self.conn() as c:
            row = c.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
            return int(row["cnt"] if row else 0)
    def list_users(self, limit: int = 50, offset: int = 0):
        with self.conn() as c: return c.execute("SELECT * FROM users ORDER BY COALESCE(last_seen_at,0) DESC, user_id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    def touch_user_activity(self, user_id: int, source: str | None = None, last_message: str | None = None, spam_delta: int = 0):
        now = int(time.time())
        with self.conn() as c:
            c.execute("""UPDATE users SET
                last_seen_at=?,
                source=COALESCE(?, source),
                last_message=COALESCE(?, last_message),
                spam_score=COALESCE(spam_score,0)+?
                WHERE user_id=?
            """, (now, source, last_message, spam_delta, user_id))
    def set_soft_ban(self, user_id: int, until_ts: int, spam_delta: int = 1):
        now = int(time.time())
        with self.conn() as c:
            c.execute("""UPDATE users SET soft_ban_until=?, last_seen_at=?, spam_score=COALESCE(spam_score,0)+? WHERE user_id=?""", (until_ts, now, spam_delta, user_id))
    def set_menu_active(self, menu_id: int, active: bool):
        with self.conn() as c: c.execute("UPDATE menus SET is_active=?, updated_at=? WHERE id=?", (1 if active else 0, int(time.time()), menu_id))
    def set_button_active(self, button_id: int, active: bool):
        with self.conn() as c: c.execute("UPDATE menu_buttons SET is_active=?, updated_at=? WHERE id=?", (1 if active else 0, int(time.time()), button_id))
    def save_shortcuts(self, items: dict[str, str]):
        cleaned=[]
        for k,v in items.items():
            if k is None or not str(k).strip():
                logging.warning("shortcut_rejected_invalid_key key=%r", k); continue
            cleaned.append((str(k).strip(), str(v or "").strip()))
        with self.conn() as c:
            for name, resp in cleaned:
                c.execute("INSERT INTO shortcuts(name,response) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET response=excluded.response", (name, resp))
    def load_shortcuts(self) -> dict[str, str]:
        with self.conn() as c:
            return {r["name"]: r["response"] for r in c.execute("SELECT name,response FROM shortcuts").fetchall()}
    def add_feedback(self, user_id:int, username:str, full_name:str, message:str):
        with self.conn() as c:
            c.execute("INSERT INTO feedbacks(user_id,username,full_name,message,created_at) VALUES(?,?,?,?,?)", (user_id, username, full_name, message, int(time.time())))
    def list_feedbacks(self, limit:int=100):
        with self.conn() as c:
            return c.execute("SELECT * FROM feedbacks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    def set_watch(self, key: str, value: Any):
        with self.conn() as c:
            c.execute("INSERT INTO watch_settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, json.dumps(value, ensure_ascii=False)))
    def get_watch(self, key: str, default: Any):
        with self.conn() as c:
            row = c.execute("SELECT v FROM watch_settings WHERE k=?", (key,)).fetchone()
            return default if not row else json.loads(row["v"])
    def add_keyword_hit(self, keyword: str, user_id: int | None, username: str, full_name: str, chat_id: int, chat_title: str, text: str):
        with self.conn() as c:
            c.execute("INSERT INTO keyword_hits(keyword,user_id,username,full_name,chat_id,chat_title,text,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (keyword, user_id, username, full_name, chat_id, chat_title, text, int(time.time())))
    def hit_stats(self):
        with self.conn() as c:
            return c.execute("SELECT keyword, COUNT(*) AS cnt FROM keyword_hits GROUP BY keyword ORDER BY cnt DESC, keyword ASC").fetchall()
    def keyword_count(self, keyword: str) -> int:
        with self.conn() as c:
            row = c.execute("SELECT COUNT(*) AS cnt FROM keyword_hits WHERE keyword=?", (keyword,)).fetchone()
            return int(row["cnt"] if row else 0)
    def delete_shortcut(self, key: str):
        with self.conn() as c:
            c.execute("DELETE FROM shortcuts WHERE name=?", (key,))
    def set_admin_state(self, admin_id: int, state: str, payload: dict[str, Any]):
        with self.conn() as c:
            c.execute("INSERT INTO admin_states(admin_id,state,payload,updated_at) VALUES(?,?,?,?) ON CONFLICT(admin_id) DO UPDATE SET state=excluded.state,payload=excluded.payload,updated_at=excluded.updated_at",
                      (admin_id, state, json.dumps(payload, ensure_ascii=False), int(time.time())))
    def get_admin_state(self, admin_id: int):
        with self.conn() as c:
            r = c.execute("SELECT * FROM admin_states WHERE admin_id=?", (admin_id,)).fetchone()
            if not r: return None
            return {"state": r["state"], "payload": json.loads(r["payload"] or "{}")}
    def clear_admin_state(self, admin_id: int):
        with self.conn() as c: c.execute("DELETE FROM admin_states WHERE admin_id=?", (admin_id,))
    def clear_admin_state_if(self, admin_id: int, state: str):
        with self.conn() as c: c.execute("DELETE FROM admin_states WHERE admin_id=? AND state=?", (admin_id, state))
    def log_admin(self, admin_id:int, action:str, target_type:str, target_id:int|None, old_value:str|None, new_value:str|None):
        with self.conn() as c:
            c.execute("INSERT INTO admin_logs(admin_id,action,target_type,target_id,old_value,new_value,created_at) VALUES(?,?,?,?,?,?,?)",
                      (admin_id, action, target_type, target_id, old_value, new_value, int(time.time())))
    def create_menu(self, command:str, preview_text:str):
        now=int(time.time())
        with self.conn() as c:
            cur=c.execute("INSERT INTO menus(command,preview_text,is_active,created_at,updated_at) VALUES(?,?,?,?,?)",(command,preview_text,1,now,now))
            return cur.lastrowid
    def add_menu_button(self, menu_id:int, button_text:str, action_type:str, action_payload:str):
        now=int(time.time())
        with self.conn() as c:
            sort=int(c.execute("SELECT COALESCE(MAX(sort_order),0)+1 v FROM menu_buttons WHERE menu_id=?",(menu_id,)).fetchone()["v"])
            cur=c.execute("INSERT INTO menu_buttons(menu_id,button_text,sort_order,action_type,action_payload,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (menu_id,button_text,sort,action_type,action_payload,1,now,now))
            return cur.lastrowid
    def list_menus(self):
        with self.conn() as c: return c.execute("SELECT * FROM menus ORDER BY id DESC").fetchall()
    def has_command_ci(self, command:str, exclude_menu_id:int|None=None) -> bool:
        wanted = normalize_trigger(command)
        with self.conn() as c:
            rows = c.execute("SELECT id, command FROM menus").fetchall()
            return any(normalize_trigger(r["command"]) == wanted and (exclude_menu_id is None or int(r["id"]) != exclude_menu_id) for r in rows)
    def menu_by_command(self, command:str):
        wanted = normalize_trigger(command)
        if not wanted:
            return None
        with self.conn() as c:
            rows = c.execute("SELECT * FROM menus WHERE is_active=1 ORDER BY id DESC").fetchall()
            matches = [r for r in rows if normalize_trigger(r["command"]) == wanted]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                logging.warning("menu_case_collision command=%r normalized=%r count=%s", command, wanted, len(matches))
            return None
    def menu_buttons(self, menu_id:int):
        with self.conn() as c: return c.execute("SELECT * FROM menu_buttons WHERE menu_id=? AND is_active=1 ORDER BY sort_order,id",(menu_id,)).fetchall()
    def menu_buttons_all(self, menu_id:int):
        with self.conn() as c: return c.execute("SELECT * FROM menu_buttons WHERE menu_id=? ORDER BY sort_order,id",(menu_id,)).fetchall()
    def menu_by_id(self, menu_id:int):
        with self.conn() as c: return c.execute("SELECT * FROM menus WHERE id=?", (menu_id,)).fetchone()
    def delete_menu_atomic(self, menu_id: int) -> bool:
        with self.conn() as c:
            cur = c.execute("DELETE FROM menus WHERE id=?", (menu_id,))
            return cur.rowcount > 0
    def delete_button_atomic(self, button_id: int) -> bool:
        with self.conn() as c:
            row = c.execute("SELECT menu_id, sort_order FROM menu_buttons WHERE id=?", (button_id,)).fetchone()
            if not row: return False
            menu_id = int(row["menu_id"]); sort_order = int(row["sort_order"])
            c.execute("DELETE FROM menu_buttons WHERE id=?", (button_id,))
            c.execute("UPDATE menu_buttons SET sort_order = sort_order - 1, updated_at=? WHERE menu_id=? AND sort_order>?",
                      (int(time.time()), menu_id, sort_order))
            return True
    def update_menu_preview(self, menu_id:int, preview:str):
        with self.conn() as c: c.execute("UPDATE menus SET preview_text=?, updated_at=? WHERE id=?", (preview, int(time.time()), menu_id))
    def update_menu_command(self, menu_id:int, command:str):
        with self.conn() as c: c.execute("UPDATE menus SET command=?, updated_at=? WHERE id=?", (command, int(time.time()), menu_id))
    def button_by_id(self, button_id:int):
        with self.conn() as c: return c.execute("SELECT * FROM menu_buttons WHERE id=?", (button_id,)).fetchone()
    def update_button_name(self, button_id:int, name:str):
        with self.conn() as c: c.execute("UPDATE menu_buttons SET button_text=?, updated_at=? WHERE id=?", (name, int(time.time()), button_id))
    def update_button_output(self, button_id:int, output:str):
        with self.conn() as c: c.execute("UPDATE menu_buttons SET action_payload=?, updated_at=? WHERE id=?", (output, int(time.time()), button_id))

db = DB(DB_PATH)
FSM_TTL_SECONDS = 15 * 60
BUSINESS_OWNER_CACHE_TTL_SECONDS = 60 * 60
BUSINESS_OWNER_CACHE: dict[str, tuple[int, float]] = {}

def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])

def get_default_data() -> dict[str, Any]:
    return {"admin_id": int(os.getenv("ADMIN_ID") or 0), "active": False, "force_join_channel": os.getenv("FORCE_JOIN_CHANNEL", ""), "bold_mode": True,
            "welcome_enabled": False, "welcome_text": "سلام 🌟\nبه پیج بیزینسی ما خوش آمدید.", "self_bot_enabled": False, "inline_menu_enabled": False,
            "market": {"market_engine_enabled": False},
            "features": {**{k: True for k in ADMIN_FEATURES}, **{k: True for k in USER_FEATURES}}, "offline_message": "پیام شما دریافت شد. به‌زودی پاسخ می‌دهیم.",
            "service_text": "", "hours_text": "", "location_text": "", "faq_text": "", "contact_text": "", "feedback_prompt_text": "لطفاً بازخورد خود را ارسال کنید.", "feedback_success_text": "✅ بازخورد شما با موفقیت ثبت شد."}

def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes", "y", "فعال", "روشن"}:
            return True
        if normalized in {"0", "false", "off", "no", "n", "غیرفعال", "خاموش"}:
            return False
    return default


def normalize_trigger(text: str | None) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^[\s/!#@]+", "", value)
    value = re.sub(r"[\s.!؟?،,؛;:]+$", "", value)
    return value.casefold()


def normalize_shortcut_map(shortcuts: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in shortcuts.items():
        nk = normalize_trigger(key)
        if nk and nk not in normalized:
            normalized[nk] = value
    return normalized


def load_data() -> dict[str, Any]:
    d=get_default_data()
    settings_raw = db.get_kv_raw("settings")
    data=db.get_json("settings", d.copy())
    if not isinstance(data, dict):
        logging.warning("settings_invalid_type type=%s", type(data).__name__)
        data = d.copy()
    for k,v in d.items(): data.setdefault(k,v)
    data.setdefault("features",{})
    if not isinstance(data["features"], dict):
        data["features"] = {}
    for k,v in d["features"].items(): data["features"].setdefault(k,v)
    if settings_raw is None:
        legacy_welcome_enabled = db.get_kv_raw("welcome_enabled")
        legacy_welcome_text = db.get_kv_raw("welcome_text")
        if legacy_welcome_enabled is not None:
            try:
                data["welcome_enabled"] = json.loads(legacy_welcome_enabled)
            except Exception:
                data["welcome_enabled"] = legacy_welcome_enabled
        if legacy_welcome_text is not None:
            try:
                data["welcome_text"] = json.loads(legacy_welcome_text)
            except Exception:
                data["welcome_text"] = legacy_welcome_text
    data["welcome_enabled"] = coerce_bool(data.get("welcome_enabled"), d["welcome_enabled"])
    if not isinstance(data.get("welcome_text"), str):
        data["welcome_text"] = str(data.get("welcome_text") or d["welcome_text"])
    merge_market_settings(data)
    merge_branding_settings(data)
    return data

def save_data(data: dict[str, Any]) -> None: db.set_json("settings", data)
def is_admin(user_id: int, data: dict[str, Any]) -> bool: return user_id == int(data.get("admin_id") or os.getenv("ADMIN_ID") or 0)


def configured_admin_id(data: dict[str, Any] | None = None) -> int:
    return int((data or {}).get("admin_id") or os.getenv("ADMIN_ID") or 0)


def business_owner_from_connection(connection) -> int | None:
    owner = getattr(connection, "user", None)
    if owner and getattr(owner, "id", None) is not None:
        return int(owner.id)
    user_chat_id = getattr(connection, "user_chat_id", None)
    return int(user_chat_id) if user_chat_id is not None else None


async def business_connection_owner_id(context: ContextTypes.DEFAULT_TYPE, business_connection_id: str | None) -> int | None:
    bc_id = str(business_connection_id or "").strip()
    if not bc_id:
        return None
    now = time.time()
    cached = BUSINESS_OWNER_CACHE.get(bc_id)
    if cached and now - cached[1] <= BUSINESS_OWNER_CACHE_TTL_SECONDS:
        return cached[0]
    try:
        connection = await context.bot.get_business_connection(bc_id)
        owner_id = business_owner_from_connection(connection)
        if owner_id:
            BUSINESS_OWNER_CACHE[bc_id] = (owner_id, now)
        return owner_id
    except Exception as exc:
        logging.warning("business_connection_owner_lookup_failed bc=%s reason=%s", bc_id, exc)
        return None


async def is_admin_or_business_owner(user_id: int, data: dict[str, Any], context: ContextTypes.DEFAULT_TYPE, business_connection_id: str | None = None) -> bool:
    if is_admin(user_id, data):
        return True
    owner_id = await business_connection_owner_id(context, business_connection_id)
    return bool(owner_id and owner_id == int(user_id))


async def is_admin_authored_business_message(message, data: dict[str, Any], context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not getattr(message, "business_connection_id", None):
        return False
    sender = getattr(message, "from_user", None)
    if sender and await is_admin_or_business_owner(int(sender.id), data, context, getattr(message, "business_connection_id", None)):
        return True
    owner_id = await business_connection_owner_id(context, getattr(message, "business_connection_id", None))
    if owner_id and is_admin(owner_id, data) and sender is None and not getattr(message, "sender_business_bot", None):
        return True
    if sender is None and not getattr(message, "sender_business_bot", None):
        return bool(configured_admin_id(data))
    return False


async def message_sender_id(message, data: dict[str, Any] | None = None, context: ContextTypes.DEFAULT_TYPE | None = None) -> int | None:
    sender = getattr(message, "from_user", None)
    if sender:
        return int(sender.id)
    if getattr(message, "business_connection_id", None) and not getattr(message, "sender_business_bot", None):
        if context is not None:
            owner_id = await business_connection_owner_id(context, getattr(message, "business_connection_id", None))
            if owner_id:
                return owner_id
        return configured_admin_id(data) or None
    chat = getattr(message, "chat", None)
    return int(chat.id) if chat and getattr(chat, "id", None) is not None else None

def create_primary_button(text: str, callback: str) -> InlineKeyboardButton: return InlineKeyboardButton(f"✨ {text}", callback_data=callback)
def create_success_button(text: str, callback: str) -> InlineKeyboardButton: return InlineKeyboardButton(f"🚀 {text}", callback_data=callback)
def create_danger_button(text: str, callback: str) -> InlineKeyboardButton: return InlineKeyboardButton(f"🧨 {text}", callback_data=callback)

def create_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[create_success_button("خدمات","user:services"),create_primary_button("ساعات کاری","user:hours")],[create_primary_button("آدرس","user:location"),create_success_button("پرسش‌های پرتکرار","user:faq")],[create_danger_button("تماس","user:contact"),create_primary_button("ارسال بازخورد","user:feedback")]])

def create_shortcut_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [create_primary_button("مشاهده شورت‌کات‌های فعلی", "admin:shortcut_view"), create_primary_button("حذف شورت‌کات", "admin:shortcut_delete_menu")],
        [create_success_button("افزودن/ویرایش شورت‌کات", "admin:shortcut_cfg"), create_success_button("ویرایش شورت‌کات موجود", "admin:shortcut_edit_menu")],
        [create_primary_button("تنظیم چنل گزارشات", "admin:watch_channel_cfg"), create_primary_button("افزودن کلمات مانیتور", "admin:watch_keywords_add")],
        [create_primary_button("حذف کلمات مانیتور", "admin:watch_keywords_remove"), create_primary_button("آمار کلمات مانیتور", "admin:watch_keywords_stats")],
        [create_danger_button("بازگشت", "menu:admin")],
    ])


def parse_keyword_csv(raw: str) -> list[str]:
    normalized = str(raw or "").replace("،", ",")
    return [x.strip() for x in normalized.split(",") if x.strip()]


def build_shortcut_pick_keyboard(shortcuts: dict[str, str], prefix: str, map_key: str) -> InlineKeyboardMarkup:
    token_map: dict[str, str] = {}
    rows = []
    for idx, key in enumerate(shortcuts.keys(), start=1):
        token = f"s{idx}"
        token_map[token] = key
        label = key if len(key) <= 40 else f"{key[:37]}..."
        rows.append([create_primary_button(label, f"{prefix}:{token}")])
    db.set_watch(map_key, token_map)
    rows.append([create_danger_button("بازگشت", "admin:shortcut_menu")])
    return InlineKeyboardMarkup(rows)


def build_watch_keyword_pick_keyboard(keywords: list[str], prefix: str, map_key: str) -> InlineKeyboardMarkup:
    token_map: dict[str, str] = {}
    rows = []
    for idx, key in enumerate(keywords, start=1):
        token = f"w{idx}"
        token_map[token] = key
        label = key if len(key) <= 40 else f"{key[:37]}..."
        rows.append([create_primary_button(label, f"{prefix}:{token}")])
    db.set_watch(map_key, token_map)
    rows.append([create_danger_button("بازگشت", "admin:shortcut_menu")])
    return InlineKeyboardMarkup(rows)




MARKET_SECRET_ENV_KEYS = ("COINGECKO_API_KEY", "EXCHANGERATE_API_KEY")

def mask_secret(value: str | None) -> str:
    raw = str(value or "")
    if not raw:
        return "ثبت نشده"
    if len(raw) <= 8:
        return "****"
    return f"{raw[:4]}...{raw[-4:]}"


def set_env_value(key: str, value: str, *, update_backup: bool = True) -> None:
    key = str(key or "").strip()
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Z0-9_]+", key):
        raise ValueError("invalid env key")
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    rendered = f"{key}={value}"
    replaced = False
    for idx, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[idx] = rendered
            replaced = True
            break
    if not replaced:
        lines.append(rendered)
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.environ[key] = value
    if update_backup and key in MARKET_SECRET_ENV_KEYS:
        backup_market_api_secrets()




def _market_secret_key() -> bytes:
    raw = f"{os.getenv('BOT_TOKEN', '')}:{os.getenv('ADMIN_ID', '')}"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _secret_stream(key: bytes, nonce: bytes, size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        out.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(out[:size])


def _xor_secret(raw: bytes, stream: bytes) -> bytes:
    return bytes(byte ^ stream[idx] for idx, byte in enumerate(raw))


def encrypt_market_secret(value: str) -> str:
    raw = str(value or "").encode("utf-8")
    if not raw:
        return ""
    key = _market_secret_key()
    nonce = os.urandom(16)
    encrypted = _xor_secret(raw, _secret_stream(key, nonce, len(raw)))
    tag = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + tag + encrypted).decode("ascii")


def decrypt_market_secret(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        payload = base64.urlsafe_b64decode(raw.encode("ascii"))
        nonce, tag, encrypted = payload[:16], payload[16:48], payload[48:]
        key = _market_secret_key()
        expected = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            return ""
        return _xor_secret(encrypted, _secret_stream(key, nonce, len(encrypted))).decode("utf-8")
    except Exception:
        return ""


def backup_market_api_secrets() -> None:
    secrets = {key: encrypt_market_secret(os.getenv(key, "")) for key in MARKET_SECRET_ENV_KEYS if os.getenv(key, "").strip()}
    if secrets:
        db.set_json("market_api_secrets", {"v": 1, "keys": secrets, "updated_at": int(time.time())})


def restore_market_api_secrets() -> None:
    payload = db.get_json("market_api_secrets", {})
    secrets = payload.get("keys", {}) if isinstance(payload, dict) else {}
    if not isinstance(secrets, dict):
        return
    for key in MARKET_SECRET_ENV_KEYS:
        if os.getenv(key, "").strip():
            continue
        value = decrypt_market_secret(str(secrets.get(key) or ""))
        if value:
            set_env_value(key, value, update_backup=False)

def format_market_status(data: dict[str, Any]) -> str:
    settings = merge_market_settings(data)
    cache = MARKET_SERVICE.read_cache()
    status = cache_status(cache, settings)
    return (
        "📊 Market Engine Status\n\n"
        f"Conversion Engine: {'ON' if settings.get('market_engine_enabled') else 'OFF'}{' 🔒' if not data.get('active', False) else ''}\n"
        f"API Updater: {'ON' if settings.get('market_api_enabled') else 'OFF'}\n"
        f"CoinGecko: {'ON' if settings.get('coingecko_enabled') else 'OFF'} | key: {mask_secret(os.getenv('COINGECKO_API_KEY'))}\n"
        f"ExchangeRate: {'ON' if settings.get('exchangerate_enabled') else 'OFF'} | key: {mask_secret(os.getenv('EXCHANGERATE_API_KEY'))}\n"
        f"Nobitex local: {'ON' if settings.get('nobitex_enabled') else 'OFF'}\n"
        f"Cache TTL: {settings.get('cache_ttl_seconds')}s | stale fallback: {settings.get('stale_ttl_seconds')}s\n"
        f"Cache rates: {status['rate_count']} | fresh: {status['fresh']} | usable: {status['usable']}\n"
        f"Last update: {format_ts(status['updated_at'])}\n"
        f"Last safe error: {status['last_error'] or '-'}"
    )


def create_market_root_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    settings = merge_market_settings(data)
    locked = " 🔒" if not data.get("active", False) else ""
    engine = ("ON" if settings.get("market_engine_enabled") else "OFF") + locked
    return InlineKeyboardMarkup([
        [create_primary_button(f"Conversion Engine: {engine}", "admin:market_toggle")],
        [create_primary_button("Market API Configuration", "admin:market_api"), create_primary_button("Test APIs", "admin:market_test")],
        [create_primary_button("Stars Rate Settings", "admin:market_stars"), create_primary_button("Cache Settings", "admin:market_cache")],
        [create_primary_button("Market Branding", "admin:market_branding"), create_primary_button("Market Card Preview", "admin:market_card_preview")],
        [create_primary_button("Theme Settings", "admin:market_theme"), create_primary_button("Quick Assets", "admin:market_quick")],
        [create_primary_button("Live Cache Status", "admin:market_status"), create_primary_button("Conversion Help", "admin:market_help")],
        [create_danger_button("بازگشت", "menu:admin")],
    ])


def create_market_api_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    settings = merge_market_settings(data)
    return InlineKeyboardMarkup([
        [create_primary_button(f"API Updater: {'ON' if settings.get('market_api_enabled') else 'OFF'}", "admin:market_api_toggle:market_api_enabled")],
        [create_primary_button(f"CoinGecko API: {'ON' if settings.get('coingecko_enabled') else 'OFF'}", "admin:market_api_toggle:coingecko_enabled")],
        [create_primary_button(f"ExchangeRate API: {'ON' if settings.get('exchangerate_enabled') else 'OFF'}", "admin:market_api_toggle:exchangerate_enabled")],
        [create_primary_button(f"Nobitex Local Rates: {'ON' if settings.get('nobitex_enabled') else 'OFF'}", "admin:market_api_toggle:nobitex_enabled")],
        [create_primary_button("Set CoinGecko API Key", "admin:market_set_key:coingecko"), create_primary_button("Validate CoinGecko", "admin:market_validate:coingecko")],
        [create_primary_button("Set ExchangeRate API Key", "admin:market_set_key:exchangerate"), create_primary_button("Validate ExchangeRate", "admin:market_validate:exchangerate")],
        [create_primary_button("Test Live Requests", "admin:market_test"), create_danger_button("بازگشت", "admin:market_root")],
    ])


def create_market_stars_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    settings = merge_market_settings(data)
    return InlineKeyboardMarkup([
        [create_primary_button(f"Unit stars: {settings.get('stars_unit_amount')}", "admin:market_edit:stars_unit_amount")],
        [create_primary_button(f"Unit USD: {settings.get('stars_unit_usd')}", "admin:market_edit:stars_unit_usd")],
        [create_primary_button(f"Auto multiplier: {'ON' if settings.get('stars_auto_multiplier_enabled') else 'OFF'}", "admin:market_bool:stars_auto_multiplier_enabled")],
        [create_primary_button("Set manual override USD", "admin:market_edit:stars_manual_override_usd"), create_danger_button("Clear override", "admin:market_clear:stars_manual_override_usd")],
        [create_danger_button("بازگشت", "admin:market_root")],
    ])


def create_market_cache_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    settings = merge_market_settings(data)
    return InlineKeyboardMarkup([
        [create_primary_button(f"Cache TTL: {settings.get('cache_ttl_seconds')}s", "admin:market_edit:cache_ttl_seconds")],
        [create_primary_button(f"Stale fallback: {settings.get('stale_ttl_seconds')}s", "admin:market_edit:stale_ttl_seconds")],
        [create_primary_button("Refresh Cache Now", "admin:market_refresh"), create_primary_button("Live Cache Status", "admin:market_status")],
        [create_danger_button("بازگشت", "admin:market_root")],
    ])



def button_grid(items: list[tuple[str, str]], width: int = 2) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(items), width):
        rows.append([create_primary_button(label, callback) for label, callback in items[idx:idx + width]])
    return rows


def create_market_fonts_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    branding = merge_branding_settings(data)
    return InlineKeyboardMarkup([
        [create_primary_button(f"Persian Fonts: {branding.get('persian_font')}", "admin:market_fonts:persian")],
        [create_primary_button(f"English Fonts: {branding.get('english_font')}", "admin:market_fonts:english")],
        [create_primary_button(f"Persian Bold: {'ON' if branding.get('persian_bold') else 'OFF'}", "admin:market_card_bool:persian_bold")],
        [create_primary_button(f"English Bold: {'ON' if branding.get('english_bold') else 'OFF'}", "admin:market_card_bool:english_bold")],
        [create_primary_button("Preview", "admin:market_card_preview"), create_danger_button("بازگشت", "admin:market_branding")],
    ])


def create_market_font_select_keyboard(kind: str) -> InlineKeyboardMarkup:
    choices = PERSIAN_FONT_CHOICES if kind == "persian" else ENGLISH_FONT_CHOICES
    rows = button_grid([(meta["label"], f"admin:market_font_preview:{kind}:{key}") for key, meta in choices.items()], 2)
    rows.append([create_danger_button("بازگشت", "admin:market_fonts")])
    return InlineKeyboardMarkup(rows)


def create_market_confirm_keyboard(apply_cb: str, back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[create_primary_button("Apply", apply_cb), create_danger_button("Back", back_cb)]])


def create_position_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [create_primary_button("Watermark Position", "admin:market_positions:watermark")],
        [create_primary_button("Price Text Position", "admin:market_positions:price")],
        [create_primary_button("Branding Text Position", "admin:market_positions:branding")],
        [create_danger_button("بازگشت", "admin:market_branding")],
    ])


def create_market_position_keyboard(kind: str) -> InlineKeyboardMarkup:
    order = ["top_left", "top_center", "top_right", "center_left", "center", "center_right", "bottom_left", "bottom_center", "bottom_right"]
    rows = button_grid([(WATERMARK_POSITIONS[key], f"admin:market_position_preview:{kind}:{key}") for key in order], 3)
    rows.append([create_danger_button("بازگشت", "admin:market_positions")])
    return InlineKeyboardMarkup(rows)


def create_watermark_position_keyboard() -> InlineKeyboardMarkup:
    return create_market_position_keyboard("watermark")


def create_market_theme_select_keyboard() -> InlineKeyboardMarkup:
    rows = button_grid([(meta["label"], f"admin:market_theme_preview:{key}") for key, meta in CARD_THEMES.items()], 2)
    rows.append([create_danger_button("بازگشت", "admin:market_theme")])
    return InlineKeyboardMarkup(rows)


def create_market_palette_keyboard() -> InlineKeyboardMarkup:
    rows = button_grid([(meta["label"], f"admin:market_palette_preview:{key}") for key, meta in COLOR_PALETTES.items() if key != "manual"], 2)
    rows.append([create_primary_button("Manual colors", "admin:market_card_text:manual_colors")])
    rows.append([create_danger_button("بازگشت", "admin:market_theme")])
    return InlineKeyboardMarkup(rows)


def create_market_branding_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    branding = merge_branding_settings(data)
    return InlineKeyboardMarkup([
        [create_primary_button(f"Cards: {'ON' if branding.get('card_enabled') else 'OFF'}", "admin:market_card_bool:card_enabled")],
        [create_primary_button(f"Card style: {branding.get('card_style', 'classic')}", "admin:market_card_style_toggle")],
        [create_primary_button("Fonts", "admin:market_fonts"), create_primary_button(f"Text opacity: {branding.get('text_opacity')}", "admin:market_opacity_menu")],
        [create_primary_button("Branding text", "admin:market_card_text:branding_text"), create_primary_button("Watermark text", "admin:market_card_text:watermark_text")],
        [create_primary_button("Branding channel ID", "admin:market_card_text:branding_channel_id"), create_primary_button(f"Logo: {'ON' if branding.get('logo_enabled') else 'OFF'}", "admin:market_card_bool:logo_enabled")],
        [create_primary_button("Upload/Replace logo", "admin:market_card_logo_upload"), create_danger_button("Remove logo", "admin:market_logo_remove")],
        [create_primary_button("Position Settings", "admin:market_positions")],
        [create_primary_button("Preview", "admin:market_card_preview"), create_danger_button("بازگشت", "admin:market_root")],
    ])


def create_market_theme_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    branding = merge_branding_settings(data)
    return InlineKeyboardMarkup([
        [create_primary_button(f"Dark mode: {'ON' if branding.get('card_dark_mode') else 'OFF'}", "admin:market_card_bool:card_dark_mode")],
        [create_primary_button(f"Theme: {branding.get('card_theme')}", "admin:market_theme_select")],
        [create_primary_button("Preset palettes", "admin:market_palette_select")],
        [create_primary_button(f"Primary: {branding.get('card_primary_color')}", "admin:market_card_text:card_primary_color")],
        [create_primary_button(f"Secondary: {branding.get('card_secondary_color')}", "admin:market_card_text:card_secondary_color")],
        [create_primary_button("Preview", "admin:market_card_preview"), create_danger_button("بازگشت", "admin:market_root")],
    ])


def create_admin_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    status = "روشن" if data["active"] else "خاموش"
    locked = " 🔒" if not data.get("active", False) else ""
    selfb = ("ON" if data.get("self_bot_enabled") else "OFF") + locked
    wel = ("ON" if data.get("welcome_enabled") else "OFF") + locked
    market_settings = merge_market_settings(data)
    merge_branding_settings(data)
    market_state = ("ON" if market_settings.get("market_engine_enabled") else "OFF") + locked
    status_btn=create_success_button(f"وضعیت ربات: {status}","toggle:active") if data["active"] else create_danger_button(f"وضعیت ربات: {status}","toggle:active")
    return InlineKeyboardMarkup([[status_btn],[create_primary_button("ویرایش متن‌ها","admin:texts"),create_primary_button("فیچرها","admin:features")],[create_success_button("گزارش وضعیت","admin:report"),create_danger_button("راهنمای برودکست","admin:broadcast_help")],[create_primary_button(f"Self Bot: {selfb}","admin:selfbot"),create_primary_button("مدیریت سلف بات","admin:shortcut_menu")],[create_primary_button(f"Welcome: {wel}","admin:welcome_toggle"),create_primary_button("پیکربندی Welcome","admin:welcome_cfg")],[create_primary_button("Inline Menu Engine","im:root"), create_primary_button(f"Conversion Engine: {market_state}","admin:market_root")],[create_primary_button("بک‌آپ دیتابیس","admin:db_export"), create_primary_button("ایمپورت دیتابیس","admin:db_import")],[create_primary_button("لاگ‌ها","admin:logs_menu")],[create_primary_button("پیام‌های بازخورد","admin:feedback_list")],[create_danger_button("بازگشت","menu:admin")]])



async def render_admin_panel(update, context, data, *, edit=False, query=None) -> bool:
    """
    Single source of truth for admin panel UI.

    Returns True when the panel render request was handled by this UI layer.
    The return value describes only the UI side effect and must not be used
    as a global callback-pipeline stop signal.
    """
    user_data = getattr(context, "user_data", None)
    if user_data is not None:
        if user_data.get("panel_rendered_for_update") == update.update_id:
            return True
        user_data["panel_rendered_for_update"] = update.update_id

    text = "پنل ادمین"
    reply_markup = create_admin_keyboard(data)

    if edit:
        q = query or update.callback_query
        if not q:
            return False
        try:
            await q.edit_message_text(text, reply_markup=reply_markup)
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return True
            raise
        return True

    message = update.message or update.effective_message
    chat = update.effective_chat or getattr(message, "chat", None)
    if not message or not chat:
        return False
    await context.bot.send_message(chat_id=chat.id, text=text, reply_markup=reply_markup)
    return True

def create_features_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    rows=[[create_primary_button(f"{'✅' if data['features'].get(k) else '❌'} {k}",f"feature:{k}")] for k in ADMIN_FEATURES+USER_FEATURES]; rows.append([create_danger_button("بازگشت","menu:admin")]); return InlineKeyboardMarkup(rows)

def create_texts_keyboard() -> InlineKeyboardMarkup:
    rows=[[create_primary_button(name,f"text:{key}")] for key,name in TEXT_KEYS.items()]; rows.append([create_danger_button("بازگشت","menu:admin")]); return InlineKeyboardMarkup(rows)



def text_with_custom_emoji_markup(message) -> str:
    """Keep source text unchanged except wrapping custom emoji entities as tg-emoji HTML tags."""
    if not message:
        return ""
    text_html = getattr(message, "text_html", None)
    if text_html:
        return text_html
    txt = message.text or ""
    entities = [e for e in (getattr(message, "entities", []) or []) if getattr(e, "type", None) == MessageEntity.CUSTOM_EMOJI and getattr(e, "custom_emoji_id", None)]

    def u16_to_py_index(s: str, u16_index: int) -> int:
        u16_count = 0
        for i, ch in enumerate(s):
            u16_count += 2 if ord(ch) > 0xFFFF else 1
            if u16_count > u16_index:
                return i
            if u16_count == u16_index:
                return i + 1
        return len(s)

    for ent in sorted(entities, key=lambda e: e.offset, reverse=True):
        start = u16_to_py_index(txt, ent.offset)
        end = u16_to_py_index(txt, ent.offset + ent.length)
        original = txt[start:end] or "🙂"
        tag = f'<tg-emoji emoji-id="{ent.custom_emoji_id}">{original}</tg-emoji>'
        txt = txt[:start] + tag + txt[end:]
    return txt

def preserve_tg_emoji_markup(raw: str) -> str:
    pattern = re.compile(r'<tg-emoji\s+emoji-id="\d+">.*?</tg-emoji>', re.DOTALL)
    placeholders: list[str] = []
    def repl(m):
        placeholders.append(m.group(0))
        return f"__TG_EMOJI_{len(placeholders)-1}__"
    tmp = pattern.sub(repl, raw)
    escaped = html.escape(tmp)
    for i, val in enumerate(placeholders):
        escaped = escaped.replace(f"__TG_EMOJI_{i}__", val)
    return escaped




def render_html_text(raw: str, bold: bool=False) -> str:
    raw = raw if raw is not None else ""
    placeholders: list[str] = []
    pattern = re.compile(r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler|a)(?:\s+[^>]*)?>", re.IGNORECASE)
    def repl(m):
        placeholders.append(m.group(0))
        return f"__HTML_TAG_{len(placeholders)-1}__"
    tmp = pattern.sub(repl, raw)
    safe = preserve_tg_emoji_markup(tmp)
    for i, tag in enumerate(placeholders):
        safe = safe.replace(f"__HTML_TAG_{i}__", tag)
    if bold:
        # Bold plain text inside blockquotes, but keep advanced formatted segments intact.
        def bold_plain_in_blockquote(m):
            attrs = m.group(1) or ""
            inner = m.group(2) or ""
            if re.search(r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|tg-spoiler|a|tg-emoji)\b", inner, re.IGNORECASE):
                return f"<blockquote{attrs}>{inner}</blockquote>"
            return f"<blockquote{attrs}><b>{inner}</b></blockquote>"
        safe = re.sub(r"<blockquote([^>]*)>(.*?)</blockquote>", bold_plain_in_blockquote, safe, flags=re.IGNORECASE | re.DOTALL)
        return f"<b>{safe}</b>"
    return safe


async def maybe_report_watch_hit(update: Update, context: ContextTypes.DEFAULT_TYPE, source: str, text: str) -> None:
    if not text:
        return
    kws = db.get_watch("watch_keywords", [])
    if not kws:
        return
    lower = text.lower()
    matched = None
    for k in kws:
        kk = str(k or "").strip()
        if kk and kk.lower() in lower:
            matched = kk
            break
    if not matched:
        return
    report_chat = int(db.get_watch("watch_report_chat_id", 0) or 0)
    eu = update.effective_user
    ec = update.effective_chat
    db.add_keyword_hit(matched, (eu.id if eu else None), (eu.username if eu else "") or "", (eu.full_name if eu else "") or "", (ec.id if ec else 0), (ec.title if ec else "") or "", text)
    hit_count = db.keyword_count(matched)
    if not report_chat:
        return
    source_map = {"business": "Business PV", "channel": "Channel", "channel_edit": "Channel Edit", "group": "Group"}
    meta_raw = (
        f"Info: monitored message details\n"
        f"Keyword: #{matched.replace(' ', '_')}\n"
        f"TotalHits: {hit_count}\n"
        f"Source: {source_map.get(source, source)}\n"
        f"FullName: {(eu.full_name if eu else '-') or '-'}\n"
        f"UserID: {(eu.id if eu else '-')}\n"
        f"Username: @{(eu.username if eu and eu.username else '-')}\n"
        f"Chat: {(ec.title if ec else '-') or '-'} ({(ec.id if ec else '-')})\n"
        f"Time: {datetime.now(ZoneInfo('Asia/Tehran')).strftime('%Y-%m-%d %H:%M:%S')} Asia/Tehran"
    )
    meta = f"<pre><code class=\"language-ruby\">{html.escape(meta_raw)}</code></pre>"
    msg = update.message or getattr(update, "channel_post", None) or getattr(update, "business_message", None)
    dedupe_key = f"watch_dedupe:{source}:{(ec.id if ec else 0)}:{(msg.message_id if msg else 0)}:{matched}"
    if db.get_watch(dedupe_key, False):
        logging.info("watch_hit_duplicate_ignored key=%s", dedupe_key)
        return
    db.set_watch(dedupe_key, True)
    try:
        if msg and ec:
            await context.bot.forward_message(chat_id=report_chat, from_chat_id=ec.id, message_id=msg.message_id)
        await context.bot.send_message(chat_id=report_chat, text="💠 اطلاعات بیشتر از پیام مانیتور شده:", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=report_chat, text=meta, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logging.warning("watch_report_send_failed reason=%s", exc)

async def send_formatted_message(target, text: str, data: dict[str, Any]):
    return await target.reply_text(render_html_text(text, bold=data.get("bold_mode", True)), parse_mode=ParseMode.HTML)


def market_message_dedupe_key(message, source: str) -> str | None:
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = getattr(message, "message_id", None)
    if chat_id is None or message_id is None:
        return None
    return f"market_processed:{source}:{chat_id}:{message_id}"


def schedule_market_refresh(market_settings: dict[str, Any]) -> None:
    task = asyncio.create_task(MARKET_SERVICE.refresh_if_needed(dict(market_settings)))

    def _log_refresh_error(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logging.warning("market_background_refresh_failed reason=%s", exc)

    task.add_done_callback(_log_refresh_error)


def get_market_cache_for_response(market_settings: dict[str, Any]) -> dict[str, Any]:
    cache = MARKET_SERVICE.read_cache()
    status = cache_status(cache, market_settings)
    if status.get("usable"):
        if not status.get("fresh") and market_settings.get("market_api_enabled", True):
            schedule_market_refresh(market_settings)
        return cache
    return {}


async def maybe_send_market_response(message, data: dict[str, Any], *, source: str = "message", is_edit: bool = False) -> bool:
    market_settings = merge_market_settings(data)
    branding = merge_branding_settings(data)
    if not data.get("active", False) or not market_settings.get("market_engine_enabled", False):
        return False
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not text:
        return False
    intent = parse_market_intent(text)
    if is_edit and not market_settings.get("market_process_edited_messages", False) and not intent:
        logging.info("market_edit_skipped source=%s msg_id=%s", source, getattr(message, "message_id", None))
        return False
    if not intent:
        return False
    dedupe_key = market_message_dedupe_key(message, source)
    if dedupe_key and db.get_json(dedupe_key, False):
        logging.info("market_duplicate_ignored key=%s", dedupe_key)
        return True
    if dedupe_key:
        db.set_json(dedupe_key, {"processed_at": int(time.time()), "intent": intent.kind})
    cache = get_market_cache_for_response(market_settings) or await MARKET_SERVICE.refresh_if_needed(market_settings)
    try:
        response = render_market_response(text, market_settings, cache)
    except Exception as exc:
        logging.exception("market_response_failed reason=%s", exc)
        response = "⚠️ پردازش تبدیل بازار ناموفق بود، اما ربات پایدار است."
    if not response:
        return False
    if branding.get("card_enabled", False):
        try:
            image_bytes = await asyncio.to_thread(render_market_card, response, branding)
            photo = BytesIO(image_bytes)
            photo.name = "market-card.png"
            await message.reply_photo(photo=photo, caption=render_html_text(response[:1000], bold=False), parse_mode=ParseMode.HTML)
            return True
        except Exception as exc:
            logging.warning("market_card_send_failed reason=%s", exc)
    await message.reply_text(render_html_text(response, bold=False), parse_mode=ParseMode.HTML)
    return True

def is_spam(text: str, shortcuts: dict[str, str]) -> bool:
    words=re.findall(r"\w+", text.lower());
    if not words: return False
    c={}
    for w in words:
        c[w]=c.get(w,0)+1
        if c[w]>5: return True
    for s in shortcuts.keys():
        if not s or not str(s).strip(): continue
        if text.lower().count(str(s).strip().lower())>=3: return True
    return False

def match_shortcut(text: str, shortcuts: dict[str, str]) -> str | None:
    return normalize_shortcut_map(shortcuts).get(normalize_trigger(text))

def hit_limit_and_maybe_ban(uid: int, bucket: str = "global_action", window_sec: int = 60, max_hits: int = 5, ban_sec: int = 5 * 60) -> bool:
    now = int(time.time())
    key = f"rate:{bucket}:{uid}"
    hits = [int(x) for x in db.get_json(key, []) if isinstance(x, int) or str(x).isdigit()]
    hits = [t for t in hits if now - t < window_sec]
    hits.append(now)
    db.set_json(key, hits)
    if len(hits) > max_hits:
        db.set_soft_ban(uid, now + ban_sec, spam_delta=1)
        return True
    return False


def smart_rate_limit(uid: int, bucket: str, action_key: str, window_sec: int, max_total: int, same_window_sec: int, max_same: int, ban_sec: int = 5 * 60) -> bool:
    now = int(time.time())
    key = f"rate2:{bucket}:{uid}"
    action_key = normalize_trigger(action_key) or str(action_key or "-")
    raw_events = db.get_json(key, [])
    events = []
    for item in raw_events if isinstance(raw_events, list) else []:
        if isinstance(item, dict) and str(item.get("t", "")).isdigit():
            t = int(item["t"])
            if now - t < window_sec:
                events.append({"t": t, "k": str(item.get("k") or "-")})
    events.append({"t": now, "k": action_key})
    db.set_json(key, events)
    same_count = sum(1 for e in events if e["k"] == action_key and now - int(e["t"]) < same_window_sec)
    if len(events) > max_total or same_count > max_same:
        db.set_soft_ban(uid, now + ban_sec, spam_delta=1)
        return True
    return False


def callback_action_slug(action_key: str | None) -> str:
    normalized = normalize_trigger(action_key or "-") or "-"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def callback_rate_key(uid: int, action_key: str | None = None) -> str:
    if action_key is None:
        return f"rate2:{CALLBACK_RATE_BUCKET}:{uid}"
    return f"rate2:{CALLBACK_RATE_BUCKET}:{uid}:{callback_action_slug(action_key)}"


def callback_cooldown_key(uid: int, action_key: str | None = None) -> str:
    if action_key is None:
        return f"cooldown:{CALLBACK_RATE_BUCKET}:{uid}"
    return f"cooldown:{CALLBACK_RATE_BUCKET}:{uid}:{callback_action_slug(action_key)}"


def reset_callback_session(uid: int) -> None:
    # Keep this backward-compatible for old global buckets, but per-button limits
    # intentionally survive menu reopens until their own 20-minute window expires.
    db.set_json(callback_rate_key(uid), [])
    db.set_json(callback_cooldown_key(uid), 0)


def callback_button_rate_limited(uid: int, action_key: str) -> bool:
    now = int(time.time())
    action_key = normalize_trigger(action_key) or str(action_key or "-")
    cooldown_until = int(db.get_json(callback_cooldown_key(uid, action_key), 0) or 0)
    if cooldown_until > now:
        return True
    raw_events = db.get_json(callback_rate_key(uid, action_key), [])
    events = []
    for item in raw_events if isinstance(raw_events, list) else []:
        if isinstance(item, int) or str(item).isdigit():
            t = int(item)
        elif isinstance(item, dict) and str(item.get("t", "")).isdigit():
            t = int(item["t"])
        else:
            continue
        if now - t < CALLBACK_COOLDOWN_SECONDS:
            events.append(t)
    events.append(now)
    db.set_json(callback_rate_key(uid, action_key), events)
    if len(events) > CALLBACK_ALLOWED_INTERACTIONS:
        until = now + CALLBACK_COOLDOWN_SECONDS
        db.set_json(callback_cooldown_key(uid, action_key), until)
        db.set_soft_ban(uid, until, spam_delta=1)
        return True
    return False


def inline_button_rate_limited(uid: int, button_id: int) -> bool:
    return callback_button_rate_limited(uid, f"im:btn:{button_id}")


def user_button_rate_limited(uid: int, callback_data: str) -> bool:
    return callback_button_rate_limited(uid, callback_data)


def menu_command_rate_limited(uid: int, command: str) -> bool:
    return smart_rate_limit(uid, "menu_command", command, 60, 16, 30, 5)


def shortcut_rate_limited(uid: int, command: str) -> bool:
    return smart_rate_limit(uid, "shortcut", command, 60, 20, 30, 7)

async def safe_callback_answer(q, text: str | None = None, show_alert: bool = False) -> bool:
    try:
        if text is None:
            await q.answer()
        else:
            await q.answer(text, show_alert=show_alert)
        return True
    except TimedOut:
        logging.warning("callback_answer_timeout id=%s data=%r", getattr(q, "id", None), getattr(q, "data", None))
        return False
    except BadRequest as exc:
        logging.warning("callback_answer_failed id=%s data=%r reason=%s", getattr(q, "id", None), getattr(q, "data", None), exc)
        return False


def format_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts), ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def clip_report_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) > limit:
        return f"{text[:limit-1]}…"
    return text or "-"


def truncate_preserving_tg_emoji(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if not text:
        return "-"
    pattern = re.compile(r'<tg-emoji\s+emoji-id="\d+">.*?</tg-emoji>', re.DOTALL)
    out: list[str] = []
    pos = 0
    visible = 0
    truncated = False
    for match in pattern.finditer(text):
        plain = text[pos:match.start()]
        room = limit - visible
        if room <= 0:
            truncated = True
            break
        if len(plain) > room:
            out.append(plain[:room])
            visible += room
            truncated = True
            break
        out.append(plain)
        visible += len(plain)
        inner = re.sub(r"<[^>]+>", "", match.group(0)) or "🙂"
        if visible + len(inner) > limit:
            truncated = True
            break
        out.append(match.group(0))
        visible += len(inner)
        pos = match.end()
    if not truncated:
        tail = text[pos:]
        room = limit - visible
        if len(tail) > room:
            out.append(tail[:room])
            truncated = True
        else:
            out.append(tail)
    result = "".join(out).strip()
    if truncated:
        result += "…"
    return result or "-"


def format_report_message(value: Any, limit: int = 160) -> str:
    clipped = truncate_preserving_tg_emoji(value, limit)
    if clipped == "-":
        return clipped
    return preserve_tg_emoji_markup(clipped)


def build_report_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []
    nav = []
    if page > 0:
        nav.append(create_primary_button("⬅️ صفحه قبل", f"admin:report_page:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(create_success_button("صفحه بعد ➡️", f"admin:report_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([create_primary_button("🔄 بروزرسانی گزارش", f"admin:report_page:{page}"), create_danger_button("🏠 بازگشت به پنل", "menu:admin")])
    return InlineKeyboardMarkup(rows)


def build_users_report(page: int = 0, page_size: int = 8) -> tuple[str, InlineKeyboardMarkup]:
    total = db.count_users()
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    users = db.list_users(page_size, page * page_size)
    uptime = int(time.time() - START_TIME)
    header = (
        "📊 <b>گزارش وضعیت ربات</b>\n"
        f"👥 <b>کل کاربران ثبت‌شده:</b> {total}\n"
        f"📄 <b>صفحه:</b> {page + 1}/{total_pages}\n"
        f"⏱ <b>آپتایم:</b> {uptime} ثانیه\n"
        f"🧠 <b>CPU:</b> {psutil.cpu_percent()}%\n"
        f"💾 <b>RAM:</b> {psutil.virtual_memory().percent}%\n\n"
        "👤 <b>لیست کاربران</b>\n"
    )
    if not users:
        body = "\nهنوز کاربری ثبت نشده است."
    else:
        blocks = []
        for index, u in enumerate(users, start=page * page_size + 1):
            ban_until = int(u["soft_ban_until"] or 0)
            is_banned = ban_until > int(time.time())
            username = f"@{u['username']}" if u["username"] else "-"
            blocks.append(
                f"\n<b>#{index}</b> 👤 {html.escape(u['full_name'] or '-')}\n"
                f"🆔 <code>{u['user_id']}</code> | 🔗 {html.escape(username)}\n"
                f"📱 موبایل: {html.escape(u['phone'] or '-')} | 📣 جوین: {'✅' if u['is_channel_joined'] else '❌'}\n"
                f"🟢 اولین حضور: {format_ts(u['first_seen_at'])}\n"
                f"🕘 آخرین حضور: {format_ts(u['last_seen_at'])}\n"
                f"🚪 منبع: {html.escape(u['source'] or '-')} | 🚫 محدودیت: {('تا ' + format_ts(ban_until)) if is_banned else 'ندارد'}\n"
                f"⚠️ امتیاز اسپم: {int(u['spam_score'] or 0)}\n"
                f"💬 آخرین پیام: {format_report_message(u['last_message'])}"
            )
        body = "\n".join(blocks)
    return header + body, build_report_keyboard(page, total_pages)


async def disable_callback_markup(q) -> None:
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:
        logging.warning("callback_markup_disable_failed uid=%s data=%r reason=%s", getattr(getattr(q, "from_user", None), "id", None), getattr(q, "data", None), exc)


async def block_banned_callback(q, data: dict[str, Any]) -> None:
    await safe_callback_answer(q, "🚫 شما موقتاً محدود شدید. ۲۰ دقیقه دیگر دوباره تلاش کنید.", show_alert=True)


def persian_digits(value: int | str) -> str:
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


async def block_rate_limited_callback(q, data: dict[str, Any], cooldown_seconds: int = CALLBACK_COOLDOWN_SECONDS) -> None:
    minutes = max(1, int(cooldown_seconds // 60))
    await safe_callback_answer(q, f"🚫 شما بن شدید. {persian_digits(minutes)} دقیقه دیگر مجدد تلاش کنید.", show_alert=True)

async def send_or_replace_button_response(q, context: ContextTypes.DEFAULT_TYPE, button_id: int, payload: str, data: dict[str, Any]):
    if not q.message:
        return
    chat_id = q.message.chat.id
    storage_key = f"last_btn_response:{q.from_user.id}:{chat_id}:{button_id}"
    out_text = render_html_text(payload, bold=data.get("bold_mode", True))
    bc = getattr(q.message, "business_connection_id", None)
    kwargs = {"chat_id": chat_id, "text": out_text, "parse_mode": ParseMode.HTML}
    if bc:
        kwargs["business_connection_id"] = bc
    previous = db.get_json(storage_key, {})
    prev_msg_id = int(previous.get("message_id") or 0) if isinstance(previous, dict) else 0
    if prev_msg_id:
        try:
            await context.bot.edit_message_text(message_id=prev_msg_id, **kwargs)
            db.set_json(storage_key, {"message_id": prev_msg_id, "updated_at": int(time.time())})
            return
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                db.set_json(storage_key, {"message_id": prev_msg_id, "updated_at": int(time.time())})
                return
            logging.warning("button_response_edit_failed uid=%s bid=%s reason=%s", q.from_user.id, button_id, exc)
        except Exception as exc:
            logging.warning("button_response_edit_failed uid=%s bid=%s reason=%s", q.from_user.id, button_id, exc)
    try:
        sent = await context.bot.send_message(**kwargs)
        db.set_json(storage_key, {"message_id": sent.message_id, "updated_at": int(time.time())})
    except TimedOut:
        logging.warning("button_response_send_timeout uid=%s bid=%s", q.from_user.id, button_id)


async def delete_admin_trigger_message(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        kwargs = {"chat_id": message.chat.id, "message_id": message.message_id}
        bc = getattr(message, "business_connection_id", None)
        if bc:
            kwargs["business_connection_id"] = bc
        await context.bot.delete_message(**kwargs)
    except Exception as exc:
        logging.warning("admin_menu_trigger_delete_failed uid=%s msg_id=%s reason=%s", getattr(getattr(message, "from_user", None), "id", None), getattr(message, "message_id", None), exc)


def build_menu_markup(buttons) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(buttons[i]["button_text"], callback_data=f"im:btn:{buttons[i]['id']}"),
             InlineKeyboardButton(buttons[i+1]["button_text"], callback_data=f"im:btn:{buttons[i+1]['id']}")] if i+1 < len(buttons)
            else [InlineKeyboardButton(buttons[i]["button_text"], callback_data=f"im:btn:{buttons[i]['id']}")]
            for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows) if rows else None


def build_toggle_menu_keyboard(menu_id: int) -> InlineKeyboardMarkup:
    menu = db.menu_by_id(menu_id)
    if not menu:
        return build_inline_menu_admin_kb(False, False)
    rows = [[create_success_button(f"{'✅' if menu['is_active'] else '❌'} منو: {menu['command']}", f"im:togmenu:{menu_id}")]]
    for b in db.menu_buttons_all(menu_id):
        state = "✅ فعال" if b["is_active"] else "❌ غیرفعال"
        rows.append([create_primary_button(f"{state} | {b['button_text']}", f"im:togbtn:{b['id']}")])
    rows.append([create_primary_button("🔙 انتخاب منوی دیگر", "im:active"), create_danger_button("🏠 بازگشت", "im:root")])
    return InlineKeyboardMarkup(rows)



def is_safe_callback_data(raw: str | None) -> bool:
    if not raw:
        return False
    if len(raw.encode("utf-8")) > 64:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_:\-]+", raw))

async def maybe_welcome(update: Update, data: dict[str, Any], uid: int, source: str) -> bool:
    if source!="business" or not data.get("welcome_enabled",True): return False
    row=db.get_user(uid); now=int(time.time())
    if row is None or int(row["last_seen_at"] or 0)<=0 or now-int(row["last_seen_at"] or 0)>=WELCOME_COOLDOWN_SECONDS:
        await send_formatted_message(update.message if update.message else update.business_message, data.get("welcome_text","خوش آمدید"), data)
        return True
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    data=load_data(); u=update.effective_user
    db.upsert_user(u.id,u.username or "",u.full_name or u.first_name or "",None,False,"start",update.message.text)
    await send_formatted_message(update.message, f"سلام {u.first_name} 🌟\nبه ربات خوش آمدی. دکمه منو را بزن.", data)
    await update.message.reply_text("menu", reply_markup=ReplyKeyboardMarkup([["menu"]], resize_keyboard=True))

async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    data=load_data()
    if not is_admin(update.effective_user.id,data): return
    return await render_admin_panel(update, context, data, edit=False)

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not q: return
    if not is_safe_callback_data(q.data):
        logging.warning("callback_rejected_invalid data=%r", q.data)
        await safe_callback_answer(q, "Callback نامعتبر است.", show_alert=True)
        return
    data=load_data(); uid=q.from_user.id
    bc = getattr(getattr(q, "message", None), "business_connection_id", None)
    admin_allowed = await is_admin_or_business_owner(uid, data, context, bc)
    db.upsert_user(uid, q.from_user.username or "", q.from_user.full_name or q.from_user.first_name or "", None, False, "callback", q.data or "")
    if not (q.data and q.data.startswith(("im:btn:", "user:"))):
        await safe_callback_answer(q)
    if q.data and q.data.startswith("im:btn:"):
        bid=int(q.data.split(":")[2])
        db.touch_user_activity(uid, "inline_button", f"button:{bid}")
        if not admin_allowed:
            row_user = db.get_user(uid)
            if row_user and int(row_user["soft_ban_until"] or 0) > int(time.time()):
                await block_banned_callback(q, data)
                return
            if inline_button_rate_limited(uid, bid):
                await block_rate_limited_callback(q, data)
                return
        await safe_callback_answer(q)
        if not admin_allowed and (not data.get("active", False) or not data.get("inline_menu_enabled", False)):
            return
        with db.conn() as c:
            row=c.execute("SELECT action_type, action_payload FROM menu_buttons WHERE id=? AND is_active=1",(bid,)).fetchone()
        if not row: return
        handler = ACTION_REGISTRY.get(row["action_type"])
        if not handler: return
        async def send_fn(payload: str):
            await send_or_replace_button_response(q, context, bid, payload, data)
        await handler.execute(send_fn, row["action_payload"])
        return
    if q.data and q.data.startswith("im:"):
        if not is_valid_im_callback(q.data):
            logging.warning("im_callback_rejected_invalid uid=%s data=%r", uid, q.data)
            return
        if is_state_stale(uid):
            db.clear_admin_state(uid)
            await q.edit_message_text("State منقضی شد. دوباره از منو شروع کنید.", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False)))
            return
        if not admin_allowed: return
        if (not data.get("active", False)) and not admin_allowed and q.data not in {IMCB["ROOT"], IMCB["TOGGLE"], IMCB["CANCEL"], IMCB["CONFIRM_NO"]}:
            await safe_callback_answer(q, "اول باید ربات را از وضعیت سراسری روشن کنید.", show_alert=True); return
        if q.data == IMCB["ROOT"]:
            await q.edit_message_text("Inline Menu Engine", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False)))
            return
        if q.data == IMCB["TOGGLE"]:
            if not data.get("active", False) and not admin_allowed:
                await safe_callback_answer(q, "اول ربات را روشن کنید.", show_alert=True); return
            data["inline_menu_enabled"] = not data.get("inline_menu_enabled", False); save_data(data)
            db.log_admin(uid, "toggle", "inline_menu", None, None, str(data["inline_menu_enabled"]))
            await q.edit_message_text("Inline Menu Engine", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False)))
            return
        if q.data == IMCB["CREATE"]:
            db.set_admin_state(uid, "im_create_command", {})
            await q.edit_message_text("Step 1/5: command menu را ارسال کنید.", reply_markup=build_back_kb("im:root")); return
        if q.data == IMCB["ADD_BTN"]:
            menus=[{"id":m["id"],"label":f"{m['command']}"} for m in db.list_menus()]
            await q.edit_message_text("انتخاب منو:", reply_markup=paged_rows(menus,"im:addbtnpick",0)); return
        if ":page:" in q.data and q.data.startswith(("im:addbtnpick","im:mgrpick","im:editpick","im:livepick","im:togpick")):
            parts=q.data.split(":")
            prefix=":".join(parts[:2]); page=int(parts[-1])
            menus=[{"id":m["id"],"label":(f"{'✅' if m['is_active'] else '❌'} {m['command']}" if prefix=="im:togpick" else f"{m['command']}")} for m in db.list_menus()]
            await q.edit_message_text("انتخاب منو:", reply_markup=paged_rows(menus,prefix,page)); return
        if q.data == IMCB["ACTIVE"]:
            menus=[{"id":m["id"],"label":f"{'✅' if m['is_active'] else '❌'} {m['command']}"} for m in db.list_menus()]
            await q.edit_message_text("🟢 مدیریت فعال/غیرفعال منو و دکمه‌ها:", reply_markup=paged_rows(menus,"im:togpick",0)); return
        if q.data == IMCB["LIVE"]:
            menus=[{"id":m["id"],"label":f"{m['command']}"} for m in db.list_menus()]
            await q.edit_message_text("Live Menus:", reply_markup=paged_rows(menus,"im:livepick",0)); return
        if q.data.startswith("im:livepick:"):
            parts=q.data.split(":")
            if len(parts) >= 4 and parts[2] == "page":
                pass
            elif len(parts) >= 3:
                mid=int(parts[2])
                menu = next((m for m in db.list_menus() if int(m["id"]) == mid), None)
                if not menu:
                    await q.edit_message_text("این منو دیگر وجود ندارد.", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False))); return
                btns = db.menu_buttons(mid)
                rows = [[InlineKeyboardButton(btns[i]["button_text"], callback_data=f"im:btn:{btns[i]['id']}"),
                         InlineKeyboardButton(btns[i+1]["button_text"], callback_data=f"im:btn:{btns[i+1]['id']}")] if i+1 < len(btns)
                        else [InlineKeyboardButton(btns[i]["button_text"], callback_data=f"im:btn:{btns[i]['id']}")]
                        for i in range(0, len(btns), 2)]
                rows.append([InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="im:live")])
                await q.edit_message_text(f"📋 Menu: {menu['command']}\n\n{menu['preview_text']}", reply_markup=InlineKeyboardMarkup(rows)); return

        if q.data.startswith("im:togpick:"):
            try: mid=int(q.data.split(":")[2])
            except Exception: logging.warning("im_invalid_payload uid=%s data=%r", uid, q.data); return
            menu = db.menu_by_id(mid)
            if not menu:
                await q.edit_message_text("این منو دیگر وجود ندارد.", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False))); return
            await q.edit_message_text(f"⚙️ وضعیت منو: {menu['command']}", reply_markup=build_toggle_menu_keyboard(mid)); return
        if q.data.startswith("im:togmenu:"):
            mid=int(q.data.split(":")[2])
            menu = db.menu_by_id(mid)
            if not menu:
                await q.edit_message_text("این منو دیگر وجود ندارد.", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False))); return
            db.set_menu_active(mid, not bool(menu["is_active"]))
            db.log_admin(uid,"toggle","menu_active",mid,str(bool(menu["is_active"])),str(not bool(menu["is_active"])))
            menu = db.menu_by_id(mid)
            await q.edit_message_text(f"⚙️ وضعیت منو: {menu['command']}", reply_markup=build_toggle_menu_keyboard(mid)); return
        if q.data.startswith("im:togbtn:"):
            bid=int(q.data.split(":")[2])
            btn = db.button_by_id(bid)
            if not btn:
                await q.edit_message_text("این دکمه دیگر وجود ندارد.", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False))); return
            db.set_button_active(bid, not bool(btn["is_active"]))
            db.log_admin(uid,"toggle","button_active",bid,str(bool(btn["is_active"])),str(not bool(btn["is_active"])))
            menu = db.menu_by_id(int(btn["menu_id"]))
            await q.edit_message_text(f"⚙️ وضعیت منو: {menu['command'] if menu else '-'}", reply_markup=build_toggle_menu_keyboard(int(btn["menu_id"]))); return

        if q.data.startswith("im:addbtnpick:"):
            try: mid=int(q.data.split(":")[2])
            except Exception: logging.warning("im_invalid_payload uid=%s data=%r", uid, q.data); return
            if not any(int(m["id"])==mid for m in db.list_menus()):
                logging.warning("im_stale_menu_pick uid=%s menu_id=%s", uid, mid); await q.edit_message_text("منو دیگر وجود ندارد."); return
            db.set_admin_state(uid,"im_add_btn_text",{"menu_id":mid})
            await q.edit_message_text("متن دکمه جدید را ارسال کنید.", reply_markup=build_back_kb("im:root")); return
        if q.data == IMCB["MGR"] or q.data == IMCB["EDIT"]:
            menus=[{"id":m["id"],"label":f"{m['command']}"} for m in db.list_menus()]
            pref="im:mgrpick" if q.data==IMCB["MGR"] else "im:editpick"
            await q.edit_message_text("انتخاب منو:", reply_markup=paged_rows(menus,pref,0)); return
        if q.data.startswith("im:mgrpick:"):
            try: mid=int(q.data.split(":")[2])
            except Exception: logging.warning("im_invalid_payload uid=%s data=%r", uid, q.data); return
            btns=db.menu_buttons(mid)
            rows=[[InlineKeyboardButton("❌ Delete Entire Menu",callback_data=f"im:delmenu:{mid}")]]
            for b in btns: rows.append([InlineKeyboardButton(f"🧨 حذف {b['button_text']}",callback_data=f"im:delbtn:{b['id']}")])
            rows.append([InlineKeyboardButton("🧨 Cancel",callback_data="im:root")])
            await q.edit_message_text("Menu Manager", reply_markup=InlineKeyboardMarkup(rows)); return
        if q.data.startswith("im:editpick:"):
            try: mid=int(q.data.split(":")[2])
            except Exception: logging.warning("im_invalid_payload uid=%s data=%r", uid, q.data); return
            db.set_admin_state(uid,"im_edit_choose",{"menu_id":mid})
            await q.edit_message_text("Edit Menu", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("1) Edit Preview Text",callback_data="im:edit:preview")],
                [InlineKeyboardButton("2) Edit Menu Command",callback_data="im:edit:command")],
                [InlineKeyboardButton("3) Edit Button Name",callback_data="im:edit:btnname")],
                [InlineKeyboardButton("4) Edit Button Output",callback_data="im:edit:btnout")],
                [InlineKeyboardButton("🧨 Cancel",callback_data="im:cancel")]
            ])); return
        if q.data.startswith("im:edit:"):
            st=db.get_admin_state(uid) or {}
            mid=int((st.get("payload") or {}).get("menu_id",0))
            if not mid: await q.edit_message_text("State invalid", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled",False), data.get("active",False))); return
            op=q.data.split(":")[2]
            if op=="preview":
                db.set_admin_state(uid,"im_edit_preview_text",{"menu_id":mid}); await q.edit_message_text("Preview جدید را بفرستید."); return
            if op=="command":
                db.set_admin_state(uid,"im_edit_command_text",{"menu_id":mid}); await q.edit_message_text("Command جدید را بفرستید."); return
            btns=db.menu_buttons(mid)
            rows=[[InlineKeyboardButton(b["button_text"],callback_data=f"im:editpickbtn:{op}:{b['id']}")] for b in btns]
            rows.append([InlineKeyboardButton("🧨 Cancel",callback_data="im:cancel")])
            await q.edit_message_text("Button را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(rows)); return
        if q.data.startswith("im:editpickbtn:"):
            _,_,op,bid=q.data.split(":")
            if not db.button_by_id(int(bid)):
                logging.warning("im_stale_callback_button_missing uid=%s button_id=%s", uid, bid)
                await q.edit_message_text("آیتم دیگر وجود ندارد.", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False))); return
            if op=="btnname":
                db.set_admin_state(uid, "im_edit_button_name", {"button_id":int(bid)})
                await q.edit_message_text("نام جدید دکمه را بفرستید.", reply_markup=build_back_kb("im:root")); return
            db.set_admin_state(uid, "im_edit_button_output_action_type", {"button_id":int(bid)})
            await q.edit_message_text("What should this button do?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("just_text",callback_data="im:act:just_text")],[InlineKeyboardButton("Cancel",callback_data="im:cancel")]])); return
        if q.data.startswith("im:delmenu:"):
            mid=int(q.data.split(":")[2]); db.set_admin_state(uid,"im_confirm_del_menu",{"menu_id":mid})
            await q.edit_message_text("تایید حذف کامل منو؟", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("YES",callback_data="im:confirm:yes"),InlineKeyboardButton("NO",callback_data="im:confirm:no")]])); return
        if q.data.startswith("im:delbtn:"):
            bid=int(q.data.split(":")[2]); db.set_admin_state(uid,"im_confirm_del_btn",{"button_id":bid})
            await q.edit_message_text("تایید حذف دکمه؟", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("YES",callback_data="im:confirm:yes"),InlineKeyboardButton("NO",callback_data="im:confirm:no")]])); return
        if q.data == IMCB["CONFIRM_NO"] or q.data == IMCB["CANCEL"]:
            db.clear_admin_state(uid); await q.edit_message_text("لغو شد.", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False))); return
        if q.data == IMCB["CONFIRM_YES"]:
            st=db.get_admin_state(uid) or {}
            if st.get("state")=="im_confirm_del_menu":
                mid=int(st["payload"]["menu_id"])
                ok = db.delete_menu_atomic(mid)
                db.log_admin(uid,"delete_menu","menu",mid,None,json.dumps({"status":"ok" if ok else "missing"}, ensure_ascii=False))
            elif st.get("state")=="im_confirm_del_btn":
                bid=int(st["payload"]["button_id"])
                ok = db.delete_button_atomic(bid)
                db.log_admin(uid,"delete_button","menu_button",bid,None,json.dumps({"status":"ok" if ok else "missing"}, ensure_ascii=False))
            db.clear_admin_state(uid); await q.edit_message_text("انجام شد.", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False))); return
        if q.data=="im:act:just_text":
            st=db.get_admin_state(uid) or {}
            if st.get("state") in {"im_create_action_type", "im_edit_button_output_action_type"}:
                p=st.get("payload",{}); p["action_type"]="just_text"
                if st.get("state")=="im_create_action_type":
                    db.set_admin_state(uid,"im_create_output_text",p)
                    await q.edit_message_text("Step 5/5: Enter the text that will be sent when this button is clicked.", reply_markup=build_back_kb("im:root")); return
                db.set_admin_state(uid,"im_edit_button_output",p)
                await q.edit_message_text("Enter the new output message for this button", reply_markup=build_back_kb("im:root")); return
        if q.data=="im:addbtn:act:just_text":
            st=db.get_admin_state(uid) or {}
            if st.get("state")=="im_add_btn_action_type":
                p=st.get("payload",{}); p["action_type"]="just_text"; db.set_admin_state(uid,"im_add_btn_output",p)
                await q.edit_message_text("Enter the text that will be sent when this button is clicked", reply_markup=build_back_kb("im:root")); return
        if q.data=="im:create:confirm:yes":
            st=db.get_admin_state(uid) or {}
            if st.get("state")=="im_create_confirm":
                p=st["payload"]
                mid=db.create_menu(p["command"], p["preview_text"])
                bid=db.add_menu_button(mid, p["button_text"], p.get("action_type","just_text"), p["output_text"])
                db.log_admin(uid,"create","menu",mid,None,json.dumps(p,ensure_ascii=False))
                db.clear_admin_state(uid)
                await q.edit_message_text(f"✅ Menu created (menu_id={mid}, button_id={bid})", reply_markup=build_inline_menu_admin_kb(data.get("inline_menu_enabled", False), data.get("active", False))); return
        if q.data=="im:create:confirm:no":
            st=db.get_admin_state(uid) or {}
            if st.get("state")=="im_create_confirm":
                p=st.get("payload",{}); db.set_admin_state(uid,"im_create_output_text",p)
                await q.edit_message_text("Output را دوباره بفرستید.", reply_markup=build_back_kb("im:root")); return
    if q.data and q.data.startswith("user:"):
        db.touch_user_activity(uid, "user_button", q.data)
        if not admin_allowed:
            row_user = db.get_user(uid)
            if row_user and int(row_user["soft_ban_until"] or 0) > int(time.time()):
                await block_banned_callback(q, data)
                return
            if user_button_rate_limited(uid, q.data):
                await block_rate_limited_callback(q, data)
                return
        await safe_callback_answer(q)
        if not data.get("active", False) and not admin_allowed:
            await q.message.reply_text("ربات خاموش است.")
            return
        if q.data=="user:feedback": db.set_json(f"feedback_wait:{uid}", True)
        mapping={"user:services":data.get("service_text") or "متن خدمات ثبت نشده.","user:hours":data.get("hours_text") or "متن ساعات کاری ثبت نشده.","user:location":data.get("location_text") or "متن آدرس ثبت نشده.","user:faq":data.get("faq_text") or "متن FAQ ثبت نشده.","user:contact":data.get("contact_text") or "متن تماس ثبت نشده.","user:feedback":data.get("feedback_prompt_text") or "لطفاً بازخورد خود را ارسال کنید."}
        msg=mapping.get(q.data)
        if msg: await send_formatted_message(q.message, msg, data)
        return
    if not admin_allowed: return
    panel_callback_data = {"menu:admin", "toggle:active", "admin:selfbot", "admin:welcome_toggle", "admin:db_export"}
    handled_panel_ui = False
    if q.data in panel_callback_data or q.data.startswith("panel:"):
        if q.data=="toggle:active":
            data["active"]=not data["active"]
            save_data(data)
        elif q.data=="admin:selfbot":
            if not data.get("active", False):
                await safe_callback_answer(q, "اول ربات را از وضعیت سراسری روشن کنید.", show_alert=True)
            else:
                data["self_bot_enabled"]=not data.get("self_bot_enabled",False)
                save_data(data)
        elif q.data=="admin:welcome_toggle":
            data["welcome_enabled"]=not data.get("welcome_enabled",False)
            save_data(data)
        elif q.data=="admin:db_export":
            backup_market_api_secrets()
            stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            await context.bot.send_document(chat_id=uid, document=DB_PATH.open("rb"), filename=f"bot-backup-{stamp}.db")
        handled_panel_ui = await render_admin_panel(update, context, data, edit=True, query=q)
    if handled_panel_ui:
        pass
    elif q.data=="admin:features": await q.edit_message_text("فیچرها", reply_markup=create_features_keyboard(data))
    elif q.data.startswith("feature:"):
        key=q.data.split(":",1)[1]
        if key in data["features"]:
            data["features"][key]=not data["features"][key]
            if key=="admin_bold_mode": data["bold_mode"]=data["features"][key]
            save_data(data); await q.edit_message_text(f"{key} => {data['features'][key]}", reply_markup=create_features_keyboard(data))
    elif q.data=="admin:texts": await q.edit_message_text("انتخاب متن برای ویرایش", reply_markup=create_texts_keyboard())
    elif q.data.startswith("text:"):
        key=q.data.split(":",1)[1]
        if key in TEXT_KEYS:
            STATE.flow,STATE.step,STATE.admin_id,STATE.message_id,STATE.pending_key="text_edit","waiting_value",uid,q.message.message_id,key
            await q.edit_message_text(f"متن قبلی ({TEXT_KEYS[key]}):\n{data.get(key,'') or '(خالی)'}\n\nمتن جدید را ارسال کنید.")
    elif q.data=="admin:shortcut_menu": await q.edit_message_text("مدیریت سلف بات", reply_markup=create_shortcut_menu_keyboard())
    elif q.data=="admin:shortcut_view":
        sc = db.load_shortcuts()
        if sc:
            lines = []
            for k, v in sc.items():
                safe_key = html.escape(str(k))
                safe_val = preserve_tg_emoji_markup(str(v))
                lines.append(f"• {safe_key} => {safe_val}")
            out = "📚 شورت‌کات‌های فعلی:\n\n" + "\n".join(lines)
        else:
            out = "📚 شورت‌کات‌های فعلی:\n\nموردی ثبت نشده است."
        await q.edit_message_text(out, parse_mode=ParseMode.HTML, reply_markup=create_shortcut_menu_keyboard())
    elif q.data=="admin:shortcut_cfg": STATE.flow,STATE.step,STATE.admin_id,STATE.message_id,STATE.temp_shortcuts="shortcut_cfg","waiting_name",uid,q.message.message_id,{}; await q.edit_message_text("نام شورت‌کات را وارد کنید:", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="admin:shortcut_delete_menu":
        sc = db.load_shortcuts()
        kb = build_shortcut_pick_keyboard(sc, "admin:shortcut_delete_pick", "shortcut_delete_tokens") if sc else InlineKeyboardMarkup([[create_danger_button("بازگشت", "admin:shortcut_menu")]])
        await q.edit_message_text("انتخاب شورت‌کات برای حذف:", reply_markup=kb)
    elif q.data.startswith("admin:shortcut_delete_pick:"):
        token = q.data.split(":", 2)[2]
        key = db.get_watch("shortcut_delete_tokens", {}).get(token)
        if not key:
            await q.edit_message_text("آیتم معتبر نیست. دوباره لیست را باز کنید.", reply_markup=create_shortcut_menu_keyboard()); return
        db.set_watch("shortcut_delete_confirm_token", {"x": key})
        await q.edit_message_text(f"حذف «{key}» تایید می‌شود؟", reply_markup=InlineKeyboardMarkup([[create_danger_button("تایید حذف", "admin:shortcut_delete_confirm:x")], [create_primary_button("انصراف", "admin:shortcut_menu")]]))
    elif q.data.startswith("admin:shortcut_delete_confirm:"):
        token = q.data.split(":", 2)[2]
        key = db.get_watch("shortcut_delete_confirm_token", {}).get(token)
        if not key:
            await q.edit_message_text("آیتم معتبر نیست.", reply_markup=create_shortcut_menu_keyboard()); return
        db.delete_shortcut(key)
        await q.edit_message_text("شورت‌کات حذف شد.", reply_markup=create_shortcut_menu_keyboard())
    elif q.data=="admin:shortcut_edit_menu":
        sc = db.load_shortcuts()
        kb = build_shortcut_pick_keyboard(sc, "admin:shortcut_edit_pick", "shortcut_edit_tokens") if sc else InlineKeyboardMarkup([[create_danger_button("بازگشت", "admin:shortcut_menu")]])
        await q.edit_message_text("انتخاب شورت‌کات برای ویرایش:", reply_markup=kb)
    elif q.data.startswith("admin:shortcut_edit_pick:"):
        token = q.data.split(":", 2)[2]
        key = db.get_watch("shortcut_edit_tokens", {}).get(token)
        if not key:
            await q.edit_message_text("آیتم معتبر نیست. دوباره لیست را باز کنید.", reply_markup=create_shortcut_menu_keyboard()); return
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "shortcut_edit", "choose_field", uid, q.message.message_id, key
        await q.edit_message_text(f"شورت‌کات «{key}»\nکدام بخش ویرایش شود؟", reply_markup=InlineKeyboardMarkup([[create_primary_button("کلید", "admin:shortcut_edit_key"), create_primary_button("متن", "admin:shortcut_edit_value")], [create_danger_button("بازگشت", "admin:shortcut_menu")]]))
    elif q.data=="admin:shortcut_edit_key":
        STATE.step = "waiting_new_key"
        await q.edit_message_text("کلید جدید را ارسال کنید:", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="admin:shortcut_edit_value":
        STATE.step = "waiting_new_value"
        await q.edit_message_text("متن جدید را ارسال کنید:", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="admin:watch_channel_cfg":
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id = "watch_cfg", "waiting_channel_id", uid, q.message.message_id
        await q.edit_message_text("آیدی عددی چنل گزارشات را با علامت - ارسال کنید.\nمثال: -1001234567890", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="admin:watch_keywords_add":
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id = "watch_cfg", "waiting_keywords_add", uid, q.message.message_id
        await q.edit_message_text("کلمات مانیتور را با ویرگول بفرستید.\nمثال: نامحدود,تست,ربات سلف", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="admin:watch_keywords_remove":
        current = db.get_watch("watch_keywords", [])
        kb = build_watch_keyword_pick_keyboard(current, "admin:watch_kw_remove_pick", "watch_kw_remove_tokens") if current else InlineKeyboardMarkup([[create_danger_button("بازگشت", "admin:shortcut_menu")]])
        await q.edit_message_text("انتخاب کلمه مانیتور برای حذف:", reply_markup=kb)
    elif q.data.startswith("admin:watch_kw_remove_pick:"):
        token = q.data.split(":", 2)[2]
        key = db.get_watch("watch_kw_remove_tokens", {}).get(token)
        if not key:
            await q.edit_message_text("آیتم معتبر نیست.", reply_markup=create_shortcut_menu_keyboard()); return
        db.set_watch("watch_kw_remove_confirm", {"x": key})
        await q.edit_message_text(f"حذف کلمه «{key}» تایید می‌شود؟", reply_markup=InlineKeyboardMarkup([[create_danger_button("تایید حذف", "admin:watch_kw_remove_confirm:x")], [create_primary_button("انصراف", "admin:shortcut_menu")]]))
    elif q.data.startswith("admin:watch_kw_remove_confirm:"):
        token = q.data.split(":", 2)[2]
        key = db.get_watch("watch_kw_remove_confirm", {}).get(token)
        if not key:
            await q.edit_message_text("آیتم معتبر نیست.", reply_markup=create_shortcut_menu_keyboard()); return
        old = db.get_watch("watch_keywords", [])
        left = [x for x in old if str(x).strip().lower() != str(key).strip().lower()]
        db.set_watch("watch_keywords", left)
        await q.edit_message_text("کلمه مانیتور حذف شد.", reply_markup=create_shortcut_menu_keyboard())
    elif q.data=="admin:watch_keywords_stats":
        configured = db.get_watch("watch_keywords", [])
        rows = {r["keyword"]: r["cnt"] for r in db.hit_stats()}
        out = "📚 مشاهده کلمات مانیتور:\n\n" + ("\n".join([f"• {k}: {rows.get(k, 0)}" for k in configured]) if configured else "کلمه‌ای ثبت نشده.")
        await q.edit_message_text(out, reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="admin:db_import":
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id = "db_import", "waiting_document", uid, q.message.message_id
        await q.edit_message_text("فایل دیتابیس (.db) را همینجا ارسال کنید.", reply_markup=build_back_kb("menu:admin"))
    elif q.data=="admin:logs_menu":
        await q.edit_message_text("یک لاگ را انتخاب کنید:", reply_markup=build_logs_keyboard(BASE_DIR))
    elif q.data.startswith("admin:log_file:"):
        name = q.data.split(":", 2)[2]
        fp = (BASE_DIR / name).resolve()
        logs_root = (BASE_DIR / "logs").resolve()
        if logs_root not in fp.parents and fp != logs_root:
            await q.edit_message_text("مسیر فایل معتبر نیست.", reply_markup=build_back_kb("admin:logs_menu"))
            return
        if not fp.exists():
            await q.edit_message_text("فایل لاگ یافت نشد.", reply_markup=build_back_kb("admin:logs_menu"))
        else:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                pretty = humanize_log_text(fp, f.read())
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".txt", delete=False) as tempf:
                tempf.write(pretty)
                temp_path = Path(tempf.name)
            with temp_path.open("rb") as doc:
                await context.bot.send_document(chat_id=uid, document=doc, filename=f"{fp.stem}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.txt")
            temp_path.unlink(missing_ok=True)
            await q.edit_message_text("فایل لاگ ارسال شد.", reply_markup=build_back_kb("admin:logs_menu"))
    elif q.data=="shortcut:continue_yes": STATE.step="waiting_name"; await q.edit_message_text("نام شورت‌کات بعدی را وارد کنید:", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="shortcut:continue_no":
        if STATE.temp_shortcuts:
            db.save_shortcuts(STATE.temp_shortcuts)
        STATE.flow=STATE.step=STATE.pending_key=None
        STATE.temp_shortcuts=None
        await q.edit_message_text("شورت‌کات‌ها ذخیره شدند.", reply_markup=create_shortcut_menu_keyboard())
    elif q.data=="admin:market_root":
        STATE.flow = STATE.step = STATE.pending_key = None
        await q.edit_message_text(format_market_status(data), reply_markup=create_market_root_keyboard(data))
    elif q.data=="admin:market_toggle":
        if not data.get("active", False):
            await safe_callback_answer(q, "اول ربات را روشن کنید.", show_alert=True); return
        market_settings = merge_market_settings(data)
        market_settings["market_engine_enabled"] = not market_settings.get("market_engine_enabled", False)
        data["market"] = market_settings
        save_data(data)
        db.log_admin(uid, "toggle", "market_engine", None, None, str(market_settings["market_engine_enabled"]))
        await q.edit_message_text(format_market_status(data), reply_markup=create_market_root_keyboard(data))
    elif q.data=="admin:market_api":
        STATE.flow = STATE.step = STATE.pending_key = None
        await q.edit_message_text(format_market_status(data), reply_markup=create_market_api_keyboard(data))
    elif q.data.startswith("admin:market_api_toggle:"):
        key = q.data.rsplit(":", 1)[1]
        settings = merge_market_settings(data)
        if key in {"market_api_enabled", "coingecko_enabled", "exchangerate_enabled", "nobitex_enabled"}:
            settings[key] = not settings.get(key, True)
            data["market"] = settings
            save_data(data)
        await q.edit_message_text(format_market_status(data), reply_markup=create_market_api_keyboard(data))
    elif q.data.startswith("admin:market_set_key:"):
        provider = q.data.rsplit(":", 1)[1]
        if provider not in {"coingecko", "exchangerate"}: return
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "market_cfg", "waiting_api_key", uid, q.message.message_id, provider
        await q.edit_message_text(f"کلید API برای {provider} را ارسال کنید. قبل از ذخیره، درخواست واقعی اعتبارسنجی انجام می‌شود.", reply_markup=build_back_kb("admin:market_api"))
    elif q.data.startswith("admin:market_validate:"):
        provider = q.data.rsplit(":", 1)[1]
        key = os.getenv("COINGECKO_API_KEY" if provider=="coingecko" else "EXCHANGERATE_API_KEY", "")
        result = await asyncio.to_thread(validate_market_api_key, provider, key, merge_market_settings(data).get("request_timeout_seconds", 8))
        await q.edit_message_text(("✅ " if result.get("ok") else "❌ ") + result.get("message", "unknown"), reply_markup=create_market_api_keyboard(data))
    elif q.data=="admin:market_test":
        await MARKET_SERVICE.refresh(merge_market_settings(data))
        await q.edit_message_text("درخواست زنده انجام شد.\n\n" + format_market_status(data), reply_markup=create_market_api_keyboard(data))
    elif q.data=="admin:market_stars":
        STATE.flow = STATE.step = STATE.pending_key = None
        await q.edit_message_text("⭐ Stars Rate Settings", reply_markup=create_market_stars_keyboard(data))
    elif q.data=="admin:market_cache":
        STATE.flow = STATE.step = STATE.pending_key = None
        await q.edit_message_text(format_market_status(data), reply_markup=create_market_cache_keyboard(data))
    elif q.data=="admin:market_status":
        await q.edit_message_text(format_market_status(data), reply_markup=create_market_root_keyboard(data))
    elif q.data=="admin:market_refresh":
        await MARKET_SERVICE.refresh(merge_market_settings(data))
        await q.edit_message_text("کش بروزرسانی شد.\n\n" + format_market_status(data), reply_markup=create_market_cache_keyboard(data))
    elif q.data=="admin:market_quick":
        settings = merge_market_settings(data)
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "market_cfg", "waiting_quick_assets", uid, q.message.message_id, "quick_assets"
        await q.edit_message_text("Quick Assets فعلی:\n" + ", ".join(settings.get("quick_assets", [])) + "\n\nلیست جدید را با کاما ارسال کنید. نمونه: BTC,ETH,TRX,TON,USDT", reply_markup=build_back_kb("admin:market_root"))
    elif q.data=="admin:market_help":
        await q.edit_message_text(market_help_text(is_admin=True), reply_markup=create_market_root_keyboard(data))
    elif q.data=="admin:market_branding":
        STATE.flow = STATE.step = STATE.pending_key = None
        merge_branding_settings(data)
        await q.edit_message_text("🎨 Market Card Branding", reply_markup=create_market_branding_keyboard(data))
    elif q.data=="admin:market_theme":
        STATE.flow = STATE.step = STATE.pending_key = None
        merge_branding_settings(data)
        await q.edit_message_text("🌓 Market Card Theme", reply_markup=create_market_theme_keyboard(data))
    elif q.data=="admin:market_fonts":
        STATE.flow = STATE.step = STATE.pending_key = None
        await q.edit_message_text("🔤 Market Fonts", reply_markup=create_market_fonts_keyboard(data))
    elif q.data.startswith("admin:market_fonts:"):
        kind = q.data.rsplit(":", 1)[1]
        if kind not in {"persian", "english"}: return
        await q.edit_message_text("فونت را انتخاب کنید:", reply_markup=create_market_font_select_keyboard(kind))
    elif q.data.startswith("admin:market_font_preview:"):
        _, _, kind, font_key = q.data.split(":", 3)
        branding = merge_branding_settings(data)
        field = "persian_font" if kind == "persian" else "english_font"
        choices = PERSIAN_FONT_CHOICES if kind == "persian" else ENGLISH_FONT_CHOICES
        if font_key not in choices: return
        preview_branding = dict(branding, **{field: font_key})
        sample = "قیمت بیت‌کوین\n💵 70,000 USD\n✨ تبدیل 100 ترون\n💸 600,000 تومان"
        try:
            image_bytes = await asyncio.to_thread(render_market_card, sample, preview_branding)
            await context.bot.send_photo(chat_id=uid, photo=BytesIO(image_bytes), caption=f"Preview: {choices[font_key]['label']}")
        except Exception as exc:
            logging.warning("market_font_preview_failed reason=%s", exc)
        await q.edit_message_text("این فونت اعمال شود؟", reply_markup=create_market_confirm_keyboard(f"admin:market_font_apply:{kind}:{font_key}", f"admin:market_fonts:{kind}"))
    elif q.data.startswith("admin:market_font_apply:"):
        _, _, kind, font_key = q.data.split(":", 3)
        branding = merge_branding_settings(data)
        choices = PERSIAN_FONT_CHOICES if kind == "persian" else ENGLISH_FONT_CHOICES
        if font_key not in choices: return
        branding["persian_font" if kind == "persian" else "english_font"] = font_key
        data.setdefault("market", {})["card"] = branding
        save_data(data)
        await q.edit_message_text("✅ فونت اعمال شد.", reply_markup=create_market_fonts_keyboard(data))
    elif q.data=="admin:market_positions":
        await q.edit_message_text("Position Settings", reply_markup=create_position_settings_keyboard())
    elif q.data=="admin:market_watermark_positions":
        await q.edit_message_text("Watermark position را انتخاب کنید:", reply_markup=create_watermark_position_keyboard())
    elif q.data.startswith("admin:market_positions:"):
        kind = q.data.rsplit(":", 1)[1]
        if kind not in {"watermark", "price", "branding"}: return
        await q.edit_message_text("Position را انتخاب کنید:", reply_markup=create_market_position_keyboard(kind))
    elif q.data.startswith("admin:market_position_preview:"):
        parts = q.data.split(":")
        if len(parts) == 4:
            kind, pos = parts[2], parts[3]
        else:
            kind, pos = "watermark", q.data.rsplit(":", 1)[1]
        field_map = {"watermark": "watermark_position", "price": "price_position", "branding": "branding_position"}
        if kind not in field_map or pos not in WATERMARK_POSITIONS: return
        branding = merge_branding_settings(data)
        preview_branding = dict(branding, **{field_map[kind]: pos})
        try:
            image_bytes = await asyncio.to_thread(render_market_card, "✨ 1 TRX :\n💸 59,042 toman\n💵 $0.3456 dollar\nنمونه متن فارسی", preview_branding)
            await context.bot.send_photo(chat_id=uid, photo=BytesIO(image_bytes), caption=f"Preview: {kind} / {WATERMARK_POSITIONS[pos]}")
        except Exception as exc:
            logging.warning("market_position_preview_failed reason=%s", exc)
        await q.edit_message_text("این موقعیت ذخیره شود؟", reply_markup=create_market_confirm_keyboard(f"admin:market_position_apply:{kind}:{pos}", f"admin:market_positions:{kind}"))
    elif q.data.startswith("admin:market_position_apply:"):
        parts = q.data.split(":")
        if len(parts) == 4:
            kind, pos = parts[2], parts[3]
        else:
            kind, pos = "watermark", q.data.rsplit(":", 1)[1]
        field_map = {"watermark": "watermark_position", "price": "price_position", "branding": "branding_position"}
        if kind not in field_map or pos not in WATERMARK_POSITIONS: return
        branding = merge_branding_settings(data)
        branding[field_map[kind]] = pos
        data.setdefault("market", {})["card"] = branding
        save_data(data)
        await q.edit_message_text("✅ Position ذخیره شد.", reply_markup=create_position_settings_keyboard())
    elif q.data=="admin:market_theme_select":
        await q.edit_message_text("Theme را انتخاب کنید:", reply_markup=create_market_theme_select_keyboard())
    elif q.data.startswith("admin:market_theme_preview:"):
        theme_key = q.data.rsplit(":", 1)[1]
        if theme_key not in CARD_THEMES: return
        theme = CARD_THEMES[theme_key]
        branding = merge_branding_settings(data)
        preview_branding = dict(branding, card_theme=theme_key, card_primary_color=theme["primary"], card_secondary_color=theme["secondary"], card_dark_mode=theme["dark"])
        try:
            image_bytes = await asyncio.to_thread(render_market_card, "Theme preview\nقیمت BTC: 70,000 USD", preview_branding)
            await context.bot.send_photo(chat_id=uid, photo=BytesIO(image_bytes), caption=f"Preview: {theme['label']}")
        except Exception as exc:
            logging.warning("market_theme_preview_failed reason=%s", exc)
        await q.edit_message_text("این theme اعمال شود؟", reply_markup=create_market_confirm_keyboard(f"admin:market_theme_apply:{theme_key}", "admin:market_theme_select"))
    elif q.data.startswith("admin:market_theme_apply:"):
        theme_key = q.data.rsplit(":", 1)[1]
        if theme_key not in CARD_THEMES: return
        theme = CARD_THEMES[theme_key]
        branding = merge_branding_settings(data)
        branding.update({"card_theme": theme_key, "card_primary_color": theme["primary"], "card_secondary_color": theme["secondary"], "card_dark_mode": bool(theme["dark"])})
        data.setdefault("market", {})["card"] = branding
        save_data(data)
        await q.edit_message_text("✅ Theme اعمال شد.", reply_markup=create_market_theme_keyboard(data))
    elif q.data=="admin:market_palette_select":
        await q.edit_message_text("Color palette را انتخاب کنید:", reply_markup=create_market_palette_keyboard())
    elif q.data.startswith("admin:market_palette_preview:"):
        palette_key = q.data.rsplit(":", 1)[1]
        if palette_key not in COLOR_PALETTES: return
        palette = COLOR_PALETTES[palette_key]
        branding = merge_branding_settings(data)
        preview_branding = dict(branding, card_primary_color=palette["primary"], card_secondary_color=palette["secondary"])
        try:
            image_bytes = await asyncio.to_thread(render_market_card, "Palette preview\nنمونه رنگ کارت", preview_branding)
            await context.bot.send_photo(chat_id=uid, photo=BytesIO(image_bytes), caption=f"Preview: {palette['label']}")
        except Exception as exc:
            logging.warning("market_palette_preview_failed reason=%s", exc)
        await q.edit_message_text("این palette اعمال شود؟", reply_markup=create_market_confirm_keyboard(f"admin:market_palette_apply:{palette_key}", "admin:market_palette_select"))
    elif q.data.startswith("admin:market_palette_apply:"):
        palette_key = q.data.rsplit(":", 1)[1]
        if palette_key not in COLOR_PALETTES: return
        palette = COLOR_PALETTES[palette_key]
        branding = merge_branding_settings(data)
        branding["card_primary_color"] = palette["primary"]
        branding["card_secondary_color"] = palette["secondary"]
        data.setdefault("market", {})["card"] = branding
        save_data(data)
        await q.edit_message_text("✅ Palette اعمال شد.", reply_markup=create_market_theme_keyboard(data))
    elif q.data=="admin:market_card_style_toggle":
        branding = merge_branding_settings(data)
        branding["card_style"] = "advanced" if branding.get("card_style") != "advanced" else "classic"
        data.setdefault("market", {})["card"] = branding
        save_data(data)
        await q.edit_message_text("✅ نوع کارت تغییر کرد. برای دیدن نتیجه Preview بگیرید.", reply_markup=create_market_branding_keyboard(data))
    elif q.data=="admin:market_opacity_menu":
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "market_card_cfg", "waiting_number", uid, q.message.message_id, "text_opacity"
        await q.edit_message_text("Opacity متن میزان شفافیت نوشته‌های روی کارت است؛ عدد 255 کاملاً پررنگ و 40 خیلی کم‌رنگ است. مقدار 40 تا 255 را ارسال کنید و بعد Preview بگیرید.", reply_markup=build_back_kb("admin:market_branding"))
    elif q.data=="admin:market_logo_remove":
        branding = merge_branding_settings(data)
        branding["logo_enabled"] = False
        branding["logo_path"] = ""
        data.setdefault("market", {})["card"] = branding
        save_data(data)
        await q.edit_message_text("✅ لوگو حذف/غیرفعال شد.", reply_markup=create_market_branding_keyboard(data))
    elif q.data=="admin:market_card_preview":
        branding = merge_branding_settings(data)
        sample = "<b>🔺 قیمت TRX</b>\n<blockquote>💰 <b>جزئیات قیمت</b>\n💵 دلاری: <b>$0.3456</b>\n🇮🇷 تومانی: <b>59,042</b>\n</blockquote>\n<blockquote>📊 <b>تغییرات روزانه</b>\n🟢 رشد: <b>0.20%</b></blockquote>\n🕘 <code>1405/03/10 | 06:28:42</code>"
        try:
            image_bytes = await asyncio.to_thread(render_market_card, sample, branding)
            await context.bot.send_photo(chat_id=uid, photo=BytesIO(image_bytes), caption="Market Card Preview")
            await q.edit_message_text("✅ پیش‌نمایش کارت ارسال شد.", reply_markup=create_market_root_keyboard(data))
        except Exception as exc:
            logging.warning("market_card_preview_failed reason=%s", exc)
            await q.edit_message_text(f"❌ ساخت کارت ناموفق بود: {exc}", reply_markup=create_market_root_keyboard(data))
    elif q.data.startswith("admin:market_card_bool:"):
        field = q.data.rsplit(":", 1)[1]
        branding = merge_branding_settings(data)
        if field in {"card_enabled", "logo_enabled", "card_dark_mode", "persian_bold", "english_bold"}:
            branding[field] = not branding.get(field, False)
            data.setdefault("market", {})["card"] = branding
            save_data(data)
        kb = create_market_theme_keyboard(data) if field == "card_dark_mode" else create_market_fonts_keyboard(data) if field in {"persian_bold", "english_bold"} else create_market_branding_keyboard(data)
        await q.edit_message_text("🎨 Market Card Settings", reply_markup=kb)
    elif q.data=="admin:market_card_logo_upload":
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "market_card_cfg", "waiting_logo", uid, q.message.message_id, "logo_path"
        await q.edit_message_text("لوگوی کارت را به صورت photo یا فایل تصویر ارسال کنید.", reply_markup=build_back_kb("admin:market_branding"))
    elif q.data.startswith("admin:market_card_text:"):
        field = q.data.rsplit(":", 1)[1]
        if field not in {"branding_text", "branding_channel_id", "watermark_text", "watermark_position", "card_theme", "card_primary_color", "card_secondary_color", "manual_colors"}: return
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "market_card_cfg", "waiting_text", uid, q.message.message_id, field
        await q.edit_message_text(f"مقدار جدید برای {field} را ارسال کنید.", reply_markup=build_back_kb("admin:market_branding"))
    elif q.data.startswith("admin:market_card_number:"):
        field = q.data.rsplit(":", 1)[1]
        if field not in {"text_opacity"}: return
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "market_card_cfg", "waiting_number", uid, q.message.message_id, field
        await q.edit_message_text(f"عدد جدید برای {field} را ارسال کنید. مثال: 220", reply_markup=build_back_kb("admin:market_branding"))
    elif q.data.startswith("admin:market_edit:"):
        field = q.data.rsplit(":", 1)[1]
        if field not in {"stars_unit_amount", "stars_unit_usd", "stars_manual_override_usd", "cache_ttl_seconds", "stale_ttl_seconds"}: return
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "market_cfg", "waiting_number", uid, q.message.message_id, field
        await q.edit_message_text(f"مقدار عددی جدید برای {field} را ارسال کنید.", reply_markup=build_back_kb("admin:market_root"))
    elif q.data.startswith("admin:market_bool:"):
        field = q.data.rsplit(":", 1)[1]
        settings = merge_market_settings(data)
        if field in {"stars_auto_multiplier_enabled"}:
            settings[field] = not settings.get(field, False)
            data["market"] = settings
            save_data(data)
        await q.edit_message_text("⭐ Stars Rate Settings", reply_markup=create_market_stars_keyboard(data))
    elif q.data.startswith("admin:market_clear:"):
        field = q.data.rsplit(":", 1)[1]
        settings = merge_market_settings(data)
        if field == "stars_manual_override_usd":
            settings[field] = None
            data["market"] = settings
            save_data(data)
        await q.edit_message_text("⭐ Stars Rate Settings", reply_markup=create_market_stars_keyboard(data))
    elif q.data=="admin:welcome_cfg":
        STATE.flow,STATE.step,STATE.admin_id,STATE.message_id,STATE.pending_key="welcome_cfg","waiting_value",uid,q.message.message_id,"welcome_text"
        await q.edit_message_text(f"📝 حالت ویرایش Welcome فعال شد.\n\n✅ همین حالا متن جدید ولکام را در پیام بعدی ارسال کنید.\n\nمتن فعلی:\n{render_html_text(data.get('welcome_text','') or '(خالی)')}", parse_mode=ParseMode.HTML, reply_markup=build_back_kb("menu:admin"))
    elif q.data=="admin:report" or q.data.startswith("admin:report_page:"):
        page = 0
        if q.data.startswith("admin:report_page:"):
            try:
                page = int(q.data.rsplit(":", 1)[1])
            except Exception:
                page = 0
        rep, kb = build_users_report(page)
        await q.edit_message_text(rep, parse_mode=ParseMode.HTML, reply_markup=kb)
    elif q.data=="admin:broadcast_help": await q.edit_message_text("برای برودکست از دستور /broadcast <متن> استفاده کنید.", reply_markup=build_back_kb("menu:admin"))
    elif q.data=="admin:feedback_list":
        rows=db.list_feedbacks(100)
        out="📝 همه بازخوردها:\n\n" + ("\n\n".join([f"• {r['full_name'] or '-'} (@{r['username'] or '-'})\n  پیام: {r['message']}" for r in rows]) if rows else "موردی ثبت نشده است.")
        await q.edit_message_text(out, reply_markup=build_back_kb("menu:admin"))
    elif q.data.startswith("feedback:view:"):
        vid=q.data.split(":",2)[2]; msg=db.get_json(f"feedback_last:{vid}","(یافت نشد)")
        await q.edit_message_text(f"متن پیام بازخورد:\n{msg}", reply_markup=build_back_kb("menu:admin"))




def message_interaction_text(message) -> str:
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if text:
        return text
    if getattr(message, "media_group_id", None):
        return "[media group]"
    if getattr(message, "sticker", None):
        return "[sticker]"
    if getattr(message, "voice", None):
        return "[voice]"
    if getattr(message, "video_note", None):
        return "[voice note]"
    if getattr(message, "video", None):
        return "[video]"
    if getattr(message, "animation", None):
        return "[gif]"
    if getattr(message, "document", None):
        return "[document]"
    if getattr(message, "photo", None):
        return "[photo]"
    if getattr(message, "audio", None):
        return "[audio]"
    if getattr(message, "contact", None):
        return "[contact]"
    if getattr(message, "location", None):
        return "[location]"
    return "[interaction]"


def is_welcome_due(prev) -> bool:
    now = int(time.time())
    return prev is None or int(prev["last_seen_at"] or 0) <= 0 or now - int(prev["last_seen_at"] or 0) >= WELCOME_COOLDOWN_SECONDS


async def send_business_welcome_once(message, context: ContextTypes.DEFAULT_TYPE, data: dict[str, Any], uid: int, prev) -> bool:
    if not data.get("welcome_enabled", True) or not is_welcome_due(prev):
        return False
    try:
        out_welcome = render_html_text(data.get("welcome_text", "خوش آمدید"), bold=data.get("bold_mode", True))
        await context.bot.send_message(
            chat_id=message.chat.id,
            text=out_welcome,
            parse_mode=ParseMode.HTML,
            business_connection_id=getattr(message, "business_connection_id", None),
        )
        logging.info("welcome_trigger_business uid=%s", uid)
        return True
    except Exception as exc:
        logging.exception("welcome_business_failed uid=%s reason=%s", uid, exc)
        return False

def is_fresh_business_update(message_date) -> bool:
    if message_date is None:
        return True
    try:
        return int(message_date.timestamp()) >= (int(time.time()) - BUSINESS_UPDATE_FRESHNESS_SECONDS)
    except Exception:
        return True

async def business_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bm=getattr(update,"business_message",None)
    if not bm: return
    if not is_fresh_business_update(getattr(bm, "date", None)):
        logging.info("business_update_skipped_stale msg_id=%s date=%s", getattr(bm, "message_id", None), getattr(bm, "date", None))
        return
    data=load_data()
    text_markup = text_with_custom_emoji_markup(bm) if getattr(bm, "text", None) else ""
    src_txt = text_markup or message_interaction_text(bm)
    txt=(getattr(bm, "text", None) or getattr(bm, "caption", None) or "").strip()
    uid = await message_sender_id(bm, data, context)
    if uid is None:
        logging.warning("business_message_missing_sender msg_id=%s", getattr(bm, "message_id", None))
        return
    admin_allowed = await is_admin_or_business_owner(uid, data, context, getattr(bm, "business_connection_id", None))
    await maybe_report_watch_hit(update, context, "business", txt)
    if not data.get("active", False) and not admin_allowed:
        logging.info("business_ignored_bot_inactive uid=%s", uid)
        return
    prev=db.get_user(uid)
    if prev and int(prev["soft_ban_until"] or 0) > int(time.time()) and not admin_allowed:
        return
    db.upsert_user(uid,(bm.from_user.username if bm.from_user else "") or "",(bm.from_user.full_name if bm.from_user else bm.chat.full_name) or "",None,False,"business",src_txt)
    if (data.get("active", False) and data.get("inline_menu_enabled", False)) or admin_allowed:
        menu = db.menu_by_command(txt)
        if menu:
            if not admin_allowed and menu_command_rate_limited(uid, txt):
                logging.warning("business_menu_command_rate_limited uid=%s command=%r", uid, txt)
                return
            buttons = db.menu_buttons(menu["id"])
            if not admin_allowed:
                reset_callback_session(uid)
            out = render_html_text(menu["preview_text"], bold=data.get("bold_mode", True))
            kwargs = {"chat_id": bm.chat.id, "text": out, "parse_mode": ParseMode.HTML, "reply_markup": build_menu_markup(buttons)}
            bc = getattr(bm, "business_connection_id", None)
            if bc: kwargs["business_connection_id"] = bc
            if admin_allowed or await is_admin_authored_business_message(bm, data, context):
                await delete_admin_trigger_message(bm, context)
            try:
                await context.bot.send_message(**kwargs)
            except TimedOut:
                logging.warning("business_inline_menu_send_timeout uid=%s chat_id=%s command=%r", uid, bm.chat.id, txt)
            return
    if await maybe_send_market_response(bm, data, source="business"):
        return
    sc=db.load_shortcuts(); resp=match_shortcut(txt, sc)
    if resp and data.get("self_bot_enabled", False):
        if not admin_allowed and shortcut_rate_limited(uid, txt):
            await send_formatted_message(bm, "<b>🚫 محدودیت ضداسپم فعال شد.</b>\n\nبه دلیل ارسال سریع/پرتکرار، به مدت <b>۵ دقیقه</b> محدود شدید.", data)
            return
        bc=getattr(bm,"business_connection_id",None)
        if data.get("self_bot_enabled") and admin_allowed:
            out_self = render_html_text(resp, bold=data.get("bold_mode", True))
            try:
                edit_kwargs = {"chat_id": bm.chat.id, "message_id": bm.message_id, "text": out_self, "parse_mode": ParseMode.HTML}
                if bc:
                    edit_kwargs["business_connection_id"] = bc
                await context.bot.edit_message_text(**edit_kwargs)
                logging.info("business_admin_shortcut_edit_ok uid=%s msg_id=%s", uid, bm.message_id)
                return
            except Exception as exc:
                logging.warning("business_admin_shortcut_edit_failed uid=%s reason=%s", uid, exc)
                try:
                    del_kwargs = {"chat_id": bm.chat.id, "message_id": bm.message_id}
                    if bc:
                        del_kwargs["business_connection_id"] = bc
                    await context.bot.delete_message(**del_kwargs)
                    logging.info("business_admin_shortcut_delete_ok uid=%s msg_id=%s", uid, bm.message_id)
                except Exception as delete_exc:
                    logging.warning("business_admin_shortcut_delete_failed uid=%s reason=%s", uid, delete_exc)
        out_text = render_html_text(resp, bold=data.get("bold_mode", True))
        kwargs={"chat_id":bm.chat.id,"text":out_text,"parse_mode":ParseMode.HTML}
        if bc: kwargs["business_connection_id"]=bc
        try:
            await context.bot.send_message(**kwargs)
        except TimedOut:
            logging.warning("business_shortcut_send_timeout uid=%s chat_id=%s text=%r", uid, bm.chat.id, txt)
            return
        logging.info("business_shortcut_sent uid=%s text=%s",uid, txt)
        return
    if await maybe_send_market_response(bm, data, source="business"):
        return
    if await send_business_welcome_once(bm, context, data, uid, prev):
        return
    logging.info("business_shortcut_no_match uid=%s text=%s", uid, txt)

async def all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if getattr(update,"business_message",None): await business_message_handler(update, context); return
    if getattr(update, "edited_business_message", None):
        data = load_data()
        await maybe_send_market_response(update.edited_business_message, data, source="business", is_edit=True)
        return
    if getattr(update, "channel_post", None):
        cp = update.channel_post
        await maybe_report_watch_hit(update, context, "channel", cp.text or cp.caption or "")
        return
    if getattr(update, "edited_channel_post", None):
        update.channel_post = update.edited_channel_post
        cp = update.channel_post
        await maybe_report_watch_hit(update, context, "channel_edit", cp.text or cp.caption or "")
        return
    is_edited_message = False
    if getattr(update, "edited_message", None):
        is_edited_message = True
        update.message = update.edited_message
        data = load_data()
        edit_source = "group" if update.effective_chat and update.effective_chat.type in {"group", "supergroup"} else "message"
        if await maybe_send_market_response(update.message, data, source=edit_source, is_edit=True):
            return
        if update.effective_chat and update.effective_chat.type in {"group", "supergroup"}:
            await maybe_report_watch_hit(update, context, "group", update.message.text or update.message.caption or "")
            return
    if not update.message or not update.effective_user: return
    if STATE.admin_id == (update.effective_user.id if update.effective_user else None) and STATE.flow == "db_import" and STATE.step == "waiting_document":
        txt_cmd = (update.message.text or "").strip().lower() if update.message else ""
        if txt_cmd in {"panel", "/panel", "menu"}:
            STATE.flow = STATE.step = None
            if txt_cmd in {"panel", "/panel"} and update.effective_user and is_admin(update.effective_user.id, load_data()):
                await render_admin_panel(update, context, load_data(), edit=False)
            elif txt_cmd == "menu":
                await update.message.reply_text("منوی کاربر", reply_markup=create_menu_keyboard())
            return
        doc = update.message.document
        if not doc:
            await update.message.reply_text("لطفاً فایل .db ارسال کنید.")
            return
        file = await doc.get_file()
        tmp = BASE_DIR / "bot.new.db"
        await file.download_to_drive(custom_path=str(tmp))
        backup_old = BASE_DIR / f"bot.backup.{int(time.time())}.db"
        if DB_PATH.exists():
            DB_PATH.replace(backup_old)
        tmp.replace(DB_PATH)
        restore_market_api_secrets()
        STATE.flow = STATE.step = None
        await update.message.reply_text("✅ دیتابیس جدید جایگزین شد. ربات در حال ریلود...")
        os.execlp("python", "python", str(BASE_DIR / "bot.py"))
        return
    if update.effective_chat and update.effective_chat.type in {"group", "supergroup"}:
        await maybe_report_watch_hit(update, context, "group", update.message.text or update.message.caption or "")
        data = load_data()
        await maybe_send_market_response(update.message, data, source="group")
        return
    # Monitoring is event-driven from business/channel/group updates.
    data=load_data(); uid=update.effective_user.id; src_txt=text_with_custom_emoji_markup(update.message); txt=(update.message.text or "").strip()
    if txt.lower() in {"panel", "/panel"} and is_admin(uid,data):
        STATE.flow=STATE.step=STATE.pending_key=None
        STATE.temp_shortcuts=None
        db.clear_admin_state(uid)
        return await render_admin_panel(update, context, data, edit=False)
    if txt.casefold() in {"help convert", "help market", "market help", "راهنمای تبدیل", "راهنمای بازار"}:
        await update.message.reply_text(market_help_text(is_admin=is_admin(uid, data)))
        return
    st=db.get_admin_state(uid) if is_admin(uid, data) else None
    if st:
        s=st.get("state"); p=st.get("payload",{})
        if s=="im_create_command":
            if len(txt) > 64 or not txt.strip():
                await update.message.reply_text("command نامعتبر است."); return
            if db.has_command_ci(txt):
                await update.message.reply_text("این command تکراری است. دوباره بفرستید."); return
            p["command"]=txt; db.set_admin_state(uid,"im_create_preview_text",p); await update.message.reply_text("Step 2/5: menu preview text را بفرستید."); return
        if s=="im_create_preview_text":
            p["preview_text"]=src_txt; db.set_admin_state(uid,"im_create_button_text",p); await update.message.reply_text("Step 3/5: متن دکمه را بفرستید."); return
        if s=="im_create_button_text":
            if len((update.message.text or "")) > 120:
                await update.message.reply_text("متن دکمه بیش از حد طولانی است."); return
            p["button_text"]=src_txt; db.set_admin_state(uid,"im_create_action_type",p); await update.message.reply_text("Step 4/5: What should this button do?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("just_text",callback_data="im:act:just_text")],[InlineKeyboardButton("Cancel",callback_data="im:cancel")]])); return
        if s=="im_create_output_text":
            p["output_text"]=src_txt; db.set_admin_state(uid,"im_create_confirm",p); await update.message.reply_text("Confirm?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("YES",callback_data="im:create:confirm:yes"),InlineKeyboardButton("NO",callback_data="im:create:confirm:no")]])); return
        if s=="im_add_btn_text":
            if len((update.message.text or "")) > 120:
                await update.message.reply_text("متن دکمه بیش از حد طولانی است."); return
            p["button_text"]=src_txt; db.set_admin_state(uid,"im_add_btn_action_type",p); await update.message.reply_text("What should this button do?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("just_text",callback_data="im:addbtn:act:just_text")],[InlineKeyboardButton("Cancel",callback_data="im:cancel")]])); return
        if s=="im_add_btn_output":
            db.add_menu_button(int(p["menu_id"]), p["button_text"], p.get("action_type","just_text"), src_txt); db.clear_admin_state(uid); await update.message.reply_text("دکمه اضافه شد."); return
        if s=="im_edit_preview_text":
            mid=int(p["menu_id"]); db.update_menu_preview(mid, src_txt); db.log_admin(uid,"edit","menu_preview",mid,None,src_txt); db.clear_admin_state(uid); await update.message.reply_text("Preview بروزرسانی شد."); return
        if s=="im_edit_command_text":
            mid=int(p["menu_id"])
            if db.has_command_ci(txt, exclude_menu_id=mid):
                await update.message.reply_text("این command تکراری است."); return
            db.update_menu_command(mid, txt); db.log_admin(uid,"edit","menu_command",mid,None,txt); db.clear_admin_state(uid); await update.message.reply_text("Command بروزرسانی شد."); return
        if s=="im_edit_button_name":
            bid=int(p["button_id"]); db.update_button_name(bid, src_txt); db.log_admin(uid,"edit","menu_button_name",bid,None,src_txt); db.clear_admin_state(uid); await update.message.reply_text("نام دکمه بروزرسانی شد."); return
        if s=="im_edit_button_output":
            bid=int(p["button_id"]); db.update_button_output(bid, src_txt); db.log_admin(uid,"edit","menu_button_output",bid,None,src_txt); db.clear_admin_state(uid); await update.message.reply_text("خروجی دکمه بروزرسانی شد."); return
    admin_allowed = is_admin(uid, data)
    row=db.get_user(uid)
    if row and int(row["soft_ban_until"] or 0)>int(time.time()) and not admin_allowed: return
    db.upsert_user(uid, update.effective_user.username or "", update.effective_user.full_name or update.effective_user.first_name or "", update.message.contact.phone_number if update.message.contact else None, False, "message", txt)

    if (data.get("active", False) and data.get("inline_menu_enabled", False)) or admin_allowed:
        m = db.menu_by_command(txt)
        if m:
            if not admin_allowed and menu_command_rate_limited(uid, txt):
                logging.warning("menu_command_rate_limited uid=%s command=%r", uid, txt)
                return
            btns=db.menu_buttons(m["id"])
            if not admin_allowed:
                reset_callback_session(uid)
            if admin_allowed:
                await delete_admin_trigger_message(update.message, context)
                await context.bot.send_message(chat_id=update.effective_chat.id, text=render_html_text(m["preview_text"], bold=data.get("bold_mode", True)), parse_mode=ParseMode.HTML, reply_markup=build_menu_markup(btns))
            else:
                await update.message.reply_text(render_html_text(m["preview_text"], bold=data.get("bold_mode", True)), parse_mode=ParseMode.HTML, reply_markup=build_menu_markup(btns))
            return
    if txt.lower()=="menu":
        if not data.get("active", False) and not admin_allowed:
            return
        logging.info("user_menu_open uid=%s", uid)
        if not admin_allowed:
            reset_callback_session(uid)
        await update.message.reply_text("منوی کاربر", reply_markup=create_menu_keyboard())
        return


    if STATE.admin_id==uid and STATE.flow=="market_card_cfg" and can_edit_flow(uid):
        branding = merge_branding_settings(data)
        field = STATE.pending_key or ""
        if STATE.step=="waiting_logo":
            tg_file = None
            suffix = ".png"
            if update.message.photo:
                tg_file = await update.message.photo[-1].get_file()
            elif update.message.document and str(update.message.document.mime_type or "").startswith("image/"):
                tg_file = await update.message.document.get_file()
                suffix = Path(update.message.document.file_name or "logo.png").suffix or ".png"
            if not tg_file:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="لطفاً فقط تصویر ارسال کنید.", reply_markup=build_back_kb("admin:market_branding")); return
            logo_dir = BASE_DIR / "assets" / "market"
            logo_dir.mkdir(parents=True, exist_ok=True)
            logo_path = logo_dir / f"card_logo{suffix}"
            await tg_file.download_to_drive(custom_path=str(logo_path))
            branding["logo_path"] = str(logo_path)
            branding["logo_enabled"] = True
            data.setdefault("market", {})["card"] = branding
            save_data(data)
            STATE.flow = STATE.step = STATE.pending_key = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="✅ لوگوی کارت ذخیره و فعال شد.", reply_markup=create_market_branding_keyboard(data)); return
        if STATE.step=="waiting_text":
            value = txt.strip()
            if field == "manual_colors":
                parts = [x.strip() for x in re.split(r"[,،\s]+", value) if x.strip()]
                if len(parts) < 2 or not all(re.fullmatch(r"#?[0-9a-fA-F]{6}", x) for x in parts[:2]):
                    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="دو رنگ معتبر ارسال کنید. نمونه: #2336ff #8a2be2", reply_markup=build_back_kb("admin:market_theme")); return
                branding["card_primary_color"] = parts[0] if parts[0].startswith("#") else f"#{parts[0]}"
                branding["card_secondary_color"] = parts[1] if parts[1].startswith("#") else f"#{parts[1]}"
                data.setdefault("market", {})["card"] = branding
                save_data(data)
                STATE.flow = STATE.step = STATE.pending_key = None
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="✅ رنگ‌های دستی ذخیره شد. برای مشاهده Preview بگیرید.", reply_markup=create_market_theme_keyboard(data)); return
            if field in {"card_primary_color", "card_secondary_color"} and not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="رنگ نامعتبر است. نمونه: #2336ff", reply_markup=build_back_kb("admin:market_theme")); return
            if field in {"card_primary_color", "card_secondary_color"} and not value.startswith("#"):
                value = f"#{value}"
            if field == "branding_channel_id":
                try:
                    if value and int(value) >= 0:
                        raise ValueError()
                except Exception:
                    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="Channel ID نامعتبر است. نمونه معتبر: -1001234567890", reply_markup=build_back_kb("admin:market_branding")); return
                if value:
                    try:
                        me = await context.bot.get_chat_member(int(value), context.bot.id)
                        value = str(int(value))
                        feedback = f"✅ Channel ID ذخیره شد. وضعیت ربات: {getattr(me, 'status', 'unknown')}"
                    except Exception as exc:
                        feedback = f"⚠️ Channel ID ذخیره شد اما اعتبارسنجی دسترسی ناموفق بود: {exc}"
                else:
                    feedback = "✅ Channel ID پاک شد."
            else:
                feedback = f"✅ {field} بروزرسانی شد."
            if field in {"watermark_position", "branding_position", "price_position"} and value not in WATERMARK_POSITIONS:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="از دکمه‌های Position استفاده کنید.", reply_markup=create_position_settings_keyboard()); return
            branding[field] = value[:256]
            data.setdefault("market", {})["card"] = branding
            save_data(data)
            STATE.flow = STATE.step = STATE.pending_key = None
            kb = create_market_theme_keyboard(data) if field.startswith("card_") else create_market_branding_keyboard(data)
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=feedback, reply_markup=kb); return
        if STATE.step=="waiting_number":
            try:
                value = int(float(txt.strip()))
            except Exception:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="عدد نامعتبر است.", reply_markup=build_back_kb("admin:market_branding")); return
            if field == "text_opacity" and not 40 <= value <= 255:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="opacity باید بین 40 و 255 باشد.", reply_markup=build_back_kb("admin:market_branding")); return
            branding[field] = value
            data.setdefault("market", {})["card"] = branding
            save_data(data)
            STATE.flow = STATE.step = STATE.pending_key = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"✅ {field} بروزرسانی شد: {value}", reply_markup=create_market_branding_keyboard(data)); return

    if STATE.admin_id==uid and STATE.flow=="market_cfg" and can_edit_flow(uid):
        settings = merge_market_settings(data)
        if STATE.step=="waiting_api_key":
            provider = STATE.pending_key or ""
            env_key = "COINGECKO_API_KEY" if provider == "coingecko" else "EXCHANGERATE_API_KEY" if provider == "exchangerate" else ""
            api_key = txt.strip()
            if not env_key:
                STATE.flow = STATE.step = STATE.pending_key = None
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="Provider نامعتبر است.", reply_markup=create_market_api_keyboard(data)); return
            result = await asyncio.to_thread(validate_market_api_key, provider, api_key, settings.get("request_timeout_seconds", 8))
            if result.get("ok"):
                set_env_value(env_key, api_key)
                STATE.flow = STATE.step = STATE.pending_key = None
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="✅ کلید معتبر بود و در .env ذخیره شد.\n" + result.get("message", ""), reply_markup=create_market_api_keyboard(data)); return
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="❌ کلید ذخیره نشد؛ اعتبارسنجی واقعی ناموفق بود.\n" + result.get("message", ""), reply_markup=build_back_kb("admin:market_api")); return
        if STATE.step=="waiting_number":
            field = STATE.pending_key or ""
            try:
                value = float(txt.replace(",", "").strip())
            except Exception:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="عدد نامعتبر است. دوباره مقدار عددی ارسال کنید.", reply_markup=build_back_kb("admin:market_root")); return
            if field in {"cache_ttl_seconds", "stale_ttl_seconds"}:
                value = int(value)
            if field == "cache_ttl_seconds" and not 30 <= int(value) <= 3600:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="TTL باید بین 30 و 3600 ثانیه باشد.", reply_markup=build_back_kb("admin:market_cache")); return
            if field == "stale_ttl_seconds" and not 30 <= int(value) <= 86400:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="Stale fallback باید بین 30 و 86400 ثانیه باشد.", reply_markup=build_back_kb("admin:market_cache")); return
            if field in {"stars_unit_amount", "stars_unit_usd", "stars_manual_override_usd"} and value < 0:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="مقدار نمی‌تواند منفی باشد.", reply_markup=build_back_kb("admin:market_stars")); return
            settings[field] = value
            data["market"] = settings
            save_data(data)
            STATE.flow = STATE.step = STATE.pending_key = None
            kb = create_market_cache_keyboard(data) if field in {"cache_ttl_seconds", "stale_ttl_seconds"} else create_market_stars_keyboard(data)
            suffix = ""
            if field in {"stars_unit_amount", "stars_unit_usd", "stars_manual_override_usd"}:
                unit = max(float(settings.get("stars_unit_amount") or 1000), 1.0)
                effective = float(settings.get("stars_manual_override_usd") or settings.get("stars_unit_usd") or 0) / unit
                suffix = f"\nPreview: 2000 Stars ≈ {effective * 2000:.4f} USD"
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"✅ {field} بروزرسانی شد: {value}{suffix}", reply_markup=kb); return
        if STATE.step=="waiting_quick_assets":
            raw_items = [x.strip() for x in re.split(r"[,،\s]+", txt) if x.strip()]
            normalized = normalize_asset_list(raw_items, settings.get("quick_assets", []))
            settings["quick_assets"] = normalized
            data["market"] = settings
            save_data(data)
            STATE.flow = STATE.step = STATE.pending_key = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="✅ Quick Assets بروزرسانی شد:\n" + ", ".join(normalized), reply_markup=create_market_root_keyboard(data)); return

    if STATE.admin_id==uid and STATE.flow=="text_edit" and STATE.step=="waiting_value" and STATE.pending_key and can_edit_flow(uid):
        key=STATE.pending_key; old=data.get(key,""); data[key]=src_txt; save_data(data); STATE.flow=STATE.step=STATE.pending_key=None
        preview = f"ذخیره شد.\nمتن قبلی:\n{render_html_text(old or '(خالی)')}\n\nمتن جدید:\n{render_html_text(src_txt)}"
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=preview, parse_mode=ParseMode.HTML, reply_markup=create_texts_keyboard()); return
    if STATE.admin_id==uid and STATE.flow=="shortcut_cfg" and can_edit_flow(uid):
        if STATE.step=="waiting_name": STATE.pending_key=txt; STATE.step="waiting_value"; await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"نام شورت‌کات: {txt}\nمتن شورت‌کات را وارد کنید:", reply_markup=build_back_kb("admin:shortcut_menu")); return
        if STATE.step=="waiting_value":
            key=(STATE.pending_key or "").strip()
            if key:
                (STATE.temp_shortcuts or {})[key]=src_txt
                db.save_shortcuts({key: src_txt})
                logging.info("shortcut_saved_single key=%s", key)
            STATE.step="confirm_continue"
            kb=InlineKeyboardMarkup([[create_success_button("ادامه","shortcut:continue_yes"),create_danger_button("پایان","shortcut:continue_no")]])
            await context.bot.edit_message_text(chat_id=update.effective_chat.id,message_id=STATE.message_id,text="آیا ادامه می‌دهید؟",reply_markup=kb)
            return
    if STATE.admin_id==uid and STATE.flow=="shortcut_edit" and can_edit_flow(uid):
        old_key = (STATE.pending_key or "").strip()
        if not old_key:
            STATE.flow = STATE.step = STATE.pending_key = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="کلید قبلی یافت نشد. دوباره از منو وارد شوید.", reply_markup=create_shortcut_menu_keyboard()); return
        sc = db.load_shortcuts()
        if STATE.step=="waiting_new_key":
            new_key = txt.strip()
            if not new_key:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="کلید جدید خالی است. دوباره وارد کنید.", reply_markup=build_back_kb("admin:shortcut_menu")); return
            if old_key not in sc:
                STATE.flow = STATE.step = STATE.pending_key = None
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="شورت‌کات قبلی پیدا نشد.", reply_markup=create_shortcut_menu_keyboard()); return
            if new_key != old_key and new_key in sc:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="این کلید قبلاً وجود دارد. کلید دیگری بفرستید.", reply_markup=build_back_kb("admin:shortcut_menu")); return
            val = sc[old_key]
            db.delete_shortcut(old_key)
            db.save_shortcuts({new_key: val})
            STATE.flow = STATE.step = STATE.pending_key = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"کلید شورت‌کات تغییر کرد:\n{old_key} -> {new_key}", reply_markup=create_shortcut_menu_keyboard()); return
        if STATE.step=="waiting_new_value":
            if old_key not in sc:
                STATE.flow = STATE.step = STATE.pending_key = None
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="شورت‌کات پیدا نشد.", reply_markup=create_shortcut_menu_keyboard()); return
            db.save_shortcuts({old_key: src_txt})
            STATE.flow = STATE.step = STATE.pending_key = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="متن شورت‌کات بروزرسانی شد.", reply_markup=create_shortcut_menu_keyboard()); return
    if STATE.admin_id==uid and STATE.flow=="welcome_cfg" and STATE.step=="waiting_value" and can_edit_flow(uid):
        old=data.get("welcome_text",""); data["welcome_text"]=src_txt; save_data(data); STATE.flow=STATE.step=None
        preview = f"✅ Welcome بروزرسانی شد و از همین دیتابیس خوانده می‌شود.\n\nقبلی:\n{render_html_text(old or '(خالی)')}\n\nجدید:\n{render_html_text(src_txt)}"
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=preview, parse_mode=ParseMode.HTML, reply_markup=create_admin_keyboard(data)); return
    if STATE.admin_id==uid and STATE.flow=="watch_cfg" and can_edit_flow(uid):
        if STATE.step=="waiting_channel_id":
            raw = txt.strip()
            try:
                cid = int(raw)
                if cid >= 0:
                    raise ValueError()
            except Exception:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="فرمت نامعتبر است. نمونه: -1001234567890", reply_markup=build_back_kb("admin:shortcut_menu"))
                return
            db.set_watch("watch_report_chat_id", cid)
            STATE.flow = STATE.step = None
            test_ok = "نامشخص"
            try:
                me = await context.bot.get_chat_member(cid, context.bot.id)
                test_ok = f"ok: {getattr(me, 'status', 'unknown')}"
                utc_now = datetime.utcnow()
                tehran_now = datetime.now(ZoneInfo("Asia/Tehran"))
                await context.bot.send_message(chat_id=cid, text=f"✅ اتصال ربات به چنل گزارشات موفق بود.\nUTC: {utc_now.strftime('%Y-%m-%d %H:%M:%S')}\nAsia/Tehran: {tehran_now.strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception as exc:
                test_ok = f"failed: {exc}"
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"چنل گزارشات ذخیره شد: {cid}\nوضعیت دسترسی ربات: {test_ok}", reply_markup=create_shortcut_menu_keyboard()); return
        if STATE.step=="waiting_keywords_add":
            old = db.get_watch("watch_keywords", [])
            merged = list(dict.fromkeys(old + parse_keyword_csv(txt)))
            db.set_watch("watch_keywords", merged)
            STATE.flow = STATE.step = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"کلمات ذخیره شدند.\n{', '.join(merged) if merged else '-'}", reply_markup=create_shortcut_menu_keyboard()); return
        if STATE.step=="waiting_keywords_remove":
            old = db.get_watch("watch_keywords", [])
            remove_set = {x.lower() for x in parse_keyword_csv(txt)}
            left = [x for x in old if str(x).lower() not in remove_set]
            db.set_watch("watch_keywords", left)
            STATE.flow = STATE.step = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"حذف انجام شد.\nباقی‌مانده: {', '.join(left) if left else '-'}", reply_markup=create_shortcut_menu_keyboard()); return

    if not data.get("active", False) and not is_admin(uid, data):
        return

    if not is_edited_message and await maybe_send_market_response(update.message, data, source="message"):
        return

    shortcuts=db.load_shortcuts()
    if is_spam(txt,shortcuts):
        ban_until = int(time.time())+SOFT_BAN_SECONDS
        db.set_soft_ban(uid, ban_until)
        logging.warning("anti_spam_trigger uid=%s ban_until=%s text=%s", uid, ban_until, txt)
        return

    if db.get_json(f"feedback_wait:{uid}", False):
        db.add_feedback(uid, update.effective_user.username or "", update.effective_user.full_name or update.effective_user.first_name or "", txt)
        db.set_json(f"feedback_wait:{uid}", False)
        await send_formatted_message(update.message, data.get("feedback_success_text","✅ بازخورد شما با موفقیت ثبت شد."), data)
        admin_id=int(data.get("admin_id") or os.getenv("ADMIN_ID") or 0)
        if admin_id:
            db.set_json(f"feedback_last:{uid}", txt)
            kb=InlineKeyboardMarkup([[create_primary_button("مشاهده متن پیام", f"feedback:view:{uid}")]])
            await context.bot.send_message(admin_id, f"شما یک پیام جدید دارید از طرف {update.effective_user.full_name or update.effective_user.first_name}", reply_markup=kb)
        return

    if data.get("self_bot_enabled", False):
        resp=match_shortcut(txt,shortcuts)
        if resp:
            if not admin_allowed and shortcut_rate_limited(uid, txt):
                await send_formatted_message(update.message, "<b>🚫 محدودیت ضداسپم فعال شد.</b>\n\nبه دلیل ارسال سریع/پرتکرار، به مدت <b>۵ دقیقه</b> محدود شدید.", data)
                return
            if is_admin(uid,data):
                out_text = render_html_text(resp, bold=data.get("bold_mode", True))
                try:
                    await update.message.edit_text(out_text, parse_mode=ParseMode.HTML)
                    logging.info("admin_shortcut_edit_ok uid=%s msg_id=%s", uid, update.message.message_id)
                except Exception as exc:
                    logging.warning("admin_shortcut_edit_failed uid=%s msg_id=%s reason=%s", uid, update.message.message_id, exc)
                    try:
                        await update.message.delete()
                        logging.info("admin_shortcut_delete_ok uid=%s msg_id=%s", uid, update.message.message_id)
                    except Exception as delete_exc:
                        logging.warning("admin_shortcut_delete_failed uid=%s msg_id=%s reason=%s", uid, update.message.message_id, delete_exc)
                    await send_formatted_message(update.message, resp, data)
                return
            if not is_admin(uid,data): await send_formatted_message(update.message, resp, data); return

    if not is_edited_message and await maybe_send_market_response(update.message, data, source="message"):
        return

    if data["active"] and not is_admin(uid,data): await send_formatted_message(update.message, data.get("offline_message",""), data)

async def help_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    data = load_data()
    await update.message.reply_text(market_help_text(is_admin=is_admin(update.effective_user.id, data)))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and str(context.args[0]).lower() in {"convert", "market", "conversion"}:
        await help_market(update, context)
        return
    if update.message:
        await update.message.reply_text("برای راهنمای تبدیل و بازار از /help_market یا /help convert استفاده کنید.")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    data=load_data()
    if not is_admin(update.effective_user.id,data): return
    text=" ".join(context.args).strip()
    if not text: await update.message.reply_text("Usage: /broadcast your message"); return
    sent=0
    for u in db.list_users(100000):
        try: await context.bot.send_message(int(u["user_id"]), text); sent+=1
        except Exception as exc: logging.warning("broadcast failed %s %s", u["user_id"], exc)
    await update.message.reply_text(f"ارسال شد برای {sent} کاربر")

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE): logging.exception("Unhandled error: %s", context.error)


def build_back_kb(target: str="menu:admin") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[create_danger_button("بازگشت", target)]])


def can_edit_flow(uid: int) -> bool:
    return STATE.admin_id == uid and STATE.message_id is not None and STATE.flow is not None


def is_state_stale(admin_id: int) -> bool:
    with db.conn() as c:
        row = c.execute("SELECT updated_at FROM admin_states WHERE admin_id=?", (admin_id,)).fetchone()
    if not row:
        return False
    return int(time.time()) - int(row["updated_at"] or 0) > FSM_TTL_SECONDS


def get_market_runtime_settings() -> dict[str, Any]:
    data = load_data()
    settings = merge_market_settings(data)
    settings["_global_active"] = bool(data.get("active", False))
    return settings


async def post_init(app: Application) -> None:
    MARKET_SERVICE.bind_store(db)
    restore_market_api_secrets()
    app.bot_data["market_task"] = asyncio.create_task(MARKET_SERVICE.run_forever(get_market_runtime_settings))


async def post_shutdown(app: Application) -> None:
    MARKET_SERVICE.stop()
    task = app.bot_data.get("market_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main() -> None:
    setup_logging(); db.init(); MARKET_SERVICE.bind_store(db); restore_market_api_secrets(); backup_market_api_secrets(); save_data(load_data())
    token=os.getenv("BOT_TOKEN")
    if not token: raise RuntimeError("BOT_TOKEN is missing")
    app=Application.builder().token(token).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("panel", panel)); app.add_handler(CommandHandler("help_market", help_market)); app.add_handler(CommandHandler("help", help_command)); app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(callbacks)); app.add_handler(MessageHandler(filters.ALL, all_messages)); app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__": main()
