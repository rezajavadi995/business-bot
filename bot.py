import json
import logging
import os
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

try:
    import mysql.connector  # type: ignore
except Exception:  # optional dependency at runtime
    mysql = None

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DATA_PATH = BASE_DIR / "data.json"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "bot.log"
load_dotenv(ENV_PATH)

ADMIN_FEATURES = ["admin_panel_access", "admin_broadcast", "admin_reports", "admin_edit_texts", "admin_toggle_features", "admin_user_stats", "admin_system_info", "admin_public_ip", "admin_export_users", "admin_bold_mode"]
USER_FEATURES = ["user_auto_reply", "user_services", "user_hours", "user_location", "user_faq", "user_contact", "user_request_callback", "user_feedback", "user_join_channel", "user_business_test_reply"]
TEXT_KEYS = {"offline_message": "پیام آفلاین", "service_text": "متن خدمات", "hours_text": "ساعات کاری", "location_text": "متن آدرس", "faq_text": "متن پرسش‌های پرتکرار", "contact_text": "متن تماس"}

DEFAULT_DATA: dict[str, Any] = {
    "admin_id": 0,
    "active": False,
    "force_join_channel": "",
    "visitors": {},
    "bold_mode": False,
    "features": {**{k: True for k in ADMIN_FEATURES}, **{k: True for k in USER_FEATURES}},
    "offline_message": "پیام شما دریافت شد. به‌زودی پاسخ می‌دهیم.",
    "service_text": "",
    "hours_text": "",
    "location_text": "",
    "faq_text": "",
    "contact_text": "",
}
START_TIME = time.time()

@dataclass
class State:
    wait_text_admin: int | None = None
    wait_text_key: str | None = None

STATE = State()


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])


def load_data() -> dict[str, Any]:
    if not DATA_PATH.exists():
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    for k, v in DEFAULT_DATA.items():
        data.setdefault(k, v)
    data.setdefault("features", {})
    for k, v in DEFAULT_DATA["features"].items():
        data["features"].setdefault(k, v)
    if isinstance(data.get("visitors"), list):
        data["visitors"] = {str(uid): {"id": uid, "username": "", "name": ""} for uid in data["visitors"]}
    return data


def save_data(data: dict[str, Any]) -> None:
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_admin(user_id: int, data: dict[str, Any]) -> bool:
    return user_id == int(data.get("admin_id") or os.getenv("ADMIN_ID") or 0)


def styled(text: str, data: dict[str, Any]) -> tuple[str, str | None]:
    if data.get("features", {}).get("admin_bold_mode", True) and data.get("bold_mode"):
        return f"<b>{text}</b>", ParseMode.HTML
    return text, None


def feature_enabled(data: dict[str, Any], key: str) -> bool:
    return data.get("features", {}).get(key, False)


def create_button(text: str, callback: str, style: str = "primary") -> InlineKeyboardButton:
    prefix = {"primary": "🔵", "success": "🟢", "danger": "🔴"}.get(style, "⚪")
    return InlineKeyboardButton(f"{prefix} {text}", callback_data=callback)


def admin_panel(data: dict[str, Any]) -> InlineKeyboardMarkup:
    status = "روشن" if data["active"] else "خاموش"
    return InlineKeyboardMarkup([
        [create_button(f"وضعیت ربات: {status}", "toggle:active", "success" if data["active"] else "danger")],
        [create_button("ویرایش متن‌ها", "admin:texts", "primary"), create_button("فیچرها", "admin:features", "primary")],
        [create_button("گزارش وضعیت", "admin:report", "success"), create_button("راهنمای برودکست", "admin:broadcast_help", "danger")],
    ])


def features_menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = []
    for k in ADMIN_FEATURES + USER_FEATURES:
        st = "✅" if data["features"].get(k) else "❌"
        rows.append([create_button(f"{st} {k}", f"feature:{k}", "primary")])
    rows.append([create_button("بازگشت", "menu:admin", "danger")])
    return InlineKeyboardMarkup(rows)


