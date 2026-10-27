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
import plaza_prayer
from daily_plan import get_daily_plan


FIXTURES = Path(__file__).parent / "fixtures/recognition"


class PrayerRecognitionTests(unittest.TestCase):
    def test_pc_evidence_and_unrelated_pages(self):
        cases = {
            "plaza-prayer-life-cards-2304x1296.png": "square_card",
            "plaza-prayer-ready-2304x1296.png": "prayer_ready",
            "plaza-prayer-unavailable-2304x1296.png": "prayer_unavailable",
            "home-bright-video-background.png": "unknown",
            "restaurant-home-2567x1446.png": "unknown",
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename), Image.open(FIXTURES / filename) as image:
                state, details = plaza_prayer.recognize_prayer_page(image)
                self.assertEqual(state, expected, details)
                if state == "square_card":
                    self.assertAlmostEqual(details["card_point"][0], 660 / 2304, places=2)
                    self.assertAlmostEqual(details["card_point"][1], 704 / 1296, places=2)

    def test_dimmed_square_and_failed_ocr_are_not_completed(self):
        with Image.open(FIXTURES / "plaza-prayer-unavailable-2304x1296.png") as image:
            state, _ = plaza_prayer.recognize_prayer_page(ImageEnhance.Brightness(image).enhance(0.4))
            self.assertEqual(state, "unknown")
            with patch("plaza_prayer._ocr_engine", side_effect=RuntimeError("OCR failed")):
                state, details = plaza_prayer.recognize_prayer_page(image)
            self.assertEqual(state, "unknown")
            self.assertIn("OCR failed", details["error"])

    def test_low_confidence_prayer_or_only_one_remaining_label_stops(self):
        def box(x, y):
            return np.array([[x - 20, y - 10], [x + 20, y - 10],
                             [x + 20, y + 10], [x - 20, y + 10]])
        image = Image.new("RGB", (2304, 1296), "white")
        for labels, scores in [(["向女神像许愿"], [0.75]),
                               (["向女神像许愿"], [0.99]),
                               (["每日奖励"], [0.99])]:
            points = [(1160, 162)] if labels[0] != "每日奖励" else [(1944, 236)]
            result = SimpleNamespace(
                boxes=[box(500, 192), box(2055, 178)] + [box(*point) for point in points],
                txts=["招募情况", "创建队伍"] + labels, scores=[0.99, 0.99] + scores,
            )
            with patch("plaza_prayer._ocr_engine") as engine:
                engine.return_value.return_value = result
                state, _ = plaza_prayer.recognize_prayer_page(image)
            self.assertEqual(state, "unknown")

    def test_collection_with_incomplete_unlock_count_cannot_enter(self):
        image = Image.new("RGB", (2304, 1296), "white")
        points = [(394, 56), (540, 545), (660, 834)]
        boxes = [np.array([[x - 10, y - 5], [x + 10, y - 5],
                           [x + 10, y + 5], [x - 10, y + 5]]) for x, y in points]
        result = SimpleNamespace(boxes=boxes, scores=[0.99] * 3,
                                 txts=["游戏卡珍藏集", "生活玩法游戏卡带3/4", "奇幻广场"])
        with patch("plaza_prayer._ocr_engine") as engine:
            engine.return_value.return_value = result
            state, details = plaza_prayer.recognize_prayer_page(image)
        self.assertEqual(state, "collection")
        self.assertNotIn("card_point", details)


class PrayerFlowTests(unittest.TestCase):
    def run_flow(self, *, ready=True, clears=True, home=True, dry_run=False):
        image = Image.new("RGB", (2304, 1296), "white")
        states = [
            (True, image, {"state": "square_card", "card_point": (0.286, 0.543)}),
            (True, image, {"state": "prayer_ready" if ready else "prayer_unavailable",
                           "wish_point": (0.503, 0.125)}),
            (clears, image, {"state": "prayer_unavailable" if clears else "unknown"}),
            (True, image, {"state": "prayer_unavailable"}),
            (home, image, {}),
        ]
        with tempfile.TemporaryDirectory() as temporary, \
                patch("plaza_prayer.find_game_window", return_value=123), \
                patch("plaza_prayer.prepare_actionable_home", return_value=(True, image, "home")), \
                patch("plaza_prayer.wait_for_recognition", side_effect=states), \
                patch("plaza_prayer.click_ratio_logged") as click, \
                patch("plaza_prayer.time.sleep"):
            result = plaza_prayer.run_plaza_prayer(dry_run=dry_run, log_root=Path(temporary))
        return result, [call.kwargs["key"] for call in click.call_args_list]

    def test_one_prayer_only_after_ready_and_home_required(self):
        (ok, _), keys = self.run_flow()
        self.assertTrue(ok)
        self.assertEqual(keys, ["prayer_game_cards", "square_cartridge",
                                "free_goddess_prayer", "prayer_home"])
        (ok, _), _keys = self.run_flow(home=False)
        self.assertFalse(ok)

    def test_uncleared_result_fails_without_retry_or_blind_return(self):
        (ok, _), keys = self.run_flow(clears=False)
        self.assertFalse(ok)
        self.assertEqual(keys.count("free_goddess_prayer"), 1)
        self.assertNotIn("prayer_home", keys)

    def test_initial_unavailable_skips_without_claiming_completion(self):
        (ok, reason), keys = self.run_flow(ready=False)
        self.assertTrue(ok)
        self.assertIn("不推断此前已完成", reason)
        self.assertNotIn("free_goddess_prayer", keys)

    def test_dry_run_has_no_real_input(self):
        image = Image.new("RGB", (2304, 1296), "white")
        with tempfile.TemporaryDirectory() as temporary, \
                patch("plaza_prayer.find_game_window", return_value=123), \
                patch("plaza_prayer.prepare_actionable_home", return_value=(True, image, "home")), \
                patch("reward_flow.click_client") as click:
            ok, reason = plaza_prayer.run_plaza_prayer(dry_run=True, log_root=Path(temporary))
        self.assertTrue(ok)
        self.assertIn("no prayer sent", reason)
        click.assert_not_called()

    def test_daily_claims_integration_order_and_failure_propagation(self):
        events = []
        with tempfile.TemporaryDirectory() as temporary, \
                patch("daily_automation.ensure_home", side_effect=lambda **_: (events.append("home") or True, "home")), \
                patch("daily_automation.run_business_management", side_effect=lambda **_: (events.append("business") or True, "business")), \
                patch("daily_automation.run_plaza_prayer", side_effect=lambda **_: (events.append("prayer") or False, "unknown square")):
            with self.assertRaises(daily_automation.DailyRunError):
                daily_automation._execute_daily_phase(
                    "daily_claims", master=daily_automation.MasterLogger(Path(temporary)),
                    run_root=Path(temporary),
                )
        self.assertEqual(events, ["home", "business", "prayer"])
        stage = next(item for item in get_daily_plan("fast") if item.id == "daily_claims")
        self.assertFalse(stage.implemented)
        self.assertIn("plaza_prayer.run_plaza_prayer", stage.current_runners)


if __name__ == "__main__":
    unittest.main()
