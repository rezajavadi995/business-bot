import time
import unittest
from unittest.mock import Mock, patch

from features.market_engine import (
    cache_status,
    market_help_text,
    merge_market_settings,
    parse_market_intent,
    render_market_response,
    validate_market_api_key,
    stars_unit_usd,
    MarketRateService,
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

    def test_dollar_and_multiword_aliases_parse(self):
        self.assertEqual((parse_market_intent("100$ trx").source, parse_market_intent("100$ trx").target), ("usd", "trx"))
        self.assertEqual((parse_market_intent("$100 trx").source, parse_market_intent("$100 trx").target), ("usd", "trx"))
        self.assertEqual(parse_market_intent("قیمت بیت کوین").query_asset, "btc")
        self.assertEqual((parse_market_intent("100 بیت کوین تومان").source, parse_market_intent("100 بیت کوین تومان").target), ("btc", "irt"))


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
            "meta": {"crypto": {"24h_change": {"trx": 2.5}, "24h_high": {"trx": 0.13}, "24h_low": {"trx": 0.11}, "trending": [{"symbol": "BTC", "name": "Bitcoin"}], "top_gainers": [{"symbol": "TRX", "change": 2.5}], "dominance": {"btc": 52.3}}, "fear_greed": {"value": 70, "classification": "Greed", "timestamp": int(time.time())}},
        }

    def test_conversion_uses_cache_only(self):
        text = render_market_response("100 usd trx", self.settings, self.cache)
        self.assertIn("833.333 TRX", text)

    def test_price_uses_cached_change(self):
        text = render_market_response("trx status", self.settings, self.cache)
        self.assertIn("24h: +2.50%", text)
        self.assertIn("High", text)

    def test_extra_market_features_render_from_cache(self):
        self.assertIn("Trending", render_market_response("trend", self.settings, self.cache))
        self.assertIn("Top gainers", render_market_response("top gainers", self.settings, self.cache))
        self.assertIn("52.30%", render_market_response("btc dominance", self.settings, self.cache))
        self.assertIn("70/100", render_market_response("fear greed", self.settings, self.cache))

    def test_stale_cache_fails_safely(self):
        stale = dict(self.cache, updated_at=1)
        text = render_market_response("100 usd trx", self.settings, stale)
        self.assertIn("نرخ معتبر", text)


class MarketEngineAdminSupportTests(unittest.TestCase):
    def test_cache_status_reports_fresh_usable_cache(self):
        data = {}
        settings = merge_market_settings(data)
        status = cache_status({"updated_at": int(time.time()), "rates_usd": {"usd": 1.0}}, settings)
        self.assertTrue(status["fresh"])
        self.assertTrue(status["usable"])
        self.assertEqual(status["rate_count"], 1)

    def test_help_text_includes_admin_section_when_requested(self):
        text = market_help_text(is_admin=True)
        self.assertIn("Market API Configuration", text)
        self.assertIn("۲۰۰۰ استارز", text)

    def test_stars_manual_override_is_functional_when_auto_multiplier_is_off(self):
        settings = {"stars_unit_amount": 1000, "stars_unit_usd": 30, "stars_manual_override_usd": 45, "stars_auto_multiplier_enabled": False}
        self.assertEqual(stars_unit_usd(settings), 0.045)
        settings["stars_auto_multiplier_enabled"] = True
        self.assertEqual(stars_unit_usd(settings), 0.03)

    def test_fetch_rates_keeps_partial_provider_success(self):
        service = MarketRateService()
        service._fetch_coingecko = Mock(side_effect=RuntimeError("rate limited"))
        service._fetch_exchange_rates = Mock(return_value={"rates_usd": {"eur": 1.1, "irt": 0.00002}, "meta": {"source": "exchange"}})
        service._fetch_fear_greed = Mock(return_value={})
        payload = service._fetch_rates({"coingecko_enabled": True, "exchangerate_enabled": True, "stars_unit_amount": 1000, "stars_unit_usd": 30})
        self.assertIn("irt", payload["rates_usd"])
        self.assertIn("coingecko", payload["meta"]["provider_errors"])

    @patch("features.market_engine.requests.get")
    def test_validate_exchangerate_key_uses_real_validation_endpoint_shape(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"result": "success", "conversion_rate": 0.92}
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        result = validate_market_api_key("exchangerate", "abc123", timeout=3)

        self.assertTrue(result["ok"])
        self.assertIn("USD/EUR", result["message"])
        self.assertIn("/abc123/pair/USD/EUR", mock_get.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
