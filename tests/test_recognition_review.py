from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from recognition_review import ReviewStore


class RecognitionReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "tests" / "fixtures" / "recognition").mkdir(parents=True)
        (self.root / "logs" / "daily").mkdir(parents=True)
        self.fixture = self.root / "tests" / "fixtures" / "recognition" / "home.png"
        Image.new("RGB", (80, 45), color=(24, 30, 36)).save(self.fixture)
        (self.root / "tests" / "recognition_cases.json").write_text(
            json.dumps(
                [
                    {
                        "path": "fixtures/recognition/home.png",
                        "expected": "real_home",
                        "reason": "主页测试",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.store = ReviewStore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_catalog_combines_test_cases_and_failed_run_screenshots(self) -> None:
        run_root = self.root / "logs" / "daily" / "2026-08-29" / "104911"
        step_root = run_root / "04-quick-hunt"
        step_root.mkdir(parents=True)
        screenshot = step_root / "step-001-home_overlay.png"
        Image.new("RGB", (80, 45), color=(10, 12, 14)).save(screenshot)
        (run_root / "summary.json").write_text(
            json.dumps(
                {
                    "result": "failed",
                    "reason": "quick-hunt entry requires real_home, got home_overlay",
                    "started_at": "2026-08-29T10:49:11",
                }
            ),
            encoding="utf-8",
        )
        (step_root / "events.jsonl").write_text(
            json.dumps(
                {
                    "action": "classify",
                    "state": "home_overlay",
                    "screenshot": str(screenshot),
                }
            )
            + "\n",
            encoding="utf-8",
        )

        catalog = self.store.catalog()

        self.assertEqual(len(catalog["items"]), 2)
        test_item = next(item for item in catalog["items"] if item["source"] == "test")
        daily_item = next(item for item in catalog["items"] if item["source"] == "daily")
        self.assertEqual(test_item["expected_state"], "real_home")
        self.assertEqual(daily_item["recorded_state"], "home_overlay")
        self.assertIn("quick-hunt entry", daily_item["reason"])

    def test_annotation_is_written_for_the_selected_item_only(self) -> None:
        item = self.store.items()[0]

        annotation = self.store.save_annotation(
            item.id,
            "quick_hunt_map",
            "左侧显示快速狩猎分类",
        )

        payload = json.loads(self.store.annotation_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["id"], item.id)
        self.assertEqual(annotation["correct_state"], "quick_hunt_map")
        self.assertEqual(annotation["note"], "左侧显示快速狩猎分类")

    def test_single_annotation_can_be_cancelled_without_a_clear_all_action(self) -> None:
        item = self.store.items()[0]
        self.store.save_annotation(item.id, "real_home", "误标")

        removed = self.store.delete_annotation(item.id)

        self.assertTrue(removed)
        payload = json.loads(self.store.annotation_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["items"], [])


if __name__ == "__main__":
    unittest.main()
