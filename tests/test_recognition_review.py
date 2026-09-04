from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from recognition_review import ReviewStore, _failure_reason_label
from game_text_recognition import _partial_similarity


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
        (run_root / "events.jsonl").write_text(
            json.dumps(
                {
                    "time": "2026-08-29T10:50:00",
                    "stage": "quick_hunt_entry",
                    "status": "error",
                    "message": "执行失败",
                    "log_root": str(step_root),
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        catalog = self.store.catalog()

        self.assertEqual(len(catalog["items"]), 2)
        test_item = next(item for item in catalog["items"] if item["source"] == "test")
        daily_item = next(item for item in catalog["items"] if item["source"] == "daily")
        self.assertEqual(test_item["expected_state"], "real_home")
        self.assertEqual(test_item["expected_state_label"], "主页")
        self.assertEqual(daily_item["recorded_state"], "home_overlay")
        self.assertEqual(daily_item["recorded_state_label"], "弹窗页面")
        self.assertEqual(catalog["state_labels"]["real_home"], "主页")
        self.assertEqual(catalog["state_labels"]["home_overlay"], "弹窗页面")
        self.assertEqual(
            daily_item["reason"],
            "进入快速狩猎失败：进入快速狩猎前需要处于主页，实际识别为弹窗页面。",
        )
        self.assertEqual(daily_item["stage_label"], "进入快速狩猎")
        self.assertEqual(daily_item["sequence_index"], 1)
        self.assertEqual(daily_item["sequence_total"], 1)

    def test_daily_catalog_only_keeps_last_three_screenshots_from_failure_stage(self) -> None:
        run_root = self.root / "logs" / "daily" / "2026-09-03" / "204745"
        earlier_step = run_root / "04-return-home"
        failure_step = run_root / "05-quick-hunt-entry"
        earlier_step.mkdir(parents=True)
        failure_step.mkdir(parents=True)
        Image.new("RGB", (80, 45)).save(earlier_step / "unrelated.png")
        for index in range(4):
            path = failure_step / f"step-{index + 1}.png"
            Image.new("RGB", (80, 45), color=(index, index, index)).save(path)
            timestamp = 1_700_000_000 + index
            os.utime(path, (timestamp, timestamp))
        (run_root / "summary.json").write_text(
            json.dumps(
                {
                    "result": "failed",
                    "reason": "quick_hunt_entry: quick-hunt entry requires real_home, got unknown",
                    "started_at": "2026-09-03T20:47:45",
                }
            ),
            encoding="utf-8",
        )
        (run_root / "events.jsonl").write_text(
            json.dumps(
                {
                    "stage": "quick_hunt_entry",
                    "status": "error",
                    "log_root": str(failure_step),
                }
            )
            + "\n",
            encoding="utf-8",
        )

        items = [item for item in self.store.catalog()["items"] if item["source"] == "daily"]

        self.assertEqual([item["name"] for item in items], ["step-2.png", "step-3.png", "step-4.png"])
        self.assertEqual([item["sequence_index"] for item in items], [1, 2, 3])
        self.assertTrue(all("unrelated" not in item["name"] for item in items))

    def test_joined_ocr_text_counts_as_a_complete_expected_label(self) -> None:
        self.assertEqual(_partial_similarity("快速狩猎", "通行证快速狩猎"), 1.0)

    def test_failure_reason_is_presented_in_chinese(self) -> None:
        self.assertEqual(
            _failure_reason_label(
                "crystal_cave_cycle: MAX changed to unexpected state blocking_ad_overlay; retry cancelled"
            ),
            "执行圣石洞穴失败：点击最大次数后出现了非预期的广告弹窗，已停止重试。",
        )

    def test_latest_completed_run_is_available_by_stage_without_loading_every_frame(self) -> None:
        run_root = self.root / "logs" / "daily" / "2026-09-04" / "193811"
        stage_root = run_root / "03-free-gacha"
        stage_root.mkdir(parents=True)
        screenshots = []
        for index in range(8):
            prefix = "click" if index in {2, 4, 6} else "wait"
            path = stage_root / f"{prefix}-{index + 1:03d}-real_home.png"
            Image.new("RGB", (80, 45), color=(index, index, index)).save(path)
            timestamp = 1_800_000_000 + index
            os.utime(path, (timestamp, timestamp))
            screenshots.append(path)
        (run_root / "summary.json").write_text(
            json.dumps(
                {
                    "result": "completed",
                    "started_at": "2026-09-04T19:38:11",
                }
            ),
            encoding="utf-8",
        )
        (run_root / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "time": "2026-09-04T19:44:37",
                            "stage": "free_gacha",
                            "status": "start",
                            "log_root": str(stage_root),
                        }
                    ),
                    json.dumps(
                        {
                            "time": "2026-09-04T20:09:21",
                            "stage": "daily",
                            "status": "success",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        latest_items = [
            item for item in self.store.catalog()["items"] if item["source"] == "latest"
        ]

        self.assertEqual(len(latest_items), 5)
        self.assertEqual(latest_items[0]["stage_label"], "免费抽卡")
        self.assertEqual(latest_items[0]["sequence_index"], 1)
        self.assertEqual(latest_items[-1]["sequence_index"], 5)
        self.assertTrue(all(item["sequence_total"] == 5 for item in latest_items))

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
        self.assertEqual(annotation["correct_state_label"], "快速狩猎地图")
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
