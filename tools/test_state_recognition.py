"""Run screenshot-based regression cases for the free-gacha state classifier."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from free_gacha import classify_state


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    manifest_path = project_root / "tests" / "recognition_cases.json"
    cases = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []

    for case in cases:
        image_path = (manifest_path.parent / case["path"]).resolve()
        if not image_path.is_file():
            failures.append(f"MISSING {image_path}")
            continue
        with Image.open(image_path) as image:
            actual, _ = classify_state(image)
        status = "PASS" if actual == case["expected"] else "FAIL"
        print(f"{status} {image_path.name}: expected={case['expected']} actual={actual}")
        if status == "FAIL":
            failures.append(
                f"{image_path.name}: expected={case['expected']} actual={actual}; {case['reason']}"
            )

    if failures:
        raise SystemExit("\n".join(failures))

    print(f"all {len(cases)} recognition cases passed")


if __name__ == "__main__":
    main()
