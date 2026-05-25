from __future__ import annotations
import json
import time
from dataclasses import dataclass
from typing import Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CB = {
    "ROOT": "im:root",
    "TOGGLE": "im:toggle",
    "CREATE": "im:create",
    "ADD_BTN": "im:addbtn",
    "MGR": "im:mgr",
    "EDIT": "im:edit",
    "CANCEL": "im:cancel",
    "CONFIRM_YES": "im:confirm:yes",
    "CONFIRM_NO": "im:confirm:no",
}


def build_inline_menu_admin_kb(enabled: bool, global_active: bool) -> InlineKeyboardMarkup:
    lock = " 🔒" if not global_active else ""
    state = ("ON" if enabled else "OFF") + lock
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🧩 Inline Menu: {state}", callback_data=CB["TOGGLE"])],
        [InlineKeyboardButton("🆕 Create New Menu", callback_data=CB["CREATE"]), InlineKeyboardButton("➕ Add Button", callback_data=CB["ADD_BTN"])],
        [InlineKeyboardButton("🗂 Menu Manager", callback_data=CB["MGR"]), InlineKeyboardButton("✏️ Edit Menu", callback_data=CB["EDIT"])],
        [InlineKeyboardButton("🧨 بازگشت", callback_data="menu:admin")],
    ])


def paged_rows(items: list[dict[str, Any]], prefix: str, page: int, page_size: int = 8):
    start = page * page_size
    chunk = items[start:start + page_size]
    rows = [[InlineKeyboardButton(x["label"], callback_data=f"{prefix}:{x['id']}")] for x in chunk]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}:page:{page-1}"))
    if start + page_size < len(items):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"{prefix}:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🧨 Cancel", callback_data=CB["CANCEL"])])
    return InlineKeyboardMarkup(rows)
