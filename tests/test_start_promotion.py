from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_automation import ensure_home
from free_gacha import classify_state
from game_text_recognition import recognize_entry_status


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "recognition" / "startup-pickup-promotion-2304x1296.png"


class StartPromotionTests(unittest.TestCase):
    def test_recorded_post_login_promotion_has_actionable_text(self) -> None:
        # Saved failure frame from 2026-11-05: general recognition is unknown,
        # but the existing entry OCR can identify the Pickup promotion.
        with Image.open(FIXTURE) as image:
            state, _details = classify_state(image)
            entry_state, _entry_details = recognize_entry_status(image)

        self.assertEqual(state, "unknown")
        self.assertEqual(entry_state, "startup_promotion")

    @patch("builtins.print")
    def test_return_home_advances_promotion_after_loading_or_on_resume(self, _print) -> None:
        with Image.open(FIXTURE) as recorded:
            promotion = recorded.convert("RGB")
        home = Image.new("RGB", promotion.size)

        for source in ("loading", "blocking_ad_overlay", "unknown", "gacha_animation"):
            with self.subTest(source=source):
                states = [(source, {}), ("unknown", {}), ("real_home", {})]
                images = [home, promotion, home]
                if source in {"unknown", "gacha_animation"}:
                    states = [(source, {}), ("real_home", {})]
                    images = [promotion, home]

                def click_and_verify(_hwnd, current, key, **kwargs):
                    if key == "dismiss_overlay":
                        return True, "loading", home, "overlay closed"
                    self.assertEqual(key, "startup_promotion")
                    self.assertEqual(kwargs["attempts"], 1)
                    # An unchanged unknown frame must not count as progress.
                    self.assertFalse(kwargs["verify"]("unknown", current.copy()))
                    self.assertTrue(kwargs["verify"]("unknown", home))
                    self.assertTrue(kwargs["verify"]("real_home", home))
                    return True, "real_home", home, "promotion advanced"

                with (
                    tempfile.TemporaryDirectory() as temporary,
                    patch("open_game.find_game_window", return_value=123),
                    patch("daily_automation.time.sleep"),
                    patch("daily_automation.safe_capture_client", side_effect=images),
                    patch("daily_automation.classify_state", side_effect=states),
                    patch("daily_automation.recognize_return_home_control", return_value=(False, {})),
                    patch("daily_automation.click_with_fixed_retry", side_effect=click_and_verify) as click,
                ):
                    ok, reason = ensure_home(timeout=120.0, log_root=Path(temporary))

                self.assertTrue(ok, reason)
                expected = ["dismiss_overlay"] if source == "blocking_ad_overlay" else []
                self.assertEqual([call.args[2] for call in click.call_args_list], expected + ["startup_promotion"])


if __name__ == "__main__":
    unittest.main()
