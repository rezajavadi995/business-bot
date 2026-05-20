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

DEFAULT_DATA: dict[str, Any] = {
    "admin_id": 0,
    "active": False,
    "visitors": [],
    "features": {
        "f_admin_stats": True,
        "f_admin_broadcast": True,
        "f_admin_manage_offline": True,
        "f_admin_reply_all": True,
        "f_admin_uptime": True,
        "f_admin_sysinfo": True,
        "f_admin_public_ip": True,
        "f_admin_user_count": True,
        "f_admin_block_user": True,
        "f_admin_quick_reports": True,
        "f_visitor_auto_reply": True,
        "f_visitor_help": True,
        "f_visitor_contact_admin": True,
        "f_visitor_faq": True,
        "f_visitor_services": True,
        "f_visitor_price_list": True,
        "f_visitor_location": True,
        "f_visitor_working_hours": True,
        "f_visitor_request_call": True,
        "f_visitor_feedback": True,
    },
    "offline_message": "سلام 🌹\nالان آف هستم. پیام شما دریافت شد و در اولین فرصت پاسخ می‌دهم.",
    "blocked_users": [],
    "service_text": "لیست خدمات شما اینجا قرار می‌گیرد.",
    "pricing_text": "تعرفه‌ها اینجا قرار می‌گیرد.",
    "hours_text": "ساعات کاری: شنبه تا چهارشنبه ۹ تا ۱۸",
    "location_text": "آدرس کسب‌وکار شما اینجاست.",
}
START_TIME = time.time()


@dataclass
class State:
    await_offline_text_for: int | None = None


STATE = State()


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def load_data() -> dict[str, Any]:
    if not DATA_PATH.exists():
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    with DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict[str, Any]) -> None:
    with DATA_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_env(key: str, value: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    for idx, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[idx] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def is_admin(user_id: int, data: dict[str, Any]) -> bool:
    return user_id == int(data.get("admin_id") or 0)

STATE = State()

def uptime() -> str:
    seconds = int(time.time() - START_TIME)
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m {seconds % 60}s"


def get_system_info() -> str:
    return f"🖥 CPU: {psutil.cpu_percent()}%\n💾 RAM: {psutil.virtual_memory().percent}%\n📂 Disk: {psutil.disk_usage('/').percent}%"

def load_data() -> dict[str, Any]:
    if not DATA_PATH.exists():
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    with DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)

def get_public_ip() -> str:
    try:
        return requests.get("https://api.ipify.org", timeout=8).text
    except Exception:
        return "Unknown"

def save_data(data: dict[str, Any]) -> None:
    with DATA_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["menu"]], resize_keyboard=True)


def admin_panel(data: dict[str, Any]) -> InlineKeyboardMarkup:
    active = "🟢 روشن" if data["active"] else "🔴 خاموش"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"✨ وضعیت ربات: {active}", callback_data="toggle:bot_active")],
            [
                InlineKeyboardButton("👤 ست ادمین", callback_data="set:admin_id"),
                InlineKeyboardButton("🔑 ست توکن", callback_data="set:token"),
            ],
            [InlineKeyboardButton("✍️ تغییر پیام آفلاین", callback_data="set:offline_text")],
            [
                InlineKeyboardButton("📊 گزارش سریع", callback_data="admin:report"),
                InlineKeyboardButton("⚙️ فیچرها", callback_data="menu:features"),
            ],
            [InlineKeyboardButton("🚀 فعال‌سازی سرویس دائمی (systemd)", callback_data="admin:systemd_help")],
        ]
    )


