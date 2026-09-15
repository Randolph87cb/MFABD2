"""Collect daily and weekly task rewards from fixed, OCR-confirmed controls."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from PIL import Image

from adaptive_wait import AdaptivePoll
from free_gacha import RunLogger, safe_capture_client
from game_text_recognition import recognize_home_labels, recognize_reward_overlay_labels
from home_notifications import detect_home_reward_notification, detect_red_exclamation_badge
from open_game import find_game_window
from reward_flow import click_ratio_logged, recognize_text_at, return_to_home


TASK_HOME_CLICK = (0.356, 0.925)
TASK_TITLE_REGION = (0.170, 0.026, 0.088, 0.061)
TASK_CLAIM_REGION = (0.822, 0.896, 0.064, 0.042)
TASK_CLAIM_CLICK = (0.854, 0.917)
DAILY_BADGE_REGION = (0.205, 0.105, 0.035, 0.055)
# The weekly-tab red diamond is centred near x=0.223 on the current client.
# Keep this region narrow so only that red exclamation can satisfy the check.
WEEKLY_BADGE_REGION = (0.205, 0.165, 0.035, 0.055)
STEP_TIMEOUT = 5.0
CLICK_SETTLE_SECONDS = 1.0
MAX_REWARD_OVERLAYS = 6

PageRecognition = Callable[[Image.Image], tuple[bool, dict[str, object]]]


def _recognize_daily_page(image: Image.Image) -> tuple[bool, dict[str, object]]:
    _found, details = recognize_text_at(image, TASK_TITLE_REGION, ("每日任务", "每日任務"))
    texts = [str(text).replace(" ", "") for text in details.get("texts", [])]
    exact = any(label in text for text in texts for label in ("每日任务", "每日任務"))
    return exact, {**details, "exact_daily_title": exact}


def _recognize_weekly_page(image: Image.Image) -> tuple[bool, dict[str, object]]:
    _found, details = recognize_text_at(image, TASK_TITLE_REGION, ("每周任务", "每週任務"))
    texts = [str(text).replace(" ", "") for text in details.get("texts", [])]
    exact = any(label in text for text in texts for label in ("每周任务", "每週任務"))
    return exact, {**details, "exact_weekly_title": exact}


def _recognize_claim_all(image: Image.Image) -> tuple[bool, dict[str, object]]:
    return recognize_text_at(image, TASK_CLAIM_REGION, ("全部领取", "全部領取", "全部获得", "全部獲得"))


def _capture_logged(hwnd: int, logger: RunLogger, label: str, sample: int) -> Image.Image:
    image = safe_capture_client(hwnd, logger=logger)
    path = logger.save_image(image, f"{label}-{sample:02d}.png")
    logger.event(action="capture", label=label, sample=sample, screenshot=str(path))
    return image


def _wait_for_page(
    hwnd: int,
    *,
    logger: RunLogger,
    label: str,
    recognize_page: PageRecognition,
    timeout: float = STEP_TIMEOUT,
) -> tuple[bool, Image.Image, str]:
    deadline = time.monotonic() + timeout
    poll = AdaptivePoll()
    sample = 0
    while True:
        sample += 1
        image = _capture_logged(hwnd, logger, label, sample)
        found, details = recognize_page(image)
        logger.event(action="recognize_page", label=label, found=found, details=details)
        if found:
            return True, image, f"{label} confirmed"
        now = time.monotonic()
        if now >= deadline:
            return False, image, f"{label} was not OCR-confirmed within {timeout:.0f} seconds"
        time.sleep(poll.next_delay(remaining=deadline - now))


def _wait_after_click(
    hwnd: int,
    *,
    logger: RunLogger,
    label: str,
    recognize_page: PageRecognition,
    wait_for_claim: bool = False,
    timeout: float = STEP_TIMEOUT,
) -> tuple[bool, Image.Image, str]:
    """Wait for a click result without accepting an unchanged claim page early."""
    if CLICK_SETTLE_SECONDS:
        time.sleep(CLICK_SETTLE_SECONDS)
    deadline = time.monotonic() + timeout
    poll = AdaptivePoll()
    sample = 0
    unchanged_claim_page = True
    claim_gone_streak = 0
    while True:
        sample += 1
        image = _capture_logged(hwnd, logger, label, sample)
        overlay, overlay_details = recognize_reward_overlay_labels(image)
        page, page_details = recognize_page(image)
        if wait_for_claim:
            claimable, claim_details = _recognize_claim_all(image)
        else:
            claimable, claim_details = False, {"skipped": "not a claim transition"}
        logger.event(
            action="recognize_click_result",
            label=label,
            sample=sample,
            reward_overlay=overlay,
            overlay_details=overlay_details,
            page=page,
            page_details=page_details,
            claimable=claimable,
            claim_details=claim_details,
        )
        if overlay:
            return True, image, "reward_overlay"
        if page and not wait_for_claim:
            return True, image, "page"
        if wait_for_claim and page and not claimable:
            claim_gone_streak += 1
            if claim_gone_streak >= 2:
                return True, image, "claim_button_gone_stable"
        else:
            claim_gone_streak = 0
        if wait_for_claim and not (page and claimable):
            unchanged_claim_page = False
        now = time.monotonic()
        if now >= deadline:
            if wait_for_claim and unchanged_claim_page and page and claimable:
                return (
                    False,
                    image,
                    f"{label}：领取按钮点击后 {timeout:.0f} 秒内始终停留在原页面且领取文字仍存在，点击未生效",
                )
            return False, image, f"{label} had no recognized result within {timeout:.0f} seconds"
        time.sleep(poll.next_delay(remaining=deadline - now))


def _dismiss_reward_overlays(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
    recognize_page: PageRecognition,
) -> tuple[bool, Image.Image, str]:
    """Close consecutive results, with one bounded wait and a hard loop cap per click."""
    dismissed = 0
    for index in range(1, MAX_REWARD_OVERLAYS + 1):
        overlay, details = recognize_reward_overlay_labels(image)
        logger.event(action="recognize_reward_overlay", index=index, found=overlay, details=details)
        if not overlay:
            return True, image, f"dismissed {dismissed} reward overlays"
        click_ratio_logged(
            hwnd,
            image,
            (0.50, 0.82),
            key="task_reward_overlay_dismiss",
            logger=logger,
        )
        dismissed += 1
        ok, image, result = _wait_after_click(
            hwnd,
            logger=logger,
            label=f"task-overlay-{index}",
            recognize_page=recognize_page,
        )
        if not ok:
            return False, image, result
        if result == "page":
            return True, image, f"dismissed {dismissed} reward overlays"

    overlay, _details = recognize_reward_overlay_labels(image)
    if overlay:
        return False, image, f"reward overlay limit reached ({MAX_REWARD_OVERLAYS})"
    return True, image, f"dismissed {dismissed} reward overlays"


def _claim_page_once(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
    page_name: str,
    recognize_page: PageRecognition,
) -> tuple[bool, Image.Image, str]:
    claimable, details = _recognize_claim_all(image)
    logger.event(action="recognize_claim_all", page=page_name, found=claimable, details=details)
    if not claimable:
        return True, image, f"{page_name} has no claim-all reward"

    click_ratio_logged(
        hwnd,
        image,
        TASK_CLAIM_CLICK,
        key=f"task_{page_name}_claim_all",
        logger=logger,
    )
    ok, image, result = _wait_after_click(
        hwnd,
        logger=logger,
        label=f"task-{page_name}-claim",
        recognize_page=recognize_page,
        wait_for_claim=True,
    )
    if not ok:
        return False, image, result
    if result == "claim_button_gone":
        return True, image, f"{page_name} claim-all disappeared without a reward overlay"
    return _dismiss_reward_overlays(
        hwnd,
        image,
        logger=logger,
        recognize_page=recognize_page,
    )


def run_task_rewards(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Collect all available daily rewards, then marked weekly rewards."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="task_rewards", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    try:
        image = safe_capture_client(hwnd, logger=logger)
        is_home, home_details = recognize_home_labels(image)
        has_notification = False
        notification_details: dict[str, object] = {"skipped": "homepage text not confirmed"}
        if is_home:
            has_notification, notification_details = detect_home_reward_notification(image, "tasks")
        logger.event(
            action="task_entry_check",
            home=is_home,
            home_details=home_details,
            found=has_notification,
            details=notification_details,
        )
        if not is_home:
            reason = "task rewards require fixed-position home text"
            logger.failure(reason)
            return False, reason
        if not has_notification:
            reason = "skipped: tasks have no home reward notification"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason

        click_ratio_logged(
            hwnd,
            image,
            TASK_HOME_CLICK,
            key="home_tasks",
            logger=logger,
            dry_run=dry_run,
        )
        if dry_run:
            reason = "completed: dry-run planned task reward entry"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason

        ok, image, reason = _wait_for_page(
            hwnd,
            logger=logger,
            label="daily-task-page",
            recognize_page=_recognize_daily_page,
        )
        if not ok:
            logger.failure(reason)
            return False, reason

        daily, daily_details = detect_red_exclamation_badge(image, DAILY_BADGE_REGION)
        logger.event(
            action="detect_notification",
            target="daily_tasks",
            found=daily,
            details=daily_details,
        )
        if daily:
            ok, image, reason = _claim_page_once(
                hwnd,
                image,
                logger=logger,
                page_name="daily",
                recognize_page=_recognize_daily_page,
            )
            if not ok:
                logger.failure(reason)
                return False, reason

        weekly, weekly_details = detect_red_exclamation_badge(image, WEEKLY_BADGE_REGION)
        logger.event(action="detect_notification", target="weekly_tasks", found=weekly, details=weekly_details)
        if weekly:
            badge_center = weekly_details.get("center")
            if not isinstance(badge_center, tuple | list) or len(badge_center) != 2:
                reason = "已识别每周任务红点，但没有得到可验证的红点位置，未点击"
                logger.failure(reason)
                return False, reason
            weekly_tab_click = (
                max(0.0, float(badge_center[0]) - 0.087),
                float(badge_center[1]),
            )
            click_ratio_logged(
                hwnd,
                image,
                weekly_tab_click,
                key="weekly_task_tab",
                logger=logger,
            )
            ok, image, reason = _wait_for_page(
                hwnd,
                logger=logger,
                label="weekly-task-page",
                recognize_page=_recognize_weekly_page,
            )
            if not ok:
                logger.failure(reason)
                return False, reason
            ok, image, reason = _claim_page_once(
                hwnd,
                image,
                logger=logger,
                page_name="weekly",
                recognize_page=_recognize_weekly_page,
            )
            if not ok:
                logger.failure(reason)
                return False, reason

        source_recognizer = _recognize_weekly_page if weekly else _recognize_daily_page
        ok, reason = return_to_home(
            hwnd,
            logger=logger,
            recognize_source=source_recognizer,
            source_name="任务页面",
            notification_target="tasks",
            notification_name="任务",
        )
        if not ok:
            logger.failure(reason)
            return False, reason
        reason = (
            "completed: task rewards processed; "
            f"daily={'yes' if daily else 'no'}; weekly={'yes' if weekly else 'no'}"
        )
        logger.event(action="stop", result="success", reason=reason)
        return True, reason
    except Exception as exc:  # noqa: BLE001 - automation failures are persisted for review.
        reason = f"task rewards failed: {exc}"
        logger.failure(reason)
        return False, reason
