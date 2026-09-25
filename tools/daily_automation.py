"""Run the BrownDust II daily automation once per local calendar day."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from enter_game import TOUCH_CLICK, recognize_entry_state  # noqa: E402
from adaptive_wait import AdaptivePoll  # noqa: E402
from free_gacha import (  # noqa: E402
    CLICK_POINTS,
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
from business_management import run_business_management  # noqa: E402
from activity_rewards import run_activity_rewards  # noqa: E402
from mail_rewards import run_mail_rewards  # noqa: E402
from pass_rewards import run_pass_rewards  # noqa: E402
from task_rewards import run_task_rewards  # noqa: E402
from mute_browndust import set_mute  # noqa: E402
from daily_plan import (  # noqa: E402
    DAILY_PRESETS,
    DAILY_STAGE_IDS,
    FAST_PRESET,
    daily_plan_to_dict,
    get_current_stages,
    get_daily_plan,
)
from daily_state import DailyStateStore  # noqa: E402


TASK_NAME = "BrownDust2DailyAutomation"
MUTEX_NAME = r"Local\BrownDust2DailyAutomation"
ERROR_ALREADY_EXISTS = 183
MOUSEEVENTF_MOVE = 0x0001
DESKTOP_ACTIVITY_INTERVAL_SECONDS = 30.0
GOOGLE_CONNECTIVITY_URL = "https://www.google.com/generate_204"
DOWNLOAD_CONFIRM_CLICK = (0.548, 0.725)
STARTUP_PROMOTION_TRANSITION_MIN_DIFF = 12.0
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
    "arena_lobby",
    "quick_hunt_map",
    "quick_hunt_setup",
    "business_management_dialog",
    "reward_overlay",
    "restaurant_home",
    "restaurant_regular_customer_mode",
    "restaurant_regular_customer_notes",
}
ENTRY_WAITING_STATES = {
    "download_waiting",
    "loading_title",
    "startup_waiting",
}
MAX_UNKNOWN_ENTRY_FRAMES = 3
DAILY_RESET_HOUR = 8

MASTER_STAGE_NAMES = {
    "daily": "每日任务",
    "network": "网络检查",
    "enter_game": "进入游戏",
    "resume_game": "恢复游戏",
    "prepare_home": "返回主页",
    "free_gacha_home": "抽卡前返回主页",
    "free_gacha": "免费抽卡",
    "return_home": "抽卡后返回主页",
    "quick_hunt_entry": "进入快速狩猎",
    "hunting_ground_setup": "设置狩猎场",
    "hunting_ground_confirm": "执行狩猎场",
    "crystal_cave_cycle": "执行圣石洞穴",
    "daily_arena": "每日竞技场",
    "business_management_home": "经营管理前返回主页",
    "business_management": "经营管理收益",
    "reward_prepare_home": "领奖前返回主页",
    "task_rewards": "每日和每周任务奖励",
    "activity_rewards": "活动奖励",
    "pass_rewards": "通行证奖励",
    "mail_rewards": "邮件奖励",
    "check": "环境检查",
}

MASTER_STATUS_NAMES = {
    "start": "开始",
    "waiting": "等待",
    "success": "完成",
    "error": "失败",
    "skipped": "跳过",
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


def _pulse_desktop_activity() -> None:
    """Reset Windows' idle timer without moving the physical cursor."""
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)


