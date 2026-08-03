"""Run the BrownDust II free gacha flow.

The script is intentionally conservative: it captures the current client,
classifies the visible state, clicks only the expected safe control for that
state, and stops with a saved screenshot when the state is unknown.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from enter_game import capture_client, recognize_home_screen
from game_text_recognition import (
    recognize_free_gacha_confirmation_labels,
    recognize_gacha_item_detail_labels,
    recognize_gacha_page_labels,
    recognize_home_labels,
    recognize_quick_hunt_map_labels,
    recognize_quick_hunt_result_labels,
    recognize_quick_hunt_setup_labels,
)
from open_game import find_game_window
from win32_windowpos_click import click_client


user32 = ctypes.windll.user32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_H = 0x48
SW_SHOWNOACTIVATE = 4

CLICK_POINTS = {
    "home_gacha": (0.086, 0.925),
    "quick_hunt": (0.918, 0.255),
    "quick_hunt_start": (0.855, 0.918),
    "quick_hunt_max": (0.609, 0.471),
    "quick_hunt_confirm": (0.540, 0.725),
    "quick_hunt_result_dismiss": (0.120, 0.800),
    "quick_hunt_crystal_cave": (0.091, 0.440),
    "quick_hunt_back": (0.090, 0.045),
    "plaza_home": (0.935, 0.055),
    # Home promotions use a bright center panel and a dimmed, non-interactive margin.
    "dismiss_overlay": (0.138, 0.565),
    "costume_tab": (0.086, 0.315),
    "gear_tab": (0.086, 0.420),
    "all_free": (0.178, 0.895),
    "confirm": (0.548, 0.598),
    "skip_animation": (0.138, 0.565),
    "result_back": (0.090, 0.045),
}


TARGET_LABELS = {
    "costume": "costume_tab",
    "gear": "gear_tab",
}


@dataclass
class ActionResult:
    state: str
    action: str
    reason: str


class RunLogger:
    def __init__(self, root: Path, *, annotate_clicks: bool = False) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self._click_count = 0
        self.annotate_clicks = annotate_clicks

    def event(self, **payload: Any) -> None:
        payload.setdefault("time", datetime.now().isoformat(timespec="seconds"))
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def next_click_index(self) -> int:
        self._click_count += 1
        return self._click_count

    def save_image(self, image: Image.Image, name: str) -> Path:
        path = self.root / name
        image.copy().save(path)
        return path

    def save_click_image(
        self,
        image: Image.Image,
        name: str,
        *,
        x: int,
        y: int,
        key: str,
        dry_run: bool,
    ) -> Path:
        marked = image.convert("RGB").copy()
        draw = ImageDraw.Draw(marked)
        width, height = marked.size
        radius = max(18, min(width, height) // 45)
        line = max(4, min(width, height) // 260)
        color = (255, 0, 0)

        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=line)
        draw.line((x - radius * 2, y, x + radius * 2, y), fill=color, width=line)
        draw.line((x, y - radius * 2, x, y + radius * 2), fill=color, width=line)

        label = f"{key} ({x},{y}) dry_run={dry_run}"
        text_x = min(max(8, x + radius + 10), max(8, width - 520))
        text_y = min(max(8, y - radius - 36), max(8, height - 48))
        box = (text_x - 6, text_y - 6, min(width - 8, text_x + 500), min(height - 8, text_y + 34))
        draw.rectangle(box, fill=(0, 0, 0), outline=color, width=2)
        draw.text((text_x, text_y), label, fill=(255, 255, 255))

        path = self.root / name
        marked.save(path)
        return path

    def failure(self, reason: str) -> Path:
        path = self.root / "failure.txt"
        path.write_text(reason + "\n", encoding="utf-8")
        self.event(action="failure_written", path=str(path), reason=reason)
        return path


def safe_capture_client(
    hwnd: int,
    *,
    logger: RunLogger | None = None,
    attempts: int = 8,
    delay: float = 1.0,
    min_size: tuple[int, int] = (1000, 600),
) -> Image.Image:
    last_error: Exception | None = None
    min_width, min_height = min_size
    recovered = False
    for attempt in range(1, attempts + 1):
        try:
            image = capture_client(hwnd).convert("RGB").copy()
            image.load()
            if image.width < min_width or image.height < min_height:
                raise ValueError(f"invalid client capture size: {image.width}x{image.height}")
            if recovered and logger:
                logger.event(
                    action="capture_recovered",
                    attempt=attempt,
                    width=image.width,
                    height=image.height,
                )
            return image
        except Exception as exc:  # noqa: BLE001 - logged and retried by design.
            last_error = exc
            window_state = _capture_window_state(hwnd)
            if logger:
                logger.event(
                    action="capture_retry",
                    attempt=attempt,
                    error=repr(exc),
                    window=window_state,
                )
            if window_state["minimized"]:
                user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
                recovered = True
                if logger:
                    logger.event(
                        action="restore_minimized_window",
                        attempt=attempt,
                        command="SW_SHOWNOACTIVATE",
                    )
            time.sleep(delay)
    raise RuntimeError(f"failed to capture valid client image after {attempts} attempts: {last_error!r}")


def _capture_window_state(hwnd: int) -> dict[str, Any]:
    rect = wintypes.RECT()
    has_client_rect = bool(user32.GetClientRect(hwnd, ctypes.byref(rect)))
    return {
        "valid": bool(user32.IsWindow(hwnd)),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "minimized": bool(user32.IsIconic(hwnd)),
        "client_width": max(0, rect.right - rect.left) if has_client_rect else 0,
        "client_height": max(0, rect.bottom - rect.top) if has_client_rect else 0,
    }


def _gray(frame: np.ndarray) -> np.ndarray:
    return (
        frame[:, :, 0].astype(np.float32) * 0.299
        + frame[:, :, 1].astype(np.float32) * 0.587
        + frame[:, :, 2].astype(np.float32) * 0.114
    )


def _roi(frame: np.ndarray, spec: tuple[float, float, float, float]) -> np.ndarray:
    height, width = frame.shape[:2]
    x, y, w, h = spec
    x0 = max(0, min(width - 1, int(width * x)))
    y0 = max(0, min(height - 1, int(height * y)))
    x1 = max(x0 + 1, min(width, int(width * (x + w))))
    y1 = max(y0 + 1, min(height, int(height * (y + h))))
    return frame[y0:y1, x0:x1]


def _stats(region: np.ndarray) -> dict[str, float]:
    if region.size == 0 or region.shape[0] < 2 or region.shape[1] < 2:
        return {
            "mean": 0.0,
            "dark_ratio": 0.0,
            "mid_ratio": 0.0,
            "bright_ratio": 0.0,
            "edge_ratio": 0.0,
            "contrast": 0.0,
        }
    gray = _gray(region)
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    return {
        "mean": float(np.mean(gray)),
        "dark_ratio": float(np.mean(gray < 90)),
        "mid_ratio": float(np.mean((gray >= 90) & (gray <= 220))),
        "bright_ratio": float(np.mean(gray > 220)),
        "edge_ratio": (float(np.mean(dx > 35)) + float(np.mean(dy > 35))) / 2,
        "contrast": float(np.std(gray)),
    }


def _mean_region_difference(
    before: Image.Image,
    after: Image.Image,
    spec: tuple[float, float, float, float] = (0.20, 0.18, 0.60, 0.66),
) -> float:
    if before.size != after.size:
        return float("inf")
    before_frame = np.asarray(before.convert("RGB"), dtype=np.float32)
    after_frame = np.asarray(after.convert("RGB"), dtype=np.float32)
    before_region = _roi(before_frame, spec)
    after_region = _roi(after_frame, spec)
    return float(np.mean(np.abs(before_region - after_region)))


def detect_selected_gacha_target(image: Image.Image) -> str | None:
    frame = np.asarray(image.convert("RGB"))
    costume = _stats(_roi(frame, (0.055, 0.24, 0.075, 0.12)))
    gear = _stats(_roi(frame, (0.055, 0.35, 0.075, 0.12)))
    if costume["bright_ratio"] > gear["bright_ratio"] + 0.015:
        return "costume"
    if gear["bright_ratio"] > costume["bright_ratio"] + 0.015:
        return "gear"
    if costume["mean"] > gear["mean"] + 8:
        return "costume"
    if gear["mean"] > costume["mean"] + 8:
        return "gear"
    return None


def classify_state(image: Image.Image) -> tuple[str, dict[str, Any]]:
    frame = np.asarray(image.convert("RGB"))
    full = _stats(frame)
    center = _stats(_roi(frame, (0.28, 0.24, 0.44, 0.46)))
    modal = _stats(_roi(frame, (0.33, 0.34, 0.34, 0.32)))
    confirm_buttons = _stats(_roi(frame, (0.40, 0.55, 0.22, 0.11)))
    top_left_back = _stats(_roi(frame, (0.00, 0.015, 0.12, 0.09)))
    gacha_button = _stats(_roi(frame, (0.10, 0.84, 0.15, 0.12)))
    home_bottom_nav = _stats(_roi(frame, (0.04, 0.86, 0.58, 0.12)))
    home_right_events = _stats(_roi(frame, (0.78, 0.22, 0.19, 0.68)))
    home_top_right = _stats(_roi(frame, (0.78, 0.02, 0.19, 0.12)))
    left_tabs = _stats(_roi(frame, (0.055, 0.24, 0.075, 0.27)))
    top_title = _stats(_roi(frame, (0.11, 0.02, 0.22, 0.10)))
    plaza_joystick = _stats(_roi(frame, (0.10, 0.72, 0.12, 0.20)))
    plaza_actions = _stats(_roi(frame, (0.72, 0.68, 0.25, 0.28)))
    plaza_top_right = _stats(_roi(frame, (0.84, 0.02, 0.14, 0.10)))
    animation_left_margin = _stats(_roi(frame, (0.00, 0.00, 0.23, 1.00)))
    animation_top_right = _stats(_roi(frame, (0.82, 0.01, 0.15, 0.12)))
    animation_bottom_reveal = _stats(_roi(frame, (0.40, 0.84, 0.20, 0.14)))

    details: dict[str, Any] = {
        "full": full,
        "center": center,
        "modal": modal,
        "confirm_buttons": confirm_buttons,
        "top_left_back": top_left_back,
        "gacha_button": gacha_button,
        "home_bottom_nav": home_bottom_nav,
        "home_right_events": home_right_events,
        "home_top_right": home_top_right,
        "left_tabs": left_tabs,
        "top_title": top_title,
        "plaza_joystick": plaza_joystick,
        "plaza_actions": plaza_actions,
        "plaza_top_right": plaza_top_right,
        "animation_left_margin": animation_left_margin,
        "animation_top_right": animation_top_right,
        "animation_bottom_reveal": animation_bottom_reveal,
    }

    large_activity_overlay_like = (
        full["dark_ratio"] > 0.65
        and center["mean"] > 120
        and center["bright_ratio"] > 0.08
        and modal["contrast"] > 45
        and home_bottom_nav["dark_ratio"] > 0.95
        and home_right_events["dark_ratio"] > 0.90
    )
    if large_activity_overlay_like:
        return "home_overlay", details

    blocking_ad_overlay_like = (
        full["dark_ratio"] > 0.55
        and center["mean"] > 150
        and center["bright_ratio"] > 0.20
        and modal["bright_ratio"] > 0.25
        and confirm_buttons["bright_ratio"] > 0.20
        and home_bottom_nav["dark_ratio"] > 0.95
        and home_right_events["dark_ratio"] > 0.90
    )
    if blocking_ad_overlay_like:
        return "blocking_ad_overlay", details

    dark_confirm_like = (
        full["dark_ratio"] > 0.90
        and modal["dark_ratio"] < 0.94
        and modal["edge_ratio"] > 0.012
        and confirm_buttons["mean"] > modal["mean"] + 20
        and confirm_buttons["edge_ratio"] > 0.015
    )
    if dark_confirm_like:
        confirm_match, confirm_text = recognize_free_gacha_confirmation_labels(image)
        details["free_gacha_confirm_text"] = confirm_text
        if confirm_match:
            return "confirm_free_gacha", details

    quick_hunt_setup_candidate = (
        full["dark_ratio"] > 0.90
        and center["mean"] > full["mean"] + 15
        and center["edge_ratio"] > 0.008
    )
    if quick_hunt_setup_candidate:
        quick_hunt_setup_match, quick_hunt_setup_text = recognize_quick_hunt_setup_labels(image)
        details["quick_hunt_setup_text"] = quick_hunt_setup_text
        if quick_hunt_setup_match:
            return "quick_hunt_setup", details

    quick_hunt_result_candidate = (
        full["dark_ratio"] > 0.85
        and center["mean"] > full["mean"] + 15
    )
    if quick_hunt_result_candidate:
        quick_hunt_result_match, quick_hunt_result_text = recognize_quick_hunt_result_labels(image)
        details["quick_hunt_result_text"] = quick_hunt_result_text
        if quick_hunt_result_match:
            return "quick_hunt_result", details

    dark_item_overlay_like = (
        full["dark_ratio"] > 0.95
        and center["mean"] > full["mean"] + 25
        and center["edge_ratio"] > 0.012
        and modal["edge_ratio"] > 0.012
        and home_bottom_nav["dark_ratio"] > 0.98
    )
    if dark_item_overlay_like:
        return "home_overlay", details

    gacha_item_detail_candidate = (
        full["dark_ratio"] > 0.95
        and 40 < full["mean"] < 80
        and abs(center["mean"] - full["mean"]) < 15
        and modal["edge_ratio"] > 0.004
    )
    if gacha_item_detail_candidate:
        item_detail_match, item_detail_text = recognize_gacha_item_detail_labels(image)
        details["gacha_item_detail_text"] = item_detail_text
        if item_detail_match:
            return "gacha_item_overlay", details

    loading_like = full["mean"] < 45 and full["dark_ratio"] > 0.92 and full["edge_ratio"] < 0.005
    if loading_like:
        return "loading", details

    gacha_like = (
        full["dark_ratio"] < 0.65
        and left_tabs["edge_ratio"] > 0.015
        and top_title["edge_ratio"] > 0.030
    )
    if gacha_like:
        quick_hunt_map_match, quick_hunt_map_text = recognize_quick_hunt_map_labels(image)
        details["quick_hunt_map_text"] = quick_hunt_map_text
        if quick_hunt_map_match:
            return "quick_hunt_map", details
        gacha_page_match, gacha_page_text = recognize_gacha_page_labels(image)
        details["gacha_page_text"] = gacha_page_text
        if gacha_page_match:
            return "gacha_page", details

    confirm_like = (
        full["dark_ratio"] > 0.45
        and modal["mid_ratio"] > 0.45
        and modal["contrast"] > 20
        and confirm_buttons["bright_ratio"] > 0.15
    )
    if confirm_like:
        confirm_match, confirm_text = recognize_free_gacha_confirmation_labels(image)
        details["free_gacha_confirm_text"] = confirm_text
        if confirm_match:
            return "confirm_free_gacha", details

    dark_animation_like = (
        animation_left_margin["dark_ratio"] > 0.98
        and animation_left_margin["contrast"] < 10
        and animation_top_right["edge_ratio"] > 0.010
        and animation_bottom_reveal["mid_ratio"] > 0.20
    )
    reveal_animation_like = (
        animation_top_right["edge_ratio"] > 0.012
        and animation_top_right["bright_ratio"] < 0.10
        and animation_bottom_reveal["mid_ratio"] > 0.20
        and animation_bottom_reveal["edge_ratio"] < 0.020
    )
    if dark_animation_like or reveal_animation_like:
        return "gacha_animation", details

    overlay_like = (
        full["dark_ratio"] > 0.45
        and center["mean"] > full["mean"] + 35
        and center["contrast"] > 35
        and home_bottom_nav["dark_ratio"] > 0.85
    )
    if overlay_like:
        return "home_overlay", details

    home_labels_match, home_text = recognize_home_labels(image)
    details["home_text"] = home_text
    if home_labels_match:
        return "real_home", details

    bright_scene = full["bright_ratio"] > 0.62 and full["dark_ratio"] < 0.10
    if bright_scene:
        if top_left_back["mean"] < 220 and top_left_back["mid_ratio"] > 0.25:
            return "gacha_result", details
        return "gacha_animation", details

    plaza_bright_joystick_like = (
        plaza_joystick["mid_ratio"] > 0.88
        and plaza_joystick["bright_ratio"] < 0.06
        and plaza_joystick["edge_ratio"] > 0.010
    )
    plaza_dark_joystick_like = (
        plaza_joystick["dark_ratio"] > 0.80
        and plaza_joystick["mid_ratio"] < 0.12
        and plaza_joystick["edge_ratio"] > 0.018
    )
    plaza_like = (
        (plaza_bright_joystick_like or plaza_dark_joystick_like)
        and plaza_actions["edge_ratio"] > 0.035
        and plaza_actions["contrast"] > 35
        and plaza_top_right["edge_ratio"] > 0.050
    )
    if plaza_like:
        return "plaza", details

    is_home, home_scores = recognize_home_screen(image)
    details["home_scores"] = home_scores
    if is_home:
        return "ambiguous_home", details

    return "unknown", details


def _click_ratio(hwnd: int, image: Image.Image, key: str, *, dry_run: bool, logger: RunLogger) -> None:
    rx, ry = CLICK_POINTS[key]
    width, height = image.size
    x = int(width * rx)
    y = int(height * ry)
    marked_path: Path | None = None
    if logger.annotate_clicks:
        click_index = logger.next_click_index()
        marked_path = logger.save_click_image(
            image,
            f"click-{click_index:03d}-{key}.png",
            x=x,
            y=y,
            key=key,
            dry_run=dry_run,
        )
    logger.event(
        action="click",
        key=key,
        x=x,
        y=y,
        dry_run=dry_run,
        screenshot=str(marked_path) if marked_path else None,
    )
    if not dry_run:
        click_client(hwnd, x, y)


def post_home_key(hwnd: int, *, dry_run: bool, logger: RunLogger) -> None:
    logger.event(action="post_key", key="H", vk=VK_H, dry_run=dry_run)
    if dry_run:
        return
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_H, 0)
    time.sleep(0.08)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_H, 0)


def wait_for_state(
    hwnd: int,
    logger: RunLogger,
    *,
    expected: set[str],
    timeout: float,
    interval: float,
    label: str,
) -> tuple[str, Image.Image]:
    deadline = time.monotonic() + timeout
    last_state = "unknown"
    last_image: Image.Image | None = None
    sample = 0
    while time.monotonic() < deadline:
        sample += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(image)
        image_path = logger.save_image(image, f"{label}-{sample:02d}-{state}.png")
        logger.event(
            action="wait_state",
            label=label,
            sample=sample,
            state=state,
            expected=sorted(expected),
            screenshot=str(image_path),
            details=details,
        )
        last_state = state
        last_image = image
        if state in expected:
            return state, image
        time.sleep(interval)
    if last_image is None:
        raise RuntimeError(f"no state captured while waiting for {sorted(expected)}")
    return last_state, last_image


def return_home_from_plaza(
    hwnd: int,
    image: Image.Image,
    *,
    dry_run: bool,
    logger: RunLogger,
    interval: float,
) -> tuple[bool, str]:
    expected = {"real_home", "home_overlay"}

    for attempt in range(1, 3):
        logger.event(action="return_home_attempt", method="plaza_home_click", attempt=attempt)
        _click_ratio(hwnd, image, "plaza_home", dry_run=dry_run, logger=logger)
        if dry_run:
            return True, "dry-run planned plaza_home click"
        state, image = wait_for_state(
            hwnd,
            logger,
            expected=expected,
            timeout=10.0,
            interval=interval,
            label=f"after-plaza-click-{attempt}",
        )
        if state in expected:
            return True, f"returned home by plaza_home click, state={state}"
        if state in {"loading", "unknown"}:
            state, image = wait_for_state(
                hwnd,
                logger,
                expected=expected | {"plaza"},
                timeout=8.0,
                interval=interval,
                label=f"after-plaza-click-extra-wait-{attempt}",
            )
            if state in expected:
                return True, f"returned home by plaza_home click after loading, state={state}"
        if state not in {"plaza", "loading", "unknown", "ambiguous_home"}:
            return False, f"unexpected state after plaza_home click: {state}"

    for attempt in range(1, 3):
        logger.event(action="return_home_attempt", method="post_h_key", attempt=attempt)
        post_home_key(hwnd, dry_run=dry_run, logger=logger)
        if dry_run:
            return True, "dry-run planned background H key"
        state, image = wait_for_state(
            hwnd,
            logger,
            expected=expected,
            timeout=10.0,
            interval=interval,
            label=f"after-post-h-{attempt}",
        )
        if state in expected:
            return True, f"returned home by background H key, state={state}"
        if state in {"loading", "unknown"}:
            state, image = wait_for_state(
                hwnd,
                logger,
                expected=expected | {"plaza"},
                timeout=8.0,
                interval=interval,
                label=f"after-post-h-extra-wait-{attempt}",
            )
            if state in expected:
                return True, f"returned home by background H key after loading, state={state}"
        if state not in {"plaza", "loading", "unknown", "ambiguous_home"}:
            return False, f"unexpected state after background H key: {state}"

    return False, "failed to return from plaza by background click or background H key"


def _target_tab(target: str) -> str:
    try:
        return TARGET_LABELS[target]
    except KeyError as exc:
        raise ValueError(f"unsupported target: {target}") from exc


def is_free_gacha_confirm_transition(state: str) -> bool:
    return state in {"gacha_page", "gacha_animation", "gacha_result", "loading"}


def click_with_fixed_retry(
    hwnd: int,
    image: Image.Image,
    key: str,
    *,
    verify: Callable[[str, Image.Image], bool],
    description: str,
    dry_run: bool,
    logger: RunLogger,
    delay: float = 6.0,
    attempts: int = 2,
) -> tuple[bool, str, Image.Image, str]:
    current_image = image
    source_state, _ = classify_state(image)
    current_state = source_state
    for attempt in range(1, attempts + 1):
        _click_ratio(hwnd, current_image, key, dry_run=dry_run, logger=logger)
        if dry_run:
            return True, current_state, current_image, f"dry-run planned {description}"

        time.sleep(delay)
        current_image = safe_capture_client(hwnd, logger=logger)
        current_state, details = classify_state(current_image)
        stamp = datetime.now().strftime("%H%M%S-%f")
        verify_path = logger.save_image(
            current_image,
            f"verify-{stamp}-{key}-attempt-{attempt}-{current_state}.png",
        )
        succeeded = verify(current_state, current_image)
        logger.event(
            action="verify_click",
            key=key,
            description=description,
            attempt=attempt,
            state=current_state,
            succeeded=succeeded,
            screenshot=str(verify_path),
            details=details,
        )
        if succeeded:
            return True, current_state, current_image, f"{description} succeeded on attempt {attempt}"
        if attempt < attempts:
            if current_state != source_state:
                reason = (
                    f"{description} changed from {source_state} to unexpected state "
                    f"{current_state}; retry cancelled"
                )
                return False, current_state, current_image, reason
            logger.event(
                action="retry_click",
                key=key,
                description=description,
                previous_attempts=attempt,
                delay=delay,
            )

    reason = f"{description} did not take effect after {attempts} clicks"
    return False, current_state, current_image, reason


def run_free_gacha(
    *,
    targets: list[str],
    timeout: float,
    interval: float,
    dry_run: bool,
    test_mode: bool,
    log_root: Path,
) -> ActionResult:
    hwnd = find_game_window()
    logger = RunLogger(log_root, annotate_clicks=test_mode)
    logger.event(action="start", targets=targets, timeout=timeout, dry_run=dry_run, test_mode=test_mode)
    if not hwnd:
        reason = "game window not found"
        logger.event(action="stop", result="error", reason=reason)
        return ActionResult("missing_window", "stop", reason)

    target_index = 0
    switched: set[str] = set()
    result_back_target: str | None = None
    deadline = time.monotonic() + timeout
    step = 0

    while time.monotonic() < deadline:
        step += 1
        try:
            image = safe_capture_client(hwnd, logger=logger)
        except Exception as exc:  # noqa: BLE001
            reason = f"capture failed: {exc!r}"
            logger.failure(reason)
            logger.event(action="stop", result="error", reason=reason)
            return ActionResult("capture_error", "stop", reason)
        state, details = classify_state(image)
        try:
            image_path = logger.save_image(image, f"step-{step:03d}-{state}.png")
        except Exception as exc:  # noqa: BLE001
            reason = f"debug image save failed: {exc!r}"
            logger.failure(reason)
            logger.event(action="stop", result="error", reason=reason)
            return ActionResult(state, "stop", reason)
        current_target = targets[target_index] if target_index < len(targets) else None
        logger.event(
            action="classify",
            step=step,
            state=state,
            current_target=current_target,
            screenshot=str(image_path),
            details=details,
        )

        if target_index >= len(targets):
            reason = "all requested free gacha targets completed"
            logger.event(action="stop", result="success", reason=reason)
            return ActionResult(state, "stop", reason)

        if result_back_target is not None and state == "gacha_page":
            logger.event(action="target_complete", target=result_back_target)
            target_index += 1
            result_back_target = None
            current_target = targets[target_index] if target_index < len(targets) else None
            if current_target is None:
                reason = "all requested free gacha targets completed"
                logger.event(action="stop", result="success", reason=reason)
                return ActionResult(state, "stop", reason)

        if state == "loading":
            logger.event(action="wait_loading", step=step)
            time.sleep(interval)
            continue

        if state in {"home_overlay", "blocking_ad_overlay", "gacha_item_overlay"}:
            overlay_before = image.copy()
            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "dismiss_overlay",
                verify=lambda next_state, next_image: (
                    next_state not in {"home_overlay", "blocking_ad_overlay", "gacha_item_overlay"}
                    or _mean_region_difference(overlay_before, next_image) >= 2.5
                ),
                description="dismiss home overlay",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            continue

        if state == "real_home":
            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "home_gacha",
                verify=lambda next_state, _next_image: next_state in {"gacha_page", "loading"},
                description="open gacha from home",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            continue

        if state == "plaza":
            ok, reason = return_home_from_plaza(
                hwnd,
                image,
                dry_run=dry_run,
                logger=logger,
                interval=interval,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason)
                return ActionResult(state, "stop", reason)
            time.sleep(interval)
            continue

        if state == "gacha_page":
            assert current_target is not None
            if current_target not in switched:
                selected_target = detect_selected_gacha_target(image)
                logger.event(
                    action="detect_selected_gacha_target",
                    expected=current_target,
                    selected=selected_target,
                )
                if selected_target == current_target:
                    switched.add(current_target)
                else:
                    ok, _, _, reason = click_with_fixed_retry(
                        hwnd,
                        image,
                        _target_tab(current_target),
                        verify=lambda next_state, next_image: (
                            next_state == "gacha_page"
                            and detect_selected_gacha_target(next_image) == current_target
                        ),
                        description=f"select {current_target} gacha tab",
                        dry_run=dry_run,
                        logger=logger,
                    )
                    if not ok:
                        logger.failure(reason)
                        logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                        return ActionResult(state, "stop", reason)
                    switched.add(current_target)
                continue

            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "all_free",
                verify=lambda next_state, _next_image: next_state == "confirm_free_gacha",
                description=f"open all-free confirmation for {current_target}",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            continue

        if state == "confirm_free_gacha":
            ok, next_state, next_image, reason = click_with_fixed_retry(
                hwnd,
                image,
                "confirm",
                verify=lambda candidate, _next_image: is_free_gacha_confirm_transition(candidate),
                description="confirm free gacha",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            if not dry_run and next_state == "gacha_page":
                next_state, next_image = wait_for_state(
                    hwnd,
                    logger,
                    expected={"gacha_animation", "gacha_result", "loading"},
                    timeout=30.0,
                    interval=interval,
                    label="after-confirm-network-transition",
                )
                if next_state not in {"gacha_animation", "gacha_result", "loading"}:
                    reason = (
                        "free gacha confirmation stayed on gacha_page for 30 seconds; "
                        "no second click was attempted"
                    )
                    logger.failure(reason)
                    logger.event(
                        action="stop",
                        result="error",
                        reason=reason,
                        screenshot=str(image_path),
                    )
                    return ActionResult(next_state, "stop", reason)
            continue

        if state == "gacha_animation":
            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "skip_animation",
                verify=lambda next_state, _next_image: next_state in {"gacha_result", "loading"},
                description="skip gacha animation",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            continue

        if state == "gacha_result":
            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "result_back",
                verify=lambda next_state, _next_image: next_state in {"gacha_page", "loading"},
                description="return from gacha result",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            result_back_target = current_target
            continue

        reason = f"unknown or unsupported state: {state}"
        logger.failure(reason)
        logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
        return ActionResult(state, "stop", reason)

    reason = f"timeout after {timeout:.0f}s"
    logger.failure(reason)
    logger.event(action="stop", result="error", reason=reason)
    return ActionResult("timeout", "stop", reason)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BrownDust II free gacha automation.")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true", help="classify and log without clicking")
    parser.add_argument("--test-mode", action="store_true", help="save annotated screenshots before every click")
    parser.add_argument("--targets", nargs="+", default=["costume", "gear"], choices=sorted(TARGET_LABELS))
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = Path(args.log_root) if args.log_root else Path.cwd() / "logs" / "free_gacha" / stamp
    result = run_free_gacha(
        targets=args.targets,
        timeout=args.timeout,
        interval=args.interval,
        dry_run=args.dry_run,
        test_mode=args.test_mode,
        log_root=log_root,
    )
    print(f"state={result.state}")
    print(f"action={result.action}")
    print(f"reason={result.reason}")
    print(f"log_root={log_root}")
    if result.reason != "all requested free gacha targets completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
