from __future__ import annotations

import json
import os
import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_state import new_daily_state, update_phase  # noqa: E402
from daily_supervisor import (  # noqa: E402
    CURRENT_PHASE_IDS,
    _parse_codex_events,
    completion_report,
    build_parser,
    finalize_prompt,
    followup_prompt,
    prune_old_logs,
    repair_prompt,
    run_codex_turn,
    notify_success,
    supervise,
    CodexTurn,
    CommandResult,
    SupervisorLogger,
)


class DailySupervisorTests(unittest.TestCase):
    def test_success_notification_uses_independent_topic_and_chinese_title(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_supervisor.urlopen") as send,
        ):
            root = Path(temporary) / "棕尘2小助手"
            config = root / "state" / "ntfy.config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"ntfyUrl": "https://ntfy.example", "ntfyTopic": "daily-only"}), encoding="utf-8-sig")
            send.return_value.__enter__.return_value = io.BytesIO(b'{"event":"message","id":"test-1"}')
            logger = MagicMock()
            self.assertTrue(notify_success(root, report={
                "game_day": "2026-10-12",
                "statuses": {"start": "completed", "arena": "skipped"},
            }, logger=logger))

        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://ntfy.example")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["topic"], "daily-only")
        self.assertEqual(payload["title"], "棕尘2每日任务")
        self.assertIn("游戏日：2026-10-12", payload["message"])
        self.assertIn("完成 1 项，跳过 1 项", payload["message"])
        self.assertEqual(logger.event.call_args.args[0], "notification_sent")

    def test_notification_errors_are_logged_without_raising(self) -> None:
        for failure in ("missing", "invalid", "empty_topic", "rejected", "timeout"):
            with (
                self.subTest(failure=failure),
                patch("daily_supervisor.Path.read_text") as read,
                patch("daily_supervisor.urlopen") as send,
            ):
                read.return_value = json.dumps({"ntfyUrl": "https://ntfy.example", "ntfyTopic": "daily-only"})
                send.return_value.__enter__.return_value = io.BytesIO(b'{"event":"error"}')
                if failure == "missing":
                    read.side_effect = FileNotFoundError("config missing")
                elif failure == "invalid":
                    read.return_value = "invalid json"
                elif failure == "empty_topic":
                    read.return_value = '{"ntfyUrl":"https://ntfy.example"}'
                if failure == "timeout":
                    send.side_effect = TimeoutError("delivery timed out")
                logger = MagicMock()
                self.assertFalse(notify_success(Path.cwd(), report={}, logger=logger))
                self.assertEqual(logger.event.call_args.args[0], "notification_failed")
                if failure in ("missing", "invalid", "empty_topic"):
                    send.assert_not_called()

    def test_supervisor_notifies_only_after_complete_successful_cleanup(self) -> None:
        for exit_code, complete, cleanup_ok in ((0, True, True), (1, True, True), (0, False, True), (0, True, False)):
            with (
                self.subTest(exit_code=exit_code, complete=complete, cleanup_ok=cleanup_ok),
                tempfile.TemporaryDirectory() as temporary,
                patch("daily_supervisor.prune_old_logs", return_value=[]),
                patch("daily_supervisor.git_status", return_value=(True, "")),
                patch("daily_supervisor.run_automation_attempt", return_value=(CommandResult(exit_code, [], []), {"ok": cleanup_ok})),
                patch("daily_supervisor.completion_report", return_value={"complete": complete}),
                patch("daily_supervisor.latest_summary", return_value=None),
                patch("daily_supervisor.notify_success", return_value=False) as notify,
                patch("builtins.print"),
            ):
                args = build_parser().parse_args(["--project-root", temporary, "--codex-path", "codex.cmd", "--max-repair-rounds", "0"])
                result = supervise(args)
                if exit_code == 0 and complete and cleanup_ok:
                    notify.assert_called_once()
                    self.assertEqual(result, 0)  # Notification failure must not replay game tasks.
                else:
                    notify.assert_not_called()
                    self.assertEqual(result, 2)

    def test_repaired_run_notifies_once_after_successful_final_review(self) -> None:
        for final_ok in (True, False):
            with (
                self.subTest(final_ok=final_ok),
                tempfile.TemporaryDirectory() as temporary,
                patch("daily_supervisor.prune_old_logs", return_value=[]),
                patch("daily_supervisor.git_status", return_value=(True, "")),
                patch("daily_supervisor.reference_is_ready", return_value=(True, "")),
                patch("daily_supervisor.run_automation_attempt", side_effect=[
                    (CommandResult(1, [], []), {"ok": True}),
                    (CommandResult(0, [], []), {"ok": True}),
                ]),
                patch("daily_supervisor.completion_report", side_effect=[{"complete": False}, {"complete": True}]),
                patch("daily_supervisor.latest_summary", return_value=None),
                patch("daily_supervisor.run_codex_turn", side_effect=[
                    CodexTurn(True, "thread-1", 0, None),
                    CodexTurn(final_ok, "thread-1", 0 if final_ok else 1, None),
                ]),
                patch("daily_supervisor.notify_success") as notify,
                patch("builtins.print"),
            ):
                args = build_parser().parse_args(["--project-root", temporary, "--codex-path", "codex.cmd"])
                self.assertEqual(supervise(args), 0 if final_ok else 2)
                self.assertEqual(notify.call_count, 1 if final_ok else 0)

    def test_completion_requires_every_current_phase_to_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = new_daily_state("2026-09-23", CURRENT_PHASE_IDS)
            for phase_id in CURRENT_PHASE_IDS[:-1]:
                state = update_phase(state, phase_id, "completed")
            state_path = root / "state" / "daily_automation.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps(state), encoding="utf-8")

            report = completion_report(root)

            self.assertFalse(report["complete"])
            self.assertEqual(report["failed_phase"], CURRENT_PHASE_IDS[-1])
            self.assertEqual(report["incomplete"], [CURRENT_PHASE_IDS[-1]])

    def test_completion_accepts_completed_and_skipped_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = new_daily_state("2026-09-23", CURRENT_PHASE_IDS)
            for index, phase_id in enumerate(CURRENT_PHASE_IDS):
                state = update_phase(
                    state,
                    phase_id,
                    "completed" if index % 2 == 0 else "skipped",
                )
            state_path = root / "state" / "daily_automation.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps(state), encoding="utf-8")

            self.assertTrue(completion_report(root)["complete"])

    def test_prune_old_logs_only_removes_allowlisted_dated_directories(self) -> None:
        now = datetime(2026, 9, 23, 8, 30, tzinfo=timezone.utc)
        old_timestamp = (now - timedelta(days=8)).timestamp()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_daily = root / "logs" / "daily" / "2026-09-15"
            recent_daily = root / "logs" / "daily" / "2026-09-22"
            unrelated = root / "logs" / "screenshots" / "2026-09-15"
            invalid = root / "logs" / "daily" / "manual-notes"
            for directory in (old_daily, recent_daily, unrelated, invalid):
                directory.mkdir(parents=True)
                file_path = directory / "item.log"
                file_path.write_text("x", encoding="utf-8")
            os.utime(old_daily / "item.log", (old_timestamp, old_timestamp))
            os.utime(old_daily, (old_timestamp, old_timestamp))

            removed = prune_old_logs(root, retention_days=7, now=now)

            self.assertEqual(removed, [old_daily.resolve()])
            self.assertFalse(old_daily.exists())
            self.assertTrue(recent_daily.exists())
            self.assertTrue(unrelated.exists())
            self.assertTrue(invalid.exists())

    def test_codex_event_parser_uses_structured_completion_not_stderr(self) -> None:
        thread_id, completed, error = _parse_codex_events(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10}}),
            ]
        )

        self.assertEqual(thread_id, "thread-1")
        self.assertTrue(completed)
        self.assertIsNone(error)

    def test_repair_prompts_allow_committing_verified_repairs(self) -> None:
        prompt = repair_prompt(
            project_root=Path("C:/project"),
            reference_root=Path("C:/project/.external/MFABD2-reference"),
            report={"state_path": "state.json", "statuses": {}, "failed_phase": "arena"},
            summary_path=None,
            supervisor_log=Path("C:/project/logs/supervisor.log"),
            round_number=1,
        )

        self.assertIn("禁止修改参考仓库", prompt)
        self.assertIn("不要把自动恢复改成 --force-phase", prompt)
        self.assertIn("完成离线验证后", prompt)
        self.assertIn("提交", prompt)
        self.assertIn("推送当前 main 分支", prompt)
        self.assertNotIn("不要 git commit", prompt)

        followup = followup_prompt(
            report={"statuses": {}, "failed_phase": "arena"},
            summary_path=None,
            supervisor_log=Path("C:/project/logs/supervisor.log"),
            round_number=2,
        )
        self.assertIn("完成离线验证后", followup)
        self.assertIn("提交并推送本轮相关改动", followup)
        self.assertNotIn("不要 commit/push", followup)

        final = finalize_prompt(
            supervisor_log=Path("C:/project/logs/supervisor.log"),
            summary_path=None,
        )
        self.assertIn("若本次修复仍有未提交", final)

    @patch("daily_supervisor.run_command")
    def test_codex_turn_uses_automatic_workspace_review_without_conflicting_flag(
        self,
        run_command: MagicMock,
    ) -> None:
        run_command.return_value = CommandResult(
            returncode=0,
            stdout=[
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps({"type": "turn.completed"}),
            ],
            stderr=[],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = run_codex_turn(
                codex_path=Path("C:/codex.cmd"),
                project_root=root,
                logger=SupervisorLogger(root / "logs"),
                prompt="repair",
            )

        command = run_command.call_args.args[0]
        self.assertTrue(result.ok)
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("--sandbox", command)


if __name__ == "__main__":
    unittest.main()
