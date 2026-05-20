# Business Bot (Telegram)

Production-ready Telegram business bot with:
- One-line installer
- Global `manage` command
- Persistent data (`data.json`) and env config (`.env`)
- Admin panel with feature toggles and text editor
- Optional `systemd` auto-start

## One-line install

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/rezajavadi995/business-bot/main/install.sh)"
```

After installation, run from **any directory**:

```bash
manage
```

---

## `manage` menu (13 options)

1. Set Telegram token (hidden input)
2. Set admin numeric ID
3. Set mandatory join channel (format: `-1003939099054`)
4. Test bot connectivity (sends success message to admin only)
5. Run bot without systemd
6. Enable/start bot with systemd
7. Show systemd status
8. Show bot status
9. Recover/reset systemd
10. Show logs
11. Show errors
12. Full uninstall
13. Exit

---

## Bot commands (BotFather)

- `/start` — welcome + reply keyboard menu
- `/panel` — open admin panel (admin only)
- `/broadcast` — send broadcast (admin only)

You can also type `panel` without slash to open admin panel (admin only).

---

## Admin panel behavior

- Admin panel is hidden from public users.
- `/start` always shows user menu flow.
- Admin panel opens only with `/panel` or `panel`.
- Admin can:
  - enable/disable bot
  - edit runtime texts
  - toggle features
  - see reports

---

## Text editor behavior

From admin panel -> **Edit Texts**:
1. Choose text key
2. Bot shows current (old) value
3. Send new text
4. Bot saves and confirms old/new values

Editable text keys:
- Offline message
- Services text
- Pricing text
- Working hours
- Location text
- FAQ text
- Contact text

---

## Feature system

The bot has 20 runtime feature toggles:
- 10 admin features
- 10 user features

All toggles are available in admin panel -> Features.

---

## Important note about Telegram Business behavior

If your Telegram account is connected to a Business bot, delivery rules depend on Telegram Business settings and update types.
This project handles standard bot updates and interactive flows. If you want auto-replies to incoming business messages from users that never pressed `/start`, your Telegram business routing and permissions must allow forwarding those messages to the bot.

---

## Logs

- Bot log file: `logs/bot.log`
- Service logs: `manage` option 10/11

---

## Data files

- `.env` -> token/admin/channel
- `data.json` -> runtime settings, visitors, feature flags, editable texts
