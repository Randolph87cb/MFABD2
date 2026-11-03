from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation
import restaurant_decor as decor
from daily_plan import get_daily_plan

FIXTURES = Path(__file__).parent / "fixtures/recognition"
FIRST = "restaurant-decor-balance-first-2304x1296.png"
SECOND = "restaurant-decor-balance-second-2304x1296.png"
READING = dict(balance=16078, earned_today=0, daily_limit=20)


class DecorRecognitionTests(unittest.TestCase):
    def test_real_pc_frames_tooltip_and_adjacent_pages(self):
        for name in (FIRST, SECOND, "restaurant-decor-tooltip-2304x1296.png",
                     "business-management-claim-disabled-2304x1296.png",
                     "home-bright-video-background.png"):
            with self.subTest(name=name), Image.open(FIXTURES / name) as image:
                reading, details = decor.recognize_decor_balance(image)
                self.assertEqual(reading, READING if name in (FIRST, SECOND) else None, details)

    def test_dimmed_size_and_ocr_failure_refuse_reading(self):
        with Image.open(FIXTURES / FIRST) as image:
            self.assertIsNone(decor.recognize_decor_balance(ImageEnhance.Brightness(image).enhance(0.4))[0])
            self.assertIsNone(decor.recognize_decor_balance(image.resize((1152, 648)))[0])
            with patch("restaurant_decor._ocr_engine", side_effect=RuntimeError("unavailable")):
                reading, details = decor.recognize_decor_balance(image)
            self.assertIsNone(reading)
            self.assertIn("unavailable", details["error"])

    def recognize_entries(self, extra=(), daily="每日0/20", balance="16,078", confidence=0.99):
        entries = [(242, 69, "格鲁菲餐厅", .99), (203, 1238, "员工", .99),
                   (341, 1240, "客人", .99), (2011, 1189, "结算", .99),
                   (1484, 69, balance, confidence), (1434, 106, daily, .99)] + list(extra)
        boxes = [np.array([[x-10, y-5], [x+10, y-5], [x+10, y+5], [x-10, y+5]])
                 for x, y, _, _ in entries]
        result = SimpleNamespace(boxes=boxes, txts=[v[2] for v in entries], scores=[v[3] for v in entries])
        image = Image.new("RGB", (2304, 1296), "white")
        with patch("restaurant_decor.classify_state", return_value=("restaurant_home", {})), \
                patch("restaurant_decor._ocr_engine") as engine:
            engine.return_value.return_value = result
            return decor.recognize_decor_balance(image)

    def test_daily_range_and_balance_are_separate_from_other_resources(self):
        for count in (0, 1, 19, 20):
            reading, details = self.recognize_entries(
                daily=f"每日{count}/20", extra=[(1310, 69, "918,482", .99)])
            self.assertEqual(reading, {**READING, "earned_today": count}, details)
        for daily in ("每日21/20", "每日0/30", "每日O/20", "每日/20", "0/20", "每日00/20"):
            self.assertIsNone(self.recognize_entries(daily=daily)[0], daily)
        for total in ("16.0K", "16,07", "-1", "+16078", "016078"):
            self.assertIsNone(self.recognize_entries(balance=total)[0], total)

    def test_ambiguous_low_confidence_dialog_and_wrong_title_refuse(self):
        for score in (.94, float("nan")):
            self.assertIsNone(self.recognize_entries(confidence=score)[0])
        for extra in ([(1480, 70, "16,078", .99)], [(1430, 105, "每日20/20", .6)],
                      [(242, 69, "其他餐厅", .99)], [(1700, 500, "装饰币", .9)],
                      [(1700, 500, "确认购买", .8)]):
            self.assertIsNone(self.recognize_entries(extra=extra)[0], extra)


class DecorFlowTests(unittest.TestCase):
    def run_flow(self, readings=None, *, dry_run=False, fail=None):
        image = Image.new("RGB", (2304, 1296), "white")
        events = []

        def operation(name):
            def run(**kwargs):
                events.append(name)
                return fail != name, name
            return run

        with tempfile.TemporaryDirectory() as temporary, \
                patch("restaurant_decor.find_game_window", return_value=123), \
                patch("restaurant_decor.prepare_actionable_home", return_value=(True, image, "home")), \
                patch("restaurant_decor.open_business_management", side_effect=operation("entry")), \
                patch("restaurant_decor.enter_restaurant", side_effect=operation("restaurant")), \
                patch("restaurant_decor.return_home_from_restaurant", side_effect=operation("return")), \
                patch("restaurant_decor.safe_capture_client", return_value=image), \
                patch("restaurant_decor.recognize_decor_balance", side_effect=readings or [(READING, {})] * 2), \
                patch("restaurant_decor.time.sleep"), \
                patch("reward_flow.click_client") as click:
            result = decor.run_restaurant_decor_recheck(dry_run=dry_run, log_root=Path(temporary))
            click.assert_not_called()
        return result, events

    def test_two_reads_return_home_and_never_claim_completion_of_collection(self):
        (ok, reason), events = self.run_flow()
        self.assertTrue(ok)
        self.assertIn("每日 0/20", reason)
        self.assertIn("未领取或消费", reason)
        self.assertEqual(events, ["entry", "restaurant", "return"])

    def test_unknown_or_changing_reading_stops_before_return(self):
        for readings in ([(None, {})], [(READING, {}), (None, {})],
                         [(READING, {}), ({**READING, "balance": 16079}, {})],
                         [(READING, {}), ({**READING, "earned_today": 1}, {})]):
            (ok, _), events = self.run_flow(readings)
            self.assertFalse(ok)
            self.assertNotIn("return", events)

    def test_navigation_failure_and_dry_run(self):
        for fail, expected in (("entry", ["entry"]), ("restaurant", ["entry", "restaurant"]),
                               ("return", ["entry", "restaurant", "return"])):
            (ok, _), events = self.run_flow(fail=fail)
            self.assertFalse(ok)
            self.assertEqual(events, expected)
        (ok, _), events = self.run_flow(dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(events, [])

    def test_daily_order_failure_and_partial_plan(self):
        for failed in (None, "decor"):
            events = []

            def operation(name):
                return lambda **_: (events.append(name) or name != failed, name)

            with tempfile.TemporaryDirectory() as temporary, \
                    patch("daily_automation.ensure_home", side_effect=operation("home")), \
                    patch("daily_automation.run_business_management", side_effect=operation("business")), \
                    patch("daily_automation.run_restaurant_decor_recheck", side_effect=operation("decor")), \
                    patch("daily_automation.run_plaza_prayer", side_effect=lambda **_: (events.append("prayer") or True, "skipped:no prayer")):
                args = dict(master=daily_automation.MasterLogger(Path(temporary)), run_root=Path(temporary))
                if failed:
                    with self.assertRaises(daily_automation.DailyRunError):
                        daily_automation._execute_daily_phase("daily_claims", **args)
                else:
                    reason = daily_automation._execute_daily_phase("daily_claims", **args)
                    self.assertFalse(reason.startswith("skipped:"))
            self.assertEqual(events, ["home", "business", "decor"] + ([] if failed else ["prayer"]))
        for preset in ("fast", "detailed"):
            stage = next(item for item in get_daily_plan(preset) if item.id == "daily_claims")
            self.assertFalse(stage.implemented)
            self.assertEqual(stage.availability, "partial")
            self.assertIn("restaurant_decor.run_restaurant_decor_recheck", stage.current_runners)


if __name__ == "__main__":
    unittest.main()
