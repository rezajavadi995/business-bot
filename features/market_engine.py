from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import requests

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

ASSET_ALIASES: dict[str, str] = {
    "btc": "btc", "bitcoin": "btc", "بیتکوین": "btc", "بیت کوین": "btc",
    "eth": "eth", "ethereum": "eth", "اتریوم": "eth",
    "trx": "trx", "tron": "trx", "ترون": "trx",
    "ton": "ton", "تون": "ton", "the open network": "ton",
    "usdt": "usdt", "tether": "usdt", "تتر": "usdt",
    "usd": "usd", "dollar": "usd", "دلار": "usd", "دلار آمریکا": "usd", "$": "usd",
    "eur": "eur", "euro": "eur", "یورو": "eur",
    "try": "try", "tl": "try", "lira": "try", "turkish lira": "try", "لیر": "try", "لیر ترکیه": "try",
    "rub": "rub", "ruble": "rub", "rouble": "rub", "russian ruble": "rub", "روبل": "rub", "روبل روسیه": "rub",
    "irt": "irt", "toman": "irt", "tomans": "irt", "تومان": "irt", "تومن": "irt",
    "irr": "irr", "rial": "irr", "ریال": "irr",
    "stars": "stars", "star": "stars", "استارز": "stars", "استار": "stars",
}

CRYPTO_IDS: dict[str, str] = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "trx": "tron",
    "ton": "the-open-network",
    "usdt": "tether",
}
FIAT_ASSETS = {"usd", "eur", "try", "rub", "irt", "irr"}
SPECIAL_ASSETS = {"stars"}
SUPPORTED_ASSETS = set(CRYPTO_IDS) | FIAT_ASSETS | SPECIAL_ASSETS
DEFAULT_QUICK_ASSETS = ["btc", "eth", "trx", "ton", "usdt"]
DEFAULT_CONVERSION_TARGETS = ["irt", "usd", "ton"]
CACHE_KEY = "market_cache"


class JsonStore(Protocol):
    def get_json(self, key: str, default: Any) -> Any: ...
    def set_json(self, key: str, value: Any) -> None: ...


@dataclass(frozen=True)
class MarketIntent:
    kind: str
    amount: float | None = None
    source: str | None = None
    target: str | None = None
    query_asset: str | None = None


@dataclass(frozen=True)
class ConversionResult:
    amount: float
    source: str
    target: str
    value: float
    source_usd: float
    target_usd: float
    cache_age: int
    stale: bool
    updated_at: int


def default_market_settings() -> dict[str, Any]:
    return {
        "market_engine_enabled": False,
        "market_api_enabled": True,
        "market_process_edited_messages": False,
        "coingecko_enabled": True,
        "exchangerate_enabled": True,
        "nobitex_enabled": True,
        "cache_ttl_seconds": int(os.getenv("MARKET_CACHE_TTL_SECONDS", "60") or 60),
        "stale_ttl_seconds": int(os.getenv("MARKET_STALE_TTL_SECONDS", "86400") or 86400),
        "request_timeout_seconds": float(os.getenv("MARKET_API_TIMEOUT_SECONDS", "8") or 8),
        "stars_unit_amount": 1000.0,
        "stars_unit_usd": 30.0,
        "stars_auto_multiplier_enabled": False,
        "stars_manual_override_usd": None,
        "quick_assets": DEFAULT_QUICK_ASSETS.copy(),
        "default_conversion_targets": DEFAULT_CONVERSION_TARGETS.copy(),
    }


def merge_market_settings(data: dict[str, Any]) -> dict[str, Any]:
    defaults = default_market_settings()
    raw = data.get("market", {})
    if not isinstance(raw, dict):
        raw = {}
    for key, value in defaults.items():
        raw.setdefault(key, value)
    raw["market_engine_enabled"] = bool(raw.get("market_engine_enabled", False))
    raw["market_api_enabled"] = bool(raw.get("market_api_enabled", True))
    raw["market_process_edited_messages"] = bool(raw.get("market_process_edited_messages", False))
    raw["coingecko_enabled"] = bool(raw.get("coingecko_enabled", True))
    raw["exchangerate_enabled"] = bool(raw.get("exchangerate_enabled", True))
    raw["nobitex_enabled"] = bool(raw.get("nobitex_enabled", True))
    raw["cache_ttl_seconds"] = max(30, min(int(raw.get("cache_ttl_seconds") or 60), 3600))
    raw["stale_ttl_seconds"] = max(raw["cache_ttl_seconds"], min(int(raw.get("stale_ttl_seconds") or 86400), 24 * 3600))
    raw["request_timeout_seconds"] = max(2.0, min(float(raw.get("request_timeout_seconds") or 8), 20.0))
    raw["quick_assets"] = normalize_asset_list(raw.get("quick_assets"), DEFAULT_QUICK_ASSETS)
    raw["default_conversion_targets"] = normalize_asset_list(raw.get("default_conversion_targets"), DEFAULT_CONVERSION_TARGETS)
    data["market"] = raw
    return raw


