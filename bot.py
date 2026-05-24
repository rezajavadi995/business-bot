import html
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from dataclasses import dataclass
import tempfile
from pathlib import Path
from typing import Any

import psutil
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram import MessageEntity
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from features.log_export import build_logs_keyboard, humanize_log_text

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "bot.db"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "bot.log"
load_dotenv(ENV_PATH)

START_TIME = int(time.time())
SOFT_BAN_SECONDS = 20 * 60
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
    def conn(self):
        c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row; return c
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
    def get_json(self, key: str, default: Any) -> Any:
        with self.conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
            return default if not row else json.loads(row["v"])
    def set_json(self, key: str, value: Any):
        with self.conn() as c:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, json.dumps(value, ensure_ascii=False)))
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
    def list_users(self, limit: int = 50):
        with self.conn() as c: return c.execute("SELECT * FROM users ORDER BY last_seen_at DESC LIMIT ?", (limit,)).fetchall()
    def set_soft_ban(self, user_id: int, until_ts: int):
        with self.conn() as c: c.execute("UPDATE users SET soft_ban_until=? WHERE user_id=?", (until_ts, user_id))
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
    def delete_shortcut(self, key: str):
        with self.conn() as c:
            c.execute("DELETE FROM shortcuts WHERE name=?", (key,))

db = DB(DB_PATH)

def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])

def get_default_data() -> dict[str, Any]:
    return {"admin_id": int(os.getenv("ADMIN_ID") or 0), "active": False, "force_join_channel": os.getenv("FORCE_JOIN_CHANNEL", ""), "bold_mode": True,
            "welcome_enabled": False, "welcome_text": "سلام 🌟\nبه پیج بیزینسی ما خوش آمدید.", "self_bot_enabled": False,
            "features": {**{k: True for k in ADMIN_FEATURES}, **{k: True for k in USER_FEATURES}}, "offline_message": "پیام شما دریافت شد. به‌زودی پاسخ می‌دهیم.",
            "service_text": "", "hours_text": "", "location_text": "", "faq_text": "", "contact_text": "", "feedback_prompt_text": "لطفاً بازخورد خود را ارسال کنید.", "feedback_success_text": "✅ بازخورد شما با موفقیت ثبت شد."}

def load_data() -> dict[str, Any]:
    data=db.get_json("settings", get_default_data()); d=get_default_data()
    for k,v in d.items(): data.setdefault(k,v)
    data.setdefault("features",{})
    for k,v in d["features"].items(): data["features"].setdefault(k,v)
    return data

def save_data(data: dict[str, Any]) -> None: db.set_json("settings", data)
def is_admin(user_id: int, data: dict[str, Any]) -> bool: return user_id == int(data.get("admin_id") or os.getenv("ADMIN_ID") or 0)
def create_primary_button(text: str, callback: str) -> InlineKeyboardButton: return InlineKeyboardButton(f"🔵 {text}", callback_data=callback)
def create_success_button(text: str, callback: str) -> InlineKeyboardButton: return InlineKeyboardButton(f"🟢 {text}", callback_data=callback)
def create_danger_button(text: str, callback: str) -> InlineKeyboardButton: return InlineKeyboardButton(f"🔴 {text}", callback_data=callback)

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
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


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

def create_admin_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    status = "روشن" if data["active"] else "خاموش"
    locked = " ⛔️" if not data.get("active", False) else ""
    selfb = ("ON" if data.get("self_bot_enabled") else "OFF") + locked
    wel = ("ON" if data.get("welcome_enabled") else "OFF") + locked
    status_btn=create_success_button(f"وضعیت ربات: {status}","toggle:active") if data["active"] else create_danger_button(f"وضعیت ربات: {status}","toggle:active")
    return InlineKeyboardMarkup([[status_btn],[create_primary_button("ویرایش متن‌ها","admin:texts"),create_primary_button("فیچرها","admin:features")],[create_success_button("گزارش وضعیت","admin:report"),create_danger_button("راهنمای برودکست","admin:broadcast_help")],[create_primary_button(f"Self Bot: {selfb}","admin:selfbot"),create_primary_button("مدیریت سلف بات","admin:shortcut_menu")],[create_primary_button(f"Welcome: {wel}","admin:welcome_toggle"),create_primary_button("پیکربندی Welcome","admin:welcome_cfg")],[create_primary_button("بک‌آپ دیتابیس","admin:db_export"), create_primary_button("ایمپورت دیتابیس","admin:db_import")],[create_primary_button("لاگ‌ها","admin:logs_menu"), create_primary_button("پیام‌های بازخورد","admin:feedback_list")],[create_danger_button("بازگشت","menu:admin")]])

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
    safe = preserve_tg_emoji_markup(raw if raw is not None else "")
    return f"<b>{safe}</b>" if bold else safe

