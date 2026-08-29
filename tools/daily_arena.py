"""Recorded daily arena automation steps."""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from adaptive_wait import AdaptivePoll
from free_gacha import (
    RunLogger,
    _click_ratio,
    _mean_region_difference,
    _roi,
    _stats,
    classify_state,
    click_with_fixed_retry,
    safe_capture_client,
    wait_for_state,
)
from game_text_recognition import recognize_arena_auto_battle_labels
from open_game import find_game_window


ARENA_DIALOGUE_STATES = {"unknown", "home_overlay", "blocking_ad_overlay"}


def is_gameplay_tab_selected(image: Image.Image) -> bool:
    frame = np.asarray(image.convert("RGB"))
    selected = _stats(_roi(frame, (0.46, 0.77, 0.13, 0.10)))
    return selected["edge_ratio"] > 0.045 and selected["bright_ratio"] > 0.025


def enter_battlefield(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_enter_battlefield", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    image_path = logger.save_image(image, f"step-001-before-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(image_path))
    if state == "arena_lobby":
        reason = "already in arena lobby"
        logger.event(action="stop", result="success", state=state, reason=reason)
        return True, reason
    if state != "real_home":
        reason = f"return-battlefield entry requires real_home; current state={state}"
        logger.failure(reason)
        return False, reason

    ok, next_state, _next_image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "home_return_battlefield",
        verify=lambda candidate, _image: candidate in {"plaza", "arena_lobby"},
        description="enter battlefield from home",
        dry_run=dry_run,
        logger=logger,
    )
    result = "success" if ok else "error"
    logger.event(action="stop", result=result, state=next_state, reason=reason)
    if not ok:
        logger.failure(reason)
    return ok, reason


def enter_battle_prep(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_enter_battle_prep", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    image_path = logger.save_image(image, f"step-001-before-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(image_path))
    if state != "arena_lobby":
        reason = f"arena pool entry requires arena_lobby; current state={state}"
        logger.failure(reason)
        return False, reason

    ok, next_state, _next_image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "arena_pool",
        verify=lambda candidate, _image: candidate in {"arena_battle_prep", "loading"},
        description="enter arena battle preparation",
        dry_run=dry_run,
        logger=logger,
    )
    if ok and not dry_run and next_state == "loading":
        next_state, _next_image = wait_for_state(
            hwnd,
            logger,
            expected={"arena_battle_prep"},
            timeout=60.0,
            interval=3.0,
            label="after-arena-pool-loading",
        )
        if next_state != "arena_battle_prep":
            ok = False
            reason = "arena pool loading ended without reaching battle preparation"
        else:
            reason = "entered arena battle preparation after loading"
    result = "success" if ok else "error"
    logger.event(action="stop", result=result, state=next_state, reason=reason)
    if not ok:
        logger.failure(reason)
    return ok, reason


