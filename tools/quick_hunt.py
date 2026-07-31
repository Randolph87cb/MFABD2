"""Record and verify the first step of the quick-hunt flow."""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from free_gacha import (
    RunLogger,
    _mean_region_difference,
    _roi,
    _stats,
    classify_state,
    click_with_fixed_retry,
    safe_capture_client,
)
from open_game import find_game_window


QUICK_HUNT_CATEGORY_REGIONS = {
    "hunting_ground": (0.05, 0.10, 0.10, 0.13),
    "gold": (0.05, 0.21, 0.10, 0.12),
    "slime": (0.05, 0.30, 0.10, 0.12),
    "crystal_cave": (0.05, 0.40, 0.10, 0.12),
}


def detect_selected_quick_hunt_category(image: Image.Image) -> tuple[str | None, dict[str, float]]:
    """Detect the highlighted left-side category using its fixed row brightness."""
    frame = np.asarray(image.convert("RGB"))
    scores = {
        name: _stats(_roi(frame, region))["bright_ratio"]
        for name, region in QUICK_HUNT_CATEGORY_REGIONS.items()
    }
    selected = max(scores, key=scores.get)
    ordered = sorted(scores.values(), reverse=True)
    if scores[selected] < 0.015 or scores[selected] - ordered[1] < 0.008:
        return None, scores
    return selected, scores


def enter_quick_hunt(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="quick_hunt_entry", dry_run=dry_run)

    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    before = safe_capture_client(hwnd, logger=logger)
    before_state, before_details = classify_state(before)
    before_path = logger.save_image(before, f"before-{before_state}.png")
    logger.event(
        action="classify_before_entry",
        state=before_state,
        screenshot=str(before_path),
        details=before_details,
    )
    if before_state != "real_home":
        reason = f"quick-hunt entry requires real_home, got {before_state}"
        logger.failure(reason)
        return False, reason

    ok, state, after, reason = click_with_fixed_retry(
        hwnd,
        before,
        "quick_hunt",
        verify=lambda next_state, next_image: (
            next_state in {"quick_hunt_map", "loading"}
            and _mean_region_difference(before, next_image, (0.0, 0.0, 1.0, 1.0)) >= 8.0
        ),
        description="open quick-hunt map",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    if dry_run:
        return True, reason

    if state == "loading":
        time.sleep(6.0)
        after = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(after)
        logger.event(action="classify_after_loading", state=state, details=details)

    after_path = logger.save_image(after, f"after-entry-{state}.png")
    if state != "quick_hunt_map":
        reason = f"quick-hunt entry ended at unexpected state: {state}"
        logger.event(
            action="stop",
            result="error",
            state=state,
            reason=reason,
            screenshot=str(after_path),
        )
        logger.failure(reason)
        return False, reason

    logger.event(
        action="stop",
        result="success",
        state=state,
        reason=reason,
        screenshot=str(after_path),
    )
    return True, f"{reason}; resulting state={state}"


def start_selected_quick_hunt(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="quick_hunt_start", dry_run=dry_run)

    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    before = safe_capture_client(hwnd, logger=logger)
    before_state, before_details = classify_state(before)
    before_path = logger.save_image(before, f"before-{before_state}.png")
    logger.event(
        action="classify_before_start",
        state=before_state,
        screenshot=str(before_path),
        details=before_details,
    )
    if before_state != "quick_hunt_map":
        reason = f"quick-hunt start requires quick_hunt_map, got {before_state}"
        logger.failure(reason)
        return False, reason

    category, category_scores = detect_selected_quick_hunt_category(before)
    logger.event(
        action="detect_selected_category",
        selected=category,
        expected="hunting_ground",
        scores=category_scores,
    )
    if category != "hunting_ground":
        reason = f"hunting ground is not confirmed as selected: selected={category}"
        logger.failure(reason)
        return False, reason

    ok, state, after, reason = click_with_fixed_retry(
        hwnd,
        before,
        "quick_hunt_start",
        verify=lambda next_state, next_image: (
            next_state != "quick_hunt_map"
            and _mean_region_difference(before, next_image, (0.0, 0.0, 1.0, 1.0)) >= 8.0
        ),
        description="start selected quick hunt",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    if dry_run:
        return True, reason

    if state == "loading":
        time.sleep(6.0)
        after = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(after)
        logger.event(action="classify_after_loading", state=state, details=details)

    after_path = logger.save_image(after, f"after-start-{state}.png")
    logger.event(
        action="stop",
        result="success",
        state=state,
        reason=reason,
        screenshot=str(after_path),
    )
    return True, f"{reason}; resulting state={state}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Test one recorded quick-hunt step.")
    parser.add_argument("--step", choices=("entry", "start"), default="entry")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = Path(args.log_root) if args.log_root else Path.cwd() / "logs" / "quick_hunt" / stamp
    if args.step == "entry":
        ok, reason = enter_quick_hunt(dry_run=args.dry_run, log_root=log_root)
    else:
        ok, reason = start_selected_quick_hunt(dry_run=args.dry_run, log_root=log_root)
    print(f"ok={ok}")
    print(f"reason={reason}")
    print(f"log_root={log_root}")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
