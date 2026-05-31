import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bot


class DummyChat:
    def __init__(self, chat_id=555):
        self.id = chat_id
        self.type = "private"
        self.full_name = "Chat"


class DummyUser:
    id = 202
    username = "user"
    full_name = "User"
    first_name = "User"


class DummyBusinessMessage:
    def __init__(self, text=None, **media):
        self.text = text
        self.caption = None
        self.chat = DummyChat()
        self.from_user = DummyUser()
        self.message_id = 9
        self.business_connection_id = "bc"
        self.replies = []
        for key, value in media.items():
            setattr(self, key, value)

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class DummyBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class DummyContext:
    def __init__(self):
        self.bot = DummyBot()



class MarketSecretBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.db
        self.old_env_path = bot.ENV_PATH
        self.old_token = os.environ.get("BOT_TOKEN")
        self.old_admin = os.environ.get("ADMIN_ID")
        self.old_cg = os.environ.get("COINGECKO_API_KEY")
        self.old_ex = os.environ.get("EXCHANGERATE_API_KEY")
        bot.db = bot.DB(Path(self.tmp.name) / "bot.db")
        bot.db.init()
        bot.ENV_PATH = Path(self.tmp.name) / ".env"
        os.environ["BOT_TOKEN"] = "token-for-secret-tests"
        os.environ["ADMIN_ID"] = "42"

    def tearDown(self):
        bot.db = self.old_db
        bot.ENV_PATH = self.old_env_path
        for key, value in {
            "BOT_TOKEN": self.old_token,
            "ADMIN_ID": self.old_admin,
            "COINGECKO_API_KEY": self.old_cg,
            "EXCHANGERATE_API_KEY": self.old_ex,
        }.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_market_api_secrets_backup_and_restore_without_plaintext(self):
        os.environ["COINGECKO_API_KEY"] = "cg-secret"
        os.environ["EXCHANGERATE_API_KEY"] = "ex-secret"
        bot.backup_market_api_secrets()
        payload = bot.db.get_json("market_api_secrets", {})

        self.assertNotIn("cg-secret", str(payload))
        self.assertNotIn("ex-secret", str(payload))

        os.environ.pop("COINGECKO_API_KEY", None)
        os.environ.pop("EXCHANGERATE_API_KEY", None)
        bot.restore_market_api_secrets()

        self.assertEqual(os.environ["COINGECKO_API_KEY"], "cg-secret")
        self.assertEqual(os.environ["EXCHANGERATE_API_KEY"], "ex-secret")

class PhaseDCallbackLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.db
        bot.db = bot.DB(Path(self.tmp.name) / "bot.db")
        bot.db.init()
        bot.db.upsert_user(101, "", "User", None, False, "test", "")

    def tearDown(self):
        bot.db = self.old_db
        self.tmp.cleanup()

    def test_callback_buttons_allow_five_then_twenty_minute_cooldown_without_global_soft_ban(self):
        for _ in range(bot.CALLBACK_ALLOWED_INTERACTIONS):
            self.assertFalse(bot.inline_button_rate_limited(101, 77))

        self.assertTrue(bot.inline_button_rate_limited(101, 77))
        row = bot.db.get_user(101)
        self.assertEqual(int(row["soft_ban_until"] or 0), 0)
        cooldown_until = int(bot.db.get_json(bot.callback_cooldown_key(101), 0) or 0)
        remaining = cooldown_until - int(__import__("time").time())
        self.assertGreaterEqual(remaining, bot.CALLBACK_COOLDOWN_SECONDS - 2)

    def test_inline_and_user_buttons_share_one_callback_bucket(self):
        for idx in range(3):
            self.assertFalse(bot.inline_button_rate_limited(101, idx))
        for idx in range(2):
            self.assertFalse(bot.user_button_rate_limited(101, f"user:services:{idx}"))

        self.assertTrue(bot.user_button_rate_limited(101, "user:contact"))

    def test_new_menu_session_reset_clears_old_callback_usage(self):
        for _ in range(bot.CALLBACK_ALLOWED_INTERACTIONS):
            self.assertFalse(bot.inline_button_rate_limited(101, 77))
        self.assertTrue(bot.inline_button_rate_limited(101, 77))

        bot.reset_callback_session(101)
        self.assertFalse(bot.inline_button_rate_limited(101, 77))


