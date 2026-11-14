from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import chapter_probe as probe
import daily_automation as daily

FIXTURES = Path(__file__).parent / "fixtures/recognition"


def load(name):
    with Image.open(FIXTURES / (name + ".png")) as source:
        return source.convert("RGB")


class ProbeRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.map = load("chapter-probe-map-1")
        cls.talent = load("chapter-probe-talent-1")
        cls.entries = probe.chapter._entries(cls.talent)
        cls.map_entries = probe.chapter._entries(cls.map)

    def test_native_counter_cost_and_unchanged_result(self):
        for name in ("chapter-probe-map-1", "chapter-probe-map-2", "chapter-probe-map-after"):
            found, details = probe.recognize_probe_map(load(name))
            self.assertTrue(found, details)
            self.assertEqual((details["collected"], details["limit"]), (0, 3))
        for name in ("chapter-probe-talent-1", "chapter-probe-talent-2"):
            found, details = probe.recognize_probe_talent(load(name))
            self.assertTrue(found, details)
            self.assertEqual((details["cost"], details["balance"]), (2, 1216363))
            self.assertNotIn("usable", details)
        self.assertTrue(probe.recognize_probe_return_field(load("chapter-probe-return-field"))[0])
        found, details = probe.recognize_probe_home(load("chapter-probe-home"))
        self.assertTrue(found, details)
        self.assertEqual(details["balance"], 1216363)

    def test_adjacent_obscured_resized_and_moved_card_are_rejected(self):
        for image in (self.map, load("chapter-map-submenu"), load("chapter-portal"),
                      self.talent.resize((1280, 720)), ImageEnhance.Brightness(self.talent).enhance(.4)):
            self.assertFalse(probe.recognize_probe_talent(image)[0])
        for region in ((1225, 980, 1315, 1050), (1220, 1095, 1320, 1132), (1250, 1131, 1292, 1166)):
            candidate = self.talent.copy()
            candidate.paste("black", region)
            self.assertFalse(probe.recognize_probe_talent(candidate)[0])
        self.assertFalse(probe.recognize_probe_map(self.talent)[0])
        self.assertFalse(probe.recognize_probe_map(load("chapter-map-submenu"))[0])
        self.assertFalse(probe.recognize_probe_return_field(self.talent)[0])
        self.assertFalse(probe.recognize_probe_home(self.talent)[0])

    def test_missing_duplicate_low_confidence_or_dangerous_text_is_rejected(self):
        target = next(e for e in self.entries if e[2] == "探查" and 1220 <= e[0] <= 1320)
        for replacement in ([], [(*target[:3], .94)], [(*target[:3], float("nan"))], [target, target]):
            values = [e for e in self.entries if e != target] + replacement
            with patch.object(probe.chapter, "_entries", return_value=values):
                self.assertFalse(probe.recognize_probe_talent(self.talent)[0])
        with patch.object(probe.chapter, "_entries", return_value=self.entries + [(1100, 600, "确认购买", .99)]):
            self.assertFalse(probe.recognize_probe_talent(self.talent)[0])
        with patch.object(probe.chapter, "_entries", side_effect=RuntimeError("OCR failed")):
            self.assertFalse(probe.recognize_probe_talent(self.talent)[0])
        with patch.object(probe.chapter, "_crop_text", return_value=None):
            self.assertFalse(probe.recognize_probe_talent(self.talent)[0])

    def test_probe_counter_needs_complete_unique_confident_value(self):
        target = next(e for e in self.map_entries if 385 <= e[0] <= 460 and 1185 <= e[1] <= 1230)
        for text, score in (("0", .99), ("0/4", .99), ("4/3", .99), ("0/3", .94), ("0/3", float("nan"))):
            entries = [e for e in self.map_entries if e != target] + [(target[0], target[1], text, score)]
            with patch.object(probe.chapter, "_entries", return_value=entries):
                self.assertFalse(probe.recognize_probe_map(self.map)[0])
        with patch.object(probe.chapter, "_entries", return_value=self.map_entries + [target]):
            self.assertFalse(probe.recognize_probe_map(self.map)[0])