def feature_menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{'✅' if v else '❌'} {k}", callback_data=f"toggle:{k}")] for k, v in data["features"].items()]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = update.effective_user
    if user.id not in data["visitors"]:
        data["visitors"].append(user.id)
        save_data(data)
    await update.message.reply_text("به ربات بیزینس خوش آمدید 🌟\nبرای پنل مدیریتی «menu» را بزنید.", reply_markup=main_menu_keyboard())


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    uid = update.effective_user.id
    if is_admin(uid, data):
        await update.message.reply_text("🎛 منوی ادمین مدرن", reply_markup=admin_panel(data))
        return
    if not data["active"]:
        await update.message.reply_text("ربات هنوز توسط ادمین فعال نشده است.")
        return
    await update.message.reply_text(
        "منوی کاربر:",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("ℹ️ راهنما", callback_data="visitor:help"), InlineKeyboardButton("🛍 خدمات", callback_data="visitor:services")],
                [InlineKeyboardButton("💵 تعرفه", callback_data="visitor:pricing"), InlineKeyboardButton("🕒 ساعات کاری", callback_data="visitor:hours")],
            ]
        ),
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = load_data()
    uid = q.from_user.id

    if q.data.startswith("toggle:") and is_admin(uid, data):
        key = q.data.split(":", 1)[1]
        if key == "bot_active":
            data["active"] = not data["active"]
        elif key in data["features"]:
            data["features"][key] = not data["features"][key]
        save_data(data)
        await q.message.reply_text("✅ بروزرسانی شد", reply_markup=admin_panel(data))
        return

    if not is_admin(uid, data) and q.data.startswith(("set:", "menu:features", "admin:")):
        return

    if q.data == "menu:admin":
        await q.message.reply_text("پنل ادمین", reply_markup=admin_panel(data))
    elif q.data == "menu:features":
        await q.message.reply_text("فیچرها (هرکدام ON/OFF)", reply_markup=feature_menu(data))
    elif q.data == "set:admin_id":
        data["admin_id"] = uid
        save_data(data)
        update_env("ADMIN_ID", str(uid))
        await q.message.reply_text(f"ادمین ست شد: `{uid}`", parse_mode=ParseMode.MARKDOWN)
    elif q.data == "set:token":
        await q.message.reply_text("توکن از .env خوانده می‌شود. مقدار BOT_TOKEN را در سرور تغییر دهید.")
    elif q.data == "set:offline_text":
        STATE.await_offline_text_for = uid
        await q.message.reply_text("متن جدید پیام آفلاین را ارسال کنید.")
    elif q.data == "admin:report":
        await q.message.reply_text(f"👥 Users: {len(data['visitors'])}\n⏱ Uptime: {uptime()}\n🌐 IP: {get_public_ip()}\n{get_system_info()}")
    elif q.data == "admin:systemd_help":
        await q.message.reply_text("برای سرویس دائمی سرور، در ترمینال فقط بنویسید:\n`manage`\nسپس گزینه «Enable systemd service» را انتخاب کن.", parse_mode=ParseMode.MARKDOWN)
    elif q.data == "visitor:help":
        await q.message.reply_text("برای ارتباط پیام بدهید. در اولین فرصت پاسخ می‌گیرید.")
    elif q.data == "visitor:services":
        await q.message.reply_text(data["service_text"])
    elif q.data == "visitor:pricing":
        await q.message.reply_text(data["pricing_text"])
    elif q.data == "visitor:hours":
        await q.message.reply_text(data["hours_text"])


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast your message")
        return
    msg = " ".join(context.args)
    sent = 0
    for user_id in data["visitors"]:
        if user_id in data["blocked_users"]:
            continue
        try:
            await context.bot.send_message(user_id, msg)
            sent += 1
        except Exception as exc:
            logging.warning("send failed %s: %s", user_id, exc)
    await update.message.reply_text(f"✅ Sent to {sent} users")


async def all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    data = load_data()
    uid = update.effective_user.id
    text = update.message.text or ""
    if text.strip().lower() == "menu":
        await menu(update, context)
        return
    if STATE.await_offline_text_for == uid and is_admin(uid, data):
        data["offline_message"] = text
        save_data(data)
        STATE.await_offline_text_for = None
        await update.message.reply_text("✅ پیام آفلاین ذخیره شد.")
        return
    if uid not in data["visitors"]:
        data["visitors"].append(uid)
        save_data(data)
    if is_admin(uid, data) or not data["active"]:
        return
    if data["features"].get("f_visitor_auto_reply", True):
        await update.message.reply_text(data["offline_message"])


def main() -> None:
    setup_logging()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing. Set it in .env")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.ALL, all_messages))
    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
