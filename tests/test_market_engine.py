import asyncio
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
    CACHE_KEY,
    EXCHANGERATE_COOLDOWN_KEY,
    EXCHANGERATE_RUNTIME_STATE,
    PROVIDER_LOCKS,
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

    def test_strict_parser_rejects_natural_language_sentences(self):
        for raw in [
            "من امروز بیت خریدم",
            "من امروز btc خریدم",
            "please send dollar invoice",
            "این trx برای تست است",
        ]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_market_intent(raw))

    def test_strict_parser_rejects_multi_intent_collisions(self):
        for raw in ["btc trx", "btc dollar trx", "100 btc eth trx", "قیمت btc trx"]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_market_intent(raw))

    def test_alias_normalization_handles_arabic_variants_and_zero_width(self):
        self.assertEqual(parse_market_intent("قیمت بیت‌کوین").query_asset, "btc")
        self.assertEqual(parse_market_intent("۱۰۰ تتر تومان").target, "irt")
        self.assertEqual(parse_market_intent("لیر").query_asset, "try")
        self.assertEqual(parse_market_intent("روبل").query_asset, "rub")
        self.assertEqual(parse_market_intent("۱ لیر").source, "try")


class MarketEngineRenderTests(unittest.TestCase):
    def setUp(self):
        data = {}
        self.settings = merge_market_settings(data)
        self.cache = {
            "updated_at": int(time.time()),
            "rates_usd": {
                "usd": 1.0,
                "eur": 1.1,
                "try": 0.02177731,
                "rub": 0.01387425,
                "irt": 1 / 170820,
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

    def test_conversion_includes_unit_price_and_daily_change(self):
        text = render_market_response("۲۰۰ ترون تومان", self.settings, self.cache)
        self.assertIn("قیمت واحد ۱ TRX", text)
        self.assertIn("رشد روزانه", text)
        self.assertIn("2.50%", text)

    def test_price_uses_cached_change(self):
        text = render_market_response("trx status", self.settings, self.cache)
        self.assertIn("24h: +2.50%", text)
        self.assertIn("High", text)

    def test_extra_market_features_render_from_cache(self):
        self.assertIn("Trending", render_market_response("trend", self.settings, self.cache))
        self.assertIn("Top gainers", render_market_response("top gainers", self.settings, self.cache))
        self.assertIn("52.30%", render_market_response("btc dominance", self.settings, self.cache))
        self.assertIn("70/100", render_market_response("fear greed", self.settings, self.cache))

    def test_fiat_price_uses_cached_toman_rate(self):
        self.assertIn("تومانی: <b>3,720</b>", render_market_response("لیر", self.settings, self.cache))
        self.assertIn("تومانی: <b>2,370</b>", render_market_response("روبل", self.settings, self.cache))
        self.assertIn("دلار: <b>$0.02177731</b>", render_market_response("۱ لیر", self.settings, self.cache))

    def test_stale_cache_fails_safely(self):
        stale = dict(self.cache, updated_at=1)
        text = render_market_response("100 usd trx", self.settings, stale)
        self.assertIn("نرخ معتبر", text)


class MarketEngineAdminSupportTests(unittest.TestCase):
    def test_cache_status_reports_fresh_usable_cache(self):
        data = {}
        settings = merge_market_settings(data)
        status = cache_status({"updated_at": int(time.time()), "rates_usd": {"usd": 1.0, "trx": 0.12}}, settings)
        self.assertTrue(status["fresh"])
        self.assertTrue(status["usable"])
        self.assertEqual(status["rate_count"], 2)
        self.assertEqual(status["external_rate_count"], 1)

    def test_cache_status_rejects_manual_only_rates_as_unusable(self):
        data = {}
        settings = merge_market_settings(data)
        status = cache_status({"updated_at": int(time.time()), "rates_usd": {"usd": 1.0, "usdt": 1.0, "stars": 0.03}}, settings)
        self.assertFalse(status["fresh"])
        self.assertFalse(status["usable"])
        self.assertEqual(status["external_rate_count"], 0)

    def test_refresh_replaces_old_rates_instead_of_marking_stale_rates_fresh(self):
        class Store:
            def __init__(self):
                self.value = {"updated_at": int(time.time()) - 5000, "rates_usd": {"usd": 1.0, "btc": 70000.0}}
            def get_json(self, key, default):
                return self.value
            def set_json(self, key, value):
                self.value = value

        async def run_refresh():
            service = MarketRateService(Store())
            service._fetch_rates = Mock(return_value={"rates_usd": {"usd": 1.0, "eur": 1.1}, "meta": {"fiat": {"source": "test"}}})
            return await service.refresh({"request_timeout_seconds": 2})

        cache = asyncio.run(run_refresh())
        self.assertNotIn("btc", cache["rates_usd"])
        self.assertIn("eur", cache["rates_usd"])


    def test_refresh_if_needed_uses_usable_stale_cache_without_provider_call(self):
        class Store:
            def __init__(self):
                self.value = {"updated_at": int(time.time()) - 120, "rates_usd": {"usd": 1.0, "eur": 1.1}}
            def get_json(self, key, default):
                return self.value if key == CACHE_KEY else default
            def set_json(self, key, value):
                self.value = value

        async def run_refresh():
            service = MarketRateService(Store())
            service.refresh = Mock()
            return await service.refresh_if_needed({"market_api_enabled": True, "cache_ttl_seconds": 30, "stale_ttl_seconds": 86400})

        cache = asyncio.run(run_refresh())
        self.assertIn("eur", cache["rates_usd"])

    def test_exchangerate_recent_cache_skips_provider_request(self):
        class Store:
            def __init__(self):
                self.values = {CACHE_KEY: {"updated_at": int(time.time()) - 60, "rates_usd": {"usd": 1.0, "eur": 1.1}, "meta": {"fiat": {"updated_at": int(time.time()) - 60}}}}
            def get_json(self, key, default):
                return self.values.get(key, default)
            def set_json(self, key, value):
                self.values[key] = value

        service = MarketRateService(Store())
        service._fetch_coingecko = Mock(return_value={"rates_usd": {}, "meta": {}})
        service._fetch_exchange_rates = Mock(return_value={"rates_usd": {"eur": 1.2}, "meta": {"source": "exchange"}})
        service._fetch_nobitex = Mock(return_value={"rates_usd": {}, "meta": {}})
        service._fetch_fear_greed = Mock(return_value={})

        payload = service._fetch_rates({"coingecko_enabled": False, "exchangerate_enabled": True, "nobitex_enabled": False, "stars_unit_amount": 1000, "stars_unit_usd": 30})

        service._fetch_exchange_rates.assert_not_called()
        self.assertEqual(payload["rates_usd"]["eur"], 1.1)

    def test_exchangerate_429_sets_progressive_cooldown(self):
        EXCHANGERATE_RUNTIME_STATE.clear()
        class Store:
            def __init__(self):
                self.values = {}
            def get_json(self, key, default):
                return self.values.get(key, default)
            def set_json(self, key, value):
                self.values[key] = value

        store = Store()
        service = MarketRateService(store)

        service._record_exchangerate_429()
        first = store.values[EXCHANGERATE_COOLDOWN_KEY]
        service._record_exchangerate_429()
        second = store.values[EXCHANGERATE_COOLDOWN_KEY]

        self.assertEqual(first["penalty_level"], 1)
        self.assertEqual(second["penalty_level"], 2)
        self.assertGreaterEqual(second["cooldown_until"] - int(time.time()), 15 * 60 - 2)
        self.assertTrue(service._exchangerate_cooldown_active())

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
        EXCHANGERATE_RUNTIME_STATE.clear()
        service = MarketRateService()
        service._fetch_coingecko = Mock(side_effect=RuntimeError("rate limited"))
        service._fetch_exchange_rates = Mock(return_value={"rates_usd": {"eur": 1.1, "irt": 0.00002}, "meta": {"source": "exchange"}})
        service._fetch_nobitex = Mock(return_value={"rates_usd": {}, "meta": {"source": "nobitex"}})
        service._fetch_fear_greed = Mock(return_value={})
        payload = service._fetch_rates({"coingecko_enabled": True, "exchangerate_enabled": True, "nobitex_enabled": True, "stars_unit_amount": 1000, "stars_unit_usd": 30})
        self.assertIn("irt", payload["rates_usd"])
        self.assertIn("coingecko", payload["meta"]["provider_errors"])

    def test_nobitex_irt_rates_override_official_irr_rates(self):
        service = MarketRateService()
        service._fetch_coingecko = Mock(return_value={"rates_usd": {"trx": 0.30}, "meta": {}})
        service._fetch_exchange_rates = Mock(return_value={"rates_usd": {"irt": 0.0000075}, "meta": {"source": "exchange"}})
        service._fetch_nobitex = Mock(return_value={"rates_usd": {"irt": 1 / 170_820, "usdt": 1.0, "trx": 59042 / 170820}, "meta": {"source": "nobitex", "24h_change": {"trx": 0.2}}})
        service._fetch_fear_greed = Mock(return_value={})

        payload = service._fetch_rates({"coingecko_enabled": True, "exchangerate_enabled": True, "nobitex_enabled": True, "stars_unit_amount": 1000, "stars_unit_usd": 30})

        self.assertAlmostEqual(payload["rates_usd"]["irt"], 1 / 170_820)
        self.assertAlmostEqual(payload["rates_usd"]["trx"], 59042 / 170820)
        self.assertEqual(payload["meta"]["crypto"]["24h_change"]["trx"], 0.2)


    def test_nobitex_fetch_uses_symbol_markets_for_local_crypto_rates(self):
        service = MarketRateService()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status": "ok",
            "stats": {
                "usdt-rls": {"latest": "1708200"},
                "trx-rls": {"latest": "590420", "dayHigh": "602220", "dayLow": "584410", "dayChange": "0.2"},
            },
        }
        with patch("features.market_engine.requests.get", return_value=response) as mock_get:
            payload = service._fetch_nobitex({}, 3)

        self.assertEqual(mock_get.call_args.kwargs["params"]["srcCurrency"], "usdt,btc,eth,trx,ton")
        self.assertAlmostEqual(payload["rates_usd"]["irt"], 1 / 170_820)
        self.assertAlmostEqual(payload["rates_usd"]["trx"], 59042 / 170820)
        self.assertEqual(payload["meta"]["24h_change"]["trx"], 0.2)

    def test_provider_singleflight_rejects_duplicate_provider_fetch(self):
        service = MarketRateService()
        lock = PROVIDER_LOCKS["exchangerate"]
        self.assertTrue(lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(RuntimeError, "already in progress"):
                service._run_provider_singleflight("exchangerate", lambda: "called")
        finally:
            lock.release()

    @patch("features.market_engine.requests.get")
    def test_validate_exchangerate_key_is_cache_only_and_never_calls_pair_endpoint(self, mock_get):
        result = validate_market_api_key("exchangerate", "abc123", timeout=3)

        self.assertFalse(result["ok"])
        self.assertTrue(result["cache_only"])
        self.assertIn("no usable cached USD/EUR rate", result["message"])
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
