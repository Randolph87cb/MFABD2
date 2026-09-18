"""Shared text-first primitives for reward-collection flows."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PIL import Image

from adaptive_wait import AdaptivePoll
from free_gacha import RunLogger, safe_capture_client
from game_text_recognition import (
    LabelRecognitionSession,
    recognize_home_labels,
    recognize_reward_overlay_labels,
)
from home_notifications import detect_home_reward_notification
from win32_windowpos_click import click_client, swipe_client, swipe_client_foreground


NormalizedRegion = tuple[float, float, float, float]
Recognition = Callable[[Image.Image], tuple[bool, dict[str, Any]]]
MAX_REWARD_DISMISS_ATTEMPTS = 2


def recognize_text_at(
    image: Image.Image,
    region: NormalizedRegion,
    labels: tuple[str, ...],
    *,
    minimum_matches: int = 1,
) -> tuple[bool, dict[str, Any]]:
    """Recognize expected text only when its OCR centre is inside ``region``."""
    grouped, matches, error = LabelRecognitionSession(image).recognize(
        {"anchor": {"region": region, "labels": labels}}
    )
    if error is not None:
        return False, error
    matched = matches.get("anchor", [])
    return len(matched) >= minimum_matches, {
        "available": True,
        "region": region,
        "texts": grouped.get("anchor", []),
        "matches": matched,
        "minimum_matches": minimum_matches,
    }


def click_ratio_logged(
    hwnd: int,
    image: Image.Image,
    point: tuple[float, float],
    *,
    key: str,
    logger: RunLogger,
    dry_run: bool = False,
) -> None:
    """Click a normalized point and preserve the annotated evidence."""
    x = int(image.width * point[0])
    y = int(image.height * point[1])
    click_index = logger.next_click_index()
    marked = logger.save_click_image(
        image,
        f"click-{click_index:03d}-{key}.png",
        x=x,
        y=y,
        key=key,
        dry_run=dry_run,
    )
    logger.event(
        action="click",
        key=key,
        x=x,
        y=y,
        dry_run=dry_run,
        screenshot=str(marked),
    )
    if not dry_run:
        click_client(hwnd, x, y)


def swipe_ratio_logged(
    hwnd: int,
    image: Image.Image,
    begin: tuple[float, float],
    end: tuple[float, float],
    *,
    key: str,
    logger: RunLogger,
    dry_run: bool = False,
    duration: float = 0.5,
    end_hold: float = 0.0,
    foreground: bool = False,
) -> None:
    """Swipe between normalized points while logging the source screenshot."""
    start_x, start_y = int(image.width * begin[0]), int(image.height * begin[1])
    end_x, end_y = int(image.width * end[0]), int(image.height * end[1])
    path = logger.save_image(image, f"swipe-{key}.png")
    logger.event(
        action="swipe",
        key=key,
        begin=(start_x, start_y),
        end=(end_x, end_y),
        duration=duration,
        end_hold=end_hold,
        foreground=foreground,
        dry_run=dry_run,
        screenshot=str(path),
    )
    if not dry_run:
        swipe = swipe_client_foreground if foreground else swipe_client
        swipe(
            hwnd,
            start_x,
            start_y,
            end_x,
            end_y,
            duration=duration,
            end_hold=end_hold,
        )


def wait_for_home_notification_clear(
    hwnd: int,
    *,
    logger: RunLogger,
    target: str,
    target_name: str,
    timeout: float = 6.0,
) -> tuple[bool, str]:
    """Require home OCR and the exact target red exclamation to be gone."""

    def cleared(image: Image.Image) -> tuple[bool, dict[str, Any]]:
        is_home, home_details = recognize_home_labels(image)
        if not is_home:
            return False, {"home": False, "home_details": home_details}
        has_badge, badge_details = detect_home_reward_notification(image, target)
        return not has_badge, {
            "home": True,
            "home_details": home_details,
            "has_notification": has_badge,
            "notification_details": badge_details,
        }

    ok, _image, _details = wait_for_recognition(
        hwnd,
        logger=logger,
        label=f"home-{target}-notification-cleared",
        recognize=cleared,
        timeout=timeout,
    )
    if ok:
        return True, f"已确认主页{target_name}红点消失"
    return False, f"已返回主页，但{target_name}红点仍存在，不能判定该环节完成"


def wait_for_recognition(
    hwnd: int,
    *,
    logger: RunLogger,
    label: str,
    recognize: Recognition,
    timeout: float = 20.0,
) -> tuple[bool, Image.Image, dict[str, Any]]:
    """Poll one step until its text recognizer succeeds or its own timeout expires."""
    deadline = time.monotonic() + timeout
    poll = AdaptivePoll()
    last_image: Image.Image | None = None
    last_details: dict[str, Any] = {}
    sample = 0
    while True:
        image = safe_capture_client(hwnd, logger=logger)
        last_image = image
        sample += 1
        found, details = recognize(image)
        last_details = details
        screenshot = logger.save_image(image, f"{label}-{sample:02d}.png")
        logger.event(
            action="wait_recognition",
            label=label,
            sample=sample,
            found=found,
            details=details,
            screenshot=str(screenshot),
        )
        if found:
            return True, image, details
        now = time.monotonic()
        if now >= deadline:
            return False, image, details
        time.sleep(poll.next_delay(remaining=deadline - now))


def dismiss_reward_overlays(
    hwnd: int,
    *,
    logger: RunLogger,
    max_overlays: int = 6,
) -> tuple[bool, Image.Image, str]:
    """Dismiss consecutive OCR-confirmed reward overlays, stopping on the next page."""
    image = safe_capture_client(hwnd, logger=logger)
    for index in range(1, max_overlays + 1):
        is_overlay, details = recognize_reward_overlay_labels(image)
        logger.event(
            action="recognize_reward_overlay",
            index=index,
            found=is_overlay,
            details=details,
        )
        if not is_overlay:
            return True, image, f"dismissed {index - 1} reward overlays"
        def overlay_closed(candidate: Image.Image) -> tuple[bool, dict[str, Any]]:
            found, candidate_details = recognize_reward_overlay_labels(candidate)
            return not found, candidate_details

        closed = False
        for attempt in range(1, MAX_REWARD_DISMISS_ATTEMPTS + 1):
            click_ratio_logged(
                hwnd,
                image,
                (0.50, 0.82),
                key="reward_overlay_dismiss",
                logger=logger,
            )
            closed, image, _details = wait_for_recognition(
                hwnd,
                logger=logger,
                label=f"reward-overlay-closed-{index}-attempt-{attempt}",
                recognize=overlay_closed,
            )
            if closed:
                break
            if attempt >= MAX_REWARD_DISMISS_ATTEMPTS:
                break
            logger.event(
                action="retry_click",
                key="reward_overlay_dismiss",
                attempt=attempt + 1,
                reason="OCR-confirmed reward overlay remained after the click",
            )
        if not closed:
            return False, image, "reward overlay did not close"
    return False, image, f"more than {max_overlays} reward overlays appeared"


def return_to_home(
    hwnd: int,
    *,
    logger: RunLogger,
    recognize_source: Recognition,
    source_name: str,
    notification_target: str | None = None,
    notification_name: str | None = None,
) -> tuple[bool, str]:
    """Prefer the verified source page, then return once and require fixed home OCR."""
    image = safe_capture_client(hwnd, logger=logger)
    is_source, source_details = recognize_source(image)
    logger.event(
        action="recognize_reward_return_source",
        source=source_name,
        found=is_source,
        details=source_details,
    )
    if not is_source:
        is_home, home_details = recognize_home_labels(image)
        logger.event(action="recognize_home", found=is_home, details=home_details)
        if is_home:
            if notification_target is None:
                return True, "already on home page"
            return wait_for_home_notification_clear(
                hwnd,
                logger=logger,
                target=notification_target,
                target_name=notification_name or notification_target,
            )
        return False, f"未识别到{source_name}，为避免误点未执行返回"

    click_ratio_logged(
        hwnd,
        image,
        (0.075, 0.045),
        key="reward_back",
        logger=logger,
    )
    def home_after_source_closed(candidate: Image.Image) -> tuple[bool, dict[str, Any]]:
        is_home, home_details = recognize_home_labels(candidate)
        source_still_open, current_source_details = recognize_source(candidate)
        return is_home and not source_still_open, {
            "home_found": is_home,
            "home": home_details,
            "source_found": source_still_open,
            "source": current_source_details,
            "requirements": "fixed home text is present and the source page is absent",
        }

    reached_home, _image, _details = wait_for_recognition(
        hwnd,
        logger=logger,
        label="reward-back-home",
        recognize=home_after_source_closed,
        timeout=12.0,
    )
    if reached_home:
        if notification_target is None:
            return True, f"已从{source_name}返回主页"
        cleared, clear_reason = wait_for_home_notification_clear(
            hwnd,
            logger=logger,
            target=notification_target,
            target_name=notification_name or notification_target,
        )
        if not cleared:
            return False, clear_reason
        return True, f"已从{source_name}返回主页；{clear_reason}"
    return False, f"从{source_name}点击返回后，12秒内未识别到主页文字"
