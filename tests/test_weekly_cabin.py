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
    def test_fourth_cabin_native_frames_and_ambiguous_identity(self):
        for name, expected, count in (
            ("ready", "like_ready", 1084),
            ("liked", "like_done", 1085),
            ("liked-stable", "like_done", 1085),
            ("ambiguous-identity", "unknown", None),
        ):
            with self.subTest(name=name), Image.open(FIXTURES / f"cabin-fourth-{name}-2304x1296.png") as image:
                state, details = weekly_cabin.recognize_cabin_page(image)
                self.assertEqual(state, expected, details)
                if count is not None:
                    self.assertEqual(details["identity"], "WolfgangMittermeyer")
                    self.assertEqual(details["count"], count)

    def test_third_cabin_native_before_and_two_after_frames(self):
        for name, expected, count in (
            ("ready", "like_ready", 26155),
            ("liked", "like_done", 26156),
            ("liked-stable", "like_done", 26156),
        ):
            with self.subTest(name=name), Image.open(FIXTURES / f"cabin-third-{name}-2304x1296.png") as image:
                state, details = weekly_cabin.recognize_cabin_page(image)
                self.assertEqual(state, expected, details)
                self.assertEqual(details["identity"], "CijaNepoi")
                self.assertEqual(details["count"], count)

    def test_second_cabin_native_before_and_two_after_frames(self):
        for name, expected, count in (
            ("ready", "like_ready", 1954),
            ("liked", "like_done", 1955),
            ("liked-stable", "like_done", 1955),
        ):
            with self.subTest(name=name), Image.open(FIXTURES / f"cabin-second-{name}-2304x1296.png") as image:
                state, details = weekly_cabin.recognize_cabin_page(image)
                self.assertEqual(state, expected, details)
                self.assertEqual(details["identity"], "阿噗巴拉蹦")
                self.assertEqual(details["count"], count)

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
                 home=True, dry_run=False, crash=False, excluded_identity=None, second=False,
                 third=False, fourth=False, initial_identity="guest"):
        image = Image.new("RGB", (2304, 1296), "white")
        records, keys = [], []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "state" / "2026-10-26.json"
            first_path = root / "first" / "2026-10-26.json"
            first_path.parent.mkdir()
            first_bytes = json.dumps(dict(version=1, week="2026-10-26", status="completed",
                                         identity="first", before_count=20, after_count=21)).encode()
            first_path.write_bytes(first_bytes)
            second_path = root / "second" / "2026-10-26.json"
            second_path.parent.mkdir()
            second_bytes = json.dumps(dict(version=1, week="2026-10-26", status="completed",
                                          identity="second", before_count=30, after_count=31,
                                          excluded_identity="first")).encode()
            second_path.write_bytes(second_bytes)
            third_path = root / "third" / "2026-10-26.json"
            third_bytes = json.dumps(dict(version=1, week="2026-10-26", status="completed",
                                         identity="third", before_count=40, after_count=41,
                                         excluded_identities=["first", "second"])).encode()
            if fourth:
                third_path.parent.mkdir()
                third_path.write_bytes(third_bytes)

            def execute(log_root, dry_run=False):
                if fourth:
                    return weekly_cabin.run_weekly_cabin_fourth_like(dry_run=dry_run, log_root=log_root)
                if third:
                    return weekly_cabin.run_weekly_cabin_third_like(dry_run=dry_run, log_root=log_root)
                if second:
                    return weekly_cabin.run_weekly_cabin_second_like(dry_run=dry_run, log_root=log_root)
                return weekly_cabin.run_weekly_cabin(
                    dry_run=dry_run, log_root=log_root, state_root=path.parent,
                    excluded_identity=excluded_identity,
                )

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
                    state, details = initial, {"identity": initial_identity, "count": 10}
                else:
                    state, details = "like_done", {"identity": after_identity, "count": after_count}
                with patch("weekly_cabin.recognize_cabin_page", return_value=(state, details)):
                    found, result = kwargs["recognize"](image)
                return found, image, result

            with patch("weekly_cabin.find_game_window", return_value=123), \
                 patch.object(weekly_cabin, "STATE_ROOT", first_path.parent), \
                 patch.object(weekly_cabin, "SECOND_STATE_ROOT", path.parent if second else second_path.parent), \
                 patch.object(weekly_cabin, "THIRD_STATE_ROOT", third_path.parent if fourth else path.parent), \
                 patch.object(weekly_cabin, "FOURTH_STATE_ROOT", path.parent), \
                 patch("weekly_cabin.game_week_key", return_value="2026-10-26"), \
                 patch("weekly_cabin.prepare_actionable_home", return_value=(True, image, "home")), \
                 patch("weekly_cabin.wait_for_recognition", side_effect=wait), \
                 patch("weekly_cabin.click_ratio_logged", side_effect=click), \
                 patch("weekly_cabin.time.sleep"):
                if crash:
                    with self.assertRaises(RuntimeError):
                        execute(root / "logs")
                    result = False, "crash"
                else:
                    result = execute(root / "logs", dry_run=dry_run)
                if path.exists():
                    records.append(json.loads(path.read_text(encoding="utf-8")))
                    with patch("weekly_cabin.find_game_window") as window:
                        again = execute(root / "again")
                    window.assert_not_called()
                    self.assertEqual(again[0], records[0]["status"] == "completed")
                self.assertEqual(first_path.read_bytes(), first_bytes)
                self.assertEqual(second_path.read_bytes(), second_bytes)
                if fourth:
                    self.assertEqual(third_path.read_bytes(), third_bytes)
        return result, keys, records

    def test_single_like_durable_before_input_and_same_week_skip(self):
        (ok, _), keys, records = self.run_flow()
        self.assertTrue(ok)
        self.assertEqual(keys.count("one_free_cabin_like"), 1)
        self.assertEqual(records[0]["status"], "completed")
        self.assertEqual(records[0]["after_count"], 11)

    def test_fourth_like_excludes_all_prior_cabins_and_preserves_their_records(self):
        (ok, _), keys, records = self.run_flow(fourth=True)
        self.assertTrue(ok)
        self.assertEqual(keys.count("one_free_cabin_like"), 1)
        self.assertEqual(records[0]["excluded_identities"], ["first", "second", "third"])
        for identity in ("first", "second", "third"):
            (ok, _), keys, records = self.run_flow(fourth=True, initial_identity=identity)
            self.assertFalse(ok)
            self.assertNotIn("one_free_cabin_like", keys)
            self.assertEqual(records, [])

    def test_fourth_like_failure_pending_blue_skip_and_dry_run(self):
        for kwargs, expected, sent in (
            ({"crash": True}, False, 1),
            ({"after_identity": "other"}, False, 1),
            ({"after_count": 10}, False, 1),
            ({"home": False}, False, 1),
            ({"initial": "unknown"}, False, 0),
            ({"initial": "like_done", "after_count": 10}, True, 0),
            ({"dry_run": True}, True, 0),
        ):
            with self.subTest(kwargs=kwargs):
                (ok, _), keys, records = self.run_flow(fourth=True, **kwargs)
                self.assertEqual(ok, expected)
                self.assertEqual(keys.count("one_free_cabin_like"), sent)
                if sent:
                    self.assertEqual(records[0]["status"], "action_pending")
                else:
                    self.assertEqual(records, [])

    def test_second_like_distinct_identity_and_pending_input_guard(self):
        (ok, _), keys, records = self.run_flow(second=True)
        self.assertTrue(ok)
        self.assertEqual(keys.count("one_free_cabin_like"), 1)
        self.assertEqual(records[0]["excluded_identity"], "first")
        (ok, _), keys, records = self.run_flow(excluded_identity="guest")
        self.assertFalse(ok)
        self.assertNotIn("one_free_cabin_like", keys)
        self.assertEqual(records, [])
        (ok, _), keys, records = self.run_flow(second=True, crash=True)
        self.assertFalse(ok)
        self.assertEqual(keys.count("one_free_cabin_like"), 1)
        self.assertEqual(records[0]["status"], "action_pending")

    def test_third_like_preserves_prior_records_and_never_replays_pending_input(self):
        (ok, _), keys, records = self.run_flow(third=True)
        self.assertTrue(ok)
        self.assertEqual(keys.count("one_free_cabin_like"), 1)
        self.assertEqual(records[0]["excluded_identities"], ["first", "second"])
        for options in ({"crash": True}, {"after_identity": "other"},
                        {"after_count": 10}, {"home": False}):
            with self.subTest(options=options):
                (ok, _), keys, records = self.run_flow(third=True, **options)
                self.assertFalse(ok)
                self.assertEqual(keys.count("one_free_cabin_like"), 1)
                self.assertEqual(records[0]["status"], "action_pending")

    def test_third_like_rejects_either_prior_identity_and_handles_blue_and_dry_run(self):
        for identity in ("first", "second"):
            with self.subTest(identity=identity):
                (ok, _), keys, records = self.run_flow(third=True, initial_identity=identity)
                self.assertFalse(ok)
                self.assertNotIn("one_free_cabin_like", keys)
                self.assertEqual(records, [])
        for options in ({"initial": "like_done", "after_count": 10}, {"dry_run": True}):
            (ok, _), keys, records = self.run_flow(third=True, **options)
            self.assertTrue(ok)
            self.assertNotIn("one_free_cabin_like", keys)
            self.assertEqual(records, [])

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
        self.assertEqual(stage.current_runners, (
            "weekly_cabin.run_weekly_cabin", "weekly_cabin.run_weekly_cabin_second_like",
            "weekly_cabin.run_weekly_cabin_third_like",
            "weekly_cabin.run_weekly_cabin_fourth_like",
        ))


