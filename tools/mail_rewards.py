"""Collect normal and product/month-card mail from fixed OCR-confirmed controls."""

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


MAIL_HOME_CLICK = (0.842, 0.055)
MAIL_PAGE_REGION = (0.163, 0.021, 0.107, 0.075)
MAIL_CLAIM_REGION = (0.822, 0.896, 0.064, 0.042)
MAIL_CLAIM_CLICK = (0.854, 0.917)
GENERAL_BADGE_REGION = (0.205, 0.105, 0.035, 0.055)
PRODUCT_BADGE_REGION = (0.144, 0.203, 0.145, 0.086)
PRODUCT_TAB_CLICK = (0.216, 0.246)
STEP_TIMEOUT = 5.0
CLICK_SETTLE_SECONDS = 1.0
MAX_REWARD_OVERLAYS = 6

PageRecognition = Callable[[Image.Image], tuple[bool, dict[str, object]]]


def _recognize_mail_page(image: Image.Image) -> tuple[bool, dict[str, object]]:
    return recognize_text_at(image, MAIL_PAGE_REGION, ("邮箱", "郵箱", "记录", "記錄"))


def _recognize_claim_all(image: Image.Image) -> tuple[bool, dict[str, object]]:
    return recognize_text_at(image, MAIL_CLAIM_REGION, ("全部领取", "全部領取", "全部获得", "全部獲得"))


def _capture_logged(hwnd: int, logger: RunLogger, label: str, sample: int) -> Image.Image:
    image = safe_capture_client(hwnd, logger=logger)
    path = logger.save_image(image, f"{label}-{sample:02d}.png")
    logger.event(action="capture", label=label, sample=sample, screenshot=str(path))
    return image


def _wait_for_mail_page(
    hwnd: int,
    *,
    logger: RunLogger,
    label: str,
    timeout: float = STEP_TIMEOUT,
) -> tuple[bool, Image.Image, str]:
    deadline = time.monotonic() + timeout
    poll = AdaptivePoll()
    sample = 0
    while True:
        sample += 1
        image = _capture_logged(hwnd, logger, label, sample)
        found, details = _recognize_mail_page(image)
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
    wait_for_claim: bool = False,
    timeout: float = STEP_TIMEOUT,
) -> tuple[bool, Image.Image, str]:
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
        page, page_details = _recognize_mail_page(image)
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
) -> tuple[bool, Image.Image, str]:
    dismissed = 0
    for index in range(1, MAX_REWARD_OVERLAYS + 1):
        overlay, details = recognize_reward_overlay_labels(image)
        logger.event(action="recognize_reward_overlay", index=index, found=overlay, details=details)
        if not overlay:
            return True, image, f"dismissed {dismissed} mail reward overlays"
        click_ratio_logged(
            hwnd,
            image,
            (0.50, 0.82),
            key="mail_reward_overlay_dismiss",
            logger=logger,
        )
        dismissed += 1
        ok, image, result = _wait_after_click(
            hwnd,
            logger=logger,
            label=f"mail-overlay-{index}",
        )
        if not ok:
            return False, image, result
        if result == "page":
            return True, image, f"dismissed {dismissed} mail reward overlays"

    overlay, _details = recognize_reward_overlay_labels(image)
    if overlay:
        return False, image, f"mail reward overlay limit reached ({MAX_REWARD_OVERLAYS})"
    return True, image, f"dismissed {dismissed} mail reward overlays"


def _claim_mail_once(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
    mailbox: str,
) -> tuple[bool, Image.Image, str]:
    claimable, details = _recognize_claim_all(image)
    logger.event(action="recognize_claim_all", mailbox=mailbox, found=claimable, details=details)
    if not claimable:
        return True, image, f"{mailbox} mail has no claim-all reward"
    click_ratio_logged(
        hwnd,
        image,
        MAIL_CLAIM_CLICK,
        key=f"mail_{mailbox}_claim_all",
        logger=logger,
    )
    ok, image, result = _wait_after_click(
        hwnd,
        logger=logger,
        label=f"mail-{mailbox}-claim",
        wait_for_claim=True,
    )
    if not ok:
        return False, image, result
    if result == "claim_button_gone":
        return True, image, f"{mailbox} claim-all disappeared without a reward overlay"
    return _dismiss_reward_overlays(hwnd, image, logger=logger)


