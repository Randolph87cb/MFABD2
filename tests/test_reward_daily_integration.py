from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_automation import DailyRunError, _require_phase


class RewardDailyIntegrationTests(unittest.TestCase):
    def test_optional_reward_without_notification_is_logged_as_skipped(self) -> None:
        master = MagicMock()
        operation = MagicMock(return_value=(True, "skipped: no red exclamation"))
        with tempfile.TemporaryDirectory() as temporary:
            reason = _require_phase(
                master,
                "task_rewards",
                operation,
                log_root=Path(temporary),
            )

        self.assertEqual(reason, "skipped: no red exclamation")
        self.assertEqual(master.event.call_args_list[-1].args[1], "skipped")

    def test_completed_reward_is_logged_as_success(self) -> None:
        master = MagicMock()
        operation = MagicMock(return_value=(True, "completed: claimed rewards"))
        with tempfile.TemporaryDirectory() as temporary:
            _require_phase(
                master,
                "mail_rewards",
                operation,
                log_root=Path(temporary),
            )

        self.assertEqual(master.event.call_args_list[-1].args[1], "success")

    def test_failed_reward_stops_the_daily_flow_at_that_phase(self) -> None:
        master = MagicMock()
        operation = MagicMock(return_value=(False, "领取按钮点击后仍存在，点击未生效"))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(DailyRunError, "活动奖励执行失败"):
                _require_phase(
                    master,
                    "activity_rewards",
                    operation,
                    log_root=Path(temporary),
                )

        self.assertEqual(master.event.call_args_list[-1].args[1], "error")

    def test_unhandled_reward_exception_is_saved_and_reported_in_chinese(self) -> None:
        master = MagicMock()
        operation = MagicMock(side_effect=RuntimeError("OCR engine unavailable"))
        with tempfile.TemporaryDirectory() as temporary:
            log_root = Path(temporary) / "activity"
            with self.assertRaisesRegex(DailyRunError, "活动奖励执行异常"):
                _require_phase(
                    master,
                    "activity_rewards",
                    operation,
                    log_root=log_root,
                )

            failure = (log_root / "failure.txt").read_text(encoding="utf-8")

        self.assertIn("未处理异常", failure)
        self.assertIn("OCR engine unavailable", failure)
        self.assertEqual(master.event.call_args_list[-1].args[1], "error")


if __name__ == "__main__":
    unittest.main()
