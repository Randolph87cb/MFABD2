"""Run the BrownDust II daily automation once per local calendar day."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sys
import time
import traceback
from ctypes import wintypes
from datetime import date, datetime
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from enter_game import TOUCH_CLICK, recognize_entry_state  # noqa: E402
from free_gacha import (  # noqa: E402
    RunLogger,
    _click_ratio,
    _mean_region_difference,
    classify_state,
    click_with_fixed_retry,
    run_free_gacha,
    safe_capture_client,
)
from game_text_recognition import (  # noqa: E402
    recognize_entry_status,
    recognize_return_home_control,
)
from open_game import find_game_window, open_game  # noqa: E402
from quick_hunt import (  # noqa: E402
    enter_quick_hunt,
    maximize_and_confirm_quick_hunt,
    run_crystal_cave_cycle,
    start_selected_quick_hunt,
)
from win32_windowpos_click import click_client  # noqa: E402
from daily_arena import run_daily_arena  # noqa: E402


TASK_NAME = "BrownDust2DailyAutomation"
MUTEX_NAME = r"Local\BrownDust2DailyAutomation"
ERROR_ALREADY_EXISTS = 183
NETWORK_ENDPOINTS = (
    ("www.baidu.com", 443),
    ("github.com", 443),
)
DOWNLOAD_CONFIRM_CLICK = (0.548, 0.598)
DAILY_READY_STATES = {
    "real_home",
    "home_overlay",
    "blocking_ad_overlay",
    "plaza",
    "gacha_page",
    "confirm_free_gacha",
    "gacha_animation",
    "gacha_result",
    "gacha_item_overlay",
}
ENTRY_WAITING_STATES = {
    "download_waiting",
    "loading_title",
    "startup_waiting",
}


class DailyRunError(RuntimeError):
    """A fatal daily-flow error that has already been described for the log."""


class SingleInstance:
    """Prevent concurrent scheduled and manually forced runs."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self.handle: int | None = None

    def __enter__(self) -> "SingleInstance":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise DailyRunError("another daily automation process is already running")
        self.handle = int(handle)
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self.handle:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(self.handle)
            self.handle = None


class MasterLogger:
    """Write both human-readable and structured run logs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.text_path = root / "run.log"
        self.events_path = root / "events.jsonl"
        self.summary_path = root / "summary.json"

    def event(self, stage: str, status: str, message: str, **details: Any) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        payload = {
            "time": now,
            "stage": stage,
            "status": status,
            "message": message,
            **details,
        }
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        with self.text_path.open("a", encoding="utf-8") as file:
            file.write(f"[{now}] [{status.upper()}] [{stage}] {message}\n")

    def summary(self, **payload: Any) -> None:
        self.summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyRunError(f"could not read daily state: {exc}") from exc
    if not isinstance(data, dict):
        raise DailyRunError("daily state is not a JSON object")
    return data


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def claim_daily_run(
    state_path: Path,
    *,
    run_date: str,
    run_root: Path,
    force: bool,
    started_at: str,
) -> tuple[bool, dict[str, Any]]:
    """Atomically record that today's one allowed run has started."""
    previous = _read_json(state_path)
    if not force and previous.get("last_started_date") == run_date:
        return False, previous

    current = {
        "last_started_date": run_date,
        "started_at": started_at,
        "completed_at": None,
        "status": "started",
        "run_root": str(run_root),
        "error": None,
    }
    _write_json_atomic(state_path, current)
    return True, current


