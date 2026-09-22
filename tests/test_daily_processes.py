from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_processes import (  # noqa: E402
    ProcessIdentity,
    close_game,
    close_new_exact_processes,
)


class DailyProcessesTests(unittest.TestCase):
    @patch("daily_processes.find_game_window", return_value=0)
    def test_close_game_is_safe_when_game_is_not_running(self, _finder: MagicMock) -> None:
        self.assertEqual(close_game(), {"ok": True, "action": "not_running"})

    @patch("daily_processes._stop_verified_process", return_value="terminated")
    @patch("daily_processes.psutil.Process")
    @patch("daily_processes.post_close", return_value=True)
    @patch("daily_processes.process_identity")
    @patch("daily_processes.window_process_id", return_value=123)
    @patch("daily_processes.find_game_window", return_value=456)
    def test_close_game_posts_close_then_forces_only_same_identity(
        self,
        _finder: MagicMock,
        _window_pid: MagicMock,
        identity_lookup: MagicMock,
        _post_close: MagicMock,
        process_factory: MagicMock,
        stop_verified: MagicMock,
    ) -> None:
        identity = ProcessIdentity(123, 10.5, "c:\\game.exe")
        identity_lookup.return_value = identity
        process_factory.return_value.wait.side_effect = psutil.TimeoutExpired(15)

        result = close_game(grace_seconds=15)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "terminated")
        stop_verified.assert_called_once_with(identity, grace_seconds=5.0)

    @patch("daily_processes._stop_verified_process", return_value="terminated")
    @patch("daily_processes.snapshot_exact_executable")
    def test_helper_cleanup_never_touches_preexisting_matching_process(
        self,
        snapshot: MagicMock,
        stop_verified: MagicMock,
    ) -> None:
        existing = ProcessIdentity(11, 1.0, "c:\\starter.exe")
        created = ProcessIdentity(22, 2.0, "c:\\starter.exe")
        snapshot.return_value = {11: existing, 22: created}

        result = close_new_exact_processes("C:\\starter.exe", {11: existing})

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["process"]["pid"], 22)
        stop_verified.assert_called_once_with(created, grace_seconds=5.0)


if __name__ == "__main__":
    unittest.main()
