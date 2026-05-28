from __future__ import annotations

import importlib
from io import BytesIO
from pathlib import Path
from typing import Any


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
        "card_theme": "glass",
        "card_primary_color": "#2336ff",
        "card_secondary_color": "#8a2be2",
        "card_dark_mode": True,
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


def _line_wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if not raw_line:
            lines.append("")
            continue
        current = ""
        for word in raw_line.split():
            candidate = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def render_market_card(response_text: str, branding: dict[str, Any]) -> bytes:
    image_mod = importlib.import_module("PIL.Image")
    draw_mod = importlib.import_module("PIL.ImageDraw")
    font_mod = importlib.import_module("PIL.ImageFont")

    width, height = 1080, 1080
    primary = _hex_to_rgb(branding.get("card_primary_color"), (35, 54, 255))
    secondary = _hex_to_rgb(branding.get("card_secondary_color"), (138, 43, 226))
    dark = bool(branding.get("card_dark_mode", True))
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

    font_large = font_mod.load_default(size=56)
    font_body = font_mod.load_default(size=40)
    font_small = font_mod.load_default(size=30)
    fill = (255, 255, 255, int(branding.get("text_opacity", 220))) if dark else (28, 33, 55, int(branding.get("text_opacity", 220)))
    muted = (220, 225, 255, 190) if dark else (80, 87, 120, 190)

    branding_text = str(branding.get("branding_text") or "Market Bot")[:64]
    draw.text((105, 145), branding_text, font=font_large, fill=fill)
    draw.text((105, 215), "Dynamic Market & Conversion Engine", font=font_small, fill=muted)
    logo_path = Path(str(branding.get("logo_path") or ""))
    if branding.get("logo_enabled", False) and logo_path.exists():
        logo = image_mod.open(logo_path).convert("RGBA").resize((112, 112))
        base.alpha_composite(logo, (width - 220, 140))

    y = 310
    for line in _line_wrap(draw, response_text, font_body, width - 210)[:13]:
        draw.text((105, y), line, font=font_body, fill=fill)
        y += 58 if line else 34

    watermark = str(branding.get("watermark_text") or "")[:64]
    if watermark:
        pos = str(branding.get("watermark_position") or "bottom_right")
        bbox = draw.textbbox((0, 0), watermark, font=font_small)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = width - tw - 105 if pos.endswith("right") else 105
        y = height - th - 145 if pos.startswith("bottom") else 105
        draw.text((x, y), watermark, font=font_small, fill=muted)

    out = BytesIO()
    base.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
