import tempfile
import unittest
from pathlib import Path

from PIL import Image

from features.market_cards import (
    CARD_THEMES,
    COLOR_PALETTES,
    ENGLISH_FONT_CHOICES,
    PERSIAN_FONT_CHOICES,
    WATERMARK_POSITIONS,
    merge_branding_settings,
    render_market_card,
    shape_rtl_text,
    _extract_card_facts,
)


class MarketCardBrandingTests(unittest.TestCase):
    def test_branding_defaults_include_phase_c_controls(self):
        data = {}
        branding = merge_branding_settings(data)
        self.assertIn(branding["persian_font"], PERSIAN_FONT_CHOICES)
        self.assertIn(branding["english_font"], ENGLISH_FONT_CHOICES)
        self.assertIn(branding["watermark_position"], WATERMARK_POSITIONS)
        self.assertIn(branding["branding_position"], WATERMARK_POSITIONS)
        self.assertIn(branding["price_position"], WATERMARK_POSITIONS)
        self.assertGreaterEqual(len(PERSIAN_FONT_CHOICES), 10)
        self.assertGreaterEqual(len(ENGLISH_FONT_CHOICES), 10)
        self.assertGreaterEqual(len(CARD_THEMES), 10)
        self.assertGreaterEqual(len(COLOR_PALETTES), 15)

    def test_persian_text_rendering_generates_card(self):
        data = {"market": {"card": {"persian_bold": True, "english_bold": True, "watermark_position": "center", "branding_position": "top_center", "price_position": "center"}}}
        branding = merge_branding_settings(data)
        image = render_market_card("قیمت بیت‌کوین\n💵 70,000 USD\n✨ تبدیل 100 ترون", branding)
        self.assertGreater(len(image), 1000)
        self.assertTrue(image.startswith(b"\x89PNG"))

    def test_advanced_card_respects_positions_theme_and_logo(self):
        with tempfile.TemporaryDirectory() as tmp:
            logo_path = Path(tmp) / "logo.png"
            Image.new("RGBA", (96, 96), (255, 255, 255, 255)).save(logo_path)
            data = {"market": {"card": {"card_style": "advanced", "card_theme": "emerald", "card_primary_color": CARD_THEMES["emerald"]["primary"], "card_secondary_color": CARD_THEMES["emerald"]["secondary"], "branding_position": "top_right", "watermark_position": "bottom_center", "price_position": "bottom_center", "logo_enabled": True, "logo_path": str(logo_path)}}}
            branding = merge_branding_settings(data)
            image = render_market_card("<b>🔺 قیمت TRX</b>\n💵 دلاری: <b>$0.3456</b>\n🇮🇷 تومانی: <b>59,042</b>\n🟢 رشد: <b>0.20%</b>", branding)
            self.assertGreater(len(image), 1000)
            self.assertTrue(image.startswith(b"\x89PNG"))


    def test_advanced_card_extracts_signed_24h_change_over_direction_percent(self):
        facts = _extract_card_facts("""<b>🔺 قیمت TRX</b>
📊 تغییرات روزانه
🔴 افت: <b>2.83%</b>
<code>24h: -2.83%</code>""")
        self.assertEqual(facts["symbol"], "TRX")
        self.assertAlmostEqual(facts["change"], -2.83)


    def test_advanced_card_extracts_negative_direction_from_conversion_line(self):
        facts = _extract_card_facts("""<b>🔺 تبدیل 12 TRX</b>
<blockquote>🔁 <b>690,000 تومان</b></blockquote>
📊 🔴 افت روزانه: <b>2.83%</b>
💵 دلار: <b>$4.004</b>""")
        self.assertAlmostEqual(facts["change"], -2.83)

    def test_rtl_fallback_changes_persian_display_order(self):
        shaped = shape_rtl_text("قیمت بیت کوین")
        self.assertNotEqual(shaped, "قیمت بیت کوین")


if __name__ == "__main__":
    unittest.main()
