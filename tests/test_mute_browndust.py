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
    @patch("mute_browndust.AudioUtilities.GetAudioSessionManager")
    def test_missing_default_audio_endpoint_needs_no_mute(
        self,
        get_audio_session_manager: MagicMock,
    ) -> None:
        error = RuntimeError("default endpoint not found")
        error.hresult = -2147023728  # type: ignore[attr-defined]
        get_audio_session_manager.side_effect = error

        changed = set_mute(True)

        self.assertIsNone(changed)

    @patch("mute_browndust._get_audio_sessions")
    def test_stale_session_does_not_hide_later_game_session(
        self,
        get_audio_sessions: MagicMock,
    ) -> None:
        stale_session = MagicMock()
        type(stale_session).Process = PropertyMock(
            side_effect=RuntimeError("audio session disappeared")
        )
        game_session = MagicMock()
        game_session.Process.name.return_value = "BrownDust II.exe"
        game_session.Process.pid = 1234
        get_audio_sessions.return_value = [stale_session, game_session]

        changed = set_mute(True)

        self.assertEqual(changed, 1)
        game_session.SimpleAudioVolume.SetMute.assert_called_once_with(1, None)

    @patch("mute_browndust._get_audio_sessions")
    def test_only_exact_game_process_is_muted(
        self,
        get_audio_sessions: MagicMock,
    ) -> None:
        unrelated_session = MagicMock()
        unrelated_session.Process.name.return_value = "BrownDust II Helper.exe"
        get_audio_sessions.return_value = [unrelated_session]

        changed = set_mute(True)

        self.assertEqual(changed, 0)
        unrelated_session.SimpleAudioVolume.SetMute.assert_not_called()

    @patch("mute_browndust._get_audio_sessions")
    def test_expired_game_session_does_not_hide_later_game_session(
        self,
        get_audio_sessions: MagicMock,
    ) -> None:
        expired_game_session = MagicMock()
        expired_game_session.Process.name.return_value = "BrownDust II.exe"
        type(expired_game_session).SimpleAudioVolume = PropertyMock(
            side_effect=RuntimeError("audio endpoint disappeared")
        )
        game_session = MagicMock()
        game_session.Process.name.return_value = "BrownDust II.exe"
        game_session.Process.pid = 5678
        get_audio_sessions.return_value = [expired_game_session, game_session]

        changed = set_mute(False)

        self.assertEqual(changed, 1)
        game_session.SimpleAudioVolume.SetMute.assert_called_once_with(0, None)

    @patch("mute_browndust.AudioSession")
    @patch("mute_browndust.AudioUtilities.GetAudioSessionManager")
    def test_failed_enumerator_item_does_not_hide_later_game_session(
        self,
        get_audio_session_manager: MagicMock,
        audio_session_type: MagicMock,
    ) -> None:
        enumerator = MagicMock()
        enumerator.GetCount.return_value = 2
        valid_control = MagicMock()
        enumerator.GetSession.side_effect = [
            RuntimeError("session vanished during enumeration"),
            valid_control,
        ]
        get_audio_session_manager.return_value.GetSessionEnumerator.return_value = (
            enumerator
        )
        game_session = MagicMock()
        game_session.Process.name.return_value = "BrownDust II.exe"
        game_session.Process.pid = 9012
        audio_session_type.return_value = game_session

        changed = set_mute(True)

        self.assertEqual(changed, 1)
        game_session.SimpleAudioVolume.SetMute.assert_called_once_with(1, None)


if __name__ == "__main__":
    unittest.main()
