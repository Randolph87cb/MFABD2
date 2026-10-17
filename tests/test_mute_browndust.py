from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from mute_browndust import set_mute  # noqa: E402


class MuteBrownDustTests(unittest.TestCase):
    @patch("mute_browndust.AudioUtilities.GetAllSessions")
    def test_stale_session_does_not_hide_later_game_session(
        self,
        get_all_sessions: MagicMock,
    ) -> None:
        stale_session = MagicMock()
        type(stale_session).Process = PropertyMock(
            side_effect=RuntimeError("audio session disappeared")
        )
        game_session = MagicMock()
        game_session.Process.name.return_value = "BrownDust II.exe"
        game_session.Process.pid = 1234
        get_all_sessions.return_value = [stale_session, game_session]

        changed = set_mute(True)

        self.assertEqual(changed, 1)
        game_session.SimpleAudioVolume.SetMute.assert_called_once_with(1, None)

    @patch("mute_browndust.AudioUtilities.GetAllSessions")
    def test_only_exact_game_process_is_muted(
        self,
        get_all_sessions: MagicMock,
    ) -> None:
        unrelated_session = MagicMock()
        unrelated_session.Process.name.return_value = "BrownDust II Helper.exe"
        get_all_sessions.return_value = [unrelated_session]

        changed = set_mute(True)

        self.assertEqual(changed, 0)
        unrelated_session.SimpleAudioVolume.SetMute.assert_not_called()

    @patch("mute_browndust.AudioUtilities.GetAllSessions")
    def test_expired_game_session_does_not_hide_later_game_session(
        self,
        get_all_sessions: MagicMock,
    ) -> None:
        expired_game_session = MagicMock()
        expired_game_session.Process.name.return_value = "BrownDust II.exe"
        type(expired_game_session).SimpleAudioVolume = PropertyMock(
            side_effect=RuntimeError("audio endpoint disappeared")
        )
        game_session = MagicMock()
        game_session.Process.name.return_value = "BrownDust II.exe"
        game_session.Process.pid = 5678
        get_all_sessions.return_value = [expired_game_session, game_session]

        changed = set_mute(False)

        self.assertEqual(changed, 1)
        game_session.SimpleAudioVolume.SetMute.assert_called_once_with(0, None)


if __name__ == "__main__":
    unittest.main()
