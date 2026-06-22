"""PC window management custom actions."""

import sys
import time

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction


TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
WINDOW_CLASS = "UnityWndClass"


def _resize_pc_window() -> tuple[bool, str]:
    if sys.platform != "win32":
        return True, "non-Windows platform, skipped"

    try:
        from ctypes import WINFUNCTYPE, byref, create_unicode_buffer, windll
        from ctypes.wintypes import BOOL, HWND, LPARAM, RECT

        user32 = windll.user32
        found_hwnd = HWND(0)

        def enum_callback(hwnd: HWND, _lparam: LPARAM) -> bool:
            nonlocal found_hwnd
            class_name = create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)

            if class_name.value == WINDOW_CLASS and user32.IsWindowVisible(hwnd):
                found_hwnd = hwnd
                return False
            return True

        enum_proc = WINFUNCTYPE(BOOL, HWND, LPARAM)(enum_callback)
        user32.EnumWindows(enum_proc, 0)

        if not found_hwnd:
            return False, f"window class {WINDOW_CLASS!r} not found"

        hwnd = found_hwnd
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.1)

        window_rect = RECT()
        client_rect = RECT()

        user32.GetWindowRect(hwnd, byref(window_rect))
        user32.GetClientRect(hwnd, byref(client_rect))

        client_width = client_rect.right - client_rect.left
        client_height = client_rect.bottom - client_rect.top
        if client_width == TARGET_WIDTH and client_height == TARGET_HEIGHT:
            return True, f"client area already {TARGET_WIDTH}x{TARGET_HEIGHT}"

        window_width = window_rect.right - window_rect.left
        window_height = window_rect.bottom - window_rect.top
        border_width = window_width - client_width
        border_height = window_height - client_height

        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_FRAMECHANGED = 0x0020
        ok = user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            TARGET_WIDTH + border_width,
            TARGET_HEIGHT + border_height,
            SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
        if not ok:
            return False, "SetWindowPos failed"

        time.sleep(0.2)
        user32.GetClientRect(hwnd, byref(client_rect))
        actual_width = client_rect.right - client_rect.left
        actual_height = client_rect.bottom - client_rect.top
        if actual_width != TARGET_WIDTH or actual_height != TARGET_HEIGHT:
            return (
                False,
                f"client area is {actual_width}x{actual_height}, expected "
                f"{TARGET_WIDTH}x{TARGET_HEIGHT}",
            )

        return True, f"client area resized to {TARGET_WIDTH}x{TARGET_HEIGHT}"
    except Exception as exc:
        return False, f"resize failed: {exc}"


@AgentServer.custom_action("PC_ResizeWindow")
class PC_ResizeWindow(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        success, message = _resize_pc_window()
        print(f"[PC_ResizeWindow] {message}")
        return success
