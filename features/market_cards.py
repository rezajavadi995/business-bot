from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

_CARD_CACHE: dict[str, tuple[float, bytes]] = {}
_CARD_CACHE_TTL_SECONDS = 300
_FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
_DEFAULT_REGULAR = str(_FONT_DIR / "DejaVuSans.ttf")
_DEFAULT_BOLD = str(_FONT_DIR / "DejaVuSans-Bold.ttf")
_DEFAULT_SERIF = str(_FONT_DIR / "DejaVuSerif.ttf")
_DEFAULT_SERIF_BOLD = str(_FONT_DIR / "DejaVuSerif-Bold.ttf")
_DEFAULT_MONO = str(_FONT_DIR / "DejaVuSansMono.ttf")
_DEFAULT_MONO_BOLD = str(_FONT_DIR / "DejaVuSansMono-Bold.ttf")

PERSIAN_FONT_CHOICES: dict[str, dict[str, str]] = {
    "vazir": {"label": "Vazir", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "shabnam": {"label": "Shabnam", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "sahel": {"label": "Sahel", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "samim": {"label": "Samim", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "yekan": {"label": "Yekan", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "iransans": {"label": "IRANSans", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "estedad": {"label": "Estedad", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "nahid": {"label": "Nahid", "regular": _DEFAULT_SERIF, "bold": _DEFAULT_SERIF_BOLD},
    "parastoo": {"label": "Parastoo", "regular": _DEFAULT_SERIF, "bold": _DEFAULT_SERIF_BOLD},
    "tanha": {"label": "Tanha", "regular": _DEFAULT_MONO, "bold": _DEFAULT_MONO_BOLD},
}

ENGLISH_FONT_CHOICES: dict[str, dict[str, str]] = {
    "inter": {"label": "Inter", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "roboto": {"label": "Roboto", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "open_sans": {"label": "Open Sans", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "montserrat": {"label": "Montserrat", "regular": _DEFAULT_BOLD, "bold": _DEFAULT_BOLD},
    "lato": {"label": "Lato", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "poppins": {"label": "Poppins", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "nunito": {"label": "Nunito", "regular": _DEFAULT_REGULAR, "bold": _DEFAULT_BOLD},
    "serif": {"label": "Serif", "regular": _DEFAULT_SERIF, "bold": _DEFAULT_SERIF_BOLD},
    "mono": {"label": "Mono", "regular": _DEFAULT_MONO, "bold": _DEFAULT_MONO_BOLD},
    "display": {"label": "Display", "regular": _DEFAULT_SERIF_BOLD, "bold": _DEFAULT_SERIF_BOLD},
}

CARD_THEMES: dict[str, dict[str, Any]] = {
    "glass": {"label": "Glass", "primary": "#2336ff", "secondary": "#8a2be2", "dark": True},
    "midnight": {"label": "Midnight", "primary": "#07111f", "secondary": "#172554", "dark": True},
    "aurora": {"label": "Aurora", "primary": "#00c6ff", "secondary": "#0072ff", "dark": True},
    "sunset": {"label": "Sunset", "primary": "#ff512f", "secondary": "#dd2476", "dark": True},
    "emerald": {"label": "Emerald", "primary": "#11998e", "secondary": "#38ef7d", "dark": True},
    "royal": {"label": "Royal", "primary": "#141e30", "secondary": "#6a11cb", "dark": True},
    "gold": {"label": "Gold", "primary": "#f7971e", "secondary": "#ffd200", "dark": False},
    "ice": {"label": "Ice", "primary": "#e0eafc", "secondary": "#cfdef3", "dark": False},
    "rose": {"label": "Rose", "primary": "#f953c6", "secondary": "#b91d73", "dark": True},
    "graphite": {"label": "Graphite", "primary": "#232526", "secondary": "#414345", "dark": True},
}

COLOR_PALETTES: dict[str, dict[str, str]] = {
    "blue_violet": {"label": "Blue/Violet", "primary": "#2336ff", "secondary": "#8a2be2"},
    "ocean": {"label": "Ocean", "primary": "#00c6ff", "secondary": "#0072ff"},
    "sunset": {"label": "Sunset", "primary": "#ff512f", "secondary": "#dd2476"},
    "emerald": {"label": "Emerald", "primary": "#11998e", "secondary": "#38ef7d"},
    "purple": {"label": "Purple", "primary": "#8e2de2", "secondary": "#4a00e0"},
    "gold": {"label": "Gold", "primary": "#f7971e", "secondary": "#ffd200"},
    "rose": {"label": "Rose", "primary": "#f953c6", "secondary": "#b91d73"},
    "mint": {"label": "Mint", "primary": "#00b09b", "secondary": "#96c93d"},
    "fire": {"label": "Fire", "primary": "#f12711", "secondary": "#f5af19"},
    "night": {"label": "Night", "primary": "#141e30", "secondary": "#243b55"},
    "ice": {"label": "Ice", "primary": "#e0eafc", "secondary": "#cfdef3"},
    "carbon": {"label": "Carbon", "primary": "#232526", "secondary": "#414345"},
    "telegram": {"label": "Telegram", "primary": "#2aabee", "secondary": "#229ed9"},
    "lime": {"label": "Lime", "primary": "#a8ff78", "secondary": "#78ffd6"},
    "berry": {"label": "Berry", "primary": "#8360c3", "secondary": "#2ebf91"},
    "manual": {"label": "Manual", "primary": "#2336ff", "secondary": "#8a2be2"},
}

WATERMARK_POSITIONS: dict[str, str] = {
    "top_left": "Top Left",
    "top_center": "Top Center",
    "top_right": "Top Right",
    "center_left": "Center Left",
    "center": "Center",
    "center_right": "Center Right",
    "bottom_left": "Bottom Left",
    "bottom_center": "Bottom Center",
    "bottom_right": "Bottom Right",
}

_RTL_RE = re.compile(r"[\u0600-\u06ff]")
_TOKEN_RE = re.compile(r"(\s+|[^\s]+)", re.UNICODE)


def default_branding_settings() -> dict[str, Any]:
    return {
        "card_enabled": False,
        "branding_text": "Market Bot",
        "branding_channel_id": "",
        "watermark_text": "@market",
        "logo_enabled": False,
        "logo_path": "",
        "text_opacity": 220,
        "watermark_position": "bottom_right",
        "branding_position": "top_left",
        "price_position": "center_left",
        "card_theme": "glass",
        "card_primary_color": "#2336ff",
        "card_secondary_color": "#8a2be2",
        "card_dark_mode": True,
        "persian_font": "vazir",
        "english_font": "inter",
        "persian_bold": False,
        "english_bold": False,
    }


def merge_branding_settings(data: dict[str, Any]) -> dict[str, Any]:
    market = data.setdefault("market", {}) if isinstance(data, dict) else {}
    if not isinstance(market, dict):
        market = {}
        data["market"] = market
    branding = market.get("card", {})
    if not isinstance(branding, dict):
        branding = {}
    for key, value in default_branding_settings().items():
        branding.setdefault(key, value)
    try:
        branding["text_opacity"] = max(40, min(int(branding.get("text_opacity") or 220), 255))
    except Exception:
        branding["text_opacity"] = 220
    branding["card_dark_mode"] = bool(branding.get("card_dark_mode", True))
    branding["card_enabled"] = bool(branding.get("card_enabled", False))
    branding["logo_enabled"] = bool(branding.get("logo_enabled", False))
    branding["persian_bold"] = bool(branding.get("persian_bold", False))
    branding["english_bold"] = bool(branding.get("english_bold", False))
    if branding.get("persian_font") not in PERSIAN_FONT_CHOICES:
        branding["persian_font"] = "vazir"
    if branding.get("english_font") not in ENGLISH_FONT_CHOICES:
        branding["english_font"] = "inter"
    for position_field, fallback in {"watermark_position": "bottom_right", "branding_position": "top_left", "price_position": "center_left"}.items():
        if branding.get(position_field) not in WATERMARK_POSITIONS:
            branding[position_field] = fallback
    if branding.get("card_theme") not in CARD_THEMES:
        branding["card_theme"] = "glass"
    market["card"] = branding
    return branding


def _hex_to_rgb(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    raw = str(value or "").strip().lstrip("#")
    if len(raw) != 6:
        return fallback
    try:
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def _font_path(choice: dict[str, str], bold: bool) -> str:
    path = choice.get("bold" if bold else "regular") or _DEFAULT_REGULAR
    return path if Path(path).exists() else _DEFAULT_REGULAR


def _load_font(font_mod, path: str, size: int):
    try:
        return font_mod.truetype(path, size=size)
    except Exception:
        return font_mod.load_default(size=size)


def _is_rtl(text: str) -> bool:
    return bool(_RTL_RE.search(str(text or "")))


def shape_rtl_text(text: str) -> str:
    raw = str(text or "")
    if not _is_rtl(raw):
        return raw
    if importlib.util.find_spec("arabic_reshaper") and importlib.util.find_spec("bidi") and importlib.util.find_spec("bidi.algorithm"):
        reshaper = importlib.import_module("arabic_reshaper")
        bidi_algorithm = importlib.import_module("bidi.algorithm")
        return bidi_algorithm.get_display(reshaper.reshape(raw))
    # Safe fallback for environments without shaping libraries: reverse RTL word
    # order while keeping Latin/number tokens readable. This is not as complete as
    # arabic_reshaper + python-bidi but prevents left-to-right Persian output.
    shaped_lines: list[str] = []
    for line in raw.splitlines():
        if _is_rtl(line):
            tokens = [t for t in _TOKEN_RE.findall(line) if t]
            shaped_lines.append("".join(reversed(tokens)))
        else:
            shaped_lines.append(line)
    return "\n".join(shaped_lines)


def _display_text(text: str) -> str:
    return shape_rtl_text(text) if _is_rtl(text) else str(text or "")


def _line_wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if not raw_line:
            lines.append("")
            continue
        current = ""
        for word in raw_line.split():
            candidate = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), _display_text(candidate), font=font)
            if bbox[2] - bbox[0] <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _draw_text(draw, xy: tuple[int, int], text: str, font, fill, *, anchor: str | None = None) -> None:
    draw.text(xy, _display_text(text), font=font, fill=fill, anchor=anchor)


def _watermark_xy(position: str, width: int, height: int, tw: int, th: int, margin_x: int = 105, margin_y: int = 105) -> tuple[int, int]:
    y_map = {"top": margin_y, "center": (height - th) // 2, "bottom": height - th - 145}
    x_map = {"left": margin_x, "center": (width - tw) // 2, "right": width - tw - margin_x}
    if position == "center":
        return x_map["center"], y_map["center"]
    vertical, _, horizontal = position.partition("_")
    return x_map.get(horizontal, x_map["right"]), y_map.get(vertical, y_map["bottom"])


def render_market_card(response_text: str, branding: dict[str, Any]) -> bytes:
    branding = dict(branding or {})
    cache_key = hashlib.sha256((response_text + json.dumps(branding, sort_keys=True, ensure_ascii=False)).encode("utf-8")).hexdigest()
    now = time.time()
    cached = _CARD_CACHE.get(cache_key)
    if cached and now - cached[0] <= _CARD_CACHE_TTL_SECONDS:
        return cached[1]
    image_mod = importlib.import_module("PIL.Image")
    draw_mod = importlib.import_module("PIL.ImageDraw")
    font_mod = importlib.import_module("PIL.ImageFont")

    theme = CARD_THEMES.get(str(branding.get("card_theme") or "glass"), CARD_THEMES["glass"])
    width, height = 1080, 1080
    primary = _hex_to_rgb(branding.get("card_primary_color"), _hex_to_rgb(theme["primary"], (35, 54, 255)))
    secondary = _hex_to_rgb(branding.get("card_secondary_color"), _hex_to_rgb(theme["secondary"], (138, 43, 226)))
    dark = bool(branding.get("card_dark_mode", theme.get("dark", True)))
    base = image_mod.new("RGB", (width, height), (8, 11, 25) if dark else (245, 247, 255))
    px = base.load()
    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(primary[0] * (1 - ratio) + secondary[0] * ratio)
        g = int(primary[1] * (1 - ratio) + secondary[1] * ratio)
        b = int(primary[2] * (1 - ratio) + secondary[2] * ratio)
        for x in range(width):
            vignette = 1 - (abs(x - width / 2) / width) * 0.35
            px[x, y] = (int(r * vignette), int(g * vignette), int(b * vignette))

    overlay = image_mod.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = draw_mod.Draw(overlay)
    panel = (255, 255, 255, 38) if dark else (255, 255, 255, 205)
    draw.rounded_rectangle((70, 110, width - 70, height - 110), radius=48, fill=panel, outline=(255, 255, 255, 85), width=2)
    base = image_mod.alpha_composite(base.convert("RGBA"), overlay)
    draw = draw_mod.Draw(base)

    persian_choice = PERSIAN_FONT_CHOICES.get(str(branding.get("persian_font") or "vazir"), PERSIAN_FONT_CHOICES["vazir"])
    english_choice = ENGLISH_FONT_CHOICES.get(str(branding.get("english_font") or "inter"), ENGLISH_FONT_CHOICES["inter"])
    persian_path = _font_path(persian_choice, bool(branding.get("persian_bold", False)))
    english_path = _font_path(english_choice, bool(branding.get("english_bold", False)))
    title_font = _load_font(font_mod, english_path, 56)
    body_font_en = _load_font(font_mod, english_path, 40)
    body_font_fa = _load_font(font_mod, persian_path, 40)
    small_font = _load_font(font_mod, english_path, 30)
    watermark_font = _load_font(font_mod, persian_path if _is_rtl(str(branding.get("watermark_text") or "")) else english_path, 30)
    fill = (255, 255, 255, int(branding.get("text_opacity", 220))) if dark else (28, 33, 55, int(branding.get("text_opacity", 220)))
    muted = (220, 225, 255, 190) if dark else (80, 87, 120, 190)

    branding_text = str(branding.get("branding_text") or "Market Bot")[:64]
    branding_font = title_font if not _is_rtl(branding_text) else _load_font(font_mod, persian_path, 56)
    branding_display = _display_text(branding_text)
    branding_bbox = draw.textbbox((0, 0), branding_display, font=branding_font)
    branding_x, branding_y = _watermark_xy(str(branding.get("branding_position") or "top_left"), width, height, branding_bbox[2] - branding_bbox[0], 110, margin_y=145)
    _draw_text(draw, (branding_x, branding_y), branding_text, branding_font, fill)
    _draw_text(draw, (branding_x, branding_y + 70), "Dynamic Market & Conversion Engine", small_font, muted)
    logo_path = Path(str(branding.get("logo_path") or ""))
    if branding.get("logo_enabled", False) and logo_path.exists():
        logo = image_mod.open(logo_path).convert("RGBA").resize((112, 112))
        base.alpha_composite(logo, (width - 220, 140))

    wrapped_lines = _line_wrap(draw, response_text, body_font_fa if _is_rtl(response_text) else body_font_en, width - 210)[:13]
    line_metrics = []
    max_line_width = 0
    total_height = 0
    for line in wrapped_lines:
        font = body_font_fa if _is_rtl(line) else body_font_en
        bbox = draw.textbbox((0, 0), _display_text(line), font=font)
        line_width = bbox[2] - bbox[0]
        line_height = 58 if line else 34
        line_metrics.append((line, font, line_width, line_height))
        max_line_width = max(max_line_width, line_width)
        total_height += line_height
    x, y = _watermark_xy(str(branding.get("price_position") or "center_left"), width, height, max_line_width, total_height, margin_y=310)
    for line, font, _, line_height in line_metrics:
        _draw_text(draw, (x, y), line, font, fill)
        y += line_height

    watermark = str(branding.get("watermark_text") or "")[:64]
    if watermark:
        pos = str(branding.get("watermark_position") or "bottom_right")
        display_watermark = _display_text(watermark)
        bbox = draw.textbbox((0, 0), display_watermark, font=watermark_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x, y = _watermark_xy(pos, width, height, tw, th)
        draw.text((x, y), display_watermark, font=watermark_font, fill=muted)

    out = BytesIO()
    base.convert("RGB").save(out, format="PNG", optimize=True)
    data = out.getvalue()
    if len(_CARD_CACHE) > 64:
        _CARD_CACHE.clear()
    _CARD_CACHE[cache_key] = (now, data)
    return data
