from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from maa.controller import CustomController
from maa.pipeline import JAnd, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "pc_collect"
MANIFEST = FIXTURE_DIR / "manifest.json"


class FixtureController(CustomController):
    def __init__(self, image: np.ndarray):
        self.image = image
        super().__init__()

    def connect(self) -> bool:
        return True

    def connected(self) -> bool:
        return True

    def request_uuid(self) -> str:
        return "pc-collect-fixture"

    def get_features(self) -> int:
        return 0

    def start_app(self, intent: str) -> bool:
        return True

    def stop_app(self, intent: str) -> bool:
        return True

    def screencap(self) -> np.ndarray:
        return self.image

    def click(self, x: int, y: int) -> bool:
        return True

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
        return True

    def touch_down(self, contact: int, x: int, y: int, pressure: int) -> bool:
        return True

    def touch_move(self, contact: int, x: int, y: int, pressure: int) -> bool:
        return True

    def touch_up(self, contact: int) -> bool:
        return True

    def click_key(self, keycode: int) -> bool:
        return True

    def input_text(self, text: str) -> bool:
        return True

    def key_down(self, keycode: int) -> bool:
        return True

    def key_up(self, keycode: int) -> bool:
        return True

    def scroll(self, dx: int, dy: int) -> bool:
        return True


def load_image(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"))


def create_tasker(image: np.ndarray) -> Tasker:
    resource = Resource()
    for bundle in ("base", "pc"):
        job = resource.post_bundle(ROOT / "assets" / "resource" / bundle).wait()
        if not job.status.succeeded:
            raise RuntimeError(f"failed to load resource bundle: {bundle}")

    controller = FixtureController(image)
    if not controller.post_connection().wait().status.succeeded:
        raise RuntimeError("failed to connect fixture controller")

    tasker = Tasker()
    if not tasker.bind(resource, controller):
        raise RuntimeError("failed to bind resource and fixture controller")
    if not tasker.inited:
        raise RuntimeError("tasker is not initialized")

    return tasker


def recognize(tasker: Tasker, node_name: str, image: np.ndarray) -> tuple[bool, dict[str, Any]]:
    task = tasker.post_recognition(
        JRecognitionType.And,
        JAnd(all_of=[node_name]),
        image,
    ).wait().get()
    node = task.nodes[0]
    if not node.recognition:
        return False, {}
    return bool(node.recognition.hit), node.recognition.raw_detail


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cases = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures: list[str] = []

    checks = {
        "expected_pc_sandplay": "Collect_PC_Sandplay_Stable",
        "expected_skill1_ready": "Collect_PC_Skill1_Ready",
        "expected_home": "Rec_HomePage_PC_Stable",
    }

    for case in cases:
        image_path = FIXTURE_DIR / case["file"]
        image = load_image(image_path)
        tasker = create_tasker(image)

        observed: dict[str, bool] = {}
        details: dict[str, dict[str, Any]] = {}
        for expected_key, node_name in checks.items():
            if expected_key not in case:
                continue
            hit, detail = recognize(tasker, node_name, image)
            observed[expected_key] = hit
            details[expected_key] = detail
            expected = bool(case[expected_key])
            if hit != expected:
                failures.append(
                    f"{case['name']} {node_name}: expected {expected}, got {hit}"
                )

        print(f"{case['name']}: {observed}")
        if case.get("notes"):
            print(f"  notes: {case['notes']}")
        if any(observed[key] != bool(case[key]) for key in observed):
            print(json.dumps(details, ensure_ascii=False, indent=2))

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
