from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation as daily
import season_activity as season
from daily_plan import get_daily_plan

FIXTURES = Path(__file__).parent / "fixtures/recognition"
READING = dict(stage=1, ap=5, ap_limit=5,
               normal_cost=1, cleared=False, repeat_reward_locked=True, quick_battle=False)


class SeasonRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Image.open(FIXTURES / "season-locked-1.png").convert("RGB")
        cls.entries = season._entries(cls.source)

    def test_native_main_locked_and_adjacent_pages(self):
        for name, expected in (("season-main", "main"), ("season-main-return", "main"),
                               ("season-locked-1", "normal_locked"), ("season-locked-2", "normal_locked"),
                               ("season-home-before", "unknown"), ("season-loading", "unknown"),
                               ("adventure-gold-setup-1", "unknown")):
            with self.subTest(name=name), Image.open(FIXTURES / (name + ".png")) as image:
                state, details = season.recognize_season_page(image)
                self.assertEqual(state, expected, details)
                if expected == "normal_locked":
                    self.assertEqual(details["reading"], READING)

    def test_native_home_entry_and_unchanged_return_balances(self):
        readings = []
        for name, entry in (("season-home-before", True), ("season-home-after", False)):
            with Image.open(FIXTURES / (name + ".png")) as image:
                ok, details = season.recognize_season_home(image, entry=entry)
                self.assertTrue(ok, details)
                readings.append(details["balances"])
        self.assertEqual(readings, [(176606, 110404802, 1217083)] * 2)

    def test_native_main_last_day_header(self):
        # 2026-11-18: the wider hours/minutes countdown shifts 活动服装 left.
        with Image.open(FIXTURES / "season-main-last-day.png") as image:
            state, details = season.recognize_season_page(image)
        self.assertEqual(state, "main", details)

    def test_last_day_main_still_requires_identity_and_unobstructed_controls(self):
        with Image.open(FIXTURES / "season-main-last-day.png") as source:
            image = source.convert("RGB")
        entries = season._entries(image)
        for label in ("帮助", "行程", "活动服装", "剧情", "普通战斗", "挑战战斗",
                      "魔兽追踪者", "任务", "活动商店"):
            for score in (.94, float("nan")):
                changed = [(x, y, text, score if text == label else confidence)
                           for x, y, text, confidence in entries]
                with self.subTest(label=label, score=score), patch.object(season, "_entries", return_value=changed):
                    self.assertEqual(season.recognize_season_page(image)[0], "unknown")
        for changed in (
                entries + [(1628, 60, "活动服装", .99)],
                entries + [(1150, 600, "确认购买", .99)],
                [(1300 if text == "活动服装" else x, y, text, score)
                 for x, y, text, score in entries]):
            with patch.object(season, "_entries", return_value=changed):
                self.assertEqual(season.recognize_season_page(image)[0], "unknown")
        for box in ((203, 49, 216, 73),):
            changed = image.copy()
            changed.paste("black", box)
            with self.subTest(box=box), patch.object(season, "_entries", return_value=entries):
                self.assertEqual(season.recognize_season_page(changed)[0], "unknown")

    def test_native_home_stylized_blood_low_ocr_confidence(self):
        # 2026-11-18 stopped before any input: BLOOD scored only .87537,
        # while the native logo, ROYALE, subtitle and balances were intact.
        with Image.open(FIXTURES / "season-home-blood-low-confidence.png") as image:
            ok, details = season.recognize_season_home(image, entry=True)
        self.assertTrue(ok, details)
        self.assertEqual(details["balances"], (177351, 111590902, 1218693))

    def test_native_different_season_uses_the_same_entry_controls(self):
        # Exact retained failure frame, 2026-11-20 08:44:55; not a live capture.
        with Image.open(FIXTURES / "season-home-cheer.png") as image:
            ok, details = season.recognize_season_home(image, entry=True)
        self.assertTrue(ok, details)
        self.assertNotIn("unsupported_season", details)
        self.assertEqual(details["balances"], (177376, 112210902, 1220193))
        self.assertNotIn("reading", details)

    def test_season_entry_requires_countdown_subtitle_and_balances(self):
        with Image.open(FIXTURES / "season-home-cheer.png") as source:
            image = source.convert("RGB")
        entries = season._entries(image)
        with patch.object(season, "_recognize_actionable_home", return_value=(True, {})):
            for label in ("普通战斗解锁", "D-12", "177,376"):
                for replacement, score, duplicate in (("OTHER", .99, False), (label, .94, False),
                                                       (label, float("nan"), False), (label, .99, True)):
                    changed = [(x, y, replacement if text == label else text,
                                score if text == label else confidence)
                               for x, y, text, confidence in entries]
                    if duplicate:
                        changed.extend(e for e in entries if e[2] == label)
                    with self.subTest(label=label, score=score, duplicate=duplicate), patch.object(
                            season, "_entries", return_value=changed):
                        self.assertFalse(season.recognize_season_home(image, entry=True)[0])
            for color in ("black", "white"):
                changed = image.copy()
                changed.paste(color, (1910, 895, 2105, 970))
                with patch.object(season, "_entries", return_value=entries):
                    self.assertTrue(season.recognize_season_home(changed, entry=True)[0])
            self.assertFalse(season.recognize_season_home(image.resize((1152, 648)), entry=True)[0])
        with patch.object(season, "_recognize_actionable_home", return_value=(False, {})):
            self.assertFalse(season.recognize_season_home(image, entry=True)[0])

    def test_home_entry_requires_countdown_unlock_text_and_balances(self):
        with Image.open(FIXTURES / "season-home-blood-low-confidence.png") as source:
            image = source.convert("RGB")
        entries = season._entries(image)
        with patch.object(season, "_recognize_actionable_home", return_value=(True, {})):
            for label in ("D-DAY", "魔兽追踪者解锁", "177,351"):
                for replacement, score, duplicate in ((label, .94, False),
                                                       (label, float("nan"), False),
                                                       ("OTHER", .99, False),
                                                       (label, .99, True)):
                    modified = [(x, y, replacement if text == label else text,
                                 score if text == label else confidence)
                                for x, y, text, confidence in entries]
                    if duplicate:
                        modified.extend(e for e in entries if e[2] == label)
                    with self.subTest(label=label, replacement=replacement, score=score,
                                      duplicate=duplicate), patch.object(season, "_entries", return_value=modified):
                        self.assertFalse(season.recognize_season_home(image, entry=True)[0])

    def test_ocr_uncertainty_conflicts_unsafe_and_unseen_eligibility_rejected(self):
        for replacement, score, duplicate in (("5/5", .94, False), ("5/5", float("nan"), False),
                                               ("5/5", .99, True), ("6/5", .99, False),
                                               ("05/5", .99, False), ("5/6", .99, False)):
            entries = [(x, y, replacement if text == "5/5" else text,
                        score if text == "5/5" else confidence) for x, y, text, confidence in self.entries]
            if duplicate:
                entries.append((2120, 60, replacement, .99))
            with self.subTest(replacement=replacement, score=score, duplicate=duplicate), patch.object(
                    season, "_entries", return_value=entries):
                self.assertEqual(season.recognize_season_page(self.source)[0], "unknown")
        for entries in (
                self.entries + [(1150, 600, "确认购买", .55)],
                self.entries + [(1560, 1180, "快速战斗", .99)],
                [(x, y, "普通战斗2" if text == "普通战斗1" else text, score)
                 for x, y, text, score in self.entries],
                [(x, y, "活动已结束" if text.startswith("活动还剩") else text, score)
                 for x, y, text, score in self.entries]):
            with patch.object(season, "_entries", return_value=entries):
                self.assertEqual(season.recognize_season_page(self.source)[0], "unknown")

    def test_lock_stars_cost_back_control_and_size_required(self):
        images = [self.source.resize((1152, 648)), ImageEnhance.Brightness(self.source).enhance(.4)]
        for box in (*season.LOCKED_CROPS.values(), (203, 49, 216, 73)):
            changed = self.source.copy()
            changed.paste((220, 160, 0), box)
            images.append(changed)
        with patch.object(season, "_entries", return_value=self.entries):
            for image in images:
                self.assertEqual(season.recognize_season_page(image)[0], "unknown")
        with patch.object(season, "_entries", side_effect=RuntimeError("OCR unavailable")):
            state, details = season.recognize_season_page(self.source)
            self.assertEqual(state, "unknown")
            self.assertIn("OCR unavailable", details["error"])

    def test_countdown_units_and_style_do_not_change_locked_page_identity(self):
        for countdown in ("活动还剩2天", "活动还剩22小时59分钟", "活动还剩8分钟"):
            entries = [(1496 if text == "活动服装" else x, y,
                        countdown if text.startswith("活动还剩") else text, score)
                       for x, y, text, score in self.entries]
            changed = self.source.copy()
            changed.paste("white", (80, 1100, 210, 1260))
            with self.subTest(countdown=countdown), patch.object(season, "_entries", return_value=entries):
                state, details = season.recognize_season_page(changed)
            self.assertEqual(state, "normal_locked", details)
            self.assertEqual(details["reading"], READING)

    def test_zero_ap_is_only_an_audit_never_battle_permission(self):
        entries = [(x, y, "0/5" if text == "5/5" else text, score)
                   for x, y, text, score in self.entries]
        with patch.object(season, "_entries", return_value=entries):
            state, details = season.recognize_season_page(self.source)
        self.assertEqual(state, "normal_locked")
        self.assertEqual(details["reading"]["ap"], 0)
        self.assertFalse(details["reading"]["quick_battle"])


