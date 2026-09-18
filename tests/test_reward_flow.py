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
from reward_flow import (
    click_ratio_logged,
    dismiss_reward_overlays,
    return_to_home,
    wait_for_home_notification_clear,
    wait_for_recognition,
)
from win32_windowpos_click import (
    MK_LBUTTON,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
    click_client,
    swipe_client,
    swipe_client_foreground,
)


class RewardFlowTests(unittest.TestCase):
    @patch("reward_flow.wait_for_recognition")
    @patch("reward_flow.click_ratio_logged")
    @patch("reward_flow.recognize_reward_overlay_labels")
    @patch("reward_flow.safe_capture_client", return_value=Image.new("RGB", (1000, 600)))
    def test_reward_overlay_dismiss_retries_one_dropped_click(
        self,
        _capture: MagicMock,
        recognize: MagicMock,
        click: MagicMock,
        wait: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1000, 600))
        recognize.side_effect = [(True, {}), (False, {})]
        wait.side_effect = [
            (False, image, {"header": ["REWARD"]}),
            (True, image, {}),
        ]

        ok, _image, reason = dismiss_reward_overlays(
            123,
            logger=MagicMock(),
            max_overlays=2,
        )

        self.assertTrue(ok, reason)
        self.assertEqual(click.call_count, 2)
        self.assertEqual(wait.call_count, 2)

    @patch("reward_flow.detect_home_reward_notification", return_value=(True, {}))
    @patch("reward_flow.recognize_home_labels", return_value=(True, {}))
    @patch("reward_flow.safe_capture_client", return_value=Image.new("RGB", (1000, 600)))
    def test_persistent_home_badge_prevents_false_completion(
        self,
        _capture: MagicMock,
        _home: MagicMock,
        _badge: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = wait_for_home_notification_clear(
                123,
                logger=RunLogger(Path(temporary)),
                target="tasks",
                target_name="任务",
                timeout=0,
            )
        self.assertFalse(ok)
        self.assertIn("任务红点仍存在", reason)

    @patch("reward_flow.detect_home_reward_notification", return_value=(False, {}))
    @patch("reward_flow.recognize_home_labels", return_value=(True, {}))
    @patch("reward_flow.safe_capture_client", return_value=Image.new("RGB", (1000, 600)))
    def test_cleared_home_badge_confirms_completion(
        self,
        _capture: MagicMock,
        _home: MagicMock,
        _badge: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = wait_for_home_notification_clear(
                123,
                logger=RunLogger(Path(temporary)),
                target="mail",
                target_name="邮件",
                timeout=0,
            )
        self.assertTrue(ok)
        self.assertIn("邮件红点消失", reason)

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
    def test_return_home_rejects_home_text_visible_behind_the_source_overlay(
        self,
        _capture: MagicMock,
        _recognize_home: MagicMock,
        _click: MagicMock,
        wait: MagicMock,
    ) -> None:
        source = MagicMock(return_value=(True, {"available": True}))
        wait.return_value = (False, Image.new("RGB", (1000, 600)), {})

        ok, _reason = return_to_home(
            123,
            logger=MagicMock(),
            recognize_source=source,
            source_name="通行证页面",
        )
        recognize = wait.call_args.kwargs["recognize"]

        found, details = recognize(Image.new("RGB", (1000, 600)))

        self.assertFalse(ok)
        self.assertFalse(found)
        self.assertTrue(details["home_found"])
        self.assertTrue(details["source_found"])

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
    def test_foreground_swipe_uses_real_mouse_drag(
        self,
        user32: MagicMock,
        _get_size: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        user32.ClientToScreen.return_value = 1
        user32.GetCursorPos.return_value = 1
        user32.GetForegroundWindow.return_value = 123
        user32.SetCursorPos.return_value = 1

        swipe_client_foreground(123, 200, 500, 200, 100, steps=2)

        flags = [entry.args[0] for entry in user32.mouse_event.call_args_list]
        self.assertEqual(flags, [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP])
        self.assertGreaterEqual(user32.SetCursorPos.call_count, 4)

    @patch("win32_windowpos_click.time.sleep")
    @patch("win32_windowpos_click.get_client_size", return_value=(1000, 600))
    @patch("win32_windowpos_click.user32")
    def test_foreground_swipe_releases_button_if_focus_is_lost(
        self,
        user32: MagicMock,
        _get_size: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        user32.ClientToScreen.return_value = 1
        user32.GetCursorPos.return_value = 1
        user32.GetForegroundWindow.side_effect = [123, 999]
        user32.SetCursorPos.return_value = 1

        with self.assertRaisesRegex(RuntimeError, "lost foreground"):
            swipe_client_foreground(123, 200, 500, 200, 100, steps=2)

        flags = [entry.args[0] for entry in user32.mouse_event.call_args_list]
        self.assertEqual(flags, [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP])

    @patch("win32_windowpos_click.ctypes.WinError", return_value=PermissionError(5, "拒绝访问"))
    @patch("win32_windowpos_click.get_client_size", return_value=(1000, 600))
    @patch("win32_windowpos_click.user32")
    def test_click_explains_when_windows_blocks_cursor_access(
        self,
        user32: MagicMock,
        _get_size: MagicMock,
        _win_error: MagicMock,
    ) -> None:
        user32.GetWindowRect.return_value = 1
        user32.ClientToScreen.return_value = 1
        user32.GetCursorPos.return_value = 0

        with self.assertRaisesRegex(RuntimeError, "屏幕保护程序或锁屏界面"):
            click_client(123, 10, 10)

        user32.SetWindowPos.assert_not_called()

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

        swipe_client(123, 200, 500, 200, 100, steps=2, end_hold=0.25)

        message_kinds = [entry.args[1] for entry in user32.PostMessageW.call_args_list]
        self.assertIn(WM_LBUTTONDOWN, message_kinds)
        self.assertEqual(message_kinds.count(WM_MOUSEMOVE), 3)
        self.assertEqual(message_kinds[-1], WM_LBUTTONUP)
        move_calls = [
            entry for entry in user32.PostMessageW.call_args_list
            if entry.args[1] == WM_MOUSEMOVE and entry.args[2] == MK_LBUTTON
        ]
        self.assertEqual(len(move_calls), 2)
        self.assertIn(call(0.25), _sleep.call_args_list)


if __name__ == "__main__":
    unittest.main()
