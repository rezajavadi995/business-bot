import time
import unittest

from features.market_engine import (
    merge_market_settings,
    parse_market_intent,
    render_market_response,
)


class MarketEngineParserTests(unittest.TestCase):
    def test_required_conversion_examples_parse(self):
        cases = {
            "۲۰۰۰ استارز": (2000.0, "stars", None),
            "2000 stars": (2000.0, "stars", None),
            "۲۰۰۰استارز": (2000.0, "stars", None),
            "1000stars": (1000.0, "stars", None),
            "۲۰ ترون": (20.0, "trx", None),
            "20 trx": (20.0, "trx", None),
            "1200000 تومان تتر": (1200000.0, "irt", "usdt"),
            "100 ترون تومان": (100.0, "trx", "irt"),
            "100 trx toman": (100.0, "trx", "irt"),
            "۱ دلار": (1.0, "usd", None),
            "100 usd trx": (100.0, "usd", "trx"),
            "۲۰۰۰ استارز ترون": (2000.0, "stars", "trx"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                intent = parse_market_intent(raw)
                self.assertIsNotNone(intent)
                self.assertEqual((intent.amount, intent.source, intent.target), expected)

    def test_price_and_status_examples_parse(self):
        self.assertEqual(parse_market_intent("btc").kind, "price")
        self.assertEqual(parse_market_intent("price trx").query_asset, "trx")
        self.assertEqual(parse_market_intent("eth price").query_asset, "eth")
        self.assertEqual(parse_market_intent("trx status").kind, "status")
        self.assertEqual(parse_market_intent("btc today").kind, "status")
        self.assertEqual(parse_market_intent("btc dominance").kind, "dominance")
        self.assertEqual(parse_market_intent("trend").kind, "trending")


class MarketEngineRenderTests(unittest.TestCase):
    def setUp(self):
        data = {}
        self.settings = merge_market_settings(data)
        self.cache = {
            "updated_at": int(time.time()),
            "rates_usd": {
                "usd": 1.0,
                "eur": 1.1,
                "irt": 0.00002,
                "trx": 0.12,
                "ton": 3.0,
                "stars": 0.03,
                "usdt": 1.0,
                "btc": 70000.0,
                "eth": 3500.0,
            },
            "meta": {"crypto": {"24h_change": {"trx": 2.5}}},
        }

    def test_conversion_uses_cache_only(self):
        text = render_market_response("100 usd trx", self.settings, self.cache)
        self.assertIn("833.333 TRX", text)

    def test_price_uses_cached_change(self):
        text = render_market_response("trx status", self.settings, self.cache)
        self.assertIn("24h: +2.50%", text)

    def test_stale_cache_fails_safely(self):
        stale = dict(self.cache, updated_at=1)
        text = render_market_response("100 usd trx", self.settings, stale)
        self.assertIn("نرخ معتبر", text)


if __name__ == "__main__":
    unittest.main()
