from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image

from maa.controller import Win32Controller
from maa.define import (
    LoggingLevelEnum,
    MaaWin32InputMethodEnum,
    MaaWin32ScreencapMethodEnum,
)
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit


ROOT = Path(__file__).resolve().parents[1]
DEBUG_DIR = ROOT / "install" / "debug"
HARNESS_DIR = DEBUG_DIR / "harness"


def find_game_window():
    windows = [
        window
        for window in Toolkit.find_desktop_windows()
        if window.class_name == "UnityWndClass" and "BrownDust II" in window.window_name
    ]
    if not windows:
        raise RuntimeError("BrownDust II UnityWndClass window was not found.")
    return windows[0]


def create_controller(input_method: str) -> Win32Controller:
    window = find_game_window()
    method = getattr(MaaWin32InputMethodEnum, input_method)
    controller = Win32Controller(
        window.hwnd,
        int(MaaWin32ScreencapMethodEnum.FramePool),
        int(method),
        int(method),
    )
    if not controller.post_connection().wait().status.succeeded:
        raise RuntimeError("failed to connect Win32 controller")
    controller.set_screenshot_target_short_side(720)
    return controller


def save_screenshot(controller: Win32Controller, name: str) -> Path:
    HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    image = controller.post_screencap().wait().get()
    path = HARNESS_DIR / name
    # MAA exposes BGR images to Python; convert to RGB for human-facing files.
    Image.fromarray(image[:, :, ::-1]).save(path)
    return path


def load_resource() -> Resource:
    resource = Resource()
    for bundle in ("base", "pc"):
        job = resource.post_bundle(ROOT / "assets" / "resource" / bundle).wait()
        if not job.status.succeeded:
            raise RuntimeError(f"failed to load resource bundle: {bundle}")
    return resource


def run_task(controller: Win32Controller, entry: str, timeout: int) -> dict:
    resource = load_resource()
    tasker = Tasker()
    if not tasker.bind(resource, controller):
        raise RuntimeError("failed to bind tasker")
    if not tasker.inited:
        raise RuntimeError("tasker is not initialized")

    job = tasker.post_task(entry)
    started = time.monotonic()
    while not job.done and time.monotonic() - started < timeout:
        time.sleep(0.5)

    if not job.done:
        tasker.post_stop().wait()

    detail = job.get()
    nodes = []
    if detail:
        for node in detail.nodes:
            nodes.append(
                {
                    "name": node.name,
                    "completed": node.completed,
                    "recognition": (
                        {
                            "name": node.recognition.name,
                            "algorithm": str(node.recognition.algorithm),
                            "hit": node.recognition.hit,
                            "raw_detail": node.recognition.raw_detail,
                        }
                        if node.recognition
                        else None
                    ),
                    "action": (
                        {
                            "action": str(node.action.action),
                            "success": node.action.success,
                            "raw_detail": node.action.raw_detail,
                        }
                        if node.action
                        else None
                    ),
                }
            )
    return {
        "entry": entry,
        "done": job.done,
        "succeeded": job.succeeded,
        "timed_out": not job.done,
        "nodes": nodes,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="PC QuickHunt runtime harness")
    parser.add_argument("--input", default="PostMessageWithCursorPos")
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument("--run-task", action="store_true")
    parser.add_argument("--entry", default="QuickHunt_Start")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    Tasker.set_log_dir(DEBUG_DIR)
    Tasker.set_debug_mode(True)
    Tasker.set_save_draw(True)
    Tasker.set_save_on_error(True)
    Tasker.set_stdout_level(LoggingLevelEnum.Info)

    controller = create_controller(args.input)
    before = save_screenshot(controller, "pc_quickhunt_before.png")
    result = {"before": str(before)}

    if args.run_task:
        result["task"] = run_task(controller, args.entry, args.timeout)
        after = save_screenshot(controller, "pc_quickhunt_after.png")
        result["after"] = str(after)
    elif args.screenshot:
        result["resolution"] = controller.resolution

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
