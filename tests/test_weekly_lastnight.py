from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation
import weekly_lastnight as flow
from daily_plan import get_daily_plan


FIXTURES = Path(__file__).parent / "fixtures" / "recognition"
WEEK = "2026-11-02"


class LastNightTests(unittest.TestCase):
    def test_native_pages_and_adjacent_negatives(self):
        cases = {"collection": "collection", "collection-selected": "collection",
                 "field": "field_spawn", "ready": "ready", "ready-return": "ready",
                 "battle": "battle", "result": "result", "result-stable": "result",
                 "help": "unknown", "home": "unknown"}
        for name, expected in cases.items():
            with self.subTest(name=name), Image.open(FIXTURES / f"lastnight-{name}-2304x1296.png") as image:
                state, details = flow.recognize_lastnight_page(image)
                self.assertEqual(state, expected, details)
                if state == "result":
                    self.assertEqual(details["damage"], 700068622)
                if state == "collection":
                    x, y = details["point"]
                    self.assertTrue(1300 < x < 1370 and 330 < y < 560)
        for name in ("quick-hunt-setup.png", "cabin-own-2304x1296.png", "arcade-list-2304x1296.png"):
            with Image.open(FIXTURES / name) as image:
                self.assertEqual(flow.recognize_lastnight_page(image)[0], "unknown")
        with Image.open(FIXTURES / "lastnight-home-2304x1296.png") as image:
            self.assertTrue(flow._home(image)[0])

    def test_dimmed_changed_cost_control_and_uncertain_ocr_are_closed(self):
        with Image.open(FIXTURES / "lastnight-ready-2304x1296.png") as image:
            texts = flow._texts(image)
            for entries in (texts + [(1150, 650, "确认", .82)], texts + [texts[0]],
                            [(x, y, t, .94) for x, y, t, _ in texts]):
                with patch.object(flow, "_texts", return_value=entries):
                    self.assertEqual(flow.recognize_lastnight_page(image)[0], "unknown")
            for candidate in (ImageEnhance.Brightness(image).enhance(.45), image.resize((1152, 648))):
                self.assertEqual(flow.recognize_lastnight_page(candidate)[0], "unknown")
            # An added fee icon must reject input even if the normal OCR anchors survive.
            changed = image.copy()
            changed.paste((255, 20, 20), (1820, 1170, 1880, 1220))
            with patch.object(flow, "_texts", return_value=texts):
                self.assertEqual(flow.recognize_lastnight_page(changed)[0], "unknown")
            with patch.object(flow, "_ocr_engine", side_effect=RuntimeError("OCR unavailable")):
                self.assertEqual(flow.recognize_lastnight_page(image)[0], "unknown")

    def run_flow(self, root, *, fail_at=None, conflict=False, reserve_error=False,
                 complete_error=False, dry_run=False, cross_at=None, new_record=False,
                 new_record_damage=700068622):
        events = []
        image = Image.new("RGB", (2304, 1296))
        states = ["collection", "field_spawn", "ready", "battle", "result", "result", "field_spawn", "home"]
        if new_record:
            states.insert(6, "new_record")
        calls = 0
        def wait(*_, **kwargs):
            nonlocal calls
            index = calls
            calls += 1
            damage = new_record_damage if states[index] == "new_record" else (
                700068623 if conflict and index == 5 else 700068622)
            return index != fail_at, image, dict(state=states[index], point=(1330, 360), damage=damage)
        def action(*_, key, **kwargs):
            record = json.loads((root / "state" / f"{WEEK}.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "action_pending")
            events.append(key)
        def write(path, record, *, reserve):
            if reserve_error and reserve:
                raise FileExistsError("claimed")
            if complete_error and not reserve:
                raise OSError("disk error")
            return original_write(path, record, reserve=reserve)
        original_write = flow._write_record
        week_calls = 0
        def week():
            nonlocal week_calls
            week_calls += 1
            return "2026-11-09" if cross_at is not None and week_calls >= cross_at else WEEK
        with ExitStack() as stack:
            stack.enter_context(patch.object(flow, "game_week_key", side_effect=week))
            stack.enter_context(patch.object(flow, "find_game_window", return_value=1))
            stack.enter_context(patch.object(flow, "prepare_actionable_home", return_value=(True, image, "home")))
            stack.enter_context(patch.object(flow, "wait_for_recognition", side_effect=wait))
            stack.enter_context(patch.object(flow, "click_ratio_logged", side_effect=action))
            stack.enter_context(patch.object(flow, "swipe_ratio_logged", side_effect=action))
            stack.enter_context(patch.object(flow, "_write_record", side_effect=write))
            stack.enter_context(patch.object(flow.time, "sleep"))
            result = flow.run_weekly_lastnight(dry_run=dry_run, log_root=root / "logs", state_root=root / "state")
        return result, events

    def test_one_battle_completes_only_after_two_results_and_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (ok, reason), events = self.run_flow(root)
            self.assertTrue(ok, reason)
            self.assertEqual(events, ["lastnight_open_collection", "lastnight_select_cartridge",
                                     "lastnight_walk_to_battle", "lastnight_start_once", "lastnight_skip_animation",
                                     "lastnight_leave_result", "lastnight_return_home"])
            record = json.loads((root / "state" / f"{WEEK}.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "completed")
            self.assertEqual(len(set(record["result_screenshots"])), 2)
            self.assertTrue(record["returned_home"])
            for _ in range(2):  # Same-week calls stop before window lookup, across days too.
                with patch.object(flow, "game_week_key", return_value=WEEK), patch.object(flow, "find_game_window") as window:
                    ok, reason = flow.run_weekly_lastnight(dry_run=False, log_root=root / "skip", state_root=root / "state")
                    self.assertTrue(ok, reason)
                    self.assertTrue(reason.startswith("skipped:"))
                    window.assert_not_called()
            with patch.object(flow, "game_week_key", return_value="2026-11-09"), \
                 patch.object(flow, "find_game_window", return_value=None) as window:
                self.assertFalse(flow.run_weekly_lastnight(dry_run=False, log_root=root / "new", state_root=root / "state")[0])
                window.assert_called_once()

    def test_new_record_dismisses_once_before_home_without_another_battle(self):
        for options in ({}, dict(fail_at=7), dict(new_record_damage=700068623)):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, reason), events = self.run_flow(root, new_record=True, **options)
                self.assertEqual(ok, not options, reason)
                self.assertEqual(events.count("lastnight_start_once"), 1)
                self.assertEqual(events.count("lastnight_dismiss_new_record"),
                                 0 if "new_record_damage" in options else 1)
                record = json.loads((root / "state" / f"{WEEK}.json").read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "action_pending" if options else "completed")
                if options:
                    self.assertNotIn("lastnight_return_home", events)

    def test_native_new_record_requires_full_identity_and_undimmed_badge(self):
        for name in ("lastnight-new-record", "lastnight-new-record-stable"):
            with Image.open(FIXTURES / f"{name}-2304x1296.png") as image:
                state, details = flow.recognize_lastnight_page(image)
                self.assertEqual((state, details.get("damage")), ("new_record", 775872164), details)
                texts = flow._texts(image)
                title = next(e for e in texts if e[2] == "创造新纪录")
                cases = [texts + [title], [e for e in texts if e != title],
                         texts + [(1150, 650, "确认", .99)],
                         [(x, y, t, .94) for x, y, t, _ in texts],
                         [(x, y, t, float("nan")) for x, y, t, _ in texts]]
                for entries in cases:
                    with patch.object(flow, "_texts", return_value=entries):
                        self.assertEqual(flow.recognize_lastnight_page(image)[0], "unknown")
                changed = image.copy()
                changed.paste("black", (1495, 550, 1575, 610))
                for candidate in (changed, ImageEnhance.Brightness(image).enhance(.45),
                                  image.resize((1152, 648))):
                    self.assertEqual(flow.recognize_lastnight_page(candidate)[0], "unknown")

    def seed_pending_result(self, root):
        original = root / "original"
        original.mkdir()
        for index, suffix in ((1, ""), (2, "-stable")):
            shutil.copyfile(FIXTURES / f"lastnight-result-new-record{suffix}-2304x1296.png",
                            original / f"lastnight-result-verified-{index}.png")
        record = dict(version=1, week=WEEK, status="action_pending", log_root=str(original),
                      reserved_at="2026-11-02T09:00:00+08:00")
        path = root / f"{WEEK}.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return path, original

    def recover(self, root, *, home=True, dry_run=False, write_error=False, cross=False):
        image = Image.new("RGB", (2304, 1296))
        with ExitStack() as stack:
            stack.enter_context(patch.object(flow, "game_week_key", side_effect=(
                [WEEK, WEEK, WEEK, "2026-11-09"] if cross else None), return_value=WEEK))
            stack.enter_context(patch.object(flow, "find_game_window", return_value=1))
            stack.enter_context(patch.object(flow, "prepare_actionable_home", return_value=(home, image, "home")))
            clicks = stack.enter_context(patch.object(flow, "click_ratio_logged"))
            swipes = stack.enter_context(patch.object(flow, "swipe_ratio_logged"))
            if write_error:
                stack.enter_context(patch.object(flow, "_write_record", side_effect=OSError("disk error")))
            result = flow.run_weekly_lastnight(dry_run=dry_run, log_root=root / "recovered", state_root=root)
            clicks.assert_not_called()
            swipes.assert_not_called()
        return result

    def test_pending_original_results_finish_at_verified_home_without_battle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, original = self.seed_pending_result(root)
            evidence = {p.name: p.read_bytes() for p in original.iterdir()}
            ok, reason = self.recover(root)
            self.assertTrue(ok, reason)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((record["status"], record["damage"], record["returned_home"]),
                             ("completed", 775872164, True))
            self.assertEqual({p.name: p.read_bytes() for p in original.iterdir()}, evidence)
            ok, reason = self.recover(root)
            self.assertTrue(ok, reason)
            self.assertTrue(reason.startswith("skipped:"))

    def test_pending_recovery_requires_results_and_home_and_preserves_failures(self):
        original_week = flow.game_week_key
        # Recognition is covered by the native recovery above. Reuse its parsed
        # result for the failure matrix so it does not repeat expensive OCR.
        for option in (dict(home=False), dict(write_error=True), dict(dry_run=True), dict(cross=True)):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path, _ = self.seed_pending_result(root)
                before = path.read_bytes()
                with patch.object(flow, "recognize_lastnight_page", return_value=("result", dict(damage=775872164))):
                    ok, _ = self.recover(root, **option)
                self.assertEqual(ok, option.get("dry_run", False))
                self.assertEqual(path.read_bytes(), before)
        for kind in ("missing", "corrupt", "not_result", "conflict", "no_log_root", "no_reserved_at", "wrong_week"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary)
                path, original = self.seed_pending_result(root)
                if kind == "missing":
                    (original / "lastnight-result-verified-2.png").unlink()
                elif kind == "corrupt":
                    (original / "lastnight-result-verified-2.png").write_text("invalid", encoding="utf-8")
                elif kind in {"no_log_root", "no_reserved_at", "wrong_week"}:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if kind == "wrong_week":
                        record["reserved_at"] = "2026-10-26T09:00:00+08:00"
                    else:
                        del record[kind[3:]]
                    path.write_text(json.dumps(record), encoding="utf-8")
                states = [("result", dict(damage=775872164)),
                          ("unknown", {}) if kind == "not_result" else ("result", dict(damage=1 if kind == "conflict" else 775872164))]
                stack.enter_context(patch.object(flow, "recognize_lastnight_page", side_effect=states))
                stack.enter_context(patch.object(flow, "game_week_key", side_effect=lambda now=None:
                    original_week(now) if now is not None else WEEK))
                window = stack.enter_context(patch.object(flow, "find_game_window"))
                before = path.read_bytes()
                ok, _ = flow.run_weekly_lastnight(dry_run=False, log_root=root / "failure", state_root=root)
                self.assertFalse(ok)
                window.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_each_navigation_or_result_failure_stops_without_replay(self):
        for index in range(8):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, _), events = self.run_flow(root, fail_at=index)
                self.assertFalse(ok)
                self.assertLessEqual(events.count("lastnight_start_once"), 1)
                record = json.loads((root / "state" / f"{WEEK}.json").read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "action_pending")
                with patch.object(flow, "game_week_key", return_value=WEEK), patch.object(flow, "find_game_window") as window:
                    self.assertFalse(flow.run_weekly_lastnight(dry_run=False, log_root=root / "retry", state_root=root / "state")[0])
                    window.assert_not_called()
        for kwargs in (dict(conflict=True), dict(complete_error=True), dict(cross_at=5)):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.assertFalse(self.run_flow(root, **kwargs)[0][0])
                self.assertEqual(json.loads((root / "state" / f"{WEEK}.json").read_text(encoding="utf-8"))["status"], "action_pending")

    def test_dry_run_reservation_failure_and_invalid_record_do_not_input(self):
        for kwargs in (dict(dry_run=True), dict(reserve_error=True), dict(cross_at=2)):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _, events = self.run_flow(root, **kwargs)
                self.assertEqual(events, [])
                self.assertFalse((root / "state").exists())
        for record in ("{", [], dict(version=True, week=WEEK, status="completed"),
                       dict(version=1, week=WEEK, status="completed")):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / f"{WEEK}.json").write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
                with patch.object(flow, "game_week_key", return_value=WEEK), patch.object(flow, "find_game_window") as window:
                    self.assertFalse(flow.run_weekly_lastnight(dry_run=False, log_root=root / "logs", state_root=root)[0])
                    window.assert_not_called()

    def test_real_stage_order_lastnight_failure_and_skip_merge(self):
        runners = ("run_weekly_cabin", "run_weekly_cabin_second_like", "run_weekly_cabin_third_like",
                   "run_weekly_cabin_fourth_like", "run_weekly_arcade", "run_weekly_lastnight")
        for ok, skip in ((True, True), (True, False), (False, False)):
            with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary)
                events = []
                stack.enter_context(patch.object(daily_automation, "ensure_home", return_value=(True, "home")))
                for runner in runners:
                    def operation(*, log_root, name=runner, **_):
                        events.append((name, log_root.name))
                        return (ok, "skipped:done" if skip else "battle") if name == runners[-1] else (True, "skipped:done")
                    stack.enter_context(patch.object(daily_automation, runner, side_effect=operation))
                if ok:
                    reason = daily_automation._execute_daily_phase("weekly", master=daily_automation.MasterLogger(root), run_root=root)
                    self.assertEqual(reason.startswith("skipped:"), skip)
                else:
                    with self.assertRaises(daily_automation.DailyRunError):
                        daily_automation._execute_daily_phase("weekly", master=daily_automation.MasterLogger(root), run_root=root)
                self.assertEqual([name for name, _ in events], list(runners))
                self.assertEqual(events[-1][1], "07-lastnight")
        for preset in ("fast", "detailed"):
            stage = next(s for s in get_daily_plan(preset) if s.id == "weekly")
            self.assertEqual(stage.current_runners[-1], "weekly_lastnight.run_weekly_lastnight")
            self.assertEqual(stage.availability, "partial")
            self.assertFalse(stage.implemented)


if __name__ == "__main__":
    unittest.main()
