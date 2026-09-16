"""Minimal Win32 window-position click for BrownDust II.

This is a small extraction of the idea behind MaaFramework's
PostMessageWithWindowPos input mode:

1. Find the target Unity window.
2. Temporarily move the window so client coordinate (x, y) sits under the
   current real cursor position.
3. Post mouse messages to the target window.
4. Restore the original window position.

It supports clicks and short client-area swipe gestures.  The swipe path is
posted directly to the Unity window, so it does not move the physical cursor.
"""

from __future__ import annotations

import argparse
import ctypes
import time
from ctypes import wintypes

from win32_dpi import enable_dpi_awareness


enable_dpi_awareness()


user32 = ctypes.windll.user32

WM_ACTIVATE = 0x0006
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WA_ACTIVE = 1
MK_LBUTTON = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SW_RESTORE = 9

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _makelong(low: int, high: int) -> int:
    return (high & 0xFFFF) << 16 | (low & 0xFFFF)


def _post_message(hwnd: int, message: int, wparam: int, lparam: int) -> None:
    if not user32.PostMessageW(hwnd, message, wparam, lparam):
        raise ctypes.WinError()


def _set_window_origin(hwnd: int, left: int, top: int) -> None:
    flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    if not user32.SetWindowPos(hwnd, 0, left, top, 0, 0, flags):
        raise ctypes.WinError()


def _get_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _get_window_text(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def find_window(class_name: str = "UnityWndClass", title: str | None = "BrownDust II") -> int:
    found = wintypes.HWND(0)

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True
        if class_name and _get_class_name(hwnd) != class_name:
            return True
        if title and title not in _get_window_text(hwnd):
            return True
        found = wintypes.HWND(hwnd)
        return False

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return int(found.value or 0)


def get_client_size(hwnd: int) -> tuple[int, int]:
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    return rect.right - rect.left, rect.bottom - rect.top


def click_client(hwnd: int, x: int, y: int, *, restore: bool = True, delay: float = 0.06) -> None:
    width, height = get_client_size(hwnd)
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f"client coordinate out of range: ({x}, {y}) not in {width}x{height}")

    original_rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(original_rect)):
        raise ctypes.WinError()

    client_origin = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(client_origin)):
        raise ctypes.WinError()

    cursor = POINT()
    if not user32.GetCursorPos(ctypes.byref(cursor)):
        cause = ctypes.WinError()
        raise RuntimeError(
            "无法读取鼠标位置；Windows 屏幕保护程序或锁屏界面可能正在运行"
        ) from cause

    border_x = client_origin.x - original_rect.left
    border_y = client_origin.y - original_rect.top
    target_left = cursor.x - x - border_x
    target_top = cursor.y - y - border_y

    _set_window_origin(hwnd, target_left, target_top)

    lparam = _makelong(x, y)
    button_down = False
    try:
        time.sleep(delay)
        _post_message(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
        time.sleep(0.01)
        _post_message(hwnd, WM_MOUSEMOVE, 0, lparam)
        _post_message(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        button_down = True
        time.sleep(delay)
        _post_message(hwnd, WM_LBUTTONUP, 0, lparam)
        button_down = False
    finally:
        if button_down:
            user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
        if restore:
            time.sleep(delay)
            _set_window_origin(hwnd, original_rect.left, original_rect.top)


def swipe_client(
    hwnd: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    duration: float = 0.5,
    end_hold: float = 0.0,
    steps: int = 12,
    restore: bool = True,
    delay: float = 0.06,
) -> None:
    """Post a short left-button drag entirely inside the game client."""
    width, height = get_client_size(hwnd)
    for x, y in ((start_x, start_y), (end_x, end_y)):
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"client coordinate out of range: ({x}, {y}) not in {width}x{height}"
            )
    if duration <= 0:
        raise ValueError("swipe duration must be positive")
    if end_hold < 0:
        raise ValueError("swipe end hold must not be negative")
    if steps < 1:
        raise ValueError("swipe steps must be at least 1")

    original_rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(original_rect)):
        raise ctypes.WinError()

    client_origin = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(client_origin)):
        raise ctypes.WinError()

    cursor = POINT()
    if not user32.GetCursorPos(ctypes.byref(cursor)):
        raise ctypes.WinError()

    border_x = client_origin.x - original_rect.left
    border_y = client_origin.y - original_rect.top
    target_left = cursor.x - start_x - border_x
    target_top = cursor.y - start_y - border_y
    _set_window_origin(hwnd, target_left, target_top)

    button_down = False
    try:
        time.sleep(delay)
        _post_message(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
        _post_message(hwnd, WM_MOUSEMOVE, 0, _makelong(start_x, start_y))
        _post_message(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, _makelong(start_x, start_y))
        button_down = True
        step_delay = duration / steps
        for step in range(1, steps + 1):
            ratio = step / steps
            x = round(start_x + (end_x - start_x) * ratio)
            y = round(start_y + (end_y - start_y) * ratio)
            _post_message(hwnd, WM_MOUSEMOVE, MK_LBUTTON, _makelong(x, y))
            time.sleep(step_delay)
        if end_hold:
            time.sleep(end_hold)
        _post_message(hwnd, WM_LBUTTONUP, 0, _makelong(end_x, end_y))
        button_down = False
    finally:
        if button_down:
            user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, _makelong(end_x, end_y))
        if restore:
            time.sleep(delay)
            _set_window_origin(hwnd, original_rect.left, original_rect.top)


