import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bot


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

    def test_inline_buttons_allow_five_then_twenty_minute_cooldown(self):
        for _ in range(bot.CALLBACK_ALLOWED_INTERACTIONS):
            self.assertFalse(bot.inline_button_rate_limited(101, 77))

        self.assertTrue(bot.inline_button_rate_limited(101, 77))
        row = bot.db.get_user(101)
        remaining = int(row["soft_ban_until"] or 0) - int(__import__("time").time())
        self.assertGreaterEqual(remaining, bot.CALLBACK_COOLDOWN_SECONDS - 2)

    def test_user_buttons_share_same_five_interaction_policy(self):
        for idx in range(bot.CALLBACK_ALLOWED_INTERACTIONS):
            self.assertFalse(bot.user_button_rate_limited(101, f"user:services:{idx}"))

        self.assertTrue(bot.user_button_rate_limited(101, "user:contact"))


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
        ]
        for message, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(bot.message_interaction_text(message), expected)

    def test_welcome_due_uses_first_seen_or_daily_cooldown(self):
        self.assertTrue(bot.is_welcome_due(None))
        self.assertTrue(bot.is_welcome_due({"last_seen_at": 0}))
        self.assertFalse(bot.is_welcome_due({"last_seen_at": int(__import__("time").time())}))


if __name__ == "__main__":
    unittest.main()