def normalize_asset_list(value: Any, fallback: list[str]) -> list[str]:
    items = value if isinstance(value, list) else fallback
    normalized: list[str] = []
    for item in items:
        asset = normalize_asset(str(item))
        if asset in SUPPORTED_ASSETS and asset not in normalized:
            normalized.append(asset)
    return normalized or fallback.copy()


def normalize_number(value: str) -> float | None:
    cleaned = str(value or "").translate(PERSIAN_DIGITS)
    cleaned = cleaned.replace("٬", "").replace(",", "").replace("_", "")
    cleaned = cleaned.replace("٫", ".")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_text(text: str) -> str:
    value = str(text or "").translate(PERSIAN_DIGITS).casefold()
    value = (
        value.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("‌", " ")
        .replace("٫", ".")
        .replace("٬", ",")
    )
    value = re.sub(r"\$", " $ ", value)
    value = re.sub(r"([0-9][0-9,]*(?:\.[0-9]+)?)(?=[^\W\d_\$])", r"\1 ", value, flags=re.UNICODE)
    value = re.sub(r"(?<=[^\W\d_])([0-9])", r" \1", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_asset(value: str | None) -> str | None:
    if not value:
        return None
    key = normalize_text(str(value)).strip(" /\\|:؛;،,.؟?!()[]{}<>")
    return ASSET_ALIASES.get(key)


def extract_assets(words: list[str]) -> list[str]:
    return [asset for asset, _, _ in extract_asset_matches(words)]


def extract_asset_matches(words: list[str]) -> list[tuple[str, int, int]]:
    matches: list[tuple[str, int, int]] = []
    idx = 0
    max_alias_words = max(len(normalize_text(alias).split()) for alias in ASSET_ALIASES)
    while idx < len(words):
        matched: str | None = None
        matched_len = 0
        for size in range(min(max_alias_words, len(words) - idx), 0, -1):
            phrase = " ".join(words[idx:idx + size])
            asset = normalize_asset(phrase)
            if asset:
                matched = asset
                matched_len = size
                break
        if matched:
            matches.append((matched, idx, idx + matched_len))
            idx += matched_len
        else:
            idx += 1
    return matches


def _unique_in_order(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _asset_tokens(words: list[str], matches: list[tuple[str, int, int]]) -> set[int]:
    tokens: set[int] = set()
    for _, start, end in matches:
        tokens.update(range(start, end))
    return tokens


def _number_tokens(words: list[str]) -> set[int]:
    return {idx for idx, word in enumerate(words) if normalize_number(word) is not None}


def _has_only_intent_tokens(words: list[str], matches: list[tuple[str, int, int]], allowed_words: set[str]) -> bool:
    asset_tokens = _asset_tokens(words, matches)
    number_tokens = _number_tokens(words)
    for idx, word in enumerate(words):
        if idx in asset_tokens or idx in number_tokens or word in allowed_words:
            continue
        return False
    return True


def parse_market_intent(text: str) -> MarketIntent | None:
    normalized = normalize_text(text)
    if not normalized or len(normalized) > 160:
        return None
    words = normalized.split()
    if len(words) > 8:
        return None

    matches = extract_asset_matches(words)
    assets = [asset for asset, _, _ in matches]
    unique_assets = _unique_in_order(assets)
    amount: float | None = None
    for word in words:
        amount = normalize_number(word)
        if amount is not None:
            break

    price_commands = {"price", "قیمت"}
    status_commands = {"status", "today", "وضعیت", "امروز"}
    dominance_commands = {"dominance", "دامیننس"}

    if normalized in {"fear", "fear greed", "fear & greed", "ترس", "طمع", "شاخص ترس"}:  # exact commands only
        return MarketIntent(kind="fear_greed")
    if normalized in {"trend", "trends", "ترند", "top gainers", "gainers"}:  # exact commands only
        return MarketIntent(kind="trending")

    # Conversion requests must be compact, intentional commands: an amount plus one
    # or two known assets and no natural-language filler. This avoids firing on
    # ordinary sentences that merely contain market keywords.
    if amount is not None and assets:
        if len(unique_assets) > 2:
            return None
        if not _has_only_intent_tokens(words, matches, set()):
            return None
        source = assets[0]
        target = next((asset for asset in assets[1:] if asset != source), None)
        return MarketIntent(kind="conversion", amount=amount, source=source, target=target)

    if assets:
        if len(unique_assets) != 1:
            return None
        asset = unique_assets[0]
        command_words = set(words) - {word for _, start, end in matches for word in words[start:end]}
        if command_words & dominance_commands:
            if asset == "btc" and _has_only_intent_tokens(words, matches, dominance_commands):
                return MarketIntent(kind="dominance", query_asset="btc")
            return None
        if command_words & price_commands:
            if _has_only_intent_tokens(words, matches, price_commands):
                return MarketIntent(kind="price", query_asset=asset)
            return None
        if command_words & status_commands:
            if asset in CRYPTO_IDS and _has_only_intent_tokens(words, matches, status_commands):
                return MarketIntent(kind="status", query_asset=asset)
            return None
        if len(words) <= 3 and len(matches) == 1 and asset in SUPPORTED_ASSETS and _has_only_intent_tokens(words, matches, set()):
            return MarketIntent(kind="price", query_asset=asset)

    return None



class MarketRateService:
    def __init__(self, store: JsonStore | None = None):
        self.store = store
        self._stop = asyncio.Event()
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_started_at = 0

    def bind_store(self, store: JsonStore) -> None:
        self.store = store

    def read_cache(self) -> dict[str, Any]:
        if not self.store:
            return {}
        cache = self.store.get_json(CACHE_KEY, {})
        return dict(cache) if isinstance(cache, dict) else {}

    def write_cache(self, cache: dict[str, Any]) -> None:
        if self.store:
            self.store.set_json(CACHE_KEY, dict(cache))

    def cache_is_fresh(self, settings: dict[str, Any]) -> bool:
        status = cache_status(self.read_cache(), settings)
        return bool(status.get("fresh") and status.get("usable"))

    async def refresh_if_needed(self, settings: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        if not settings.get("market_api_enabled", True):
            return self.read_cache()
        if not force and self.cache_is_fresh(settings):
            return self.read_cache()
        if self._refresh_lock.locked() and not force:
            return self.read_cache()
        async with self._refresh_lock:
            if not force and self.cache_is_fresh(settings):
                return self.read_cache()
            return await self.refresh(settings, _locked=True)

    async def run_forever(self, settings_getter) -> None:
        await asyncio.sleep(2)
        while not self._stop.is_set():
            settings = settings_getter()
            interval = int(settings.get("cache_ttl_seconds", 60) or 60)
            if settings.get("_global_active", False) and settings.get("market_engine_enabled", False) and settings.get("market_api_enabled", True):
                await self.refresh(settings)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(30, interval))
            except asyncio.TimeoutError:
                pass

    async def refresh(self, settings: dict[str, Any], _locked: bool = False) -> dict[str, Any]:
        if not _locked:
            async with self._refresh_lock:
                return await self.refresh(settings, _locked=True)
        self._last_refresh_started_at = int(time.time())
        try:
            timeout = float(settings.get("request_timeout_seconds", 8) or 8) + 5.0
            payload = await asyncio.wait_for(asyncio.to_thread(self._fetch_rates, settings), timeout=timeout)
            rates = payload.get("rates_usd", {}) if isinstance(payload.get("rates_usd"), dict) else {}
            if not any(asset not in {"usd", "usdt", "stars"} for asset in rates):
                raise RuntimeError("refresh returned no external market rates")
            cache = {**payload, "rates_usd": dict(rates), "meta": payload.get("meta", {}) if isinstance(payload.get("meta"), dict) else {}, "updated_at": int(time.time()), "last_error": None}
            self.write_cache(cache)
            return cache
        except Exception as exc:
            logging.warning("market_refresh_failed reason=%s", safe_error(exc))
            cache = self.read_cache()
            cache["last_error"] = safe_error(exc)
            cache["last_error_at"] = int(time.time())
            self.write_cache(cache)
            return cache

    def _fetch_rates(self, settings: dict[str, Any]) -> dict[str, Any]:
        timeout = float(settings.get("request_timeout_seconds", 8) or 8)
        crypto: dict[str, Any] = {}
        fiat: dict[str, Any] = {}
        local: dict[str, Any] = {}
        errors: dict[str, str] = {}
        if settings.get("coingecko_enabled", True):
            try:
                crypto = self._fetch_coingecko(settings, timeout)
            except Exception as exc:
                errors["coingecko"] = safe_error(exc)
                logging.warning("market_provider_failed provider=coingecko reason=%s", errors["coingecko"])
        if settings.get("exchangerate_enabled", True):
            try:
                fiat = self._fetch_exchange_rates(settings, timeout)
            except Exception as exc:
                errors["exchangerate"] = safe_error(exc)
                logging.warning("market_provider_failed provider=exchangerate reason=%s", errors["exchangerate"])
        if settings.get("nobitex_enabled", True):
            try:
                local = self._fetch_nobitex(settings, timeout)
            except Exception as exc:
                errors["nobitex"] = safe_error(exc)
                logging.warning("market_provider_failed provider=nobitex reason=%s", errors["nobitex"])
        rates_usd: dict[str, float] = {"usd": 1.0}
        sentiment = self._fetch_fear_greed(timeout)
        crypto_meta = dict(crypto.get("meta", {})) if isinstance(crypto.get("meta", {}), dict) else {}
        local_meta = local.get("meta", {}) if isinstance(local.get("meta", {}), dict) else {}
        for key in ("24h_change", "24h_high", "24h_low"):
            merged = dict(crypto_meta.get(key, {})) if isinstance(crypto_meta.get(key, {}), dict) else {}
            local_values = local_meta.get(key, {}) if isinstance(local_meta.get(key, {}), dict) else {}
            merged.update(local_values)
            crypto_meta[key] = merged
        if local_meta.get("source"):
            crypto_meta["local_source"] = local_meta.get("source")
        meta: dict[str, Any] = {"crypto": crypto_meta, "fiat": fiat.get("meta", {}), "local": local_meta, "fear_greed": sentiment, "provider_errors": errors}
        rates_usd.update(crypto.get("rates_usd", {}))
        rates_usd.update(fiat.get("rates_usd", {}))
        rates_usd.update(local.get("rates_usd", {}))
        stars_usd = stars_unit_usd(settings)
        if stars_usd > 0:
            rates_usd["stars"] = stars_usd
        if "usdt" not in rates_usd:
            rates_usd["usdt"] = 1.0
        if len(rates_usd) <= 2 and errors:
            raise RuntimeError("; ".join(f"{k}: {v}" for k, v in errors.items()))
        if len(rates_usd) <= 2:
            raise RuntimeError("no usable market rates returned")
        return {"rates_usd": rates_usd, "meta": meta}

    def _fetch_coingecko(self, settings: dict[str, Any], timeout: float) -> dict[str, Any]:
        ids = ",".join(CRYPTO_IDS.values())
        headers = {}
        key = os.getenv("COINGECKO_API_KEY", "").strip()
        if key:
            headers["x-cg-demo-api-key"] = key
        response = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "ids": ids, "price_change_percentage": "24h"},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        rates: dict[str, float] = {}
        changes: dict[str, float] = {}
        highs: dict[str, float] = {}
        lows: dict[str, float] = {}
        id_to_symbol = {coin_id: symbol for symbol, coin_id in CRYPTO_IDS.items()}
        for item in body if isinstance(body, list) else []:
            if not isinstance(item, dict):
                continue
            symbol = id_to_symbol.get(str(item.get("id") or ""))
            if not symbol:
                continue
            usd = item.get("current_price")
            if isinstance(usd, (int, float)) and usd > 0:
                rates[symbol] = float(usd)
            change = item.get("price_change_percentage_24h")
            if isinstance(change, (int, float)):
                changes[symbol] = float(change)
            high = item.get("high_24h")
            low = item.get("low_24h")
            if isinstance(high, (int, float)):
                highs[symbol] = float(high)
            if isinstance(low, (int, float)):
                lows[symbol] = float(low)
        trending = self._fetch_coingecko_trending(headers, timeout)
        return {"rates_usd": rates, "meta": {"24h_change": changes, "24h_high": highs, "24h_low": lows, "trending": trending.get("trending", []), "top_gainers": build_top_gainers(changes), "dominance": self._fetch_coingecko_global(headers, timeout), "source": "coingecko"}}

    def _fetch_coingecko_trending(self, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        try:
            response = requests.get("https://api.coingecko.com/api/v3/search/trending", headers=headers, timeout=timeout)
            response.raise_for_status()
            body = response.json()
            coins = []
            for row in (body.get("coins", []) if isinstance(body, dict) else [])[:7]:
                item = row.get("item", {}) if isinstance(row, dict) else {}
                symbol = str(item.get("symbol") or "").upper()
                name = str(item.get("name") or "")
                if symbol:
                    coins.append({"symbol": symbol, "name": name})
            return {"trending": coins}
        except Exception as exc:
            logging.warning("market_provider_failed provider=coingecko_trending reason=%s", safe_error(exc))
            return {"trending": [], "error": safe_error(exc)}

    def _fetch_coingecko_global(self, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        try:
            response = requests.get("https://api.coingecko.com/api/v3/global", headers=headers, timeout=timeout)
            response.raise_for_status()
            body = response.json()
            data = body.get("data", {}) if isinstance(body, dict) else {}
            dominance = data.get("market_cap_percentage", {}) if isinstance(data, dict) else {}
            btc = dominance.get("btc") if isinstance(dominance, dict) else None
            return {"btc": float(btc)} if isinstance(btc, (int, float)) else {}
        except Exception as exc:
            logging.warning("market_provider_failed provider=coingecko_global reason=%s", safe_error(exc))
            return {"error": safe_error(exc)}

    def _fetch_fear_greed(self, timeout: float) -> dict[str, Any]:
        try:
            response = requests.get("https://api.alternative.me/fng/", params={"limit": 1}, timeout=timeout)
            response.raise_for_status()
            body = response.json()
            item = (body.get("data") or [{}])[0] if isinstance(body, dict) else {}
            value = item.get("value")
            return {"value": int(value), "classification": str(item.get("value_classification") or ""), "timestamp": int(item.get("timestamp") or 0)} if str(value).isdigit() else {}
        except Exception as exc:
            logging.warning("market_provider_failed provider=fear_greed reason=%s", safe_error(exc))
            return {"error": safe_error(exc)}

    def _fetch_exchange_rates(self, settings: dict[str, Any], timeout: float) -> dict[str, Any]:
        key = os.getenv("EXCHANGERATE_API_KEY", "").strip()
        if key:
            url = f"https://v6.exchangerate-api.com/v6/{key}/latest/USD"
        else:
            url = "https://open.er-api.com/v6/latest/USD"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        rates = body.get("conversion_rates") or body.get("rates") or {} if isinstance(body, dict) else {}
        out: dict[str, float] = {"usd": 1.0}
        for code, asset in {"EUR": "eur", "TRY": "try", "RUB": "rub"}.items():
            rate = rates.get(code) if isinstance(rates, dict) else None
            if isinstance(rate, (int, float)) and rate > 0:
                out[asset] = 1.0 / float(rate)
        irr = rates.get("IRR") if isinstance(rates, dict) else None
        if isinstance(irr, (int, float)) and irr > 0:
            out["irr"] = 1.0 / float(irr)
            out["irt"] = 10.0 / float(irr)
        source = "exchangerate" if key else "open-er-api"
        return {"rates_usd": out, "meta": {"source": source, "result": body.get("result") if isinstance(body, dict) else None}}

    def _fetch_nobitex(self, settings: dict[str, Any], timeout: float) -> dict[str, Any]:
        src = ",".join(["usdt", *CRYPTO_IDS.values()])
        response = requests.get(
            "https://apiv2.nobitex.ir/market/stats",
            params={"srcCurrency": src, "dstCurrency": "rls"},
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        stats = body.get("stats", {}) if isinstance(body, dict) else {}
        rates: dict[str, float] = {}
        changes: dict[str, float] = {}
        highs: dict[str, float] = {}
        lows: dict[str, float] = {}

        usdt_toman = self._nobitex_toman(stats, "usdt")
        if not usdt_toman:
            return {"rates_usd": {}, "meta": {"source": "nobitex", "status": body.get("status") if isinstance(body, dict) else None}}
        rates["irr"] = 1.0 / (usdt_toman * 10.0)
        rates["irt"] = 1.0 / usdt_toman
        rates["usdt"] = 1.0

        id_to_symbol = {coin_id: symbol for symbol, coin_id in CRYPTO_IDS.items()}
        for coin_id, symbol in id_to_symbol.items():
            if symbol == "usdt":
                continue
            toman = self._nobitex_toman(stats, coin_id)
            if not toman:
                continue
            rates[symbol] = toman / usdt_toman
            high_toman = self._nobitex_toman(stats, coin_id, field="dayHigh")
            low_toman = self._nobitex_toman(stats, coin_id, field="dayLow")
            if high_toman:
                highs[symbol] = high_toman / usdt_toman
            if low_toman:
                lows[symbol] = low_toman / usdt_toman
            change = self._nobitex_number(stats, coin_id, "dayChange")
            if change is not None:
                changes[symbol] = change
        return {"rates_usd": rates, "meta": {"source": "nobitex", "quote": "USDTIRT", "usdt_toman": usdt_toman, "24h_change": changes, "24h_high": highs, "24h_low": lows}}

    @staticmethod
    def _nobitex_number(stats: dict[str, Any], source: str, field: str) -> float | None:
        if not isinstance(stats, dict):
            return None
        row = stats.get(f"{source}-rls") or stats.get(f"{source}-irt") or stats.get(f"{source.upper()}IRT")
        if not isinstance(row, dict):
            return None
        try:
            value = float(row.get(field))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @classmethod
    def _nobitex_toman(cls, stats: dict[str, Any], source: str, field: str = "latest") -> float | None:
        value = cls._nobitex_number(stats, source, field)
        return value / 10.0 if value else None


def build_top_gainers(changes: dict[str, float]) -> list[dict[str, Any]]:
    return [{"symbol": symbol.upper(), "change": change} for symbol, change in sorted(changes.items(), key=lambda item: item[1], reverse=True)[:5]]


def stars_unit_usd(settings: dict[str, Any]) -> float:
    amount = max(float(settings.get("stars_unit_amount") or 1000), 1.0)
    override = settings.get("stars_manual_override_usd")
    if not settings.get("stars_auto_multiplier_enabled", False) and isinstance(override, (int, float)) and override > 0:
        return float(override) / amount
    total = float(settings.get("stars_unit_usd") or 0)
    return total / amount if total > 0 else 0.0


def safe_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    text = re.sub(r"(?i)(api[_-]?key=|/v6/)[^/&\s]+", r"\1***", text)
    return text[:180]


def convert_amount(cache: dict[str, Any], amount: float, source: str, target: str, stale_ttl: int) -> ConversionResult | None:
    rates = cache.get("rates_usd") if isinstance(cache, dict) else None
    if not isinstance(rates, dict):
        return None
    source_usd = rates.get(source)
    target_usd = rates.get(target)
    if not isinstance(source_usd, (int, float)) or not isinstance(target_usd, (int, float)) or source_usd <= 0 or target_usd <= 0:
        return None
    updated_at = int(cache.get("updated_at") or 0)
    age = max(0, int(time.time()) - updated_at) if updated_at else 10**9
    if age > stale_ttl:
        return None
    return ConversionResult(amount, source, target, amount * float(source_usd) / float(target_usd), float(source_usd), float(target_usd), age, age > 90, updated_at)


def format_number(value: float, asset: str | None = None) -> str:
    decimals = 2
    if asset in {"btc", "eth", "ton", "trx"}:
        decimals = 6 if abs(value) < 100 else 3
    if asset in {"irt", "irr"}:
        decimals = 0
    if asset == "usd":
        abs_value = abs(value)
        decimals = 8 if abs_value < 0.1 else 4 if abs_value < 1 else 3 if abs_value < 10 else 2
    if asset == "stars":
        decimals = 0
    text = f"{value:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def asset_label(asset: str) -> str:
    return {"irt": "تومان", "irr": "ریال", "usd": "دلار آمریکا", "eur": "یورو", "try": "لیر ترکیه", "rub": "روبل روسیه", "stars": "Stars", "usdt": "USDT"}.get(asset, asset.upper())


def _gregorian_to_jalali(year: int, month: int, day: int) -> tuple[int, int, int]:
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    gy = year - 1600
    gm = month - 1
    gd = day - 1
    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    for idx in range(gm):
        g_day_no += g_days_in_month[idx]
    if gm > 1 and ((gy + 1600) % 4 == 0 and ((gy + 1600) % 100 != 0 or (gy + 1600) % 400 == 0)):
        g_day_no += 1
    g_day_no += gd
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    jm = 0
    while jm < 11 and j_day_no >= j_days_in_month[jm]:
        j_day_no -= j_days_in_month[jm]
        jm += 1
    return jy, jm + 1, j_day_no + 1


def format_timestamp(ts: int) -> str:
    if not ts:
        return "نامشخص"
    try:
        dt = datetime.fromtimestamp(ts, ZoneInfo("Asia/Tehran"))
    except Exception:
        dt = datetime.utcfromtimestamp(ts)
    jy, jm, jd = _gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d} | {dt:%H:%M:%S}"


def render_conversion(intent: MarketIntent, settings: dict[str, Any], cache: dict[str, Any]) -> str | None:
    if intent.amount is None or not intent.source:
        return None
    stale_ttl = int(settings.get("stale_ttl_seconds", 3600))

    if intent.target:
        result = convert_amount(cache, intent.amount, intent.source, intent.target, stale_ttl)
        if not result:
            return unavailable_message(cache)
        lines = [f"🔄 {format_number(intent.amount, intent.source)} {intent.source} = {format_number(result.value, intent.target)} {asset_label(intent.target)}"]
        toman = convert_amount(cache, intent.amount, intent.source, "irt", stale_ttl)
        usd_value = intent.amount * result.source_usd
        if toman:
            lines.append(f"💸 {format_number(toman.value, 'irt')} toman")
        lines.append(f"💵 ${format_number(usd_value, 'usd')} dollar")
        if result.stale:
            lines.append("⚠️ نرخ‌ها از کش قبلی استفاده شده‌اند.")
        lines.append(f"🪙 {format_timestamp(result.updated_at)}")
        return "\n".join(lines)

    toman = convert_amount(cache, intent.amount, intent.source, "irt", stale_ttl)
    usd = convert_amount(cache, intent.amount, intent.source, "usd", stale_ttl)
    if not toman and not usd:
        return unavailable_message(cache)
    lines = [f"✨ {format_number(intent.amount, intent.source)} {asset_label(intent.source)} :"]
    if toman:
        lines.append(f"💸 {format_number(toman.value, 'irt')} toman")
    if usd:
        lines.append(f"💵 ${format_number(usd.value, 'usd')} dollar")
    first = toman or usd
    if first and first.stale:
        lines.append("⚠️ نرخ‌ها از کش قبلی استفاده شده‌اند.")
    if first:
        lines.append(f"🪙 {format_timestamp(first.updated_at)}")
    return "\n".join(lines)


def render_price(intent: MarketIntent, settings: dict[str, Any], cache: dict[str, Any]) -> str | None:
    asset = intent.query_asset
    if not asset:
        return None
    rates = cache.get("rates_usd", {}) if isinstance(cache, dict) else {}
    usd = rates.get(asset) if isinstance(rates, dict) else None
    if not isinstance(usd, (int, float)):
        return unavailable_message(cache)
    age = int(time.time()) - int(cache.get("updated_at") or 0)
    if age > int(settings.get("stale_ttl_seconds", 3600)):
        return unavailable_message(cache)
    crypto_meta = ((cache.get("meta") or {}).get("crypto") or {})
    change = (crypto_meta.get("24h_change") or {}).get(asset)
    high = (crypto_meta.get("24h_high") or {}).get(asset)
    low = (crypto_meta.get("24h_low") or {}).get(asset)
    irt = rates.get("irt") if isinstance(rates, dict) else None
    toman = float(usd) / float(irt) if isinstance(irt, (int, float)) and irt > 0 else None
    lines = [f"✨ 1 {asset_label(asset)} :"]
    if toman is not None:
        lines.append(f"💸 {format_number(toman, 'irt')} toman")
    lines.append(f"💵 ${format_number(float(usd), 'usd')} dollar")
    if isinstance(change, (int, float)):
        arrow = "↗️" if change >= 0 else "↘️"
        lines.append(f"{'🟢' if change >= 0 else '🔴'} {abs(change):.2f}%{arrow}")
        lines.append(f"24h: {change:+.2f}%")
    if isinstance(high, (int, float)) and isinstance(low, (int, float)):
        lines.append("High & Low 📉")
        if isinstance(irt, (int, float)) and irt > 0:
            lines.append(f"💸 {format_number(float(high) / float(irt), 'irt')} / {format_number(float(low) / float(irt), 'irt')} toman")
        lines.append(f"💵 {format_number(float(high), 'usd')} / {format_number(float(low), 'usd')} dollar")
    if age > 90:
        lines.append("⚠️ نرخ از کش قبلی خوانده شد.")
    lines.append(f"🪙 {format_timestamp(int(cache.get('updated_at') or 0))}")
    return "\n".join(lines)


def render_trending(cache: dict[str, Any], kind: str) -> str:
    crypto_meta = ((cache.get("meta") or {}).get("crypto") or {}) if isinstance(cache, dict) else {}
    if kind == "gainers":
        gainers = crypto_meta.get("top_gainers") or []
        if not gainers:
            return unavailable_message(cache)
        lines = ["🚀 Top gainers (cached):", ""]
        for item in gainers[:5]:
            lines.append(f"• {item.get('symbol')}: {float(item.get('change') or 0):+.2f}%")
        lines.append(f"\n⏱ {format_timestamp(int(cache.get('updated_at') or 0))}")
        return "\n".join(lines)
    trending = crypto_meta.get("trending") or []
    if not trending:
        return unavailable_message(cache)
    lines = ["🔥 Trending coins (cached):", ""]
    for item in trending[:7]:
        lines.append(f"• {item.get('symbol')} — {item.get('name') or '-'}")
    lines.append(f"\n⏱ {format_timestamp(int(cache.get('updated_at') or 0))}")
    return "\n".join(lines)


def render_dominance(cache: dict[str, Any]) -> str:
    dominance = (((cache.get("meta") or {}).get("crypto") or {}).get("dominance") or {}).get("btc") if isinstance(cache, dict) else None
    if not isinstance(dominance, (int, float)):
        return unavailable_message(cache)
    return f"👑 BTC Dominance\n\n📊 {dominance:.2f}%\n⏱ {format_timestamp(int(cache.get('updated_at') or 0))}"


def render_fear_greed(cache: dict[str, Any]) -> str:
    data = ((cache.get("meta") or {}).get("fear_greed") or {}) if isinstance(cache, dict) else {}
    value = data.get("value")
    if not isinstance(value, int):
        return unavailable_message(cache)
    return f"😨 Fear & Greed\n\n📊 {value}/100 — {data.get('classification') or '-'}\n⏱ {format_timestamp(int(data.get('timestamp') or cache.get('updated_at') or 0))}"


def unavailable_message(cache: dict[str, Any]) -> str:
    err = cache.get("last_error") if isinstance(cache, dict) else None
    suffix = f"\nجزئیات امن خطا: {err}" if err else ""
    return "⚠️ نرخ معتبر و تازه فعلاً در کش بازار موجود نیست. لطفاً چند لحظه بعد دوباره تلاش کنید." + suffix


def render_market_response(text: str, settings: dict[str, Any], cache: dict[str, Any]) -> str | None:
    intent = parse_market_intent(text)
    if not intent:
        return None
    if intent.kind == "conversion":
        return render_conversion(intent, settings, cache)
    if intent.kind in {"price", "status"}:
        return render_price(intent, settings, cache)
    if intent.kind == "trending":
        normalized = normalize_text(text)
        return render_trending(cache, "gainers" if "gainers" in normalized else "trending")
    if intent.kind == "dominance":
        return render_dominance(cache)
    if intent.kind == "fear_greed":
        return render_fear_greed(cache)
    return None


def validate_market_api_key(provider: str, api_key: str, timeout: float = 8.0) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    key = str(api_key or "").strip()
    if provider not in {"coingecko", "exchangerate"}:
        return {"ok": False, "provider": provider, "message": "Unknown API provider."}
    if not key or len(key) > 256:
        return {"ok": False, "provider": provider, "message": "API key is empty or too long."}
    try:
        if provider == "coingecko":
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                headers={"x-cg-demo-api-key": key},
                timeout=timeout,
            )
            if response.status_code in {401, 403, 429}:
                return {"ok": False, "provider": provider, "message": f"CoinGecko rejected the key/status: {response.status_code}"}
            response.raise_for_status()
            body = response.json()
            price = ((body.get("bitcoin") or {}).get("usd") if isinstance(body, dict) else None)
            if not isinstance(price, (int, float)) or price <= 0:
                return {"ok": False, "provider": provider, "message": "CoinGecko response did not include BTC/USD price."}
            return {"ok": True, "provider": provider, "message": f"CoinGecko validation OK. BTC/USD={price}", "sample": price}
        response = requests.get(f"https://v6.exchangerate-api.com/v6/{key}/pair/USD/EUR", timeout=timeout)
        if response.status_code in {401, 403, 404, 429}:
            return {"ok": False, "provider": provider, "message": f"ExchangeRate rejected the key/status: {response.status_code}"}
        response.raise_for_status()
        body = response.json()
        if body.get("result") != "success":
            return {"ok": False, "provider": provider, "message": str(body.get("error-type") or body.get("result") or "ExchangeRate validation failed")}
        rate = body.get("conversion_rate")
        if not isinstance(rate, (int, float)) or rate <= 0:
            return {"ok": False, "provider": provider, "message": "ExchangeRate response did not include USD/EUR rate."}
        return {"ok": True, "provider": provider, "message": f"ExchangeRate validation OK. USD/EUR={rate}", "sample": rate}
    except Exception as exc:
        return {"ok": False, "provider": provider, "message": safe_error(exc)}


def cache_status(cache: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    updated_at = int(cache.get("updated_at") or 0) if isinstance(cache, dict) else 0
    age = max(0, int(time.time()) - updated_at) if updated_at else None
    rates = cache.get("rates_usd") if isinstance(cache, dict) else None
    rate_count = len(rates) if isinstance(rates, dict) else 0
    external_rate_count = len([asset for asset in rates if asset not in {"usd", "usdt", "stars"}]) if isinstance(rates, dict) else 0
    ttl = int(settings.get("cache_ttl_seconds", 60) or 60)
    stale_ttl = int(settings.get("stale_ttl_seconds", 86400) or 86400)
    usable = age is not None and age <= stale_ttl and external_rate_count > 0
    return {
        "updated_at": updated_at,
        "age": age,
        "rate_count": rate_count,
        "external_rate_count": external_rate_count,
        "fresh": usable and age <= ttl,
        "usable": usable,
        "last_error": cache.get("last_error") if isinstance(cache, dict) else None,
    }


def market_help_text(is_admin: bool = False) -> str:
    base = [
        "📈 راهنمای Market & Conversion Engine",
        "",
        "نمونه تبدیل‌ها:",
        "• ۲۰۰۰ استارز",
        "• 100 usd trx",
        "• ۲۰ ترون تومان",
        "• 1200000 تومان تتر",
        "",
        "نمونه قیمت/وضعیت:",
        "• btc",
        "• price trx",
        "• trx status",
        "• btc today",
        "• trend",
        "• top gainers",
        "• btc dominance",
        "• fear greed",
        "",
        "Alias ها: ترون/trx، تون/ton، تتر/usdt، دلار/usd/$، تومان/irt، ریال/irr، استارز/stars",
    ]
    if is_admin:
        base.extend([
            "",
            "ادمین:",
            "• از پنل: Market API Configuration برای تنظیم و اعتبارسنجی API ها",
            "• Stars Rate Settings برای نرخ دستی استارز",
            "• Cache Settings برای TTL و وضعیت کش",
        ])
    return "\n".join(base)


MARKET_SERVICE = MarketRateService()
