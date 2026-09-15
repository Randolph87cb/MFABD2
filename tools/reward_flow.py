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
from win32_windowpos_click import click_client, swipe_client


NormalizedRegion = tuple[float, float, float, float]
Recognition = Callable[[Image.Image], tuple[bool, dict[str, Any]]]


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
        dry_run=dry_run,
        screenshot=str(path),
    )
    if not dry_run:
        swipe_client(hwnd, start_x, start_y, end_x, end_y)


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
        click_ratio_logged(
            hwnd,
            image,
            (0.50, 0.82),
            key="reward_overlay_dismiss",
            logger=logger,
        )
        def overlay_closed(candidate: Image.Image) -> tuple[bool, dict[str, Any]]:
            found, candidate_details = recognize_reward_overlay_labels(candidate)
            return not found, candidate_details

        closed, image, _details = wait_for_recognition(
            hwnd,
            logger=logger,
            label=f"reward-overlay-closed-{index}",
            recognize=overlay_closed,
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
            return True, "already on home page"
        return False, f"未识别到{source_name}，为避免误点未执行返回"

    click_ratio_logged(
        hwnd,
        image,
        (0.075, 0.045),
        key="reward_back",
        logger=logger,
    )
    reached_home, _image, _details = wait_for_recognition(
        hwnd,
        logger=logger,
        label="reward-back-home",
        recognize=recognize_home_labels,
        timeout=12.0,
    )
    if reached_home:
        return True, f"已从{source_name}返回主页"
    return False, f"从{source_name}点击返回后，12秒内未识别到主页文字"