class ProbeFlowTests(unittest.TestCase):
    def run_flow(self, *, fail=None, change=None, nonempty=False, dry_run=False, no_window=False):
        home = load("chapter-probe-home")
        calls = []
        captures = []

        def wait(hwnd, *, recognize, label, **kwargs):
            captures.append(label)
            details = {"state": "field", "empty_slot": not nonempty}
            if label == "probe-collection":
                details = {"state": "collection"}
            elif label == "probe-story-card":
                details = {"state": "chapter_card"}
            elif label.startswith("probe-map"):
                details = {"collected": 0, "limit": 3}
            elif label.startswith("probe-talent-"):
                details = {"cost": 2, "balance": 1216363}
            elif label == "probe-home":
                details = {"balance": 1216363}
            if change and label == change[0]:
                details[change[1]] = change[2]
            return label != fail, home, details

        def click(hwnd, image, point, *, key, **kwargs):
            calls.append((key, point))

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(probe, "find_game_window", return_value=None if no_window else 123))
            stack.enter_context(patch.object(probe, "prepare_actionable_home", return_value=(True, home, "home")))
            stack.enter_context(patch.object(probe, "recognize_probe_home", return_value=(True, {"balance": 1216363})))
            stack.enter_context(patch.object(probe, "wait_for_recognition", side_effect=wait))
            stack.enter_context(patch.object(probe, "click_ratio_logged", side_effect=click))
            stack.enter_context(patch.object(probe.time, "sleep"))
            result = probe.run_chapter_probe_recheck(dry_run=dry_run, log_root=Path(temporary))
            self.assertFalse(list(Path(temporary).rglob("*.json")), "read-only flow must not write collection state")
        return result, calls, captures

    def test_readonly_navigation_has_no_card_input_and_checks_return(self):
        result, calls, captures = self.run_flow()
        self.assertTrue(result[0], result)
        self.assertIn("未施放或拾取", result[1])
        self.assertEqual(calls[-1][0], "probe_return_home")
        self.assertEqual(captures[-1], "probe-home")
        # Card text/cost/upgrade is never an input target, even with ample potions.
        self.assertFalse(any(940 < point[1] * 1296 < 1240 and point[0] * 2304 < 1700 for _, point in calls))
        self.assertEqual(sum(key == "probe_talent_page" for key, _ in calls), 1)

    def test_each_failed_observation_stops_without_retrying_an_input(self):
        _, successful, labels = self.run_flow()
        for label in labels:
            result, calls, captures = self.run_flow(fail=label)
            self.assertFalse(result[0], label)
            self.assertEqual(captures[-1], label)
            self.assertLessEqual(len(calls), len(successful))
            self.assertEqual(len(calls), len(set(key for key, _ in calls)))

    def test_changed_frames_counter_or_potions_never_complete(self):
        for change in (("probe-map-2", "collected", 1), ("probe-talent-2", "cost", 3),
                       ("probe-talent-2", "balance", 1216361), ("probe-talent-1", "balance", 1),
                       ("probe-map-after-1", "collected", 1), ("probe-map-after-2", "collected", 1),
                       ("probe-home", "balance", 1216361)):
            result, _, _ = self.run_flow(change=change)
            self.assertFalse(result[0], change)

    def test_configured_slot_dry_run_and_missing_window_send_no_skill_input(self):
        result, calls, _ = self.run_flow(nonempty=True)
        self.assertFalse(result[0])
        self.assertNotIn("probe_talent_page", [key for key, _ in calls])
        for options in ({"dry_run": True}, {"no_window": True}):
            result, calls, _ = self.run_flow(**options)
            self.assertEqual(result[0], options.get("dry_run", False))
            self.assertEqual(calls, [])

    def test_real_phase_preserves_skip_and_propagates_audit_failure(self):
        for absorb_reason, probe_ok in (("skipped:cooldown", True), ("absorb completed", True), ("skipped:cooldown", False)):
            order = []
            with tempfile.TemporaryDirectory() as temporary, \
                 patch.object(daily, "ensure_home", return_value=(True, "home")), \
                 patch.object(daily, "run_chapter_absorb", side_effect=lambda **_: (order.append("absorb") or True, absorb_reason)), \
                 patch.object(daily, "run_chapter_probe_recheck", side_effect=lambda **_: (order.append("probe") or probe_ok, "audit")):
                root = Path(temporary)
                if probe_ok:
                    self.assertEqual(daily._execute_daily_phase("chapter_collection", master=daily.MasterLogger(root), run_root=root), absorb_reason)
                else:
                    with self.assertRaises(daily.DailyRunError):
                        daily._execute_daily_phase("chapter_collection", master=daily.MasterLogger(root), run_root=root)
                self.assertEqual(order, ["absorb", "probe"])


if __name__ == "__main__":
    unittest.main()
