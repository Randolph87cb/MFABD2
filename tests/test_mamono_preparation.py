from __future__ import annotations

import sys
import itertools
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import season_activity as season

FIXTURES = Path(__file__).parent / "fixtures/recognition"


class MamonoPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = {name: Image.open(FIXTURES / (name + ".png")).convert("RGB")
                      for name in ("mamono-home-before", "mamono-menu", "mamono-preparation-1",
                                   "mamono-preparation-2", "mamono-menu-return", "mamono-home-after",
                                   "mamono-loading", "season-locked-1", "mamono-preparation-hours",
                                   "mamono-menu-marker-unconfirmed-20261126", "mamono-menu-no-practice-20261127",
                                   "mamono-active-20261029")}
        unverified = cls.frames["mamono-active-20261029"].copy()
        unverified.paste("black", (1630, 30, 2060, 90))
        cls.frames["mamono-period-unverified"] = unverified
        cls.entries = {id(frame): season._entries(frame) for frame in cls.frames.values()}

    def test_native_preparation_and_adjacent_pages(self):
        for name, frame in self.frames.items():
            with self.subTest(name=name), patch.object(season, "_entries", return_value=self.entries[id(frame)]):
                ok, details = season.recognize_mamono_preparation(frame)
                self.assertEqual(ok, name.startswith("mamono-preparation"), details)
                if ok:
                    self.assertEqual(details["days"], None if name.endswith("hours") else 3)
                    self.assertFalse(details["battle"])

    def test_formal_missing_lock_dialog_low_confidence_and_obscured_rejected(self):
        frame = self.frames["mamono-preparation-1"]
        entries = self.entries[id(frame)]
        # Synthetic negatives; no actual formal battle or purchase was opened.
        changes = [entries + [(1150, 600, "确认购买", .99)],
                   [(x, y, text.replace("距离开始还剩3天", "活动还剩3天"), score)
                    for x, y, text, score in entries]]
        for anchor in ("魔兽", "队伍1", "队伍2", "队伍3", "距离开始还剩3天",
                       "从狩猎期间开始解除", "去战斗"):
            changes.append([e for e in entries if e[2] != anchor])
            for confidence in (.89 if anchor == "魔兽" else .94, float("nan")):
                changes.append([(x, y, text, confidence if text == anchor else score)
                                for x, y, text, score in entries])
        changes.append(entries + [e for e in entries if e[2] == "魔兽"])
        for changed in changes:
            # Synthetic OCR failures apply to both full-frame and ROI reads.
            with patch.object(season, "_entries", return_value=changed), patch.object(season, "_crop_text", return_value=None):
                self.assertFalse(season.recognize_mamono_preparation(frame)[0])
        obscured = frame.copy()
        obscured.paste("black", (203, 49, 216, 73))
        with patch.object(season, "_entries", return_value=entries):
            self.assertFalse(season.recognize_mamono_preparation(obscured)[0])
            self.assertFalse(season.recognize_mamono_preparation(ImageEnhance.Brightness(frame).enhance(.3))[0])
        self.assertFalse(season.recognize_mamono_preparation(frame.resize((1152, 648)))[0])
        with patch.object(season, "_entries", side_effect=ValueError("ocr")):
            self.assertFalse(season.recognize_mamono_preparation(frame)[0])

    def flow(self, *, fail=None, changed_days=False, changed_balance=False, dry_run=False,
             countdowns=None, menu_names=None):
        names = iter(menu_names or ("mamono-menu", "mamono-preparation-1", "mamono-preparation-2",
                                   "mamono-menu-return", "mamono-home-after"))
        keys = []
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(season, "find_game_window", return_value=123))
            stack.enter_context(patch.object(season.time, "sleep"))
            stack.enter_context(patch.object(season.time, "monotonic", side_effect=itertools.count(100, 2)))
            stack.enter_context(patch.object(season, "_entries", side_effect=lambda im: self.entries[id(im)]))
            stack.enter_context(patch.object(season, "prepare_actionable_home", return_value=(True, self.frames["mamono-home-before"], "home")))
            stack.enter_context(patch.object(season, "click_ratio_logged", side_effect=lambda *a, **k: keys.append(k["key"])))
            def wait(*args, **kwargs):
                frame = self.frames[next(names)]
                ok, details = kwargs["recognize"](frame)
                if countdowns and kwargs["label"].startswith("mamono-period-"):
                    index = int(kwargs["label"][-1]) - 1
                    minutes, precision = countdowns[index]
                    details = {**details, "minutes": minutes, "precision": precision}
                if changed_days and kwargs["label"] == "mamono-period-2":
                    details = {**details, "days": 1, "minutes": 1440}
                if changed_balance and kwargs["label"] == "mamono-home-return":
                    details = {**details, "balances": (0, 0, 0)}
                return ok and kwargs["label"] != fail, frame, details
            stack.enter_context(patch.object(season, "wait_for_recognition", side_effect=wait))
            result = season.run_mamono_preparation_recheck(dry_run=dry_run, log_root=Path(directory))
        return result, keys

    def test_native_flow_balances_and_input_boundaries(self):
        result, keys = self.flow()
        self.assertTrue(result[0], result)
        self.assertTrue(result[1].startswith("skipped:"))
        self.assertEqual(keys, ["mamono_season_entry", "mamono_entry", "mamono_back", "mamono_home"])
        for fail, count in (("mamono-practice-menu", 1), ("mamono-period-1", 2),
                            ("mamono-period-2", 2), ("mamono-menu-return", 3),
                            ("mamono-home-return", 4)):
            result, keys = self.flow(fail=fail)
            self.assertFalse(result[0], result)
            self.assertEqual(len(keys), count)
        result, keys = self.flow(changed_days=True)
        self.assertFalse(result[0])
        self.assertEqual(len(keys), 2)
        self.assertFalse(self.flow(changed_balance=True)[0][0])
        self.assertEqual(self.flow(dry_run=True)[1], [])

    def test_countdown_rollover_and_increase_boundaries(self):
        for readings, expected in ((((1440, 1440), (1439, 1)), True),
                                   (((60, 1), (59, 1)), True),
                                   (((60, 1), (61, 1)), False),
                                   (((1395, 1), (1300, 1)), False)):
            with self.subTest(readings=readings):
                result, keys = self.flow(countdowns=readings)
                self.assertEqual(result[0], expected, result)
                self.assertEqual(len(keys), 4 if expected else 2)

    def test_complete_duration_formats_and_reject_expired_or_invalid(self):
        frame = self.frames["mamono-preparation-hours"]
        original = self.entries[id(frame)]
        for text, expected in (("23小时15分钟", 1395), ("23小时", 1380),
                               ("1分钟", 1), ("0小时1分钟", 1), ("24小时", None),
                               ("23小时60分钟", None), ("0分钟", None), ("已开始", None)):
            entries = [(x, y, "距离开始还剩" + text if t.startswith("距离开始还剩") else t, score)
                       for x, y, t, score in original]
            with self.subTest(text=text), patch.object(season, "_entries", return_value=entries), patch.object(season, "_crop_text", return_value=None):
                ok, details = season.recognize_mamono_preparation(frame)
                self.assertEqual(ok, expected is not None, details)
                if ok:
                    self.assertEqual(details["minutes"], expected)

    def test_merged_countdown_is_re_read_from_its_own_roi(self):
        frame = self.frames["mamono-preparation-hours"]
        original = self.entries[id(frame)]
        merged = [(x, y, t + "100" if t.startswith("距离开始还剩") else t, s)
                  for x, y, t, s in original]
        with patch.object(season, "_entries", return_value=merged):
            ok, details = season.recognize_mamono_preparation(frame)
        self.assertTrue(ok, details)
        self.assertEqual(details["minutes"], 1395)

    def test_recovery_uses_only_verified_preparation_back(self):
        frame = self.frames["mamono-preparation-hours"]
        with ExitStack() as stack:
            stack.enter_context(patch.object(season, "_entries", return_value=self.entries[id(frame)]))
            self.assertEqual(season.recognize_season_navigation(frame)[0], "mamono_preparation")
            stack.enter_context(patch.object(season, "wait_for_recognition", side_effect=[
                (True, self.frames["mamono-menu-return"], {}), (True, self.frames["mamono-home-after"], {})]))
            click = stack.enter_context(patch.object(season, "click_ratio_logged"))
            self.assertTrue(season.return_from_season(123, frame, logger=None)[0])
            self.assertEqual(click.call_count, 2)

    def test_menu_identity_is_independent_of_preparation_marker(self):
        for name, expected, marker in (("mamono-menu", True, True),
                                       ("mamono-menu-marker-unconfirmed-20261126", True, False),
                                       ("mamono-preparation-hours", False, None),
                                       ("mamono-home-before", False, None)):
            with self.subTest(name=name):
                ok, details = season.recognize_mamono_menu(self.frames[name])
                self.assertEqual(ok, expected, details)
                if ok:
                    self.assertEqual(details["preparation_marker"], marker)
                    self.assertFalse(details["battle"])

    def test_marker_omitted_in_full_frame_is_reread_from_roi(self):
        frame = self.frames["mamono-menu"]
        entries = [e for e in self.entries[id(frame)] if e[2] != "可练习战斗"]
        original = season._entries
        with patch.object(season, "_entries", side_effect=lambda image: entries if image is frame else original(image)):
            ok, details = season.recognize_mamono_menu(frame)
        self.assertTrue(ok, details)
        self.assertTrue(details["preparation_marker"])
        self.assertTrue(details["marker_crop_reread"])

    def test_missing_badge_still_audits_the_destination_period(self):
        for missing in ("mamono-menu-marker-unconfirmed-20261126", "mamono-menu-no-practice-20261127"):
            for period in ("mamono-preparation-1", "mamono-active-20261029"):
                result, keys = self.flow(menu_names=(missing, period, period,
                                                    missing, "mamono-home-after"))
                self.assertTrue(result[0], result)
                self.assertTrue(result[1].startswith("skipped:"))
                if period.startswith("mamono-active"):
                    self.assertIn("准备期检查不适用", result[1])
                    self.assertIn("正式战斗未实现", result[1])
                self.assertEqual(keys, ["mamono_season_entry", "mamono_entry", "mamono_back", "mamono_home"])

    def test_unknown_or_changing_period_remains_a_failure_without_battle_inputs(self):
        for first, second in (("mamono-active-20261029", "mamono-preparation-1"),
                              ("mamono-preparation-1", "mamono-active-20261029"),
                              ("mamono-period-unverified", "mamono-period-unverified"),
                              ("mamono-active-20261029", "mamono-loading")):
            result, keys = self.flow(menu_names=("mamono-menu-no-practice-20261127", first, second))
            self.assertFalse(result[0], result)
            self.assertEqual(keys, ["mamono_season_entry", "mamono_entry"])
        self.assertEqual(season.recognize_season_navigation(self.frames["mamono-period-unverified"])[0],
                         "mamono_selection")

    def test_return_menu_does_not_require_period_marker(self):
        result, keys = self.flow(menu_names=("mamono-menu", "mamono-preparation-1",
                                            "mamono-preparation-2", "mamono-menu-marker-unconfirmed-20261126",
                                            "mamono-home-after"))
        self.assertTrue(result[0], result)
        self.assertEqual(keys[-1], "mamono_home")

    def test_native_active_page_is_only_period_evidence(self):
        frame = self.frames["mamono-active-20261029"]
        with patch.object(season, "_entries", return_value=self.entries[id(frame)]):
            state, details = season.recognize_mamono_page(frame)
            self.assertEqual(state, "active", details)
            self.assertEqual(details["minutes"], 6 * 1440)
            self.assertFalse(details["battle"])
            self.assertNotIn("free", details)
            self.assertFalse(season.recognize_mamono_preparation(frame)[0])
            self.assertEqual(season.recognize_season_navigation(frame)[0], "mamono_active")

    def test_fixed_title_accepts_help_and_uses_title_confidence_only(self):
        frame = self.frames["mamono-active-20261029"]
        entries = self.entries[id(frame)]
        for score, expected in ((.94, "active"), (.90, "active"), (.89, "unknown"), (float("nan"), "unknown")):
            changed = [(x, y, "魔兽?帮助" if t == "魔兽" else t, score if t == "魔兽" else s)
                       for x, y, t, s in entries]
            with self.subTest(score=score), patch.object(season, "_entries", return_value=changed):
                self.assertEqual(season.recognize_mamono_page(frame)[0], expected)

    def test_active_merged_or_omitted_countdown_and_title_are_crop_reread(self):
        frame = self.frames["mamono-active-20261029"]
        entries = self.entries[id(frame)]
        for changed in (
                [e for e in entries if not e[2].startswith("距离结束")],
                [(x, y, t + "100" if t.startswith("距离结束") else t, s) for x, y, t, s in entries],
                [e for e in entries if e[2] != "魔兽"]):
            with patch.object(season, "_entries", return_value=changed):
                state, details = season.recognize_mamono_page(frame)
            self.assertEqual(state, "active", details)
            self.assertEqual(details["minutes"], 8640)

    def test_unverified_countdown_allows_only_return_navigation(self):
        frame = self.frames["mamono-active-20261029"]
        entries = self.entries[id(frame)]
        changes = [[(x, y, "距离结束还剩" + value if t.startswith("距离结束") else t, s)
                    for x, y, t, s in entries] for value in ("0分钟", "24小时", "6天100", "23小时60分钟")]
        changes.extend([
            [(x, y, t, .94 if t.startswith("距离结束") else s) for x, y, t, s in entries],
            entries + [e for e in entries if e[2].startswith("距离结束")],
            entries + [(1530, 468, "从狩猎期间开始解除", .99)],
        ])
        for changed in changes:
            with patch.object(season, "_entries", return_value=changed), patch.object(season, "_crop_text", return_value=None):
                state, details = season.recognize_season_navigation(frame)
            self.assertEqual(state, "mamono_selection", details)
            self.assertNotIn("period", details)
            self.assertFalse(details["battle"])

    def test_obscured_or_unsafe_active_page_is_not_a_return_permit(self):
        frame = self.frames["mamono-active-20261029"]
        entries = self.entries[id(frame)]
        for changed in (entries + [(1150, 600, "确认购买", .99)],
                        entries + [(375, 62, "魔兽", .99)],
                        [e for e in entries if e[2] != "去战斗"]):
            with patch.object(season, "_entries", return_value=changed), patch.object(season, "_crop_text", return_value=None):
                self.assertEqual(season.recognize_season_navigation(frame)[0], "unknown")
        for box in ((203, 49, 216, 73), (330, 25, 420, 90)):
            covered = frame.copy()
            covered.paste("black", box)
            self.assertEqual(season.recognize_season_navigation(covered)[0], "unknown")

    def test_active_return_verifies_both_destinations_without_battle(self):
        for name in ("mamono-active-20261029", "mamono-period-unverified"):
            frame = self.frames[name]
            with self.subTest(name=name), patch.object(season, "_entries", return_value=self.entries[id(frame)]), \
                    patch.object(season, "wait_for_recognition", side_effect=[
                        (True, self.frames["mamono-menu-no-practice-20261127"], {}),
                        (True, self.frames["mamono-home-after"], {})]), \
                    patch.object(season, "click_ratio_logged") as click:
                self.assertTrue(season.return_from_season(123, frame, logger=None)[0])
            self.assertEqual([call.kwargs["key"] for call in click.call_args_list],
                             ["season_normal_back", "season_return_home"])

    def test_entry_uses_current_menu_text_centre(self):
        frame = self.frames["mamono-menu-no-practice-20261127"]
        expected = next((x / frame.width, y / frame.height)
                        for x, y, t, s in self.entries[id(frame)] if t == "魔兽追踪者")
        points = []
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(season, "find_game_window", return_value=123), \
                patch.object(season, "prepare_actionable_home", return_value=(True, frame, "home")), \
                patch.object(season, "recognize_season_home", return_value=(True, {"balances": (1, 2, 3)})), \
                patch.object(season, "wait_for_recognition", side_effect=[
                    (True, frame, season.recognize_mamono_menu(frame)[1]), (False, frame, {})]), \
                patch.object(season, "click_ratio_logged", side_effect=lambda *a, **k: points.append(a[2])):
            self.assertFalse(season.run_mamono_preparation_recheck(dry_run=False, log_root=Path(directory))[0])
        self.assertEqual(points[1], expected)

    def test_season_wait_keeps_network_loading_unbounded(self):
        frame = self.frames["mamono-loading"]
        with patch.object(season, "classify_state", return_value=("loading", {})), \
                patch("reward_flow.safe_capture_client", return_value=frame), \
                patch("reward_flow.time.monotonic", side_effect=[0, 1000]), \
                patch("reward_flow.time.sleep"), patch.object(season, "click_ratio_logged") as click:
            ok, _, _ = season._wait_for_season_recognition(
                123, logger=MagicMock(), label="network", timeout=1,
                recognize=MagicMock(side_effect=[(False, {}), (True, {})]))
        self.assertTrue(ok)
        click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
