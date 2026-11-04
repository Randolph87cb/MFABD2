from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import adventure_route as route
import daily_automation as daily
from daily_plan import get_daily_plan

FIXTURES = Path(__file__).parent / "fixtures/recognition"
MAP = dict(target="哥布林遗迹", difficulty="极难", free_rice=0)
SETUP = dict(**MAP, count=1, rice_cost=6, fixed_gold=22000, free_only=True)


class RouteRecognitionTests(unittest.TestCase):
    def test_restored_hunting_map_is_valid_next_step(self):
        with Image.open(FIXTURES / "adventure-hunting-restored.png") as image:
            found, details = route._hunting_map(image)
            self.assertTrue(found, details)
            self.assertEqual(details["free_rice"], 0)

    def test_native_pages_and_adjacent_pages(self):
        for name in ("adventure-gold-map", "adventure-gold-cancelled",
                     "adventure-gold-setup-1", "adventure-gold-setup-2",
                     "adventure-hunting-restored", "quick-hunt-setup", "quick-hunt-result"):
            with self.subTest(name=name), Image.open(FIXTURES / (name + ".png")) as image:
                for setup in (False, True):
                    value, details = route.recognize_gold_route(image, setup=setup)
                    expected = (SETUP if "adventure-gold-setup" in name and setup else
                                MAP if name in ("adventure-gold-map", "adventure-gold-cancelled")
                                and not setup else None)
                    self.assertEqual(value, expected, details)

    def test_dimmed_disabled_switch_and_unknown_cost_are_rejected(self):
        with Image.open(FIXTURES / "adventure-gold-setup-2.png") as source:
            images = [ImageEnhance.Brightness(source).enhance(0.4), source.resize((1152, 648))]
            for box in (route.SWITCH_BOX, (1275, 915, 1310, 965), (932, 918, 999, 962)):
                changed = source.convert("RGB")
                changed.paste((30, 30, 30), box)
                images.append(changed)
            for image in images:
                self.assertIsNone(route.recognize_gold_route(image, setup=True)[0])
            with patch("adventure_route._ocr_engine", side_effect=RuntimeError("unavailable")):
                value, details = route.recognize_gold_route(source, setup=True)
                self.assertIsNone(value)
                self.assertIn("unavailable", details["error"])

    def test_ocr_conflicts_and_low_confidence_never_authorize_input(self):
        with Image.open(FIXTURES / "adventure-gold-setup-2.png") as source:
            original = route._entries(source)
            for entries in (
                original + [(860, 366, "快速狩猎", .99)],
                original + [(1150, 600, "确认购买", .8)],
                [(x, y, t, .94 if t == "仅使用免费米饭" else s) for x, y, t, s in original],
                [(x, y, t, float("nan") if t == "狩猎1次" else s) for x, y, t, s in original],
                [(x, y, "狩猎2次" if t == "狩猎1次" else t, s) for x, y, t, s in original],
                [(x, y, "23,000" if t == "22,000" else t, s) for x, y, t, s in original],
            ):
                original_entries = route._entries
                def read(image, **kwargs):
                    return entries if image.size == source.size else original_entries(image, **kwargs)
                with patch("adventure_route._entries", side_effect=read):
                    self.assertIsNone(route.recognize_gold_route(source, setup=True)[0])

    def test_invalid_rice_never_means_empty(self):
        with Image.open(FIXTURES / "adventure-gold-map.png") as source:
            for text, score in (("0/90", .94), ("0/90", float("nan")), ("91/90", .99),
                                ("00/90", .99), ("0/60", .99), ("0/90+9.72K", .99)):
                with self.subTest(text=text, score=score), patch(
                    "adventure_route._crop_entries", return_value=[(150, 55, text, score)]
                ):
                    self.assertIsNone(route.recognize_gold_route(source)[0])
            with patch("adventure_route._crop_entries", return_value=[(150, 55, "0/90", .99)] * 2):
                self.assertIsNone(route.recognize_gold_route(source)[0])