def enter_arena_from_plaza(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_cartridge_route", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    image_path = logger.save_image(image, f"step-001-before-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(image_path))
    if state == "arena_lobby":
        return True, "already in arena lobby"
    if state != "plaza":
        reason = f"arena cartridge route requires plaza; current state={state}"
        logger.failure(reason)
        return False, reason

    ok, _state, bar_image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "plaza_cartridge",
        verify=lambda candidate, _image: candidate == "arena_cartridge_bar",
        description="open battlefield cartridge bar",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok or dry_run:
        if not ok:
            logger.failure(reason)
        return ok, reason

    ok, _state, gameplay_image, reason = click_with_fixed_retry(
        hwnd,
        bar_image,
        "cartridge_gameplay_tab",
        verify=lambda candidate, candidate_image: (
            candidate == "arena_cartridge_bar" and is_gameplay_tab_selected(candidate_image)
        ),
        description="select gameplay cartridge category",
        dry_run=False,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason

    _click_ratio(
        hwnd,
        gameplay_image,
        "cartridge_first_gameplay",
        dry_run=False,
        logger=logger,
    )
    dialogue_clicks = 0
    card_attempts = 1
    stall_timeout = 150.0
    last_progress_at = time.monotonic()
    previous_state = "arena_cartridge_bar"
    previous_image = gameplay_image
    poll = AdaptivePoll()
    while time.monotonic() - last_progress_at < stall_timeout:
        time.sleep(poll.next_delay())
        current = safe_capture_client(hwnd, logger=logger)
        current_state, current_details = classify_state(current)
        visual_difference = _mean_region_difference(previous_image, current)
        if current_state != previous_state or visual_difference >= 2.5:
            last_progress_at = time.monotonic()
            poll.reset()
            logger.event(
                action="progress",
                reason=(
                    "recognized_state_changed"
                    if current_state != previous_state
                    else "screen_content_changed"
                ),
                previous_state=previous_state,
                state=current_state,
                visual_difference=round(visual_difference, 3),
                stall_timeout=stall_timeout,
            )
        previous_state = current_state
        previous_image = current
        stamp = datetime.now().strftime("%H%M%S-%f")
        current_path = logger.save_image(current, f"transition-{stamp}-{current_state}.png")
        logger.event(
            action="arena_card_transition",
            state=current_state,
            dialogue_clicks=dialogue_clicks,
            card_attempts=card_attempts,
            details=current_details,
            screenshot=str(current_path),
        )
        if current_state == "arena_lobby":
            reason = f"arena lobby reached; dialogue_clicks={dialogue_clicks}"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason
        if current_state == "loading":
            last_progress_at = time.monotonic()
            continue
        if current_state == "arena_cartridge_bar":
            if card_attempts >= 2:
                reason = "first gameplay cartridge did not open after 2 clicks"
                logger.failure(reason)
                return False, reason
            card_attempts += 1
            _click_ratio(
                hwnd,
                current,
                "cartridge_first_gameplay",
                dry_run=False,
                logger=logger,
                attempt=card_attempts,
            )
            poll.reset()
            continue
        if current_state in ARENA_DIALOGUE_STATES and dialogue_clicks < 12:
            dialogue_clicks += 1
            _click_ratio(
                hwnd,
                current,
                "arena_dialogue_advance",
                dry_run=False,
                logger=logger,
                attempt=dialogue_clicks,
            )
            poll.reset()
            continue

        reason = f"unsupported state after arena cartridge selection: {current_state}"
        logger.failure(reason)
        return False, reason

    reason = "arena cartridge transition made no progress for 150 seconds"
    logger.failure(reason)
    return False, reason


def open_auto_battle(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_open_auto_battle", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    image_path = logger.save_image(image, f"step-001-before-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(image_path))
    if state != "arena_battle_prep":
        reason = f"auto-battle entry requires arena_battle_prep; current state={state}"
        logger.failure(reason)
        return False, reason

    ok, next_state, _next_image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "arena_auto_battle",
        verify=lambda candidate, _image: candidate == "arena_auto_battle_dialog",
        description="open arena auto-battle dialog",
        dry_run=dry_run,
        logger=logger,
    )
    result = "success" if ok else "error"
    logger.event(action="stop", result=result, state=next_state, reason=reason)
    if not ok:
        logger.failure(reason)
    return ok, reason


def _auto_battle_count(image: Image.Image) -> int | None:
    matched, details = recognize_arena_auto_battle_labels(image)
    if not matched:
        return None
    for text in details["texts"]["dialog"]:
        match = re.search(r"自动战斗\s*(\d+)\s*次", text)
        if match:
            return int(match.group(1))
    return None