def swipe_client_foreground(
    hwnd: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    duration: float = 1.0,
    end_hold: float = 1.0,
    steps: int = 20,
    delay: float = 0.10,
) -> None:
    """Perform a real foreground drag for Unity lists that ignore posted drags."""
    width, height = get_client_size(hwnd)
    for x, y in ((start_x, start_y), (end_x, end_y)):
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"client coordinate out of range: ({x}, {y}) not in {width}x{height}"
            )
    if duration <= 0:
        raise ValueError("swipe duration must be positive")
    if end_hold < 0:
        raise ValueError("swipe end hold must not be negative")
    if steps < 1:
        raise ValueError("swipe steps must be at least 1")

    original_cursor = POINT()
    if not user32.GetCursorPos(ctypes.byref(original_cursor)):
        raise ctypes.WinError()

    button_down = False
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        start = POINT(start_x, start_y)
        end = POINT(end_x, end_y)
        if not user32.ClientToScreen(hwnd, ctypes.byref(start)):
            raise ctypes.WinError()
        if not user32.ClientToScreen(hwnd, ctypes.byref(end)):
            raise ctypes.WinError()
        user32.SetForegroundWindow(hwnd)
        time.sleep(delay)
        if user32.GetForegroundWindow() != hwnd:
            raise RuntimeError("target game window did not become foreground before swipe")
        if not user32.SetCursorPos(start.x, start.y):
            raise ctypes.WinError()
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        button_down = True
        step_delay = duration / steps
        for step in range(1, steps + 1):
            if user32.GetForegroundWindow() != hwnd:
                raise RuntimeError("target game window lost foreground during swipe")
            ratio = step / steps
            x = round(start.x + (end.x - start.x) * ratio)
            y = round(start.y + (end.y - start.y) * ratio)
            if not user32.SetCursorPos(x, y):
                raise ctypes.WinError()
            time.sleep(step_delay)
        if end_hold:
            time.sleep(end_hold)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        button_down = False
    finally:
        if button_down:
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        user32.SetCursorPos(original_cursor.x, original_cursor.y)


def main() -> None:
    parser = argparse.ArgumentParser(description="Click a BrownDust II client coordinate via Win32 window-position messages.")
    parser.add_argument("--x", type=int, required=True, help="client-area x coordinate")
    parser.add_argument("--y", type=int, required=True, help="client-area y coordinate")
    parser.add_argument("--class-name", default="UnityWndClass", help="target window class name")
    parser.add_argument("--title", default="BrownDust II", help="substring of target window title")
    parser.add_argument("--no-restore", action="store_true", help="leave the window at its temporary position")
    parser.add_argument("--delay", type=float, default=0.06, help="small delay around window move and click")
    args = parser.parse_args()

    hwnd = find_window(args.class_name, args.title)
    if not hwnd:
        raise SystemExit(f"target window not found: class={args.class_name!r}, title={args.title!r}")

    width, height = get_client_size(hwnd)
    click_client(hwnd, args.x, args.y, restore=not args.no_restore, delay=args.delay)
    print(f"clicked hwnd=0x{hwnd:X} client=({args.x},{args.y}) size={width}x{height}")


if __name__ == "__main__":
    main()