def _wait_for_product_tab(
    hwnd: int,
    *,
    logger: RunLogger,
    timeout: float = STEP_TIMEOUT,
) -> tuple[bool, Image.Image, str]:
    """Require a settled mail page whose product-tab notification has cleared."""
    if CLICK_SETTLE_SECONDS:
        time.sleep(CLICK_SETTLE_SECONDS)
    deadline = time.monotonic() + timeout
    poll = AdaptivePoll()
    sample = 0
    while True:
        sample += 1
        image = _capture_logged(hwnd, logger, "product-mail-switch", sample)
        page, page_details = _recognize_mail_page(image)
        red, red_details = detect_red_exclamation_badge(image, PRODUCT_BADGE_REGION)
        logger.event(
            action="product_tab_switch_state",
            before_red=True,
            after_red=red,
            page=page,
            page_details=page_details,
            red_details=red_details,
            sample=sample,
        )
        if page and not red:
            return True, image, "product mail tab settled and red notification cleared"
        now = time.monotonic()
        if now >= deadline:
            return False, image, f"product mail tab did not settle within {timeout:.0f} seconds"
        time.sleep(poll.next_delay(remaining=deadline - now))


def run_mail_rewards(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Collect general mail, then marked product/month-card mail, and return home."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="mail_rewards", dry_run=dry_run)
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
            has_notification, notification_details = detect_home_reward_notification(image, "mail")
        logger.event(
            action="mail_entry_check",
            home=is_home,
            home_details=home_details,
            found=has_notification,
            details=notification_details,
        )
        if not is_home:
            reason = "mail rewards require fixed-position home text"
            logger.failure(reason)
            return False, reason
        if not has_notification:
            reason = "skipped: mail has no home reward notification"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason

        click_ratio_logged(
            hwnd,
            image,
            MAIL_HOME_CLICK,
            key="home_mail",
            logger=logger,
            dry_run=dry_run,
        )
        if dry_run:
            reason = "completed: dry-run planned mail reward entry"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason

        ok, image, reason = _wait_for_mail_page(hwnd, logger=logger, label="mail-page")
        if not ok:
            logger.failure(reason)
            return False, reason

        general, general_details = detect_red_exclamation_badge(image, GENERAL_BADGE_REGION)
        logger.event(
            action="detect_notification",
            target="general_mail",
            found=general,
            details=general_details,
        )
        if general:
            ok, image, reason = _claim_mail_once(
                hwnd,
                image,
                logger=logger,
                mailbox="general",
            )
            if not ok:
                logger.failure(reason)
                return False, reason

        product, product_details = detect_red_exclamation_badge(image, PRODUCT_BADGE_REGION)
        logger.event(action="detect_notification", target="product_mail", found=product, details=product_details)
        if product:
            click_ratio_logged(
                hwnd,
                image,
                PRODUCT_TAB_CLICK,
                key="product_mail_tab",
                logger=logger,
            )
            ok, image, reason = _wait_for_product_tab(hwnd, logger=logger)
            if not ok:
                logger.failure(reason)
                return False, reason
            ok, image, reason = _claim_mail_once(
                hwnd,
                image,
                logger=logger,
                mailbox="product",
            )
            if not ok:
                logger.failure(reason)
                return False, reason

        ok, reason = return_to_home(
            hwnd,
            logger=logger,
            recognize_source=_recognize_mail_page,
            source_name="邮箱页面",
            notification_target="mail",
            notification_name="邮件",
        )
        if not ok:
            logger.failure(reason)
            return False, reason
        reason = (
            "completed: mail rewards processed; "
            f"general={'yes' if general else 'no'}; product={'yes' if product else 'no'}"
        )
        logger.event(action="stop", result="success", reason=reason)
        return True, reason
    except Exception as exc:  # noqa: BLE001 - automation failures are persisted for review.
        reason = f"mail rewards failed: {exc}"
        logger.failure(reason)
        return False, reason
