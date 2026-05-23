from pathlib import Path
from telegram import InlineKeyboardMarkup, InlineKeyboardButton


def build_logs_keyboard(base_dir: Path) -> InlineKeyboardMarkup:
    logs_dir = base_dir / "logs"
    rows = []
    if logs_dir.exists():
        for fp in sorted(logs_dir.glob("*.log")):
            rows.append([InlineKeyboardButton(f"📄 {fp.name}", callback_data=f"admin:log_file:{fp.name}")])
    if not rows:
        rows.append([InlineKeyboardButton("(لاگی پیدا نشد)", callback_data="noop")])
    rows.append([InlineKeyboardButton("🔴 بازگشت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)
