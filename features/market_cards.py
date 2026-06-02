from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import re
import math
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


def _font_key(value: str) -> str:
    return str(value or "").lower().replace(" ", "").replace("-", "_")


def _first_existing_font(*names: str, fallback: str = _DEFAULT_REGULAR) -> str:
    roots = [Path("/usr/share/fonts"), Path.home() / ".local/share/fonts"]
    lowered = [_font_key(name) for name in names]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.ttf"):
            stem = _font_key(path.name)
            if any(name and name in stem for name in lowered):
                return str(path)
    return fallback

PERSIAN_FONT_CHOICES: dict[str, dict[str, str]] = {
    "vazir": {"label": "Vazir", "regular": _first_existing_font("vazir"), "bold": _first_existing_font("vazir_bold", "vazir-bold", fallback=_DEFAULT_BOLD)},
    "shabnam": {"label": "Shabnam", "regular": _first_existing_font("shabnam", fallback=_DEFAULT_SERIF), "bold": _first_existing_font("shabnam_bold", "shabnam-bold", fallback=_DEFAULT_SERIF_BOLD)},
    "sahel": {"label": "Sahel", "regular": _first_existing_font("sahel", fallback=_DEFAULT_MONO), "bold": _first_existing_font("sahel_bold", "sahel-bold", fallback=_DEFAULT_MONO_BOLD)},
    "samim": {"label": "Samim", "regular": _first_existing_font("samim", fallback=_DEFAULT_BOLD), "bold": _first_existing_font("samim_bold", "samim-bold", fallback=_DEFAULT_BOLD)},
    "yekan": {"label": "Yekan", "regular": _first_existing_font("yekan", "b_yekan", fallback=_DEFAULT_SERIF_BOLD), "bold": _first_existing_font("yekan_bold", "b_yekan_bold", fallback=_DEFAULT_SERIF_BOLD)},
    "iransans": {"label": "IRANSans", "regular": _first_existing_font("iransans", "iran_sans", fallback=_DEFAULT_REGULAR), "bold": _first_existing_font("iransans_bold", "iran_sans_bold", fallback=_DEFAULT_BOLD)},
    "estedad": {"label": "Estedad", "regular": _first_existing_font("estedad", fallback=_DEFAULT_SERIF), "bold": _first_existing_font("estedad_bold", "estedad-bold", fallback=_DEFAULT_SERIF_BOLD)},
    "nahid": {"label": "Nahid", "regular": _first_existing_font("nahid", fallback=_DEFAULT_REGULAR), "bold": _first_existing_font("nahid", fallback=_DEFAULT_BOLD)},
    "parastoo": {"label": "Parastoo", "regular": _first_existing_font("parastoo", fallback=_DEFAULT_REGULAR), "bold": _first_existing_font("parastoo_bold", "parastoo-bold", fallback=_DEFAULT_BOLD)},
    "tanha": {"label": "Tanha", "regular": _first_existing_font("tanha", fallback=_DEFAULT_MONO), "bold": _first_existing_font("tanha_bold", "tanha-bold", fallback=_DEFAULT_MONO_BOLD)},
}

