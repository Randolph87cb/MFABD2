"""Recorded daily arena automation steps."""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

from free_gacha import RunLogger, _click_ratio, classify_state, safe_capture_client
from open_game import find_game_window


def enter_cartridge(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="daily_arena_enter_cartridge", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    before = safe_capture_client(hwnd, logger=logger)
    state, details = classify_state(before)
    before_path = logger.save_image(before, f"step-001-before-{state}.png")
    logger.event(action="classify", state=state, details=details, screenshot=str(before_path))
    if state != "real_home":
        reason = f"arena cartridge entry requires real_home; current state={state}"
        logger.failure(reason)
        return False, reason

    _click_ratio(hwnd, before, "home_cartridge", dry_run=dry_run, logger=logger)
    if dry_run:
        reason = "dry-run planned home cartridge click"
        logger.event(action="stop", result="success", reason=reason)
        return True, reason

    time.sleep(6.0)
    after = safe_capture_client(hwnd, logger=logger)
    next_state, next_details = classify_state(after)
    after_path = logger.save_image(after, f"step-002-after-{next_state}.png")
    logger.event(
        action="capture_after_click",
        state=next_state,
        details=next_details,
        screenshot=str(after_path),
    )
    reason = f"home cartridge clicked; captured resulting state={next_state}"
    logger.event(action="stop", result="success", reason=reason)
    return True, reason


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one recorded daily arena step.")
    parser.add_argument("--step", choices=("entry",), default="entry")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = Path(args.log_root) if args.log_root else Path.cwd() / "logs" / "daily_arena" / stamp
    ok, reason = enter_cartridge(dry_run=args.dry_run, log_root=log_root)
    print(f"ok={ok}")
    print(f"reason={reason}")
    print(f"log_root={log_root}")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
