"""One free goddess prayer through the PC cartridge collection.

Only the observed Fantasia Square spawn is supported. Missing action/task text
allows a no-input skip, not an assertion that an earlier prayer succeeded.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from free_gacha import RunLogger, safe_capture_client
from game_text_recognition import _ocr_engine, recognize_home_labels
from open_game import find_game_window
from reward_flow import click_ratio_logged, prepare_actionable_home, wait_for_recognition


def recognize_prayer_page(image: Image.Image) -> tuple[str, dict[str, Any]]:
    """Require exact, positioned PC text; never turn OCR errors into a skip."""
    if abs(image.width / image.height - 16 / 9) > 0.01:
        return "unknown", {"error": "unverified client aspect ratio"}
    try:
        result = _ocr_engine()(np.asarray(image.convert("RGB")))
        entries = []
        uncertain = []
        boxes = result.boxes if result.boxes is not None else ()
        texts = result.txts if result.txts is not None else ()
        scores = result.scores if result.scores is not None else ()
        for box, text, score in zip(boxes, texts, scores):
            if 0.55 <= float(score) < 0.90:
                uncertain.append((float(np.mean(box[:, 0])) / image.width,
                                  float(np.mean(box[:, 1])) / image.height, str(text)))
            if float(score) >= 0.90:
                entries.append((
                    float(np.mean(box[:, 0])) / image.width,
                    float(np.mean(box[:, 1])) / image.height,
                    "".join(str(text).split()),
                ))
    except Exception as exc:
        return "unknown", {"error": repr(exc)}

    def locate(text: str, region: tuple[float, float, float, float], *, prefix: bool = False):
        left, top, right, bottom = region
        found = [(x, y) for x, y, actual in entries
                 if left <= x <= right and top <= y <= bottom
                 and (actual.startswith(text) if prefix else actual == text)]
        return found[0] if len(found) == 1 else None

    details: dict[str, Any] = {"texts": entries, "uncertain": uncertain}
    title = locate("游戏卡珍藏集", (0.10, 0.01, 0.28, 0.08))
    if title:
        life = locate("生活玩法游戏卡带4/4", (0.06, 0.12, 0.72, 0.85), prefix=True)
        square = locate("奇幻广场", (0.06, 0.20, 0.92, 0.90))
        if life and square and square[1] > life[1] + 0.10:
            # Click the poster below its plus/info button, not a mobile coordinate.
            details["card_point"] = (square[0], square[1] - 0.10)
            return "square_card", details
        return "collection", details

    recruit = locate("招募情况", (0.16, 0.12, 0.28, 0.18))
    team = locate("创建队伍", (0.82, 0.10, 0.97, 0.17))
    if recruit and team:
        # The light PC recruit button must be undimmed; do not act under a modal.
        crop = image.convert("RGB").crop((
            round(image.width * 0.17), round(image.height * 0.125),
            round(image.width * 0.255), round(image.height * 0.17),
        ))
        if float(np.asarray(crop).mean()) < 150:
            return "unknown", {**details, "error": "square controls are dimmed"}
        wish = locate("向女神像许愿", (0.44, 0.09, 0.58, 0.16))
        task = locate("每日奖励", (0.81, 0.16, 0.92, 0.21))
        if wish and task:
            details["wish_point"] = wish
            return "prayer_ready", details
        if not wish and not task:
            if any((0.44 <= x <= 0.58 and 0.09 <= y <= 0.16)
                   or (0.81 <= x <= 0.92 and 0.16 <= y <= 0.21)
                   for x, y, _text in entries + uncertain):
                return "unknown", {**details, "error": "ambiguous prayer/task text"}
            return "prayer_unavailable", details
    return "unknown", details


def run_plaza_prayer(*, dry_run: bool, log_root: Path) -> tuple[bool, str]:
    """Enter the verified square, send at most one prayer, verify, and leave."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="plaza_prayer", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        logger.failure("game window not found")
        return False, "game window not found"

    def stop(reason: str) -> tuple[bool, str]:
        logger.failure(reason)
        return False, reason

    def wait_page(expected: set[str], label: str, timeout: float = 30.0):
        def recognize(candidate: Image.Image):
            state, details = recognize_prayer_page(candidate)
            return state in expected, {**details, "state": state}
        return wait_for_recognition(
            hwnd, logger=logger, recognize=recognize, label=label, timeout=timeout,
        )

    ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
    if not ok:
        return stop(reason)
    click_ratio_logged(hwnd, image, (2033 / 2304, 1170 / 1296),
                       key="prayer_game_cards", logger=logger, dry_run=dry_run)
    if dry_run:
        return True, "dry-run planned square cartridge entry; no prayer sent"
    ok, image, details = wait_page({"collection", "square_card"}, "collection")
    if not ok:
        return stop("未识别游戏卡珍藏集，未执行祈求")
    for index in range(2):
        if details["state"] == "square_card":
            break
        click_ratio_logged(hwnd, image, (2268 / 2304, 1210 / 1296),
                           key=f"life_scrollbar_{index + 1}", logger=logger)
        # A scrollbar click must not become an unverified swipe on a card.
        time.sleep(0.8)
        ok, image, details = wait_page({"collection", "square_card"}, f"life-cards-{index + 1}")
        if not ok:
            return stop("卡带列表滚动后页面未知，停止")
    if details["state"] != "square_card":
        return stop("生活玩法中未找到已解锁奇幻广场，停止")
    click_ratio_logged(hwnd, image, tuple(details["card_point"]),
                       key="square_cartridge", logger=logger)
    ok, image, details = wait_page(
        {"prayer_ready", "prayer_unavailable"}, "square-arrival", timeout=90.0,
    )
    if not ok:
        return stop("未到达可辨认的广场祈求页面；不导航未知位置")

    prayed = details["state"] == "prayer_ready"
    if prayed:
        click_ratio_logged(hwnd, image, tuple(details["wish_point"]),
                           key="free_goddess_prayer", logger=logger)
    # Never retry the prayer. Both the action and daily-task text must disappear
    # on two fresh frames with the same positive square anchors still present.
    for sample in range(2):
        if sample:
            time.sleep(2.0)
        ok, image, details = wait_page({"prayer_unavailable"}, f"prayer-cleared-{sample + 1}")
        if not ok:
            return stop("许愿入口或每日奖励未稳定消失；未重试、未标记完成")
    click_ratio_logged(hwnd, image, (2156 / 2304, 68 / 1296),
                       key="prayer_home", logger=logger)
    ok, _image, _details = wait_for_recognition(
        hwnd, logger=logger, recognize=recognize_home_labels,
        label="prayer-return-home", timeout=45.0,
    )
    if not ok:
        return stop("祈求后未确认返回主页")
    reason = ("广场免费祈求已单次执行，入口与每日奖励消失并返回主页" if prayed
              else "广场未显示许愿入口及每日奖励，安全跳过；不推断此前已完成")
    logger.event(action="stop", result="success", skipped=not prayed, reason=reason)
    return True, reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", type=Path, default=Path("logs/plaza_prayer") /
                        datetime.now().strftime("%Y-%m-%d/%H%M%S"))
    args = parser.parse_args()
    ok, reason = run_plaza_prayer(dry_run=args.dry_run, log_root=args.log_root)
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