class DesktopActivityGuard:
    """Keep the interactive desktop available while the daily run is active."""

    def __init__(self, interval: float = DESKTOP_ACTIVITY_INTERVAL_SECONDS) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            _pulse_desktop_activity()

    def start(self) -> None:
        _pulse_desktop_activity()
        self._thread = threading.Thread(
            target=self._run,
            name="daily-desktop-activity",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def __enter__(self) -> "DesktopActivityGuard":
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.stop()


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
        stage_name = MASTER_STAGE_NAMES.get(stage, stage)
        status_name = MASTER_STATUS_NAMES.get(status, status)
        line = f"[{now[11:19]}] [{status_name}] [{stage_name}] {message}"
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
            file.write(line + "\n")
        print(line, flush=True)

    def summary(self, **payload: Any) -> None:
        self.summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def game_day_key(current: datetime) -> str:
    """Return the local game day, which rolls over every day at 08:00."""
    if current.hour < DAILY_RESET_HOUR:
        current -= timedelta(days=1)
    return current.date().isoformat()


def current_proxy_settings() -> dict[str, str]:
    """Read proxy settings again so a VPN started after this process is visible."""
    if os.name != "nt":
        return urllib.request.getproxies()

    registry = urllib.request.getproxies_registry()
    environment = urllib.request.getproxies_environment()
    supported_environment = {
        scheme: url
        for scheme, url in environment.items()
        if scheme in {"http", "https", "ftp", "all"}
    }
    return {**registry, **supported_environment}


def wait_for_network(
    logger: MasterLogger,
    *,
    timeout: float | None,
    request_timeout: float = 5.0,
) -> bool:
    deadline = None if timeout is None or timeout <= 0 else time.monotonic() + timeout
    poll = AdaptivePoll()
    attempt = 0
    while True:
        attempt += 1
        try:
            request = urllib.request.Request(
                GOOGLE_CONNECTIVITY_URL,
                headers={"User-Agent": "BrownDust2DailyAutomation/1.0"},
            )
            proxy_handler = urllib.request.ProxyHandler(current_proxy_settings())
            opener = urllib.request.build_opener(proxy_handler)
            with opener.open(request, timeout=request_timeout) as response:
                status = response.getcode()
            if status == 204:
                logger.event(
                    "network",
                    "success",
                    "已确认可以访问 Google",
                    attempt=attempt,
                    endpoint=GOOGLE_CONNECTIVITY_URL,
                    http_status=status,
                )
                return True
            error = f"unexpected HTTP status {status}"
        except (OSError, urllib.error.URLError) as exc:
            error = repr(exc)

        now = time.monotonic()
        if deadline is not None and now >= deadline:
            logger.event("network", "error", f"等待 Google 网络连接超过 {timeout:.0f} 秒")
            return False

        remaining = None if deadline is None else deadline - now
        delay = poll.next_delay(remaining=remaining)
        logger.event(
            "network",
            "waiting",
            "暂时无法访问 Google，稍后重新检查",
            attempt=attempt,
            endpoint=GOOGLE_CONNECTIVITY_URL,
            error=error,
            next_check_seconds=delay,
        )
        time.sleep(delay)


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


def mute_game_audio(logger: RunLogger, *, attempt: int) -> bool:
    """Mute every active BrownDust II audio session and record the result."""
    try:
        muted_sessions = set_mute(True)
    except Exception as exc:  # noqa: BLE001 - audio control must not hide failures.
        logger.event(
            action="mute_game_audio",
            result="error",
            attempt=attempt,
            error=repr(exc),
        )
        return False

    if muted_sessions:
        logger.event(
            action="mute_game_audio",
            result="success",
            attempt=attempt,
            muted_sessions=muted_sessions,
        )
        return True

    logger.event(
        action="mute_game_audio",
        result="waiting",
        attempt=attempt,
        reason="BrownDust II audio session is not available yet",
    )
    return False


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
    if (
        state == "gacha_animation"
        and details.get("classification_rule") == "bright_scene"
    ):
        return (
            "entry_screen",
            details,
            "startup_promotion",
            {"source": "bright_startup_promotion", "text": text_details},
        )
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


def startup_promotion_transition_succeeded(
    before: Any,
    next_state: str,
    after: Any,
) -> bool:
    return (
        next_state
        in {"real_home", "home_overlay", "blocking_ad_overlay", "plaza", "loading"}
        or _mean_region_difference(before, after)
        >= STARTUP_PROMOTION_TRANSITION_MIN_DIFF
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

    last_progress_at = time.monotonic()
    previous_context: tuple[str, str] | None = None
    step = 0
    touch_attempt = 0
    promotion_clicks = 0
    download_confirm_attempts = 0
    audio_muted = False
    mute_attempt = 0
    next_mute_attempt = 0.0
    unknown_entry_frames = 0
    terms_agreement_attempts = 0
    poll = AdaptivePoll()
    while time.monotonic() - last_progress_at < timeout:
        if not audio_muted and time.monotonic() >= next_mute_attempt:
            mute_attempt += 1
            audio_muted = mute_game_audio(logger, attempt=mute_attempt)
            next_mute_attempt = time.monotonic() + 5.0
            if audio_muted:
                last_progress_at = time.monotonic()
                logger.event(
                    action="progress",
                    reason="game_audio_muted",
                    stall_timeout=timeout,
                )

        step += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details, entry_state, entry_details = classify_daily_entry_context(image)
        context = (state, entry_state)
        if previous_context is not None and context != previous_context:
            last_progress_at = time.monotonic()
            poll.reset()
            logger.event(
                action="progress",
                reason="recognized_state_changed",
                previous_state=previous_context[0],
                previous_entry_state=previous_context[1],
                state=state,
                entry_state=entry_state,
                stall_timeout=timeout,
            )
        previous_context = context
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

        if state == "unknown" and entry_state == "unknown":
            unknown_entry_frames += 1
        else:
            unknown_entry_frames = 0

        if can_finish_entry_phase(
            state,
            requires_entry_screen=requires_entry_screen,
            touch_screen_seen=touch_screen_seen,
        ):
            if not audio_muted:
                logger.event(
                    action="wait_audio_mute",
                    state=state,
                    reason="game is ready but its audio session is not muted yet",
                )
                time.sleep(poll.next_delay())
                continue
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
            time.sleep(poll.next_delay())
            continue
        if state == "terms_agreement":
            terms_agreement_attempts += 1
            if terms_agreement_attempts > 2:
                reason = "game terms agreement did not close after 2 attempts"
                logger.failure(reason)
                return False, reason
            _click_logged_ratio(
                hwnd,
                image,
                CLICK_POINTS["terms_all_agree"],
                key="terms_all_agree",
                logger=logger,
                attempt=terms_agreement_attempts,
            )
            time.sleep(poll.next_delay())
            ok, next_state, _next_image, reason = click_with_fixed_retry(
                hwnd,
                image,
                "terms_start",
                verify=lambda candidate, _image: candidate != "terms_agreement",
                description="accept game terms and start",
                dry_run=False,
                logger=logger,
                attempts=1,
                wait_on_unknown_transition=True,
            )
            if ok:
                touch_screen_seen = True
                last_progress_at = time.monotonic()
                poll.reset()
                logger.event(
                    action="progress",
                    reason="game_terms_accepted",
                    next_state=next_state,
                    stall_timeout=timeout,
                )
            elif terms_agreement_attempts >= 2:
                logger.failure(reason)
                return False, reason
            continue
        if state == "loading":
            last_progress_at = time.monotonic()
            time.sleep(poll.next_delay())
            continue
        if state == "arena_rank_change":
            accepted = {"arena_lobby", "plaza", "real_home", "loading"}
            ok, next_state, _next_image, reason = click_with_fixed_retry(
                hwnd,
                image,
                "arena_rank_confirm",
                verify=lambda candidate, _image: candidate in accepted,
                description="confirm leftover arena rank change during game entry",
                dry_run=False,
                logger=logger,
                wait_on_unknown_transition=True,
            )
            if not ok:
                logger.failure(reason)
                return False, reason
            touch_screen_seen = True
            last_progress_at = time.monotonic()
            poll.reset()
            logger.event(
                action="progress",
                reason="verified_rank_change_confirmation",
                state=next_state,
                click="arena_rank_confirm",
                stall_timeout=timeout,
            )
            continue
        if entry_state == "startup_promotion":
            promotion_clicks += 1
            promotion_before = image.copy()
            ok, next_state, _next_image, reason = click_with_fixed_retry(
                hwnd,
                image,
                "startup_promotion",
                verify=lambda next_state, next_image: startup_promotion_transition_succeeded(
                    promotion_before,
                    next_state,
                    next_image,
                ),
                description="advance startup promotion",
                dry_run=False,
                logger=logger,
                attempts=1,
            )
            if ok:
                touch_screen_seen = True
                last_progress_at = time.monotonic()
                logger.event(
                    action="progress",
                    reason="verified_promotion_advance",
                    click="startup_promotion",
                    click_count=promotion_clicks,
                    next_state=next_state,
                    stall_timeout=timeout,
                )
            else:
                logger.event(
                    action="wait_startup_promotion",
                    reason=reason,
                    click_count=promotion_clicks,
                    stall_timeout=timeout,
                )
            continue
        if entry_state in ENTRY_WAITING_STATES:
            download_confirm_attempts = 0
            last_progress_at = time.monotonic()
            logger.event(
                action="wait_entry_state",
                reason="startup, loading, or download work is still in progress",
                screenshot=str(path),
                entry_details=entry_details,
            )
            time.sleep(poll.next_delay())
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
            poll.reset()
            time.sleep(poll.next_delay())
            continue
        if entry_state == "touch_ready":
            download_confirm_attempts = 0
            touch_attempt += 1
            _click_touch(hwnd, image, logger=logger, attempt=touch_attempt)
            poll.reset()
            time.sleep(poll.next_delay())
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
            last_progress_at = time.monotonic()
            logger.event(
                action="progress",
                reason="verified_click_succeeded",
                state=state,
                click="plaza_home",
                stall_timeout=timeout,
            )
            continue

        if unknown_entry_frames >= MAX_UNKNOWN_ENTRY_FRAMES:
            reason = (
                "unrecognized startup page persisted for "
                f"{unknown_entry_frames} checks; stopped safely for review"
            )
            logger.failure(reason)
            return False, reason

        logger.event(
            action="wait_entry_screen",
            state=state,
            entry_state=entry_state,
            reason="startup or login screen is not actionable yet",
            screenshot=str(path),
        )
        time.sleep(poll.next_delay())

    reason = f"game entry made no progress for {timeout:.0f} seconds"
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

    last_progress_at = time.monotonic()
    previous_state: str | None = None
    step = 0
    poll = AdaptivePoll()
    while time.monotonic() - last_progress_at < timeout:
        step += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(image)
        should_probe_return_home = state in {"unknown", "gacha_animation"} or (
            state == "blocking_ad_overlay"
            and details.get("classification_rule") == "blocking_overlay_brightness"
        )
        if should_probe_return_home:
            returnable, return_details = recognize_return_home_control(image)
            details["return_home_control"] = return_details
            if returnable:
                state = "returnable_scene"
        path = logger.save_image(image, f"step-{step:03d}-{state}.png")
        logger.event(
            action="classify",
            step=step,
            state=state,
            screenshot=str(path),
            details=details,
        )
        if previous_state is not None and state != previous_state:
            last_progress_at = time.monotonic()
            poll.reset()
            logger.event(
                action="progress",
                reason="recognized_state_changed",
                previous_state=previous_state,
                state=state,
                stall_timeout=timeout,
            )
        previous_state = state
        if state == "real_home":
            reason = "returned to real_home"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason
        if state == "loading":
            last_progress_at = time.monotonic()
            time.sleep(poll.next_delay())
            continue

        if state == "reward_overlay":
            key = "reward_overlay_dismiss"
            description = "dismiss reward overlay before returning home"
            expected = {
                "arena_lobby",
                "business_management_dialog",
                "restaurant_regular_customer_notes",
                "quick_hunt_map",
                "loading",
            }
        elif state == "business_management_dialog":
            key = "business_management_cancel"
            description = "close business-management dialog"
            expected = {"real_home", "loading"}
        elif state in {"restaurant_home", "restaurant_regular_customer_mode"}:
            key = "restaurant_home"
            description = "return home from restaurant"
            expected = {"real_home", "home_overlay", "blocking_ad_overlay", "loading"}
        elif state == "restaurant_regular_customer_notes":
            key = "restaurant_notes_back"
            description = "return from regular-customer notes"
            expected = {"restaurant_home", "restaurant_regular_customer_mode", "loading"}
        elif state == "quick_hunt_map":
            key = "quick_hunt_back"
            description = "return home from quick-hunt map"
            expected = {"real_home", "home_overlay", "blocking_ad_overlay", "loading"}
        elif state == "quick_hunt_setup":
            key = "quick_hunt_cancel"
            description = "close quick-hunt setup before returning home"
            expected = {"quick_hunt_map", "loading"}
        elif state == "gacha_result":
            key = "result_back"
            description = "return from gacha result to gacha page"
            expected = {"gacha_page", "loading"}
        elif state in {"gacha_page", "arena_cartridge_collection"}:
            key = "result_back"
            description = f"return home from {state}"
            expected = {"real_home", "home_overlay", "blocking_ad_overlay", "loading"}
        elif state in {"home_overlay", "blocking_ad_overlay"}:
            key = "dismiss_overlay"
            description = "dismiss home overlay"
            expected = {"real_home", "plaza", "loading"}
            overlay_before = image.copy()
        elif state == "returnable_scene":
            key = "plaza_home"
            description = "return home from fallback scene"
            expected = {"real_home", "home_overlay", "blocking_ad_overlay", "loading"}
        elif state == "plaza":
            key = "plaza_home"
            description = "return home from plaza"
            expected = {"real_home", "home_overlay", "blocking_ad_overlay", "loading"}
        elif state == "arena_lobby":
            key = "arena_home"
            description = "return home from arena lobby"
            expected = {"real_home", "home_overlay", "blocking_ad_overlay", "loading"}
        elif state == "arena_rank_change":
            key = "arena_rank_confirm"
            description = "confirm leftover arena rank change before returning home"
            expected = {"arena_lobby", "plaza", "real_home", "loading"}
        else:
            reason = f"cannot safely return home from state={state}"
            logger.failure(reason)
            return False, reason

        ok, next_state, _next_image, reason = click_with_fixed_retry(
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
            verify_timeout=timeout if state == "plaza" else 20.0,
            attempts=1 if state == "plaza" else 2,
            wait_on_unknown_transition=True,
        )
        if not ok:
            logger.failure(reason)
            return False, reason
        last_progress_at = time.monotonic()
        poll.reset()
        previous_state = next_state
        logger.event(
            action="progress",
            reason="verified_click_succeeded",
            key=key,
            state=next_state,
            stall_timeout=timeout,
        )

    reason = f"returning home made no progress for {timeout:.0f} seconds"
    logger.failure(reason)
    return False, reason


def _require_phase(
    master: MasterLogger,
    stage: str,
    operation: Any,
    *,
    log_root: Path,
) -> str:
    master.event(stage, "start", "开始执行", log_root=str(log_root))
    try:
        ok, reason = operation(log_root=log_root)
    except Exception as exc:
        stage_name = MASTER_STAGE_NAMES.get(stage, stage)
        reason = f"未处理异常：{exc!r}"
        log_root.mkdir(parents=True, exist_ok=True)
        failure_path = log_root / "failure.txt"
        if not failure_path.exists():
            failure_path.write_text(reason + "\n", encoding="utf-8")
        master.event(
            stage,
            "error",
            "执行时发生异常，已停在当前界面并保留日志",
            technical_reason=reason,
            log_root=str(log_root),
        )
        raise DailyRunError(f"{stage_name}执行异常，已保留现场") from exc
    if not ok:
        stage_name = MASTER_STAGE_NAMES.get(stage, stage)
        master.event(
            stage,
            "error",
            "执行失败，已停在当前界面；请查看该步骤日志和截图",
            technical_reason=reason,
            log_root=str(log_root),
        )
        raise DailyRunError(f"{stage_name}执行失败，已保留现场")
    if reason.startswith("skipped:"):
        master.event(
            stage,
            "skipped",
            "当前没有可领取的奖励，已跳过",
            technical_reason=reason,
            log_root=str(log_root),
        )
    else:
        master.event(
            stage,
            "success",
            "执行完成",
            technical_reason=reason,
            log_root=str(log_root),
        )
    return reason


CURRENT_PHASE_IDS = tuple(stage.id for stage in get_current_stages(FAST_PRESET))


def daily_plan_report(preset: str | None = None) -> dict[str, Any]:
    """Return target plans plus the safe compatibility path, without side effects."""
    selected = DAILY_PRESETS if preset is None else (preset,)
    return {
        "mode": "target-plan",
        "compatibility_stage_ids": list(CURRENT_PHASE_IDS),
        "presets": [daily_plan_to_dict(name) for name in selected],
    }


def _phase_log_root(run_root: Path, phase_id: str) -> Path:
    position = DAILY_STAGE_IDS.index(phase_id) + 1
    return run_root / f"{position:02d}-{phase_id.replace('_', '-')}"


def _run_free_gacha_phase(master: MasterLogger, log_root: Path) -> str:
    _require_phase(
        master,
        "free_gacha_home",
        lambda *, log_root: ensure_home(timeout=120.0, log_root=log_root),
        log_root=log_root / "01-return-home",
    )
    action_root = log_root / "02-free-gacha"
    master.event("free_gacha", "start", "开始执行", log_root=str(action_root))
    result = run_free_gacha(
        targets=["costume", "gear"],
        timeout=360.0,
        interval=2.0,
        dry_run=False,
        test_mode=True,
        log_root=action_root,
    )
    if result.reason == "gacha has no home reward notification":
        master.event("free_gacha", "skipped", "抽抽乐没有红点，跳过免费抽卡")
        return "skipped:gacha has no home reward notification"
    if result.reason != "all requested free gacha targets completed":
        master.event(
            "free_gacha",
            "error",
            f"执行失败，当前画面：{result.state}；详细原因：{result.reason}",
            state=result.state,
        )
        raise DailyRunError(f"free_gacha: {result.reason}")
    master.event("free_gacha", "success", "人物和装备免费抽卡均已完成", state=result.state)
    _require_phase(
        master,
        "return_home",
        lambda *, log_root: ensure_home(timeout=120.0, log_root=log_root),
        log_root=log_root / "03-return-home",
    )
    return result.reason


def _execute_daily_phase(phase_id: str, *, master: MasterLogger, run_root: Path) -> str:
    log_root = _phase_log_root(run_root, phase_id)
    if phase_id == "start":
        _require_phase(
            master,
            "enter_game",
            lambda *, log_root: enter_game_logged(timeout=240.0, log_root=log_root),
            log_root=log_root / "01-enter-game",
        )
        return _require_phase(
            master,
            "prepare_home",
            lambda *, log_root: ensure_home(timeout=120.0, log_root=log_root),
            log_root=log_root / "02-prepare-home",
        )
    if phase_id == "quick_hunt":
        _require_phase(
            master,
            "prepare_home",
            lambda *, log_root: ensure_home(timeout=120.0, log_root=log_root),
            log_root=log_root / "00-prepare-home",
        )
        entry_reason = _require_phase(
            master,
            "quick_hunt_entry",
            lambda *, log_root: enter_quick_hunt(
                dry_run=False, log_root=log_root, require_notification=False
            ),
            log_root=log_root / "01-entry",
        )
        if entry_reason == "quick_hunt has no home reward notification":
            return "skipped:quick_hunt has no home reward notification"
        _require_phase(
            master,
            "hunting_ground_setup",
            lambda *, log_root: start_selected_quick_hunt(dry_run=False, log_root=log_root),
            log_root=log_root / "02-hunting-ground-setup",
        )
        _require_phase(
            master,
            "hunting_ground_confirm",
            lambda *, log_root: maximize_and_confirm_quick_hunt(dry_run=False, log_root=log_root),
            log_root=log_root / "03-hunting-ground-confirm",
        )
        return _require_phase(
            master,
            "crystal_cave_cycle",
            lambda *, log_root: run_crystal_cave_cycle(dry_run=False, log_root=log_root),
            log_root=log_root / "04-crystal-cave-cycle",
        )
    if phase_id == "free_gacha":
        return _run_free_gacha_phase(master, log_root)
    if phase_id == "arena":
        return _require_phase(
            master,
            "daily_arena",
            lambda *, log_root: run_daily_arena(dry_run=False, log_root=log_root),
            log_root=log_root,
        )
    if phase_id == "daily_claims":
        _require_phase(
            master,
            "business_management_home",
            lambda *, log_root: ensure_home(timeout=120.0, log_root=log_root),
            log_root=log_root / "01-return-home",
        )
        return _require_phase(
            master,
            "business_management",
            lambda *, log_root: run_business_management(dry_run=False, log_root=log_root),
            log_root=log_root / "02-claim-rewards",
        )
    reward_operations = {
        "task_rewards": ("task_rewards", run_task_rewards),
        "activity_rewards": ("activity_rewards", run_activity_rewards),
        "pass_rewards": ("pass_rewards", run_pass_rewards),
        "mail_rewards": ("mail_rewards", run_mail_rewards),
    }
    if phase_id in reward_operations:
        stage, operation = reward_operations[phase_id]
        return _require_phase(
            master,
            stage,
            lambda *, log_root: operation(dry_run=False, log_root=log_root),
            log_root=log_root,
        )
    raise DailyRunError(f"phase has no local runner: {phase_id}")


def _initialize_unavailable_phases(store: DailyStateStore) -> None:
    for stage in get_daily_plan(FAST_PRESET):
        if store.phase_status(stage.id) != "pending" or stage.current_runnable:
            continue
        if not stage.enabled:
            store.mark_skipped(stage.id)
        else:
            store.mark_unavailable(stage.id, stage.unavailable_reason)


def _preset_blockers(preset: str) -> tuple[str, ...]:
    return tuple(
        stage.id for stage in get_daily_plan(preset) if stage.enabled and not stage.implemented
    )


def run_daily(
    *,
    project_root: Path,
    force: bool,
    network_timeout: float,
    force_phase: str | None = None,
    preset: str | None = None,
) -> int:
    os.chdir(project_root)
    started = datetime.now()
    run_date = game_day_key(started)
    run_root = project_root / "logs" / "daily" / run_date / started.strftime("%H%M%S")
    state_path = project_root / "state" / "daily_automation.json"
    master = MasterLogger(run_root)
    master.event(
        "daily",
        "start",
        "每日自动任务已启动（每天 08:00 刷新）",
        force=force,
        force_phase=force_phase,
        preset=preset or "current-compatible",
        game_day=run_date,
    )

    if preset is not None:
        blockers = _preset_blockers(preset)
        if blockers:
            message = f"preset {preset} has unavailable stages: {', '.join(blockers)}"
            master.event("daily", "error", "完整预设仍有未实现阶段，未启动游戏", blockers=blockers)
            master.summary(result="blocked", reason=message, run_root=str(run_root))
            return 2

    try:
        store = DailyStateStore.load(
            state_path, game_day=run_date, phase_ids=DAILY_STAGE_IDS
        )
    except ValueError as exc:
        raise DailyRunError(str(exc)) from exc

    state = store.state
    legacy = state.get("legacy")
    if (
        not force
        and force_phase is None
        and isinstance(legacy, dict)
        and legacy.get("status") in {"started", "failed"}
        and (legacy.get("last_started_game_day") or legacy.get("last_started_date")) == run_date
    ):
        message = "legacy failed run has no phase checkpoints; use --force-phase or --force"
        master.event("daily", "error", "旧失败记录无法安全判断断点，未自动重复执行", technical_message=message)
        master.summary(result="blocked", reason=message, legacy=legacy)
        return 2

    _initialize_unavailable_phases(store)
    if force_phase is not None and force_phase not in CURRENT_PHASE_IDS:
        message = f"phase is not available in the current PC runner: {force_phase}"
        master.event("daily", "error", "指定阶段尚无可执行的 PC 实现", technical_message=message)
        master.summary(result="blocked", reason=message)
        return 2

    phases_to_run = (
        CURRENT_PHASE_IDS
        if force
        else tuple(
            phase_id
            for phase_id in store.phases_to_run(force_phase=force_phase)
            if phase_id in CURRENT_PHASE_IDS
        )
    )
    if not phases_to_run:
        message = "all current-compatible phases are already finished for this game day"
        master.event(
            "daily",
            "skipped",
            "当前可执行阶段在本刷新周期已经完成；每天 08:00 后会重新开始",
            technical_message=message,
            game_day=run_date,
        )
        master.summary(result="skipped", reason=message, state=store.state)
        return 0

    desktop_guard: DesktopActivityGuard | None = None
    active_phase: str | None = None
    try:
        if not wait_for_network(master, timeout=network_timeout):
            raise DailyRunError("network check timed out")
        desktop_guard = DesktopActivityGuard()
        desktop_guard.start()

        if force_phase is None and phases_to_run[0] != "start" and not find_game_window():
            _require_phase(
                master,
                "resume_game",
                lambda *, log_root: enter_game_logged(timeout=240.0, log_root=log_root),
                log_root=run_root / "00-resume-game",
            )

        for phase_id in phases_to_run:
            active_phase = phase_id
            store.mark_running(phase_id)
            reason = _execute_daily_phase(phase_id, master=master, run_root=run_root)
            if reason.startswith("skipped:"):
                store.mark_skipped(phase_id)
            else:
                store.mark_completed(phase_id)
            active_phase = None

        master.event("daily", "success", "当前 PC 已具备的日常阶段已经完成")
        master.summary(
            result="completed",
            started_at=started.isoformat(timespec="seconds"),
            completed_at=datetime.now().isoformat(timespec="seconds"),
            run_root=str(run_root),
            completed_phases=list(phases_to_run),
            state=store.state,
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - fatal errors must be persisted.
        reason = str(exc)
        if active_phase is not None:
            store.mark_failed(active_phase, reason)
        master.event("daily", "error", f"任务停止，详细原因：{reason}", traceback=traceback.format_exc())
        master.summary(
            result="failed",
            reason=reason,
            started_at=started.isoformat(timespec="seconds"),
            completed_at=datetime.now().isoformat(timespec="seconds"),
            run_root=str(run_root),
            state=store.state,
        )
        return 2
    finally:
        if desktop_guard is not None:
            desktop_guard.stop()


def check_environment(*, project_root: Path, network_timeout: float) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = MasterLogger(project_root / "logs" / "daily-check" / stamp)
    logger.event("check", "start", "开始检查运行环境")
    network_ok = wait_for_network(logger, timeout=network_timeout)
    starter = Path(r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe")
    starter_ok = starter.exists()
    logger.event(
        "check",
        "success" if starter_ok else "error",
        "已找到游戏启动器" if starter_ok else "没有找到游戏启动器",
        path=str(starter),
    )
    result = network_ok and starter_ok
    logger.summary(result="passed" if result else "failed", network=network_ok, starter=starter_ok)
    print(f"environment_ok={result}")
    print(f"log_root={logger.root}")
    return 0 if result else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BrownDust II daily automation once per day.")
    force_group = parser.add_mutually_exclusive_group()
    force_group.add_argument("--force", action="store_true", help="rerun all currently available phases")
    force_group.add_argument(
        "--force-phase",
        choices=DAILY_STAGE_IDS,
        help="rerun only one currently available phase",
    )
    parser.add_argument(
        "--preset",
        choices=DAILY_PRESETS,
        help="require a complete target preset; omitted uses current PC capabilities",
    )
    parser.add_argument(
        "--show-plan",
        action="store_true",
        help="print the plan without running the game",
    )
    parser.add_argument("--check", action="store_true", help="check prerequisites without claiming today")
    parser.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--network-timeout",
        type=float,
        default=0.0,
        help="Google connectivity wait limit in seconds; 0 waits indefinitely",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    project_root = args.project_root.resolve()

    if args.show_plan:
        print(json.dumps(daily_plan_report(args.preset), ensure_ascii=False, indent=2))
        raise SystemExit(0)

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
                force_phase=args.force_phase,
                preset=args.preset,
            )
    except DailyRunError as exc:
        print(f"[每日任务失败] 无法继续运行：{exc}", file=sys.stderr, flush=True)
        raise SystemExit(2) from exc
    raise SystemExit(result)


if __name__ == "__main__":
    main()
