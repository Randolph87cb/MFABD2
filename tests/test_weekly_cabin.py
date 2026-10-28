from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation
import weekly_cabin
from daily_plan import get_daily_plan


FIXTURES = Path(__file__).parent / "fixtures" / "recognition"


class CabinRecognitionTests(unittest.TestCase):
    def test_native_pc_positive_negative_and_already_liked(self):
        for filename, expected in {
            "cabin-own-2304x1296.png": "own_cabin",
            "cabin-popular-2304x1296.png": "visit_other",
            "cabin-random-ready-2304x1296.png": "like_ready",
            "cabin-random-liked-2304x1296.png": "like_done",
            "cabin-random-liked-stable-2304x1296.png": "like_done",
            "plaza-prayer-ready-2304x1296.png": "unknown",
            "restaurant-home-2567x1446.png": "unknown",
        }.items():
            with self.subTest(filename=filename), Image.open(FIXTURES / filename) as image:
                state, details = weekly_cabin.recognize_cabin_page(image)
                self.assertEqual(state, expected, details)
                if expected.startswith("like_"):
                    self.assertEqual(details["identity"], "Guest_1317156")
                    self.assertEqual(details["count"], 5763 if expected == "like_ready" else 5764)

    def test_dimmed_controls_unknown_icon_and_ocr_failure_stop(self):
        with Image.open(FIXTURES / "cabin-random-ready-2304x1296.png") as image:
            state, _ = weekly_cabin.recognize_cabin_page(ImageEnhance.Brightness(image).enhance(0.4))
            self.assertEqual(state, "unknown")
            changed = image.copy()
            changed.paste("white", weekly_cabin.LIKE_BOX)
            state, _ = weekly_cabin.recognize_cabin_page(changed)
            self.assertEqual(state, "unknown")
            with patch("weekly_cabin._ocr_engine", side_effect=RuntimeError("OCR failed")):
                state, _ = weekly_cabin.recognize_cabin_page(image)
            self.assertEqual(state, "unknown")

    def test_ambiguous_or_low_confidence_count_cannot_authorize_like(self):
        with Image.open(FIXTURES / "cabin-random-ready-2304x1296.png") as image:
            result = weekly_cabin._ocr_engine()(np.asarray(image))
            for duplicate in (False, True):
                boxes, texts, scores = list(result.boxes), list(result.txts), list(result.scores)
                index = texts.index("5763")
                if duplicate:
                    boxes.append(boxes[index])
                    texts.append("5763")
                    scores.append(1.0)
                else:
                    scores[index] = 0.80
                with patch("weekly_cabin._ocr_engine") as engine:
                    engine.return_value.return_value = SimpleNamespace(boxes=boxes, txts=texts, scores=scores)
                    state, _ = weekly_cabin.recognize_cabin_page(image)
                self.assertEqual(state, "unknown")


class CabinWeekTests(unittest.TestCase):
    def test_week_boundary_in_game_timezone(self):
        for stamp, expected in {
            "2026-10-26T07:59:59+08:00": "2026-10-19",
            "2026-10-26T08:00:00+08:00": "2026-10-26",
            "2026-11-02T07:59:59+08:00": "2026-10-26",
            "2026-11-02T00:00:00+00:00": "2026-11-02",
            "2027-01-01T09:00:00+08:00": "2026-12-28",
        }.items():
            self.assertEqual(weekly_cabin.game_week_key(datetime.fromisoformat(stamp)), expected)

    def test_exclusive_reservation_and_bad_or_pending_state_never_touch_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "state" / "2026-10-26.json"
            pending = {"version": 1, "week": "2026-10-26", "status": "action_pending"}
            weekly_cabin._write_record(path, pending, reserve=True)
            with self.assertRaises(FileExistsError):
                weekly_cabin._write_record(path, pending, reserve=True)
            for content in (json.dumps(pending), "{", "[]", json.dumps({**pending, "week": "bad"})):
                path.write_text(content, encoding="utf-8")
                with patch("weekly_cabin.game_week_key", return_value="2026-10-26"), \
                     patch("weekly_cabin.find_game_window") as window, \
                     patch("weekly_cabin.click_ratio_logged") as click:
                    ok, _ = weekly_cabin.run_weekly_cabin(dry_run=False, log_root=root / "logs", state_root=path.parent)
                self.assertFalse(ok)
                window.assert_not_called()
                click.assert_not_called()


