"""Record and verify the first step of the quick-hunt flow."""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from free_gacha import (
    RunLogger,
    _click_ratio,
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


def _quick_hunt_count(details: dict[str, object]) -> int | None:
    setup = details.get("quick_hunt_setup_text")
    if not isinstance(setup, dict):
        return None
    texts = setup.get("texts")
    if not isinstance(texts, dict):
        return None
    body = texts.get("body")
    if not isinstance(body, list):
        return None
    for text in body:
        match = re.search(r"狩猎\s*(\d+)\s*次", str(text))
        if match:
            return int(match.group(1))
    return None


def is_quick_hunt_count_at_max(image: Image.Image) -> tuple[bool, dict[str, float]]:
    """Confirm the count slider handle and filled track are at the right end."""
    frame = np.asarray(image.convert("RGB"))
    gray = (
        frame[:, :, 0].astype(np.float32) * 0.299
        + frame[:, :, 1].astype(np.float32) * 0.587
        + frame[:, :, 2].astype(np.float32) * 0.114
    )
    region = _roi(gray[:, :, None], (0.38, 0.50, 0.24, 0.07))[:, :, 0]
    _bright_y, bright_x = np.where(region > 200)
    if bright_x.size == 0:
        return False, {"bright_ratio": 0.0, "bright_x95": 0.0}
    bright_ratio = float(bright_x.size / region.size)
    bright_x95 = float(0.38 + np.percentile(bright_x, 95) / region.shape[1] * 0.24)
    return bright_ratio > 0.30 and bright_x95 > 0.58, {
        "bright_ratio": bright_ratio,
        "bright_x95": bright_x95,
    }


def _select_max_quick_hunt_count(
    hwnd: int,
    before: Image.Image,
    initial_count: int,
    *,
    dry_run: bool,
    logger: RunLogger,
    delay: float = 6.0,
) -> tuple[bool, str, Image.Image, int | None]:
    current = before
    for attempt in range(1, 3):
        _click_ratio(hwnd, current, "quick_hunt_max", dry_run=dry_run, logger=logger)
        if dry_run:
            return True, "dry-run planned select maximum quick-hunt count", current, initial_count

        time.sleep(delay)
        current = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(current)
        count = _quick_hunt_count(details)
        at_max, max_scores = is_quick_hunt_count_at_max(current)
        verify_path = logger.save_image(
            current,
            f"verify-quick-hunt-max-attempt-{attempt}-{state}-count-{count}.png",
        )
        succeeded = (
            state == "quick_hunt_setup"
            and count is not None
            and count > initial_count
            and at_max
        )
        logger.event(
            action="verify_click",
            key="quick_hunt_max",
            description="select maximum quick-hunt count",
            attempt=attempt,
            state=state,
            initial_count=initial_count,
            count=count,
            at_max=at_max,
            max_scores=max_scores,
            succeeded=succeeded,
            screenshot=str(verify_path),
            details=details,
        )
        if succeeded:
            return True, f"maximum count selected on attempt {attempt}", current, count
        if state != "quick_hunt_setup":
            reason = f"MAX changed to unexpected state {state}; retry cancelled"
            return False, reason, current, count
        if attempt < 2:
            logger.event(
                action="retry_click",
                key="quick_hunt_max",
                description="select maximum quick-hunt count",
                previous_attempts=attempt,
                delay=delay,
            )

    return False, "MAX did not increase the quick-hunt count after 2 clicks", current, None


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

    state, after = _wait_out_loading(
        hwnd,
        state,
        after,
        logger=logger,
        label="after-quick-hunt-entry",
    )

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

    state, after = _wait_out_loading(
        hwnd,
        state,
        after,
        logger=logger,
        label="after-quick-hunt-start",
    )

    after_path = logger.save_image(after, f"after-start-{state}.png")
    if state != "quick_hunt_setup":
        reason = f"quick-hunt start ended at unexpected state: {state}"
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


def maximize_and_confirm_quick_hunt(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="quick_hunt_max_and_confirm", dry_run=dry_run)

    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    before = safe_capture_client(hwnd, logger=logger)
    before_state, before_details = classify_state(before)
    initial_count = _quick_hunt_count(before_details)
    initially_at_max, initial_max_scores = is_quick_hunt_count_at_max(before)
    before_path = logger.save_image(before, f"before-{before_state}-count-{initial_count}.png")
    logger.event(
        action="classify_before_max",
        state=before_state,
        count=initial_count,
        at_max=initially_at_max,
        max_scores=initial_max_scores,
        screenshot=str(before_path),
        details=before_details,
    )
    if before_state != "quick_hunt_setup" or initial_count is None:
        reason = (
            "quick-hunt MAX requires a recognized setup dialog and count, "
            f"got state={before_state}, count={initial_count}"
        )
        logger.failure(reason)
        return False, reason

    if initially_at_max:
        max_reason = f"maximum count already selected: {initial_count}"
        max_image = before
        max_count = initial_count
        logger.event(action="max_already_selected", count=max_count, scores=initial_max_scores)
    else:
        max_ok, max_reason, max_image, max_count = _select_max_quick_hunt_count(
            hwnd,
            before,
            initial_count,
            dry_run=dry_run,
            logger=logger,
        )
        if not max_ok:
            logger.failure(max_reason)
            return False, max_reason
    if dry_run:
        return True, max_reason

    ok, state, after, confirm_reason = click_with_fixed_retry(
        hwnd,
        max_image,
        "quick_hunt_confirm",
        verify=lambda next_state, next_image: (
            next_state != "quick_hunt_setup"
            and _mean_region_difference(max_image, next_image, (0.0, 0.0, 1.0, 1.0)) >= 8.0
        ),
        description=f"confirm quick hunt count {max_count}",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(confirm_reason)
        return False, confirm_reason

    state, after = _wait_out_loading(
        hwnd,
        state,
        after,
        logger=logger,
        label="after-hunting-ground-confirm",
    )

    after_path = logger.save_image(after, f"after-confirm-{state}.png")
    if state != "reward_overlay":
        reason = f"quick-hunt confirmation ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason
    logger.event(
        action="stop",
        result="success",
        state=state,
        max_count=max_count,
        reason=confirm_reason,
        screenshot=str(after_path),
    )
    return True, f"{max_reason}; {confirm_reason}; resulting state={state}"


def _wait_out_loading(
    hwnd: int,
    state: str,
    image: Image.Image,
    *,
    logger: RunLogger,
    label: str,
) -> tuple[str, Image.Image]:
    for attempt in range(1, 6):
        if state != "loading":
            break
        time.sleep(6.0)
        image = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(image)
        path = logger.save_image(image, f"{label}-attempt-{attempt}-{state}.png")
        logger.event(
            action="classify_after_loading",
            label=label,
            attempt=attempt,
            state=state,
            screenshot=str(path),
            details=details,
        )
    return state, image


def run_crystal_cave_cycle(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="quick_hunt_crystal_cave_cycle", dry_run=dry_run)

    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    path = logger.save_image(image, f"cycle-start-{state}.png")
    logger.event(action="classify_cycle_start", state=state, screenshot=str(path), details=details)
    if state not in {"reward_overlay", "quick_hunt_map"}:
        reason = f"crystal-cave cycle requires reward_overlay or quick_hunt_map, got {state}"
        logger.failure(reason)
        return False, reason

    if state == "reward_overlay":
        ok, state, image, reason = click_with_fixed_retry(
            hwnd,
            image,
            "reward_overlay_dismiss",
            verify=lambda next_state, _next_image: next_state in {"quick_hunt_map", "loading"},
            description="dismiss hunting-ground reward",
            dry_run=dry_run,
            logger=logger,
        )
        if not ok or dry_run:
            if not ok:
                logger.failure(reason)
            return ok, reason
        state, image = _wait_out_loading(
            hwnd,
            state,
            image,
            logger=logger,
            label="after-first-result-dismiss",
        )
        if state != "quick_hunt_map":
            reason = f"first reward dismissal ended at unexpected state: {state}"
            logger.failure(reason)
            return False, reason
    else:
        logger.event(action="first_result_already_dismissed", state=state)

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "quick_hunt_crystal_cave",
        verify=lambda next_state, next_image: (
            next_state == "quick_hunt_map"
            and detect_selected_quick_hunt_category(next_image)[0] == "crystal_cave"
        ),
        description="select crystal cave",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "quick_hunt_start",
        verify=lambda next_state, _next_image: next_state in {"quick_hunt_setup", "loading"},
        description="open crystal-cave quick-hunt setup",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    state, image = _wait_out_loading(
        hwnd,
        state,
        image,
        logger=logger,
        label="after-crystal-setup-open",
    )
    if state != "quick_hunt_setup":
        reason = f"crystal-cave setup ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason

    state, setup_details = classify_state(image)
    initial_count = _quick_hunt_count(setup_details)
    if initial_count is None:
        reason = "could not recognize crystal-cave hunt count"
        logger.failure(reason)
        return False, reason
    at_max, max_scores = is_quick_hunt_count_at_max(image)
    logger.event(
        action="detect_crystal_count",
        count=initial_count,
        at_max=at_max,
        max_scores=max_scores,
    )
    if at_max:
        max_image = image
        max_count = initial_count
        max_reason = f"maximum count already selected: {max_count}"
    else:
        max_ok, max_reason, max_image, max_count = _select_max_quick_hunt_count(
            hwnd,
            image,
            initial_count,
            dry_run=dry_run,
            logger=logger,
        )
        if not max_ok:
            logger.failure(max_reason)
            return False, max_reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        max_image,
        "quick_hunt_confirm",
        verify=lambda next_state, _next_image: next_state in {"reward_overlay", "loading"},
        description=f"confirm crystal-cave quick hunt count {max_count}",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    state, image = _wait_out_loading(
        hwnd,
        state,
        image,
        logger=logger,
        label="after-crystal-confirm",
    )
    if state != "reward_overlay":
        reason = f"crystal-cave hunt ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "reward_overlay_dismiss",
        verify=lambda next_state, _next_image: next_state in {"quick_hunt_map", "loading"},
        description="dismiss crystal-cave reward",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    state, image = _wait_out_loading(
        hwnd,
        state,
        image,
        logger=logger,
        label="after-second-result-dismiss",
    )
    if state != "quick_hunt_map":
        reason = f"second reward dismissal ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "quick_hunt_back",
        verify=lambda next_state, _next_image: next_state in {"real_home", "loading"},
        description="return home from quick-hunt map",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    state, image = _wait_out_loading(
        hwnd,
        state,
        image,
        logger=logger,
        label="after-quick-hunt-back",
    )
    final_path = logger.save_image(image, f"cycle-finished-{state}.png")
    if state != "real_home":
        reason = f"quick-hunt cycle did not finish at real_home: {state}"
        logger.failure(reason)
        return False, reason

    reason = f"crystal-cave cycle completed at count {max_count}"
    logger.event(
        action="stop",
        result="success",
        state=state,
        reason=reason,
        screenshot=str(final_path),
    )
    return True, reason


def finish_crystal_cave_cycle(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="finish_crystal_cave_cycle", dry_run=dry_run)

    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    path = logger.save_image(image, f"finish-start-{state}.png")
    logger.event(action="classify_finish_start", state=state, screenshot=str(path), details=details)
    if state != "reward_overlay":
        reason = f"finish cycle requires reward_overlay, got {state}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "reward_overlay_dismiss",
        verify=lambda next_state, _next_image: next_state in {"quick_hunt_map", "loading"},
        description="dismiss crystal-cave reward",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok or dry_run:
        if not ok:
            logger.failure(reason)
        return ok, reason
    state, image = _wait_out_loading(
        hwnd,
        state,
        image,
        logger=logger,
        label="finish-after-result-dismiss",
    )
    if state != "quick_hunt_map":
        reason = f"crystal reward dismissal ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason

    category, scores = detect_selected_quick_hunt_category(image)
    logger.event(action="detect_category_before_back", selected=category, scores=scores)
    if category != "crystal_cave":
        reason = f"expected crystal_cave before returning home, got {category}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "quick_hunt_back",
        verify=lambda next_state, _next_image: next_state in {"real_home", "loading"},
        description="return home from crystal-cave map",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    state, image = _wait_out_loading(
        hwnd,
        state,
        image,
        logger=logger,
        label="finish-after-back",
    )
    final_path = logger.save_image(image, f"finish-complete-{state}.png")
    if state != "real_home":
        reason = f"crystal-cave finish did not reach real_home: {state}"
        logger.failure(reason)
        return False, reason

    reason = "crystal-cave reward dismissed and returned home"
    logger.event(
        action="stop",
        result="success",
        state=state,
        reason=reason,
        screenshot=str(final_path),
    )
    return True, reason


def main() -> None:
    parser = argparse.ArgumentParser(description="Test one recorded quick-hunt step.")
    parser.add_argument(
        "--step",
        choices=("entry", "start", "confirm", "crystal-cycle", "finish-crystal"),
        default="entry",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = Path(args.log_root) if args.log_root else Path.cwd() / "logs" / "quick_hunt" / stamp
    if args.step == "entry":
        ok, reason = enter_quick_hunt(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "start":
        ok, reason = start_selected_quick_hunt(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "confirm":
        ok, reason = maximize_and_confirm_quick_hunt(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "crystal-cycle":
        ok, reason = run_crystal_cave_cycle(dry_run=args.dry_run, log_root=log_root)
    else:
        ok, reason = finish_crystal_cave_cycle(dry_run=args.dry_run, log_root=log_root)
    print(f"ok={ok}")
    print(f"reason={reason}")
    print(f"log_root={log_root}")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