class RouteFlowTests(unittest.TestCase):
    def flow(self, *, failure=None, changed=None, reward=False, dry_run=False, bad_start=False):
        image = Image.new("RGB", (2304, 1296), "white")
        events = []

        def wait(hwnd, *, label, **kwargs):
            events.append(label)
            data = ({"free_rice": 0} if "hunting" in label else
                    {"reading": dict(SETUP if "setup" in label else MAP)})
            if changed == label:
                if "reading" in data:
                    data["reading"]["free_rice"] = 1
                else:
                    data["free_rice"] = 1
            return failure != label, image, data

        with tempfile.TemporaryDirectory() as temporary, \
                patch("adventure_route.find_game_window", return_value=123), \
                patch("adventure_route.safe_capture_client", return_value=image), \
                patch("adventure_route.classify_state", return_value=("reward_overlay" if reward else "quick_hunt_map", {})), \
                patch("adventure_route.recognize_reward_overlay_labels", return_value=(not bad_start, {})), \
                patch("adventure_route._hunting_map", return_value=(not bad_start, {"free_rice": 0})), \
                patch("adventure_route.wait_for_recognition", side_effect=wait), \
                patch("adventure_route.time.sleep"), \
                patch("adventure_route.click_ratio_logged") as click:
            result = route.run_adventure_route_recheck(dry_run=dry_run, log_root=Path(temporary))
            keys = [c.kwargs["key"] for c in click.call_args_list]
        return result, keys, events

    def test_cancel_and_restore_without_hunting(self):
        for reward in (False, True):
            (ok, reason), keys, events = self.flow(reward=reward)
            self.assertTrue(ok, reason)
            self.assertEqual(keys, (["route_dismiss_hunting_result"] if reward else []) + [
                "route_gold_category", "route_open_setup", "route_cancel_setup", "route_restore_hunting"])
            self.assertEqual(len(events), 6 if reward else 5)

    def test_each_failure_stops_without_retry_or_later_input(self):
        expected_clicks = {"route-hunting-entry": 1, "route-gold-map": 2,
                           "route-setup-1": 3, "route-setup-2": 3,
                           "route-after-cancel": 4, "route-restored-hunting": 5}
        for failure, count in expected_clicks.items():
            with self.subTest(failure=failure):
                (ok, _), keys, _ = self.flow(failure=failure, reward=True)
                self.assertFalse(ok)
                self.assertEqual(len(keys), count)

    def test_changed_readings_stop_before_next_input(self):
        for changed, count in (("route-gold-map", 1), ("route-setup-2", 2),
                               ("route-after-cancel", 3), ("route-restored-hunting", 4)):
            with self.subTest(changed=changed):
                (ok, _), keys, _ = self.flow(changed=changed)
                self.assertFalse(ok)
                self.assertEqual(len(keys), count)

    def test_dry_run_and_unknown_start_never_input(self):
        for reward in (False, True):
            for bad_start in (False, True):
                (ok, _), keys, _ = self.flow(reward=reward, bad_start=bad_start, dry_run=True)
                self.assertEqual(ok, not bad_start)
                self.assertEqual(keys, [])

    def test_daily_order_failure_propagation_and_partial_plan(self):
        names = ("ensure_home", "enter_quick_hunt", "start_selected_quick_hunt",
                 "maximize_and_confirm_quick_hunt", "run_adventure_route_recheck",
                 "run_rice_recheck", "run_crystal_cave_cycle")
        for fail in (None, "run_adventure_route_recheck"):
            from contextlib import ExitStack
            calls = []
            def operation(name):
                def run(**kwargs):
                    calls.append(name)
                    return name != fail, name
                return run
            with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                for name in names:
                    stack.enter_context(patch.object(daily, name, operation(name)))
                root = Path(temporary)
                if fail:
                    with self.assertRaises(daily.DailyRunError):
                        daily._execute_daily_phase("quick_hunt", master=daily.MasterLogger(root), run_root=root)
                else:
                    daily._execute_daily_phase("quick_hunt", master=daily.MasterLogger(root), run_root=root)
            self.assertEqual(calls, list(names[:5] if fail else names))
        stage = next(s for s in get_daily_plan("fast") if s.id == "quick_hunt")
        self.assertEqual(stage.availability, "partial")
        self.assertIn("adventure_route.run_adventure_route_recheck", stage.current_runners)


if __name__ == "__main__":
    unittest.main()