async def send_formatted_message(target, text: str, data: dict[str, Any]):
    await target.reply_text(render_html_text(text, bold=data.get("bold_mode", True)), parse_mode=ParseMode.HTML)

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
    t = (text or "").strip().lower()
    if not t:
        return None
    for key, value in shortcuts.items():
        kk = str(key or "").strip().lower()
        if kk and t == kk:
            return value
    return None

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
    await update.message.reply_text("پنل ادمین", reply_markup=create_admin_keyboard(data))

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not q: return
    await q.answer(); data=load_data(); uid=q.from_user.id
    if q.data.startswith("user:"):
        if not data.get("active", False):
            await q.message.reply_text("ربات خاموش است.")
            return
        if q.data=="user:feedback": db.set_json(f"feedback_wait:{uid}", True)
        mapping={"user:services":data.get("service_text") or "متن خدمات ثبت نشده.","user:hours":data.get("hours_text") or "متن ساعات کاری ثبت نشده.","user:location":data.get("location_text") or "متن آدرس ثبت نشده.","user:faq":data.get("faq_text") or "متن FAQ ثبت نشده.","user:contact":data.get("contact_text") or "متن تماس ثبت نشده.","user:feedback":data.get("feedback_prompt_text") or "لطفاً بازخورد خود را ارسال کنید."}
        msg=mapping.get(q.data)
        if msg: await send_formatted_message(q.message, msg, data)
        return
    if not is_admin(uid,data): return
    if q.data=="menu:admin": await q.edit_message_text("پنل ادمین", reply_markup=create_admin_keyboard(data))
    elif q.data=="toggle:active": data["active"]=not data["active"]; save_data(data); await q.edit_message_text("وضعیت بروزرسانی شد.", reply_markup=create_admin_keyboard(data))
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
    elif q.data=="admin:selfbot":
        if not data.get("active", False):
            await q.answer("اول ربات را از وضعیت سراسری روشن کنید.", show_alert=True)
            return
        data["self_bot_enabled"]=not data.get("self_bot_enabled",False); save_data(data); await q.edit_message_text("وضعیت Self Bot تغییر کرد.", reply_markup=create_admin_keyboard(data))
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
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id = "watch_cfg", "waiting_keywords_remove", uid, q.message.message_id
        await q.edit_message_text("کلمات جهت حذف را با ویرگول بفرستید.", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="admin:watch_keywords_stats":
        configured = db.get_watch("watch_keywords", [])
        rows = {r["keyword"]: r["cnt"] for r in db.hit_stats()}
        out = "📚 مشاهده کلمات مانیتور:\n\n" + ("\n".join([f"• {k}: {rows.get(k, 0)} hit" for k in configured]) if configured else "کلمه‌ای ثبت نشده.")
        await q.edit_message_text(out, reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="admin:db_export":
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        await context.bot.send_document(chat_id=uid, document=DB_PATH.open("rb"), filename=f"bot-backup-{stamp}.db")
        await q.edit_message_text("بک‌آپ دیتابیس ارسال شد.", reply_markup=create_admin_keyboard(data))
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
    elif q.data=="admin:welcome_toggle":
        if not data.get("active", False):
            await q.answer("اول ربات را از وضعیت سراسری روشن کنید.", show_alert=True)
            return
        data["welcome_enabled"]=not data.get("welcome_enabled",False); save_data(data); await q.edit_message_text("وضعیت Welcome تغییر کرد.", reply_markup=create_admin_keyboard(data))
    elif q.data=="admin:welcome_cfg": STATE.flow,STATE.step,STATE.admin_id,STATE.message_id,STATE.pending_key="welcome_cfg","waiting_value",uid,q.message.message_id,"welcome_text"; await q.edit_message_text(f"متن فعلی Welcome:\n{data.get('welcome_text','')}\n\nمتن جدید را ارسال کنید.", reply_markup=build_back_kb("menu:admin"))
    elif q.data=="admin:report":
        users=db.list_users(50)
        lines=[f"• نام: {u['full_name'] or '-'}\n  آیدی: {u['user_id']} | یوزرنیم: @{u['username'] or '-'}\n  موبایل: {u['phone'] or '-'} | جوین: {'yes' if u['is_channel_joined'] else 'no'}" for u in users]
        rep=f"📊 گزارش سیستم\n• کاربران: {len(users)}\n• آپتایم: {int(time.time()-START_TIME)} ثانیه\n• CPU: {psutil.cpu_percent()}%\n• RAM: {psutil.virtual_memory().percent}%\n\n👤 لیست کاربران:\n\n" + ("\n\n".join(lines) if lines else "-")
        await q.edit_message_text(rep, reply_markup=build_back_kb("menu:admin"))
    elif q.data=="admin:broadcast_help": await q.edit_message_text("برای برودکست از دستور /broadcast <متن> استفاده کنید.", reply_markup=build_back_kb("menu:admin"))
    elif q.data=="admin:feedback_list":
        rows=db.list_feedbacks(100)
        out="📝 همه بازخوردها:\n\n" + ("\n\n".join([f"• {r['full_name'] or '-'} (@{r['username'] or '-'})\n  پیام: {r['message']}" for r in rows]) if rows else "موردی ثبت نشده است.")
        await q.edit_message_text(out, reply_markup=build_back_kb("menu:admin"))
    elif q.data.startswith("feedback:view:"):
        vid=q.data.split(":",2)[2]; msg=db.get_json(f"feedback_last:{vid}","(یافت نشد)")
        await q.edit_message_text(f"متن پیام بازخورد:\n{msg}", reply_markup=build_back_kb("menu:admin"))



def is_fresh_business_update(message_date) -> bool:
    if message_date is None:
        return True
    try:
        return int(message_date.timestamp()) >= (int(time.time()) - BUSINESS_UPDATE_FRESHNESS_SECONDS)
    except Exception:
        return True

async def business_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bm=getattr(update,"business_message",None)
    if not bm or not bm.text: return
    if not is_fresh_business_update(getattr(bm, "date", None)):
        logging.info("business_update_skipped_stale msg_id=%s date=%s", getattr(bm, "message_id", None), getattr(bm, "date", None))
        return
    data=load_data(); src_txt=text_with_custom_emoji_markup(bm); txt=(bm.text or "").strip(); uid=(bm.from_user.id if bm.from_user else bm.chat.id)
    if not data.get("active", False) and not is_admin(uid, data):
        logging.info("business_ignored_bot_inactive uid=%s", uid)
        return
    prev=db.get_user(uid)
    db.upsert_user(uid,(bm.from_user.username if bm.from_user else "") or "",(bm.from_user.full_name if bm.from_user else bm.chat.full_name) or "",None,False,"business",src_txt)
    if not data.get("self_bot_enabled", False):
        return
    sc=db.load_shortcuts(); resp=match_shortcut(txt, sc)
    if resp:
        bc=getattr(bm,"business_connection_id",None)
        if data.get("self_bot_enabled") and is_admin(uid, data):
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
        await context.bot.send_message(**kwargs)
        logging.info("business_shortcut_sent uid=%s text=%s",uid, txt)
        return
    logging.info("business_shortcut_no_match uid=%s text=%s", uid, txt)
    if data.get("welcome_enabled",True) and (prev is None or int(prev["last_seen_at"] or 0)<=0 or int(time.time())-int(prev["last_seen_at"] or 0)>=WELCOME_COOLDOWN_SECONDS):
        try:
            out_welcome = render_html_text(data.get("welcome_text", "خوش آمدید"), bold=data.get("bold_mode", True))
            await context.bot.send_message(chat_id=bm.chat.id, text=out_welcome, parse_mode=ParseMode.HTML, business_connection_id=getattr(bm,"business_connection_id",None))
            logging.info("welcome_trigger_business uid=%s", uid)
        except Exception as exc:
            logging.exception("welcome_business_failed uid=%s reason=%s", uid, exc)

async def all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if getattr(update,"business_message",None): await business_message_handler(update, context); return
    if getattr(update, "channel_post", None):
        cp = update.channel_post
        data = load_data()
        kw = db.get_watch("watch_keywords", [])
        report_chat = int(db.get_watch("watch_report_chat_id", 0) or 0)
        txt_channel = cp.text or ""
        lower = txt_channel.lower()
        for k in kw:
            kk = str(k or "").strip()
            if kk and kk.lower() in lower:
                db.add_keyword_hit(kk, None, "", cp.chat.title or "", cp.chat.id, cp.chat.title or "", txt_channel)
                if report_chat:
                    try:
                        await context.bot.send_message(chat_id=report_chat, text=f"🔎 کلید یافت شد: #{kk.replace(' ', '_')}\n🕒 زمان: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n📣 چنل: {cp.chat.title or '-'}\n\n{txt_channel}")
                    except Exception as exc:
                        logging.warning("watch_report_send_failed reason=%s", exc)
                break
        return
    if not update.message or not update.effective_user: return
    if STATE.admin_id == (update.effective_user.id if update.effective_user else None) and STATE.flow == "db_import" and STATE.step == "waiting_document":
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
        STATE.flow = STATE.step = None
        await update.message.reply_text("✅ دیتابیس جدید جایگزین شد. ربات در حال ریلود...")
        os.execlp("python", "python", str(BASE_DIR / "bot.py"))
        return
    if update.effective_chat and update.effective_chat.type in {"group", "supergroup"}:
        data = load_data()
        kw = db.get_watch("watch_keywords", [])
        report_chat = int(db.get_watch("watch_report_chat_id", 0) or 0)
        txt_group = update.message.text or ""
        lower = txt_group.lower()
        for k in kw:
            kk = str(k or "").strip()
            if kk and kk.lower() in lower:
                db.add_keyword_hit(kk, update.effective_user.id, update.effective_user.username or "", update.effective_user.full_name or update.effective_user.first_name or "", update.effective_chat.id, update.effective_chat.title or "", txt_group)
                report = (
                    f"🔎 کلید یافت شد: #{kk.replace(' ', '_')}\n"
                    f"👤 نام: {update.effective_user.full_name or update.effective_user.first_name}\n"
                    f"🆔 عددی: {update.effective_user.id}\n"
                    f"🔗 یوزرنیم: @{update.effective_user.username or '-'}\n"
                    f"🕒 زمان: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                    f"💬 گروه: {update.effective_chat.title or '-'}\n\n"
                    f"{txt_group}"
                )
                if report_chat:
                    try:
                        await context.bot.send_message(chat_id=report_chat, text=report)
                    except Exception as exc:
                        logging.warning("watch_report_send_failed reason=%s", exc)
                break
        return
    data=load_data(); uid=update.effective_user.id; src_txt=text_with_custom_emoji_markup(update.message); txt=(update.message.text or "").strip()
    row=db.get_user(uid)
    if row and int(row["soft_ban_until"] or 0)>int(time.time()): return
    db.upsert_user(uid, update.effective_user.username or "", update.effective_user.full_name or update.effective_user.first_name or "", update.message.contact.phone_number if update.message.contact else None, False, "message", txt)

    if txt.lower()=="panel" and is_admin(uid,data):
        STATE.flow=STATE.step=STATE.pending_key=None
        STATE.temp_shortcuts=None
        await update.message.reply_text("پنل ادمین", reply_markup=create_admin_keyboard(data))
        return
    if txt.lower()=="menu":
        if not data.get("active", False) and not is_admin(uid, data):
            return
        logging.info("user_menu_open uid=%s", uid)
        await update.message.reply_text("منوی کاربر", reply_markup=create_menu_keyboard())
        return

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
        preview = f"Welcome بروزرسانی شد.\nقبلی:\n{render_html_text(old)}\n\nجدید:\n{render_html_text(src_txt)}"
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

    if not data.get("self_bot_enabled", False):
        return
    resp=match_shortcut(txt,shortcuts)
    if resp:
        if data.get("self_bot_enabled") and is_admin(uid,data):
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

    if data["active"] and not is_admin(uid,data): await send_formatted_message(update.message, data.get("offline_message",""), data)

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


def main() -> None:
    setup_logging(); db.init(); save_data(load_data())
    token=os.getenv("BOT_TOKEN")
    if not token: raise RuntimeError("BOT_TOKEN is missing")
    app=Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("panel", panel)); app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(callbacks)); app.add_handler(MessageHandler(filters.ALL, all_messages)); app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__": main()
