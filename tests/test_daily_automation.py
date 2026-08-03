from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_automation import (
    DAILY_READY_STATES,
    claim_daily_run,
    classify_daily_entry_context,
    overlay_transition_succeeded,
    recognize_daily_entry_state,
    return_home_transition_succeeded,
    update_daily_state,
)
from game_text_recognition import recognize_return_home_control
from free_gacha import CLICK_POINTS, is_free_gacha_confirm_transition, safe_capture_client


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "recognition"


class DailyAutomationStateTests(unittest.TestCase):
    def test_second_start_on_same_day_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            run_root = root / "logs" / "first"

            first, _state = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=run_root,
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            second, previous = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "second",
                force=False,
                started_at="2026-07-31T09:00:00",
            )

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(previous["run_root"], str(run_root))

    def test_force_allows_same_day_manual_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "first",
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            claimed, current = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "forced",
                force=True,
                started_at="2026-07-31T09:00:00",
            )

            self.assertTrue(claimed)
            self.assertEqual(current["run_root"], str(root / "logs" / "forced"))

    def test_failure_status_keeps_daily_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "run",
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            update_daily_state(state_path, status="failed", error="login required")
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(state["last_started_date"], "2026-07-31")
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["error"], "login required")


class DailyAutomationEntryRecognitionTests(unittest.TestCase):
    def test_animation_skip_uses_the_safe_left_margin(self) -> None:
        self.assertEqual(CLICK_POINTS["skip_animation"], CLICK_POINTS["dismiss_overlay"])

    def test_daily_run_can_resume_every_supported_gacha_state(self) -> None:
        self.assertTrue(
            {
                "gacha_page",
                "confirm_free_gacha",
                "gacha_animation",
                "gacha_result",
                "gacha_item_overlay",
            }
            <= DAILY_READY_STATES
        )

    def test_v2318_touch_screen_is_actionable(self) -> None:
        with Image.open(FIXTURES / "entry-touch-ready-v2318.png") as image:
            state, details = recognize_daily_entry_state(image)

        self.assertEqual(state, "touch_ready")
        self.assertEqual(details["source"], "ocr")

    def test_capacity_check_screen_is_loading_not_actionable(self) -> None:
        with Image.open(FIXTURES / "entry-loading-capacity-v2318.png") as image:
            state, details = recognize_daily_entry_state(image)

        self.assertEqual(state, "download_waiting")
        self.assertEqual(details["source"], "ocr")

    def test_story_scene_home_button_is_only_a_return_fallback(self) -> None:
        with Image.open(FIXTURES / "entry-story-scene-home-button-v2318.png") as image:
            entry_state, _entry_details = recognize_daily_entry_state(image)
            returnable, details = recognize_return_home_control(image)

        self.assertEqual(entry_state, "unknown")
        self.assertTrue(returnable)
        self.assertIn("H", details["matches"]["home_control"])

    def test_today_plaza_home_button_survives_ocr_variation(self) -> None:
        with Image.open(FIXTURES / "entry-plaza-home-button-v2318-20260803.png") as image:
            state, _details, entry_state, _entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "returnable_scene")
        self.assertEqual(entry_state, "unknown")

    def test_resized_plaza_home_button_overrides_animation_heuristic(self) -> None:
        with Image.open(FIXTURES / "entry-plaza-home-button-3421x1927.png") as image:
            state, _details, entry_state, _entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "returnable_scene")
        self.assertEqual(entry_state, "unknown")

    def test_return_home_transition_frame_is_waited_without_a_second_click(self) -> None:
        with Image.open(FIXTURES / "entry-return-home-transition-3421x1927.png") as image:
            state, _details, entry_state, _entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "unknown")
        self.assertEqual(entry_state, "unknown")
        self.assertTrue(return_home_transition_succeeded(state))

    def test_known_home_overlay_wins_over_title_image_fallback(self) -> None:
        with Image.open(FIXTURES / "entry-home-signin-overlay-v2318.png") as image:
            state, _details, entry_state, entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "home_overlay")
        self.assertEqual(entry_state, "unknown")
        self.assertEqual(entry_details["source"], "deferred")

    def test_changed_stacked_overlay_counts_as_a_successful_dismissal(self) -> None:
        with Image.open(FIXTURES / "entry-home-item-detail-v2318.png") as before:
            with Image.open(FIXTURES / "entry-home-signin-overlay-v2318.png") as after:
                succeeded = overlay_transition_succeeded(before, "home_overlay", after)

        self.assertTrue(succeeded)

    def test_gacha_page_is_a_non_clicking_confirm_transition(self) -> None:
        self.assertTrue(is_free_gacha_confirm_transition("gacha_page"))
        self.assertTrue(is_free_gacha_confirm_transition("gacha_animation"))
        self.assertFalse(is_free_gacha_confirm_transition("confirm_free_gacha"))


class CaptureRecoveryTests(unittest.TestCase):
    @patch("free_gacha.time.sleep")
    @patch("free_gacha.user32")
    @patch("free_gacha.capture_client")
    def test_minimized_window_is_restored_before_capture_retry(
        self,
        capture_client: MagicMock,
        user32: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        capture_client.side_effect = [
            Image.new("RGB", (0, 0)),
            Image.new("RGB", (1000, 600)),
        ]
        user32.GetClientRect.return_value = 1
        user32.IsWindow.return_value = 1
        user32.IsWindowVisible.return_value = 1
        user32.IsIconic.return_value = 1

        image = safe_capture_client(123, attempts=2)

        self.assertEqual(image.size, (1000, 600))
        user32.ShowWindowAsync.assert_called_once_with(123, 4)


if __name__ == "__main__":
    unittest.main()
