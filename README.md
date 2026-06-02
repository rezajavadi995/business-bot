# Telegram Business Bot

Production-oriented Telegram Business Bot for Telegram Business/private messaging with an admin panel, inline menus, monitoring, welcome messages, formatted replies, and a Dynamic Market & Conversion Engine.

> **Current project status:** Phase A, Phase B, Phase C, and Phase D hardening are implemented in code. This repository still requires owner-run live Telegram validation before any production rollout. Do not treat local tests as proof of live production readiness.

---

## Table of contents

- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Installation](#installation)
- [Manage command](#manage-command)
- [Running modes](#running-modes)
- [Environment variables](#environment-variables)
- [Admin panel](#admin-panel)
- [Messaging lifecycle](#messaging-lifecycle)
- [Market & Conversion Engine](#market--conversion-engine)
- [Branding and market cards](#branding-and-market-cards)
- [Cache, dedupe, and lifecycle safety](#cache-dedupe-and-lifecycle-safety)
- [Welcome system](#welcome-system)
- [Watch/monitoring system](#watchmonitoring-system)
- [Redis preparation](#redis-preparation)
- [Troubleshooting](#troubleshooting)
- [Local checks](#local-checks)
- [Final live-testing checklist](#final-live-testing-checklist)

---

## Architecture

The bot intentionally keeps the current production-oriented architecture instead of using a clean-slate rewrite.

### Runtime

- **Language/runtime:** Python async runtime.
- **Telegram framework:** `python-telegram-bot`.
- **Primary storage:** SQLite database at `bot.db`.
- **Runtime settings:** DB-backed `kv` settings merged with `.env` values.
- **Entrypoint:** `bot.py`.
- **Market rendering:** Pillow-based local image generation.
- **Logging:** `logs/bot.log` plus systemd journal when systemd mode is enabled.

### Main flow boundaries

1. **Parser layer**
   - Strictly parses market/conversion intents.
   - Normalizes aliases, Persian digits, and compact formats.
   - Rejects natural language, duplicate aliases, and multi-intent collisions.

2. **Engine/cache layer**
   - Owns cache refresh, stale-cache detection, provider calls, validation, and rate snapshots.
   - Handlers read cache snapshots instead of calling APIs directly.

3. **Delivery/routing layer**
   - Routes business, private, group, channel, edited-message, callback, admin, FSM, and feedback paths.
   - Enforces dedupe and sends exactly one market delivery mode per consumed market message.

4. **UI/card layer**
   - Owns admin keyboards, inline menu configuration, branding settings, font/theme/palette previews, and market image rendering.

---

## Repository layout

```text
.
├── bot.py                         # Bot bootstrap, DB wrapper, admin panel, routing, callbacks, delivery
├── install.sh                     # Server installer and manage-command installer
├── requirements.txt               # Python dependencies
├── README.md                      # This document
├── features/
│   ├── inline_actions.py           # Inline button action registry
│   ├── inline_callback.py          # Callback namespace and 64-byte safety helpers
│   ├── inline_menu.py              # Inline menu admin keyboards and pagination
│   ├── log_export.py               # Log listing/export helpers
│   ├── market_cards.py             # Market card renderer, themes, palettes, fonts, logo support
│   └── market_engine.py            # Parser, aliases, cache service, providers, conversion renderer
└── tests/
    ├── test_market_cards.py        # Branding/card rendering tests
    ├── test_market_engine.py       # Parser/engine/cache tests
    └── test_phase_d_hardening.py   # Phase D callback/welcome lifecycle tests
```

Runtime-created paths:

```text
bot.db                 # SQLite database
.env                   # Secrets and environment configuration
logs/bot.log           # App log file
assets/market/         # Uploaded market-card logos/assets
.venv/                 # Python virtual environment
```

---

## Installation

### One-liner installer

For a fresh Debian/Ubuntu server, run the installer as root from a trusted checkout or the published repository URL:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/rezajavadi995/business-bot/main/install.sh)"
```

Alternative local install from a cloned checkout:

```bash
git clone https://github.com/rezajavadi995/business-bot.git
cd business-bot
sudo bash install.sh
```

The installer:

- Checks OS, architecture, disk space, and internet connectivity.
- Repairs common `apt/dpkg` lock/state issues.
- Installs Python, pip, venv support, git, curl, and base dependencies.
- Creates `.venv`.
- Installs `requirements.txt`.
- Ensures `.env` contains `BOT_TOKEN` and `ADMIN_ID` keys.
- Installs the global `/usr/local/bin/manage` command.

### Manual development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
cp .env.example .env 2>/dev/null || touch .env
```

Minimum `.env`:

```env
BOT_TOKEN=123456:telegram-token
ADMIN_ID=123456789
FORCE_JOIN_CHANNEL=
```

Then start locally:

```bash
python bot.py
```

---

## Manage command

The installer creates a global command:

```bash
manage
```

`manage` can be run from anywhere and points back to the installed project directory. Menu options include:

1. Set Telegram bot token.
2. Set admin numeric ID.
3. Set mandatory join channel.
4. Send a test message to admin.
5. Run bot without systemd.
6. Enable and start systemd service.
7. Show systemd status.
8. Show bot process/service status.
9. Recover/reset systemd service.
10. Show recent logs.
11. Show recent errors.
12. Full uninstall.
13. Exit.

### Symlink/global usage note

`install.sh` writes an executable management script to:

```text
/usr/local/bin/manage
```

Because `/usr/local/bin` is normally on `PATH`, this behaves like a global symlink/launcher for the project. If a server image does not include `/usr/local/bin` in `PATH`, run it directly:

```bash
/usr/local/bin/manage
```

---

## Running modes

### Systemd mode

Recommended for persistent server operation:

```bash
manage
# choose option 6: Enable and start systemd
```

Useful systemd commands:

```bash
systemctl status business-bot.service --no-pager
journalctl -u business-bot.service -n 300 --no-pager
journalctl -u business-bot.service -p err -n 200 --no-pager
systemctl restart business-bot.service
```

The generated service uses:

```text
WorkingDirectory=<project_dir>
ExecStart=<project_dir>/.venv/bin/python <project_dir>/bot.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
```

### Non-systemd mode

Useful for local development, debugging, or temporary shell sessions:

```bash
manage
# choose option 5: Run bot without systemd
```

Or manually:

```bash
cd /path/to/business-bot
source .venv/bin/activate
python bot.py
```

---

## Environment variables

Core variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Telegram bot token. |
| `ADMIN_ID` | Yes | Numeric Telegram user ID for the primary admin. |
| `FORCE_JOIN_CHANNEL` | Optional | Mandatory channel ID used by join-gate features when configured. |

Market/provider variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `COINGECKO_API_KEY` | Optional | CoinGecko API key for market data. Can be set from admin panel. |
| `EXCHANGERATE_API_KEY` | Optional | ExchangeRate API key for fiat rates. Can be set from admin panel. |
| `MARKET_CACHE_TTL_SECONDS` | Optional | Normal cache refresh interval. |
| `MARKET_STALE_TTL_SECONDS` | Optional | Maximum stale fallback window. |
| `MARKET_API_TIMEOUT_SECONDS` | Optional | Provider request timeout. |

Future Redis-related variables are intentionally not required yet. See [Redis preparation](#redis-preparation).

---

## Admin panel

Open the panel as the configured admin:

```text
/panel
panel
```

Major admin areas:

- Global bot ON/OFF.
- Text editing and feature toggles.
- Self-bot shortcut management.
- Welcome toggle and welcome text configuration.
- Inline Menu Engine.
- Conversion Engine / market settings.
- Market API key setup and validation.
- Market cache controls.
- Market branding/card settings.
- Database export/import.
- Log viewer/export.
- Feedback messages.
- User report/status.

Global OFF is authoritative for non-admin behavior. Market parsing/replies and API updater activity remain locked unless the global bot and Conversion Engine are enabled.

---

## Messaging lifecycle

### Business messages

Business messages pass through:

1. Freshness guard for stale Telegram business updates.
2. Watch/monitoring report check.
3. Global active/admin guard.
4. Soft-ban guard.
5. User upsert/activity tracking.
6. Inline menu command handling.
7. Market response handling.
8. Self-bot shortcut handling.
9. Welcome first-interaction fallback when no higher-priority flow consumed the update.
10. Final no-match log.

Business welcome detection now supports text/caption, sticker, voice, voice note/video note, video, GIF/animation, document, photo, media-group, audio, contact, and location interactions. Welcome is intentionally deferred until after market/menu/shortcut routing to prevent duplicate responses for valid commands such as `btc`.

### Private messages

Private messages preserve admin/FSM precedence:

1. DB import/document flow.
2. Admin panel commands.
3. Help commands.
4. Admin FSM states.
5. Soft-ban guard.
6. User upsert/activity tracking.
7. Inline menu commands.
8. User menu.
9. Market response.
10. Spam/feedback/self-bot/offline fallback.

### Group/channel messages

- Group and supergroup messages first run watch/monitoring checks.
- Group market parsing then runs with group-scoped dedupe.
- Channel posts are monitored.
- Edited channel posts are monitored as `channel_edit`.

### Edited messages

Edited message behavior is controlled by the market setting `market_process_edited_messages`.

- If disabled, edited market messages are ignored by the market pipeline.
- If enabled, edited messages use the same message ID and source-aware dedupe key.
- Edited group messages preserve watch/monitoring routing when not consumed by market processing.
- Edited private messages can continue through normal private routing when not consumed by market processing.

---

## Market & Conversion Engine

### Parser hardening

Supported examples:

```text
btc
trx
100trx
100 trx
100 trx toman
2000stars
2000 استارز
۲۰۰۰ استارز
```

The parser intentionally rejects natural sentences such as a normal invoice/message containing a market keyword. It also rejects ambiguous multi-intent or duplicate-alias input.

Supported alias families include:

| Alias examples | Asset |
| --- | --- |
| `btc`, `bitcoin`, `بیت کوین` | BTC |
| `trx`, `tron`, `ترون` | TRX |
| `ton`, `تون` | TON |
| `usdt`, `tether`, `تتر` | USDT |
| `usd`, `$`, `دلار` | USD |
| `toman`, `تومان`, `تومن` | IRT |
| `rial`, `ریال` | IRR |
| `stars`, `استارز` | Telegram Stars |

### API and cache flow

Handlers do not directly depend on live provider calls. The intended data path is:

```text
Background updater / refresh_if_needed
        -> provider clients
        -> validated cache snapshot
        -> render_market_response
        -> exactly one delivery path
```

Admin path:

```text
/panel -> Conversion Engine -> Market API Configuration
```

Admin can:

- Enable/disable API updater.
- Enable/disable providers.
- Set CoinGecko API key.
- Set ExchangeRate API key.
- Validate saved keys.
- Run test requests.
- View live cache status.
- Force refresh cache.

### Delivery modes

When market cards are enabled:

```text
one photo + caption
```

When market cards are disabled:

```text
one formatted text reply
```

If card rendering or photo delivery fails, the bot falls back to one text message.

---

## Branding and market cards

Admin path:

```text
/panel -> Conversion Engine -> Market Branding
```

Branding supports:

- Card ON/OFF.
- Branding text.
- Branding channel/caption text.
- Channel validation.
- Logo upload/remove.
- Logo ON/OFF.
- Watermark text.
- Watermark positions.
- Theme selection.
- Palette selection.
- Independent Persian/English fonts.
- Independent Persian/English bold settings.
- Text opacity controls.
- Preview/confirmation flows.

The renderer includes RTL support through `arabic_reshaper` and `python-bidi` when available, with fallback behavior if shaping dependencies are unavailable.

Runtime setting changes are DB-backed and should apply without restarting the bot.

---

## Cache, dedupe, and lifecycle safety

### Market dedupe

Market messages use a source-aware dedupe key:

```text
market_processed:<source>:<chat_id>:<message_id>
```

This prevents duplicate market delivery for the same Telegram message while allowing later messages from the same user to be processed normally.

### Watch dedupe

Watch hits use source/chat/message/keyword dedupe to avoid repeated monitoring reports for the same watched event.

### Callback cooldown

Non-admin inline-menu callback users are allowed 5 clicks per individual menu button in each one-hour cooldown window. On the next click of that same button, the user receives a one-hour soft-ban alert. Admin users are exempt from this callback limit.

Callback hardening also keeps callback data within Telegram’s 64-byte limit, avoids disabling working keyboards just because one user hits a cooldown, and keeps each menu button in its own click bucket.

### Cache consistency

The market service uses refresh locking and cache usability checks to avoid stale or partially invalid provider data being treated as fresh. Manual-only rates do not make the cache fresh unless required external rates are present.

---

## Welcome system

Welcome is DB-backed and controlled from the admin panel:

```text
/panel -> Welcome toggle
/panel -> Welcome configuration
```

Detection covers the first business interaction after the welcome cooldown, including:

- Text.
- Sticker.
- Voice.
- Voice note / video note.
- Video.
- GIF / animation.
- Document.
- Photo.
- Media groups.
- Audio.
- Contact.
- Location.

The default welcome cooldown is 24 hours per tracked user.

---

## Watch/monitoring system

The watch system can report keyword hits from:

- Business private messages.
- Groups/supergroups.
- Channel posts.
- Edited channel posts.
- Edited group messages when market processing does not consume them.

Reports include source, keyword, user/chat metadata, total keyword hit count, and forwarded message context when forwarding is possible.

---

## Redis preparation

Redis is not required by the current runtime and no destructive Redis migration is included.

The current project already uses storage boundaries that can be extended later. A future Redis implementation should preserve SQLite compatibility and support:

- Cache persistence.
- Dedupe persistence.
- TTL-backed keys.
- Distributed locking for multi-process deployments.
- Redis enable/disable from admin/server menus.
- Redis install/configuration from admin-server tooling.
- Redis credential prompt/validation/save flow.
- Runtime configuration viewer with masked secrets.

Expected future configuration visibility:

- Admin ID.
- Force-join channel ID.
- Redis configuration and URI.
- API settings and provider status.
- Masked API keys.
- Important runtime settings.

---

## Troubleshooting

### Bot does not start

Check:

```bash
source .venv/bin/activate
python -m py_compile bot.py
python bot.py
```

Common causes:

- `BOT_TOKEN` missing.
- Dependencies not installed.
- Wrong Python/venv path.
- Broken `.env` file.

### Systemd service fails

```bash
systemctl status business-bot.service --no-pager
journalctl -u business-bot.service -p err -n 200 --no-pager
manage
# choose option 9: recover/reset
```

### Conversion replies say cache is unavailable

Check:

- Global bot status is ON.
- Conversion Engine is ON.
- API updater is ON.
- Provider keys are saved and valid if required.
- Live cache status is fresh.
- Force refresh cache from admin panel.

### Market cards do not send

Check:

- `Pillow` is installed from `requirements.txt`.
- Market cards are enabled.
- Font files/fallback fonts are available.
- Logo file still exists under `assets/market/` if logo is enabled.
- Logs for `market_card_send_failed`.

### Callback users get cooldown alerts

Non-admin users get 5 allowed clicks per inline-menu button per one-hour window. Admin users are exempt. If a regular user exceeds the per-button limit, they receive a one-hour soft-ban alert and must wait for the cooldown to expire.

### Welcome does not send on media

Check:

- Global bot status is ON for non-admin users.
- Welcome is enabled.
- User is not soft-banned.
- The user has not received welcome within the 24-hour cooldown window.
- The update is a business message update received by the bot.

---

## Local checks

Recommended local checks before any handoff:

```bash
python3 -m unittest tests.test_market_engine tests.test_market_cards tests.test_phase_d_hardening
python3 -m py_compile bot.py features/inline_menu.py features/inline_actions.py features/inline_callback.py features/market_engine.py features/market_cards.py tests/test_market_engine.py tests/test_market_cards.py tests/test_phase_d_hardening.py
git diff --check
```

These checks are local and simulated; they do not replace real Telegram API/live-flow validation.

---

## Final live-testing checklist

Before any production-style rollout, the owner should validate in a controlled Telegram environment:

- `btc` produces exactly one market response.
- `1 trx` produces exactly one conversion response.
- `100 trx toman` produces exactly one conversion response.
- Card ON sends one image with caption and no second text message.
- Card OFF sends one text message.
- Edited messages obey `market_process_edited_messages`.
- Edited group messages still trigger watch/monitoring when not consumed by market processing.
- Inline/menu buttons allow repeated normal use and enforce cooldown only after the configured limit.
- Admin callbacks are not rate-limited.
- Welcome sends on first supported media/text interaction after cooldown.
- Branding, fonts, palettes, logo upload/remove, and previews persist across runtime changes.
