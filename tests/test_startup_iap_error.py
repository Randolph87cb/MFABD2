from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation as daily

FIXTURES = Path(__file__).parent / "fixtures/recognition"


class StartupIapErrorTests(unittest.TestCase):
    def setUp(self):
        # Retained 2026-11-20 08:51 frame, previously classified as loading.
        with Image.open(FIXTURES / "startup-iap-init-error.png") as source:
            self.image = source.convert("RGB")

    def test_recorded_exception_takes_priority_over_loading_and_touch_background(self):
        with patch.object(daily, "recognize_entry_status") as text, patch.object(daily, "classify_state") as classify:
            state, _, entry, _ = daily.classify_daily_entry_context(self.image)
        self.assertEqual((state, entry), ("entry_screen", "startup_iap_error"))
        text.assert_not_called()
        classify.assert_not_called()

    def test_other_dialogs_missing_controls_and_wrong_size_never_authorize_confirmation(self):
        for box in ((1070, 499, 1240, 542), (910, 573, 1392, 710), (965, 756, 1338, 813)):
            changed = self.image.copy()
            changed.paste("black", box)
            self.assertFalse(daily.recognize_startup_iap_error(changed)[0])
        self.assertFalse(daily.recognize_startup_iap_error(self.image.resize((1152, 648)))[0])
        for name in ("season-loading.png", "startup-pickup-promotion-20261119.png", "season-home-cheer.png"):
            with self.subTest(name=name), Image.open(FIXTURES / name) as image:
                self.assertFalse(daily.recognize_startup_iap_error(image)[0])

    def run_entry(self, *, persists):
        home = Image.new("RGB", self.image.size, "gray")
        error_context = daily.classify_daily_entry_context(self.image)
        contexts = [error_context, error_context if persists else ("real_home", {}, "unknown", {})]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(daily, "find_game_window", return_value=123),
            patch.object(daily, "open_game", return_value=123),
            patch.object(daily, "mute_game_audio", return_value=True),
            patch.object(daily, "safe_capture_client", side_effect=[self.image, self.image if persists else home]),
            patch.object(daily, "classify_daily_entry_context", side_effect=contexts),
            patch.object(daily, "_click_logged_ratio") as click,
            patch.object(daily.time, "sleep"),
            patch("builtins.print"),
        ):
            result = daily.enter_game_logged(timeout=240, log_root=Path(directory))
        click.assert_called_once()
        self.assertEqual(click.call_args.kwargs["key"], "startup_iap_error_confirm")
        self.assertEqual(click.call_args.args[2], (1152 / 2304, 784 / 1296))
        return result

    def test_confirmation_is_followed_by_a_new_observation_and_can_finish_entry(self):
        ok, reason = self.run_entry(persists=False)
        self.assertTrue(ok, reason)

    def test_persistent_error_stops_without_repeated_clicks(self):
        ok, reason = self.run_entry(persists=True)
        self.assertFalse(ok)
        self.assertIn("NoProductsAvailable", reason)


if __name__ == "__main__":
    unittest.main()
