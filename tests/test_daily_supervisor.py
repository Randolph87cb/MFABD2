from __future__ import annotations

import json
import os
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
    prune_old_logs,
    repair_prompt,
    run_codex_turn,
    CommandResult,
    SupervisorLogger,
)


class DailySupervisorTests(unittest.TestCase):
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

    def test_repair_prompt_forbids_single_phase_rerun_and_reference_writes(self) -> None:
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
        self.assertIn("本轮不要 git commit", prompt)

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
