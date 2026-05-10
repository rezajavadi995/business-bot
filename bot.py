import os
import time
import socket
import psutil
import requests

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = 8062924341

#AUTO_REPLY = True

START_TIME = time.time()

USERS = set()

# =========================
# HELPERS
# =========================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def uptime():
    seconds = int(time.time() - START_TIME)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    return f"{hours}h {minutes}m {secs}s"


def get_public_ip():
    try:
        return requests.get("https://api.ipify.org").text
    except:
        return "Unknown"


def get_system_info():
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent

    return (
        f"🖥 CPU: {cpu}%\n"
        f"💾 RAM: {ram}%\n"
        f"📂 Disk: {disk}%"
    )


# =========================
# KEYBOARDS
# =========================

def admin_panel():
    keyboard = [
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("🌐 IP", callback_data="ip")
        ],
        [
            InlineKeyboardButton("👥 Users", callback_data="users"),
            InlineKeyboardButton("⏱ Uptime", callback_data="uptime")
        ],
        [
            InlineKeyboardButton("✅ Auto Reply ON", callback_data="on"),
            InlineKeyboardButton("❌ Auto Reply OFF", callback_data="off")
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================
# COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    USERS.add(user.id)

    text = (
        f"سلام {user.first_name} 🌹\n\n"
        f"ربات بیزینسی فعاله."
    )

    if is_admin(user.id):
        text += "\n\n👑 شما ادمین هستید."

        await update.message.reply_text(
            text,
            reply_markup=admin_panel()
        )

    else:
        await update.message.reply_text(text)


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text(
        "🎛 پنل مدیریت",
        reply_markup=admin_panel()
    )


# =========================
# CALLBACKS
# =========================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_REPLY

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_admin(user_id):
        return

    data = query.data

    if data == "status":

        await query.message.reply_text(
            f"🤖 Bot Online\n\n{get_system_info()}"
        )

    elif data == "ip":

        ip = get_public_ip()

        await query.message.reply_text(
            f"🌐 Public IP:\n\n{ip}"
        )

    elif data == "users":

        await query.message.reply_text(
            f"👥 Total Users:\n\n{len(USERS)}"
        )

    elif data == "uptime":

        await query.message.reply_text(
            f"⏱ Uptime:\n\n{uptime()}"
        )

    elif data == "on":
        context.bot_data["AUTO_REPLY"] = True
        await query.message.reply_text("✅ Auto Reply Enabled")

    elif data == "off":
        context.bot_data["AUTO_REPLY"] = False
        await query.message.reply_text("❌ Auto Reply Disabled")


# =========================
# BROADCAST
# =========================

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/broadcast your message"
        )
        return

    msg = " ".join(context.args)

    sent = 0

    for user_id in USERS:
        try:
            await context.bot.send_message(user_id, msg)
            sent += 1
        except:
            pass

    await update.message.reply_text(
        f"✅ Sent to {sent} users"
    )


# ==========================
# msg for update
# ==============
async def on_startup(app: Application):
    await app.bot.send_message(
        chat_id=ADMIN_ID,
        text="🤖 ربات آپدیت شد و با موفقیت آنلاین است\n\nخیالت راحت 🌹"
    )


# =========================
# AUTO REPLY
# =========================

async def auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    user = update.effective_user

    USERS.add(user.id)

    if is_admin(user.id):
        return

    # 🔥 حالت صحیح
    if not context.bot_data.get("AUTO_REPLY", True):
        return

    text = (
        "<b>سلام دوست عزیز⚘️</b>\n\n"
        "<b>🔰 پیام شما توسط ربات دریافت شد 🔰</b>\n\n"
        "از این که تا زمان پاسخ‌گویی صبور هستید،\n"
        "از شما بسیار سپاس‌گزاریم 🙏"
    )

    await update.message.reply_text(text, parse_mode="HTML")


# =========================
# MAIN
# =========================

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["AUTO_REPLY"] = True
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(callbacks))

    app.add_handler(
        MessageHandler(
           # filters.TEXT & ~filters.COMMAND,
            filters.ALL
            auto_reply
        )
    )

    print("Bot Started...")
    app.post_init = on_startup

    app.run_polling()


if __name__ == "__main__":
    main()
