from __future__ import annotations

import errno
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import open_game


# Recorded starter message: 安装磁盘空间不足。请清理空间后重试。[C:] 1149MB
DISK_SPACE_LOG = (
    "fefc92f5fb8a929bf69ce6befcf592a6eeec5a5d8ea29dbf8ea3fa90"
    "f3abeda49f91908eefed4f4f140c6d0a69786b6e7703074845"
)


class StarterDiskSpaceTests(unittest.TestCase):
    def test_recorded_disk_space_failure_is_reported_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            starter = root / "starter.exe"
            starter.touch()
            log = root / "starter-current.log"
            process = MagicMock()
            process.poll.return_value = None

            def launch(*_args: object, **_kwargs: object) -> MagicMock:
                # The failure can already be written before Popen returns.
                log.write_text(DISK_SPACE_LOG, encoding="ascii")
                return process

            with (
                patch("open_game.STARTER", starter),
                patch("open_game.STARTER_LOG_DIR", root),
                patch("open_game.find_game_window", return_value=0),
                patch("open_game.subprocess.Popen", side_effect=launch) as popen,
                patch("open_game.time.sleep") as sleep,
            ):
                with self.assertRaises(OSError) as caught:
                    open_game.open_game(timeout=120.0)

            self.assertEqual(caught.exception.errno, errno.ENOSPC)
            self.assertIn("insufficient disk space", str(caught.exception))
            self.assertIn(str(log), str(caught.exception))
            popen.assert_called_once()
            sleep.assert_not_called()
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=5.0)
            process.kill.assert_not_called()

    def test_old_disk_space_failure_does_not_poison_a_new_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            starter = root / "starter.exe"
            starter.touch()
            (root / "starter-old.log").write_text(DISK_SPACE_LOG, encoding="ascii")
            process = MagicMock()
            with (
                patch("open_game.STARTER", starter),
                patch("open_game.STARTER_LOG_DIR", root),
                patch("open_game.find_game_window", side_effect=[0, 0, 123]),
                patch("open_game.subprocess.Popen", return_value=process),
                patch("open_game.time.sleep"),
            ):
                self.assertEqual(open_game.open_game(), 123)

            process.terminate.assert_not_called()

    def test_partial_and_unrelated_logs_are_not_failures(self) -> None:
        for content in (
            DISK_SPACE_LOG[:13],  # Odd-length hex while a write is in progress.
            "1d3a24373b6f073a28273d093a2f37312a3d5a5d",  # Start PmangStarter
            "not hex",
        ):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                log = root / "starter-current.log"
                log.write_text(content, encoding="ascii")
                with patch("open_game.STARTER_LOG_DIR", root):
                    current = open_game._starter_log_activity()
                    self.assertIsNone(open_game._starter_disk_space_failure((), current))


if __name__ == "__main__":
    unittest.main()