class CabinFlowTests(unittest.TestCase):
    def run_flow(self, *, initial="like_ready", after_identity="guest", after_count=11,
                 home=True, dry_run=False, crash=False):
        image = Image.new("RGB", (2304, 1296), "white")
        records, keys = [], []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "state" / "2026-10-26.json"

            def click(*args, **kwargs):
                keys.append(kwargs["key"])
                if kwargs["key"] == "one_free_cabin_like":
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "action_pending")
                    if crash:
                        raise RuntimeError("input outcome unknown")

            def wait(*args, **kwargs):
                label = kwargs["label"]
                if label == "cabin-home":
                    return home, image, {}
                if label == "own-cabin":
                    state, details = "own_cabin", {}
                elif label == "cabin-visit":
                    state, details = "visit_other", {}
                elif label == "random-cabin":
                    state, details = initial, {"identity": "guest", "count": 10}
                else:
                    state, details = "like_done", {"identity": after_identity, "count": after_count}
                with patch("weekly_cabin.recognize_cabin_page", return_value=(state, details)):
                    found, result = kwargs["recognize"](image)
                return found, image, result

            with patch("weekly_cabin.find_game_window", return_value=123), \
                 patch("weekly_cabin.game_week_key", return_value="2026-10-26"), \
                 patch("weekly_cabin.prepare_actionable_home", return_value=(True, image, "home")), \
                 patch("weekly_cabin.wait_for_recognition", side_effect=wait), \
                 patch("weekly_cabin.click_ratio_logged", side_effect=click), \
                 patch("weekly_cabin.time.sleep"):
                if crash:
                    with self.assertRaises(RuntimeError):
                        weekly_cabin.run_weekly_cabin(dry_run=False, log_root=root / "logs", state_root=path.parent)
                    result = False, "crash"
                else:
                    result = weekly_cabin.run_weekly_cabin(dry_run=dry_run, log_root=root / "logs", state_root=path.parent)
                if path.exists():
                    records.append(json.loads(path.read_text(encoding="utf-8")))
                    with patch("weekly_cabin.find_game_window") as window:
                        again = weekly_cabin.run_weekly_cabin(dry_run=False, log_root=root / "again", state_root=path.parent)
                    window.assert_not_called()
                    self.assertEqual(again[0], records[0]["status"] == "completed")
        return result, keys, records

    def test_single_like_durable_before_input_and_same_week_skip(self):
        (ok, _), keys, records = self.run_flow()
        self.assertTrue(ok)
        self.assertEqual(keys.count("one_free_cabin_like"), 1)
        self.assertEqual(records[0]["status"], "completed")
        self.assertEqual(records[0]["after_count"], 11)

    def test_wrong_identity_count_or_return_never_completes_and_never_retries(self):
        for options in ({"after_identity": "other"}, {"after_count": 10}, {"after_count": 12}, {"home": False}, {"crash": True}):
            with self.subTest(options=options):
                (ok, _), keys, records = self.run_flow(**options)
                self.assertFalse(ok)
                self.assertEqual(keys.count("one_free_cabin_like"), 1)
                self.assertEqual(records[0]["status"], "action_pending")

    def test_initial_blue_skips_without_toggling_or_claiming_this_week(self):
        (ok, reason), keys, records = self.run_flow(initial="like_done", after_count=10)
        self.assertTrue(ok)
        self.assertTrue(reason.startswith("skipped:"))
        self.assertNotIn("one_free_cabin_like", keys)
        self.assertEqual(records, [])

    def test_dry_run_no_record_and_no_real_input(self):
        image = Image.new("RGB", (2304, 1296), "white")
        with tempfile.TemporaryDirectory() as temporary, \
             patch("weekly_cabin.find_game_window", return_value=123), \
             patch("weekly_cabin.prepare_actionable_home", return_value=(True, image, "home")), \
             patch("reward_flow.click_client") as click:
            state_root = Path(temporary) / "state"
            ok, _ = weekly_cabin.run_weekly_cabin(dry_run=True, log_root=Path(temporary) / "logs", state_root=state_root)
            self.assertTrue(ok)
            self.assertFalse(state_root.exists())
            click.assert_not_called()

    def test_real_weekly_stage_failure_propagates_and_stays_partial(self):
        events = []
        with tempfile.TemporaryDirectory() as temporary, \
             patch("daily_automation.ensure_home", side_effect=lambda **_: (events.append("home") or True, "home")), \
             patch("daily_automation.run_weekly_cabin", side_effect=lambda **_: (events.append("cabin") or False, "unknown")):
            with self.assertRaises(daily_automation.DailyRunError):
                daily_automation._execute_daily_phase("weekly", master=daily_automation.MasterLogger(Path(temporary)), run_root=Path(temporary))
        self.assertEqual(events, ["home", "cabin"])
        ids = daily_automation.CURRENT_PHASE_IDS
        self.assertLess(ids.index("arena"), ids.index("weekly"))
        self.assertLess(ids.index("weekly"), ids.index("daily_claims"))
        stage = next(item for item in get_daily_plan("fast") if item.id == "weekly")
        self.assertFalse(stage.implemented)
        self.assertEqual(stage.availability, "partial")
        self.assertEqual(stage.current_runners, ("weekly_cabin.run_weekly_cabin",))


if __name__ == "__main__":
    unittest.main()