ENGLISH_FONT_CHOICES: dict[str, dict[str, str]] = {
    "inter": {"label": "Inter", "regular": _first_existing_font("inter"), "bold": _first_existing_font("inter_bold", "inter-bold", fallback=_DEFAULT_BOLD)},
    "roboto": {"label": "Roboto", "regular": _first_existing_font("roboto", fallback=_DEFAULT_SERIF), "bold": _first_existing_font("roboto_bold", "roboto-bold", fallback=_DEFAULT_SERIF_BOLD)},
    "open_sans": {"label": "Open Sans", "regular": _first_existing_font("opensans", "open_sans", fallback=_DEFAULT_MONO), "bold": _first_existing_font("opensans_bold", "open_sans_bold", fallback=_DEFAULT_MONO_BOLD)},
    "montserrat": {"label": "Montserrat", "regular": _first_existing_font("montserrat", fallback=_DEFAULT_SERIF), "bold": _first_existing_font("montserrat_bold", fallback=_DEFAULT_SERIF_BOLD)},
    "lato": {"label": "Lato", "regular": _first_existing_font("lato", fallback=_DEFAULT_SERIF), "bold": _first_existing_font("lato_bold", fallback=_DEFAULT_SERIF_BOLD)},
    "poppins": {"label": "Poppins", "regular": _first_existing_font("poppins", fallback=_DEFAULT_MONO), "bold": _first_existing_font("poppins_bold", fallback=_DEFAULT_MONO_BOLD)},
    "nunito": {"label": "Nunito", "regular": _first_existing_font("nunito", fallback=_DEFAULT_REGULAR), "bold": _first_existing_font("nunito_bold", fallback=_DEFAULT_BOLD)},
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

_ARABIC_FORMS: dict[str, tuple[str, str, str, str, bool]] = {
    "ا": ("ﺍ", "ﺎ", "ﺍ", "ﺎ", False), "آ": ("ﺁ", "ﺂ", "ﺁ", "ﺂ", False), "أ": ("ﺃ", "ﺄ", "ﺃ", "ﺄ", False), "إ": ("ﺇ", "ﺈ", "ﺇ", "ﺈ", False),
    "د": ("ﺩ", "ﺪ", "ﺩ", "ﺪ", False), "ذ": ("ﺫ", "ﺬ", "ﺫ", "ﺬ", False), "ر": ("ﺭ", "ﺮ", "ﺭ", "ﺮ", False), "ز": ("ﺯ", "ﺰ", "ﺯ", "ﺰ", False), "ژ": ("ﮊ", "ﮋ", "ﮊ", "ﮋ", False), "و": ("ﻭ", "ﻮ", "ﻭ", "ﻮ", False), "ؤ": ("ﺅ", "ﺆ", "ﺅ", "ﺆ", False),
    "ب": ("ﺏ", "ﺐ", "ﺑ", "ﺒ", True), "پ": ("ﭖ", "ﭗ", "ﭘ", "ﭙ", True), "ت": ("ﺕ", "ﺖ", "ﺗ", "ﺘ", True), "ث": ("ﺙ", "ﺚ", "ﺛ", "ﺜ", True),
    "ج": ("ﺝ", "ﺞ", "ﺟ", "ﺠ", True), "چ": ("ﭺ", "ﭻ", "ﭼ", "ﭽ", True), "ح": ("ﺡ", "ﺢ", "ﺣ", "ﺤ", True), "خ": ("ﺥ", "ﺦ", "ﺧ", "ﺨ", True),
    "س": ("ﺱ", "ﺲ", "ﺳ", "ﺴ", True), "ش": ("ﺵ", "ﺶ", "ﺷ", "ﺸ", True), "ص": ("ﺹ", "ﺺ", "ﺻ", "ﺼ", True), "ض": ("ﺽ", "ﺾ", "ﺿ", "ﻀ", True),
    "ط": ("ﻁ", "ﻂ", "ﻃ", "ﻄ", True), "ظ": ("ﻅ", "ﻆ", "ﻇ", "ﻈ", True), "ع": ("ﻉ", "ﻊ", "ﻋ", "ﻌ", True), "غ": ("ﻍ", "ﻎ", "ﻏ", "ﻐ", True),
    "ف": ("ﻑ", "ﻒ", "ﻓ", "ﻔ", True), "ق": ("ﻕ", "ﻖ", "ﻗ", "ﻘ", True), "ک": ("ﮎ", "ﮏ", "ﮐ", "ﮑ", True), "ك": ("ﻙ", "ﻚ", "ﻛ", "ﻜ", True), "گ": ("ﮒ", "ﮓ", "ﮔ", "ﮕ", True),
    "ل": ("ﻝ", "ﻞ", "ﻟ", "ﻠ", True), "م": ("ﻡ", "ﻢ", "ﻣ", "ﻤ", True), "ن": ("ﻥ", "ﻦ", "ﻧ", "ﻨ", True), "ه": ("ﻩ", "ﻪ", "ﻫ", "ﻬ", True),
    "ی": ("ﯼ", "ﯽ", "ﯾ", "ﯿ", True), "ي": ("ﻱ", "ﻲ", "ﻳ", "ﻴ", True), "ئ": ("ﺉ", "ﺊ", "ﺋ", "ﺌ", True),
}


def _can_connect_right(ch: str) -> bool:
    return ch in _ARABIC_FORMS


def _can_connect_left(ch: str) -> bool:
    return bool(_ARABIC_FORMS.get(ch, ("", "", "", "", False))[4])


def _shape_rtl_word(word: str) -> str:
    chars = list(word)
    shaped: list[str] = []
    for idx, ch in enumerate(chars):
        forms = _ARABIC_FORMS.get(ch)
        if not forms:
            shaped.append(ch)
            continue
        prev_ch = chars[idx - 1] if idx else ""
        next_ch = chars[idx + 1] if idx + 1 < len(chars) else ""
        join_prev = _can_connect_left(prev_ch) and _can_connect_right(ch)
        join_next = _can_connect_left(ch) and _can_connect_right(next_ch)
        isolated, final, initial, medial, _ = forms
        if join_prev and join_next:
            shaped.append(medial)
        elif join_prev:
            shaped.append(final)
        elif join_next:
            shaped.append(initial)
        else:
            shaped.append(isolated)
    return "".join(reversed(shaped))


def _fallback_shape_rtl_line(line: str) -> str:
    tokens = [t for t in _TOKEN_RE.findall(line) if t]
    out: list[str] = []
    for token in reversed(tokens):
        if _is_rtl(token):
            out.append(_shape_rtl_word(token))
        else:
            out.append(token)
    return "".join(out)


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
        "card_style": "classic",
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
    if branding.get("card_style") not in {"classic", "advanced"}:
        branding["card_style"] = "classic"
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
    # Safe fallback for environments without arabic_reshaper + python-bidi:
    # use Arabic presentation forms and visual RTL order so Persian words stay
    # readable in Pillow builds without libraqm.
    shaped_lines: list[str] = []
    for line in raw.splitlines():
        shaped_lines.append(_fallback_shape_rtl_line(line) if _is_rtl(line) else line)
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


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", str(text or ""))


def _image_text(text: str) -> str:
    replacements = {"💸": "IRT", "💵": "USD", "🪙": "TIME", "🕘": "TIME", "✨": "", "🔄": "", "🔁": "", "🔺": "TRX", "🟢": "+", "🔴": "-", "📊": "CHART", "📈": "HIGH", "📉": "LOW", "🇮🇷": "IRT", "💰": "PRICE"}
    clean = _strip_html(text)
    for emoji, label in replacements.items():
        clean = clean.replace(emoji, label)
    return clean


def _draw_text(draw, xy: tuple[int, int], text: str, font, fill, *, anchor: str | None = None) -> None:
    draw.text(xy, _display_text(_image_text(text)), font=font, fill=fill, anchor=anchor)


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
    if branding.get("card_style") == "advanced":
        data = render_advanced_market_card(response_text, branding)
        if len(_CARD_CACHE) > 64:
            _CARD_CACHE.clear()
        _CARD_CACHE[cache_key] = (now, data)
        return data

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

    if branding.get("card_style") == "advanced":
        return render_advanced_market_card(response_text, branding)

    branding_text = str(branding.get("branding_text") or "Market Bot")[:64]
    branding_font = title_font if not _is_rtl(branding_text) else _load_font(font_mod, persian_path, 56)
    branding_display = _display_text(branding_text)
    branding_bbox = draw.textbbox((0, 0), branding_display, font=branding_font)
    branding_x, branding_y = _watermark_xy(str(branding.get("branding_position") or "top_left"), width, height, branding_bbox[2] - branding_bbox[0], 110, margin_y=145)
    _draw_text(draw, (branding_x, branding_y), branding_text, branding_font, fill)
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
    base.convert("RGB").save(out, format="PNG", compress_level=4)
    data = out.getvalue()
    if len(_CARD_CACHE) > 64:
        _CARD_CACHE.clear()
    _CARD_CACHE[cache_key] = (now, data)
    return data


_ASSET_THEME_COLORS: dict[str, tuple[str, str]] = {
    "TRX": ("#ff1744", "#ff6b86"),
    "USDT": ("#12b886", "#71e6c1"),
    "TON": ("#0098ea", "#73d2ff"),
    "BTC": ("#f7931a", "#ffd166"),
    "ETH": ("#627eea", "#a8b8ff"),
    "USD": ("#4776e6", "#8e54e9"),
    "EUR": ("#2f80ed", "#56ccf2"),
    "TRY": ("#ef476f", "#ffd166"),
    "RUB": ("#4361ee", "#4cc9f0"),
    "STARS": ("#f6c453", "#f7971e"),
}
_SYMBOL_ALIASES = {
    "ترون": "TRX", "لیر": "TRY", "دلار": "USD", "یورو": "EUR", "روبل": "RUB", "تتر": "USDT", "بیت": "BTC", "اتریوم": "ETH", "استارز": "STARS",
}


def _extract_card_facts(response_text: str) -> dict[str, Any]:
    plain = re.sub(r"\n{3,}", "\n\n", _strip_html(response_text)).strip()
    upper = plain.upper()
    symbol = next((asset for asset in _ASSET_THEME_COLORS if re.search(rf"\b{asset}\b", upper)), "")
    if not symbol:
        symbol = next((asset for word, asset in _SYMBOL_ALIASES.items() if word in plain), "USDT")
    usd_match = re.search(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", plain)
    toman_match = re.search(r"(?:تومان|IRT|تومانی)\s*:?\s*([0-9][0-9,]*(?:\.\d+)?)|([0-9][0-9,]*(?:\.\d+)?)\s*(?:تومان|toman)", plain, re.IGNORECASE)
    pct_match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", plain)
    high_low_match = re.search(r"([0-9][0-9,]*(?:\.\d+)?)\s*/\s*([0-9][0-9,]*(?:\.\d+)?)\s*تومان", plain)
    result_match = re.search(r"🔁\s*([^\n]+)", plain)
    title = next((line.strip() for line in plain.splitlines() if line.strip()), f"قیمت {symbol}")
    return {
        "symbol": symbol,
        "title": title.replace("🔺", "").replace("💵", "").strip(),
        "usd": usd_match.group(1) if usd_match else "1",
        "toman": ((toman_match.group(1) or toman_match.group(2)) if toman_match else "-"),
        "change": float(pct_match.group(1)) if pct_match else 0.0,
        "high_low": high_low_match.groups() if high_low_match else None,
        "result": result_match.group(1).strip() if result_match else "",
    }


def _draw_badge(draw, xy: tuple[int, int], text: str, font, fill: tuple[int, int, int, int], bg: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0] + 32
    h = bbox[3] - bbox[1] + 20
    draw.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=bg)
    draw.text((x + 16, y + 9), text, font=font, fill=fill)
    return (x, y, x + w, y + h)


"""def _fit_font(font_mod, path: str, text: str, max_width: int, start_size: int, min_size: int = 22):
    size = start_size
    while size > min_size:
        font = _load_font(font_mod, path, size)
        try:
            bbox = font.getbbox(text)
            if bbox[2] - bbox[0] <= max_width:
                return font
        except Exception:
            return font
        size -= 2
    return _load_font(font_mod, path, min_size)


def _ellipsize_to_width(draw, text: str, font, max_width: int) -> str:
    display = _display_text(_image_text(text))
    if draw.textbbox((0, 0), display, font=font)[2] <= max_width:
        return display
    raw = _image_text(text)
    while len(raw) > 4:
        raw = raw[:-2].rstrip()
        display = _display_text(raw + "…")
        if draw.textbbox((0, 0), display, font=font)[2] <= max_width:
            return display
    return _display_text(raw)
    """


def _positioned_box(position: str, canvas: tuple[int, int], size: tuple[int, int], margin: tuple[int, int] = (72, 62)) -> tuple[int, int]:
    width, height = canvas
    box_w, box_h = size
    margin_x, margin_y = margin
    x_map = {"left": margin_x, "center": (width - box_w) // 2, "right": width - box_w - margin_x}
    y_map = {"top": margin_y, "center": (height - box_h) // 2, "bottom": height - box_h - margin_y}
    if position == "center":
        return x_map["center"], y_map["center"]
    vertical, _, horizontal = str(position or "").partition("_")
    return x_map.get(horizontal, x_map["center"]), y_map.get(vertical, y_map["center"])


def _advanced_background(image_mod, width: int, height: int, primary: tuple[int, int, int], secondary: tuple[int, int, int]):
    base = image_mod.new("RGB", (width, height), primary)
    px = base.load()
    for x in range(width):
        ratio = x / max(width - 1, 1)
        r = int(primary[0] * (1 - ratio) + secondary[0] * ratio)
        g = int(primary[1] * (1 - ratio) + secondary[1] * ratio)
        b = int(primary[2] * (1 - ratio) + secondary[2] * ratio)
        for y in range(height):
            center = 1 - min(0.28, (((x - width / 2) ** 2 + (y - height / 2) ** 2) ** 0.5 / width) * 0.22)
            soft = 0.92 + 0.08 * math.sin((x + y) / 210)
            px[x, y] = (int(r * center * soft), int(g * center * soft), int(b * center * soft))
    return base.convert("RGBA")


def render_advanced_market_card(response_text: str, branding: dict[str, Any]) -> bytes:
    image_mod = importlib.import_module("PIL.Image")
    draw_mod = importlib.import_module("PIL.ImageDraw")
    font_mod = importlib.import_module("PIL.ImageFont")
    facts = _extract_card_facts(response_text)
    theme = CARD_THEMES.get(str(branding.get("card_theme") or "glass"), CARD_THEMES["glass"])
    theme_primary = _hex_to_rgb(branding.get("card_primary_color") or theme["primary"], _hex_to_rgb(theme["primary"], (35, 54, 255)))
    theme_secondary = _hex_to_rgb(branding.get("card_secondary_color") or theme["secondary"], _hex_to_rgb(theme["secondary"], (138, 43, 226)))
    accent = _hex_to_rgb(_ASSET_THEME_COLORS.get(facts["symbol"], (theme["primary"], theme["secondary"]))[0], theme_primary)
    width, height = 1080, 900
    text_alpha = max(90, min(int(branding.get("text_opacity") or 220), 255))
    base = _advanced_background(image_mod, width, height, theme_primary, theme_secondary)
    draw = draw_mod.Draw(base)

    english_choice = ENGLISH_FONT_CHOICES.get(str(branding.get("english_font") or "inter"), ENGLISH_FONT_CHOICES["inter"])
    persian_choice = PERSIAN_FONT_CHOICES.get(str(branding.get("persian_font") or "vazir"), PERSIAN_FONT_CHOICES["vazir"])
    en_bold = _load_font(font_mod, _font_path(english_choice, True), 58)
    en = _load_font(font_mod, _font_path(english_choice, False), 32)
    en_small = _load_font(font_mod, _font_path(english_choice, False), 24)
    fa_bold = _load_font(font_mod, _font_path(persian_choice, True), 42)
    fa = _load_font(font_mod, _font_path(persian_choice, False), 31)
    fa_small = _load_font(font_mod, _font_path(persian_choice, False), 24)

    logo_slot = (width - 192, 46, width - 74, 164)
    brand = str(branding.get("branding_text") or "Market Bot")[:36]
    brand_font = fa_bold if _is_rtl(brand) else en_bold
    brand_bbox = draw.textbbox((0, 0), _display_text(brand), font=brand_font)
    brand_w = min(brand_bbox[2] - brand_bbox[0] + 38, 610)
    brand_h = 58
    brand_x, brand_y = _positioned_box(str(branding.get("branding_position") or "top_left"), (width, height), (brand_w, brand_h), (76, 38))
    if brand_x + brand_w > logo_slot[0] - 16 and brand_y < logo_slot[3] + 8:
        brand_x = 76
    draw.rounded_rectangle((brand_x, brand_y, brand_x + brand_w, brand_y + brand_h), radius=20, fill=(15, 21, 34, 105), outline=(255, 255, 255, 74), width=1)
    _draw_text(draw, (brand_x + 18, brand_y + 9), brand, brand_font, (255, 255, 255, text_alpha))

    logo_path = Path(str(branding.get("logo_path") or ""))
    if branding.get("logo_enabled", False) and logo_path.exists():
        try:
            logo = image_mod.open(logo_path).convert("RGBA")
            logo.thumbnail((92, 92))
            draw.rounded_rectangle(logo_slot, radius=26, fill=(255, 255, 255, 54), outline=(255, 255, 255, 95), width=1)
            base.alpha_composite(logo, (logo_slot[0] + (118 - logo.width) // 2, logo_slot[1] + (118 - logo.height) // 2))
        except Exception:
            pass

    panel_w, panel_h = 910, 540
    panel_x, panel_y = _positioned_box(str(branding.get("price_position") or "center"), (width, height), (panel_w, panel_h), (84, 132))
    panel_y = max(132, min(panel_y, height - panel_h - 130))
    draw.rounded_rectangle((panel_x + 10, panel_y + 14, panel_x + panel_w + 10, panel_y + panel_h + 14), radius=46, fill=(12, 16, 26, 65))
    draw.rounded_rectangle((panel_x, panel_y, panel_x + panel_w, panel_y + panel_h), radius=46, fill=(255, 255, 255, 244), outline=(255, 255, 255, 180), width=2)

    dot = (panel_x + 48, panel_y + 48, panel_x + 100, panel_y + 100)
    draw.ellipse(dot, fill=accent + (255,))
    draw.text((panel_x + 122, panel_y + 32), facts["symbol"], font=en_bold, fill=(16, 20, 32, text_alpha))
    draw.text((panel_x + panel_w - 240, panel_y + 48), f"{facts['symbol']} / USD", font=en, fill=(130, 136, 148, 230))

    draw.text((panel_x + 62, panel_y + 130), f"${facts['usd']}", font=_load_font(font_mod, _font_path(english_choice, True), 82), fill=(8, 12, 24, text_alpha))
    change = facts["change"]
    change_color = (12, 180, 110, 255) if change >= 0 else (226, 64, 75, 255)
    draw.text((panel_x + 66, panel_y + 230), f"{change:+.2f}%", font=en_bold, fill=change_color)

    chart_left, chart_top = panel_x + 70, panel_y + 306
    chart_right, chart_bottom = panel_x + panel_w - 70, panel_y + panel_h - 136
    for i in range(5):
        y = chart_top + i * ((chart_bottom - chart_top) // 4)
        draw.line((chart_left, y, chart_right, y), fill=(218, 223, 231, 255), width=2)
    points = []
    seed = sum(ord(c) for c in facts["symbol"])
    for i in range(54):
        x = chart_left + int(i * (chart_right - chart_left) / 53)
        drift = (change / 100) * (i / 53) * -80
        wave = math.sin(i / 4 + seed) * 19 + math.sin(i / 8 + seed / 3) * 25
        y = int((chart_top + chart_bottom) / 2 + wave + drift)
        points.append((x, max(chart_top + 10, min(chart_bottom - 10, y))))
    for a, b in zip(points, points[1:]):
        draw.line((a, b), fill=change_color, width=5)
    for point in points[::8]:
        draw.ellipse((point[0] - 5, point[1] - 5, point[0] + 5, point[1] + 5), fill=change_color)

    info_w, info_h = panel_w - 120, 74
    info_x, info_y = panel_x + 60, panel_y + panel_h - 100
    draw.rounded_rectangle((info_x, info_y, info_x + info_w, info_y + info_h), radius=26, fill=(15, 21, 34, 235))
    _draw_badge(draw, (info_x + 28, info_y + 17), "IRT", en, (255, 255, 255, 255), (39, 150, 92, 255))
    draw.text((info_x + 118, info_y + 21), f"{facts['toman']}", font=en, fill=(255, 255, 255, text_alpha))
    _draw_badge(draw, (info_x + 430, info_y + 17), "USD", en, (255, 255, 255, 255), (74, 105, 164, 255))
    draw.text((info_x + 530, info_y + 21), f"${facts['usd']}", font=en, fill=(255, 255, 255, text_alpha))

    if facts.get("result") and not re.search(r"(?:تومان|toman|IRT)", str(facts.get("result")), re.IGNORECASE):
        result = facts["result"][:34]
        rb_w, rb_h = 520, 58
        rb_x, rb_y = panel_x + panel_w - rb_w - 42, panel_y + 140
        draw.rounded_rectangle((rb_x, rb_y, rb_x + rb_w, rb_y + rb_h), radius=20, fill=accent + (35,), outline=accent + (105,), width=2)
        draw.text((rb_x + 20, rb_y + 12), _display_text(_image_text(result)), font=fa if _is_rtl(result) else en, fill=(18, 24, 38, text_alpha))
    if facts.get("high_low"):
        high, low = facts["high_low"]
        text = f"High / Low  {high} / {low}"
        draw.text((panel_x + 62, panel_y + 286), text, font=en_small, fill=(102, 110, 128, 230))

    watermark = str(branding.get("watermark_text") or branding.get("branding_channel_id") or "")[:64]
    if watermark:
        wm_font = fa_small if _is_rtl(watermark) else en_small
        bbox = draw.textbbox((0, 0), _display_text(watermark), font=wm_font)
        wm_w, wm_h = bbox[2] - bbox[0] + 26, bbox[3] - bbox[1] + 18
        wm_x, wm_y = _positioned_box(str(branding.get("watermark_position") or "bottom_center"), (width, height), (wm_w, wm_h), (80, 34))
        draw.rounded_rectangle((wm_x, wm_y, wm_x + wm_w, wm_y + wm_h), radius=15, fill=(0, 0, 0, 38))
        _draw_text(draw, (wm_x + 13, wm_y + 7), watermark, wm_font, (255, 255, 255, min(text_alpha, 230)))

    out = BytesIO()
    base.convert("RGB").save(out, format="PNG", optimize=True)
    #base.convert("RGB").save(out, format="PNG", compress_level=4)
    return out.getvalue()
