import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
import requests
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

ADMIN_FEATURES = ["admin_panel_access", "admin_broadcast", "admin_reports", "admin_edit_texts", "admin_toggle_features", "admin_user_stats", "admin_system_info", "admin_public_ip", "admin_export_users", "admin_bold_mode"]
USER_FEATURES = ["user_auto_reply", "user_services", "user_hours", "user_location", "user_faq", "user_contact", "user_request_callback", "user_feedback", "user_join_channel", "user_business_test_reply"]
TEXT_KEYS = {"offline_message": "پیام آفلاین", "service_text": "متن خدمات", "hours_text": "ساعات کاری", "location_text": "متن آدرس", "faq_text": "متن پرسش‌های پرتکرار", "contact_text": "متن تماس"}
START_TIME = time.time()


@dataclass
class State:
    wait_text_admin: int | None = None
    wait_text_key: str | None = None


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
                source TEXT
            )""")
            c.execute("CREATE TABLE IF NOT EXISTS logs_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    def get_json(self, key: str, default: Any) -> Any:
        with self.conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
            if not row:
                return default
            import json
            return json.loads(row["v"])

    def set_json(self, key: str, value: Any):
        import json
        with self.conn() as c:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, json.dumps(value, ensure_ascii=False)))

    def upsert_user(self, user_id: int, username: str, full_name: str, phone: str | None, joined: bool, source: str):
        with self.conn() as c:
            c.execute(
                """INSERT INTO users(user_id,username,full_name,phone,is_channel_joined,last_seen_at,source)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                  username=excluded.username,
                  full_name=excluded.full_name,
                  phone=COALESCE(excluded.phone, users.phone),
                  is_channel_joined=excluded.is_channel_joined,
                  last_seen_at=excluded.last_seen_at,
                  source=excluded.source""",
                (user_id, username, full_name, phone, 1 if joined else 0, int(time.time()), source),
            )

    def list_users(self, limit: int = 50):
        with self.conn() as c:
            return c.execute("SELECT * FROM users ORDER BY last_seen_at DESC LIMIT ?", (limit,)).fetchall()


db = DB(DB_PATH)


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])


def get_default_data() -> dict[str, Any]:
    return {
        "admin_id": int(os.getenv("ADMIN_ID") or 0),
        "active": False,
        "force_join_channel": os.getenv("FORCE_JOIN_CHANNEL", ""),
        "bold_mode": False,
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


def styled(text: str, data: dict[str, Any]) -> tuple[str, str | None]:
    if data.get("features", {}).get("admin_bold_mode", True) and data.get("bold_mode"):
        return f"<b>{text}</b>", ParseMode.HTML
    return text, None


def feature_enabled(data: dict[str, Any], key: str) -> bool:
    return data.get("features", {}).get(key, False)


# Adapter/factory layer (migration-friendly)
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
    return InlineKeyboardMarkup([
        [status_btn],
        [create_primary_button("ویرایش متن‌ها", "admin:texts"), create_primary_button("فیچرها", "admin:features")],
        [create_success_button("گزارش وضعیت", "admin:report"), create_danger_button("راهنمای برودکست", "admin:broadcast_help")],
    ])


def create_features_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = []
    for k in ADMIN_FEATURES + USER_FEATURES:
        st = "✅" if data["features"].get(k) else "❌"
        rows.append([create_primary_button(f"{st} {k}", f"feature:{k}")])
    rows.append([create_danger_button("بازگشت", "menu:admin")])
    return InlineKeyboardMarkup(rows)


def create_texts_keyboard() -> InlineKeyboardMarkup:
    rows = [[create_primary_button(name, f"text:{key}")] for key, name in TEXT_KEYS.items()]
    rows.append([create_danger_button("بازگشت", "menu:admin")])
    return InlineKeyboardMarkup(rows)


async def check_join_status(bot, user_id: int, channel_id: str) -> bool:
    if not channel_id:
        return False
    try:
        m = await bot.get_chat_member(channel_id, user_id)
        return m.status in {"member", "administrator", "creator"}
    except Exception:
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    user = update.effective_user
    joined = await check_join_status(context.bot, user.id, data.get("force_join_channel", "")) if feature_enabled(data, "user_join_channel") else False
    db.upsert_user(user.id, user.username or "", user.full_name or user.first_name or "", None, joined, "start")
    text, pm = styled(f"سلام {user.first_name} 🌟\nبه ربات خوش آمدی. دکمه منو را بزن.", data)
    await update.message.reply_text(text, parse_mode=pm, reply_markup=ReplyKeyboardMarkup([["menu"]], resize_keyboard=True))


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    if not is_admin(update.effective_user.id, data) or not feature_enabled(data, "admin_panel_access"):
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
            "user:services": ("user_services", data.get("service_text") or "متن خدمات ثبت نشده."),
            "user:hours": ("user_hours", data.get("hours_text") or "متن ساعات کاری ثبت نشده."),
            "user:location": ("user_location", data.get("location_text") or "متن آدرس ثبت نشده."),
            "user:faq": ("user_faq", data.get("faq_text") or "متن FAQ ثبت نشده."),
            "user:contact": ("user_contact", data.get("contact_text") or "متن تماس ثبت نشده."),
            "user:feedback": ("user_feedback", "بازخورد خود را در پیام بعدی ارسال کنید."),
        }
        fkey, msg = mapping.get(q.data, (None, "عملیات ناشناخته"))
        if fkey and feature_enabled(data, fkey):
            text, pm = styled(msg, data)
            await q.message.reply_text(text, parse_mode=pm)
        return

    if not is_admin(uid, data):
        return
    if q.data == "menu:admin":
        await q.message.reply_text("پنل ادمین", reply_markup=create_admin_keyboard(data))
    elif q.data == "toggle:active":
        data["active"] = not data["active"]
        save_data(data)
        await q.message.reply_text("وضعیت بروزرسانی شد.", reply_markup=create_admin_keyboard(data))
    elif q.data == "admin:features" and feature_enabled(data, "admin_toggle_features"):
        await q.message.reply_text("فیچرها", reply_markup=create_features_keyboard(data))
    elif q.data.startswith("feature:") and feature_enabled(data, "admin_toggle_features"):
        key = q.data.split(":", 1)[1]
        if key in data["features"]:
            data["features"][key] = not data["features"][key]
            if key == "admin_bold_mode":
                data["bold_mode"] = data["features"][key]
            save_data(data)
            await q.message.reply_text(f"{key} => {data['features'][key]}", reply_markup=create_features_keyboard(data))
    elif q.data == "admin:texts" and feature_enabled(data, "admin_edit_texts"):
        await q.message.reply_text("انتخاب متن برای ویرایش", reply_markup=create_texts_keyboard())
    elif q.data.startswith("text:") and feature_enabled(data, "admin_edit_texts"):
        key = q.data.split(":", 1)[1]
        if key in TEXT_KEYS:
            STATE.wait_text_admin = uid
            STATE.wait_text_key = key
            old = data.get(key, "")
            await q.message.reply_text(f"متن قبلی ({TEXT_KEYS[key]}):\n{old or '(خالی)'}\n\nمتن جدید را بفرستید.")
    elif q.data == "admin:report" and feature_enabled(data, "admin_reports"):
        users = db.list_users(limit=50)
        lines = [f"{u['user_id']} | @{u['username'] or '-'} | {u['full_name'] or '-'} | phone:{u['phone'] or '-'} | joined:{'yes' if u['is_channel_joined'] else 'no'}" for u in users]
        report = f"تعداد کاربران: {len(users)}\nآپتایم: {int(time.time()-START_TIME)}s\nCPU: {psutil.cpu_percent()}%\nRAM: {psutil.virtual_memory().percent}%\n\n" + ("\n".join(lines) if lines else "-")
        await q.message.reply_text(report)
    elif q.data == "admin:broadcast_help":
        await q.message.reply_text("از دستور /broadcast <متن> استفاده کنید.")


async def business_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bm = getattr(update, "business_message", None)
    if not bm or not bm.text:
        return
    if bm.text.strip() != "عجیبستان":
        return
    logging.info("Business test trigger received chat_id=%s", bm.chat.id)
    try:
        kwargs = {"chat_id": bm.chat.id, "text": "✅ Business Bot Works"}
        bc_id = getattr(bm, "business_connection_id", None)
        if bc_id:
            kwargs["business_connection_id"] = bc_id
        await context.bot.send_message(**kwargs)
        logging.info("Business test reply sent")
    except Exception as exc:
        logging.exception("Business test failed: %s", exc)


async def all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if getattr(update, "business_message", None):
        await business_test_handler(update, context)
        return
    if not update.message or not update.effective_user:
        return
    data = load_data()
    uid = update.effective_user.id
    txt = (update.message.text or "").strip()
    joined = await check_join_status(context.bot, uid, data.get("force_join_channel", "")) if feature_enabled(data, "user_join_channel") else False
    phone = update.message.contact.phone_number if update.message.contact else None
    db.upsert_user(uid, update.effective_user.username or "", update.effective_user.full_name or update.effective_user.first_name or "", phone, joined, "message")

    if STATE.wait_text_admin == uid and STATE.wait_text_key and is_admin(uid, data):
        key = STATE.wait_text_key
        old = data.get(key, "")
        data[key] = txt
        save_data(data)
        STATE.wait_text_admin = None
        STATE.wait_text_key = None
        await update.message.reply_text(f"ذخیره شد.\nمتن قبلی:\n{old or '(خالی)'}\n\nمتن جدید:\n{txt}")
        return
    if txt.lower() == "panel":
        if is_admin(uid, data):
            await update.message.reply_text("پنل ادمین", reply_markup=create_admin_keyboard(data))
        return
    if txt.lower() == "menu":
        await update.message.reply_text("منوی کاربر", reply_markup=create_menu_keyboard())
        return
    if is_admin(uid, data):
        return
    if data["active"] and feature_enabled(data, "user_auto_reply"):
        msg, pm = styled(data.get("offline_message", ""), data)
        await update.message.reply_text(msg, parse_mode=pm)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    if not is_admin(update.effective_user.id, data) or not feature_enabled(data, "admin_broadcast"):
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
    data = load_data()
    save_data(data)
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
