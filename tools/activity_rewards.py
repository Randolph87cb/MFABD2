"""Collect rewards from marked Activities entries.

The flow deliberately uses only fixed-position OCR and the existing red
diamond/exclamation detector.  It never infers a page from brightness or a
button's enabled state from colour.  In particular, a paid-diamond purchase
confirmation is clicked only when *one captured frame* contains both the
fixed-position ``免费1次`` offer and the fixed-position ``购买`` confirmation.
"""

from __future__ import annotations

import argparse
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from free_gacha import RunLogger, safe_capture_client
from game_text_recognition import (
    LabelRecognitionSession,
    recognize_home_labels,
    recognize_reward_overlay_labels,
)
from home_notifications import detect_home_reward_notification, find_red_exclamation_badges
from open_game import find_game_window
from reward_flow import click_ratio_logged, return_to_home, swipe_ratio_logged


NormalizedRegion = tuple[float, float, float, float]
MatchMap = dict[str, list[str]]
SHORT_ACTIVITY_IDENTITIES = {"转盘"}


def _region(x: int, y: int, width: int, height: int) -> NormalizedRegion:
    """Convert an upstream 1280x720 fixed region to normalized coordinates."""
    return x / 1280, y / 720, width / 1280, height / 720


def _center(region: NormalizedRegion) -> tuple[float, float]:
    x, y, width, height = region
    return x + width / 2, y + height / 2


def _offset_center(
    region: NormalizedRegion,
    offset_x: int,
    offset_y: int,
) -> tuple[float, float]:
    x, y = _center(region)
    return x + offset_x / 1280, y + offset_y / 720


# Fixed geometry mirrored from upstream Activities.json.
ACTIVITY_PAGE_REGION = _region(108, 110, 529, 483)
# Current-client calibration: list-card red exclamations sit at x≈0.266.
# Keep the strip narrow so artwork elsewhere cannot satisfy the detector.
ACTIVITY_LIST_BADGE_REGION = (0.255, 0.170, 0.025, 0.620)
ACTIVITY_LIST_TEXT_REGION = _region(100, 100, 205, 516)
ACTIVITY_LIST_END_REGION = _region(125, 335, 176, 281)
ACTIVITY_DETAIL_IDENTITY_REGION = (0.250, 0.120, 0.660, 0.360)
TOKEN_EXCHANGE_REGION = _region(531, 515, 114, 33)
TOKEN_CONFIRM_REGION = _region(665, 403, 85, 29)
DICE_AUTO_REGION = _region(1068, 486, 87, 37)
DICE_SWITCH_REGION = _region(1059, 517, 104, 47)
DICE_SWITCH_CONTROL_REGION = _region(1076, 534, 11, 13)
PUZZLE_UNLOCK_REGION = _region(1002, 514, 120, 49)
BINGO_UNLOCK_REGION = _region(533, 518, 119, 54)
# Current roulette layout: the single-spin control is left of the ten-spin
# control.  The reference flow only handles tens; this project also consumes
# a remaining 1-9 tokens through the fixed single-spin control.
ROULETTE_SINGLE_REGION = _region(760, 493, 170, 81)
FREE_ROULETTE_REGION = ROULETTE_SINGLE_REGION
TOKEN_ROULETTE_REGION = ROULETTE_SINGLE_REGION
TOKEN_ROULETTE_TEN_REGION = _region(966, 493, 170, 81)
ROULETTE_BALANCE_REGION = (0.885, 0.020, 0.065, 0.065)
CLOTHING_OFFER_REGION = _region(320, 145, 602, 422)
CLOTHING_CLAIM_NOW_REGION = _region(320, 145, 843, 422)
# Activities_FreeClothing_Ty2 searches [1060,284,86,269], then Ty2_Ck
# expands each detected button by [-30,-30,+60,+60].  The text-only version
# stays inside that exact expanded envelope.
CLOTHING_BUTTON_TEMPLATE_REGION = _region(1060, 284, 86, 269)
CLOTHING_BUTTON_COLUMN_REGION = _region(1030, 254, 146, 329)
REGULAR_CLAIM_REGION = _region(544, 527, 595, 37)
PAID_FREE_REGION = _region(719, 515, 92, 44)
PAID_PURCHASE_REGION = _region(630, 358, 201, 136)