class SeasonFlowTests(unittest.TestCase):
    def flow(self, *, failure=None, changed=False, home_changed=False, dry_run=False, bad_start=False):
        image = Image.new("RGB", (2304, 1296), "white")
        labels = []

        def wait(hwnd, *, label, **kwargs):
            labels.append(label)
            reading = dict(READING, ap=4 if changed and label == "season-locked-2" else 5)
            return failure != label, image, dict(reading=reading,
                balances=(1, 2, 4 if home_changed and label == "season-home" else 3))

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in (("find_game_window", 123),
                                ("prepare_actionable_home", (True, image, "home")),
                                ("recognize_season_home", (not bad_start, {"balances": (1, 2, 3)}))):
                stack.enter_context(patch.object(season, name, return_value=value))
            stack.enter_context(patch.object(season, "wait_for_recognition", side_effect=wait))
            stack.enter_context(patch.object(season.time, "sleep"))
            click = stack.enter_context(patch.object(season, "click_ratio_logged"))
            result = season.run_season_battle_recheck(dry_run=dry_run, log_root=Path(directory))
            keys = [call.kwargs["key"] for call in click.call_args_list]
        return result, keys, labels

    def test_success_is_skipped_and_only_four_navigation_inputs(self):
        (ok, reason), keys, labels = self.flow()
        self.assertTrue(ok, reason)
        self.assertTrue(reason.startswith("skipped:"))
        self.assertEqual(keys, ["season_entry", "season_normal_page", "season_normal_back", "season_return_home"])
        self.assertEqual(len(labels), 5)

    def test_every_observation_failure_stops_without_repeat_or_later_input(self):
        for label, count in (("season-main", 1), ("season-locked-1", 2), ("season-locked-2", 2),
                             ("season-main-return", 3), ("season-home", 4)):
            with self.subTest(label=label):
                (ok, _), keys, _ = self.flow(failure=label)
                self.assertFalse(ok)
                self.assertEqual(len(keys), count)

    def test_changed_ap_stops_before_return_and_changed_home_never_completes(self):
        for kwargs, count in ((dict(changed=True), 2), (dict(home_changed=True), 4)):
            (ok, _), keys, _ = self.flow(**kwargs)
            self.assertFalse(ok)
            self.assertEqual(len(keys), count)

    def test_dry_run_and_unverified_start_never_input(self):
        for dry_run, bad_start in ((True, False), (True, True), (False, True)):
            (ok, _), keys, labels = self.flow(dry_run=dry_run, bad_start=bad_start)
            self.assertEqual(ok, not bad_start)
            self.assertEqual((keys, labels), ([], []))

    def test_real_phase_order_failure_propagation_and_partial_plan(self):
        for fail in (None, "ensure_home", "run_season_battle_recheck"):
            calls = []
            def operation(name):
                def run(**kwargs):
                    calls.append(name)
                    return name != fail, "skipped:season audit" if name != fail else "failed"
                return run
            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                for name in ("ensure_home", "run_season_battle_recheck"):
                    stack.enter_context(patch.object(daily, name, operation(name)))
                root = Path(directory)
                if fail:
                    with self.assertRaises(daily.DailyRunError):
                        daily._execute_daily_phase("season_activity", master=daily.MasterLogger(root), run_root=root)
                else:
                    reason = daily._execute_daily_phase("season_activity", master=daily.MasterLogger(root), run_root=root)
                    self.assertTrue(reason.startswith("skipped:"))
            self.assertEqual(calls, ["ensure_home"] if fail == "ensure_home" else ["ensure_home", "run_season_battle_recheck"])
        for preset in ("fast", "detailed"):
            plan = get_daily_plan(preset)
            stage = next(s for s in plan if s.id == "season_activity")
            self.assertEqual(stage.availability, "partial")
            self.assertFalse(stage.implemented)
            self.assertTrue(stage.current_runnable)
            self.assertEqual(stage.current_runners, ("season_activity.run_season_battle_recheck",))
            self.assertEqual([s.id for s in plan][5:8], ["arena", "season_activity", "weekly"])


if __name__ == "__main__":
    unittest.main()
