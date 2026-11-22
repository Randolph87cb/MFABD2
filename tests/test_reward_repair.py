"""Offline regressions for dropped input and page-specific recovery."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import activity_rewards as activity
import daily_automation as daily
import pass_rewards as passes
import win32_windowpos_click as mouse

FIXTURES = Path(__file__).parent / "fixtures/recognition"


class RewardRepairTests(unittest.TestCase):
    def test_retained_pass_failure_is_source_task_page_and_not_claim_success(self):
        with Image.open(FIXTURES / "pass-task-claim-unchanged-2304x1296.png") as image:
            self.assertTrue(passes._confirm_pass_reward_page(image)[0])
            self.assertTrue(passes._confirm_pass_task_page(image)[0])
            self.assertFalse(passes._confirm_claim_effect(image)[0])
            self.assertFalse(activity.recognize_activity_navigation(image)[0])
            covered = image.copy()
            covered.paste("black", (300, 0, 600, 130))
            self.assertFalse(passes._confirm_pass_reward_page(covered)[0])
        with Image.open(FIXTURES / "activity-page-misclassified-overlay-2304x1296.png") as image:
            self.assertFalse(passes._confirm_pass_reward_page(image)[0])
        with Image.open(FIXTURES / "home-bright-video-background.png") as image:
            self.assertFalse(passes._confirm_pass_reward_page(image)[0])

    def test_claim_retries_once_only_from_confirmed_task_page(self):
        image = Image.new("RGB", (1000, 600))
        for same_source in (True, False):
            with self.subTest(same_source=same_source), \
                    patch.object(passes, "_click_logged") as click, \
                    patch.object(passes, "_capture_until", side_effect=[
                        (False, image, {}), (True, image, {"reward_overlay": True})]), \
                    patch.object(passes, "_confirm_pass_reward_page", return_value=(same_source, {})), \
                    patch.object(passes, "_confirm_pass_task_page", return_value=(True, {})):
                ok, _, _ = passes._claim_task_rewards(123, image, logger=MagicMock(), label="claim")
                self.assertEqual(ok, same_source)
                self.assertEqual(click.call_count, 2 if same_source else 1)

    def test_missing_button_on_unknown_page_is_not_success(self):
        with patch.object(passes, "_is_reward_overlay", return_value=(False, {})), \
                patch.object(passes, "_confirm_claim_all", return_value=(False, {"available": True})), \
                patch.object(passes, "_confirm_pass_reward_page", return_value=(False, {})):
            self.assertFalse(passes._confirm_claim_effect(Image.new("RGB", (1000, 600)))[0])

    def test_pass_recovery_precedes_overlay_and_does_not_require_rewards_cleared(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("open_game.find_game_window", return_value=123), \
                patch.object(daily, "safe_capture_client", return_value=Image.new("RGB", (1000, 600))), \
                patch.object(daily, "classify_state", return_value=("blocking_ad_overlay", {})), \
                patch.object(daily, "_confirm_pass_reward_page", return_value=(True, {})), \
                patch.object(daily, "return_from_pass", return_value=(True, "home")) as recover, \
                patch.object(daily, "click_with_fixed_retry") as generic:
            self.assertEqual(daily.ensure_home(timeout=5, log_root=Path(directory)), (True, "home"))
            recover.assert_called_once()
            generic.assert_not_called()
        with patch.object(passes, "return_to_home", return_value=(False, "still pass")) as back:
            self.assertEqual(passes.return_from_pass(123, logger=None), (False, "still pass"))
            self.assertNotIn("notification_target", back.call_args.kwargs)

    def test_oversized_window_drag_is_translated_and_restored(self):
        def to_screen(_hwnd, point):
            point._obj.x -= 400
            point._obj.y += 400
            return 1
        with patch.object(mouse, "user32") as api, \
                patch.object(mouse, "get_client_size", return_value=(2304, 1296)), \
                patch.object(mouse.time, "sleep"):
            api.GetSystemMetrics.side_effect = lambda i: {76: 0, 77: 0, 78: 1920, 79: 1080}[i]
            api.ClientToScreen.side_effect = to_screen
            api.GetForegroundWindow.return_value = 123
            mouse.swipe_client_foreground(123, 403, 907, 403, 362, steps=2)
            path = [c.args for c in api.SetCursorPos.call_args_list[:-1]]
            self.assertTrue(all(0 <= x < 1920 and 0 <= y < 1080 for x, y in path), path)
            self.assertEqual(len(api.SetWindowPos.call_args_list), 2)
            self.assertEqual(api.SetWindowPos.call_args_list[-1].args[2:4], (0, 0))

    def test_drag_exceeding_desktop_stops_before_mouse_down(self):
        with patch.object(mouse, "user32") as api, \
                patch.object(mouse, "get_client_size", return_value=(2304, 1296)):
            api.GetSystemMetrics.side_effect = lambda i: {76: 0, 77: 0, 78: 1920, 79: 1080}[i]
            with self.assertRaisesRegex(RuntimeError, "does not fit"):
                mouse.swipe_client_foreground(123, 200, 1200, 200, 100)
            api.mouse_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
