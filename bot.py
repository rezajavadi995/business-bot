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

TEXT_KEYS = {
    "offline_message": "پیام آفلاین",
    "service_text": "متن خدمات",
    "pricing_text": "متن تعرفه‌ها",
    "hours_text": "متن ساعات کاری",
    "location_text": "متن آدرس",
}

DEFAULT_DATA: dict[str, Any] = {
    "admin_id": 0,
    "active": False,
    "force_join_channel": "",
    "bold_mode": False,
    "visitors": [],
    "features": {"f_visitor_auto_reply": True},
    "offline_message": "سلام 🌹\nالان آف هستم. پیام شما دریافت شد و در اولین فرصت پاسخ می‌دهم.",
    "service_text": "",
    "pricing_text": "",
    "hours_text": "",
    "location_text": "",
}
START_TIME = time.time()


@dataclass
class State:
    await_text_for_admin: int | None = None
    await_text_key: str | None = None


STATE = State()


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])


def load_data() -> dict[str, Any]:
    if not DATA_PATH.exists():
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    with DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for k, v in DEFAULT_DATA.items():
        data.setdefault(k, v)
    return data


def save_data(data: dict[str, Any]) -> None:
    with DATA_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_env(key: str, value: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    done = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            done = True
    if not done:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def is_admin(user_id: int, data: dict[str, Any]) -> bool:
    admin_id = int(data.get("admin_id") or os.getenv("ADMIN_ID") or 0)
    return user_id == admin_id


def apply_style(text: str, data: dict[str, Any]) -> tuple[str, str | None]:
    if data.get("bold_mode") and text:
        return f"<b>{text}</b>", ParseMode.HTML
    return text, None


def get_public_ip() -> str:
    try:
        return requests.get("https://api.ipify.org", timeout=8).text
    except Exception:
        return "Unknown"


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


def text_list_menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"🧩 {name}", callback_data=f"text:{key}")] for key, name in TEXT_KEYS.items()]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    uid = update.effective_user.id
    if uid not in data["visitors"]:
        data["visitors"].append(uid)
        save_data(data)
    msg, pm = apply_style("به ربات بیزینس خوش آمدید 🌟\nبرای پنل مدیریتی «menu» را بزنید.", data)
    await update.message.reply_text(msg, parse_mode=pm, reply_markup=ReplyKeyboardMarkup([["menu"]], resize_keyboard=True))


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = load_data()
    uid = q.from_user.id

    if q.data == "menu:admin":
        await q.message.reply_text("🎛 منوی مدیریت", reply_markup=admin_panel(data))
        return
    if not is_admin(uid, data):
        if q.data == "visitor:pricing":
            text, pm = apply_style(data.get("pricing_text", ""), data)
            await q.message.reply_text(text or "تعرفه هنوز ثبت نشده است.", parse_mode=pm)
        return

    if q.data == "toggle:active":
        data["active"] = not data["active"]
        save_data(data)
        await q.message.reply_text("✅ انجام شد", reply_markup=admin_panel(data))
    elif q.data == "toggle:bold":
        data["bold_mode"] = not data["bold_mode"]
        save_data(data)
        await q.message.reply_text("✅ حالت Bold تغییر کرد", reply_markup=admin_panel(data))
    elif q.data == "admin:texts":
        await q.message.reply_text("لیست متن‌ها برای ویرایش:", reply_markup=text_list_menu(data))
    elif q.data.startswith("text:"):
        key = q.data.split(":", 1)[1]
        if key in TEXT_KEYS:
            old = data.get(key, "")
            STATE.await_text_for_admin = uid
            STATE.await_text_key = key
            await q.message.reply_text(f"🔹 متن قبلی ({TEXT_KEYS[key]}):\n\n{old or '(خالی)'}\n\nمتن جدید را ارسال کنید:")
    elif q.data == "admin:report":
        uptime = int(time.time() - START_TIME)
        rep = f"👥 users: {len(data['visitors'])}\n🌐 ip: {get_public_ip()}\n⏱ uptime: {uptime}s\n💾 ram: {psutil.virtual_memory().percent}%"
        await q.message.reply_text(rep)
    elif q.data == "visitor:pricing":
        text, pm = apply_style(data.get("pricing_text", ""), data)
        await q.message.reply_text(text or "تعرفه هنوز ثبت نشده است.", parse_mode=pm)


async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    data = load_data()
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if uid not in data["visitors"]:
        data["visitors"].append(uid)
        save_data(data)

    if STATE.await_text_for_admin == uid and STATE.await_text_key and is_admin(uid, data):
        key = STATE.await_text_key
        old = data.get(key, "")
        data[key] = text
        save_data(data)
        STATE.await_text_for_admin = None
        STATE.await_text_key = None
        await update.message.reply_text(f"✅ ذخیره شد\n\n🔹 قبلی:\n{old or '(خالی)'}\n\n🔸 جدید:\n{text}")
        return

    if text.lower() == "menu":
        if is_admin(uid, data):
            await update.message.reply_text("🎛 منوی مدیریت", reply_markup=admin_panel(data))
            return
        if not data.get("active"):
            await update.message.reply_text("ربات هنوز فعال نشده است.")
            return
        await update.message.reply_text("منوی کاربر:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💵 تعرفه", callback_data="visitor:pricing")]]))
        return

    if not is_admin(uid, data) and data.get("active") and data["features"].get("f_visitor_auto_reply", True):
        msg, pm = apply_style(data.get("offline_message", ""), data)
        await update.message.reply_text(msg, parse_mode=pm)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        return
    msg = " ".join(context.args).strip()
    if not msg:
        await update.message.reply_text("Usage: /broadcast your message")
        return
    sent = 0
    for user_id in data["visitors"]:
        try:
            await context.bot.send_message(user_id, msg)
            sent += 1
        except Exception as exc:
            logging.warning("broadcast failed %s %s", user_id, exc)
    await update.message.reply_text(f"✅ Sent to {sent} users")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    app.add_handler(MessageHandler(filters.ALL, menu_text))
    app.add_error_handler(on_error)
    app.run_polling()


if __name__ == "__main__":
    main()
