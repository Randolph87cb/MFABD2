"""Inspect the verified gold route setup, then cancel; never hunt or allocate rice."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from free_gacha import RunLogger, classify_state, safe_capture_client
from game_text_recognition import _ocr_engine, recognize_reward_overlay_labels
from open_game import find_game_window
from quick_hunt import _rice_ocr_engine, detect_selected_quick_hunt_category, recognize_free_rice
from reward_flow import click_ratio_logged, wait_for_recognition


SWITCH_BOX = (1464, 350, 1528, 385)
SWITCH_TEMPLATE = Path(__file__).parent / "assets" / "adventure-free-rice-on.png"


def _entries(image: Image.Image, *, engine=None):
    result = (_ocr_engine() if engine is None else engine)(np.asarray(image.convert("RGB")))
    boxes = result.boxes if result.boxes is not None else ()
    texts = result.txts if result.txts is not None else ()
    scores = result.scores if result.scores is not None else ()
    if not len(boxes) == len(texts) == len(scores):
        raise ValueError("missing OCR position or confidence")
    return [(float(np.mean(box[:, 0])), float(np.mean(box[:, 1])),
             "".join(str(text).split()), float(score))
            for box, text, score in zip(boxes, texts, scores)]


def _one(entries, pattern, region):
    left, top, right, bottom = region
    candidates = [(text, score) for x, y, text, score in entries
                  if left <= x <= right and top <= y <= bottom]
    if len(candidates) != 1 or not candidates[0][1] >= 0.95:
        return None
    return re.fullmatch(pattern, candidates[0][0])


def _crop_entries(image, box, *, blacken_red=False, width=None, rice=False):
    crop = image.convert("RGB").crop(box)
    if blacken_red:
        pixels = np.asarray(crop).copy()
        pixels[(pixels[:, :, 0] > 150) & (pixels[:, :, 1] < 130)
               & (pixels[:, :, 2] < 130)] = 0
        crop = Image.fromarray(pixels)
    size = (crop.width * 3, crop.height * 3) if width is None else (width, round(width * crop.height / crop.width))
    return _entries(crop.resize(size, Image.Resampling.LANCZOS), engine=_rice_ocr_engine() if rice else None)


def recognize_gold_route(image: Image.Image, *, setup: bool = False
                         ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Accept only the demonstrated unlocked Goblin Ruins / very hard layout."""
    if image.size != (2304, 1296):
        return None, {"error": "unverified client size"}
    try:
        state, _ = classify_state(image)
        if state != ("quick_hunt_setup" if setup else "quick_hunt_map"):
            return None, {"state": state, "error": "wrong route page"}
        entries = _entries(image)
        details = {"state": state, "texts": entries}
        if any(re.search("购买|充值|补充|不足|确认|解锁|每日最多" + ("" if setup else "|取消"), text)
               for _, _, text, score in entries if score >= 0.55):
            return None, {**details, "error": "unknown or unsafe dialog"}
        if not (_one(entries, "哥布林遗迹", (330, 30, 535, 90))
                and _one(entries, "可以获得金币。", (325, 85, 540, 125))):
            return None, {**details, "error": "unverified route identity"}
        rice = _one(_crop_entries(image, (1958, 49, 2057, 88), width=640, rice=True),
                    r"(0|[1-9]\d?)/90", (0, 0, 640, 253))
        if rice is None or int(rice.group(1)) > 90:
            return None, {**details, "error": "unverified free rice"}
        reading = dict(target="哥布林遗迹", difficulty="极难", free_rice=int(rice.group(1)))
        if not setup:
            category, _ = detect_selected_quick_hunt_category(image)
            arrow = np.asarray(image.convert("RGB"))[49:73, 203:216]
            if (category != "gold" or float(np.mean(np.min(arrow, axis=2) > 210)) < 0.20
                    or not _one(entries, "极难", (390, 1150, 475, 1220))
                    or not _one(entries, "快速狩猎", (1900, 1150, 2100, 1220))):
                return None, {**details, "error": "unverified gold selection or controls"}
        else:
            anchors = (
                ("快速狩猎", (770, 330, 950, 395)),
                ("哥布林遗迹[·›>]?极难", (770, 390, 980, 435)),
                ("仅使用免费米饭", (1250, 335, 1460, 395)),
                ("狩猎1次", (1070, 460, 1230, 510)),
                ("固定奖励", (1060, 730, 1200, 790)),
                ("22,000", (1100, 840, 1230, 895)),
            )
            if not all(_one(entries, pattern, box) for pattern, box in anchors):
                return None, {**details, "error": "unverified route setup"}
            switch = np.asarray(image.convert("RGB").crop(SWITCH_BOX)).astype(float)
            with Image.open(SWITCH_TEMPLATE) as source:
                template = np.asarray(source.convert("RGB")).astype(float)
            difference = float(np.mean(np.abs(switch - template)))
            cancel = _crop_entries(image, (932, 918, 999, 962))
            button = _crop_entries(image, (1070, 890, 1430, 985), blacken_red=True)
            details.update(switch_difference=difference, cancel=cancel, cost_button=button)
            if (difference > 10.0
                    or not _one(cancel, "取消", (0, 0, 201, 132))
                    or len(button) != 2
                    or sorted(text for _, _, text, score in button if score >= 0.95) != ["6", "狩猎"]):
                return None, {**details, "error": "unverified free switch, cancel or cost"}
            reading.update(count=1, rice_cost=6, fixed_gold=22000, free_only=True)
        return reading, details
    except Exception as exc:
        return None, {"error": repr(exc)}


