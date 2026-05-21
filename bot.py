import html
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "bot.db"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "bot.log"
load_dotenv(ENV_PATH)

START_TIME = int(time.time())
SOFT_BAN_SECONDS = 20 * 60
WELCOME_COOLDOWN_SECONDS = 24 * 60 * 60

ADMIN_FEATURES = ["admin_panel_access", "admin_broadcast", "admin_reports", "admin_edit_texts", "admin_toggle_features", "admin_user_stats", "admin_system_info", "admin_public_ip", "admin_export_users", "admin_bold_mode"]
USER_FEATURES = ["user_auto_reply", "user_services", "user_hours", "user_location", "user_faq", "user_contact", "user_request_callback", "user_feedback", "user_join_channel", "user_business_test_reply"]
TEXT_KEYS = {"offline_message": "پیام آفلاین", "service_text": "متن خدمات", "hours_text": "ساعات کاری", "location_text": "متن آدرس", "faq_text": "متن پرسش‌های پرتکرار", "contact_text": "متن تماس"}


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
    def __init__(self, path: Path):
        self.path = path

    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def init(self):
        with self.conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            c.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                is_channel_joined INTEGER DEFAULT 0,
                last_seen_at INTEGER,
                first_seen_at INTEGER,
                source TEXT,
                soft_ban_until INTEGER DEFAULT 0,
                spam_score INTEGER DEFAULT 0,
                last_message TEXT
            )""")
            c.execute("CREATE TABLE IF NOT EXISTS shortcuts (name TEXT PRIMARY KEY, response TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS emoji_map (src TEXT PRIMARY KEY, repl TEXT NOT NULL)")

    def get_json(self, key: str, default: Any) -> Any:
        with self.conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
            if not row:
                return default
            return json.loads(row["v"])

    def set_json(self, key: str, value: Any):
        with self.conn() as c:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, json.dumps(value, ensure_ascii=False)))

    def upsert_user(self, user_id: int, username: str, full_name: str, phone: str | None, joined: bool, source: str, last_message: str | None):
        now = int(time.time())
        with self.conn() as c:
            c.execute(
                """INSERT INTO users(user_id,username,full_name,phone,is_channel_joined,last_seen_at,first_seen_at,source,last_message)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name,
                    phone=COALESCE(excluded.phone, users.phone),
                    is_channel_joined=excluded.is_channel_joined,
                    last_seen_at=excluded.last_seen_at,
                    source=excluded.source,
                    last_message=COALESCE(excluded.last_message, users.last_message)
                """,
                (user_id, username, full_name, phone, 1 if joined else 0, now, now, source, last_message),
            )

    def get_user(self, user_id: int):
        with self.conn() as c:
            return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    def list_users(self, limit: int = 50):
        with self.conn() as c:
            return c.execute("SELECT * FROM users ORDER BY last_seen_at DESC LIMIT ?", (limit,)).fetchall()

    def set_soft_ban(self, user_id: int, until_ts: int):
        with self.conn() as c:
            c.execute("UPDATE users SET soft_ban_until=? WHERE user_id=?", (until_ts, user_id))

    def save_shortcuts(self, items: dict[str, str]):
        with self.conn() as c:
            c.execute("DELETE FROM shortcuts")
            c.executemany("INSERT INTO shortcuts(name,response) VALUES(?,?)", list(items.items()))

    def load_shortcuts(self) -> dict[str, str]:
        with self.conn() as c:
            rows = c.execute("SELECT name,response FROM shortcuts").fetchall()
            return {r["name"]: r["response"] for r in rows}

    def save_emoji_map(self, mapping: dict[str, str]):
        with self.conn() as c:
            c.execute("DELETE FROM emoji_map")
            c.executemany("INSERT INTO emoji_map(src,repl) VALUES(?,?)", list(mapping.items()))

    def load_emoji_map(self) -> dict[str, str]:
        with self.conn() as c:
            rows = c.execute("SELECT src,repl FROM emoji_map").fetchall()
            return {r["src"]: r["repl"] for r in rows}


db = DB(DB_PATH)


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])


def get_default_data() -> dict[str, Any]:
    return {
        "admin_id": int(os.getenv("ADMIN_ID") or 0),
        "active": False,
        "force_join_channel": os.getenv("FORCE_JOIN_CHANNEL", ""),
        "bold_mode": True,
        "welcome_enabled": True,
        "welcome_text": "سلام 🌟\nبه پیج بیزینسی ما خوش آمدید.",
        "self_bot_enabled": False,
        "features": {**{k: True for k in ADMIN_FEATURES}, **{k: True for k in USER_FEATURES}},
        "offline_message": "پیام شما دریافت شد. به‌زودی پاسخ می‌دهیم.",
        "service_text": "",
        "hours_text": "",
        "location_text": "",
        "faq_text": "",
        "contact_text": "",
    }


def load_data() -> dict[str, Any]:
    data = db.get_json("settings", get_default_data())
    defaults = get_default_data()
    for k, v in defaults.items():
        data.setdefault(k, v)
    data.setdefault("features", {})
    for k, v in defaults["features"].items():
        data["features"].setdefault(k, v)
    return data


def save_data(data: dict[str, Any]) -> None:
    db.set_json("settings", data)


def is_admin(user_id: int, data: dict[str, Any]) -> bool:
    return user_id == int(data.get("admin_id") or os.getenv("ADMIN_ID") or 0)


def feature_enabled(data: dict[str, Any], key: str) -> bool:
    return data.get("features", {}).get(key, False)


def create_primary_button(text: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(f"🔵 {text}", callback_data=callback)


def create_success_button(text: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(f"🟢 {text}", callback_data=callback)


def create_danger_button(text: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(f"🔴 {text}", callback_data=callback)


def create_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [create_success_button("خدمات", "user:services"), create_primary_button("ساعات کاری", "user:hours")],
        [create_primary_button("آدرس", "user:location"), create_success_button("پرسش‌های پرتکرار", "user:faq")],
        [create_danger_button("تماس", "user:contact"), create_primary_button("ارسال بازخورد", "user:feedback")],
    ])


def create_admin_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    status = "روشن" if data["active"] else "خاموش"
    status_btn = create_success_button(f"وضعیت ربات: {status}", "toggle:active") if data["active"] else create_danger_button(f"وضعیت ربات: {status}", "toggle:active")
    selfb = "ON" if data.get("self_bot_enabled") else "OFF"
    wel = "ON" if data.get("welcome_enabled") else "OFF"
    return InlineKeyboardMarkup([
        [status_btn],
        [create_primary_button("ویرایش متن‌ها", "admin:texts"), create_primary_button("فیچرها", "admin:features")],
        [create_success_button("گزارش وضعیت", "admin:report"), create_danger_button("راهنمای برودکست", "admin:broadcast_help")],
        [create_primary_button(f"Self Bot: {selfb}", "admin:selfbot"), create_primary_button("پیکربندی سلف بات", "admin:shortcut_cfg")],
        [create_primary_button(f"Welcome: {wel}", "admin:welcome_toggle"), create_primary_button("پیکربندی Welcome", "admin:welcome_cfg")],
        [create_primary_button("پیکربندی ایموجی", "admin:emoji_cfg")],
    ])


def create_features_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = [[create_primary_button(f"{'✅' if data['features'].get(k) else '❌'} {k}", f"feature:{k}")] for k in ADMIN_FEATURES + USER_FEATURES]
    rows.append([create_danger_button("بازگشت", "menu:admin")])
    return InlineKeyboardMarkup(rows)


def create_texts_keyboard() -> InlineKeyboardMarkup:
    rows = [[create_primary_button(name, f"text:{key}")] for key, name in TEXT_KEYS.items()]
    rows.append([create_danger_button("بازگشت", "menu:admin")])
    return InlineKeyboardMarkup(rows)


def map_emojis(text: str) -> str | None:
    mapping = db.load_emoji_map()
    if not mapping:
        return text
    out = text
    for src, repl in mapping.items():
        out = out.replace(src, repl)
    return out


async def send_formatted_message(target, text: str, data: dict[str, Any]):
    mapped = map_emojis(text)
    if mapped is None:
        return
    safe = html.escape(mapped)
    if data.get("bold_mode", True):
        safe = f"<b>{safe}</b>"
    await target.reply_text(safe, parse_mode=ParseMode.HTML)


def is_spam(text: str, shortcuts: dict[str, str]) -> bool:
    words = re.findall(r"\w+", text.lower())
    if not words:
        return False
    counts = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
        if counts[w] > 5:
            return True
    for s in shortcuts.keys():
        if text.lower().count(s.lower()) >= 3:
            return True
    return False


def match_shortcut(text: str, shortcuts: dict[str, str]) -> str | None:
    t = text.lower()
    for k, v in shortcuts.items():
        if k.lower() == t or k.lower() in t:
            return v
    return None


async def maybe_welcome(update: Update, data: dict[str, Any], uid: int) -> bool:
    if not data.get("welcome_enabled", True):
        return False
    row = db.get_user(uid)
    now = int(time.time())
    if row is None or now - int(row["last_seen_at"] or 0) > WELCOME_COOLDOWN_SECONDS:
        await send_formatted_message(update.message, data.get("welcome_text", "خوش آمدید"), data)
        logging.info("welcome_trigger user=%s", uid)
        return True
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    u = update.effective_user
    db.upsert_user(u.id, u.username or "", u.full_name or u.first_name or "", None, False, "start", update.message.text)
    await send_formatted_message(update.message, f"سلام {u.first_name} 🌟\nبه ربات خوش آمدی. دکمه منو را بزن.", data)
    await update.message.reply_text("menu", reply_markup=ReplyKeyboardMarkup([["menu"]], resize_keyboard=True))


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        return
    await update.message.reply_text("پنل ادمین", reply_markup=create_admin_keyboard(data))


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    data = load_data()
    uid = q.from_user.id

    if q.data.startswith("user:"):
        if not data["active"]:
            await q.message.reply_text("ربات خاموش است.")
            return
        mapping = {
            "user:services": data.get("service_text") or "متن خدمات ثبت نشده.",
            "user:hours": data.get("hours_text") or "متن ساعات کاری ثبت نشده.",
            "user:location": data.get("location_text") or "متن آدرس ثبت نشده.",
            "user:faq": data.get("faq_text") or "متن FAQ ثبت نشده.",
            "user:contact": data.get("contact_text") or "متن تماس ثبت نشده.",
            "user:feedback": "بازخورد خود را در پیام بعدی ارسال کنید.",
        }
        msg = mapping.get(q.data)
        if msg:
            await send_formatted_message(q.message, msg, data)
        return

    if not is_admin(uid, data):
        return

    if q.data == "menu:admin":
        await q.edit_message_text("پنل ادمین", reply_markup=create_admin_keyboard(data))
    elif q.data == "toggle:active":
        data["active"] = not data["active"]
        save_data(data)
        await q.edit_message_text("وضعیت بروزرسانی شد.", reply_markup=create_admin_keyboard(data))
    elif q.data == "admin:features":
        await q.edit_message_text("فیچرها", reply_markup=create_features_keyboard(data))
    elif q.data.startswith("feature:"):
        key = q.data.split(":", 1)[1]
        if key in data["features"]:
            data["features"][key] = not data["features"][key]
            if key == "admin_bold_mode":
                data["bold_mode"] = data["features"][key]
            save_data(data)
            await q.edit_message_text(f"{key} => {data['features'][key]}", reply_markup=create_features_keyboard(data))
    elif q.data == "admin:texts":
        await q.edit_message_text("انتخاب متن برای ویرایش", reply_markup=create_texts_keyboard())
    elif q.data.startswith("text:"):
        key = q.data.split(":", 1)[1]
        if key in TEXT_KEYS:
            STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "text_edit", "waiting_value", uid, q.message.message_id, key
            old = data.get(key, "")
            await q.edit_message_text(f"متن قبلی ({TEXT_KEYS[key]}):\n{old or '(خالی)'}\n\nمتن جدید را ارسال کنید.")
    elif q.data == "admin:selfbot":
        data["self_bot_enabled"] = not data.get("self_bot_enabled", False)
        save_data(data)
        await q.edit_message_text("وضعیت Self Bot تغییر کرد.", reply_markup=create_admin_keyboard(data))
    elif q.data == "admin:shortcut_cfg":
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id = "shortcut_cfg", "waiting_name", uid, q.message.message_id
        STATE.temp_shortcuts = {}
        await q.edit_message_text("نام شورت‌کات را وارد کنید:")
    elif q.data == "shortcut:continue_yes":
        STATE.step = "waiting_name"
        await q.edit_message_text("نام شورت‌کات بعدی را وارد کنید:")
    elif q.data == "shortcut:continue_no":
        db.save_shortcuts(STATE.temp_shortcuts or {})
        logging.info("admin_action shortcut_saved count=%s", len(STATE.temp_shortcuts or {}))
        STATE.flow = STATE.step = STATE.pending_key = None
        STATE.temp_shortcuts = None
        await q.edit_message_text("شورت‌کات‌ها ذخیره شدند.")
    elif q.data == "admin:welcome_toggle":
        data["welcome_enabled"] = not data.get("welcome_enabled", True)
        save_data(data)
        await q.edit_message_text("وضعیت Welcome تغییر کرد.", reply_markup=create_admin_keyboard(data))
    elif q.data == "admin:welcome_cfg":
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id, STATE.pending_key = "welcome_cfg", "waiting_value", uid, q.message.message_id, "welcome_text"
        await q.edit_message_text(f"متن فعلی Welcome:\n{data.get('welcome_text','')}\n\nمتن جدید را ارسال کنید.")
    elif q.data == "admin:emoji_cfg":
        STATE.flow, STATE.step, STATE.admin_id, STATE.message_id = "emoji_cfg", "waiting_value", uid, q.message.message_id
        await q.edit_message_text("فرمت: هر خط = source|custom_emoji_id\nمثال:\n🌟|<tg-emoji emoji-id=1234567890123456789>🌟</tg-emoji>")
    elif q.data == "admin:report":
        users = db.list_users(limit=50)
        lines = [f"• id:{u['user_id']} | @{u['username'] or '-'} | نام:{u['full_name'] or '-'} | موبایل:{u['phone'] or '-'} | جوین:{'yes' if u['is_channel_joined'] else 'no'}" for u in users]
        report = (
            f"📊 گزارش سیستم\n"
            f"• کاربران: {len(users)}\n"
            f"• آپتایم: {int(time.time()-START_TIME)} ثانیه\n"
            f"• CPU: {psutil.cpu_percent()}%\n"
            f"• RAM: {psutil.virtual_memory().percent}%\n\n"
            f"👤 لیست کاربران:\n" + ("\n".join(lines) if lines else "-")
        )
        await q.edit_message_text(report)


async def business_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bm = getattr(update, "business_message", None)
    if not bm or not bm.text:
        return
    if bm.text.strip() != "عجیبستان":
        return
    logging.info("business_trigger chat_id=%s", bm.chat.id)
    try:
        kwargs = {"chat_id": bm.chat.id, "text": "✅ Business Bot Works"}
        bc_id = getattr(bm, "business_connection_id", None)
        if bc_id:
            kwargs["business_connection_id"] = bc_id
        await context.bot.send_message(**kwargs)
        logging.info("business_success chat_id=%s", bm.chat.id)
    except Exception as exc:
        logging.exception("business_failure reason=%s", exc)


async def all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if getattr(update, "business_message", None):
        await business_test_handler(update, context)
        return
    if not update.message or not update.effective_user:
        return

    data = load_data()
    uid = update.effective_user.id
    txt = (update.message.text or "").strip()
    db.upsert_user(uid, update.effective_user.username or "", update.effective_user.full_name or update.effective_user.first_name or "", update.message.contact.phone_number if update.message.contact else None, False, "message", txt)

    user_row = db.get_user(uid)
    if user_row and int(user_row["soft_ban_until"] or 0) > int(time.time()):
        logging.info("anti_spam_ignored user=%s until=%s", uid, user_row["soft_ban_until"])
        return

    if await maybe_welcome(update, data, uid):
        return

    if STATE.admin_id == uid and STATE.flow == "text_edit" and STATE.step == "waiting_value" and STATE.pending_key:
        key = STATE.pending_key
        old = data.get(key, "")
        data[key] = txt
        save_data(data)
        STATE.flow = STATE.step = STATE.pending_key = None
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"ذخیره شد.\nمتن قبلی:\n{old or '(خالی)'}\n\nمتن جدید:\n{txt}")
        return

    if STATE.admin_id == uid and STATE.flow == "shortcut_cfg":
        if STATE.step == "waiting_name":
            STATE.pending_key = txt
            STATE.step = "waiting_value"
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"نام شورت‌کات: {txt}\nمتن شورت‌کات را وارد کنید:")
            return
        if STATE.step == "waiting_value":
            (STATE.temp_shortcuts or {})[STATE.pending_key or ""] = txt
            STATE.step = "confirm_continue"
            kb = InlineKeyboardMarkup([[create_success_button("بله", "shortcut:continue_yes"), create_danger_button("خیر", "shortcut:continue_no")]])
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text="آیا ادامه می‌دهید؟", reply_markup=kb)
            return

    if STATE.admin_id == uid and STATE.flow in {"welcome_cfg", "emoji_cfg"} and STATE.step == "waiting_value":
        if STATE.flow == "welcome_cfg":
            old = data.get("welcome_text", "")
            data["welcome_text"] = txt
            save_data(data)
            STATE.flow = STATE.step = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"Welcome بروزرسانی شد.\nقبلی:\n{old}\n\nجدید:\n{txt}")
            return
        if STATE.flow == "emoji_cfg":
            mapping = {}
            for line in txt.splitlines():
                if "|" in line:
                    src, repl = line.split("|", 1)
                    mapping[src.strip()] = repl.strip()
            db.save_emoji_map(mapping)
            logging.info("emoji_mapping_updated count=%s", len(mapping))
            STATE.flow = STATE.step = None
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"ایموجی‌ها ذخیره شدند. تعداد: {len(mapping)}")
            return

    shortcuts = db.load_shortcuts()
    if is_spam(txt, shortcuts):
        until_ts = int(time.time()) + SOFT_BAN_SECONDS
        db.set_soft_ban(uid, until_ts)
        logging.warning("anti_spam_trigger user=%s ban_until=%s text=%s", uid, until_ts, txt)
        return

    if txt.lower() == "panel":
        if is_admin(uid, data):
            await update.message.reply_text("پنل ادمین", reply_markup=create_admin_keyboard(data))
        return

    if txt.lower() == "menu":
        await update.message.reply_text("منوی کاربر", reply_markup=create_menu_keyboard())
        return

    shortcut_response = match_shortcut(txt, shortcuts)
    if shortcut_response:
        if data.get("self_bot_enabled") and is_admin(uid, data):
            try:
                await update.message.delete()
            except Exception:
                pass
            await send_formatted_message(update.message, shortcut_response, data)
            logging.info("shortcut_execution admin=%s key_match=%s", uid, txt)
            return
        if not is_admin(uid, data):
            await send_formatted_message(update.message, shortcut_response, data)
            logging.info("shortcut_execution user=%s key_match=%s", uid, txt)
            return

    if data["active"] and feature_enabled(data, "user_auto_reply") and not is_admin(uid, data):
        await send_formatted_message(update.message, data.get("offline_message", ""), data)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /broadcast your message")
        return
    users = db.list_users(limit=100000)
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(int(u["user_id"]), text)
            sent += 1
        except Exception as exc:
            logging.warning("broadcast failed %s %s", u["user_id"], exc)
    await update.message.reply_text(f"ارسال شد برای {sent} کاربر")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled error: %s", context.error)


def main() -> None:
    setup_logging()
    db.init()
    save_data(load_data())
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.ALL, all_messages))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