def text_menu() -> InlineKeyboardMarkup:
    rows = [[create_button(name, f"text:{key}", "primary")] for key, name in TEXT_KEYS.items()]
    rows.append([create_button("بازگشت", "menu:admin", "danger")])
    return InlineKeyboardMarkup(rows)


def user_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [create_button("خدمات", "user:services", "success"), create_button("ساعات کاری", "user:hours", "primary")],
        [create_button("آدرس", "user:location", "primary"), create_button("پرسش‌های پرتکرار", "user:faq", "success")],
        [create_button("تماس", "user:contact", "danger"), create_button("ارسال بازخورد", "user:feedback", "primary")],
    ])


def upsert_mysql_user(user_id: int, username: str, full_name: str) -> None:
    if os.getenv("USE_MYSQL", "0") != "1" or mysql is None:
        return
    conn = mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"), user=os.getenv("MYSQL_USER", "root"), password=os.getenv("MYSQL_PASSWORD", ""), database=os.getenv("MYSQL_DB", "business_bot")
    )
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username VARCHAR(255), full_name VARCHAR(255), updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)")
    cur.execute("INSERT INTO users (user_id, username, full_name) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE username=VALUES(username), full_name=VALUES(full_name)", (user_id, username, full_name))
    conn.commit()
    cur.close()
    conn.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    user = update.effective_user
    data["visitors"][str(user.id)] = {"id": user.id, "username": user.username or "", "name": user.full_name or user.first_name or ""}
    save_data(data)
    upsert_mysql_user(user.id, user.username or "", user.full_name or user.first_name or "")
    text, pm = styled(f"سلام {user.first_name} 🌟\nبه ربات خوش آمدی. دکمه منو را بزن.", data)
    await update.message.reply_text(text, parse_mode=pm, reply_markup=ReplyKeyboardMarkup([["menu"]], resize_keyboard=True))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    user = update.effective_user
    if user.id not in data["visitors"]:
        data["visitors"].append(user.id)
        save_data(data)
    text, pm = styled(f"Welcome {user.first_name}!\nTap Menu to continue.", data)
    await update.message.reply_text(text, parse_mode=pm, reply_markup=ReplyKeyboardMarkup([["menu"]], resize_keyboard=True))

def admin_panel(data: dict[str, Any]) -> InlineKeyboardMarkup:
    onoff = "🟢 روشن" if data["active"] else "🔴 خاموش"
    bold = "🟢 Bold ON" if data.get("bold_mode") else "⚪ Bold OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🧊  {onoff}  🧊", callback_data="toggle:active")],
        [InlineKeyboardButton("🎨  ویرایش متن‌ها  🎨", callback_data="admin:texts")],
        [InlineKeyboardButton("💵  مشاهده تعرفه  💵", callback_data="visitor:pricing")],
        [InlineKeyboardButton(f"🔠  {bold}  🔠", callback_data="toggle:bold")],
        [InlineKeyboardButton("⚙️  مدیریت فیچر  ⚙️", callback_data="admin:features")],
        [InlineKeyboardButton("📊  گزارش وضعیت  📊", callback_data="admin:report")],
    ])

