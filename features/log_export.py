from __future__ import annotations

from pathlib import Path
from typing import Iterable
from telegram import InlineKeyboardMarkup, InlineKeyboardButton


def build_logs_keyboard(base_dir: Path) -> InlineKeyboardMarkup:
    logs_dir = base_dir / "logs"
    rows = []
    if logs_dir.exists():
        for fp in iter_log_files(base_dir):
            rel = fp.relative_to(base_dir).as_posix()
            rows.append([InlineKeyboardButton(f"📄 {rel}", callback_data=f"admin:log_file:{rel}")])
    if not rows:
        rows.append([InlineKeyboardButton("(لاگی پیدا نشد)", callback_data="noop")])
    rows.append([InlineKeyboardButton("🔴 بازگشت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def iter_log_files(base_dir: Path) -> Iterable[Path]:
    logs_dir = base_dir / "logs"
    if not logs_dir.exists():
        return []
    return sorted([p for p in logs_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".log", ".txt"}], key=lambda p: p.as_posix())


def humanize_log_text(path: Path, raw: str, max_lines: int = 1500) -> str:
    lines = raw.splitlines()
    tail = lines[-max_lines:] if len(lines) > max_lines else lines
    pretty: list[str] = []
    for ln in tail:
        if " | " in ln:
            parts = ln.split(" | ", 3)
            if len(parts) == 4:
                ts, level, logger, msg = parts
                pretty.append(f"[{ts}] [{level}] [{logger}] {msg}")
                continue
        pretty.append(ln)
    header = [
        "========== Telegram Business Bot Log Export ==========",
        f"Source File : {path.as_posix()}",
        f"Line Count  : {len(lines)}",
        f"Included    : last {len(tail)} line(s)",
        "=====================================================",
        "",
    ]
    return "\n".join(header + pretty) + "\n"
