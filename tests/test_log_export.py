import tempfile
import unittest
from pathlib import Path

from features.log_export import build_logs_keyboard, log_callback_payload, resolve_log_callback_path


class LogExportCallbackTests(unittest.TestCase):
    def test_long_log_paths_use_short_resolvable_callback_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            nested = base_dir / "logs" / ("very-long-directory-name-" * 3)
            nested.mkdir(parents=True)
            fp = nested / ("very-long-log-file-name-" * 3 + ".log")
            fp.write_text("hello", encoding="utf-8")

            payload = log_callback_payload(base_dir, fp)
            callback_data = f"admin:log_file:{payload}"

            self.assertLessEqual(len(callback_data.encode("utf-8")), 64)
            self.assertEqual(resolve_log_callback_path(base_dir, payload), fp.resolve())

            markup = build_logs_keyboard(base_dir)
            self.assertTrue(any(button.callback_data == callback_data for row in markup.inline_keyboard for button in row))

    def test_log_callback_rejects_paths_outside_logs_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "logs").mkdir()
            self.assertIsNone(resolve_log_callback_path(base_dir, "../secret.log"))


if __name__ == "__main__":
    unittest.main()
