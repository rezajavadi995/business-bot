import unittest

from features.market_cards import (
    CARD_THEMES,
    COLOR_PALETTES,
    ENGLISH_FONT_CHOICES,
    PERSIAN_FONT_CHOICES,
    WATERMARK_POSITIONS,
    merge_branding_settings,
    render_market_card,
    shape_rtl_text,
)


class MarketCardBrandingTests(unittest.TestCase):
    def test_branding_defaults_include_phase_c_controls(self):
        data = {}
        branding = merge_branding_settings(data)
        self.assertIn(branding["persian_font"], PERSIAN_FONT_CHOICES)
        self.assertIn(branding["english_font"], ENGLISH_FONT_CHOICES)
        self.assertIn(branding["watermark_position"], WATERMARK_POSITIONS)
        self.assertGreaterEqual(len(PERSIAN_FONT_CHOICES), 10)
        self.assertGreaterEqual(len(ENGLISH_FONT_CHOICES), 10)
        self.assertGreaterEqual(len(CARD_THEMES), 10)
        self.assertGreaterEqual(len(COLOR_PALETTES), 15)

    def test_persian_text_rendering_generates_card(self):
        data = {"market": {"card": {"persian_bold": True, "english_bold": True, "watermark_position": "center"}}}
        branding = merge_branding_settings(data)
        image = render_market_card("قیمت بیت‌کوین\n💵 70,000 USD\n✨ تبدیل 100 ترون", branding)
        self.assertGreater(len(image), 1000)
        self.assertTrue(image.startswith(b"\x89PNG"))

    def test_rtl_fallback_changes_persian_display_order(self):
        shaped = shape_rtl_text("قیمت بیت کوین")
        self.assertNotEqual(shaped, "قیمت بیت کوین")


if __name__ == "__main__":
    unittest.main()