def update_daily_state(state_path: Path, *, status: str, error: str | None = None) -> None:
    current = _read_json(state_path)
    current["status"] = status
    current["error"] = error
    current["completed_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json_atomic(state_path, current)


def wait_for_network(
    logger: MasterLogger,
    *,
    timeout: float,
    interval: float = 15.0,
) -> bool:
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        errors: list[str] = []
        for host, port in NETWORK_ENDPOINTS:
            try:
                with socket.create_connection((host, port), timeout=5.0):
                    logger.event(
                        "network",
                        "success",
                        f"network is ready via {host}:{port}",
                        attempt=attempt,
                    )
                    return True
            except OSError as exc:
                errors.append(f"{host}:{port}={exc}")
        logger.event(
            "network",
            "waiting",
            "network is not ready; waiting before the next check",
            attempt=attempt,
            errors=errors,
        )
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    logger.event("network", "error", f"network was not ready within {timeout:.0f} seconds")
    return False


def _click_touch(
    hwnd: int,
    image: Any,
    *,
    logger: RunLogger,
    attempt: int,
) -> None:
    width, height = image.size
    x = int(width * TOUCH_CLICK[0])
    y = int(height * TOUCH_CLICK[1])
    click_index = logger.next_click_index()
    marked = logger.save_click_image(
        image,
        f"click-{click_index:03d}-touch-to-start.png",
        x=x,
        y=y,
        key="touch_to_start",
        dry_run=False,
    )
    logger.event(
        action="click",
        key="touch_to_start",
        x=x,
        y=y,
        attempt=attempt,
        dry_run=False,
        screenshot=str(marked),
    )
    click_client(hwnd, x, y)


def _click_logged_ratio(
    hwnd: int,
    image: Any,
    ratio: tuple[float, float],
    *,
    key: str,
    logger: RunLogger,
    attempt: int,
) -> None:
    width, height = image.size
    x = int(width * ratio[0])
    y = int(height * ratio[1])
    click_index = logger.next_click_index()
    marked = logger.save_click_image(
        image,
        f"click-{click_index:03d}-{key}.png",
        x=x,
        y=y,
        key=key,
        dry_run=False,
    )
    logger.event(
        action="click",
        key=key,
        x=x,
        y=y,
        attempt=attempt,
        dry_run=False,
        screenshot=str(marked),
    )
    click_client(hwnd, x, y)


def recognize_daily_entry_state(
    image: Any,
    *,
    text_result: tuple[str, dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Recognize current title art without mistaking loading text for Touch To Start."""
    text_state, text_details = text_result or recognize_entry_status(image)
    if text_state != "unknown":
        return text_state, {"source": "ocr", "text": text_details}

    state, details = recognize_entry_state(image)
    if state != "unknown":
        return state, details

    title = details.get("title", {})
    touch = details.get("touch", {})
    title_anchor = (
        title.get("dark_ratio", 0.0) > 0.08
        and title.get("bright_ratio", 0.0) > 0.40
        and title.get("edge_ratio", 0.0) > 0.030
        and title.get("contrast", 0.0) > 55
    )
    touch_prompt = (
        title_anchor
        and touch.get("dark_ratio", 0.0) > 0.01
        and touch.get("bright_ratio", 0.0) > 0.80
        and touch.get("edge_ratio", 0.0) > 0.010
        and touch.get("contrast", 0.0) > 30
    )
    details = {
        **details,
        "source": "image_stats",
        "text": text_details,
        "daily_fallback": {
            "title_anchor": title_anchor,
            "touch_prompt": touch_prompt,
        },
    }
    if touch_prompt:
        return "touch_ready", details
    if title_anchor:
        return "loading_title", details
    return state, details


def classify_daily_entry_context(
    image: Any,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    """Apply specific known-state recognition before the return-home fallback."""
    text_result = recognize_entry_status(image)
    text_state, text_details = text_result
    if text_state != "unknown":
        return (
            "entry_screen",
            {},
            text_state,
            {"source": "ocr", "text": text_details},
        )

    state, details = classify_state(image)
    if state not in {"unknown", "gacha_animation"}:
        return state, details, "unknown", {"source": "deferred", "text": text_details}

    entry_state, entry_details = recognize_daily_entry_state(
        image,
        text_result=text_result,
    )
    if entry_state in {"touch_ready", "download_waiting", "download_confirmation", "loading_title"}:
        return "entry_screen", details, entry_state, entry_details

    returnable, return_details = recognize_return_home_control(image)
    details["return_home_control"] = return_details
    if returnable:
        state = "returnable_scene"
    return state, details, entry_state, entry_details


def overlay_transition_succeeded(before: Any, next_state: str, after: Any) -> bool:
    return (
        next_state not in {"home_overlay", "blocking_ad_overlay"}
        or _mean_region_difference(before, after) >= 2.5
    )


def return_home_transition_succeeded(next_state: str) -> bool:
    return next_state in {
        "real_home",
        "home_overlay",
        "blocking_ad_overlay",
        "loading",
        "unknown",
    }


def can_finish_entry_phase(
    state: str,
    *,
    requires_entry_screen: bool,
    touch_screen_seen: bool,
) -> bool:
    """Allow warm resumes, but gate cold launches until Touch To Start was observed."""
    return state in DAILY_READY_STATES and (
        not requires_entry_screen or touch_screen_seen
    )


def enter_game_logged(*, timeout: float, log_root: Path) -> tuple[bool, str]:
    """Open the client and record every Touch To Start click."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="enter_game", timeout=timeout)
    existing_hwnd = find_game_window()
    requires_entry_screen = not bool(existing_hwnd)
    touch_screen_seen = not requires_entry_screen
    logger.event(
        action="startup_gate",
        requires_entry_screen=requires_entry_screen,
        existing_window=bool(existing_hwnd),
    )
    try:
        hwnd = open_game(timeout=min(timeout, 120.0))
    except Exception as exc:  # noqa: BLE001
        reason = f"failed to open game: {exc!r}"
        logger.failure(reason)
        return False, reason

    deadline = time.monotonic() + timeout
    step = 0
    touch_attempt = 0
    download_confirm_attempts = 0
    while time.monotonic() < deadline:
        step += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details, entry_state, entry_details = classify_daily_entry_context(image)
        path = logger.save_image(image, f"step-{step:03d}-{state}-{entry_state}.png")
        logger.event(
            action="classify",
            step=step,
            state=state,
            entry_state=entry_state,
            screenshot=str(path),
            details=details,
            entry_details=entry_details,
        )

        if entry_state == "touch_ready":
            touch_screen_seen = True

        if can_finish_entry_phase(
            state,
            requires_entry_screen=requires_entry_screen,
            touch_screen_seen=touch_screen_seen,
        ):
            reason = f"game is ready at state={state}"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason
        if state in DAILY_READY_STATES:
            logger.event(
                action="wait_startup_gate",
                state=state,
                entry_state=entry_state,
                reason="cold launch has not shown Touch To Start yet",
                screenshot=str(path),
            )
            time.sleep(5.0)
            continue
        if state == "loading":
            time.sleep(3.0)
            continue
        if entry_state in ENTRY_WAITING_STATES:
            download_confirm_attempts = 0
            logger.event(
                action="wait_entry_state",
                reason="startup, loading, or download work is still in progress",
                screenshot=str(path),
                entry_details=entry_details,
            )
            time.sleep(10.0)
            continue
        if entry_state == "download_confirmation":
            download_confirm_attempts += 1
            if download_confirm_attempts > 2:
                reason = "download confirmation did not take effect after 2 clicks"
                logger.failure(reason)
                return False, reason
            _click_logged_ratio(
                hwnd,
                image,
                DOWNLOAD_CONFIRM_CLICK,
                key="confirm_download",
                logger=logger,
                attempt=download_confirm_attempts,
            )
            time.sleep(6.0)
            continue
        if entry_state == "touch_ready":
            download_confirm_attempts = 0
            touch_attempt += 1
            _click_touch(hwnd, image, logger=logger, attempt=touch_attempt)
            time.sleep(6.0)
            continue
        if state == "returnable_scene":
            ok, _next_state, _next_image, reason = click_with_fixed_retry(
                hwnd,
                image,
                "plaza_home",
                verify=lambda next_state, _image: return_home_transition_succeeded(next_state),
                description="return home from fallback scene",
                dry_run=False,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                return False, reason
            continue

        logger.event(
            action="wait_entry_screen",
            state=state,
            entry_state=entry_state,
            reason="startup or login screen is not actionable yet",
            screenshot=str(path),
        )
        time.sleep(5.0)

    reason = f"game entry timed out after {timeout:.0f} seconds"
    logger.failure(reason)
    return False, reason


def ensure_home(*, timeout: float, log_root: Path) -> tuple[bool, str]:
    """Return from gacha/plaza/overlay states to the recognized home screen."""
    from open_game import find_game_window

    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="ensure_home", timeout=timeout)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    deadline = time.monotonic() + timeout
    step = 0
    while time.monotonic() < deadline:
        step += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(image)
        path = logger.save_image(image, f"step-{step:03d}-{state}.png")
        logger.event(
            action="classify",
            step=step,
            state=state,
            screenshot=str(path),
            details=details,
        )
        if state == "real_home":
            reason = "returned to real_home"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason
        if state == "loading":
            time.sleep(3.0)
            continue

        if state in {"gacha_page", "arena_cartridge_collection"}:
            key = "result_back"
            description = f"return home from {state}"
            expected = {"real_home", "home_overlay", "blocking_ad_overlay", "loading"}
        elif state in {"home_overlay", "blocking_ad_overlay"}:
            key = "dismiss_overlay"
            description = "dismiss home overlay"
            expected = {"real_home", "plaza", "loading"}
            overlay_before = image.copy()
        elif state == "plaza":
            key = "plaza_home"
            description = "return home from plaza"
            expected = {"real_home", "home_overlay", "blocking_ad_overlay", "loading"}
        else:
            reason = f"cannot safely return home from state={state}"
            logger.failure(reason)
            return False, reason

        ok, _next_state, _next_image, reason = click_with_fixed_retry(
            hwnd,
            image,
            key,
            verify=(
                (lambda next_state, next_image: overlay_transition_succeeded(
                    overlay_before,
                    next_state,
                    next_image,
                ))
                if state in {"home_overlay", "blocking_ad_overlay"}
                else (lambda next_state, _image, accepted=expected: next_state in accepted)
            ),
            description=description,
            dry_run=False,
            logger=logger,
        )
        if not ok:
            logger.failure(reason)
            return False, reason

    reason = f"returning home timed out after {timeout:.0f} seconds"
    logger.failure(reason)
    return False, reason


def _require_phase(
    master: MasterLogger,
    stage: str,
    operation: Any,
    *,
    log_root: Path,
) -> None:
    master.event(stage, "start", "phase started", log_root=str(log_root))
    ok, reason = operation(log_root=log_root)
    if not ok:
        master.event(stage, "error", reason, log_root=str(log_root))
        raise DailyRunError(f"{stage}: {reason}")
    master.event(stage, "success", reason, log_root=str(log_root))


def run_daily(*, project_root: Path, force: bool, network_timeout: float) -> int:
    os.chdir(project_root)
    started = datetime.now()
    run_date = date.today().isoformat()
    run_root = project_root / "logs" / "daily" / run_date / started.strftime("%H%M%S")
    state_path = project_root / "state" / "daily_automation.json"
    master = MasterLogger(run_root)
    master.event("daily", "start", "daily automation process started", force=force)

    claimed, previous = claim_daily_run(
        state_path,
        run_date=run_date,
        run_root=run_root,
        force=force,
        started_at=started.isoformat(timespec="seconds"),
    )
    if not claimed:
        message = (
            f"today has already started once; previous status={previous.get('status')}, "
            f"run_root={previous.get('run_root')}"
        )
        master.event("daily", "skipped", message)
        master.summary(result="skipped", reason=message, previous=previous)
        return 0

    try:
        if not wait_for_network(master, timeout=network_timeout):
            raise DailyRunError("network check timed out")

        _require_phase(
            master,
            "enter_game",
            lambda *, log_root: enter_game_logged(timeout=240.0, log_root=log_root),
            log_root=run_root / "01-enter-game",
        )

        gacha_root = run_root / "02-free-gacha"
        master.event("free_gacha", "start", "phase started", log_root=str(gacha_root))
        gacha = run_free_gacha(
            targets=["costume", "gear"],
            timeout=360.0,
            interval=2.0,
            dry_run=False,
            test_mode=True,
            log_root=gacha_root,
        )
        if gacha.reason != "all requested free gacha targets completed":
            master.event("free_gacha", "error", gacha.reason, state=gacha.state)
            raise DailyRunError(f"free_gacha: {gacha.reason}")
        master.event("free_gacha", "success", gacha.reason, state=gacha.state)

        _require_phase(
            master,
            "return_home",
            lambda *, log_root: ensure_home(timeout=120.0, log_root=log_root),
            log_root=run_root / "03-return-home",
        )
        _require_phase(
            master,
            "quick_hunt_entry",
            lambda *, log_root: enter_quick_hunt(dry_run=False, log_root=log_root),
            log_root=run_root / "04-quick-hunt-entry",
        )
        _require_phase(
            master,
            "hunting_ground_setup",
            lambda *, log_root: start_selected_quick_hunt(dry_run=False, log_root=log_root),
            log_root=run_root / "05-hunting-ground-setup",
        )
        _require_phase(
            master,
            "hunting_ground_confirm",
            lambda *, log_root: maximize_and_confirm_quick_hunt(
                dry_run=False,
                log_root=log_root,
            ),
            log_root=run_root / "06-hunting-ground-confirm",
        )
        _require_phase(
            master,
            "crystal_cave_cycle",
            lambda *, log_root: run_crystal_cave_cycle(dry_run=False, log_root=log_root),
            log_root=run_root / "07-crystal-cave-cycle",
        )
        _require_phase(
            master,
            "daily_arena",
            lambda *, log_root: run_daily_arena(dry_run=False, log_root=log_root),
            log_root=run_root / "08-daily-arena",
        )

        update_daily_state(state_path, status="completed")
        master.event("daily", "success", "all daily automation phases completed")
        master.summary(
            result="completed",
            started_at=started.isoformat(timespec="seconds"),
            completed_at=datetime.now().isoformat(timespec="seconds"),
            run_root=str(run_root),
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - fatal errors must be persisted.
        reason = str(exc)
        update_daily_state(state_path, status="failed", error=reason)
        master.event("daily", "error", reason, traceback=traceback.format_exc())
        master.summary(
            result="failed",
            reason=reason,
            started_at=started.isoformat(timespec="seconds"),
            completed_at=datetime.now().isoformat(timespec="seconds"),
            run_root=str(run_root),
        )
        return 2


def check_environment(*, project_root: Path, network_timeout: float) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = MasterLogger(project_root / "logs" / "daily-check" / stamp)
    logger.event("check", "start", "environment check started")
    network_ok = wait_for_network(logger, timeout=network_timeout, interval=2.0)
    starter = Path(r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe")
    starter_ok = starter.exists()
    logger.event(
        "check",
        "success" if starter_ok else "error",
        f"game starter exists={starter_ok}",
        path=str(starter),
    )
    result = network_ok and starter_ok
    logger.summary(result="passed" if result else "failed", network=network_ok, starter=starter_ok)
    print(f"environment_ok={result}")
    print(f"log_root={logger.root}")
    return 0 if result else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BrownDust II daily automation once per day.")
    parser.add_argument("--force", action="store_true", help="allow a manual rerun today")
    parser.add_argument("--check", action="store_true", help="check prerequisites without claiming today")
    parser.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--network-timeout", type=float, default=900.0)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    project_root = args.project_root.resolve()

    if args.check:
        raise SystemExit(
            check_environment(project_root=project_root, network_timeout=args.network_timeout)
        )

    try:
        with SingleInstance():
            result = run_daily(
                project_root=project_root,
                force=args.force,
                network_timeout=args.network_timeout,
            )
    except DailyRunError as exc:
        print(f"daily_automation_error={exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    raise SystemExit(result)


if __name__ == "__main__":
    main()
