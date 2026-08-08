"""Collect accumulated business-management rewards from the home screen."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from free_gacha import (
    RunLogger,
    classify_state,
    click_with_fixed_retry,
    safe_capture_client,
    wait_for_state,
)
from open_game import find_game_window


def _wait_out_loading(
    hwnd: int,
    state: str,
    image: Image.Image,
    *,
    expected: set[str],
    logger: RunLogger,
    label: str,
) -> tuple[str, Image.Image]:
    if state != "loading":
        return state, image
    return wait_for_state(
        hwnd,
        logger,
        expected=expected,
        timeout=20.0,
        interval=2.0,
        label=label,
    )


def open_business_management(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="business_management_entry", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    path = logger.save_image(image, f"entry-start-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(path))
    if state != "real_home":
        reason = f"business-management entry requires real_home, got {state}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "home_business_management",
        verify=lambda next_state, _image: next_state in {"business_management_dialog", "loading"},
        description="open business-management dialog",
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
        expected={"business_management_dialog"},
        logger=logger,
        label="after-business-management-open",
    )
    if state != "business_management_dialog":
        reason = f"business-management entry ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason
    logger.event(action="stop", result="success", state=state)
    return True, "business-management dialog opened"


def claim_business_management_rewards(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="business_management_claim", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    path = logger.save_image(image, f"claim-start-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(path))
    if state != "business_management_dialog":
        reason = f"claiming business-management rewards requires dialog, got {state}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "business_management_claim_all",
        verify=lambda next_state, _image: next_state in {"business_management_reward", "loading"},
        description="claim all business-management rewards",
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
        expected={"business_management_reward"},
        logger=logger,
        label="after-business-management-claim",
    )
    if state != "business_management_reward":
        reason = f"claiming business-management rewards ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason
    logger.event(action="stop", result="success", state=state)
    return True, "business-management rewards claimed"


def dismiss_business_management_reward(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="business_management_reward_dismiss", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    path = logger.save_image(image, f"dismiss-start-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(path))
    if state != "business_management_reward":
        reason = f"dismissing business-management reward requires reward screen, got {state}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "business_management_reward_dismiss",
        verify=lambda next_state, _image: next_state in {"business_management_dialog", "loading"},
        description="dismiss business-management reward",
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
        expected={"business_management_dialog"},
        logger=logger,
        label="after-business-management-reward-dismiss",
    )
    if state != "business_management_dialog":
        reason = f"reward dismissal ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason
    logger.event(action="stop", result="success", state=state)
    return True, "business-management reward dismissed"


def enter_restaurant(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="restaurant_entry", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    path = logger.save_image(image, f"restaurant-start-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(path))
    if state != "business_management_dialog":
        reason = f"entering restaurant requires business-management dialog, got {state}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "business_management_restaurant",
        verify=lambda next_state, _image: next_state in {"restaurant_loading", "restaurant_home"},
        description="enter restaurant from business management",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    if dry_run:
        return True, reason

    if state == "restaurant_loading":
        state, image = wait_for_state(
            hwnd,
            logger,
            expected={"restaurant_home"},
            timeout=90.0,
            interval=3.0,
            label="restaurant-loading",
        )
    if state != "restaurant_home":
        reason = f"restaurant entry ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason

    final_path = logger.save_image(image, f"restaurant-arrival-{state}.png")
    logger.event(
        action="stop",
        result="success",
        state=state,
        reason="restaurant home reached",
        screenshot=str(final_path),
    )
    return True, "restaurant home reached"


def open_regular_customer_rewards(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="restaurant_regular_customer", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    path = logger.save_image(image, f"regular-customer-start-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(path))
    if state != "restaurant_home":
        reason = f"opening regular-customer rewards requires restaurant_home, got {state}"
        logger.failure(reason)
        return False, reason

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "restaurant_regular_customer",
        verify=lambda next_state, _image: next_state == "restaurant_regular_customer_mode",
        description="open regular-customer rewards",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    if dry_run:
        return True, reason

    if state != "restaurant_regular_customer_mode":
        reason = f"regular-customer entry ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason

    final_path = logger.save_image(image, f"regular-customer-opened-{state}.png")
    logger.event(
        action="stop",
        result="success",
        state=state,
        reason="regular-customer screen opened",
        screenshot=str(final_path),
    )
    return True, "regular-customer mode opened"


def detect_regular_customer_note_notification(image: Image.Image) -> tuple[bool, dict[str, float | int]]:
    """Detect the red notification diamond on the regular-customer notebook."""
    frame = np.asarray(image.convert("RGB"))
    height, width = frame.shape[:2]
    x0, x1 = int(width * 0.075), int(width * 0.115)
    y0, y1 = int(height * 0.075), int(height * 0.155)
    crop = frame[y0:y1, x0:x1]
    red = crop[:, :, 0].astype(np.int16)
    green = crop[:, :, 1].astype(np.int16)
    blue = crop[:, :, 2].astype(np.int16)
    red_mask = (
        (red > 150)
        & (red > green * 1.35)
        & (red > blue * 1.20)
        & ((red - green) > 45)
    )
    red_pixels = int(red_mask.sum())
    red_ratio = float(red_mask.mean()) if red_mask.size else 0.0
    return red_pixels >= 80, {
        "red_pixels": red_pixels,
        "red_ratio": red_ratio,
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
    }


def open_regular_customer_note_rewards(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="restaurant_regular_customer_notes", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    path = logger.save_image(image, f"regular-customer-notes-start-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(path))
    if state not in {"restaurant_home", "restaurant_regular_customer_mode"}:
        reason = f"checking regular-customer notes requires restaurant state, got {state}"
        logger.failure(reason)
        return False, reason

    has_notification, notification_details = detect_regular_customer_note_notification(image)
    logger.event(
        action="detect_notification",
        target="regular_customer_notes",
        found=has_notification,
        details=notification_details,
    )
    if not has_notification:
        logger.event(action="stop", result="success", state=state, reason="no reward notification")
        return True, "regular-customer notes have no reward notification"

    ok, state, image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "restaurant_regular_customer_notes",
        verify=lambda next_state, _image: next_state == "restaurant_regular_customer_notes",
        description="open regular-customer note rewards",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    if dry_run:
        return True, reason
    if state != "restaurant_regular_customer_notes":
        reason = f"regular-customer notes ended at unexpected state: {state}"
        logger.failure(reason)
        return False, reason

    final_path = logger.save_image(image, f"regular-customer-notes-opened-{state}.png")
    logger.event(
        action="stop",
        result="success",
        state=state,
        reason="regular-customer note rewards opened",
        screenshot=str(final_path),
    )
    return True, "regular-customer note rewards are ready"


def run_business_management(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    ok, reason = open_business_management(
        dry_run=dry_run,
        log_root=log_root / "01-entry",
    )
    if not ok or dry_run:
        return ok, reason
    ok, reason = claim_business_management_rewards(
        dry_run=False,
        log_root=log_root / "02-claim",
    )
    if not ok:
        return False, reason
    ok, reason = dismiss_business_management_reward(
        dry_run=False,
        log_root=log_root / "03-dismiss-reward",
    )
    if not ok:
        return False, reason
    ok, reason = enter_restaurant(
        dry_run=False,
        log_root=log_root / "04-enter-restaurant",
    )
    if not ok:
        return False, reason
    ok, reason = open_regular_customer_rewards(
        dry_run=False,
        log_root=log_root / "05-regular-customer-rewards",
    )
    if not ok:
        return False, reason
    return open_regular_customer_note_rewards(
        dry_run=False,
        log_root=log_root / "06-regular-customer-notes",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the business-management reward flow.")
    parser.add_argument(
        "--step",
        choices=(
            "all",
            "entry",
            "claim",
            "dismiss",
            "restaurant",
            "regular-customer",
            "regular-customer-notes",
        ),
        default="all",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", type=Path, default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = args.log_root or Path.cwd() / "logs" / "business_management" / stamp
    if args.step == "entry":
        ok, reason = open_business_management(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "claim":
        ok, reason = claim_business_management_rewards(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "dismiss":
        ok, reason = dismiss_business_management_reward(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "restaurant":
        ok, reason = enter_restaurant(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "regular-customer":
        ok, reason = open_regular_customer_rewards(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "regular-customer-notes":
        ok, reason = open_regular_customer_note_rewards(dry_run=args.dry_run, log_root=log_root)
    else:
        ok, reason = run_business_management(dry_run=args.dry_run, log_root=log_root)
    print(f"ok={ok}")
    print(f"reason={reason}")
    print(f"log_root={log_root}")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
