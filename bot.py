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

db = DB(DB_PATH)

def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])

def get_default_data() -> dict[str, Any]:
    return {"admin_id": int(os.getenv("ADMIN_ID") or 0), "active": False, "force_join_channel": os.getenv("FORCE_JOIN_CHANNEL", ""), "bold_mode": True,
            "welcome_enabled": True, "welcome_text": "سلام 🌟\nبه پیج بیزینسی ما خوش آمدید.", "self_bot_enabled": False,
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
    return InlineKeyboardMarkup([[create_primary_button("مشاهده شورت‌کات‌های فعلی","admin:shortcut_view")],[create_success_button("افزودن/ویرایش شورت‌کات","admin:shortcut_cfg")],[create_danger_button("بازگشت","menu:admin")]])

def create_admin_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    status = "روشن" if data["active"] else "خاموش"; selfb="ON" if data.get("self_bot_enabled") else "OFF"; wel="ON" if data.get("welcome_enabled") else "OFF"
    status_btn=create_success_button(f"وضعیت ربات: {status}","toggle:active") if data["active"] else create_danger_button(f"وضعیت ربات: {status}","toggle:active")
    return InlineKeyboardMarkup([[status_btn],[create_primary_button("ویرایش متن‌ها","admin:texts"),create_primary_button("فیچرها","admin:features")],[create_success_button("گزارش وضعیت","admin:report"),create_danger_button("راهنمای برودکست","admin:broadcast_help")],[create_primary_button(f"Self Bot: {selfb}","admin:selfbot"),create_primary_button("مدیریت سلف بات","admin:shortcut_menu")],[create_primary_button(f"Welcome: {wel}","admin:welcome_toggle"),create_primary_button("پیکربندی Welcome","admin:welcome_cfg")],[create_primary_button("پیام‌های بازخورد","admin:feedback_list"),create_danger_button("بازگشت","menu:admin")]])

def create_features_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    rows=[[create_primary_button(f"{'✅' if data['features'].get(k) else '❌'} {k}",f"feature:{k}")] for k in ADMIN_FEATURES+USER_FEATURES]; rows.append([create_danger_button("بازگشت","menu:admin")]); return InlineKeyboardMarkup(rows)

def create_texts_keyboard() -> InlineKeyboardMarkup:
    rows=[[create_primary_button(name,f"text:{key}")] for key,name in TEXT_KEYS.items()]; rows.append([create_danger_button("بازگشت","menu:admin")]); return InlineKeyboardMarkup(rows)

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


async def send_formatted_message(target, text: str, data: dict[str, Any]):
    safe = preserve_tg_emoji_markup(text if text is not None else "")
    if data.get("bold_mode", True): safe = f"<b>{safe}</b>"
    await target.reply_text(safe, parse_mode=ParseMode.HTML)

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
    t=text.lower()
    for k,v in shortcuts.items():
        if not k or not str(k).strip(): continue
        kk=str(k).strip().lower()
        if kk==t or kk in t: return v
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
        if not data["active"]: await q.message.reply_text("ربات خاموش است."); return
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
    elif q.data=="admin:selfbot": data["self_bot_enabled"]=not data.get("self_bot_enabled",False); save_data(data); await q.edit_message_text("وضعیت Self Bot تغییر کرد.", reply_markup=create_admin_keyboard(data))
    elif q.data=="admin:shortcut_menu": await q.edit_message_text("مدیریت سلف بات", reply_markup=create_shortcut_menu_keyboard())
    elif q.data=="admin:shortcut_view":
        sc=db.load_shortcuts(); out="📚 شورت‌کات‌های فعلی:\n\n" + ("\n".join([f"• {k} => {v}" for k,v in sc.items()]) if sc else "موردی ثبت نشده است.")
        await q.edit_message_text(out, reply_markup=create_shortcut_menu_keyboard())
    elif q.data=="admin:shortcut_cfg": STATE.flow,STATE.step,STATE.admin_id,STATE.message_id,STATE.temp_shortcuts="shortcut_cfg","waiting_name",uid,q.message.message_id,{}; await q.edit_message_text("نام شورت‌کات را وارد کنید:", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="shortcut:continue_yes": STATE.step="waiting_name"; await q.edit_message_text("نام شورت‌کات بعدی را وارد کنید:", reply_markup=build_back_kb("admin:shortcut_menu"))
    elif q.data=="shortcut:continue_no": db.save_shortcuts(STATE.temp_shortcuts or {}); STATE.flow=STATE.step=STATE.pending_key=None; STATE.temp_shortcuts=None; await q.edit_message_text("شورت‌کات‌ها ذخیره شدند.", reply_markup=create_shortcut_menu_keyboard())
    elif q.data=="admin:welcome_toggle": data["welcome_enabled"]=not data.get("welcome_enabled",True); save_data(data); await q.edit_message_text("وضعیت Welcome تغییر کرد.", reply_markup=create_admin_keyboard(data))
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

async def business_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bm=getattr(update,"business_message",None)
    if not bm or not bm.text: return
    data=load_data(); txt=bm.text.strip(); uid=(bm.from_user.id if bm.from_user else bm.chat.id)
    prev=db.get_user(uid)
    if data.get("welcome_enabled",True) and (prev is None or int(prev["last_seen_at"] or 0)<=0 or int(time.time())-int(prev["last_seen_at"] or 0)>=WELCOME_COOLDOWN_SECONDS):
        try: await context.bot.send_message(chat_id=bm.chat.id, text=f"<b>{html.escape(data.get('welcome_text','خوش آمدید'))}</b>", parse_mode=ParseMode.HTML, business_connection_id=getattr(bm,"business_connection_id",None))
        except Exception: pass
        db.upsert_user(uid,(bm.from_user.username if bm.from_user else "") or "",(bm.from_user.full_name if bm.from_user else bm.chat.full_name) or "",None,False,"business",txt); logging.info("welcome_trigger_business uid=%s",uid); return
    db.upsert_user(uid,(bm.from_user.username if bm.from_user else "") or "",(bm.from_user.full_name if bm.from_user else bm.chat.full_name) or "",None,False,"business",txt)
    if txt=="عجیبستان":
        kwargs={"chat_id":bm.chat.id,"text":"✅ Business Bot Works"}; bc=getattr(bm,"business_connection_id",None)
        if bc: kwargs["business_connection_id"]=bc
        await context.bot.send_message(**kwargs); return
    sc=db.load_shortcuts(); resp=match_shortcut(txt, sc)
    if resp:
        kwargs={"chat_id":bm.chat.id,"text":f"<b>{preserve_tg_emoji_markup(resp)}</b>","parse_mode":ParseMode.HTML}; bc=getattr(bm,"business_connection_id",None)
        if bc: kwargs["business_connection_id"]=bc
        await context.bot.send_message(**kwargs); logging.info("business_shortcut_sent uid=%s",uid)

async def all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if getattr(update,"business_message",None): await business_test_handler(update, context); return
    if not update.message or not update.effective_user: return
    data=load_data(); uid=update.effective_user.id; txt=(update.message.text or "").strip()
    row=db.get_user(uid)
    if row and int(row["soft_ban_until"] or 0)>int(time.time()): return
    db.upsert_user(uid, update.effective_user.username or "", update.effective_user.full_name or update.effective_user.first_name or "", update.message.contact.phone_number if update.message.contact else None, False, "message", txt)

    if STATE.admin_id==uid and STATE.flow=="text_edit" and STATE.step=="waiting_value" and STATE.pending_key and can_edit_flow(uid):
        key=STATE.pending_key; old=data.get(key,""); data[key]=txt; save_data(data); STATE.flow=STATE.step=STATE.pending_key=None
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"ذخیره شد.\nمتن قبلی:\n{old or '(خالی)'}\n\nمتن جدید:\n{txt}", reply_markup=create_texts_keyboard()); return
    if STATE.admin_id==uid and STATE.flow=="shortcut_cfg" and can_edit_flow(uid):
        if STATE.step=="waiting_name": STATE.pending_key=txt; STATE.step="waiting_value"; await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"نام شورت‌کات: {txt}\nمتن شورت‌کات را وارد کنید:", reply_markup=build_back_kb("admin:shortcut_menu")); return
        if STATE.step=="waiting_value": (STATE.temp_shortcuts or {})[STATE.pending_key or ""]=txt; STATE.step="confirm_continue"; kb=InlineKeyboardMarkup([[create_success_button("ادامه","shortcut:continue_yes"),create_danger_button("پایان","shortcut:continue_no")]]); await context.bot.edit_message_text(chat_id=update.effective_chat.id,message_id=STATE.message_id,text="آیا ادامه می‌دهید؟",reply_markup=kb); return
    if STATE.admin_id==uid and STATE.flow=="welcome_cfg" and STATE.step=="waiting_value" and can_edit_flow(uid):
        old=data.get("welcome_text",""); data["welcome_text"]=txt; save_data(data); STATE.flow=STATE.step=None
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=STATE.message_id, text=f"Welcome بروزرسانی شد.\nقبلی:\n{old}\n\nجدید:\n{txt}", reply_markup=create_admin_keyboard(data)); return

    shortcuts=db.load_shortcuts()
    if is_spam(txt,shortcuts):
        ban_until = int(time.time())+SOFT_BAN_SECONDS
        db.set_soft_ban(uid, ban_until)
        logging.warning("anti_spam_trigger uid=%s ban_until=%s text=%s", uid, ban_until, txt)
        return

    if txt.lower()=="panel":
        if is_admin(uid,data): await update.message.reply_text("پنل ادمین", reply_markup=create_admin_keyboard(data)); return
    if txt.lower()=="menu":
        logging.info("user_menu_open uid=%s", uid)
        await update.message.reply_text("منوی کاربر", reply_markup=create_menu_keyboard())
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

    resp=match_shortcut(txt,shortcuts)
    if resp:
        if data.get("self_bot_enabled") and is_admin(uid,data):
            try: await update.message.delete()
            except Exception: pass
            await send_formatted_message(update.message, resp, data); return
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
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__": main()
