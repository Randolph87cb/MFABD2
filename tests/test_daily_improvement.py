from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_supervisor import (  # noqa: E402
    CodexTurn,
    CommandResult,
    build_parser,
    improve_after_success,
    supervise,
)


class DailyImprovementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.args = build_parser().parse_args([
            "--project-root", str(self.root), "--codex-path", "codex.cmd",
        ])
        self.logger = MagicMock()
        self.logger.text_path = self.root / "supervisor.log"
        self.clock = self.stack.enter_context(patch("daily_supervisor.datetime"))
        self.clock.now.return_value = datetime(2026, 10, 18, 8, 30, tzinfo=timezone.utc)
        self.git = self.stack.enter_context(patch("daily_supervisor.git_status", return_value=(True, "")))
        self.reference = self.stack.enter_context(patch("daily_supervisor.reference_is_ready", return_value=(True, "")))
        self.snapshot = self.stack.enter_context(patch("daily_supervisor.snapshot_run_helpers", return_value=[]))
        self.cleanup = self.stack.enter_context(patch("daily_supervisor.cleanup_after_attempt", return_value={"ok": True}))
        self.codex = self.stack.enter_context(patch("daily_supervisor.run_codex_turn", return_value=CodexTurn(True, "new-thread", 0, None)))
        self.state_path = self.root / "state" / "daily_improvement.json"

    def improve(self, *, day: str = "2026-10-18", clean: bool = True, complete: bool = True) -> None:
        improve_after_success(
            args=self.args,
            logger=self.logger,
            report={"complete": complete, "game_day": day, "statuses": {"mail_rewards": "completed"}},
            summary_path=None,
            project_was_clean=clean,
        )

    def read_days(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))["days"]

    def test_success_starts_new_thread_once_and_next_game_day_starts_another(self) -> None:
        def complete_turn(**kwargs):
            # The daily claim must reach disk before Codex is launched.
            self.assertEqual(self.read_days()["2026-10-18"]["status"], "running")
            return CodexTurn(True, "new-thread", 0, None)

        self.codex.side_effect = complete_turn
        self.improve()
        call = self.codex.call_args.kwargs
        self.assertNotIn("thread_id", call)  # Never resume a repair thread.
        self.assertEqual(call["label"], "codex-improvement")
        self.assertIn("tools/daily_automation.py", call["prompt"])
        self.assertIn("current_runners", call["prompt"])
        first = self.read_days()["2026-10-18"]
        self.assertEqual(first["thread_id"], "new-thread")
        self.assertEqual(first["status"], "turn_completed")
        self.assertEqual(first["candidates"][0], "quick_hunt")
        self.assertNotIn("equipment_daily", first["candidates"])
        self.assertNotIn("arbitrage", first["candidates"])
        self.improve()  # A fresh invocation reads the claim from disk.
        self.assertEqual(self.codex.call_count, 1)
        self.clock.now.return_value = datetime(2026, 10, 19, 8, 30, tzinfo=timezone.utc)
        self.codex.side_effect = None
        self.improve(day="2026-10-19")
        self.assertEqual(self.codex.call_count, 2)
        self.assertEqual(len(self.read_days()), 2)
        self.assertEqual(self.cleanup.call_count, 2)

    def test_before_reset_uses_previous_game_day(self) -> None:
        self.clock.now.return_value = datetime(2026, 10, 19, 7, 59, tzinfo=timezone.utc)
        self.improve(day="2026-10-18")
        self.codex.assert_called_once()

    def test_failed_or_interrupted_turn_is_not_restarted_same_day(self) -> None:
        for status in ("running", "failed", "turn_completed"):
            with self.subTest(status=status):
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(json.dumps({"days": {"2026-10-18": {"status": status}}}), encoding="utf-8")
                self.improve()
                self.codex.assert_not_called()

    def test_blockers_do_not_claim_day_or_launch_codex(self) -> None:
        for blocker in ("initial_dirty", "currently_dirty", "reference", "stale", "incomplete", "manual_phase", "bad_state"):
            with self.subTest(blocker=blocker):
                self.git.return_value = (blocker != "currently_dirty", " M file.py" if blocker == "currently_dirty" else "")
                self.reference.return_value = (blocker != "reference", "missing reference")
                self.args.force_phase = "arena" if blocker == "manual_phase" else None
                if blocker == "bad_state":
                    self.state_path.parent.mkdir(parents=True, exist_ok=True)
                    self.state_path.write_text('{"days": []}', encoding="utf-8")
                self.improve(
                    day="2026-10-17" if blocker == "stale" else "2026-10-18",
                    clean=blocker != "initial_dirty",
                    complete=blocker != "incomplete",
                )
                self.codex.assert_not_called()
                self.assertEqual(self.state_path.exists(), blocker == "bad_state")

    def test_codex_error_and_demo_cleanup_failure_are_recorded(self) -> None:
        for failure in ("turn", "launch", "cleanup"):
            with self.subTest(failure=failure):
                if self.state_path.exists():
                    self.state_path.unlink()
                self.codex.side_effect = OSError("cannot launch") if failure == "launch" else None
                self.codex.return_value = CodexTurn(failure != "turn", "new-thread", 1 if failure == "turn" else 0, None)
                self.cleanup.return_value = {"ok": failure != "cleanup"}
                self.improve()
                self.assertEqual(self.read_days()["2026-10-18"]["status"], "failed")
                calls = self.codex.call_count
                self.improve()
                self.assertEqual(self.codex.call_count, calls)

    def test_uncommitted_changes_or_modified_reference_are_not_reported_as_completed(self) -> None:
        for target in ("project", "reference"):
            with self.subTest(target=target):
                if self.state_path.exists():
                    self.state_path.unlink()
                self.git.side_effect = [(True, ""), (False, " M module.py")] if target == "project" else None
                self.reference.side_effect = [(True, ""), (False, "reference changed")] if target == "reference" else None
                self.improve()
                self.assertEqual(self.read_days()["2026-10-18"]["status"], "failed")

    def test_supervisor_only_improves_after_daily_and_cleanup_success(self) -> None:
        for exit_code, complete, cleanup in ((0, True, True), (1, True, True), (0, False, True), (0, True, False)):
            with (
                self.subTest(exit_code=exit_code, complete=complete, cleanup=cleanup),
                patch("daily_supervisor.prune_old_logs", return_value=[]),
                patch("daily_supervisor.run_automation_attempt", return_value=(CommandResult(exit_code, [], []), {"ok": cleanup})) as daily,
                patch("daily_supervisor.completion_report", return_value={"complete": complete, "game_day": "2026-10-18"}),
                patch("daily_supervisor.latest_summary", return_value=None),
                patch("daily_supervisor.notify_success") as notify,
                patch("daily_supervisor.improve_after_success") as improve,
                patch("builtins.print"),
            ):
                self.args.max_repair_rounds = 0
                order = MagicMock()
                order.attach_mock(notify, "notify")
                order.attach_mock(improve, "improve")
                success = exit_code == 0 and complete and cleanup
                self.assertEqual(supervise(self.args), 0 if success else 2)
                daily.assert_called_once()  # No same-day rerun after development.
                self.assertEqual(improve.call_count, int(success))
                if success:
                    self.assertEqual([call[0] for call in order.mock_calls], ["notify", "improve"])

    def test_repair_then_improvement_uses_separate_thread_and_no_extra_daily_run(self) -> None:
        with (
            patch("daily_supervisor.prune_old_logs", return_value=[]),
            patch("daily_supervisor.run_automation_attempt", side_effect=[
                (CommandResult(1, [], []), {"ok": True}),
                (CommandResult(0, [], []), {"ok": True}),
            ]) as daily,
            patch("daily_supervisor.completion_report", side_effect=[
                {"complete": False, "game_day": "2026-10-18"},
                {"complete": True, "game_day": "2026-10-18"},
            ]),
            patch("daily_supervisor.latest_summary", return_value=None),
            patch("daily_supervisor.notify_success"),
            patch("builtins.print"),
        ):
            self.codex.side_effect = [
                CodexTurn(True, "repair-thread", 0, None),
                CodexTurn(True, "repair-thread", 0, None),
                CodexTurn(False, "improvement-thread", 1, "development failed"),
            ]
            self.assertEqual(supervise(self.args), 0)  # Development failure preserves daily success.
        self.assertEqual(daily.call_count, 2)
        repair, finalize, improvement = [call.kwargs for call in self.codex.call_args_list]
        self.assertIsNone(repair["thread_id"])
        self.assertEqual(finalize["thread_id"], "repair-thread")
        self.assertNotIn("thread_id", improvement)
        self.assertEqual(self.read_days()["2026-10-18"]["thread_id"], "improvement-thread")


if __name__ == "__main__":
    unittest.main()
