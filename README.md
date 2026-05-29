# Telegram Business Bot — Dynamic Market & Conversion Engine

Production-oriented Telegram Business Bot with a modular admin panel, DB-backed FSM, inline menu engine, self-bot shortcuts, welcome system, monitoring, formatted/premium-emoji message delivery, and the **Dynamic Market & Conversion Engine**.

> وضعیت اولیه امن است: `global robot status = OFF` و `conversion engine = OFF`. تا وقتی ادمین فعال نکند، موتور بازار پاسخ نمی‌دهد و API updater هم quota مصرف نمی‌کند.

---

## Architecture

### Runtime
- Python async runtime: `python-telegram-bot`
- Storage: SQLite (`bot.db`)
- Settings: DB `kv/settings` + environment `.env`
- Entry point: `bot.py`

### Main modules
- `bot.py` — bot bootstrap, admin panel, delivery flows, callbacks, FSM wiring.
- `features/inline_menu.py` — inline menu admin keyboards and pagination.
- `features/inline_actions.py` — inline button action registry.
- `features/inline_callback.py` — callback namespace helpers and length safety.
- `features/log_export.py` — admin log export helpers.
- `features/market_engine.py` — parser, alias normalization, cache-first rate engine, API clients, conversion rendering, help text.
- `features/market_cards.py` — local Pillow card rendering, branding defaults, theme/watermark/logo support.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # if available, otherwise create .env manually
python bot.py
```

Minimum `.env`:

```env
BOT_TOKEN=
ADMIN_ID=
FORCE_JOIN_CHANNEL=
COINGECKO_API_KEY=
EXCHANGERATE_API_KEY=
MARKET_CACHE_TTL_SECONDS=60
MARKET_STALE_TTL_SECONDS=86400
MARKET_API_TIMEOUT_SECONDS=8
```

API keys are optional at boot. Admins can set and validate them later from the panel.

---

## Global lock behavior

The global robot status is authoritative.

If global status is **OFF**:
- Welcome/self/inline/market features show lock state in the admin panel.
- Conversion Engine is globally inactive.
- Market parsing/replies do not run.
- Background updater does not call APIs.

If global status returns **ON** and Conversion Engine was enabled:
- Lock disappears.
- Cache updater resumes.
- Market parsing and replies resume automatically.

---

## Market cache system

Message handlers **never call external APIs directly**. The flow is:

```text
Background updater -> CoinGecko / ExchangeRate / safe optional sources -> SQLite cache -> handlers read cache only
```

Cache controls:
- `cache_ttl_seconds`: normal refresh interval, clamped between 30 and 3600 seconds.
- `stale_ttl_seconds`: safe stale fallback window, clamped up to 86400 seconds.
- Provider failures are isolated: if CoinGecko fails, ExchangeRate rates can still refresh, and vice versa.
- Existing cached rates are merged with partial successful refreshes to avoid losing unrelated prices.

Fallback behavior:
- Empty/expired cache returns a safe Persian fallback message.
- API exceptions are sanitized before logs/admin display.
- The bot must not crash or freeze because of a market provider failure.

---

## API setup

Open admin panel:

```text
/panel -> Conversion Engine -> Market API Configuration
```

Admin can:
- Enable/disable API updater.
- Enable/disable CoinGecko.
- Enable/disable ExchangeRate.
- Set CoinGecko API key.
- Set ExchangeRate API key.
- Validate saved keys.
- Test live requests.
- View cache status.

When setting a key, the bot performs a real validation request before saving to `.env`:
- Success: key is saved and `os.environ` is updated for current runtime.
- Failure: key is not saved; admin sees the safe failure reason.

Secrets are masked in status output.

---

## Supported aliases

Examples:

| Alias | Normalized asset |
| --- | --- |
| `ترون`, `trx`, `tron` | `trx` |
| `تون`, `ton`, `the open network` | `ton` |
| `تتر`, `usdt`, `tether` | `usdt` |
| `دلار`, `usd`, `$` | `usd` |
| `تومان`, `تومن`, `toman` | `irt` |
| `ریال`, `rial` | `irr` |
| `استارز`, `stars` | `stars` |
| `بیت کوین`, `bitcoin`, `btc` | `btc` |

Parser supports Persian digits, English digits, decimals, comma-separated numbers, no-space formats, `$100`, and `100$`.

---

## Conversion examples

```text
۲۰۰۰ استارز
2000 stars
۲۰۰۰استارز
1000stars
۲۰ ترون
20 trx
1200000 تومان تتر
100 ترون تومان
100 trx toman
۱ دلار
100 usd trx
100$ trx
$100 trx
۲۰۰۰ استارز ترون
100 eur toman
```

Supported conversion directions:
- crypto -> crypto
- crypto -> fiat
- fiat -> crypto
- fiat -> fiat
- stars -> crypto
- stars -> fiat
- toman -> crypto
- toman -> fiat
- crypto -> toman

---

## Market lookup examples

```text
btc
price trx
eth price
trx status
btc today
trend
top gainers
btc dominance
fear greed
```

Status replies include cached price, 24h percentage change, and 24h high/low when available.

---

## Stars rate system

Telegram Stars do not have a stable public conversion API. The bot uses admin-configured rates:

```text
/panel -> Conversion Engine -> Stars Rate Settings
```

Admin can configure:
- Unit stars, e.g. `1000`.
- Unit USD, e.g. `30`.
- Manual override USD.
- Auto multiplier toggle.

Conversion flow:

```text
stars -> configured USD value -> target asset
```

---

## Branding and image cards

```text
/panel -> Conversion Engine -> Market Branding
/panel -> Conversion Engine -> Theme Settings
```

Admin can configure:
- Card ON/OFF.
- Branding text.
- Branding channel/caption text.
- Watermark text.
- Watermark position: `bottom_right`, `bottom_left`, `top_right`, `top_left`.
- Logo upload and logo ON/OFF.
- Text opacity.
- Theme name.
- Primary/secondary colors.
- Dark mode.
- Preview card.

Image rendering is local via Pillow. If rendering fails, text replies still send successfully.

Logo files are stored under:

```text
assets/market/
```

Preserve that directory if you expect logos to survive deployments.

---

## Admin guide

Primary admin commands:

```text
/panel
panel
/broadcast <text>
/help_market
/help convert
```

Market admin buttons:
- Conversion Engine ON/OFF
- Market API Configuration
- Stars Rate Settings
- Cache Settings
- Market Branding
- Test APIs
- Live Cache Status
- Quick Assets
- Conversion Help
- Market Card Preview
- Theme Settings

Admin UX prefers editing existing admin messages where safe to avoid keyboard spam.

---

## User help

Users can ask:

```text
/help_market
/help convert
help convert
راهنمای تبدیل
راهنمای بازار
```

The help response includes conversion examples, price/status examples, and supported aliases.

---

## Delivery behavior

Market responses use the existing formatted delivery pipeline.

Ordering is intentionally non-invasive:
1. Existing admin/FSM flows.
2. Pending feedback flow.
3. Inline menu commands.
4. Self-bot shortcuts.
5. Market parser/response.
6. Existing offline fallback.

This prevents market replies from shadowing feedback or configured shortcuts.

---

## Troubleshooting

### Conversion replies say cache is unavailable
- Turn global bot ON.
- Turn Conversion Engine ON.
- Ensure API updater is ON.
- Validate API keys in Market API Configuration.
- Open Live Cache Status.
- Use Refresh Cache Now.

### API key does not save
The bot validates keys before saving. If validation fails due to invalid key, network outage, provider downtime, or rate limit, the key is not saved.

### Image cards do not send
- Ensure `Pillow==10.4.0` is installed from `requirements.txt`.
- Ensure Cards are ON in Market Branding.
- If image rendering fails, text replies still send.
- Check logs for `market_card_send_failed`.

### Global OFF but APIs still expected to update
They will not. The background updater obeys global lock and Conversion Engine state.

---

## Programmatic checks

```bash
python -m unittest tests.test_market_engine
python -m py_compile bot.py features/inline_menu.py features/inline_actions.py features/inline_callback.py features/market_engine.py features/market_cards.py tests/test_market_engine.py
git diff --check
```

---

## Production readiness

After Phase 4, the market engine is ready for controlled live testing, not uncontrolled mass rollout. Start with a small admin-controlled test group, verify provider limits, cache status, conversion accuracy, card rendering, and business delivery behavior before wider usage.
