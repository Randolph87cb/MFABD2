from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation
import weekly_arcade as arcade
from daily_plan import get_daily_plan


FIXTURES = Path(__file__).parent / "fixtures" / "recognition"
WEEK = "2026-10-26"


class WeeklyArcadeTests(unittest.TestCase):
    def test_native_replay_and_adjacent_pages(self):
        for name, expected in (
            ("arcade-list-2304x1296.png", True),
            ("arcade-list-stable-2304x1296.png", True),
            ("arcade-home-entry-2304x1296.png", False),
            ("cabin-own-2304x1296.png", False),
            ("quick-hunt-setup.png", False),
        ):
            with self.subTest(name=name), Image.open(FIXTURES / name) as image:
                self.assertEqual(arcade.recognize_arcade_page(image)[0], expected)
        with Image.open(FIXTURES / "arcade-list-2304x1296.png") as image:
            for candidate in (ImageEnhance.Brightness(image).enhance(0.45), image.resize((1152, 648))):
                self.assertFalse(arcade.recognize_arcade_page(candidate)[0])
            missing = image.copy()
            missing.paste((30, 35, 50), (1950, 30, 2150, 90))
            self.assertFalse(arcade.recognize_arcade_page(missing)[0])

    def test_ocr_errors_low_confidence_conflicts_and_confirmation_are_closed(self):
        with Image.open(FIXTURES / "arcade-list-2304x1296.png") as image:
            with patch.object(arcade, "_ocr_engine", side_effect=RuntimeError("offline")):
                self.assertFalse(arcade.recognize_arcade_page(image)[0])
            texts = [(420, 60, "街机游戏"), (2045, 60, "小游戏排名"),
                     (847, 330, "运行"), (1524, 330, "运行"), (2200, 330, "运行")]
            for entries in (texts + [(1100, 700, "确认")], texts + [texts[0]], texts[:-1]):
                with patch.object(arcade, "_positioned_text", return_value=entries):
                    self.assertFalse(arcade.recognize_arcade_page(image)[0])
            for confidence in (0.94, float("nan")):
                result = SimpleNamespace(boxes=[np.array([[400, 50], [440, 70]])],
                                         txts=["街机游戏"], scores=[confidence])
                with patch.object(arcade, "_ocr_engine") as engine:
                    engine.return_value.return_value = result
                    self.assertEqual(arcade._positioned_text(image), [])

    def run_flow(self, root, *, waits=(True, True, True), weeks=None, dry_run=False, reserve_error=False):
        image = Image.new("RGB", (2304, 1296))
        events = []
        records_at_input = []
        def click(_hwnd, _image, _point, *, key, **kwargs):
            events.append(key)
            if not kwargs.get("dry_run", False):
                records_at_input.append(json.loads((root / "state" / f"{WEEK}.json").read_text(encoding="utf-8")))
        with ExitStack() as stack:
            stack.enter_context(patch.object(arcade, "game_week_key", side_effect=weeks or None,
                                             return_value=WEEK))
            stack.enter_context(patch.object(arcade, "find_game_window", return_value=123))
            stack.enter_context(patch.object(arcade, "prepare_actionable_home", return_value=(True, image, "home")))
            stack.enter_context(patch.object(arcade, "_positioned_text", return_value=[(552, 360, "街机游戏")]))
            stack.enter_context(patch.object(arcade, "wait_for_recognition",
                                             side_effect=[(ok, image, {}) for ok in waits]))
            stack.enter_context(patch.object(arcade, "click_ratio_logged", side_effect=click))
            stack.enter_context(patch.object(arcade.time, "sleep"))
            if reserve_error:
                stack.enter_context(patch.object(arcade, "_write_record", side_effect=FileExistsError("claimed")))
            result = arcade.run_weekly_arcade(dry_run=dry_run, log_root=root / "logs", state_root=root / "state")
        return result, events, records_at_input

    def test_visit_reserves_before_input_and_completes_only_after_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (ok, reason), events, records = self.run_flow(root)
            self.assertTrue(ok, reason)
            self.assertEqual(events, ["weekly_arcade_entry", "arcade_return_home"])
            self.assertTrue(all(r["status"] == "action_pending" for r in records))
            path = root / "state" / f"{WEEK}.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "completed")
            self.assertEqual(len(set(record["visit_screenshots"])), 2)
            self.assertTrue(record["returned_home"])
            before = path.read_bytes()
            for now in (datetime(2026, 10, 27, 9), datetime(2026, 11, 1, 23)):
                with patch.object(arcade, "game_week_key", side_effect=lambda: __import__("weekly_cabin").game_week_key(now)), \
                     patch.object(arcade, "find_game_window") as window:
                    ok, reason = arcade.run_weekly_arcade(dry_run=False, log_root=root / "skip", state_root=path.parent)
                self.assertTrue(ok)
                self.assertTrue(reason.startswith("skipped:"))
                window.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            # A new week chooses its own file and never rewrites the old one.
            with patch.object(arcade, "game_week_key", return_value="2026-11-02"), \
                 patch.object(arcade, "find_game_window", return_value=None) as window:
                self.assertFalse(arcade.run_weekly_arcade(dry_run=False, log_root=root / "new", state_root=path.parent)[0])
                window.assert_called_once()
            self.assertEqual(path.read_bytes(), before)

    def test_no_progress_bad_second_frame_or_return_failure_stays_pending_and_never_replays(self):
        for waits, clicks in (((False,), 1), ((True, False), 1), ((True, True, False), 2)):
            with self.subTest(waits=waits), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, _), events, _records = self.run_flow(root, waits=waits)
                self.assertFalse(ok)
                self.assertEqual(len(events), clicks)
                path = root / "state" / f"{WEEK}.json"
                before = path.read_bytes()
                self.assertEqual(json.loads(before)["status"], "action_pending")
                with patch.object(arcade, "game_week_key", return_value=WEEK), \
                     patch.object(arcade, "find_game_window") as window:
                    self.assertFalse(arcade.run_weekly_arcade(dry_run=False, log_root=root / "retry", state_root=path.parent)[0])
                    window.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_dry_run_reservation_failure_and_week_boundaries_do_not_send_entry(self):
        for kwargs in (dict(dry_run=True), dict(reserve_error=True),
                       dict(weeks=[WEEK, "2026-11-02"]),
                       dict(weeks=[WEEK, WEEK, "2026-11-02"])):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, _), events, records = self.run_flow(root, **kwargs)
                self.assertEqual(ok, kwargs.get("dry_run", False))
                self.assertEqual(records, [])
                self.assertEqual(events, ["weekly_arcade_entry"] if ok else [])
                if ok:
                    self.assertFalse((root / "state").exists())
        for weeks in ([WEEK, WEEK, WEEK, "2026-11-02"], [WEEK, WEEK, WEEK, WEEK, "2026-11-02"]):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, _), _events, _records = self.run_flow(root, weeks=weeks)
                self.assertFalse(ok)
                self.assertEqual(json.loads((root / "state" / f"{WEEK}.json").read_text())["status"], "action_pending")

    def test_corrupt_or_incomplete_records_never_find_window(self):
        completed = dict(version=1, week=WEEK, status="completed", visit_screenshots=["a", "b"],
                         returned_home=True, home_screenshot="home")
        for record in ("{", [], dict(completed, version=True), dict(completed, week="2026-10-19"),
                       dict(completed, visit_screenshots=["a", "a"]), dict(completed, visit_screenshots=[]),
                       dict(completed, returned_home=False), dict(completed, home_screenshot=""),
                       dict(completed, status="action_pending"), dict(completed, status=[])):
            with self.subTest(record=record), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / f"{WEEK}.json"
                path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
                with patch.object(arcade, "game_week_key", return_value=WEEK), \
                     patch.object(arcade, "find_game_window") as window:
                    self.assertFalse(arcade.run_weekly_arcade(dry_run=False, log_root=root / "logs", state_root=root)[0])
                    window.assert_not_called()

    def test_real_weekly_entry_order_skip_merge_and_arcade_failure(self):
        runners = ("run_weekly_cabin", "run_weekly_cabin_second_like", "run_weekly_cabin_third_like",
                   "run_weekly_cabin_fourth_like", "run_weekly_arcade")
        for arcade_ok, arcade_skip in ((True, False), (True, True), (False, False)):
            with self.subTest(ok=arcade_ok, skip=arcade_skip), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                events = []
                stack.enter_context(patch.object(daily_automation, "ensure_home", return_value=(True, "home")))
                for runner in runners:
                    ok = arcade_ok if runner == "run_weekly_arcade" else True
                    skip = arcade_skip if runner == "run_weekly_arcade" else True
                    def operation(*, log_root, name=runner, ok=ok, skip=skip, **_):
                        events.append((name, log_root.name))
                        return ok, "skipped:done" if skip else "visited"
                    stack.enter_context(patch.object(daily_automation, runner, side_effect=operation))
                root = Path(temporary)
                if arcade_ok:
                    reason = daily_automation._execute_daily_phase("weekly", master=daily_automation.MasterLogger(root), run_root=root)
                    self.assertEqual(reason.startswith("skipped:"), arcade_skip)
                else:
                    with self.assertRaises(daily_automation.DailyRunError):
                        daily_automation._execute_daily_phase("weekly", master=daily_automation.MasterLogger(root), run_root=root)
                self.assertEqual([name for name, _ in events], list(runners))
                self.assertEqual(events[-1][1], "06-arcade-visit")
        for preset in ("fast", "detailed"):
            stage = next(s for s in get_daily_plan(preset) if s.id == "weekly")
            self.assertIn("weekly_arcade.run_weekly_arcade", stage.current_runners)
            self.assertEqual(stage.availability, "partial")
            self.assertFalse(stage.implemented)


if __name__ == "__main__":
    unittest.main()
