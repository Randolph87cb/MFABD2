from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation as daily
from daily_plan import REWARD_RECHECK_PHASE_IDS
from daily_supervisor import completion_report


class DailyRecoveryTests(unittest.TestCase):
    def run_offline(self, root, execute, recovery=(True, "home"), **kwargs):
        with ExitStack() as stack:
            for name, value in (("wait_for_network", True), ("find_game_window", 123),
                                ("ensure_home", recovery)):
                stack.enter_context(patch.object(daily, name, return_value=value))
            stack.enter_context(patch.object(daily, "os"))
            stack.enter_context(patch.object(daily, "DesktopActivityGuard"))
            stack.enter_context(patch("builtins.print"))
            runner = stack.enter_context(patch.object(daily, "_execute_daily_phase", side_effect=execute))
            result = daily.run_daily(project_root=root, force=False, network_timeout=1, **kwargs)
        return result, [call.args[0] for call in runner.call_args_list]

    def test_multiple_failures_leave_evidence_but_run_later_rewards(self):
        def execute(phase, **kwargs):
            if phase in {"arena", "season_activity", "activity_rewards"}:
                raise daily.DailyRunError(phase + " failed")
            return "completed"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, calls = self.run_offline(root, execute)
            state = json.loads((root / "state/daily_automation.json").read_text(encoding="utf-8"))
            summary = json.loads(next(root.glob("logs/daily/*/*/summary.json")).read_text(encoding="utf-8"))
            report = completion_report(root)
        self.assertEqual(report["errors"], summary["failures"])
        self.assertEqual(result, 2)
        self.assertEqual(calls, list(daily.CURRENT_PHASE_IDS))
        self.assertEqual(state["phases"]["arena"]["status"], "failed")
        self.assertEqual(state["phases"]["mail_rewards"]["status"], "completed")
        self.assertEqual(set(summary["failures"]), {"arena", "season_activity", "activity_rewards"})

    def test_cannot_restore_home_does_not_run_next_task_or_erase_original_failure(self):
        def execute(phase, **kwargs):
            if phase == "arena":
                raise daily.DailyRunError("arena original error")
            return "completed"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, calls = self.run_offline(root, execute, recovery=(False, "unknown page"))
            summary = json.loads(next(root.glob("logs/daily/*/*/summary.json")).read_text(encoding="utf-8"))
        self.assertEqual(result, 2)
        self.assertEqual(calls[-1], "arena")
        self.assertNotIn("season_activity", calls)
        self.assertEqual(summary["failures"], {"arena": "arena original error"})
        self.assertEqual(summary["state"]["phases"]["season_activity"]["status"], "pending")

    def test_each_resume_rechecks_rewards_without_replaying_completed_consumption(self):
        fail = [True]
        def execute(phase, **kwargs):
            if phase == "arena" and fail[0]:
                fail[0] = False
                raise daily.DailyRunError("arena failed once")
            return "completed"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, first_calls = self.run_offline(root, execute)
            second, second_calls = self.run_offline(root, execute)
            third, third_calls = self.run_offline(root, execute)
        self.assertEqual((first, second, third), (2, 0, 0))
        self.assertEqual(first_calls, list(daily.CURRENT_PHASE_IDS))
        rewards = [phase for phase in daily.CURRENT_PHASE_IDS if phase in REWARD_RECHECK_PHASE_IDS]
        self.assertEqual(second_calls, ["arena"] + rewards)
        self.assertEqual(third_calls, rewards)

    def test_stage_failure_preserves_technical_reason_for_summary_and_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(daily.DailyRunError, "OCR merged balance with distant speech"):
                daily._require_phase(MagicMock(), "restaurant_decor_recheck",
                                     lambda **kwargs: (False, "OCR merged balance with distant speech"),
                                     log_root=Path(directory))

    def test_login_failure_remains_global_and_forced_phase_remains_isolated(self):
        def execute(phase, **kwargs):
            raise daily.DailyRunError("failed")
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.run_offline(Path(directory), execute)
        self.assertEqual((result, calls), (2, ["start"]))
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.run_offline(Path(directory), lambda *a, **k: "completed", force_phase="arena")
        self.assertEqual((result, calls), (0, ["arena"]))


if __name__ == "__main__":
    unittest.main()
