"""Recognize the title screen, click Touch To Start, and wait for home screen.

This is the second recorded automation step. It uses only image statistics in
the user-marked title/touch regions for now. The goal is a minimal, inspectable
unit that can later be replaced by template matching or OCR.
"""

from __future__ import annotations

import argparse
import ctypes
import time
from pathlib import Path

import numpy as np
from PIL import Image

from open_game import find_game_window, open_game
from probe_printwindow import RECT, save_hwnd
from win32_windowpos_click import click_client


user32 = ctypes.windll.user32

TITLE_ROI = (0.57, 0.30, 0.32, 0.23)
TOUCH_ROI = (0.59, 0.62, 0.30, 0.16)
TOUCH_CLICK = (0.74, 0.70)
TITLE_LEFT_HUD_ROI = (0.28, 0.18, 0.10, 0.25)
HOME_TOP_LEFT_UI_ROI = (0.00, 0.08, 0.28, 0.25)
HOME_MINIMAP_ROI = (0.10, 0.25, 0.22, 0.28)


def _client_offset(hwnd: int) -> tuple[int, int, int, int]:
    window_rect = RECT()
    client_rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
        raise ctypes.WinError()
    if not user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
        raise ctypes.WinError()

    point = ctypes.wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
        raise ctypes.WinError()

    left = point.x - window_rect.left
    top = point.y - window_rect.top
    width = client_rect.right - client_rect.left
    height = client_rect.bottom - client_rect.top
    return left, top, width, height


def capture_client(hwnd: int, out_path: Path | None = None) -> Image.Image:
    tmp_path = out_path or (Path.cwd() / "last_window_capture.png")
    save_hwnd(hwnd, tmp_path)
    image = Image.open(tmp_path).convert("RGB")
    left, top, width, height = _client_offset(hwnd)
    return image.crop((left, top, left + width, top + height))


def _roi(image: np.ndarray, spec: tuple[float, float, float, float]) -> np.ndarray:
    height, width = image.shape[:2]
    x, y, w, h = spec
    x0 = int(width * x)
    y0 = int(height * y)
    x1 = min(width, int(width * (x + w)))
    y1 = min(height, int(height * (y + h)))
    return image[y0:y1, x0:x1]


def _region_score(region: np.ndarray) -> dict[str, float]:
    gray = (
        region[:, :, 0].astype(np.float32) * 0.299
        + region[:, :, 1].astype(np.float32) * 0.587
        + region[:, :, 2].astype(np.float32) * 0.114
    )
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    edge_ratio = (float(np.mean(dx > 35)) + float(np.mean(dy > 35))) / 2
    dark_ratio = float(np.mean(gray < 90))
    bright_ratio = float(np.mean(gray > 190))
    contrast = float(np.std(gray))
    return {
        "dark_ratio": dark_ratio,
        "bright_ratio": bright_ratio,
        "edge_ratio": edge_ratio,
        "contrast": contrast,
    }


def recognize_title_screen(image: Image.Image) -> tuple[bool, dict[str, dict[str, float]]]:
    frame = np.asarray(image)
    title_score = _region_score(_roi(frame, TITLE_ROI))
    touch_score = _region_score(_roi(frame, TOUCH_ROI))
    left_hud_score = _region_score(_roi(frame, TITLE_LEFT_HUD_ROI))

    title_ok = (
        title_score["dark_ratio"] > 0.035
        and title_score["bright_ratio"] > 0.45
        and title_score["edge_ratio"] > 0.035
        and title_score["contrast"] > 45
    )
    touch_ok = (
        touch_score["dark_ratio"] > 0.015
        and touch_score["bright_ratio"] > 0.45
        and touch_score["edge_ratio"] > 0.018
        and touch_score["contrast"] > 25
    )

    # PrintWindow can miss the title logo / Touch To Start overlay on this Unity
    # client. The version/power/settings cluster remains visible and is a stable
    # fallback marker for the title screen in the current capture path.
    left_hud_ok = (
        left_hud_score["dark_ratio"] > 0.08
        and left_hud_score["bright_ratio"] > 0.70
        and 0.035 < left_hud_score["edge_ratio"] < 0.09
        and left_hud_score["contrast"] > 45
    )
    return (title_ok and touch_ok) or left_hud_ok, {
        "title": title_score,
        "touch": touch_score,
        "left_hud": left_hud_score,
    }


