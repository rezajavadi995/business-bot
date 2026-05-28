from __future__ import annotations
import json
import time
from dataclasses import dataclass
from typing import Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from features.inline_callback import cb

CB = {
    "ROOT": "im:root",
    "TOGGLE": "im:toggle",
    "CREATE": "im:create",
    "ADD_BTN": "im:addbtn",
    "MGR": "im:mgr",
    "EDIT": "im:edit",
    "LIVE": "im:live",
    "ACTIVE": "im:active",
    "CANCEL": "im:cancel",
    "CONFIRM_YES": "im:confirm:yes",
    "CONFIRM_NO": "im:confirm:no",
}


def build_inline_menu_admin_kb(enabled: bool, global_active: bool) -> InlineKeyboardMarkup:
    lock = " 🔒" if not global_active else ""
    state = ("ON" if enabled else "OFF") + lock
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🧩 Inline Menu: {state}", callback_data=CB["TOGGLE"])],
        [InlineKeyboardButton("🆕 ساخت منوی جدید", callback_data=CB["CREATE"]), InlineKeyboardButton("➕ افزودن دکمه", callback_data=CB["ADD_BTN"])],
        [InlineKeyboardButton("🗂️ مدیریت منوها", callback_data=CB["MGR"]), InlineKeyboardButton("✏️ ویرایش منو", callback_data=CB["EDIT"])],
        [InlineKeyboardButton("🟢 مدیریت فعال/غیرفعال", callback_data=CB["ACTIVE"]), InlineKeyboardButton("📡 لیست زنده منوها", callback_data=CB["LIVE"])],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:admin")],
    ])


def paged_rows(items: list[dict[str, Any]], prefix: str, page: int, page_size: int = 8):
    start = page * page_size
    chunk = items[start:start + page_size]
    rows = [[InlineKeyboardButton(x["label"], callback_data=cb(prefix, str(x["id"])))] for x in chunk]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=cb(prefix, "page", str(page-1))))
    if start + page_size < len(items):
        nav.append(InlineKeyboardButton("➡️", callback_data=cb(prefix, "page", str(page+1))))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🧨 Cancel", callback_data=CB["CANCEL"])])
    return InlineKeyboardMarkup(rows)
