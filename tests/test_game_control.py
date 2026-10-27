from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import game_control as control


class GameControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        image = self.root / "client.png"
        Image.new("RGB", (1152, 648), "white").save(image)
        self.window = {"hwnd": 123, "process": {"pid": 99, "create_time": 100.0,
                       "executable": "c:/game/browndust ii.exe"}, "client_size": [1152, 648]}
        self.observation = {"window": self.window, "captured_at": time.time(),
                            "image": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
        self.path = self.root / "observation.json"
        self.write_observation()

    def write_observation(self) -> None:
        self.path.write_text(json.dumps(self.observation), encoding="utf-8")

    def test_rejects_old_restarted_resized_and_modified_observations_before_input(self) -> None:
        for failure in ("old", "restarted", "resized", "image", "used"):
            with self.subTest(failure=failure):
                observed = json.loads(json.dumps(self.observation))
                if failure == "old":
                    observed["captured_at"] -= 301
                elif failure == "restarted":
                    observed["window"]["process"]["create_time"] += 1
                elif failure == "resized":
                    observed["window"]["client_size"][0] += 1
                elif failure == "image":
                    observed["sha256"] = "modified"
                elif failure == "used":
                    self.path.with_suffix(".used").touch()
                self.path.write_text(json.dumps(observed), encoding="utf-8")
                args = control.build_parser().parse_args(["click", "--observation", str(self.path), "--x", "10", "--y", "10"])
                with patch.object(control, "require_window", return_value=self.window), patch.object(control, "click_client") as click:
                    with self.assertRaises(ValueError):
                        control.execute(args, self.root)
                    click.assert_not_called()

    def test_input_consumes_observation_and_returns_a_new_capture(self) -> None:
        args = control.build_parser().parse_args(["click", "--observation", str(self.path), "--x", "10", "--y", "10"])
        with (patch.object(control, "require_window", return_value=self.window),
              patch.object(control, "click_client") as click,
              patch.object(control, "capture", return_value={"observation": "new-observation"}),
              patch.object(control.time, "sleep")):
            result = control.execute(args, self.root)
            click.assert_called_once_with(123, 10, 10)
            self.assertEqual(result["after"]["observation"], "new-observation")
            self.assertEqual(json.loads((self.root / "input.json").read_text())["status"], "sent")
            with self.assertRaisesRegex(ValueError, "already used"):
                control.execute(args, self.root)
            self.assertEqual(click.call_count, 1)

    def test_out_of_bounds_does_not_consume_observation_or_send_input(self) -> None:
        args = control.build_parser().parse_args(["click", "--observation", str(self.path), "--x", "1152", "--y", "10"])
        with patch.object(control, "require_window", return_value=self.window), patch.object(control, "click_client") as click:
            with self.assertRaisesRegex(ValueError, "outside"):
                control.execute(args, self.root)
            click.assert_not_called()
            self.assertFalse(self.path.with_suffix(".used").exists())

    def test_status_does_not_launch_game(self) -> None:
        args = control.build_parser().parse_args(["status"])
        with patch.object(control, "game_window", return_value=None), patch.object(control, "start_game") as launch:
            result = control.execute(args, self.root)
            self.assertFalse(result["desktop_app_required"])
            self.assertIsNone(result["window"])
            launch.assert_not_called()

    def test_open_timeout_cleans_only_new_launcher_instances(self) -> None:
        import subprocess
        before = {11: "existing-launcher"}
        with (patch.object(control, "game_window", return_value=None),
              patch.object(control, "snapshot_run_helpers", return_value=before),
              patch.object(control.subprocess, "run", side_effect=subprocess.TimeoutExpired("open", 1)),
              patch.object(control, "close_new_exact_processes", return_value=[]) as cleanup):
            with self.assertRaisesRegex(RuntimeError, "within 1s"):
                control.start_game(1)
            cleanup.assert_called_once_with(control.STARTER, before)

    def test_open_reuses_existing_window_without_starting_another_process(self) -> None:
        with patch.object(control, "game_window", return_value=self.window), patch.object(control.subprocess, "run") as run:
            self.assertEqual(control.start_game(1), self.window)
            run.assert_not_called()

    def test_multiple_matching_windows_are_rejected(self) -> None:
        def enumerate_windows(callback, data):
            callback(11, data)
            callback(22, data)
            return True

        with (patch.object(control.user32, "EnumWindows", side_effect=enumerate_windows),
              patch.object(control.user32, "IsWindowVisible", return_value=True),
              patch.object(control, "_get_class_name", return_value="UnityWndClass"),
              patch.object(control, "_get_window_text", return_value="BrownDust II"),
              patch.object(control, "window_process_id", return_value=99),
              patch.object(control, "process_identity") as identity,
              patch.object(control, "get_client_size", return_value=(1152, 648))):
            from daily_processes import ProcessIdentity
            identity.return_value = ProcessIdentity(99, 100, "c:/game/browndust ii.exe")
            with self.assertRaisesRegex(RuntimeError, "Multiple"):
                control.game_window()


if __name__ == "__main__":
    unittest.main()
