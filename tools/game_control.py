"""Local CLI control for the BrownDust II window, without a desktop-app runtime."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from PIL import Image, ImageDraw, ImageStat

from daily_processes import (
    close_new_exact_processes,
    identity_is_current,
    post_close,
    process_identity,
    snapshot_run_helpers,
    window_process_id,
)
from enter_game import capture_client
from open_game import STARTER, WINDOW_CLASS, WINDOW_TITLE
from win32_windowpos_click import (
    EnumWindowsProc,
    _get_class_name,
    _get_window_text,
    click_client,
    get_client_size,
    swipe_client,
    swipe_client_foreground,
    user32,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_OBSERVATION_AGE = 300
KEYS = {"ESC": 0x1B, "ENTER": 0x0D, "SPACE": 0x20, "TAB": 0x09}
KEYS.update({letter: ord(letter) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})


def game_window() -> dict[str, Any] | None:
    """Require one visible game window with a verified owning executable."""
    windows: list[dict[str, Any]] = []

    def visit(hwnd: int, _data: int) -> bool:
        if (user32.IsWindowVisible(hwnd) and _get_class_name(hwnd) == WINDOW_CLASS
                and _get_window_text(hwnd) == WINDOW_TITLE):
            pid = window_process_id(hwnd)
            identity = process_identity(pid) if pid else None
            if identity is None or Path(identity.executable).name.casefold() != "browndust ii.exe":
                raise_errors.append(f"Cannot verify game process for window {hwnd}")
            else:
                windows.append({"hwnd": int(hwnd), "process": asdict(identity),
                                "client_size": list(get_client_size(hwnd))})
        return True

    # Exceptions must not escape a ctypes callback (ctypes otherwise ignores them).
    raise_errors: list[str] = []

    def safe_visit(hwnd: int, data: int) -> bool:
        try:
            return visit(hwnd, data)
        except Exception as exc:
            raise_errors.append(str(exc))
            return False

    enumerated = user32.EnumWindows(EnumWindowsProc(safe_visit), 0)
    if raise_errors:
        raise RuntimeError("; ".join(raise_errors))
    if not enumerated:
        raise ctypes.WinError()
    if len(windows) > 1:
        raise RuntimeError("Multiple BrownDust II windows found; select one game instance first")
    return windows[0] if windows else None


def require_window() -> dict[str, Any]:
    window = game_window()
    if window is None:
        raise RuntimeError("Game window not found; use the open command first")
    return window


def start_game(timeout: float) -> dict[str, Any]:
    existing = game_window()
    if existing is not None:
        return existing
    before = snapshot_run_helpers()
    try:
        result = subprocess.run(
            # open_game only uses the standard library. Bypass the Windows venv
            # redirector so timeout kills the worker, not just its launcher.
            [getattr(sys, "_base_executable", None) or sys.executable,
             str(PROJECT_ROOT / "tools" / "open_game.py"), "--timeout", str(timeout)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        if result.returncode:
            raise RuntimeError(f"Game startup failed ({result.returncode}): {result.stderr or result.stdout}")
        return require_window()
    except subprocess.TimeoutExpired as exc:
        # The game may have appeared just before the worker's deadline.
        window = game_window()
        if window is not None:
            return window
        cleanup = close_new_exact_processes(STARTER, before)
        raise RuntimeError(
            f"Game window did not appear within {timeout:g}s; new launcher cleanup: {cleanup}"
        ) from exc
    except Exception:
        close_new_exact_processes(STARTER, before)
        raise


def capture(window: dict[str, Any], run_root: Path) -> dict[str, Any]:
    hwnd = window["hwnd"]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE, restore only this game.
        time.sleep(0.3)
    window = require_window()
    if window["hwnd"] != hwnd:
        raise RuntimeError("Game window changed before capture")
    image = capture_client(hwnd, run_root / "window.png")
    if list(image.size) != window["client_size"] or min(image.size) < 1:
        raise RuntimeError("Client size changed during capture; capture again")
    if max(ImageStat.Stat(image).stddev) < 1.0:
        raise RuntimeError("Blank game capture; check the active Windows session and game visibility")
    if require_window() != window:
        raise RuntimeError("Game window or process changed during capture")
    image_path = run_root / "client.png"
    image.save(image_path)
    observation = {
        "window": window,
        "captured_at": time.time(),
        "image": str(image_path.resolve()),
        "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
    }
    path = run_root / "observation.json"
    path.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**observation, "observation": str(path.resolve())}


def validate_observation(path: Path, window: dict[str, Any]) -> dict[str, Any]:
    observation = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - observation["captured_at"]
    if not 0 <= age <= MAX_OBSERVATION_AGE:
        raise ValueError("Observation expired; capture and inspect a fresh client image")
    if observation["window"] != window:
        raise ValueError("Game window, process or client size changed; capture again")
    image = Path(observation["image"])
    if hashlib.sha256(image.read_bytes()).hexdigest() != observation["sha256"]:
        raise ValueError("Observation image changed; capture again")
    if path.with_suffix(".used").exists():
        raise ValueError("Observation already used; inspect the screenshot returned by the last action")
    return observation


def post_key(hwnd: int, key: str) -> None:
    vk = KEYS[key]
    scan = user32.MapVirtualKeyW(vk, 0)
    if not user32.PostMessageW(hwnd, 0x0100, vk, 1 | (scan << 16)):
        raise ctypes.WinError()
    try:
        time.sleep(0.08)
    finally:
        if not user32.PostMessageW(hwnd, 0x0101, vk, 1 | (scan << 16) | (3 << 30)):
            raise ctypes.WinError()


def execute(args: argparse.Namespace, run_root: Path) -> dict[str, Any]:
    if args.command == "status":
        return {"backend": "local-win32", "window": game_window(),
                "desktop_app_required": False, "python": sys.executable}
    if args.command == "open":
        return {"window": start_game(args.timeout)}
    if args.command == "close":
        window = game_window()
        if window is None:
            return {"action": "not_running"}
        identity = process_identity(window["process"]["pid"])
        if identity is None or asdict(identity) != window["process"] or not identity_is_current(identity):
            raise RuntimeError("Game process changed before close")
        if not post_close(window["hwnd"]):
            raise ctypes.WinError()
        try:
            psutil.Process(identity.pid).wait(timeout=args.timeout)
        except psutil.NoSuchProcess:
            pass
        return {"action": "closed", "window": window}
    window = require_window()
    if args.command == "capture":
        return capture(window, run_root)

    observation = validate_observation(args.observation, window)
    width, height = window["client_size"]
    points = []
    if args.command == "click":
        points = [(args.x, args.y)]
    elif args.command == "swipe":
        points = [(args.x, args.y), (args.end_x, args.end_y)]
    for x, y in points:
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"Client coordinate ({x}, {y}) outside {width}x{height}")
    with Image.open(observation["image"]) as image:
        marked = image.convert("RGB")
    draw = ImageDraw.Draw(marked)
    for x, y in points:
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline="red", width=3)
    if len(points) == 2:
        draw.line(points, fill="red", width=3)
    draw.text((10, 10), args.command + (" " + args.key if args.command == "key" else ""), fill="red")
    marked.save(run_root / "action.png")
    # Recheck immediately before input, then atomically consume the observation.
    if require_window() != window:
        raise RuntimeError("Game identity changed before input")
    with args.observation.with_suffix(".used").open("x", encoding="utf-8") as stream:
        stream.write(str(run_root.resolve()))
    input_path = run_root / "input.json"
    input_record = {"command": args.command, "window": window, "status": "dispatching"}
    input_path.write_text(json.dumps(input_record, indent=2), encoding="utf-8")
    hwnd = window["hwnd"]
    if args.command == "click":
        click_client(hwnd, args.x, args.y)
    elif args.command == "swipe":
        swipe = swipe_client_foreground if args.foreground else swipe_client
        swipe(hwnd, args.x, args.y, args.end_x, args.end_y, duration=args.duration)
    elif args.command == "key":
        post_key(hwnd, args.key)
    input_record["status"] = "sent"
    input_path.write_text(json.dumps(input_record, indent=2), encoding="utf-8")
    time.sleep(0.5)
    return {"before": str(args.observation.resolve()), "annotation": str((run_root / "action.png").resolve()),
            "after": capture(window, run_root)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-root", type=Path, default=PROJECT_ROOT / "logs" / "manual" / "cli-control")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Inspect backend and game window without starting it")
    for name in ("open", "close"):
        sub = commands.add_parser(name)
        sub.add_argument("--timeout", type=float, default=90 if name == "open" else 15)
    commands.add_parser("capture", help="Save native client pixels and an observation for one input")
    for name in ("click", "swipe", "key"):
        sub = commands.add_parser(name)
        sub.add_argument("--observation", type=Path, required=True)
        if name == "key":
            sub.add_argument("--key", type=str.upper, choices=KEYS, required=True)
        else:
            sub.add_argument("--x", type=int, required=True)
            sub.add_argument("--y", type=int, required=True)
        if name == "swipe":
            sub.add_argument("--end-x", type=int, required=True)
            sub.add_argument("--end-y", type=int, required=True)
            sub.add_argument("--duration", type=float, default=0.5)
            sub.add_argument("--foreground", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    for name in ("timeout", "duration"):
        if hasattr(args, name) and not 0 < getattr(args, name) <= 300:
            raise SystemExit(f"{name} must be greater than zero and at most 300 seconds")
    run_root = args.log_root / datetime.now().strftime("%Y-%m-%d/%H%M%S-%f")
    run_root.mkdir(parents=True, exist_ok=False)
    (run_root / "request.json").write_text(
        json.dumps(vars(args), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    try:
        result = {"ok": True, "command": args.command, **execute(args, run_root)}
    except Exception as exc:
        result = {"ok": False, "command": args.command, "error": f"{type(exc).__name__}: {exc}"}
    result["run_root"] = str(run_root.resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    (run_root / "result.json").write_text(rendered, encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