# Calibrated from this project's homepage layout; the reference project's
# activity-entry target is offset on the current client.
HOME_ACTIVITY_POINT = (0.467, 0.925)
TOKEN_EXCHANGE_POINT = _center(TOKEN_EXCHANGE_REGION)
TOKEN_CONFIRM_POINT = _center(TOKEN_CONFIRM_REGION)
DICE_AUTO_POINT = _center(DICE_AUTO_REGION)
DICE_SWITCH_POINT = _offset_center(DICE_SWITCH_CONTROL_REGION, 10, 4)
PUZZLE_UNLOCK_POINT = _center(PUZZLE_UNLOCK_REGION)
BINGO_UNLOCK_POINT = _center(BINGO_UNLOCK_REGION)
FREE_ROULETTE_POINT = _center(FREE_ROULETTE_REGION)
TOKEN_ROULETTE_POINT = _center(TOKEN_ROULETTE_REGION)
TOKEN_ROULETTE_TEN_POINT = _center(TOKEN_ROULETTE_TEN_REGION)
CLOTHING_OPEN_POINT = (1047 / 1280, 204 / 720)
CLOTHING_CLAIM_NOW_POINT = _center(CLOTHING_CLAIM_NOW_REGION)
REGULAR_CLAIM_POINT = _center(REGULAR_CLAIM_REGION)
# Upstream clicks 50 pixels above the OCR match to open the free paid-diamond
# offer, then uses the centre of the fixed confirmation region.
PAID_FREE_POINT = _offset_center(PAID_FREE_REGION, 0, -50)
PAID_PURCHASE_POINT = _center(PAID_PURCHASE_REGION)
ACTIVITY_BACK_POINT = (120 / 1280, 35 / 720)
# The current client only scrolls reliably when the drag starts in the lower
# half of an activity card and stays inside the list for the whole gesture.
SCROLL_BEGIN = (0.175, 0.700)
SCROLL_END = (0.175, 0.280)
ACTIVITY_SCROLL_DURATION = 1.2
ACTIVITY_SCROLL_END_HOLD = 0.3

STEP_TIMEOUT = 12.0
SETTLEMENT_TIMEOUT = 20.0
POLL_INTERVAL = 0.5
MAX_SCAN_CYCLES = 40
MAX_ACTION_REPEATS = 10
MAX_SETTLEMENTS = 6
MAX_ACTIVITY_SWIPES = 6
MAX_ACTIVITY_ENTRY_CLICKS = 2
DICE_RUN_SETTLE_SECONDS = 15.0


CONTROL_GROUPS: dict[str, dict[str, Any]] = {
    "page": {"region": ACTIVITY_PAGE_REGION, "labels": ("活动",)},
    "token_exchange": {"region": TOKEN_EXCHANGE_REGION, "labels": ("兑换",)},
    "token_confirm": {"region": TOKEN_CONFIRM_REGION, "labels": ("确认",)},
    "dice_auto": {"region": DICE_AUTO_REGION, "labels": ("自动进行",)},
    "dice_switch_on": {
        "region": DICE_SWITCH_REGION,
        "labels": ("ON", "开启", "已开启", "自动进行中"),
    },
    "dice_switch_off": {
        "region": DICE_SWITCH_REGION,
        "labels": ("OFF", "关闭", "未开启", "自动进行已关闭"),
    },
    "puzzle_unlock": {"region": PUZZLE_UNLOCK_REGION, "labels": ("全部解锁",)},
    "bingo_unlock": {"region": BINGO_UNLOCK_REGION, "labels": ("全部解锁",)},
    "free_roulette": {"region": FREE_ROULETTE_REGION, "labels": ("1次免费",)},
    "token_roulette": {"region": TOKEN_ROULETTE_REGION, "labels": ("旋转1次",)},
    "clothing_offer": {
        "region": CLOTHING_OFFER_REGION,
        "labels": ("强化免费赠送", "强化服装的机会", "+5强化"),
    },
    "clothing_claim_now": {"region": CLOTHING_CLAIM_NOW_REGION, "labels": ("立刻获得",)},
    "clothing_button": {
        "region": CLOTHING_BUTTON_COLUMN_REGION,
        "labels": ("获得", "领取", "免费"),
    },
    "regular_claim": {"region": REGULAR_CLAIM_REGION, "labels": ("全部领取",)},
    "paid_free": {"region": PAID_FREE_REGION, "labels": ("免费1次",)},
    "paid_purchase": {"region": PAID_PURCHASE_REGION, "labels": ("购买",)},
}


def recognize_activity_controls(image: Image.Image) -> tuple[MatchMap, dict[str, Any]]:
    """OCR every fixed activity region once and return matched labels."""
    grouped, matches, error = LabelRecognitionSession(image).recognize(CONTROL_GROUPS)
    if error is not None:
        return {}, error
    return matches, {"available": True, "texts": grouped, "matches": matches}


def classify_activity_from_matches(matches: MatchMap) -> str:
    """Dispatch an activity from fixed-position OCR matches.

    More specific layouts precede the broad regular-claim region.  A purchase
    label without the free offer is explicitly unsafe rather than unknown.
    """
    if matches.get("paid_purchase") and not matches.get("paid_free"):
        return "unsafe_paid"
    if matches.get("paid_free"):
        return "paid_diamonds"
    for group, kind in (
        ("token_exchange", "token_exchange"),
        ("dice_auto", "dice_auto"),
        ("puzzle_unlock", "puzzle"),
        ("bingo_unlock", "bingo"),
        ("free_roulette", "free_roulette"),
        ("token_roulette", "token_roulette"),
        ("clothing_claim_now", "free_clothing_style_1"),
        ("clothing_button", "free_clothing_style_2"),
        ("clothing_offer", "free_clothing"),
        ("regular_claim", "regular"),
    ):
        if matches.get(group):
            return kind
    return "unknown"