def maximize_and_start_auto_battle(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_maximize_and_start", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    image_path = logger.save_image(image, f"step-001-before-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(image_path))
    if state != "arena_auto_battle_dialog":
        reason = f"arena auto start requires arena_auto_battle_dialog; current state={state}"
        logger.failure(reason)
        return False, reason

    ok, _next_state, max_image, reason = click_with_fixed_retry(
        hwnd,
        image,
        "arena_auto_max",
        verify=lambda candidate, candidate_image: (
            candidate == "arena_auto_battle_dialog"
            and (_auto_battle_count(candidate_image) or 0) > 1
        ),
        description="maximize arena auto-battle count",
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        logger.event(action="stop", result="error", reason=reason)
        return False, reason

    selected_count = _auto_battle_count(max_image)
    ok, next_state, _next_image, reason = click_with_fixed_retry(
        hwnd,
        max_image,
        "arena_auto_start",
        verify=lambda candidate, _image: candidate != "arena_auto_battle_dialog",
        description="start 10x arena auto battle",
        dry_run=dry_run,
        logger=logger,
    )
    result = "success" if ok else "error"
    final_reason = f"{reason}; selected_count={selected_count}"
    logger.event(action="stop", result=result, state=next_state, reason=final_reason)
    if not ok:
        logger.failure(final_reason)
    return ok, final_reason


def wait_and_close_repeat_result(
    *,
    dry_run: bool,
    log_root: Path,
    timeout: float = 1800.0,
) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_wait_and_close_result", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    deadline = time.monotonic() + timeout
    sample = 0
    poll = AdaptivePoll()
    while time.monotonic() < deadline:
        sample += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(image)
        stamp = datetime.now().strftime("%H%M%S-%f")
        image_path = logger.save_image(image, f"wait-{sample:03d}-{stamp}-{state}.png")
        logger.event(
            action="wait_repeat_result",
            sample=sample,
            state=state,
            details=details,
            screenshot=str(image_path),
        )
        if state != "arena_repeat_battle_result":
            time.sleep(poll.next_delay(remaining=deadline - time.monotonic()))
            continue

        ok, next_state, _next_image, reason = click_with_fixed_retry(
            hwnd,
            image,
            "arena_repeat_result_close",
            verify=lambda candidate, _image: candidate != "arena_repeat_battle_result",
            description="close repeated arena battle result",
            dry_run=dry_run,
            logger=logger,
        )
        result = "success" if ok else "error"
        logger.event(action="stop", result=result, state=next_state, reason=reason)
        if not ok:
            logger.failure(reason)
        return ok, reason

    reason = f"arena repeated-battle result timed out after {timeout:.0f} seconds"
    logger.failure(reason)
    logger.event(action="stop", result="error", reason=reason)
    return False, reason


def leave_arena_victory(
    *,
    dry_run: bool,
    log_root: Path,
    timeout: float = 90.0,
) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_leave_victory", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    deadline = time.monotonic() + timeout
    sample = 0
    poll = AdaptivePoll()
    while time.monotonic() < deadline:
        sample += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(image)
        stamp = datetime.now().strftime("%H%M%S-%f")
        image_path = logger.save_image(image, f"wait-{sample:03d}-{stamp}-{state}.png")
        logger.event(
            action="wait_victory_result",
            sample=sample,
            state=state,
            details=details,
            screenshot=str(image_path),
        )
        if state != "arena_victory_result":
            time.sleep(poll.next_delay(remaining=deadline - time.monotonic()))
            continue

        ok, next_state, _next_image, reason = click_with_fixed_retry(
            hwnd,
            image,
            "arena_victory_leave",
            verify=lambda candidate, _image: candidate != "arena_victory_result",
            description="leave arena victory result",
            dry_run=dry_run,
            logger=logger,
        )
        result = "success" if ok else "error"
        logger.event(action="stop", result=result, state=next_state, reason=reason)
        if not ok:
            logger.failure(reason)
        return ok, reason

    reason = f"arena victory result timed out after {timeout:.0f} seconds"
    logger.failure(reason)
    logger.event(action="stop", result="error", reason=reason)
    return False, reason


def confirm_optional_rank_change(
    *,
    dry_run: bool,
    log_root: Path,
    timeout: float = 120.0,
) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_confirm_rank_change", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    stable_states = {"arena_lobby", "plaza", "real_home", "arena_cartridge_collection"}
    deadline = time.monotonic() + timeout
    sample = 0
    poll = AdaptivePoll()
    while time.monotonic() < deadline:
        sample += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(image)
        stamp = datetime.now().strftime("%H%M%S-%f")
        image_path = logger.save_image(image, f"wait-{sample:03d}-{stamp}-{state}.png")
        logger.event(
            action="wait_rank_change",
            sample=sample,
            state=state,
            details=details,
            screenshot=str(image_path),
        )
        if state in stable_states:
            reason = f"no arena rank-change confirmation; reached {state}"
            logger.event(action="stop", result="success", state=state, reason=reason)
            return True, reason
        if state != "arena_rank_change":
            time.sleep(poll.next_delay(remaining=deadline - time.monotonic()))
            continue

        ok, next_state, _next_image, reason = click_with_fixed_retry(
            hwnd,
            image,
            "arena_rank_confirm",
            verify=lambda candidate, _image: candidate != "arena_rank_change",
            description="confirm arena promotion or demotion",
            dry_run=dry_run,
            logger=logger,
        )
        result = "success" if ok else "error"
        logger.event(action="stop", result=result, state=next_state, reason=reason)
        if not ok:
            logger.failure(reason)
        return ok, reason

    reason = f"arena post-victory state timed out after {timeout:.0f} seconds"
    logger.failure(reason)
    logger.event(action="stop", result="error", reason=reason)
    return False, reason


def run_daily_arena(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    ok, reason = enter_battlefield(dry_run=dry_run, log_root=log_root / "01-battlefield")
    if not ok or dry_run:
        return ok, reason

    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found after entering battlefield"
    state, _details = classify_state(safe_capture_client(hwnd))
    if state == "plaza":
        ok, reason = enter_arena_from_plaza(
            dry_run=False,
            log_root=log_root / "02-cartridge-route",
        )
        if not ok:
            return False, reason
    elif state != "arena_lobby":
        return False, f"unexpected state after entering battlefield: {state}"

    ok, reason = enter_battle_prep(dry_run=False, log_root=log_root / "03-pool")
    if not ok:
        return False, reason
    ok, reason = open_auto_battle(dry_run=False, log_root=log_root / "04-auto-dialog")
    if not ok:
        return False, reason
    ok, reason = maximize_and_start_auto_battle(
        dry_run=False,
        log_root=log_root / "05-auto-start",
    )
    if not ok:
        return False, reason
    ok, reason = wait_and_close_repeat_result(
        dry_run=False,
        log_root=log_root / "06-repeat-result",
    )
    if not ok:
        return False, reason
    ok, reason = leave_arena_victory(
        dry_run=False,
        log_root=log_root / "07-leave",
    )
    if not ok:
        return False, reason
    return confirm_optional_rank_change(
        dry_run=False,
        log_root=log_root / "08-rank-change",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one recorded daily arena step.")
    parser.add_argument(
        "--step",
        choices=(
            "battlefield",
            "cartridge-route",
            "pool",
            "auto-dialog",
            "auto-start",
            "close-result",
            "leave",
            "confirm-rank",
            "full",
        ),
        default="battlefield",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = Path(args.log_root) if args.log_root else Path.cwd() / "logs" / "daily_arena" / stamp
    if args.step == "battlefield":
        ok, reason = enter_battlefield(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "cartridge-route":
        ok, reason = enter_arena_from_plaza(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "pool":
        ok, reason = enter_battle_prep(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "auto-dialog":
        ok, reason = open_auto_battle(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "auto-start":
        ok, reason = maximize_and_start_auto_battle(dry_run=args.dry_run, log_root=log_root)
    elif args.step == "close-result":
        ok, reason = wait_and_close_repeat_result(
            dry_run=args.dry_run,
            log_root=log_root,
        )
    elif args.step == "leave":
        ok, reason = leave_arena_victory(
            dry_run=args.dry_run,
            log_root=log_root,
        )
    elif args.step == "confirm-rank":
        ok, reason = confirm_optional_rank_change(
            dry_run=args.dry_run,
            log_root=log_root,
        )
    else:
        ok, reason = run_daily_arena(dry_run=args.dry_run, log_root=log_root)
    print(f"ok={ok}")
    print(f"reason={reason}")
    print(f"log_root={log_root}")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
