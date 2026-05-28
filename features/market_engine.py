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
    "usd": "usd", "dollar": "usd", "دلار": "usd", "$": "usd",
    "eur": "eur", "euro": "eur", "یورو": "eur",
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
FIAT_ASSETS = {"usd", "eur", "irt", "irr"}
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
        "coingecko_enabled": True,
        "exchangerate_enabled": True,
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
    raw["coingecko_enabled"] = bool(raw.get("coingecko_enabled", True))
    raw["exchangerate_enabled"] = bool(raw.get("exchangerate_enabled", True))
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
    value = value.replace("٫", ".").replace("٬", ",")
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
    assets: list[str] = []
    idx = 0
    max_alias_words = max(len(alias.split()) for alias in ASSET_ALIASES)
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
            assets.append(matched)
            idx += matched_len
        else:
            idx += 1
    return assets


def parse_market_intent(text: str) -> MarketIntent | None:
    normalized = normalize_text(text)
    if not normalized or len(normalized) > 160:
        return None
    words = normalized.split()
    assets = extract_assets(words)
    amount: float | None = None
    for word in words:
        amount = normalize_number(word)
        if amount is not None:
            break

    if normalized in {"trend", "trends", "ترند", "top gainers", "gainers"}:
        return MarketIntent(kind="trending")
    if "dominance" in words or "دامیننس" in words:
        if "btc" in assets:
            return MarketIntent(kind="dominance", query_asset="btc")
    if any(w in words for w in {"price", "قیمت"}) and assets:
        return MarketIntent(kind="price", query_asset=assets[0])
    if any(w in words for w in {"status", "today", "وضعیت", "امروز"}) and assets:
        return MarketIntent(kind="status", query_asset=assets[0])
    if amount is None and len(words) <= 3 and assets and assets[0] in CRYPTO_IDS:
        return MarketIntent(kind="price", query_asset=assets[0])
    if amount is not None and assets:
        source = assets[0]
        target = next((asset for asset in assets[1:] if asset != source), None)
        return MarketIntent(kind="conversion", amount=amount, source=source, target=target)
    return None