def recognize_entry_state(image: Image.Image) -> tuple[str, dict[str, dict[str, float]]]:
    is_home, home_scores = recognize_home_screen(image)
    is_title, title_scores = recognize_title_screen(image)
    if is_home:
        return "home", {**title_scores, **home_scores}

    title_score = title_scores["title"]
    touch_score = title_scores["touch"]
    left_hud_score = title_scores["left_hud"]

    logo_like = title_score["dark_ratio"] > 0.20 and title_score["edge_ratio"] > 0.02 and title_score["contrast"] > 60
    left_hud_like = left_hud_score["bright_ratio"] > 0.70 and left_hud_score["contrast"] > 25
    touch_ready = touch_score["bright_ratio"] > 0.08 and touch_score["edge_ratio"] > 0.015 and touch_score["contrast"] > 25

    if is_title or ((logo_like or left_hud_like) and touch_ready):
        return "touch_ready", {**title_scores, **home_scores}
    if logo_like or left_hud_like:
        return "loading_title", {**title_scores, **home_scores}
    return "unknown", {**title_scores, **home_scores}


def recognize_home_screen(image: Image.Image) -> tuple[bool, dict[str, dict[str, float]]]:
    frame = np.asarray(image)
    top_left_score = _region_score(_roi(frame, HOME_TOP_LEFT_UI_ROI))
    minimap_score = _region_score(_roi(frame, HOME_MINIMAP_ROI))

    top_left_ok = (
        top_left_score["dark_ratio"] < 0.40
        and top_left_score["bright_ratio"] > 0.20
        and top_left_score["edge_ratio"] > 0.04
        and top_left_score["contrast"] > 50
    )
    minimap_ok = minimap_score["edge_ratio"] > 0.02 and minimap_score["contrast"] > 45
    return top_left_ok and minimap_ok, {
        "top_left_ui": top_left_score,
        "minimap": minimap_score,
    }


def wait_until_home(hwnd: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        image = capture_client(hwnd)
        is_home, _home_scores = recognize_home_screen(image)
        if is_home:
            return True
        time.sleep(1)
    return False


def enter_game(timeout: float = 90.0, save_debug: bool = True) -> bool:
    hwnd = find_game_window() or open_game(timeout=timeout)

    deadline = time.monotonic() + timeout
    image = None
    last_click_at = 0.0
    while time.monotonic() < deadline:
        image = capture_client(hwnd, Path.cwd() / "enter_game_before_window.png")
        if save_debug:
            image.save(Path.cwd() / "enter_game_before_client.png")

        state, scores = recognize_entry_state(image)
        print(f"entry_state={state} scores={scores}")
        if state == "home":
            print("already_home=True")
            return True
        if state in {"touch_ready", "loading_title"} and time.monotonic() - last_click_at >= 5:
            width, height = image.size
            click_x = int(width * TOUCH_CLICK[0])
            click_y = int(height * TOUCH_CLICK[1])
            print(f"click_touch_to_start=({click_x},{click_y}) state={state}")
            click_client(hwnd, click_x, click_y)
            last_click_at = time.monotonic()
        time.sleep(2)

    reached_home = False
    after = capture_client(hwnd, Path.cwd() / "enter_game_after_window.png")
    if save_debug:
        after.save(Path.cwd() / "enter_game_after_client.png")
    return reached_home


def main() -> None:
    parser = argparse.ArgumentParser(description="Click Touch To Start and wait until the title screen changes.")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--no-debug", action="store_true")
    args = parser.parse_args()

    ok = enter_game(timeout=args.timeout, save_debug=not args.no_debug)
    if not ok:
        raise SystemExit("failed to recognize or leave title screen")
    print("enter_game_ok=True")


if __name__ == "__main__":
    main()
