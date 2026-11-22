from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import activity_rewards as activity
import daily_automation as daily
import restaurant_decor as decor

FIXTURES = Path(__file__).parent / "fixtures/recognition"


class DailyVisualReliabilityTests(unittest.TestCase):
    def test_retained_restaurant_title_below_095_is_readable(self):
        with Image.open(FIXTURES / "restaurant-title-low-confidence-2304x1296.png") as image:
            reading, details = decor.recognize_decor_balance(image)
        self.assertEqual(reading, {"balance": 16932, "earned_today": 0, "daily_limit": 20}, details)

    def test_header_recognizes_activity_and_rejects_home_or_body_only(self):
        with Image.open(FIXTURES / "activity-page-misclassified-overlay-2304x1296.png") as image:
            self.assertTrue(activity.recognize_activity_navigation(image)[0])
            obscured = image.convert("RGB")
            obscured.paste("black", (275, 0, 510, 105))
            self.assertFalse(activity.recognize_activity_navigation(obscured)[0])
        with Image.open(FIXTURES / "home-bright-video-background.png") as image:
            self.assertFalse(activity.recognize_activity_navigation(image)[0])

    def test_list_images_detect_movement_even_with_identical_pickup_labels(self):
        with Image.open(FIXTURES / "activity-page-misclassified-overlay-2304x1296.png") as source:
            image = source.convert("RGB")
        self.assertFalse(activity._activity_list_visual_progress(image, image)[0])
        x, y, w, h = activity.ACTIVITY_LIST_VISUAL_REGION
        box = tuple(round(v) for v in (image.width*x, image.height*y,
                                       image.width*(x+w), image.height*(y+h)))
        moved = image.copy()
        moved.paste(ImageChops.offset(image.crop(box), 0, -150), box)
        self.assertTrue(activity._activity_list_visual_progress(image, moved)[0])
        detail_changed = image.copy()
        ImageDraw.Draw(detail_changed).rectangle((700, 300, 2100, 1100), fill="white")
        self.assertFalse(activity._activity_list_visual_progress(image, detail_changed)[0])
        local_changed = image.copy()
        ImageDraw.Draw(local_changed).rectangle((420, 340, 500, 375), fill="white")
        self.assertFalse(activity._activity_list_visual_progress(image, local_changed)[0])

    def test_activity_recovery_precedes_generic_overlay_click(self):
        image = Image.new("RGB", (2304, 1296))
        with tempfile.TemporaryDirectory() as directory, \
                patch("open_game.find_game_window", return_value=123), \
                patch.object(daily, "safe_capture_client", return_value=image), \
                patch.object(daily, "classify_state", return_value=("blocking_ad_overlay", {})), \
                patch.object(daily, "recognize_activity_navigation", return_value=(True, {})), \
                patch.object(daily, "return_from_activity", return_value=(True, "home")) as recover, \
                patch.object(daily, "click_with_fixed_retry") as generic:
            self.assertEqual(daily.ensure_home(timeout=5, log_root=Path(directory)), (True, "home"))
        recover.assert_called_once()
        generic.assert_not_called()

    def test_activity_recovery_does_not_check_reward_completion(self):
        with patch.object(activity, "return_to_home", return_value=(False, "still activity")) as back:
            self.assertEqual(activity.return_from_activity(123, logger=None), (False, "still activity"))
        self.assertNotIn("notification_target", back.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