def recognize_activity_kind(image: Image.Image) -> tuple[str, dict[str, Any]]:
    matches, details = recognize_activity_controls(image)
    return classify_activity_from_matches(matches), details


def _normalized_ui_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value)).replace("：", ":")


def paid_confirmation_is_safe(matches: MatchMap, details: dict[str, Any]) -> bool:
    """Require exact free/purchase text from one immutable OCR frame."""
    grouped = details.get("texts")
    if not isinstance(grouped, dict):
        return False
    free_texts = {_normalized_ui_text(text) for text in grouped.get("paid_free", [])}
    purchase_texts = {
        _normalized_ui_text(text) for text in grouped.get("paid_purchase", [])
    }
    unsafe_purchase_copy = any(
        re.search(r"[1-9]\d*", text) or any(word in text for word in ("付费", "钻石", "有偿", "价格"))
        for text in purchase_texts
        if text != "购买"
    )
    return bool(
        matches.get("paid_free")
        and matches.get("paid_purchase")
        and "免费1次" in free_texts
        and "购买" in purchase_texts
        and not unsafe_purchase_copy
    )


def paid_free_offer_is_safe(matches: MatchMap, details: dict[str, Any]) -> bool:
    """Require exact free-offer OCR before even opening the confirmation."""
    grouped = details.get("texts")
    if not isinstance(grouped, dict):
        return False
    free_texts = {_normalized_ui_text(text) for text in grouped.get("paid_free", [])}
    unsafe_copy = any(
        re.search(r"[1-9]\d*", text) or any(word in text for word in ("付费", "钻石", "有偿", "价格"))
        for text in free_texts
        if text != "免费1次"
    )
    return bool(matches.get("paid_free") and "免费1次" in free_texts and not unsafe_copy)


def _dice_switch_state(image: Image.Image) -> tuple[str, dict[str, Any]]:
    matches, details = recognize_activity_controls(image)
    on = bool(matches.get("dice_switch_on"))
    off = bool(matches.get("dice_switch_off"))
    if on == off:
        return "unknown", details
    return ("on" if on else "off"), details


def _read_texts_at(image: Image.Image, region: NormalizedRegion) -> tuple[list[str], dict[str, Any]]:
    grouped, _matches, error = LabelRecognitionSession(image).recognize(
        {"identity": {"region": region, "labels": ()}}
    )
    if error is not None:
        return [], error
    texts = grouped.get("identity", [])
    return texts, {"available": True, "texts": texts}


def _normalized_activity_identity(value: object) -> str:
    text = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", str(value)).upper()
    text = re.sub(r"D\d+", "", text)
    for generic in ("活动", "EVENT"):
        text = text.replace(generic, "")
    return text


def _usable_activity_identity(value: object) -> bool:
    normalized = _normalized_activity_identity(value)
    return len(normalized) >= 4 or normalized in SHORT_ACTIVITY_IDENTITIES


def _activity_identity_matches(list_texts: list[str], detail_texts: list[str]) -> bool:
    sources = [
        _normalized_activity_identity(text)
        for text in list_texts
        if _usable_activity_identity(text)
    ]
    targets = [
        value
        for text in detail_texts
        if len(value := _normalized_activity_identity(text)) >= 2
    ]
    for source in sources:
        for target in targets:
            if source in target or target in source:
                return True
            if SequenceMatcher(None, source, target).ratio() >= 0.72:
                return True
    return False


def _activity_list_identity(image: Image.Image) -> tuple[str, ...]:
    """Read stable activity names from the fixed left-hand list area."""
    texts, _details = _read_texts_at(image, ACTIVITY_LIST_TEXT_REGION)
    values = {
        re.sub(r"\d+", "", value)
        for text in texts
        if len(value := _normalized_activity_identity(text)) >= 4
    }
    return tuple(sorted(value for value in values if len(value) >= 4))


def _activity_list_progressed(before: tuple[str, ...], after: tuple[str, ...]) -> bool:
    """Require both an old item to leave and a new item to enter the OCR list."""
    if not before or not after:
        return False

    def has_match(value: str, candidates: tuple[str, ...]) -> bool:
        return any(
            value in candidate
            or candidate in value
            or SequenceMatcher(None, value, candidate).ratio() >= 0.82
            for candidate in candidates
        )

    old_left = any(not has_match(value, after) for value in before)
    new_entered = any(not has_match(value, before) for value in after)
    return old_left and new_entered