class CabinSecondLikeTests(unittest.TestCase):
    def test_first_and_second_week_records_gate_without_touching_game(self):
        first = dict(version=1, week="2026-10-26", status="completed",
                     identity="first", before_count=10, after_count=11)
        second = dict(first, identity="second", excluded_identity="first")
        cases = [
            (None, None, True),
            (None, second, False),
            ("{", None, False),
            ([], None, False),
            (dict(first, status="action_pending"), None, False),
            (dict(first, after_count=12), None, False),
            (first, second, True),
            (first, dict(second, status="action_pending"), False),
            (first, dict(second, excluded_identity="another"), False),
            (first, dict(second, identity="first"), False),
        ]
        for initial, extra, expected in cases:
            with self.subTest(initial=initial, extra=extra), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for folder, value in (("first", initial), ("second", extra)):
                    if value is not None:
                        path = root / folder / "2026-10-26.json"
                        path.parent.mkdir()
                        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
                with patch.object(weekly_cabin, "STATE_ROOT", root / "first"), \
                     patch.object(weekly_cabin, "SECOND_STATE_ROOT", root / "second"), \
                     patch.object(weekly_cabin, "game_week_key", return_value="2026-10-26"), \
                     patch.object(weekly_cabin, "find_game_window") as window:
                    ok, _ = weekly_cabin.run_weekly_cabin_second_like(dry_run=False, log_root=root / "logs")
                self.assertEqual(ok, expected)
                window.assert_not_called()

    def test_week_rollover_does_not_reuse_previous_week_first_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first" / "2026-10-26.json"
            first.parent.mkdir()
            first.write_text(json.dumps(dict(version=1, week="2026-10-26", status="completed",
                                             identity="first", before_count=10, after_count=11)), encoding="utf-8")
            with patch.object(weekly_cabin, "STATE_ROOT", first.parent), \
                 patch.object(weekly_cabin, "SECOND_STATE_ROOT", root / "second"), \
                 patch.object(weekly_cabin, "game_week_key", side_effect=["2026-10-26", "2026-10-26", "2026-11-02"]), \
                 patch.object(weekly_cabin, "find_game_window") as window:
                ok, _ = weekly_cabin.run_weekly_cabin_second_like(dry_run=False, log_root=root / "logs")
            self.assertFalse(ok)
            window.assert_not_called()
            self.assertFalse((root / "second").exists())

    def test_real_weekly_stage_runs_four_in_order_and_propagates_failure(self):
        for first_ok, second_ok, third_ok, fourth_ok, expected in (
            (False, True, True, True, ["home", "first"]),
            (True, False, True, True, ["home", "first", "second"]),
            (True, True, False, True, ["home", "first", "second", "third"]),
            (True, True, True, False, ["home", "first", "second", "third", "fourth"]),
            (True, True, True, True, ["home", "first", "second", "third", "fourth"]),
        ):
            events = []
            def operation(name, ok):
                return lambda **_: (events.append(name) or ok, name)
            with tempfile.TemporaryDirectory() as temporary, \
                 patch.object(daily_automation, "ensure_home", operation("home", True)), \
                 patch.object(daily_automation, "run_weekly_cabin", operation("first", first_ok)), \
                 patch.object(daily_automation, "run_weekly_cabin_second_like", operation("second", second_ok)), \
                 patch.object(daily_automation, "run_weekly_cabin_third_like", operation("third", third_ok)), \
                 patch.object(daily_automation, "run_weekly_cabin_fourth_like", operation("fourth", fourth_ok)):
                root = Path(temporary)
                if first_ok and second_ok and third_ok and fourth_ok:
                    daily_automation._execute_daily_phase("weekly", master=daily_automation.MasterLogger(root), run_root=root)
                else:
                    with self.assertRaises(daily_automation.DailyRunError):
                        daily_automation._execute_daily_phase("weekly", master=daily_automation.MasterLogger(root), run_root=root)
            self.assertEqual(events, expected)

    def test_weekly_stage_is_skipped_only_when_all_four_like_slots_skip(self):
        from itertools import product
        for first_skip, second_skip, third_skip, fourth_skip in product((True, False), repeat=4):
            with self.subTest(slots=(first_skip, second_skip, third_skip, fourth_skip)), tempfile.TemporaryDirectory() as temporary, \
                 patch.object(daily_automation, "ensure_home", return_value=(True, "home")), \
                 patch.object(daily_automation, "run_weekly_cabin", return_value=(True, "skipped:first" if first_skip else "liked first")), \
                 patch.object(daily_automation, "run_weekly_cabin_second_like", return_value=(True, "skipped:second" if second_skip else "liked second")), \
                 patch.object(daily_automation, "run_weekly_cabin_third_like", return_value=(True, "skipped:third" if third_skip else "liked third")), \
                 patch.object(daily_automation, "run_weekly_cabin_fourth_like", return_value=(True, "skipped:fourth" if fourth_skip else "liked fourth")):
                root = Path(temporary)
                reason = daily_automation._execute_daily_phase("weekly", master=daily_automation.MasterLogger(root), run_root=root)
                self.assertEqual(reason.startswith("skipped:"), first_skip and second_skip and third_skip and fourth_skip)


