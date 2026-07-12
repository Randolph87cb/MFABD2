"""Open BrownDust II through the Neowiz starter protocol."""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from win32_dpi import enable_dpi_awareness


enable_dpi_awareness()


STARTER = Path(r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe")
PROTOCOL_ARG = "browndust2:games/10000001?usn=0"
WINDOW_CLASS = "UnityWndClass"
WINDOW_TITLE = "BrownDust II"

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _window_text(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def find_game_window() -> int:
    found = wintypes.HWND(0)

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True
        if _class_name(hwnd) != WINDOW_CLASS:
            return True
        if WINDOW_TITLE not in _window_text(hwnd):
            return True
        found = wintypes.HWND(hwnd)
        return False

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return int(found.value or 0)


def open_game(timeout: float = 90.0) -> int:
    hwnd = find_game_window()
    if hwnd:
        return hwnd

    if not STARTER.exists():
        raise FileNotFoundError(f"starter not found: {STARTER}")

    subprocess.Popen(
        [str(STARTER), PROTOCOL_ARG],
        cwd=str(STARTER.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_game_window()
        if hwnd:
            return hwnd
        time.sleep(1)

    raise TimeoutError(f"game window not found after {timeout:.0f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Open BrownDust II and wait for the Unity window.")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    hwnd = open_game(args.timeout)
    print(f"game_window=0x{hwnd:X} title={WINDOW_TITLE} class={WINDOW_CLASS}")


if __name__ == "__main__":
    main()
