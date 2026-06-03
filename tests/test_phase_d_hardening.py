import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bot
from telegram.error import BadRequest, RetryAfter


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


class DummyCallbackMessage:
    def __init__(self, chat_id=555, business_connection_id=None):
        self.chat = DummyChat(chat_id)
        self.message_id = 33
        self.business_connection_id = business_connection_id
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class DummyCallbackQuery:
    _seq = 0

    def __init__(self, uid, data, message=None, query_id=None):
        DummyCallbackQuery._seq += 1
        self.from_user = SimpleNamespace(id=uid, username=f"u{uid}", full_name=f"User {uid}", first_name="User")
        self.data = data
        self.message = message or DummyCallbackMessage()
        self.answers = []
        self.id = query_id or f"query-id-{DummyCallbackQuery._seq}"

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, *args, **kwargs):
        self.edited_text = (args, kwargs)

    async def edit_message_reply_markup(self, *args, **kwargs):
        self.edited_markup = (args, kwargs)


class CallbackBot(DummyBot):
    async def send_message(self, **kwargs):
        message_id = len(self.sent) + 100
        self.sent.append({**kwargs, "_message_id": message_id})
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(self, **kwargs):
        message_id = kwargs.get("message_id")
        current = next((item for item in self.sent if item.get("_message_id") == message_id), None)
        if current and current.get("text") == kwargs.get("text"):
            raise BadRequest("Message is not modified")
        self.sent.append({"edit": kwargs})

    async def get_business_connection(self, business_connection_id):
        return SimpleNamespace(user=SimpleNamespace(id=42), user_chat_id=42)


class CallbackContext(DummyContext):
    def __init__(self):
        self.bot = CallbackBot()



class CallbackNormalizationAndTelegramAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalized_dispatch_attaches_canonical_v1_payload(self):
        self.assertEqual(bot.normalize_for_dispatch("im_btn:77").canonical, "v1:im:btn:77")
        self.assertEqual(bot.normalize_for_dispatch("inline:root").runtime, "im:root")

    async def test_delete_message_adapter_strips_business_connection_id(self):
        calls = []

        class DeleteOnlyBot:
            async def delete_message(self, **kwargs):
                calls.append(kwargs)

        await bot.tg_delete_message(DeleteOnlyBot(), chat_id=1, message_id=2, business_connection_id="bc")

        self.assertEqual(calls, [{"chat_id": 1, "message_id": 2}])

    async def test_callback_execution_key_uses_message_and_payload(self):
        key1 = bot.callback_execution_key("same", 1, "v1:im:btn:1")
        key2 = bot.callback_execution_key("same", 2, "v1:im:btn:1")
        key3 = bot.callback_execution_key("same", 1, "v1:im:btn:2")

        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key1, key3)


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

    def test_callback_buttons_allow_five_per_button_then_one_hour_ban(self):
        for _ in range(bot.CALLBACK_ALLOWED_INTERACTIONS):
            self.assertFalse(bot.inline_button_rate_limited(101, 77))

        self.assertTrue(bot.inline_button_rate_limited(101, 77))
        row = bot.db.get_user(101)
        remaining_ban = int(row["soft_ban_until"] or 0) - int(__import__("time").time())
        self.assertGreaterEqual(remaining_ban, bot.CALLBACK_COOLDOWN_SECONDS - 2)
        self.assertEqual(int(row["spam_score"] or 0), 1)
        cooldown_until = int(bot.db.get_json(bot.callback_cooldown_key(101, "im:btn:77"), 0) or 0)
        remaining = cooldown_until - int(__import__("time").time())
        self.assertGreaterEqual(remaining, bot.CALLBACK_COOLDOWN_SECONDS - 2)

    def test_inline_and_user_buttons_have_independent_buckets(self):
        for idx in range(3):
            self.assertFalse(bot.inline_button_rate_limited(101, idx))
        for idx in range(2):
            self.assertFalse(bot.user_button_rate_limited(101, f"user:services:{idx}"))

        self.assertFalse(bot.user_button_rate_limited(101, "user:contact"))
        for _ in range(bot.CALLBACK_ALLOWED_INTERACTIONS - 1):
            self.assertFalse(bot.user_button_rate_limited(101, "user:contact"))
        self.assertTrue(bot.user_button_rate_limited(101, "user:contact"))

    def test_menu_session_reset_does_not_clear_per_button_cooldown(self):
        for _ in range(bot.CALLBACK_ALLOWED_INTERACTIONS):
            self.assertFalse(bot.inline_button_rate_limited(101, 77))
        self.assertTrue(bot.inline_button_rate_limited(101, 77))

        bot.reset_callback_session(101)
        self.assertTrue(bot.inline_button_rate_limited(101, 77))


class PhaseDCallbackRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.db
        self.old_load_data = bot.load_data
        bot.BUSINESS_OWNER_CACHE.clear()
        bot.db = bot.DB(Path(self.tmp.name) / "bot.db")
        bot.db.init()
        menu_id = bot.db.create_menu("buy", "preview")
        self.button_id = bot.db.add_menu_button(menu_id, "tariff", "just_text", "payload")
        self.data = {**bot.get_default_data(), "admin_id": 42, "active": True, "inline_menu_enabled": True}
        bot.load_data = lambda: self.data
        bot.CALLBACK_EXECUTION_IDS.clear()
        bot.EDIT_MESSAGE_LOCKS.clear()

    def tearDown(self):
        bot.db = self.old_db
        bot.load_data = self.old_load_data
        bot.BUSINESS_OWNER_CACHE.clear()
        bot.CALLBACK_EXECUTION_IDS.clear()
        bot.EDIT_MESSAGE_LOCKS.clear()
        self.tmp.cleanup()

    async def test_admin_inline_menu_button_bypasses_ban_and_disabled_engine(self):
        self.data["active"] = False
        self.data["inline_menu_enabled"] = False
        bot.db.upsert_user(42, "", "Admin", None, False, "test", "")
        bot.db.set_soft_ban(42, int(__import__("time").time()) + bot.CALLBACK_COOLDOWN_SECONDS)
        q = DummyCallbackQuery(42, f"im:btn:{self.button_id}", DummyCallbackMessage(business_connection_id="bc"))
        context = CallbackContext()

        await bot.callbacks(SimpleNamespace(callback_query=q), context)

        self.assertEqual(q.answers[-1], (None, False))
        self.assertEqual(context.bot.sent[0]["text"], "<b>payload</b>")

    async def test_customer_inline_menu_button_blocks_after_five_clicks_per_button(self):
        bot.db.upsert_user(101, "", "Customer", None, False, "test", "")
        context = CallbackContext()

        for _ in range(bot.CALLBACK_ALLOWED_INTERACTIONS):
            q = DummyCallbackQuery(101, f"im:btn:{self.button_id}")
            await bot.callbacks(SimpleNamespace(callback_query=q), context)
            self.assertEqual(q.answers[-1], (None, False))

        blocked = DummyCallbackQuery(101, f"im:btn:{self.button_id}")
        await bot.callbacks(SimpleNamespace(callback_query=blocked), context)

        self.assertTrue(blocked.answers[-1][1])
        self.assertIn("یک ساعت", blocked.answers[-1][0])
        sends = [item for item in context.bot.sent if "edit" not in item]
        edits = [item for item in context.bot.sent if "edit" in item]
        self.assertEqual(len(sends), bot.CALLBACK_ALLOWED_INTERACTIONS)
        self.assertEqual(len(edits), 0)
        row = bot.db.get_user(101)
        self.assertEqual(int(row["spam_score"] or 0), 1)


    async def test_legacy_inline_button_callback_prefix_still_routes(self):
        bot.db.upsert_user(101, "", "Customer", None, False, "test", "")
        context = CallbackContext()
        q = DummyCallbackQuery(101, f"im_btn:{self.button_id}")

        await bot.callbacks(SimpleNamespace(callback_query=q), context)

        self.assertEqual(q.data, f"im:btn:{self.button_id}")
        self.assertEqual(q.answers[-1], (None, False))
        self.assertEqual(context.bot.sent[0]["text"], "<b>payload</b>")

    async def test_legacy_inline_action_type_alias_still_routes(self):
        with bot.db.conn() as c:
            c.execute("UPDATE menu_buttons SET action_type='send_text' WHERE id=?", (self.button_id,))
        bot.db.upsert_user(101, "", "Customer", None, False, "test", "")
        context = CallbackContext()
        q = DummyCallbackQuery(101, f"im:btn:{self.button_id}")

        await bot.callbacks(SimpleNamespace(callback_query=q), context)

        self.assertEqual(q.answers[-1], (None, False))
        self.assertEqual(context.bot.sent[0]["text"], "<b>payload</b>")


    async def test_duplicate_callback_id_executes_only_once(self):
        bot.db.upsert_user(101, "", "Customer", None, False, "test", "")
        context = CallbackContext()
        q1 = DummyCallbackQuery(101, f"im:btn:{self.button_id}", query_id="same-callback-id")
        q2 = DummyCallbackQuery(101, f"im:btn:{self.button_id}", query_id="same-callback-id")

        await bot.callbacks(SimpleNamespace(callback_query=q1), context)
        await bot.callbacks(SimpleNamespace(callback_query=q2), context)

        self.assertEqual(q1.answers[-1], (None, False))
        self.assertEqual(q2.answers[-1], (None, False))
        self.assertEqual(len([item for item in context.bot.sent if "edit" not in item]), 1)

    async def test_repeated_callback_sends_new_message_without_editing_previous(self):
        bot.db.upsert_user(101, "", "Customer", None, False, "test", "")
        context = CallbackContext()
        q = DummyCallbackQuery(101, f"im:btn:{self.button_id}")
        await bot.callbacks(SimpleNamespace(callback_query=q), context)
        sent_before = len(context.bot.sent)

        async def retry_after_edit(**kwargs):
            raise RetryAfter(198)

        context.bot.edit_message_text = retry_after_edit
        q2 = DummyCallbackQuery(101, f"im:btn:{self.button_id}")
        await bot.callbacks(SimpleNamespace(callback_query=q2), context)

        self.assertEqual(q2.answers[-1], (None, False))
        self.assertEqual(len(context.bot.sent), sent_before + 1)
        self.assertFalse(any("edit" in item for item in context.bot.sent))




class DBImportGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bot.DB_IMPORT_IN_PROGRESS = False
        bot.ACTIVE_UPDATE_TASKS.clear()
        bot.DB_IMPORT_CONDITION = None

    async def asyncTearDown(self):
        bot.DB_IMPORT_IN_PROGRESS = False
        bot.ACTIVE_UPDATE_TASKS.clear()
        if bot.DB_IMPORT_CONDITION is not None:
            async with bot.DB_IMPORT_CONDITION:
                bot.DB_IMPORT_CONDITION.notify_all()

    async def test_begin_db_import_blocks_new_handlers_until_active_handlers_drain(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def active_handler():
            self.assertTrue(await bot.enter_update_handler())
            entered.set()
            try:
                await release.wait()
            finally:
                await bot.leave_update_handler()

        active_task = asyncio.create_task(active_handler())
        await entered.wait()
        begin_task = asyncio.create_task(bot.begin_db_import())
        await asyncio.sleep(0)

        self.assertTrue(bot.DB_IMPORT_IN_PROGRESS)
        self.assertFalse(await bot.enter_update_handler())

        release.set()
        drained = await begin_task
        self.assertEqual(drained, [])
        await active_task
        await bot.finish_db_import()
        self.assertTrue(await bot.enter_update_handler())
        await bot.leave_update_handler()


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




class PhaseDBusinessAdminMenuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.db
        self.old_load_data = bot.load_data
        bot.db = bot.DB(Path(self.tmp.name) / "bot.db")
        bot.db.init()
        bot.db.create_menu("buy", "preview")
        bot.db.add_menu_button(1, "tariff", "just_text", "payload")
        self.data = {**bot.get_default_data(), "admin_id": 42, "active": True, "inline_menu_enabled": True}
        bot.load_data = lambda: self.data
        bot.CALLBACK_EXECUTION_IDS.clear()
        bot.EDIT_MESSAGE_LOCKS.clear()

        async def no_watch(*args, **kwargs):
            return None

        self.old_watch = bot.maybe_report_watch_hit
        bot.maybe_report_watch_hit = no_watch

    def tearDown(self):
        bot.db = self.old_db
        bot.load_data = self.old_load_data
        bot.maybe_report_watch_hit = self.old_watch
        self.tmp.cleanup()

    async def test_admin_business_menu_without_from_user_deletes_trigger(self):
        message = DummyBusinessMessage(text="buy")
        message.from_user = None
        context = DummyContext()

        async def delete_message(**kwargs):
            context.bot.sent.append({"deleted": kwargs})

        context.bot.delete_message = delete_message
        await bot.business_message_handler(SimpleNamespace(business_message=message), context)

        self.assertEqual(context.bot.sent[0]["deleted"]["message_id"], message.message_id)
        self.assertEqual(context.bot.sent[1]["text"], "<b>preview</b>")

    async def test_customer_business_menu_keeps_trigger(self):
        message = DummyBusinessMessage(text="buy")
        context = DummyContext()

        async def delete_message(**kwargs):
            context.bot.sent.append({"deleted": kwargs})

        context.bot.delete_message = delete_message
        await bot.business_message_handler(SimpleNamespace(business_message=message), context)

        self.assertFalse(any("deleted" in item for item in context.bot.sent))
        self.assertEqual(context.bot.sent[0]["text"], "<b>preview</b>")

    async def test_customer_business_menu_opens_even_with_soft_ban(self):
        bot.db.upsert_user(202, "", "Customer", None, False, "test", "")
        bot.db.set_soft_ban(202, int(__import__("time").time()) + 300)
        message = DummyBusinessMessage(text="buy")
        context = DummyContext()

        await bot.business_message_handler(SimpleNamespace(business_message=message), context)

        self.assertEqual(context.bot.sent[0]["text"], "<b>preview</b>")


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