def _activity_list_at_end(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    texts, details = _read_texts_at(image, ACTIVITY_LIST_END_REGION)
    normalized = [_normalized_ui_text(text) for text in texts]
    markers = ("登录加成", "登录活动")
    found = any(marker in text for text in normalized for marker in markers)
    return found, {**details, "markers": markers, "normalized_texts": normalized}


def _read_activity_card_identity(
    image: Image.Image,
    badge: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    _x, center_y = badge["center"]
    # The badge is at the card's top-right; its title is rendered near the
    # bottom edge.  Reading around the badge can pick up the previous card.
    region = (0.075, min(0.900, float(center_y) + 0.015), 0.190, 0.080)
    return _read_texts_at(image, region)


def _is_activity_page(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    matches, details = recognize_activity_controls(image)
    return bool(matches.get("page")), details


def _wait_for_image(
    hwnd: int,
    *,
    logger: RunLogger,
    label: str,
    predicate: Callable[[Image.Image], bool],
    timeout: float = STEP_TIMEOUT,
) -> tuple[bool, Image.Image]:
    deadline = time.monotonic() + timeout
    sample = 0
    while True:
        image = safe_capture_client(hwnd, logger=logger)
        sample += 1
        found = predicate(image)
        path = logger.save_image(image, f"{label}-{sample:02d}.png")
        logger.event(
            action="wait_activity_step",
            label=label,
            sample=sample,
            found=found,
            screenshot=str(path),
        )
        if found:
            return True, image
        if time.monotonic() >= deadline:
            return False, image
        time.sleep(POLL_INTERVAL)


def _dismiss_settlements(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
    dry_run: bool,
) -> tuple[bool, Image.Image, str]:
    """Close a bounded sequence of OCR-confirmed reward result overlays."""
    current = image
    for index in range(1, MAX_SETTLEMENTS + 1):
        overlay, details = recognize_reward_overlay_labels(current)
        logger.event(action="recognize_activity_settlement", index=index, found=overlay, details=details)
        if not overlay:
            return True, current, f"closed {index - 1} settlements"
        click_ratio_logged(
            hwnd,
            current,
            (0.50, 0.82),
            key="activity_settlement_close",
            logger=logger,
            dry_run=dry_run,
        )
        if dry_run:
            return True, current, "dry-run planned settlement close"
        closed, current = _wait_for_image(
            hwnd,
            logger=logger,
            label=f"activity-settlement-closed-{index}",
            predicate=lambda candidate: not recognize_reward_overlay_labels(candidate)[0],
            timeout=SETTLEMENT_TIMEOUT,
        )
        if not closed:
            return False, current, "activity reward settlement did not close before timeout"
    if recognize_reward_overlay_labels(current)[0]:
        return False, current, f"more than {MAX_SETTLEMENTS} activity settlements appeared"
    return True, current, f"closed {MAX_SETTLEMENTS} settlements"


def _return_to_activity_index(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
    dry_run: bool,
) -> tuple[bool, Image.Image, str]:
    ok, current, reason = _dismiss_settlements(
        hwnd,
        image,
        logger=logger,
        dry_run=dry_run,
    )
    if not ok or dry_run:
        return ok, current, reason
    if _is_activity_page(current)[0]:
        return True, current, reason
    click_ratio_logged(
        hwnd,
        current,
        ACTIVITY_BACK_POINT,
        key="activity_result_back",
        logger=logger,
    )
    returned, current = _wait_for_image(
        hwnd,
        logger=logger,
        label="activity-index-returned",
        predicate=lambda candidate: _is_activity_page(candidate)[0],
    )
    if not returned:
        return False, current, "activity result did not return to the activity index before timeout"
    return True, current, reason


def _click_then_return(
    hwnd: int,
    image: Image.Image,
    point: tuple[float, float],
    *,
    key: str,
    logger: RunLogger,
    dry_run: bool,
) -> tuple[bool, Image.Image, str]:
    click_ratio_logged(hwnd, image, point, key=key, logger=logger, dry_run=dry_run)
    if dry_run:
        return True, image, f"dry-run planned {key}"

    changed, current = _wait_for_image(
        hwnd,
        logger=logger,
        label=f"{key}-effect",
        predicate=lambda candidate: recognize_reward_overlay_labels(candidate)[0],
        timeout=SETTLEMENT_TIMEOUT,
    )
    if not changed:
        return False, current, (
            f"{key}：点击后未在单步超时内识别到奖励结算，点击未确认生效"
        )
    return _return_to_activity_index(hwnd, current, logger=logger, dry_run=False)


def _handle_token_exchange(
    hwnd: int, image: Image.Image, *, logger: RunLogger, dry_run: bool
) -> tuple[bool, Image.Image, str]:
    click_ratio_logged(
        hwnd, image, TOKEN_EXCHANGE_POINT, key="activity_token_exchange", logger=logger, dry_run=dry_run
    )
    if dry_run:
        return True, image, "dry-run planned token exchange and confirmation"
    confirmed, dialog = _wait_for_image(
        hwnd,
        logger=logger,
        label="activity-token-confirm",
        predicate=lambda candidate: bool(recognize_activity_controls(candidate)[0].get("token_confirm")),
    )
    if not confirmed:
        return False, dialog, "token exchange confirmation did not appear before timeout"
    return _click_then_return(
        hwnd,
        dialog,
        TOKEN_CONFIRM_POINT,
        key="activity_token_confirm",
        logger=logger,
        dry_run=False,
    )


def _handle_dice_auto(
    hwnd: int, image: Image.Image, *, logger: RunLogger, dry_run: bool
) -> tuple[bool, Image.Image, str]:
    """Start dice auto-play and touch its switch only after fixed OCR says OFF."""
    click_ratio_logged(
        hwnd,
        image,
        DICE_AUTO_POINT,
        key="activity_dice_auto",
        logger=logger,
        dry_run=dry_run,
    )
    if dry_run:
        return True, image, "dry-run planned dice auto entry; switch was not clicked"

    recognized, current = _wait_for_image(
        hwnd,
        logger=logger,
        label="activity-dice-switch-state",
        predicate=lambda candidate: _dice_switch_state(candidate)[0] in {"on", "off"},
    )
    if not recognized:
        return False, current, (
            "dice auto switch state was not confirmed by fixed-position text; "
            "switch was not clicked"
        )
    state, details = _dice_switch_state(current)
    logger.event(action="recognize_dice_switch", state=state, details=details)
    if state == "off":
        click_ratio_logged(
            hwnd,
            current,
            DICE_SWITCH_POINT,
            key="activity_dice_switch_on",
            logger=logger,
        )
        enabled, current = _wait_for_image(
            hwnd,
            logger=logger,
            label="activity-dice-switch-enabled",
            predicate=lambda candidate: _dice_switch_state(candidate)[0] == "on",
        )
        if not enabled:
            return False, current, "dice auto switch did not reach OCR-confirmed ON state"

    # Activities_GetRoll_AutoOn waits for the automatic run before returning
    # to the activity index.  Preserve that settle interval, then require the
    # fixed-position activity title (or a reward overlay that is closed below).
    time.sleep(DICE_RUN_SETTLE_SECONDS)
    returned, current = _wait_for_image(
        hwnd,
        logger=logger,
        label="activity-dice-auto-return",
        predicate=lambda candidate: (
            _is_activity_page(candidate)[0]
            or recognize_reward_overlay_labels(candidate)[0]
        ),
        timeout=SETTLEMENT_TIMEOUT,
    )
    if not returned:
        return False, current, "dice automatic run did not return before timeout"
    return _return_to_activity_index(hwnd, current, logger=logger, dry_run=False)


def _handle_free_clothing(
    hwnd: int, image: Image.Image, *, logger: RunLogger, dry_run: bool
) -> tuple[bool, Image.Image, str]:
    click_ratio_logged(
        hwnd, image, CLOTHING_OPEN_POINT, key="activity_clothing_open", logger=logger, dry_run=dry_run
    )
    if dry_run:
        return True, image, "dry-run planned free clothing entry"
    found, current = _wait_for_image(
        hwnd,
        logger=logger,
        label="activity-clothing-style",
        predicate=lambda candidate: (
            _is_activity_page(candidate)[0]
            and classify_activity_from_matches(
                recognize_activity_controls(candidate)[0]
            ) in {"free_clothing_style_1", "free_clothing_style_2"}
        ),
    )
    if not found:
        return False, current, "free clothing claim style was not recognized before timeout"
    kind, _details = recognize_activity_kind(current)
    return _handle_activity(kind, hwnd, current, logger=logger, dry_run=False)


def _clothing_style_2_rows(
    image: Image.Image,
) -> tuple[list[int], dict[str, Any] | None]:
    row_groups = {
        f"row_{index}": {
            "region": _region(1030, 254 + index * 65, 146, 65),
            "labels": ("获得", "领取", "免费"),
        }
        for index in range(5)
    }
    _texts, matches, error = LabelRecognitionSession(image).recognize(row_groups)
    if error is not None:
        return [], error
    return [index for index in range(5) if matches.get(f"row_{index}")], None


def _handle_clothing_style_2(
    hwnd: int, image: Image.Image, *, logger: RunLogger, dry_run: bool
) -> tuple[bool, Image.Image, str]:
    current = image
    for repeat in range(1, MAX_ACTION_REPEATS + 1):
        matches, details = recognize_activity_controls(current)
        matched_rows, row_error = _clothing_style_2_rows(current)
        logger.event(
            action="recognize_clothing_style_2",
            repeat=repeat,
            found=bool(matches.get("clothing_button") and matched_rows),
            matched_rows=matched_rows,
            details=details,
            row_error=row_error,
        )
        if not matches.get("clothing_button"):
            return _return_to_activity_index(hwnd, current, logger=logger, dry_run=dry_run)
        if not matched_rows:
            return False, current, "free clothing style 2 button row could not be fixed safely"
        row = matched_rows[0]
        click_ratio_logged(
            hwnd,
            current,
            ((1030 + 73) / 1280, (254 + row * 65 + 32.5) / 720),
            key="activity_clothing_claim_button",
            logger=logger,
            dry_run=dry_run,
        )
        if dry_run:
            return True, current, "dry-run planned free clothing style 2 claim"
        changed, current = _wait_for_image(
            hwnd,
            logger=logger,
            label=f"activity-clothing-row-{row}-effect",
            predicate=lambda candidate, claimed_row=row: (
                recognize_reward_overlay_labels(candidate)[0]
                or (
                    _is_activity_page(candidate)[0]
                    and claimed_row not in _clothing_style_2_rows(candidate)[0]
                )
            ),
            timeout=SETTLEMENT_TIMEOUT,
        )
        if not changed:
            return False, current, (
                "free clothing style 2 claim produced no fixed-position OCR "
                "state change before timeout"
            )
        ok, current, reason = _dismiss_settlements(
            hwnd, current, logger=logger, dry_run=False
        )
        if not ok:
            return False, current, reason
    return False, current, f"free clothing style 2 exceeded {MAX_ACTION_REPEATS} claim clicks"


def _handle_paid_diamonds(
    hwnd: int, image: Image.Image, *, logger: RunLogger, dry_run: bool
) -> tuple[bool, Image.Image, str]:
    initial_matches, initial_details = recognize_activity_controls(image)
    if initial_matches.get("paid_purchase"):
        safe = paid_confirmation_is_safe(initial_matches, initial_details)
        logger.event(
            action="paid_confirmation_safety_gate",
            safe=safe,
            resumed=True,
            details=initial_details,
        )
        if not safe:
            return False, image, (
                "unsafe paid-diamond confirmation: the same frame did not contain both "
                "'免费1次' and '购买'; purchase was not clicked"
            )
        return _click_then_return(
            hwnd,
            image,
            PAID_PURCHASE_POINT,
            key="activity_paid_free_confirm",
            logger=logger,
            dry_run=dry_run,
        )

    offer_safe = paid_free_offer_is_safe(initial_matches, initial_details)
    logger.event(
        action="paid_free_offer_safety_gate",
        safe=offer_safe,
        details=initial_details,
    )
    if not offer_safe:
        return False, image, (
            "unsafe paid-diamond offer: fixed-position OCR did not contain exact "
            "'免费1次'; offer was not clicked"
        )

    # Opening the exact free offer is not the purchase confirmation.  The
    # latter gets its own same-frame safety gate below.
    click_ratio_logged(
        hwnd, image, PAID_FREE_POINT, key="activity_paid_free_open", logger=logger, dry_run=dry_run
    )
    if dry_run:
        return True, image, "dry-run stopped before paid-diamond purchase confirmation"
    appeared, dialog = _wait_for_image(
        hwnd,
        logger=logger,
        label="activity-paid-free-confirmation",
        predicate=lambda candidate: bool(recognize_activity_controls(candidate)[0].get("paid_purchase")),
    )
    if not appeared:
        return False, dialog, "paid-diamond purchase confirmation did not appear before timeout"

    # One OCR call over one immutable screenshot proves both labels coexist.
    matches, details = recognize_activity_controls(dialog)
    safe = paid_confirmation_is_safe(matches, details)
    logger.event(action="paid_confirmation_safety_gate", safe=safe, details=details)
    if not safe:
        return False, dialog, (
            "unsafe paid-diamond confirmation: the same frame did not contain both "
            "'免费1次' and '购买'; purchase was not clicked"
        )
    return _click_then_return(
        hwnd,
        dialog,
        PAID_PURCHASE_POINT,
        key="activity_paid_free_confirm",
        logger=logger,
        dry_run=False,
    )


def _roulette_token_balance(image: Image.Image) -> tuple[int | None, dict[str, Any]]:
    texts, details = _read_texts_at(image, ROULETTE_BALANCE_REGION)
    values = []
    for text in texts:
        compact = str(text).replace(",", "").replace(" ", "")
        if compact.isdigit():
            values.append(int(compact))
    balance = max(values) if values else None
    return balance, {**details, "parsed_values": values, "balance": balance}


def _handle_token_roulette(
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
    dry_run: bool,
) -> tuple[bool, Image.Image, str]:
    balance, details = _roulette_token_balance(image)
    logger.event(action="recognize_roulette_token_balance", balance=balance, details=details)
    if balance is None:
        logger.event(
            action="roulette_balance_fallback",
            reason="顶部单个数字未识别，仅尝试固定位置的单次按钮",
        )
        point = TOKEN_ROULETTE_POINT
        key = "activity_token_roulette_1_balance_unreadable"
        return _click_then_return(
            hwnd,
            image,
            point,
            key=key,
            logger=logger,
            dry_run=dry_run,
        )
    if balance <= 0:
        return False, image, "活动代币为 0，未点击转盘"
    if balance >= 10:
        point = TOKEN_ROULETTE_TEN_POINT
        key = "activity_token_roulette_10"
    else:
        point = TOKEN_ROULETTE_POINT
        key = "activity_token_roulette_1"
    return _click_then_return(
        hwnd,
        image,
        point,
        key=key,
        logger=logger,
        dry_run=dry_run,
    )


def _handle_activity(
    kind: str,
    hwnd: int,
    image: Image.Image,
    *,
    logger: RunLogger,
    dry_run: bool,
) -> tuple[bool, Image.Image, str]:
    logger.event(action="dispatch_activity", kind=kind)
    if kind == "unsafe_paid":
        return False, image, "unsafe or ordinary paid state detected; purchase was not clicked"
    if kind == "token_exchange":
        return _handle_token_exchange(hwnd, image, logger=logger, dry_run=dry_run)
    if kind == "dice_auto":
        return _handle_dice_auto(hwnd, image, logger=logger, dry_run=dry_run)
    if kind == "free_clothing":
        return _handle_free_clothing(hwnd, image, logger=logger, dry_run=dry_run)
    if kind == "free_clothing_style_2":
        return _handle_clothing_style_2(hwnd, image, logger=logger, dry_run=dry_run)
    if kind == "paid_diamonds":
        return _handle_paid_diamonds(hwnd, image, logger=logger, dry_run=dry_run)
    if kind == "token_roulette":
        return _handle_token_roulette(hwnd, image, logger=logger, dry_run=dry_run)

    actions = {
        "regular": (REGULAR_CLAIM_POINT, "activity_regular_claim_all", "regular_claim"),
        "puzzle": (PUZZLE_UNLOCK_POINT, "activity_puzzle_unlock_all", "puzzle_unlock"),
        "bingo": (BINGO_UNLOCK_POINT, "activity_bingo_unlock_all", "bingo_unlock"),
        "free_roulette": (FREE_ROULETTE_POINT, "activity_free_roulette", "free_roulette"),
        "free_clothing_style_1": (
            CLOTHING_CLAIM_NOW_POINT,
            "activity_clothing_claim_now",
            "clothing_claim_now",
        ),
    }
    if kind not in actions:
        return False, image, "marked activity type could not be recognized safely"
    point, key, _trigger_group = actions[kind]
    return _click_then_return(
        hwnd,
        image,
        point,
        key=key,
        logger=logger,
        dry_run=dry_run,
    )


def _click_marked_activity(
    hwnd: int,
    image: Image.Image,
    badge: dict[str, Any],
    *,
    logger: RunLogger,
    dry_run: bool,
) -> tuple[bool, Image.Image, str]:
    badge_x, badge_y = badge["center"]
    expected_identity, identity_details = _read_activity_card_identity(image, badge)
    logger.event(
        action="recognize_activity_card_identity",
        texts=expected_identity,
        details=identity_details,
    )
    if not any(_usable_activity_identity(text) for text in expected_identity):
        return False, image, "带红色感叹号的活动名称未能在固定位置识别，未点击"
    point = (max(0.0, badge_x - 50 / 1280), min(1.0, badge_y + 10 / 720))
    click_ratio_logged(
        hwnd, image, point, key="activity_marked_list_entry", logger=logger, dry_run=dry_run
    )
    if dry_run:
        return True, image, "dry-run planned marked activity selection"

    def selection_confirmed(candidate: Image.Image) -> bool:
        if not _is_activity_page(candidate)[0]:
            return False
        detail_texts, _details = _read_texts_at(candidate, ACTIVITY_DETAIL_IDENTITY_REGION)
        if not _activity_identity_matches(expected_identity, detail_texts):
            return False
        kind, _details = recognize_activity_kind(candidate)
        return kind != "unknown"

    changed, current = _wait_for_image(
        hwnd,
        logger=logger,
        label="activity-selected",
        predicate=selection_confirmed,
    )
    if not changed:
        return False, current, (
            "点击带红色感叹号的活动后，详情标题未与列表名称匹配，或活动类型未识别"
        )
    return True, current, "selected marked activity"


def _finish_at_home(
    hwnd: int,
    *,
    logger: RunLogger,
    completed: int,
) -> tuple[bool, str]:
    """Execute Activities_Fin and require fixed-position home OCR before success."""
    returned, reason = return_to_home(
        hwnd,
        logger=logger,
        recognize_source=_is_activity_page,
        source_name="活动页面",
        notification_target="events",
        notification_name="活动",
    )
    if not returned:
        logger.failure(reason)
        return False, reason
    if completed:
        return True, f"completed: processed {completed} marked activities; {reason}"
    return True, f"skipped: activity page had no actionable red exclamation; {reason}"


def _run_activity_rewards_impl(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Collect every supported marked activity reward with bounded loops."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="activity_rewards", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        reason = "game window not found"
        logger.failure(reason)
        return False, reason

    image = safe_capture_client(hwnd, logger=logger)
    is_home, home_details = recognize_home_labels(image)
    logger.event(action="recognize_home_before_activity", found=is_home, details=home_details)
    if not is_home:
        reason = "activity entry requires fixed-position home OCR"
        logger.failure(reason)
        return False, reason
    has_notification, notification_details = detect_home_reward_notification(image, "events")
    logger.event(
        action="detect_notification",
        target="events",
        found=has_notification,
        details=notification_details,
    )
    if not has_notification:
        return True, "skipped: home activity icon has no red exclamation"

    click_ratio_logged(hwnd, image, HOME_ACTIVITY_POINT, key="home_activity", logger=logger, dry_run=dry_run)
    if dry_run:
        return True, "completed: dry-run planned activity entry; no purchase confirmation was clicked"

    opened = False
    for attempt in range(1, MAX_ACTIVITY_ENTRY_CLICKS + 1):
        opened, image = _wait_for_image(
            hwnd,
            logger=logger,
            label=f"activity-index-opened-{attempt}",
            predicate=lambda candidate: _is_activity_page(candidate)[0],
        )
        if opened:
            break
        still_home, retry_home_details = recognize_home_labels(image)
        still_marked = False
        retry_badge_details: dict[str, Any] = {"skipped": "home OCR not confirmed"}
        if still_home:
            still_marked, retry_badge_details = detect_home_reward_notification(image, "events")
        logger.event(
            action="activity_entry_retry_check",
            attempt=attempt,
            home=still_home,
            home_details=retry_home_details,
            notification=still_marked,
            notification_details=retry_badge_details,
        )
        if not (still_home and still_marked and attempt < MAX_ACTIVITY_ENTRY_CLICKS):
            break
        click_ratio_logged(
            hwnd,
            image,
            HOME_ACTIVITY_POINT,
            key="home_activity_retry",
            logger=logger,
        )
    if not opened:
        reason = (
            f"点击活动 {MAX_ACTIVITY_ENTRY_CLICKS} 次后，固定位置文字仍未确认活动页"
        )
        logger.failure(reason)
        return False, reason

    completed = 0
    swipes = 0
    action_hits: dict[str, int] = {}
    for cycle in range(1, MAX_SCAN_CYCLES + 1):
        is_page, page_details = _is_activity_page(image)
        logger.event(
            action="recognize_activity_page",
            cycle=cycle,
            found=is_page,
            details=page_details,
        )
        if not is_page:
            reason = "activity loop lost the fixed-position activity title"
            logger.failure(reason)
            return False, reason

        badges = find_red_exclamation_badges(image, ACTIVITY_LIST_BADGE_REGION)
        logger.event(action="scan_activity_badges", cycle=cycle, count=len(badges), badges=badges)
        if badges:
            selected, image, reason = _click_marked_activity(
                hwnd, image, badges[0], logger=logger, dry_run=False
            )
            if not selected:
                logger.failure(reason)
                return False, reason
            kind, details = recognize_activity_kind(image)
            logger.event(action="recognize_activity_kind", cycle=cycle, kind=kind, details=details)
            action_hits[kind] = action_hits.get(kind, 0) + 1
            if action_hits[kind] > MAX_ACTION_REPEATS:
                reason = (
                    f"activity {kind} exceeded per-action hard limit "
                    f"{MAX_ACTION_REPEATS}"
                )
                logger.failure(reason)
                return False, reason
            ok, image, reason = _handle_activity(
                kind, hwnd, image, logger=logger, dry_run=False
            )
            if not ok:
                logger.failure(reason)
                return False, reason
            completed += 1
            continue

        at_end, end_details = _activity_list_at_end(image)
        logger.event(
            action="recognize_activity_list_end",
            cycle=cycle,
            found=at_end,
            details=end_details,
        )
        if at_end:
            return _finish_at_home(hwnd, logger=logger, completed=completed)
        if swipes >= MAX_ACTIVITY_SWIPES:
            reason = (
                f"活动列表已滑动 {MAX_ACTIVITY_SWIPES} 次，但未识别到列表底部文字，"
                "为避免漏领未判定完成"
            )
            logger.failure(reason)
            return False, reason
        before_identity = _activity_list_identity(image)
        swipe_ratio_logged(
            hwnd,
            image,
            SCROLL_BEGIN,
            SCROLL_END,
            key=f"activity_list_scroll_{cycle}",
            logger=logger,
            duration=ACTIVITY_SCROLL_DURATION,
            end_hold=ACTIVITY_SCROLL_END_HOLD,
            foreground=True,
        )
        swipes += 1
        time.sleep(POLL_INTERVAL)
        image = safe_capture_client(hwnd, logger=logger)
        if not _is_activity_page(image)[0]:
            reason = "activity title was lost while scrolling the activity list"
            logger.failure(reason)
            return False, reason
        next_badges = find_red_exclamation_badges(image, ACTIVITY_LIST_BADGE_REGION)
        after_identity = _activity_list_identity(image)
        progressed = _activity_list_progressed(before_identity, after_identity)
        at_end, end_details = _activity_list_at_end(image)
        logger.event(
            action="activity_scroll_result",
            cycle=cycle,
            swipes=swipes,
            next_badges=len(next_badges),
            before_identity=before_identity,
            after_identity=after_identity,
            progressed=progressed,
            at_end=at_end,
            end_details=end_details,
        )
        if not next_badges and not progressed and not at_end:
            reason = "活动列表滑动后固定区域文字没有变化，滑动未生效，未判定完成"
            logger.failure(reason)
            return False, reason

    reason = f"activity scan exceeded hard limit {MAX_SCAN_CYCLES}"
    logger.failure(reason)
    return False, reason


def run_activity_rewards(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Persist unexpected direct-entry failures using the common flow contract."""
    try:
        return _run_activity_rewards_impl(dry_run=dry_run, log_root=log_root)
    except Exception as exc:  # noqa: BLE001 - keep the game open and preserve diagnostics.
        reason = f"activity rewards failed: {exc!r}"
        RunLogger(log_root, annotate_clicks=True).failure(reason)
        return False, reason


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect BrownDust II activity rewards.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", type=Path, required=True)
    args = parser.parse_args()
    ok, reason = run_activity_rewards(dry_run=args.dry_run, log_root=args.log_root)
    print(f"success={ok}")
    print(f"reason={reason}")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
