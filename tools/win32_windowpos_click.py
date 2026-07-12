"""Minimal Win32 window-position click for BrownDust II.

This is a small extraction of the idea behind MaaFramework's
PostMessageWithWindowPos input mode:

1. Find the target Unity window.
2. Temporarily move the window so client coordinate (x, y) sits under the
   current real cursor position.
3. Post mouse messages to the target window.
4. Restore the original window position.

It is intentionally limited to single clicks. It does not implement the
low-level mouse hook MaaFramework uses to keep drag gestures stable while the
user moves the physical mouse.
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

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_ASYNCWINDOWPOS = 0x4000


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
        raise ctypes.WinError()

    border_x = client_origin.x - original_rect.left
    border_y = client_origin.y - original_rect.top
    target_left = cursor.x - x - border_x
    target_top = cursor.y - y - border_y

    flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
    if not user32.SetWindowPos(hwnd, 0, target_left, target_top, 0, 0, flags):
        raise ctypes.WinError()

    time.sleep(delay)

    lparam = _makelong(x, y)
    user32.PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
    time.sleep(0.01)
    user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(delay)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)

    if restore:
        time.sleep(delay)
        user32.SetWindowPos(
            hwnd,
            0,
            original_rect.left,
            original_rect.top,
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS,
        )


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