class PhaseDWelcomeInteractionTests(unittest.TestCase):
    def test_message_interaction_text_covers_required_media_types(self):
        cases = [
            (SimpleNamespace(text="hello", caption=None), "hello"),
            (SimpleNamespace(text=None, caption="caption"), "caption"),
            (SimpleNamespace(text=None, caption=None, sticker=object()), "[sticker]"),
            (SimpleNamespace(text=None, caption=None, voice=object()), "[voice]"),
            (SimpleNamespace(text=None, caption=None, video_note=object()), "[voice note]"),
            (SimpleNamespace(text=None, caption=None, video=object()), "[video]"),
            (SimpleNamespace(text=None, caption=None, animation=object()), "[gif]"),
            (SimpleNamespace(text=None, caption=None, document=object()), "[document]"),
            (SimpleNamespace(text=None, caption=None, photo=[object()]), "[photo]"),
            (SimpleNamespace(text=None, caption=None, media_group_id="album-1"), "[media group]"),
            (SimpleNamespace(text=None, caption=None, audio=object()), "[audio]"),
            (SimpleNamespace(text=None, caption=None, contact=object()), "[contact]"),
            (SimpleNamespace(text=None, caption=None, location=object()), "[location]"),
        ]
        for message, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(bot.message_interaction_text(message), expected)

    def test_welcome_due_uses_first_seen_or_daily_cooldown(self):
        self.assertTrue(bot.is_welcome_due(None))
        self.assertTrue(bot.is_welcome_due({"last_seen_at": 0}))
        self.assertFalse(bot.is_welcome_due({"last_seen_at": int(__import__("time").time())}))


class PhaseDBusinessWelcomeRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.db
        self.old_load_data = bot.load_data
        self.old_watch = bot.maybe_report_watch_hit
        self.old_refresh = bot.MARKET_SERVICE.refresh_if_needed
        self.old_render = bot.render_market_response
        bot.db = bot.DB(Path(self.tmp.name) / "bot.db")
        bot.db.init()
        self.data = {
            "active": True,
            "admin_id": 0,
            "bold_mode": False,
            "welcome_enabled": True,
            "inline_menu_enabled": False,
            "self_bot_enabled": False,
            "market": {"market_engine_enabled": True},
        }
        bot.load_data = lambda: self.data

        async def no_watch(*args, **kwargs):
            return None

        async def fake_refresh(settings):
            return {"rates": {"usd": 1.0, "btc": 50000.0}}

        bot.maybe_report_watch_hit = no_watch
        bot.MARKET_SERVICE.refresh_if_needed = fake_refresh
        bot.render_market_response = lambda text, settings, cache: f"market:{text}"

    def tearDown(self):
        bot.db = self.old_db
        bot.load_data = self.old_load_data
        bot.maybe_report_watch_hit = self.old_watch
        bot.MARKET_SERVICE.refresh_if_needed = self.old_refresh
        bot.render_market_response = self.old_render
        self.tmp.cleanup()

    async def test_business_market_message_does_not_also_send_welcome(self):
        message = DummyBusinessMessage(text="btc")
        context = DummyContext()
        await bot.business_message_handler(SimpleNamespace(business_message=message), context)

        self.assertEqual(len(message.replies), 1)
        self.assertEqual(len(context.bot.sent), 0)

    async def test_business_media_first_interaction_sends_welcome(self):
        message = DummyBusinessMessage(text=None, photo=[object()])
        context = DummyContext()
        await bot.business_message_handler(SimpleNamespace(business_message=message), context)

        self.assertEqual(len(message.replies), 0)
        self.assertEqual(len(context.bot.sent), 1)


class PhaseDDatabaseMigrationTests(unittest.TestCase):
    def test_init_migrates_legacy_tables_without_dropping_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            with sqlite3.connect(db_path) as c:
                c.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT)")
                c.execute("INSERT INTO users(user_id, username) VALUES(?, ?)", (5, "legacy"))
                c.execute("CREATE TABLE menus (id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT UNIQUE NOT NULL)")
                c.execute("INSERT INTO menus(command) VALUES(?)", ("buy",))
                c.execute("CREATE TABLE menu_buttons (id INTEGER PRIMARY KEY AUTOINCREMENT, menu_id INTEGER, button_text TEXT)")
                c.execute("INSERT INTO menu_buttons(menu_id, button_text) VALUES(?, ?)", (1, "tariff"))

            migrated = bot.DB(db_path)
            migrated.init()
            with migrated.conn() as c:
                user_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
                menu_cols = {r["name"] for r in c.execute("PRAGMA table_info(menus)").fetchall()}
                button_cols = {r["name"] for r in c.execute("PRAGMA table_info(menu_buttons)").fetchall()}
                self.assertIn("soft_ban_until", user_cols)
                self.assertIn("preview_text", menu_cols)
                self.assertIn("action_payload", button_cols)
                self.assertEqual(c.execute("SELECT username FROM users WHERE user_id=5").fetchone()["username"], "legacy")
                self.assertEqual(c.execute("SELECT button_text FROM menu_buttons WHERE id=1").fetchone()["button_text"], "tariff")


if __name__ == "__main__":
    unittest.main()
