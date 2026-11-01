"""Visit the verified PC arcade list once per UTC+8 game week; never run a game."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from free_gacha import RunLogger, classify_state
from game_text_recognition import _ocr_engine, recognize_home_labels
from open_game import find_game_window
from reward_flow import click_ratio_logged, prepare_actionable_home, wait_for_recognition
from weekly_cabin import GAME_TIMEZONE, _write_record, game_week_key


STATE_ROOT = Path(__file__).resolve().parents[1] / "state" / "weekly_arcade"


def _positioned_text(image: Image.Image) -> list[tuple[float, float, str]]:
    result = _ocr_engine()(np.asarray(image.convert("RGB")))
    return [(float(np.mean(box[:, 0])), float(np.mean(box[:, 1])), "".join(str(text).split()))
            for box, text, score in zip(
                result.boxes if result.boxes is not None else (),
                result.txts if result.txts is not None else (),
                result.scores if result.scores is not None else ())
            if float(score) >= 0.95]


def recognize_arcade_page(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Require list title, ranking, all three run labels and an undimmed back arrow."""
    if image.size != (2304, 1296):
        return False, {"error": "unverified client size"}
    try:
        entries = _positioned_text(image)
    except Exception as exc:
        return False, {"error": repr(exc)}

    def one(text, region):
        left, top, right, bottom = region
        return sum(actual == text and left <= x <= right and top <= y <= bottom
                   for x, y, actual in entries) == 1

    anchors = {
        "title": one("街机游戏", (330, 25, 510, 90)),
        "ranking": one("小游戏排名", (1950, 30, 2150, 90)),
        "first_run": one("运行", (780, 300, 910, 360)),
        "second_run": one("运行", (1460, 300, 1590, 360)),
        "third_run": one("运行", (2140, 300, 2260, 360)),
    }
    arrow = np.asarray(image.convert("RGB"))[49:73, 203:216]
    bright = float(np.mean(np.min(arrow, axis=2) > 210))
    # A confirmation over the list must not authorize even its return click.
    danger = any(re.search("购买|充值|消耗|确认|取消|不足", text) for _, _, text in entries)
    return all(anchors.values()) and bright >= 0.20 and not danger, {
        "texts": entries, "anchors": anchors, "back_bright": bright, "danger": danger,
    }


def _home(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    state, details = classify_state(image)
    found, labels = recognize_home_labels(image) if state == "real_home" else (False, {})
    return image.size == (2304, 1296) and found, {"state": state, "details": details, "labels": labels}


def run_weekly_arcade(*, dry_run: bool, log_root: Path,
                      state_root: Path | None = None) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    week = game_week_key()
    path = (STATE_ROOT if state_root is None else state_root) / f"{week}.json"
    logger.event(action="start", flow="weekly_arcade_visit", week=week, dry_run=dry_run)

    def stop(reason):
        logger.failure(reason)
        return False, reason

    if path.exists():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(record, dict) or type(record.get("version")) is not int
                    or record["version"] != 1 or record.get("week") != week
                    or record.get("status") not in {"completed", "action_pending"}):
                raise ValueError("invalid weekly arcade record")
            if record["status"] == "completed":
                samples = record.get("visit_screenshots")
                if (not isinstance(samples, list) or len(samples) != 2
                        or not all(isinstance(p, str) and p for p in samples)
                        or samples[0] == samples[1] or record.get("returned_home") is not True
                        or not isinstance(record.get("home_screenshot"), str)
                        or not record["home_screenshot"]):
                    raise ValueError("completed record lacks two list frames and home verification")
        except (OSError, ValueError, TypeError) as exc:
            return stop(f"街机周记录异常，禁止输入：{exc}")
        if game_week_key() != week:
            return stop("读取街机记录时跨越游戏周，停止")
        if record["status"] != "completed":
            return stop("本周街机访问输入未决，禁止自动重放；需核对原始证据")
        reason = f"skipped:街机访问在游戏周 {week} 已验证完成"
        logger.event(action="stop", result="success", reason=reason)
        return True, reason

    hwnd = find_game_window()
    if not hwnd:
        return stop("game window not found")
    ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
    if not ok:
        return stop(reason)
    if image.size != (2304, 1296):
        return stop("unverified client size; no arcade input")
    try:
        points = [(x, y) for x, y, text in _positioned_text(image)
                  if text == "街机游戏" and 500 <= x <= 610 and 335 <= y <= 385]
    except Exception as exc:
        return stop(f"街机主页入口识别失败，未输入：{exc}")
    if len(points) != 1:
        return stop("未确认主页街机入口，未输入")
    point = (points[0][0] / image.width, points[0][1] / image.height)
    if dry_run:
        click_ratio_logged(hwnd, image, point, key="weekly_arcade_entry", logger=logger, dry_run=True)
        return True, "dry-run planned arcade visit; no input or weekly record written"
    if game_week_key() != week:
        return stop("进入街机前跨越游戏周，未输入")
    record = dict(version=1, week=week, status="action_pending", log_root=str(log_root),
                  reserved_at=datetime.now(GAME_TIMEZONE).isoformat())
    try:
        _write_record(path, record, reserve=True)
    except OSError as exc:
        return stop(f"无法独占持久化街机访问意图，未输入：{exc}")
    if game_week_key() != week:
        return stop("持久化街机意图后跨越游戏周，未输入")
    click_ratio_logged(hwnd, image, point, key="weekly_arcade_entry", logger=logger)
    # No repeat entry and no clicks on any Run button, including on a timeout.
    screenshots = []
    for sample in range(2):
        if sample:
            time.sleep(2.0)
        ok, image, _details = wait_for_recognition(
            hwnd, logger=logger, recognize=recognize_arcade_page,
            label=f"arcade-list-{sample + 1}", timeout=45.0)
        if not ok:
            return stop("街机列表未通过两帧验证；不点击运行、不重试入口")
        screenshots.append(str(logger.save_image(image, f"arcade-verified-{sample + 1}.png")))
    if game_week_key() != week:
        return stop("街机验证期间跨越游戏周，未记完成")
    click_ratio_logged(hwnd, image, (208 / 2304, 60 / 1296), key="arcade_return_home", logger=logger)
    ok, image, _details = wait_for_recognition(
        hwnd, logger=logger, recognize=_home, label="arcade-home", timeout=45.0)
    if not ok:
        return stop("街机访问后未确认返回主页，保留未决记录")
    if game_week_key() != week:
        return stop("返回主页时跨越游戏周，未记完成")
    record.update(status="completed", visit_screenshots=screenshots, returned_home=True,
                  home_screenshot=str(logger.save_image(image, "arcade-final-home.png")),
                  completed_at=datetime.now(GAME_TIMEZONE).isoformat())
    try:
        _write_record(path, record, reserve=False)
    except OSError as exc:
        return stop(f"街机访问已验证但周记录写入失败，禁止重放：{exc}")
    reason = "街机列表两帧及返回主页已确认；未运行小游戏"
    logger.event(action="stop", result="success", week=week, reason=reason)
    return True, reason