class CabinThirdLikeTests(unittest.TestCase):
    def test_prior_chain_and_third_record_gate_without_touching_game(self):
        first = dict(version=1, week="2026-10-26", status="completed",
                     identity="first", before_count=10, after_count=11)
        second = dict(first, identity="second", excluded_identity="first")
        third = dict(first, identity="third", excluded_identities=["first", "second"])
        cases = [
            (None, None, None, True), (first, None, None, True),
            (None, second, None, False), (first, None, third, False),
            (None, None, third, False), ("{", second, None, False),
            (first, [], None, False), (dict(first, status="action_pending"), second, None, False),
            (first, dict(second, status="action_pending"), None, False),
            (first, dict(second, week="2026-10-19"), None, False),
            (first, dict(second, after_count=12), None, False),
            (first, dict(second, identity="first"), None, False),
            (first, dict(second, excluded_identity="other"), None, False),
            (first, second, third, True),
            (first, second, dict(third, status="action_pending"), False),
            (first, second, dict(third, excluded_identities=["second", "first"]), False),
            (first, second, dict(third, identity="first"), False),
            (first, second, dict(third, identity="second"), False),
            (first, second, dict(third, before_count=0, after_count=True), False),
        ]
        for a, b, c, expected in cases:
            with self.subTest(records=(a, b, c)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for folder, value in (("first", a), ("second", b), ("third", c)):
                    if value is not None:
                        path = root / folder / "2026-10-26.json"
                        path.parent.mkdir()
                        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
                with patch.object(weekly_cabin, "STATE_ROOT", root / "first"), \
                     patch.object(weekly_cabin, "SECOND_STATE_ROOT", root / "second"), \
                     patch.object(weekly_cabin, "THIRD_STATE_ROOT", root / "third"), \
                     patch.object(weekly_cabin, "game_week_key", return_value="2026-10-26"), \
                     patch.object(weekly_cabin, "find_game_window") as window:
                    ok, _ = weekly_cabin.run_weekly_cabin_third_like(dry_run=False, log_root=root / "logs")
                self.assertEqual(ok, expected)
                window.assert_not_called()

    def test_week_rollover_during_prerequisites_or_before_input_stops(self):
        for weeks in (["2026-10-26", "2026-11-02"],
                      ["2026-10-26", "2026-10-26", "2026-11-02"]):
            with self.subTest(weeks=weeks), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for folder, identity, extra in (("first", "first", {}),
                                                ("second", "second", {"excluded_identity": "first"})):
                    path = root / folder / "2026-10-26.json"
                    path.parent.mkdir()
                    path.write_text(json.dumps(dict(version=1, week="2026-10-26", status="completed",
                                                    identity=identity, before_count=10, after_count=11, **extra)))
                with patch.object(weekly_cabin, "STATE_ROOT", root / "first"), \
                     patch.object(weekly_cabin, "SECOND_STATE_ROOT", root / "second"), \
                     patch.object(weekly_cabin, "THIRD_STATE_ROOT", root / "third"), \
                     patch.object(weekly_cabin, "game_week_key", side_effect=weeks), \
                     patch.object(weekly_cabin, "find_game_window") as window:
                    ok, _ = weekly_cabin.run_weekly_cabin_third_like(dry_run=False, log_root=root / "logs")
                self.assertFalse(ok)
                window.assert_not_called()
                self.assertFalse((root / "third").exists())


class CabinFourthLikeTests(unittest.TestCase):
    def test_missing_corrupt_or_conflicting_chain_never_touches_game(self):
        first = dict(version=1, week="2026-10-26", status="completed",
                     identity="first", before_count=10, after_count=11)
        second = dict(first, identity="second", excluded_identity="first")
        third = dict(first, identity="third", excluded_identities=["first", "second"])
        fourth = dict(first, identity="fourth", excluded_identities=["first", "second", "third"])
        cases = [
            ([None, None, None, None], True), ([first, None, None, None], True),
            ([first, second, None, None], True),
            ([None, second, third, None], False), ([first, None, third, None], False),
            ([first, second, None, fourth], False), ([None, None, None, fourth], False),
            ([first, second, third, fourth], True),
        ]
        for index in range(4):
            record = [first, second, third, fourth][index]
            for invalid in ("{", [], dict(record, status="action_pending"),
                            dict(record, week="2026-10-19"), dict(record, identity=""),
                            dict(record, before_count=-1), dict(record, after_count=12),
                            dict(record, before_count=0, after_count=True)):
                chain = [first, second, third, fourth]
                chain[index] = invalid
                cases.append((chain, False))
        for index in (1, 2, 3):
            chain = [first, second, third, fourth]
            chain[index] = dict(chain[index], identity="first")
            cases.append((chain, False))
        cases.extend([
            ([first, dict(second, excluded_identity="other"), third, fourth], False),
            ([first, second, dict(third, excluded_identities=["second", "first"]), fourth], False),
            ([first, second, third, dict(fourth, excluded_identities=["first", "third", "second"])], False),
        ])
        for chain, expected in cases:
            with self.subTest(chain=chain), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                folders = [root / name for name in ("first", "second", "third", "fourth")]
                for folder, value in zip(folders, chain):
                    if value is not None:
                        folder.mkdir()
                        (folder / "2026-10-26.json").write_text(
                            value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
                before = {p: p.read_bytes() for p in root.glob("*/*.json")}
                with patch.object(weekly_cabin, "STATE_ROOT", folders[0]), \
                     patch.object(weekly_cabin, "SECOND_STATE_ROOT", folders[1]), \
                     patch.object(weekly_cabin, "THIRD_STATE_ROOT", folders[2]), \
                     patch.object(weekly_cabin, "FOURTH_STATE_ROOT", folders[3]), \
                     patch.object(weekly_cabin, "game_week_key", return_value="2026-10-26"), \
                     patch.object(weekly_cabin, "find_game_window") as window:
                    ok, _ = weekly_cabin.run_weekly_cabin_fourth_like(dry_run=False, log_root=root / "logs")
                self.assertEqual(ok, expected)
                window.assert_not_called()
                self.assertEqual({p: p.read_bytes() for p in root.glob("*/*.json")}, before)

    def test_week_rollover_requires_new_chain_and_stops_during_read_or_entry(self):
        for weeks, expected in (
            (["2026-11-02"], True),
            (["2026-10-26", "2026-11-02"], False),
            (["2026-10-26", "2026-10-26", "2026-11-02"], False),
        ):
            with self.subTest(weeks=weeks), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                folders = [root / name for name in ("first", "second", "third", "fourth")]
                for index in range(3):
                    folders[index].mkdir()
                    record = dict(version=1, week="2026-10-26", status="completed",
                                  identity=str(index), before_count=10, after_count=11)
                    if index == 1:
                        record["excluded_identity"] = "0"
                    if index == 2:
                        record["excluded_identities"] = ["0", "1"]
                    (folders[index] / "2026-10-26.json").write_text(json.dumps(record), encoding="utf-8")
                with patch.object(weekly_cabin, "STATE_ROOT", folders[0]), \
                     patch.object(weekly_cabin, "SECOND_STATE_ROOT", folders[1]), \
                     patch.object(weekly_cabin, "THIRD_STATE_ROOT", folders[2]), \
                     patch.object(weekly_cabin, "FOURTH_STATE_ROOT", folders[3]), \
                     patch.object(weekly_cabin, "game_week_key", side_effect=weeks), \
                     patch.object(weekly_cabin, "find_game_window") as window:
                    ok, _ = weekly_cabin.run_weekly_cabin_fourth_like(dry_run=False, log_root=root / "logs")
                self.assertEqual(ok, expected)
                window.assert_not_called()
                self.assertFalse(folders[3].exists())


if __name__ == "__main__":
    unittest.main()
