"""Collect Battle Pass rewards using fixed text anchors and red badges."""

from __future__ import annotations

import argparse
import re
import time
from collections.abc import Callable
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from PIL import Image

from free_gacha import RunLogger, safe_capture_client
from game_text_recognition import LabelRecognitionSession, recognize_home_labels, recognize_reward_overlay_labels
from home_notifications import detect_home_reward_notification, find_red_exclamation_badges
from open_game import find_game_window
from reward_flow import (
    click_ratio_logged,
    dismiss_reward_overlays,
    recognize_text_at,
    return_to_home,
    swipe_ratio_logged,
)


NormalizedRegion = tuple[float, float, float, float]
Recognition = Callable[[Image.Image], tuple[bool, dict[str, Any]]]

# Pass.json was authored against a 1280 x 720 client. Keep its OCR and badge
# rectangles as normalized fixed positions so they also work on scaled clients.
REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 720


def _region(x: int, y: int, width: int, height: int) -> NormalizedRegion:
    return (x / REFERENCE_WIDTH, y / REFERENCE_HEIGHT, width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT)


PASS_TITLE_REGION = _region(179, 6, 148, 65)
PASS_LIST_BADGE_UPSTREAM_REGION = _region(262, 130, 40, 478)
PASS_LEVEL_UPSTREAM_REGION = _region(620, 167, 53, 32)
PASS_BASIC_UPSTREAM_REGION = _region(760, 164, 202, 36)
PASS_CLAIM_ALL_REGION = _region(757, 514, 361, 66)

# Existing 2567 x 1446 run evidence shows the centred modal shifted relative to
# upstream's viewport. These are independent, fixed log-calibrated fallbacks.
PASS_LIST_BADGE_REGION = (0.250, 0.169, 0.035, 0.665)
PASS_LEVEL_LOG_REGION = (0.470, 0.265, 0.075, 0.070)
PASS_BASIC_LOG_REGION = (0.600, 0.265, 0.115, 0.070)
PASS_CLAIM_ALL_LOG_REGION = (0.554, 0.669, 0.264, 0.086)
PASS_ITEM_DETAIL_TEXT_REGION = (0.180, 0.240, 0.460, 0.500)
PASS_DETAIL_IDENTITY_REGION = (0.280, 0.220, 0.390, 0.500)
# Interaction points are calibrated from this project's 2567x1446 run logs.
# The upstream rectangles remain OCR search areas, but its action targets do
# not line up with the current client layout.
HOME_PASS_POINT = (0.873, 0.255)
PASS_TASK_LIST_POINT = (0.830, 0.629)
PASS_CLAIM_ALL_POINT = (0.744, 0.709)
PASS_ITEM_POPUP_CLOSE_POINT = (0.740, 0.302)
# The red badge is only a row locator. Click the card body at a fixed X so the
# badge itself never becomes the interaction target.
PASS_CARD_SELECT_X = 224 / REFERENCE_WIDTH
PASS_SWIPE_BEGIN = (216 / REFERENCE_WIDTH, 518 / REFERENCE_HEIGHT)
PASS_SWIPE_END = (214 / REFERENCE_WIDTH, 190 / REFERENCE_HEIGHT)

MAX_ACTION_SAMPLES = 4
ACTION_SAMPLE_DELAY = 0.75
MAX_PASS_SWIPES = 4
MAX_PASS_LOOP_STEPS = 24
MAX_REWARD_OVERLAYS = 6
MAX_PASS_TASK_LIST_ATTEMPTS = 2

# Kept for callers which imported the old limit name.
MAX_PASS_CLAIM_CYCLES = MAX_PASS_LOOP_STEPS


def _has_pass_item_popup_close(image: object) -> bool:
    """Recognize an item detail from fixed-position descriptive text."""
    found, _details = recognize_text_at(
        image,
        PASS_ITEM_DETAIL_TEXT_REGION,
        ("拥有", "使用处", "查看获取途径"),
    )
    return found