def _hunting_map(image):
    if image.size != (2304, 1296):
        return False, {"error": "unverified client size"}
    balance, details = recognize_free_rice(image)
    arrow = np.asarray(image.convert("RGB"))[49:73, 203:216]
    bright = float(np.mean(np.min(arrow, axis=2) > 210))
    return balance is not None and bright >= 0.20, {**details, "free_rice": balance}


def run_adventure_route_recheck(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Hunting map/result → gold map → two setup reads → cancel → hunting map."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="adventure_route_recheck", dry_run=dry_run)

    def stop(reason):
        logger.failure(reason)
        return False, reason

    def wait(label, recognize):
        return wait_for_recognition(hwnd, logger=logger, label=label,
                                    recognize=recognize, timeout=30.0)

    def route_recognizer(setup=False):
        def recognize(image):
            reading, details = recognize_gold_route(image, setup=setup)
            return reading is not None, {**details, "reading": reading}
        return recognize

    def click(image, point, key):
        click_ratio_logged(hwnd, image, (point[0] / 2304, point[1] / 1296),
                           key=key, logger=logger)

    hwnd = find_game_window()
    if not hwnd:
        return stop("game window not found")
    image = safe_capture_client(hwnd, logger=logger)
    logger.save_image(image, "route-start.png")
    if image.size != (2304, 1296):
        return stop("unverified client size; no input")
    if classify_state(image)[0] == "reward_overlay":
        found, _ = recognize_reward_overlay_labels(image)
        if not found:
            return stop("unverified hunting result; no input")
        if dry_run:
            return True, "dry-run planned route recheck; no input"
        click(image, (1152, 1037), "route_dismiss_hunting_result")
        ok, image, _ = wait("route-hunting-entry", _hunting_map)
        if not ok:
            return stop("hunting map not verified after result; no repeat input")
    ok, details = _hunting_map(image)
    if not ok:
        return stop("route recheck requires verified hunting map")
    initial_rice = details["free_rice"]
    if dry_run:
        return True, "dry-run planned route recheck; no input"
    click(image, (210, 312), "route_gold_category")
    ok, image, details = wait("route-gold-map", route_recognizer())
    if not ok or details["reading"]["free_rice"] != initial_rice:
        return stop("gold route or unchanged free rice unverified; no setup input")
    click(image, (1973, 1188), "route_open_setup")
    readings = []
    for sample in range(2):
        if sample:
            time.sleep(2.0)
        ok, image, details = wait(f"route-setup-{sample + 1}", route_recognizer(True))
        if not ok:
            return stop("route setup unverified; no confirmation or blind cancel")
        readings.append(details["reading"])
    if readings[0] != readings[1] or readings[0]["free_rice"] != initial_rice:
        return stop("route readings changed; no further input")
    click(image, (966, 939), "route_cancel_setup")
    ok, image, details = wait("route-after-cancel", route_recognizer())
    if not ok or details["reading"]["free_rice"] != initial_rice:
        return stop("route cancellation or unchanged rice unverified")
    click(image, (210, 185), "route_restore_hunting")
    ok, image, details = wait("route-restored-hunting", _hunting_map)
    if not ok or details["free_rice"] != initial_rice:
        return stop("hunting map restoration or unchanged rice unverified")
    reason = (f"航线只读复核：哥布林遗迹/极难，免费米饭 {initial_rice}/90，"
              "1 次费用 6 米饭；已取消并恢复狩猎场，未扫荡或分配")
    logger.event(action="stop", result="success", reading=readings[0], reason=reason)
    return True, reason
