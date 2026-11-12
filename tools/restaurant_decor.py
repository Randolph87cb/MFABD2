"""Read the verified PC restaurant decoration balance; never collect or spend it."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from business_management import open_business_management, enter_restaurant, return_home_from_restaurant
from free_gacha import RunLogger, classify_state, safe_capture_client
from game_text_recognition import _ocr_engine
from open_game import find_game_window
from reward_flow import prepare_actionable_home


def recognize_decor_balance(image: Image.Image) -> tuple[dict[str, int] | None, dict[str, Any]]:
    """Require one high-confidence total and daily counter on an unobstructed restaurant."""
    if image.size != (2304, 1296):
        return None, {"error": "unverified client size"}
    try:
        state, _ = classify_state(image)
        if state != "restaurant_home":
            return None, {"state": state, "error": "requires restaurant_home"}
        result = _ocr_engine()(np.asarray(image.convert("RGB")))
        boxes = result.boxes if result.boxes is not None else ()
        texts = result.txts if result.txts is not None else ()
        scores = result.scores if result.scores is not None else ()
        if not len(boxes) == len(texts) == len(scores):
            return None, {"error": "missing OCR confidence or position"}
        entries = [(float(np.mean(box[:, 0])), float(np.mean(box[:, 1])),
                    "".join(str(text).split()), float(score))
                   for box, text, score in zip(boxes, texts, scores)]
        details: dict[str, Any] = {"state": state, "texts": entries}
        # The real decoration tooltip is a negative sample even though the top
        # counters and restaurant title remain visible behind it.
        if any(re.search(r"装饰币|购买|充值|消耗|不足|确认|取消|重置|每日最多", text)
               for _, _, text, score in entries if score >= 0.55):
            return None, {**details, "error": "tooltip or dialog present"}

        def one(pattern, region):
            left, top, right, bottom = region
            candidates = [(text, score) for x, y, text, score in entries
                          if left <= x <= right and top <= y <= bottom]
            if len(candidates) != 1:
                return None
            text, score = candidates[0]
            return re.fullmatch(pattern, text) if score >= 0.95 else None

        title = one("格鲁菲餐厅", (150, 35, 345, 95))
        employees = one("员工", (150, 1210, 260, 1270))
        customers = one("客人", (285, 1210, 400, 1270))
        settlement = one("结算", (1880, 1150, 2150, 1230))
        total = one(r"(0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)", (1395, 40, 1535, 90))
        daily = one(r"每日(0|[1-9]\d?)/20", (1380, 90, 1510, 130))
        house = np.asarray(image.convert("RGB"))[48:82, 2140:2175]
        bright = float(np.mean(np.min(house, axis=2) > 210))
        details["home_bright"] = bright
        if not all((title, employees, customers, settlement, total)) or bright < 0.20:
            return None, {**details, "error": "unverified restaurant controls or counters"}
        if daily is None:
            # NPC speech can temporarily cover/merge with this counter. Reject
            # this frame; the runner may only capture again without input.
            return None, {**details, "error": "unverified daily counter"}
        earned = int(daily.group(1))
        if earned > 20:
            return None, {**details, "error": "daily count exceeds verified limit"}
        return {"balance": int(total.group(1).replace(",", "")),
                "earned_today": earned, "daily_limit": 20}, details
    except Exception as exc:
        return None, {"error": repr(exc)}


def run_restaurant_decor_recheck(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Home → restaurant → two consistent reads → home; no reward or NPC input."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="restaurant_decor_recheck", dry_run=dry_run)

    def stop(reason):
        logger.failure(reason)
        return False, reason

    hwnd = find_game_window()
    if not hwnd:
        return stop("game window not found")
    ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
    if not ok:
        return stop(reason)
    if image.size != (2304, 1296):
        return stop("unverified client size; no restaurant input")
    if dry_run:
        return True, "dry-run planned decoration balance recheck; no input"
    for operation, directory in ((open_business_management, "01-business-entry"),
                                 (enter_restaurant, "02-restaurant-entry")):
        ok, reason = operation(dry_run=False, log_root=log_root / directory)
        if not ok:
            return stop(reason)
    readings = []
    # Allow a transient NPC bubble to clear, while still requiring two
    # consecutive verified readings. No input is sent during these captures.
    for sample in range(5):
        if sample:
            time.sleep(2.0)
        image = safe_capture_client(hwnd, logger=logger)
        reading, details = recognize_decor_balance(image)
        path = logger.save_image(image, f"decor-balance-{sample + 1}.png")
        logger.event(action="decor_balance_recheck", sample=sample + 1,
                     reading=reading, details=details, screenshot=str(path))
        if reading is None:
            if details.get("error") == "unverified daily counter":
                readings.clear()
                continue
            return stop("装饰币余额/每日额度未确认，不领取、不盲目返回")
        readings.append(reading)
        if len(readings) == 2:
            break
    if len(readings) != 2:
        return stop("装饰币每日额度未连续两帧确认，只读复采达到上限")
    if readings[0] != readings[1]:
        return stop("装饰币两帧读数变化，不推断领取或继续输入")
    ok, reason = return_home_from_restaurant(dry_run=False, log_root=log_root / "03-return-home")
    if not ok:
        return stop(reason)
    reading = readings[0]
    reason = (f"装饰币只读复核：余额 {reading['balance']}，"
              f"每日 {reading['earned_today']}/{reading['daily_limit']}；已返回主页，未领取或消费")
    logger.event(action="stop", result="success", reading=reading, reason=reason)
    return True, reason
