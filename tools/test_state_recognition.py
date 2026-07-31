"""Run screenshot-based regression cases for the free-gacha state classifier."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from free_gacha import classify_state, detect_selected_gacha_target


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
            actual_target = detect_selected_gacha_target(image) if "expected_target" in case else None
        status = "PASS" if actual == case["expected"] else "FAIL"
        print(f"{status} {image_path.name}: expected={case['expected']} actual={actual}")
        if status == "FAIL":
            failures.append(
                f"{image_path.name}: expected={case['expected']} actual={actual}; {case['reason']}"
            )
        if "expected_target" in case:
            target_status = "PASS" if actual_target == case["expected_target"] else "FAIL"
            print(
                f"{target_status} {image_path.name}: "
                f"expected_target={case['expected_target']} actual_target={actual_target}"
            )
            if target_status == "FAIL":
                failures.append(
                    f"{image_path.name}: expected_target={case['expected_target']} "
                    f"actual_target={actual_target}; {case['reason']}"
                )

    if failures:
        raise SystemExit("\n".join(failures))

    print(f"all {len(cases)} recognition cases passed")


if __name__ == "__main__":
    main()