class MarketRateService:
    def __init__(self, store: JsonStore | None = None):
        self.store = store
        self._stop = asyncio.Event()

    def bind_store(self, store: JsonStore) -> None:
        self.store = store

    def read_cache(self) -> dict[str, Any]:
        if not self.store:
            return {}
        cache = self.store.get_json(CACHE_KEY, {})
        return cache if isinstance(cache, dict) else {}

    def write_cache(self, cache: dict[str, Any]) -> None:
        if self.store:
            self.store.set_json(CACHE_KEY, cache)

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

    async def refresh(self, settings: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = await asyncio.to_thread(self._fetch_rates, settings)
            old = self.read_cache()
            merged = {**old, **payload, "updated_at": int(time.time()), "last_error": None}
            self.write_cache(merged)
            return merged
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
        rates_usd: dict[str, float] = {"usd": 1.0}
        meta: dict[str, Any] = {"crypto": crypto.get("meta", {}), "fiat": fiat.get("meta", {}), "provider_errors": errors}
        rates_usd.update(crypto.get("rates_usd", {}))
        rates_usd.update(fiat.get("rates_usd", {}))
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
        url = "https://api.coingecko.com/api/v3/simple/price"
        headers = {}
        key = os.getenv("COINGECKO_API_KEY", "").strip()
        if key:
            headers["x-cg-demo-api-key"] = key
        params = {"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"}
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        rates: dict[str, float] = {}
        changes: dict[str, float] = {}
        for symbol, coin_id in CRYPTO_IDS.items():
            item = body.get(coin_id) if isinstance(body, dict) else None
            if not isinstance(item, dict):
                continue
            usd = item.get("usd")
            if isinstance(usd, (int, float)) and usd > 0:
                rates[symbol] = float(usd)
            change = item.get("usd_24h_change")
            if isinstance(change, (int, float)):
                changes[symbol] = float(change)
        return {"rates_usd": rates, "meta": {"24h_change": changes, "source": "coingecko"}}

    def _fetch_exchange_rates(self, settings: dict[str, Any], timeout: float) -> dict[str, Any]:
        key = os.getenv("EXCHANGERATE_API_KEY", "").strip()
        if not key:
            return {"rates_usd": {}, "meta": {"source": "exchangerate", "status": "missing_key"}}
        url = f"https://v6.exchangerate-api.com/v6/{key}/latest/USD"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        rates = body.get("conversion_rates", {}) if isinstance(body, dict) else {}
        out: dict[str, float] = {"usd": 1.0}
        eur = rates.get("EUR")
        irr = rates.get("IRR")
        if isinstance(eur, (int, float)) and eur > 0:
            out["eur"] = 1.0 / float(eur)
        if isinstance(irr, (int, float)) and irr > 0:
            out["irr"] = 1.0 / float(irr)
            out["irt"] = 10.0 / float(irr)
        return {"rates_usd": out, "meta": {"source": "exchangerate", "result": body.get("result")}}


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
    if asset == "stars":
        decimals = 0
    text = f"{value:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def asset_label(asset: str) -> str:
    return {"irt": "toman", "irr": "rial", "usd": "dollar", "eur": "euro", "stars": "STARS"}.get(asset, asset.upper())


def format_timestamp(ts: int) -> str:
    if not ts:
        return "نامشخص"
    try:
        return datetime.fromtimestamp(ts, ZoneInfo("Asia/Tehran")).strftime("%Y/%m/%d | %H:%M:%S")
    except Exception:
        return datetime.utcfromtimestamp(ts).strftime("%Y/%m/%d | %H:%M:%S")


def render_conversion(intent: MarketIntent, settings: dict[str, Any], cache: dict[str, Any]) -> str | None:
    if intent.amount is None or not intent.source:
        return None
    targets = [intent.target] if intent.target else [a for a in settings.get("default_conversion_targets", DEFAULT_CONVERSION_TARGETS) if a != intent.source]
    lines = [f"✨ {format_number(intent.amount, intent.source)} {asset_label(intent.source)} :", ""]
    results = []
    for target in targets:
        if not target or target == intent.source:
            continue
        result = convert_amount(cache, intent.amount, intent.source, target, int(settings.get("stale_ttl_seconds", 3600)))
        if result:
            results.append(result)
            prefix = "💸" if target in {"irt", "irr"} else "💵" if target in {"usd", "eur"} else "🌀"
            lines.append(f"{prefix} {format_number(result.value, target)} {asset_label(target)}")
    if not results:
        return unavailable_message(cache)
    first = results[0]
    if first.stale:
        lines.append("\n⚠️ نرخ‌ها از کش قبلی استفاده شده‌اند.")
    lines.append(f"\n🪙 {format_timestamp(first.updated_at)}")
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
    change = (((cache.get("meta") or {}).get("crypto") or {}).get("24h_change") or {}).get(asset)
    lines = [f"🪙 {asset_label(asset)}", f"💵 ${format_number(float(usd), 'usd')}"]
    if isinstance(change, (int, float)):
        arrow = "📈" if change >= 0 else "📉"
        lines.append(f"{arrow} 24h: {change:+.2f}%")
    if age > 90:
        lines.append("⚠️ نرخ از کش قبلی خوانده شد.")
    lines.append(f"⏱ {format_timestamp(int(cache.get('updated_at') or 0))}")
    return "\n".join(lines)


def unavailable_message(cache: dict[str, Any]) -> str:
    err = cache.get("last_error") if isinstance(cache, dict) else None
    suffix = f"\nجزئیات امن خطا: {err}" if err else ""
    return "⚠️ نرخ معتبر فعلاً در کش بازار موجود نیست. لطفاً کمی بعد دوباره تلاش کنید." + suffix


def render_market_response(text: str, settings: dict[str, Any], cache: dict[str, Any]) -> str | None:
    intent = parse_market_intent(text)
    if not intent:
        return None
    if intent.kind == "conversion":
        return render_conversion(intent, settings, cache)
    if intent.kind in {"price", "status"}:
        return render_price(intent, settings, cache)
    if intent.kind in {"trending", "dominance"}:
        return None
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
    ttl = int(settings.get("cache_ttl_seconds", 60) or 60)
    stale_ttl = int(settings.get("stale_ttl_seconds", 86400) or 86400)
    return {
        "updated_at": updated_at,
        "age": age,
        "rate_count": rate_count,
        "fresh": age is not None and age <= ttl,
        "usable": age is not None and age <= stale_ttl and rate_count > 0,
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
