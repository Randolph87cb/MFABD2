from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_automation import claim_daily_run, update_daily_state


class DailyAutomationStateTests(unittest.TestCase):
    def test_second_start_on_same_day_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            run_root = root / "logs" / "first"

            first, _state = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=run_root,
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            second, previous = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "second",
                force=False,
                started_at="2026-07-31T09:00:00",
            )

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(previous["run_root"], str(run_root))

    def test_force_allows_same_day_manual_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "first",
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            claimed, current = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "forced",
                force=True,
                started_at="2026-07-31T09:00:00",
            )

            self.assertTrue(claimed)
            self.assertEqual(current["run_root"], str(root / "logs" / "forced"))

    def test_failure_status_keeps_daily_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "run",
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            update_daily_state(state_path, status="failed", error="login required")
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(state["last_started_date"], "2026-07-31")
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["error"], "login required")


if __name__ == "__main__":
    unittest.main()
