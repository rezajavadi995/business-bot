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

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DATA_PATH = BASE_DIR / "data.json"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "bot.log"

load_dotenv(ENV_PATH)

ADMIN_FEATURES = [
    "admin_panel_access", "admin_broadcast", "admin_reports", "admin_edit_texts", "admin_toggle_features",
    "admin_user_stats", "admin_system_info", "admin_public_ip", "admin_export_users", "admin_bold_mode",
]
USER_FEATURES = [
    "user_auto_reply", "user_pricing", "user_services", "user_hours", "user_location",
    "user_faq", "user_contact", "user_request_callback", "user_feedback", "user_join_channel",
]

TEXT_KEYS = {
    "offline_message": "Offline message",
    "service_text": "Services text",
    "pricing_text": "Pricing text",
    "hours_text": "Working hours",
    "location_text": "Location text",
    "faq_text": "FAQ text",
    "contact_text": "Contact text",
}

DEFAULT_DATA: dict[str, Any] = {
    "admin_id": 0,
    "active": False,
    "force_join_channel": "",
    "visitors": [],
    "bold_mode": False,
    "features": {**{k: True for k in ADMIN_FEATURES}, **{k: True for k in USER_FEATURES}},
    "offline_message": "Thanks for your message. We will reply soon.",
    "service_text": "",
    "pricing_text": "",
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


def admin_panel(data: dict[str, Any]) -> InlineKeyboardMarkup:
    status = "🟢 ONLINE" if data["active"] else "🔴 OFFLINE"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🟢 {status}", callback_data="toggle:active")],
        [InlineKeyboardButton("🔵 Edit Texts", callback_data="admin:texts"), InlineKeyboardButton("🟡 Features", callback_data="admin:features")],
        [InlineKeyboardButton("🟣 Reports", callback_data="admin:report"), InlineKeyboardButton("🔴 Broadcast", callback_data="admin:broadcast_help")],
    ])


def features_menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = []
    for k in ADMIN_FEATURES + USER_FEATURES:
        st = "✅" if data["features"].get(k) else "❌"
        rows.append([InlineKeyboardButton(f"{st} {k}", callback_data=f"feature:{k}")])
    rows.append([InlineKeyboardButton("Back", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def text_menu() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(name, callback_data=f"text:{key}")] for key, name in TEXT_KEYS.items()]
    rows.append([InlineKeyboardButton("Back", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def user_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Services", callback_data="user:services"), InlineKeyboardButton("🟡 Pricing", callback_data="user:pricing")],
        [InlineKeyboardButton("🔵 Hours", callback_data="user:hours"), InlineKeyboardButton("🟣 Location", callback_data="user:location")],
        [InlineKeyboardButton("🟠 FAQ", callback_data="user:faq"), InlineKeyboardButton("🔴 Contact", callback_data="user:contact")],
        [InlineKeyboardButton("⚪ Feedback", callback_data="user:feedback"), InlineKeyboardButton("⚫ Request Callback", callback_data="user:callback")],
    ])


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


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    if not is_admin(update.effective_user.id, data) or not feature_enabled(data, "admin_panel_access"):
        return
    await update.message.reply_text("Admin Panel", reply_markup=admin_panel(data))


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    data = load_data()
    uid = q.from_user.id

    if q.data.startswith("user:"):
        if not data["active"]:
            await q.message.reply_text("Bot is offline.")
            return
        mapping = {
            "user:services": ("user_services", data.get("service_text") or "Services are not set yet."),
            "user:pricing": ("user_pricing", data.get("pricing_text") or "Pricing is not set yet."),
            "user:hours": ("user_hours", data.get("hours_text") or "Working hours are not set yet."),
            "user:location": ("user_location", data.get("location_text") or "Location is not set yet."),
            "user:faq": ("user_faq", data.get("faq_text") or "FAQ is not set yet."),
            "user:contact": ("user_contact", data.get("contact_text") or "Contact is not set yet."),
            "user:feedback": ("user_feedback", "Please send your feedback in next message."),
            "user:callback": ("user_request_callback", "Please send your phone number in next message."),
        }
        fkey, msg = mapping.get(q.data, (None, "Unknown action"))
        if fkey and feature_enabled(data, fkey):
            text, pm = styled(msg, data)
            await q.message.reply_text(text, parse_mode=pm)
        return

    if not is_admin(uid, data):
        return

    if q.data == "menu:admin":
        await q.message.reply_text("Admin Panel", reply_markup=admin_panel(data))
    elif q.data == "toggle:active":
        data["active"] = not data["active"]
        save_data(data)
        await q.message.reply_text("Status updated.", reply_markup=admin_panel(data))
    elif q.data == "admin:features" and feature_enabled(data, "admin_toggle_features"):
        await q.message.reply_text("Feature toggles", reply_markup=features_menu(data))
    elif q.data.startswith("feature:") and feature_enabled(data, "admin_toggle_features"):
        key = q.data.split(":", 1)[1]
        if key in data["features"]:
            data["features"][key] = not data["features"][key]
            save_data(data)
            await q.message.reply_text(f"{key} => {data['features'][key]}", reply_markup=features_menu(data))
    elif q.data == "admin:texts" and feature_enabled(data, "admin_edit_texts"):
        await q.message.reply_text("Choose text to edit", reply_markup=text_menu())
    elif q.data.startswith("text:") and feature_enabled(data, "admin_edit_texts"):
        key = q.data.split(":", 1)[1]
        if key in TEXT_KEYS:
            STATE.wait_text_admin = uid
            STATE.wait_text_key = key
            old = data.get(key, "")
            await q.message.reply_text(f"Current ({TEXT_KEYS[key]}):\n{old or '(empty)'}\n\nSend new text now.")
    elif q.data == "admin:report" and feature_enabled(data, "admin_reports"):
        uptime_s = int(time.time() - START_TIME)
        report = f"Users: {len(data['visitors'])}\nUptime: {uptime_s}s\nCPU: {psutil.cpu_percent()}%\nRAM: {psutil.virtual_memory().percent}%"
        await q.message.reply_text(report)
    elif q.data == "admin:broadcast_help":
        await q.message.reply_text("Use /broadcast <text>")


async def all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    uid = update.effective_user.id
    txt = (update.message.text or "").strip()

    if uid not in data["visitors"]:
        data["visitors"].append(uid)
        save_data(data)

    if STATE.wait_text_admin == uid and STATE.wait_text_key and is_admin(uid, data):
        key = STATE.wait_text_key
        old = data.get(key, "")
        data[key] = txt
        save_data(data)
        STATE.wait_text_admin = None
        STATE.wait_text_key = None
        await update.message.reply_text(f"Updated.\nOld:\n{old or '(empty)'}\n\nNew:\n{txt}")
        return

    if txt.lower() == "panel":
        if is_admin(uid, data):
            await update.message.reply_text("Admin Panel", reply_markup=admin_panel(data))
        return

    if txt.lower() == "menu":
        await update.message.reply_text("User Menu", reply_markup=user_menu())
        return

    if is_admin(uid, data):
        return

    if not data["active"]:
        return

    if feature_enabled(data, "user_auto_reply"):
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
    for uid in data["visitors"]:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
        except Exception as exc:
            logging.warning("broadcast failed %s %s", uid, exc)
    await update.message.reply_text(f"Sent to {sent} users")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled error: %s", context.error)


def main() -> None:
    setup_logging()
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