async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    if not is_admin(update.effective_user.id, data) or not feature_enabled(data, "admin_panel_access"):
        return
    await update.message.reply_text("پنل ادمین", reply_markup=admin_panel(data))


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
        mapping = {"user:services": ("user_services", data.get("service_text") or "متن خدمات ثبت نشده."), "user:hours": ("user_hours", data.get("hours_text") or "متن ساعات کاری ثبت نشده."), "user:location": ("user_location", data.get("location_text") or "متن آدرس ثبت نشده."), "user:faq": ("user_faq", data.get("faq_text") or "متن FAQ ثبت نشده."), "user:contact": ("user_contact", data.get("contact_text") or "متن تماس ثبت نشده."), "user:feedback": ("user_feedback", "بازخورد خود را در پیام بعدی ارسال کنید.")}
        fkey, msg = mapping.get(q.data, (None, "عملیات ناشناخته"))
        if fkey and feature_enabled(data, fkey):
            text, pm = styled(msg, data)
            await q.message.reply_text(text, parse_mode=pm)
        return

    if not is_admin(uid, data):
        return
    if q.data == "menu:admin":
        await q.message.reply_text("پنل ادمین", reply_markup=admin_panel(data))
    elif q.data == "toggle:active":
        data["active"] = not data["active"]
        save_data(data)
        await q.message.reply_text("وضعیت بروزرسانی شد.", reply_markup=admin_panel(data))
    elif q.data == "admin:features" and feature_enabled(data, "admin_toggle_features"):
        await q.message.reply_text("فیچرها", reply_markup=features_menu(data))
    elif q.data.startswith("feature:") and feature_enabled(data, "admin_toggle_features"):
        key = q.data.split(":", 1)[1]
        if key in data["features"]:
            data["features"][key] = not data["features"][key]
            if key == "admin_bold_mode":
                data["bold_mode"] = data["features"][key]
            save_data(data)
            await q.message.reply_text(f"{key} => {data['features'][key]}", reply_markup=features_menu(data))
    elif q.data == "admin:texts" and feature_enabled(data, "admin_edit_texts"):
        await q.message.reply_text("انتخاب متن برای ویرایش", reply_markup=text_menu())
    elif q.data.startswith("text:") and feature_enabled(data, "admin_edit_texts"):
        key = q.data.split(":", 1)[1]
        if key in TEXT_KEYS:
            STATE.wait_text_admin = uid
            STATE.wait_text_key = key
            old = data.get(key, "")
            await q.message.reply_text(f"متن قبلی ({TEXT_KEYS[key]}):\n{old or '(خالی)'}\n\nمتن جدید را بفرستید.")
    elif q.data == "admin:report" and feature_enabled(data, "admin_reports"):
        users = list(data["visitors"].values())
        lines = [f"{u.get('id')} | @{u.get('username') or '-'} | {u.get('name') or '-'}" for u in users[:30]]
        uptime_s = int(time.time() - START_TIME)
        report = f"تعداد کاربران: {len(users)}\nآپتایم: {uptime_s}s\nCPU: {psutil.cpu_percent()}%\nRAM: {psutil.virtual_memory().percent}%\n\nکاربران:\n" + ("\n".join(lines) if lines else "-")
        await q.message.reply_text(report)
    elif q.data == "admin:broadcast_help":
        await q.message.reply_text("از دستور /broadcast <متن> استفاده کنید.")


async def business_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bm = getattr(update, "business_message", None)
    if not bm or not bm.text:
        return
    if bm.text.strip() != "عجیبستان":
        return
    logging.info("Business test trigger received from chat_id=%s", bm.chat.id)
    try:
        kwargs = {"chat_id": bm.chat.id, "text": "✅ Business Bot Works"}
        bc_id = getattr(bm, "business_connection_id", None)
        if bc_id:
            kwargs["business_connection_id"] = bc_id
        await context.bot.send_message(**kwargs)
        logging.info("Business test reply sent successfully")
    except Exception as exc:
        logging.exception("Business test reply failed: %s", exc)


async def all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if getattr(update, "business_message", None):
        await business_test_handler(update, context)
        return
    if not update.message or not update.effective_user:
        return
    data = load_data()
    uid = update.effective_user.id
    txt = (update.message.text or "").strip()
    data["visitors"][str(uid)] = {"id": uid, "username": update.effective_user.username or "", "name": update.effective_user.full_name or update.effective_user.first_name or ""}
    save_data(data)
    upsert_mysql_user(uid, update.effective_user.username or "", update.effective_user.full_name or update.effective_user.first_name or "")

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
            await update.message.reply_text("پنل ادمین", reply_markup=admin_panel(data))
        return
    if txt.lower() == "menu":
        await update.message.reply_text("منوی کاربر", reply_markup=user_menu())
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
    sent = 0
    for suid in data["visitors"].keys():
        try:
            await context.bot.send_message(int(suid), text)
            sent += 1
        except Exception as exc:
            logging.warning("broadcast failed %s %s", suid, exc)
    await update.message.reply_text(f"ارسال شد برای {sent} کاربر")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled error: %s", context.error)


def main() -> None:
    setup_logging()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.ALL, all_messages))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
