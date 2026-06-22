"""PC window management custom actions."""

import sys
import time

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction


TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
RESIZE_TOLERANCE = 2
WINDOW_CLASS = "UnityWndClass"


def _rect_to_text(rect) -> str:
    return (
        f"left={rect.left}, top={rect.top}, right={rect.right}, bottom={rect.bottom}, "
        f"size={rect.right - rect.left}x{rect.bottom - rect.top}"
    )


def _hwnd_to_text(hwnd) -> str:
    value = getattr(hwnd, "value", hwnd)
    if value is None:
        value = 0
    return f"0x{int(value):X}"


def _get_window_title(user32, hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    from ctypes import create_unicode_buffer

    title = create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, length + 1)
    return title.value


def _resize_pc_window() -> tuple[bool, str]:
    if sys.platform != "win32":
        return True, "non-Windows platform, skipped"

    try:
        from ctypes import WINFUNCTYPE, byref, create_unicode_buffer, windll
        from ctypes.wintypes import BOOL, HWND, LPARAM, POINT, RECT

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
        title = _get_window_title(user32, hwnd)
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.1)

        window_rect = RECT()
        client_rect = RECT()
        client_origin = POINT(0, 0)

        user32.GetWindowRect(hwnd, byref(window_rect))
        user32.GetClientRect(hwnd, byref(client_rect))
        user32.ClientToScreen(hwnd, byref(client_origin))

        client_width = client_rect.right - client_rect.left
        client_height = client_rect.bottom - client_rect.top
        if client_width == TARGET_WIDTH and client_height == TARGET_HEIGHT:
            return (
                True,
                f"hwnd={_hwnd_to_text(hwnd)} title={title!r}; "
                f"window_before=({_rect_to_text(window_rect)}); "
                f"client_before=({_rect_to_text(client_rect)}); "
                f"client_origin=({client_origin.x},{client_origin.y}); "
                f"client area already {TARGET_WIDTH}x{TARGET_HEIGHT}",
            )

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
            return (
                False,
                f"hwnd={_hwnd_to_text(hwnd)} title={title!r}; "
                f"window_before=({_rect_to_text(window_rect)}); "
                f"client_before=({_rect_to_text(client_rect)}); SetWindowPos failed",
            )

        time.sleep(0.2)
        final_window_rect = RECT()
        final_client_rect = RECT()
        final_client_origin = POINT(0, 0)
        user32.GetWindowRect(hwnd, byref(final_window_rect))
        user32.GetClientRect(hwnd, byref(final_client_rect))
        user32.ClientToScreen(hwnd, byref(final_client_origin))
        actual_width = final_client_rect.right - final_client_rect.left
        actual_height = final_client_rect.bottom - final_client_rect.top
        width_delta = abs(actual_width - TARGET_WIDTH)
        height_delta = abs(actual_height - TARGET_HEIGHT)
        details = (
            f"hwnd={_hwnd_to_text(hwnd)} title={title!r}; "
            f"window_before=({_rect_to_text(window_rect)}); "
            f"client_before=({_rect_to_text(client_rect)}); "
            f"window_after=({_rect_to_text(final_window_rect)}); "
            f"client_after=({_rect_to_text(final_client_rect)}); "
            f"client_origin_after=({final_client_origin.x},{final_client_origin.y})"
        )

        if width_delta > RESIZE_TOLERANCE or height_delta > RESIZE_TOLERANCE:
            return (
                False,
                f"{details}; client area is {actual_width}x{actual_height}, expected "
                f"{TARGET_WIDTH}x{TARGET_HEIGHT}",
            )

        if width_delta or height_delta:
            return (
                True,
                f"{details}; client area resized to {actual_width}x{actual_height}, "
                f"within tolerance for {TARGET_WIDTH}x{TARGET_HEIGHT}",
            )

        return True, f"{details}; client area resized to {TARGET_WIDTH}x{TARGET_HEIGHT}"
    except Exception as exc:
        return False, f"resize failed: {exc}"


@AgentServer.custom_action("PC_ResizeWindow")
class PC_ResizeWindow(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        success, message = _resize_pc_window()
        print(f"[PC_ResizeWindow] {message}")
        return success
