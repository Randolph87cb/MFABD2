"""Probe returning from plaza to the real home screen.

This probe does not enter the gacha flow. It only verifies whether the current
plaza state can be returned to home by background-safe actions:

1. Win32 window-position click on the top-right home icon.
2. Background PostMessage H key.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from free_gacha import (
    RunLogger,
    classify_state,
    return_home_from_plaza,
    safe_capture_client,
)
from open_game import find_game_window


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe plaza -> home return without entering gacha.")
    parser.add_argument("--timeout", type=float, default=30.0, help="reserved for consistency")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-mode", action="store_true", help="save annotated screenshots before every click")
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = Path(args.log_root) if args.log_root else Path.cwd() / "logs" / "return_home" / stamp
    logger = RunLogger(log_root, annotate_clicks=args.test_mode)
    logger.event(action="start", dry_run=args.dry_run, test_mode=args.test_mode, timeout=args.timeout)

    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        print(f"result=error reason={reason}")
        print(f"log_root={log_root}")
        raise SystemExit(2)

    image = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(image)
    image_path = logger.save_image(image, f"initial-{state}.png")
    logger.event(action="initial_state", state=state, screenshot=str(image_path), details=details)

    if state in {"real_home", "home_overlay"}:
        reason = f"already home-compatible state: {state}"
        logger.event(action="stop", result="success", reason=reason)
        print(f"result=success state={state} reason={reason}")
        print(f"log_root={log_root}")
        return

    if state != "plaza":
        reason = f"expected plaza before probe, got {state}"
        logger.failure(reason)
        logger.event(action="stop", result="error", reason=reason)
        print(f"result=error state={state} reason={reason}")
        print(f"log_root={log_root}")
        raise SystemExit(2)

    ok, reason = return_home_from_plaza(
        hwnd,
        image,
        dry_run=args.dry_run,
        logger=logger,
        interval=args.interval,
    )
    logger.event(action="stop", result="success" if ok else "error", reason=reason)
    print(f"result={'success' if ok else 'error'} reason={reason}")
    print(f"log_root={log_root}")
    if not ok:
        logger.failure(reason)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
