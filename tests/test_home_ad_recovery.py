from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation as daily
from free_gacha import classify_state
from game_text_recognition import LabelRecognitionSession, recognize_home_ad_labels

FIXTURES = Path(__file__).parent / "fixtures/recognition"


class HomeAdRecoveryTests(unittest.TestCase):
    def test_recorded_ad_wins_over_visible_home_menu_at_multiple_sizes(self):
        with Image.open(FIXTURES / "home-ad-visible-menu-20261126.png") as image:
            for size in (image.size, (1280, 720)):
                with self.subTest(size=size):
                    state, details = classify_state(image.resize(size))
                    self.assertEqual(state, "blocking_ad_overlay")
                    self.assertEqual(details["classification_rule"], "home_ad_suppression_control")

    def test_existing_home_and_adjacent_pages_do_not_become_ads(self):
        for name, expected in (("home-black-video-background.png", "real_home"),
                               ("home-bright-video-background.png", "real_home"),
                               ("quick-hunt-map.png", "quick_hunt_map"),
                               ("entry-home-signin-overlay-v2318.png", "blocking_ad_overlay"),
                               ("home-special-pass-overlay-3421x1927.png", "blocking_ad_overlay"),
                               ("home-promotion-transition-20261126.png", "unknown")):
            with self.subTest(name=name), Image.open(FIXTURES / name) as image:
                self.assertEqual(classify_state(image)[0], expected)

    def test_crop_rereads_when_full_frame_box_misses_control(self):
        with Image.open(FIXTURES / "home-ad-visible-menu-20261126.png") as image:
            session = LabelRecognitionSession(image)
            with patch.object(session, "recognize", return_value=({"suppression": []}, {}, None)):
                matched, details = recognize_home_ad_labels(image, session=session)
        self.assertTrue(matched)
        self.assertTrue(details["crop_reread"])

    def test_ad_artwork_without_suppression_control_is_not_text_evidence(self):
        with Image.open(FIXTURES / "home-ad-visible-menu-20261126.png") as source:
            image = source.convert("RGB")
        image.paste("black", (round(image.width * .60), round(image.height * .10),
                              round(image.width * .85), round(image.height * .30)))
        self.assertFalse(recognize_home_ad_labels(image, session=LabelRecognitionSession(image))[0])

    def recover(self, states, ticks=None):
        image = Image.new("RGB", (384, 216))
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, result in (("recognize_cabin_navigation", ("unknown", {})),
                                 ("_confirm_pass_reward_page", (False, {})),
                                 ("recognize_activity_navigation", (False, {})),
                                 ("recognize_season_navigation", ("unknown", {})),
                                 ("recognize_return_home_control", (False, {})),
                                 ("recognize_entry_status", ("unknown", {}))):
                stack.enter_context(patch.object(daily, name, return_value=result))
            stack.enter_context(patch("open_game.find_game_window", return_value=123))
            stack.enter_context(patch.object(daily, "safe_capture_client", return_value=image))
            stack.enter_context(patch.object(daily, "classify_state", side_effect=[(s, {}) for s in states]))
            stack.enter_context(patch.object(daily.time, "sleep"))
            stack.enter_context(patch("builtins.print"))
            if ticks is not None:
                stack.enter_context(patch.object(daily.time, "monotonic", side_effect=ticks))
            click = stack.enter_context(patch.object(daily, "click_with_fixed_retry",
                                                    return_value=(True, "real_home", image, "closed")))
            ok, reason = daily.ensure_home(timeout=10, log_root=Path(directory))
            return ok, reason, [call.args[2] for call in click.call_args_list]

    def test_resume_observes_promotion_then_dismisses_only_recognized_ad(self):
        ok, reason, clicks = self.recover(["unknown", "gacha_animation", "blocking_ad_overlay", "real_home"])
        self.assertTrue(ok, reason)
        self.assertEqual(clicks, ["dismiss_overlay"])

    def test_alternating_unrecognized_artwork_cannot_extend_stall_deadline(self):
        ok, reason, clicks = self.recover(["unknown", "gacha_animation", "unknown"], [0, 1, 2, 3, 11])
        self.assertFalse(ok)
        self.assertIn("no progress", reason)
        self.assertEqual(clicks, [])


if __name__ == "__main__":
    unittest.main()
