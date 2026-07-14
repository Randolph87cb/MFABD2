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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from enter_game import capture_client, recognize_home_screen
from open_game import find_game_window
from win32_windowpos_click import click_client


user32 = ctypes.windll.user32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_H = 0x48

CLICK_POINTS = {
    "home_gacha": (0.086, 0.925),
    "plaza_home": (0.935, 0.055),
    "dismiss_overlay": (0.085, 0.120),
    "costume_tab": (0.086, 0.315),
    "gear_tab": (0.086, 0.420),
    "all_free": (0.178, 0.895),
    "confirm": (0.548, 0.598),
    "skip_animation": (0.930, 0.055),
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
    attempts: int = 3,
    delay: float = 0.8,
    min_size: tuple[int, int] = (1000, 600),
) -> Image.Image:
    last_error: Exception | None = None
    min_width, min_height = min_size
    for attempt in range(1, attempts + 1):
        try:
            image = capture_client(hwnd).convert("RGB").copy()
            image.load()
            if image.width < min_width or image.height < min_height:
                raise ValueError(f"invalid client capture size: {image.width}x{image.height}")
            return image
        except Exception as exc:  # noqa: BLE001 - logged and retried by design.
            last_error = exc
            if logger:
                logger.event(action="capture_retry", attempt=attempt, error=repr(exc))
            time.sleep(delay)
    raise RuntimeError(f"failed to capture valid client image after {attempts} attempts: {last_error!r}")


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
    }

    loading_like = full["mean"] < 45 and full["dark_ratio"] > 0.92 and full["edge_ratio"] < 0.005
    if loading_like:
        return "loading", details

    confirm_like = (
        full["dark_ratio"] > 0.45
        and modal["mid_ratio"] > 0.45
        and modal["contrast"] > 20
        and confirm_buttons["bright_ratio"] > 0.15
    )
    if confirm_like:
        return "confirm_free_gacha", details

    bright_scene = full["bright_ratio"] > 0.62 and full["dark_ratio"] < 0.10
    if bright_scene:
        if top_left_back["mean"] < 220 and top_left_back["mid_ratio"] > 0.25:
            return "gacha_result", details
        return "gacha_animation", details

    overlay_like = (
        full["dark_ratio"] > 0.45
        and center["mean"] > full["mean"] + 35
        and center["contrast"] > 35
    )
    if overlay_like:
        return "home_overlay", details

    gacha_like = (
        gacha_button["bright_ratio"] > 0.10
        and gacha_button["contrast"] > 35
        and left_tabs["edge_ratio"] > 0.025
        and top_title["bright_ratio"] > 0.12
    )
    if gacha_like:
        return "gacha_page", details

    plaza_like = (
        plaza_joystick["mid_ratio"] > 0.88
        and plaza_joystick["bright_ratio"] < 0.06
        and plaza_joystick["edge_ratio"] > 0.010
        and plaza_actions["edge_ratio"] > 0.035
        and plaza_actions["contrast"] > 35
        and plaza_top_right["edge_ratio"] > 0.050
    )
    if plaza_like:
        return "plaza", details

    real_home_like = (
        home_bottom_nav["edge_ratio"] > 0.035
        and home_bottom_nav["contrast"] > 35
        and home_right_events["edge_ratio"] > 0.020
        and home_right_events["contrast"] > 30
        and home_top_right["bright_ratio"] > 0.10
        and plaza_joystick["mid_ratio"] < 0.88
    )
    if real_home_like:
        return "real_home", details

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
    waiting_for_confirm = False
    waiting_since = 0.0
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

        if state == "loading":
            logger.event(action="wait_loading", step=step)
            time.sleep(interval)
            continue

        if state == "home_overlay":
            _click_ratio(hwnd, image, "dismiss_overlay", dry_run=dry_run, logger=logger)
            waiting_for_confirm = False
            time.sleep(interval)
            continue

        if state == "real_home":
            _click_ratio(hwnd, image, "home_gacha", dry_run=dry_run, logger=logger)
            waiting_for_confirm = False
            time.sleep(interval)
            continue

        if state == "plaza":
            ok, reason = return_home_from_plaza(
                hwnd,
                image,
                dry_run=dry_run,
                logger=logger,
                interval=interval,
            )
            waiting_for_confirm = False
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason)
                return ActionResult(state, "stop", reason)
            time.sleep(interval)
            continue

        if state == "gacha_page":
            if waiting_for_confirm:
                if time.monotonic() - waiting_since < 6:
                    time.sleep(interval)
                    continue
                reason = f"confirm dialog did not appear after all-free click for {current_target}"
                logger.event(action="stop", result="error", reason=reason)
                return ActionResult(state, "stop", reason)

            assert current_target is not None
            if current_target not in switched:
                _click_ratio(hwnd, image, _target_tab(current_target), dry_run=dry_run, logger=logger)
                switched.add(current_target)
                time.sleep(interval)
                continue

            _click_ratio(hwnd, image, "all_free", dry_run=dry_run, logger=logger)
            waiting_for_confirm = True
            waiting_since = time.monotonic()
            time.sleep(interval)
            continue

        if state == "confirm_free_gacha":
            _click_ratio(hwnd, image, "confirm", dry_run=dry_run, logger=logger)
            waiting_for_confirm = False
            time.sleep(interval)
            continue

        if state == "gacha_animation":
            _click_ratio(hwnd, image, "skip_animation", dry_run=dry_run, logger=logger)
            waiting_for_confirm = False
            time.sleep(interval)
            continue

        if state == "gacha_result":
            _click_ratio(hwnd, image, "result_back", dry_run=dry_run, logger=logger)
            logger.event(action="target_complete", target=current_target)
            target_index += 1
            waiting_for_confirm = False
            time.sleep(interval)
            continue

        reason = f"unknown or unsupported state: {state}"
        logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
        return ActionResult(state, "stop", reason)

    reason = f"timeout after {timeout:.0f}s"
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
