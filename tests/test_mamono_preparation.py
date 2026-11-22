from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

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
                                   "mamono-loading", "season-locked-1")}
        cls.entries = {id(frame): season._entries(frame) for frame in cls.frames.values()}

    def test_native_preparation_and_adjacent_pages(self):
        for name, frame in self.frames.items():
            with self.subTest(name=name), patch.object(season, "_entries", return_value=self.entries[id(frame)]):
                ok, details = season.recognize_mamono_preparation(frame)
                self.assertEqual(ok, name.startswith("mamono-preparation"), details)
                if ok:
                    self.assertEqual(details["days"], 3)
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
            for confidence in (.94, float("nan")):
                changes.append([(x, y, text, confidence if text == anchor else score)
                                for x, y, text, score in entries])
        changes.append(entries + [e for e in entries if e[2] == "魔兽"])
        for changed in changes:
            with patch.object(season, "_entries", return_value=changed):
                self.assertFalse(season.recognize_mamono_preparation(frame)[0])
        obscured = frame.copy()
        obscured.paste("black", (203, 49, 216, 73))
        with patch.object(season, "_entries", return_value=entries):
            self.assertFalse(season.recognize_mamono_preparation(obscured)[0])
            self.assertFalse(season.recognize_mamono_preparation(ImageEnhance.Brightness(frame).enhance(.3))[0])
        self.assertFalse(season.recognize_mamono_preparation(frame.resize((1152, 648)))[0])
        with patch.object(season, "_entries", side_effect=ValueError("ocr")):
            self.assertFalse(season.recognize_mamono_preparation(frame)[0])

    def flow(self, *, fail=None, changed_days=False, changed_balance=False, dry_run=False):
        names = iter(("mamono-menu", "mamono-preparation-1", "mamono-preparation-2",
                      "mamono-menu-return", "mamono-home-after"))
        keys = []
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(season, "find_game_window", return_value=123))
            stack.enter_context(patch.object(season.time, "sleep"))
            stack.enter_context(patch.object(season, "_entries", side_effect=lambda im: self.entries[id(im)]))
            stack.enter_context(patch.object(season, "prepare_actionable_home", return_value=(True, self.frames["mamono-home-before"], "home")))
            stack.enter_context(patch.object(season, "click_ratio_logged", side_effect=lambda *a, **k: keys.append(k["key"])))
            def wait(*args, **kwargs):
                frame = self.frames[next(names)]
                ok, details = kwargs["recognize"](frame)
                if changed_days and kwargs["label"] == "mamono-preparation-2":
                    details = {**details, "days": 2}
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
        for fail, count in (("mamono-practice-menu", 1), ("mamono-preparation-1", 2),
                            ("mamono-preparation-2", 2), ("mamono-menu-return", 3),
                            ("mamono-home-return", 4)):
            result, keys = self.flow(fail=fail)
            self.assertFalse(result[0], result)
            self.assertEqual(len(keys), count)
        result, keys = self.flow(changed_days=True)
        self.assertFalse(result[0])
        self.assertEqual(len(keys), 2)
        self.assertFalse(self.flow(changed_balance=True)[0][0])
        self.assertEqual(self.flow(dry_run=True)[1], [])

    def test_recovery_uses_only_verified_preparation_back(self):
        frame = self.frames["mamono-preparation-1"]
        with ExitStack() as stack:
            stack.enter_context(patch.object(season, "_entries", return_value=self.entries[id(frame)]))
            self.assertEqual(season.recognize_season_navigation(frame)[0], "mamono_preparation")
            stack.enter_context(patch.object(season, "wait_for_recognition", side_effect=[
                (True, self.frames["mamono-menu-return"], {}), (True, self.frames["mamono-home-after"], {})]))
            click = stack.enter_context(patch.object(season, "click_ratio_logged"))
            self.assertTrue(season.return_from_season(123, frame, logger=None)[0])
            self.assertEqual(click.call_count, 2)


if __name__ == "__main__":
    unittest.main()