def _confirm_pass_reward_page(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Confirm the pass page solely from fixed-position OCR of ``通行证``."""
    return recognize_text_at(image, PASS_TITLE_REGION, ("通行证",))


def _confirm_selected_pass_panel(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Confirm the selected pass panel from either upstream fixed text anchor."""
    grouped, matches, error = LabelRecognitionSession(image).recognize(
        {
            "level_upstream": {"region": PASS_LEVEL_UPSTREAM_REGION, "labels": ("LEVEL",)},
            "basic_upstream": {"region": PASS_BASIC_UPSTREAM_REGION, "labels": ("基础",)},
            "level_log": {"region": PASS_LEVEL_LOG_REGION, "labels": ("LEVEL",)},
            "basic_log": {"region": PASS_BASIC_LOG_REGION, "labels": ("基础",)},
        }
    )
    if error is not None:
        return False, error
    found = any(bool(group_matches) for group_matches in matches.values())
    return found, {
        "available": True,
        "texts": grouped,
        "matches": matches,
        "requirements": {"any_of": ("LEVEL", "基础")},
    }


def _confirm_claim_all(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    grouped, matches, error = LabelRecognitionSession(image).recognize(
        {
            "upstream": {"region": PASS_CLAIM_ALL_REGION, "labels": ("全部获得",)},
            "log_calibrated": {"region": PASS_CLAIM_ALL_LOG_REGION, "labels": ("全部获得",)},
        }
    )
    if error is not None:
        return False, error
    source = "upstream" if matches.get("upstream") else "log_calibrated"
    found = bool(matches.get("upstream") or matches.get("log_calibrated"))
    return found, {
        "available": True,
        "source": source if found else "none",
        "texts": grouped,
        "matches": matches,
    }


def _confirm_pass_task_page(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Distinguish the task page using only fixed-position text anchors."""
    has_overview, overview_details = _confirm_selected_pass_panel(image)
    has_claim, claim_details = _confirm_claim_all(image)
    found = has_claim and not has_overview
    return found, {
        "available": True,
        "overview_text_found": has_overview,
        "claim_text_found": has_claim,
        "overview": overview_details,
        "claim": claim_details,
        "requirements": "全部获得 is present while fixed LEVEL/基础 overview text is absent",
    }


def _find_pass_badges(image: Image.Image) -> list[dict[str, Any]]:
    """Search the upstream column plus the independently log-calibrated column."""
    candidates = [
        *find_red_exclamation_badges(image, PASS_LIST_BADGE_UPSTREAM_REGION),
        *find_red_exclamation_badges(image, PASS_LIST_BADGE_REGION),
    ]
    unique: dict[tuple[int, int], dict[str, Any]] = {}
    for candidate in candidates:
        unique[_badge_fingerprint(candidate)] = candidate
    return sorted(unique.values(), key=lambda item: (item["center"][1], item["center"][0]))


def _is_reward_overlay(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    return recognize_reward_overlay_labels(image)


def _confirm_claim_effect(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Confirm claim effect from settlement OCR or disappeared button text."""
    is_overlay, overlay_details = _is_reward_overlay(image)
    claim_present, claim_details = _confirm_claim_all(image)
    claim_text_disappeared = (
        not claim_present
        and claim_details.get("available") is True
        and "error" not in claim_details
    )
    return is_overlay or claim_text_disappeared, {
        "reward_overlay": is_overlay,
        "overlay": overlay_details,
        "claim_text_present": claim_present,
        "claim": claim_details,
        "requirements": "reward settlement or disappeared 全部获得 text",
    }


def _click_logged(
    hwnd: int,
    image: Image.Image,
    point: tuple[float, float],
    *,
    key: str,
    logger: RunLogger,
    dry_run: bool = False,
) -> None:
    """Compatibility wrapper around the shared annotated fixed click."""
    click_ratio_logged(hwnd, image, point, key=key, logger=logger, dry_run=dry_run)


def _capture_until(
    hwnd: int,
    *,
    logger: RunLogger,
    label: str,
    recognize: Recognition,
    samples: int = MAX_ACTION_SAMPLES,
    required_consecutive: int = 1,
) -> tuple[bool, Image.Image, dict[str, Any]]:
    """Use a small sample cap so a stale control cannot stall the whole run."""
    last_image: Image.Image | None = None
    last_details: dict[str, Any] = {}
    found_streak = 0
    for sample in range(1, samples + 1):
        if sample > 1:
            time.sleep(ACTION_SAMPLE_DELAY)
        image = safe_capture_client(hwnd, logger=logger)
        last_image = image
        found, details = recognize(image)
        last_details = details
        path = logger.save_image(image, f"{label}-{sample:02d}.png")
        logger.event(
            action="wait_recognition",
            label=label,
            sample=sample,
            found=found,
            details=details,
            screenshot=str(path),
        )
        found_streak = found_streak + 1 if found else 0
        if found_streak >= required_consecutive:
            return True, image, details
    assert last_image is not None
    return False, last_image, last_details


def _close_item_detail(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
) -> tuple[bool, Image.Image, str]:
    if not _has_pass_item_popup_close(image):
        return True, image, "no item detail"
    _click_logged(hwnd, image, PASS_ITEM_POPUP_CLOSE_POINT, key="pass_item_popup_close", logger=logger)
    closed, after, _details = _capture_until(
        hwnd,
        logger=logger,
        label="pass-item-detail-closed",
        recognize=lambda candidate: (
            not _has_pass_item_popup_close(candidate),
            {"popup_close_found": _has_pass_item_popup_close(candidate)},
        ),
        samples=3,
    )
    return closed, after, "item detail closed" if closed else "pass item detail did not close"


def _dismiss_reward_results(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
) -> tuple[bool, Image.Image, int, str]:
    """Delegate OCR confirmation and single-click waiting to the shared flow."""
    is_overlay, details = _is_reward_overlay(image)
    logger.event(action="recognize_reward_overlay", index=0, found=is_overlay, details=details)
    if not is_overlay:
        return False, image, 0, "pass reward settlement was not OCR-confirmed"
    ok, after, reason = dismiss_reward_overlays(
        hwnd,
        logger=logger,
        max_overlays=MAX_REWARD_OVERLAYS,
    )
    return ok, after, 0, reason


def _badge_fingerprint(badge: dict[str, Any]) -> tuple[int, int]:
    center_x, center_y = badge["center"]
    return round(float(center_x) * 1000), round(float(center_y) * 1000)


def _pass_card_click_point(badge: dict[str, Any]) -> tuple[float, float]:
    """Use the badge's Y only; X stays safely inside the pass card."""
    _badge_x, center_y = badge["center"]
    return PASS_CARD_SELECT_X, float(center_y)


def _normalized_pass_identity(value: object) -> str:
    text = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", str(value)).upper()
    text = re.sub(r"D\d+", "", text)
    for suffix in ("特别赛季通行证", "角色通行证", "赛季通行证", "通行证"):
        text = text.replace(suffix, "")
    return text


def _read_texts_at(image: Image.Image, region: NormalizedRegion) -> tuple[list[str], dict[str, Any]]:
    grouped, _matches, error = LabelRecognitionSession(image).recognize(
        {"identity": {"region": region, "labels": ()}}
    )
    if error is not None:
        return [], error
    return grouped.get("identity", []), {"available": True, "texts": grouped.get("identity", [])}


def _pass_identity_matches(card_texts: list[str], detail_texts: list[str]) -> bool:
    cards = [value for text in card_texts if len(value := _normalized_pass_identity(text)) >= 4]
    details = [value for text in detail_texts if len(value := _normalized_pass_identity(text)) >= 4]
    for card in cards:
        for detail in details:
            if card in detail or detail in card:
                return True
            if SequenceMatcher(None, card, detail).ratio() >= 0.72:
                return True
    return False


def _read_pass_card_identity(
    image: Image.Image,
    badge: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    _x, center_y = badge["center"]
    region = (0.130, max(0.120, float(center_y) - 0.050), 0.160, 0.100)
    return _read_texts_at(image, region)


def _wait_for_pass_selection(
    hwnd: int,
    badge: dict[str, Any],
    *,
    expected_identity: list[str],
    logger: RunLogger,
    step: int,
) -> tuple[bool, Image.Image, dict[str, Any]]:
    """Require evidence that the clicked card, rather than the old panel, won."""

    def selection_effect(candidate: Image.Image) -> tuple[bool, dict[str, Any]]:
        is_page, page_details = _confirm_pass_reward_page(candidate)
        has_panel, panel_details = _confirm_selected_pass_panel(candidate)
        has_task_page, task_details = _confirm_pass_task_page(candidate)
        detail_texts, identity_details = _read_texts_at(candidate, PASS_DETAIL_IDENTITY_REGION)
        identity_matches = _pass_identity_matches(expected_identity, detail_texts)
        state = "overview" if has_panel else "task_page" if has_task_page else "unknown"
        return is_page and state != "unknown" and identity_matches, {
            "page": page_details,
            "panel": panel_details,
            "task_page": task_details,
            "state": state,
            "expected_identity": expected_identity,
            "detail_identity": identity_details,
            "identity_matches": identity_matches,
            "requirements": "fixed pass/page state text and matching card/detail identity text",
        }

    return _capture_until(
        hwnd,
        logger=logger,
        label=f"pass-selected-{step:02d}",
        recognize=selection_effect,
    )


def _open_pass_task_page(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
    label: str,
) -> tuple[bool, Image.Image, dict[str, Any]]:
    """Retry one dropped task-tab click while the overview is still confirmed."""
    current = image
    last_details: dict[str, Any] = {}
    for attempt in range(1, MAX_PASS_TASK_LIST_ATTEMPTS + 1):
        _click_logged(
            hwnd,
            current,
            PASS_TASK_LIST_POINT,
            key="pass_task_list",
            logger=logger,
        )
        found, current, last_details = _capture_until(
            hwnd,
            logger=logger,
            label=f"{label}-attempt-{attempt}",
            recognize=_confirm_pass_task_page,
            samples=1 if attempt < MAX_PASS_TASK_LIST_ATTEMPTS else MAX_ACTION_SAMPLES,
        )
        if found:
            return True, current, last_details
        if not last_details.get("overview_text_found"):
            break
        logger.event(
            action="retry_click",
            key="pass_task_list",
            attempt=attempt + 1,
            reason="task tab click left the confirmed overview unchanged",
        )
    return False, current, last_details


def _collect_from_pass_page(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
) -> tuple[bool, str]:
    claimed = 0
    swipes = 0
    ignored_badges: set[tuple[int, int]] = set()
    current = image

    for step in range(1, MAX_PASS_LOOP_STEPS + 1):
        is_page, page_details = _confirm_pass_reward_page(current)
        logger.event(action="recognize_pass_page", step=step, found=is_page, details=page_details)
        if not is_page:
            if _has_pass_item_popup_close(current):
                ok, current, reason = _close_item_detail(hwnd, current, logger=logger)
                if ok:
                    continue
                return False, reason
            return False, "pass page title was not found at its fixed position"

        badges = _find_pass_badges(current)
        actionable = [badge for badge in badges if _badge_fingerprint(badge) not in ignored_badges]
        logger.event(
            action="detect_notification",
            step=step,
            target="pass_list",
            badges=badges,
            ignored=sorted(ignored_badges),
        )

        if actionable:
            badge = actionable[0]
            fingerprint = _badge_fingerprint(badge)
            expected_identity, identity_details = _read_pass_card_identity(current, badge)
            logger.event(
                action="recognize_pass_card_identity",
                step=step,
                texts=expected_identity,
                details=identity_details,
            )
            if not any(len(_normalized_pass_identity(text)) >= 4 for text in expected_identity):
                return False, "带红色感叹号的通行证名称未能在固定位置识别，未点击"
            _click_logged(
                hwnd,
                current,
                _pass_card_click_point(badge),
                key="pass_list_notification",
                logger=logger,
            )
            selected_ok, selected, selected_details = _wait_for_pass_selection(
                hwnd,
                badge,
                expected_identity=expected_identity,
                logger=logger,
                step=step,
            )
            logger.event(
                action="recognize_selected_pass",
                step=step,
                found=selected_ok,
                details=selected_details,
            )
            if not selected_ok:
                return False, "点击通行证卡片后未切换到对应通行证，固定位置页面文字或卡片名称未确认"

            selected_state = selected_details.get("state")
            if selected_state == "task_page":
                task_page = selected
                logger.event(action="skip_pass_task_list", step=step, reason="already on task page")
            elif selected_state == "overview":
                task_found, task_page, task_details = _open_pass_task_page(
                    hwnd,
                    selected,
                    logger=logger,
                    label=f"pass-task-page-{step:02d}",
                )
                if not task_found:
                    reason = "点击通行证任务标签后页面未切换，固定位置仍识别到LEVEL/基础或未识别到“全部获得”"
                    logger.failure(reason)
                    return False, reason
            else:
                return False, "通行证卡片点击后页面状态不明确，未执行任务标签点击"

            _click_logged(hwnd, task_page, PASS_CLAIM_ALL_POINT, key="pass_claim_all", logger=logger)
            claim_effect, outcome, effect_details = _capture_until(
                hwnd,
                logger=logger,
                label=f"pass-claim-result-{step:02d}",
                recognize=_confirm_claim_effect,
                required_consecutive=2,
            )
            if not claim_effect:
                reason = "点击“全部获得”后单步超时，固定位置文字仍存在，领取未生效"
                logger.failure(reason)
                return False, reason

            dismissed = 0
            if effect_details.get("reward_overlay"):
                ok, current, dismissed, reason = _dismiss_reward_results(hwnd, outcome, logger=logger)
                if not ok:
                    return False, reason
            else:
                current = outcome
            claimed += 1
            ignored_badges.add(fingerprint)
            logger.event(
                action="pass_claim_completed",
                step=step,
                claimed=claimed,
                dismissed_overlays=dismissed,
            )
            continue

        if swipes >= MAX_PASS_SWIPES:
            return True, f"completed: claimed={claimed}; swipes={swipes}"

        swipe_ratio_logged(
            hwnd,
            current,
            PASS_SWIPE_BEGIN,
            PASS_SWIPE_END,
            key=f"pass-list-up-{swipes + 1}",
            logger=logger,
        )
        time.sleep(ACTION_SAMPLE_DELAY)
        current = safe_capture_client(hwnd, logger=logger)
        swipes += 1
        # A new viewport can place a different card at the same Y coordinate.
        # Clear per-viewport stale markers so equal Y positions can be rescanned.
        ignored_badges.clear()
        logger.event(
            action="verify_swipe",
            swipe=swipes,
            pass_page_found=_confirm_pass_reward_page(current)[0],
        )

    reason = f"pass reward loop reached hard limit ({MAX_PASS_LOOP_STEPS} steps)"
    logger.failure(reason)
    return False, reason


def _enter_with_logger(
    hwnd: int,
    image: Image.Image,
    *,
    dry_run: bool,
    logger: RunLogger,
) -> tuple[bool, Image.Image, str, bool]:
    """Return ``entered=False`` only for the successful no-home-badge case."""
    is_home, home_details = recognize_home_labels(image)
    logger.event(action="recognize_home", found=is_home, details=home_details)
    if not is_home:
        return False, image, "pass entry requires fixed-position home OCR", False
    has_notification, notification_details = detect_home_reward_notification(image, "pass")
    logger.event(
        action="detect_notification",
        target="pass",
        found=has_notification,
        details=notification_details,
    )
    if not has_notification:
        return True, image, "pass has no reward notification", False

    _click_logged(
        hwnd,
        image,
        HOME_PASS_POINT,
        key="home_pass",
        logger=logger,
        dry_run=dry_run,
    )
    if dry_run:
        return True, image, "dry-run planned open pass reward list", True
    opened, next_image, _details = _capture_until(
        hwnd,
        logger=logger,
        label="pass-page-opened",
        recognize=_confirm_pass_reward_page,
    )
    reason = "opened pass reward list" if opened else "点击主页通行证后未识别到通行证页面"
    return opened, next_image, reason, True


def run_pass_rewards(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Run the complete home-to-pass reward flow for daily automation."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="pass_rewards", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason
    try:
        image = safe_capture_client(hwnd, logger=logger)
        ok, pass_image, reason, entered = _enter_with_logger(
            hwnd,
            image,
            dry_run=dry_run,
            logger=logger,
        )
        if not ok:
            logger.failure(reason)
            return False, reason
        if not entered:
            reason = f"skipped: {reason}"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason
        if dry_run:
            reason = "completed: dry-run planned pass reward entry"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason

        ok, reason = _collect_from_pass_page(hwnd, pass_image, logger=logger)
        if not ok:
            logger.event(action="stop", result="error", reason=reason)
            logger.failure(reason)
            return False, reason
        home_ok, home_reason = return_to_home(
            hwnd,
            logger=logger,
            recognize_source=_confirm_pass_reward_page,
            source_name="通行证页面",
        )
        if not home_ok:
            logger.event(action="stop", result="error", reason=home_reason)
            logger.failure(home_reason)
            return False, home_reason
        reason = f"{reason}; {home_reason}"
        logger.event(action="stop", result="success", reason=reason)
        return True, reason
    except Exception as exc:  # noqa: BLE001 - capture/OCR failures must be explicit.
        reason = f"pass reward flow failed: {exc!r}"
        logger.failure(reason)
        return False, reason


def enter_pass_rewards(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Compatibility entry point: open the pass page, but do not claim."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="pass_rewards_entry", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason
    image = safe_capture_client(hwnd, logger=logger)
    ok, next_image, reason, entered = _enter_with_logger(
        hwnd,
        image,
        dry_run=dry_run,
        logger=logger,
    )
    if not ok:
        logger.failure(reason)
        return False, reason
    if entered and not dry_run:
        path = logger.save_image(next_image, "pass-entry-opened.png")
        logger.event(action="stop", result="success", reason=reason, screenshot=str(path))
        return True, f"{reason}; pass reward list opened"
    logger.event(action="stop", result="success", reason=reason)
    return True, reason


def select_pass_with_notification(*, log_root: Path) -> tuple[bool, str]:
    """Compatibility action: select the first fixed-region red badge."""
    logger = RunLogger(log_root, annotate_clicks=True)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"
    image = safe_capture_client(hwnd, logger=logger)
    if not _confirm_pass_reward_page(image)[0]:
        return False, "pass selection requires fixed-position 通行证 OCR"
    badges = _find_pass_badges(image)
    if not badges:
        return False, "no pass-list reward notification found"
    expected_identity, _identity_details = _read_pass_card_identity(image, badges[0])
    if not any(len(_normalized_pass_identity(text)) >= 4 for text in expected_identity):
        return False, "带红色感叹号的通行证名称未能在固定位置识别，未点击"
    _click_logged(
        hwnd,
        image,
        _pass_card_click_point(badges[0]),
        key="pass_list_notification",
        logger=logger,
    )
    found, _after, _details = _wait_for_pass_selection(
        hwnd,
        badges[0],
        expected_identity=expected_identity,
        logger=logger,
        step=1,
    )
    return (True, "selected red-dot pass") if found else (False, "selected pass was not text-confirmed")


def open_selected_pass_task_list(*, log_root: Path) -> tuple[bool, str]:
    """Compatibility action: open the selected pass task page."""
    logger = RunLogger(log_root, annotate_clicks=True)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"
    image = safe_capture_client(hwnd, logger=logger)
    if not _confirm_pass_reward_page(image)[0]:
        return False, "selected pass panel was not text-confirmed"
    if _confirm_pass_task_page(image)[0]:
        return True, "selected pass task list was already open"
    if not _confirm_selected_pass_panel(image)[0]:
        return False, "selected pass panel was not text-confirmed"
    found, _after, _details = _open_pass_task_page(
        hwnd,
        image,
        logger=logger,
        label="pass-task-page",
    )
    return (
        (True, "opened selected pass task list")
        if found
        else (False, "点击通行证任务标签后页面未切换，固定位置任务页文字未确认")
    )


def claim_selected_pass_task_rewards(*, log_root: Path) -> tuple[bool, str]:
    """Compatibility action: click fixed-position ``全部获得`` when recognized."""
    logger = RunLogger(log_root, annotate_clicks=True)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"
    image = safe_capture_client(hwnd, logger=logger)
    if not _confirm_claim_all(image)[0]:
        return False, "全部获得 was not found at its fixed position"
    _click_logged(hwnd, image, PASS_CLAIM_ALL_POINT, key="pass_claim_all", logger=logger)
    found, _after, _details = _capture_until(
        hwnd,
        logger=logger,
        label="pass-claim-result",
        recognize=_confirm_claim_effect,
        required_consecutive=2,
    )
    return (
        (True, "claimed selected pass task rewards")
        if found
        else (False, "点击“全部获得”后单步超时，固定位置文字仍存在，领取未生效")
    )


def claim_selected_pass_rewards(*, log_root: Path) -> tuple[bool, str]:
    """Backward-compatible alias for task-list all-claim."""
    return claim_selected_pass_task_rewards(log_root=log_root)


def dismiss_pass_reward_overlay(*, log_root: Path) -> tuple[bool, str]:
    """Compatibility action: dismiss OCR-confirmed pass reward settlements."""
    logger = RunLogger(log_root, annotate_clicks=True)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"
    image = safe_capture_client(hwnd, logger=logger)
    if not _is_reward_overlay(image)[0]:
        return False, "pass reward settlement was not OCR-confirmed"
    ok, _after, _count, reason = _dismiss_reward_results(hwnd, image, logger=logger)
    return ok, reason


def dismiss_pass_item_detail(*, log_root: Path) -> tuple[bool, str]:
    """Compatibility action: close a pass item-detail popup."""
    logger = RunLogger(log_root, annotate_clicks=True)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"
    image = safe_capture_client(hwnd, logger=logger)
    if not _has_pass_item_popup_close(image):
        return False, "pass item-detail close icon was not found"
    ok, _after, reason = _close_item_detail(hwnd, image, logger=logger)
    return ok, reason


def claim_all_marked_pass_rewards(*, log_root: Path) -> tuple[bool, str]:
    """Compatibility action: collect marked passes from an already-open page."""
    logger = RunLogger(log_root, annotate_clicks=True)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"
    image = safe_capture_client(hwnd, logger=logger)
    return _collect_from_pass_page(hwnd, image, logger=logger)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect BrownDust II pass rewards.")
    parser.add_argument("--dry-run", action="store_true", help="record the planned home entry without clicking")
    parser.add_argument("--select-and-claim", action="store_true", help="select one marked pass and claim its tasks")
    parser.add_argument("--claim-all-marked", action="store_true", help="claim every marked pass from an open pass page")
    parser.add_argument("--claim-current-tasks", action="store_true", help="open and claim the selected pass tasks")
    parser.add_argument("--close-item-detail", action="store_true", help="close an open pass item-detail popup")
    parser.add_argument("--dismiss-reward-result", action="store_true", help="close an OCR-confirmed reward settlement")
    parser.add_argument("--enter-only", action="store_true", help="only open the pass page (legacy behavior)")
    parser.add_argument("--log-root", type=Path, default=None)
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = args.log_root or Path.cwd() / "logs" / "pass_rewards" / stamp
    if args.claim_all_marked:
        ok, reason = claim_all_marked_pass_rewards(log_root=log_root)
    elif args.close_item_detail:
        ok, reason = dismiss_pass_item_detail(log_root=log_root)
    elif args.dismiss_reward_result:
        ok, reason = dismiss_pass_reward_overlay(log_root=log_root)
    elif args.claim_current_tasks:
        ok, reason = open_selected_pass_task_list(log_root=log_root / "01-open-tasks")
        if ok:
            ok, reason = claim_selected_pass_task_rewards(log_root=log_root / "02-claim-all")
    elif args.select_and_claim:
        ok, reason = select_pass_with_notification(log_root=log_root / "01-select")
        if ok:
            ok, reason = open_selected_pass_task_list(log_root=log_root / "02-open-tasks")
        if ok:
            ok, reason = claim_selected_pass_task_rewards(log_root=log_root / "03-claim-all")
    elif args.enter_only:
        ok, reason = enter_pass_rewards(dry_run=args.dry_run, log_root=log_root)
    else:
        ok, reason = run_pass_rewards(dry_run=args.dry_run, log_root=log_root)
    print(f"success={ok}")
    print(f"reason={reason}")
    print(f"log_root={log_root}")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
