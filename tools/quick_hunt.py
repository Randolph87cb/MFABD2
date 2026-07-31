"""Record and verify the first step of the quick-hunt flow."""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

from free_gacha import (
    RunLogger,
    _mean_region_difference,
    classify_state,
    click_with_fixed_retry,
    safe_capture_client,
)
from open_game import find_game_window


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Test the quick-hunt entry click.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = Path(args.log_root) if args.log_root else Path.cwd() / "logs" / "quick_hunt" / stamp
    ok, reason = enter_quick_hunt(dry_run=args.dry_run, log_root=log_root)
    print(f"ok={ok}")
    print(f"reason={reason}")
    print(f"log_root={log_root}")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
