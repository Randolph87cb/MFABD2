from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from daily_automation import DailyRunError, MasterLogger, _execute_daily_phase
from daily_plan import get_daily_plan
from quick_hunt import recognize_free_rice, run_rice_recheck


FIXTURES = Path(__file__).parent / "fixtures" / "recognition"


class QuickHuntRiceTests(unittest.TestCase):
    def test_rice_screenshot_replay(self):
        cases = [
            ("quick-hunt-map.png", 90),
            ("quick-hunt-rice-center-full.png", 90),
            ("quick-hunt-rice-center-empty.png", 0),
            ("quick-hunt-rice-right-empty.png", 0),
            ("quick-hunt-map-crystal-selected.png", None),
            ("quick-hunt-setup.png", None),
            ("quick-hunt-result.png", None),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename), Image.open(FIXTURES / filename) as image:
                balance, details = recognize_free_rice(image)
                self.assertEqual(balance, expected, details)

    def test_right_rice_counter_can_shift_without_being_truncated(self):
        with Image.open(FIXTURES / "quick-hunt-rice-right-empty.png") as source:
            image = source.convert("RGB")
        shift = round(image.width * 0.016)
        top = round(image.height * 0.038)
        bottom = round(image.height * 0.068)
        image.paste(image.crop((0, top, image.width - shift, bottom)), (shift, top))

        balance, details = recognize_free_rice(image)

        self.assertEqual(balance, 0, details)

    def test_uncertain_rice_is_not_a_zero_balance(self):
        cases = [
            [([], []), ([], [])],
            [(["0/90"], [0.94]), ([], [])],
            [(["0/90"], [float("nan")]), ([], [])],
            [(["0/90"], []), (["0/90"], [0.99])],
            [(["90/90"], [0.99]), (["0/90"], [0.99])],
            [(["06/06"], [0.99]), ([], [])],
            [([], []), (["0/9"], [0.99])],
            [(["91/90"], [0.99]), ([], [])],
            [(["0/60"], [0.99]), ([], [])],
            [(["0/90+8.95K"], [0.99]), ([], [])],
            [(["00/90"], [0.99]), ([], [])],
        ]
        for observations in cases:
            results = [SimpleNamespace(txts=texts, scores=scores) for texts, scores in observations]
            with self.subTest(observations=observations), patch(
                "quick_hunt.classify_state", return_value=("quick_hunt_map", {})
            ), patch(
                "quick_hunt.detect_selected_quick_hunt_category", return_value=("hunting_ground", {})
            ), patch("quick_hunt._rice_ocr_engine") as engine:
                engine.return_value.side_effect = results
                balance, _details = recognize_free_rice(Image.new("RGB", (2000, 1000)))
                self.assertIsNone(balance)

    def test_ocr_failure_is_closed(self):
        with patch("quick_hunt.classify_state", return_value=("quick_hunt_map", {})), patch(
            "quick_hunt.detect_selected_quick_hunt_category", return_value=("hunting_ground", {})
        ), patch("quick_hunt._rice_ocr_engine", side_effect=RuntimeError("offline")):
            balance, details = recognize_free_rice(Image.new("RGB", (2000, 1000)))
        self.assertIsNone(balance)
        self.assertIn("offline", details["error"])

    def test_recheck_never_allocates_or_consumes_rice(self):
        for balances, expected in [([0, 0], True), ([12, 12], True), ([0, 1], False), ([None], False)]:
            image = Image.new("RGB", (2000, 1000))
            with self.subTest(balances=balances), tempfile.TemporaryDirectory() as temporary, patch(
                "quick_hunt.find_game_window", return_value=123
            ), patch("quick_hunt.safe_capture_client", return_value=image), patch(
                "quick_hunt.classify_state", return_value=("quick_hunt_map", {})
            ), patch(
                "quick_hunt.recognize_free_rice", side_effect=[(balance, {}) for balance in balances]
            ), patch("quick_hunt.click_with_fixed_retry") as click:
                ok, reason = run_rice_recheck(dry_run=False, log_root=Path(temporary))
                self.assertEqual(ok, expected, reason)
                click.assert_not_called()

    def test_reward_dismissal_is_the_only_allowed_click(self):
        for result_state, click_ok, expected in [("quick_hunt_map", True, True), ("unknown", False, False)]:
            image = Image.new("RGB", (2000, 1000))
            with self.subTest(state=result_state), tempfile.TemporaryDirectory() as temporary, patch(
                "quick_hunt.find_game_window", return_value=123
            ), patch("quick_hunt.safe_capture_client", return_value=image), patch(
                "quick_hunt.classify_state", return_value=("reward_overlay", {})
            ), patch("quick_hunt.recognize_free_rice", return_value=(0, {})), patch(
                "quick_hunt.click_with_fixed_retry", return_value=(click_ok, result_state, image, "dismiss")
            ) as click:
                ok, _reason = run_rice_recheck(dry_run=False, log_root=Path(temporary))
                self.assertEqual(ok, expected)
                self.assertEqual([call.args[2] for call in click.call_args_list], ["reward_overlay_dismiss"])

    def test_unknown_page_never_clicks(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "quick_hunt.find_game_window", return_value=123
        ), patch("quick_hunt.safe_capture_client", return_value=Image.new("RGB", (2000, 1000))), patch(
            "quick_hunt.classify_state", return_value=("unknown", {})
        ), patch("quick_hunt.click_with_fixed_retry") as click:
            ok, _reason = run_rice_recheck(dry_run=False, log_root=Path(temporary))
        self.assertFalse(ok)
        click.assert_not_called()

    def test_daily_rice_gate_runs_before_crystal_and_stops_on_failure(self):
        for recheck_ok in [True, False]:
            calls = []

            def operation(name, ok=True):
                def run(**kwargs):
                    calls.append(name)
                    return ok, name
                return run

            with self.subTest(recheck_ok=recheck_ok), tempfile.TemporaryDirectory() as temporary, patch(
                "daily_automation.ensure_home", operation("home")
            ), patch("daily_automation.enter_quick_hunt", operation("entry")), patch(
                "daily_automation.start_selected_quick_hunt", operation("setup")
            ), patch("daily_automation.maximize_and_confirm_quick_hunt", operation("hunt")), patch(
                "daily_automation.run_rice_recheck", operation("rice", recheck_ok)
            ), patch("daily_automation.run_crystal_cave_cycle", operation("crystal")):
                root = Path(temporary)
                master = MasterLogger(root)
                if recheck_ok:
                    _execute_daily_phase("quick_hunt", master=master, run_root=root)
                else:
                    with self.assertRaises(DailyRunError):
                        _execute_daily_phase("quick_hunt", master=master, run_root=root)
            self.assertEqual(calls, ["home", "entry", "setup", "hunt", "rice"] + (["crystal"] if recheck_ok else []))
        stage = next(stage for stage in get_daily_plan("fast") if stage.id == "quick_hunt")
        self.assertEqual(stage.availability, "partial")
        self.assertIn("quick_hunt.run_rice_recheck", stage.current_runners)


if __name__ == "__main__":
    unittest.main()
