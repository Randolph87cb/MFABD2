from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from free_gacha import RunLogger
from reward_flow import click_ratio_logged, return_to_home, wait_for_recognition
from win32_windowpos_click import MK_LBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, swipe_client


class RewardFlowTests(unittest.TestCase):
    @patch("reward_flow.click_client")
    def test_logged_click_scales_normalized_coordinates(self, click_client: MagicMock) -> None:
        image = Image.new("RGB", (1000, 600))
        with tempfile.TemporaryDirectory() as temporary:
            logger = RunLogger(Path(temporary), annotate_clicks=True)
            click_ratio_logged(123, image, (0.25, 0.75), key="reward", logger=logger)
        click_client.assert_called_once_with(123, 250, 450)

    @patch("reward_flow.time.sleep")
    @patch("reward_flow.safe_capture_client", return_value=Image.new("RGB", (1000, 600)))
    def test_wait_is_limited_to_the_current_recognition_step(
        self,
        _capture: MagicMock,
        sleep: MagicMock,
    ) -> None:
        clock = iter((0.0, 0.0, 1.0, 2.0, 3.0))
        with patch("reward_flow.time.monotonic", side_effect=lambda: next(clock)):
            found, _image, _details = wait_for_recognition(
                123,
                logger=MagicMock(),
                label="one-step",
                recognize=lambda _image: (False, {}),
                timeout=2.0,
            )
        self.assertFalse(found)
        self.assertGreaterEqual(sleep.call_count, 1)

    @patch("reward_flow.wait_for_recognition")
    @patch("reward_flow.click_ratio_logged")
    @patch("reward_flow.recognize_home_labels", return_value=(False, {}))
    @patch("reward_flow.safe_capture_client", return_value=Image.new("RGB", (1000, 600)))
    def test_return_home_does_not_click_from_an_unknown_page(
        self,
        _capture: MagicMock,
        _recognize_home: MagicMock,
        click: MagicMock,
        wait: MagicMock,
    ) -> None:
        source = MagicMock(return_value=(False, {"available": True}))

        ok, reason = return_to_home(
            123,
            logger=MagicMock(),
            recognize_source=source,
            source_name="活动页面",
        )

        self.assertFalse(ok)
        self.assertIn("避免误点", reason)
        click.assert_not_called()
        wait.assert_not_called()

    @patch("reward_flow.wait_for_recognition")
    @patch("reward_flow.click_ratio_logged")
    @patch("reward_flow.recognize_home_labels", return_value=(True, {"available": True}))
    @patch("reward_flow.safe_capture_client", return_value=Image.new("RGB", (1000, 600)))
    def test_return_home_prioritizes_source_when_home_text_is_also_visible(
        self,
        _capture: MagicMock,
        recognize_home: MagicMock,
        click: MagicMock,
        wait: MagicMock,
    ) -> None:
        wait.return_value = (True, Image.new("RGB", (1000, 600)), {})
        source = MagicMock(return_value=(True, {"available": True}))

        ok, reason = return_to_home(
            123,
            logger=MagicMock(),
            recognize_source=source,
            source_name="通行证页面",
        )

        self.assertTrue(ok)
        self.assertIn("已从通行证页面返回主页", reason)
        source.assert_called_once()
        recognize_home.assert_not_called()
        click.assert_called_once()
        wait.assert_called_once()

    @patch("reward_flow.wait_for_recognition")
    @patch("reward_flow.click_ratio_logged")
    @patch("reward_flow.recognize_home_labels", return_value=(True, {"available": True}))
    @patch("reward_flow.safe_capture_client", return_value=Image.new("RGB", (1000, 600)))
    def test_return_home_accepts_home_only_after_source_is_absent(
        self,
        _capture: MagicMock,
        recognize_home: MagicMock,
        click: MagicMock,
        wait: MagicMock,
    ) -> None:
        source = MagicMock(return_value=(False, {"available": True}))

        ok, reason = return_to_home(
            123,
            logger=MagicMock(),
            recognize_source=source,
            source_name="活动页面",
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "already on home page")
        source.assert_called_once()
        recognize_home.assert_called_once()
        click.assert_not_called()
        wait.assert_not_called()

    @patch("reward_flow.wait_for_recognition")
    @patch("reward_flow.click_ratio_logged")
    @patch("reward_flow.recognize_home_labels", return_value=(False, {}))
    @patch("reward_flow.safe_capture_client", return_value=Image.new("RGB", (1000, 600)))
    def test_return_home_clicks_once_from_a_verified_source_page(
        self,
        _capture: MagicMock,
        _recognize_home: MagicMock,
        click: MagicMock,
        wait: MagicMock,
    ) -> None:
        wait.return_value = (False, Image.new("RGB", (1000, 600)), {})

        ok, reason = return_to_home(
            123,
            logger=MagicMock(),
            recognize_source=MagicMock(return_value=(True, {"available": True})),
            source_name="活动页面",
        )

        self.assertFalse(ok)
        self.assertIn("12秒内", reason)
        click.assert_called_once()


class SwipeClientTests(unittest.TestCase):
    @patch("win32_windowpos_click.time.sleep")
    @patch("win32_windowpos_click.get_client_size", return_value=(1000, 600))
    @patch("win32_windowpos_click.user32")
    def test_swipe_posts_drag_path_inside_client(
        self,
        user32: MagicMock,
        _get_size: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        user32.GetWindowRect.return_value = 1
        user32.ClientToScreen.return_value = 1
        user32.GetCursorPos.return_value = 1
        user32.SetWindowPos.return_value = 1
        user32.PostMessageW.return_value = 1

        swipe_client(123, 200, 500, 200, 100, steps=2)

        message_kinds = [entry.args[1] for entry in user32.PostMessageW.call_args_list]
        self.assertIn(WM_LBUTTONDOWN, message_kinds)
        self.assertEqual(message_kinds.count(WM_MOUSEMOVE), 3)
        self.assertEqual(message_kinds[-1], WM_LBUTTONUP)
        move_calls = [
            entry for entry in user32.PostMessageW.call_args_list
            if entry.args[1] == WM_MOUSEMOVE and entry.args[2] == MK_LBUTTON
        ]
        self.assertEqual(len(move_calls), 2)


if __name__ == "__main__":
    unittest.main()
