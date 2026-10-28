"""One free random-cabin like per UTC+8 game week, with no ambiguous replay."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from free_gacha import RunLogger
from game_text_recognition import _ocr_engine, recognize_home_labels
from open_game import find_game_window
from reward_flow import click_ratio_logged, prepare_actionable_home, wait_for_recognition


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / "state" / "weekly_cabin"
GAME_TIMEZONE = timezone(timedelta(hours=8))
LIKE_BOX = (2077, 112, 2130, 162)


def game_week_key(now: datetime | None = None) -> str:
    """Monday 08:00 in UTC+8; naive test values represent the same game zone."""
    local = now or datetime.now(GAME_TIMEZONE)
    local = local.replace(tzinfo=GAME_TIMEZONE) if local.tzinfo is None else local.astimezone(GAME_TIMEZONE)
    day = (local - timedelta(hours=8)).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def _write_record(path: Path, record: dict[str, Any], *, reserve: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path if reserve else path.with_suffix(".tmp")
    # Exclusive creation claims this week before input. Even a partial write
    # blocks subsequent attempts rather than allowing a second like.
    with target.open("x" if reserve else "w", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    if not reserve:
        target.replace(path)


def recognize_cabin_page(image: Image.Image) -> tuple[str, dict[str, Any]]:
    """Only the observed PC layout; own/popular/loading pages cannot send likes."""
    if image.size != (2304, 1296):
        return "unknown", {"error": "unverified client size"}
    try:
        result = _ocr_engine()(np.asarray(image.convert("RGB")))
        entries = []
        for box, text, score in zip(
            result.boxes if result.boxes is not None else (),
            result.txts if result.txts is not None else (),
            result.scores if result.scores is not None else (),
        ):
            if float(score) >= 0.95:
                entries.append((float(np.mean(box[:, 0])), float(np.mean(box[:, 1])),
                                "".join(str(text).split())))
    except Exception as exc:
        return "unknown", {"error": repr(exc)}

    def find(pattern: str, region: tuple[int, int, int, int]):
        left, top, right, bottom = region
        matches = [(x, y, text) for x, y, text in entries
                   if left <= x <= right and top <= y <= bottom and re.fullmatch(pattern, text)]
        return matches[0] if len(matches) == 1 else None

    details: dict[str, Any] = {"texts": entries}
    own = find("我的小屋", (330, 25, 500, 90))
    edit = find("编辑房间", (1850, 1160, 2150, 1240))
    frame = np.asarray(image.convert("RGB"))
    if own and edit and frame[1180:1220, 1950:2050].mean() > 150:
        return "own_cabin", details
    title = find("参观", (330, 25, 420, 90))
    again = find("再次探查", (1850, 1160, 2150, 1240))
    random = find("随机", (285, 240, 360, 290))
    if not (title and again and random):
        return "unknown", details
    if frame[1180:1220, 1950:2050].mean() <= 150:
        return "unknown", {**details, "error": "dimmed cabin controls"}
    selected = float(np.mean(np.min(frame[253:278, 294:345], axis=2) > 210))
    details["random_highlight"] = selected
    if selected < 0.10:
        # A known visit page only permits switching the category, never liking.
        return "visit_other", details
    identity = find(r"[^\s]{1,32}", (1000, 1175, 1310, 1220))
    counter = find(r"0|[1-9]\d{0,8}", (2030, 165, 2170, 215))
    if not (identity and counter):
        return "unknown", {**details, "error": "unverified cabin identity or like count"}
    details.update(identity=identity[2], count=int(counter[2]))
    icon = np.asarray(image.convert("RGB").crop(LIKE_BOX)).astype(int)
    with Image.open(Path(__file__).parent / "assets" / "cabin-like.png") as template:
        expected = np.min(np.asarray(template.convert("RGB")), axis=2) > 210
    white = np.min(icon, axis=2) > 210
    blue = ((icon[:, :, 2] > 170) & (icon[:, :, 1] > 90)
            & (icon[:, :, 2] - icon[:, :, 0] > 70))
    def overlap(mask):
        return float(np.sum(mask & expected) / max(1, np.sum(mask | expected)))
    details.update(white_match=overlap(white), blue_match=overlap(blue))
    if details["white_match"] >= 0.90 and details["blue_match"] < 0.10:
        return "like_ready", details
    if details["blue_match"] >= 0.90 and details["white_match"] < 0.10:
        return "like_done", details
    return "unknown", details


def run_weekly_cabin(*, dry_run: bool, log_root: Path,
                     state_root: Path | None = None) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    week = game_week_key()
    state_path = (state_root if state_root is not None else STATE_ROOT) / f"{week}.json"
    logger.event(action="start", flow="weekly_cabin", week=week, dry_run=dry_run)

    def stop(reason: str) -> tuple[bool, str]:
        logger.failure(reason)
        return False, reason

    if state_path.exists():
        try:
            record = json.loads(state_path.read_text(encoding="utf-8"))
            if (record.get("version") != 1 or record.get("week") != week
                    or record.get("status") not in {"completed", "action_pending"}):
                raise ValueError("invalid weekly cabin record")
            if record["status"] == "completed" and (
                not isinstance(record.get("identity"), str) or not record["identity"]
                or type(record.get("before_count")) is not int
                or record["before_count"] < 0
                or record.get("after_count") != record["before_count"] + 1
            ):
                raise ValueError("completed record lacks the verified like change")
        except (OSError, ValueError, AttributeError) as exc:
            return stop(f"周常小屋记录异常，禁止输入：{exc}")
        if record["status"] == "completed":
            reason = f"skipped:小屋一次点赞在游戏周 {week} 已验证完成"
            logger.event(action="stop", result="success", reason=reason)
            return True, reason
        return stop("本游戏周小屋点赞输入未决，禁止自动重放；需核对原始证据")

    hwnd = find_game_window()
    if not hwnd:
        return stop("game window not found")

    def wait_page(expected: set[str], label: str, *, identity=None, count=None):
        def recognize(candidate: Image.Image):
            state, details = recognize_cabin_page(candidate)
            matches = (state in expected and (identity is None or details.get("identity") == identity)
                       and (count is None or details.get("count") == count))
            return matches, {**details, "state": state}
        return wait_for_recognition(hwnd, logger=logger, recognize=recognize,
                                    label=label, timeout=30.0)

    ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
    if not ok:
        return stop(reason)
    if image.size != (2304, 1296):
        return stop("unverified client size; no cabin input")
    click_ratio_logged(hwnd, image, (200 / 2304, 235 / 1296), key="weekly_cabin_entry",
                       logger=logger, dry_run=dry_run)
    if dry_run:
        return True, "dry-run planned cabin entry; no like or weekly record written"
    ok, image, _details = wait_page({"own_cabin"}, "own-cabin")
    if not ok:
        return stop("未确认自家小屋，不进入参观")
    click_ratio_logged(hwnd, image, (209 / 2304, 299 / 1296), key="cabin_visit", logger=logger)
    ok, image, details = wait_page({"visit_other", "like_ready", "like_done"}, "cabin-visit")
    if not ok:
        return stop("参观页未知，不点赞")
    if details["state"] == "visit_other":
        click_ratio_logged(hwnd, image, (323 / 2304, 265 / 1296), key="cabin_random", logger=logger)
        ok, image, details = wait_page({"like_ready", "like_done"}, "random-cabin")
        if not ok:
            return stop("未确认随机小屋、身份与点赞状态")
    liked = details["state"] == "like_ready"
    identity, before = details["identity"], details["count"]
    if liked:
        if game_week_key() != week:
            return stop("跨越游戏周刷新边界，未点赞")
        record = {"version": 1, "week": week, "status": "action_pending",
                  "identity": identity, "before_count": before, "log_root": str(log_root),
                  "reserved_at": datetime.now(GAME_TIMEZONE).isoformat()}
        try:
            _write_record(state_path, record, reserve=True)
        except OSError as exc:
            return stop(f"无法独占持久化本周点赞意图，未输入：{exc}")
        if game_week_key() != week:
            return stop("持久化意图后跨越游戏周，未点赞")
        click_ratio_logged(hwnd, image, (2101 / 2304, 139 / 1296), key="one_free_cabin_like", logger=logger)
    # Never retry/toggle a like, even when the click appears lost.
    for sample in range(2):
        if sample:
            time.sleep(2.0)
        ok, image, details = wait_page({"like_done"}, f"cabin-liked-{sample + 1}",
                                       identity=identity, count=before + 1 if liked else before)
        if not ok:
            return stop("点赞后未确认同一小屋蓝色按钮与精确计数；禁止重试")
    click_ratio_logged(hwnd, image, (208 / 2304, 60 / 1296), key="cabin_return_home", logger=logger)
    ok, _image, _details = wait_for_recognition(hwnd, logger=logger, recognize=recognize_home_labels,
                                               label="cabin-home", timeout=45.0)
    if not ok:
        return stop("小屋点赞后未确认返回主页")
    if liked:
        record.update(status="completed", after_count=before + 1,
                      completed_at=datetime.now(GAME_TIMEZONE).isoformat())
        try:
            _write_record(state_path, record, reserve=False)
        except OSError as exc:
            return stop(f"点赞已验证但周完成记录写入失败，禁止重放：{exc}")
        reason = "小屋单次免费点赞、同身份计数 +1 两帧与主页均已确认"
    else:
        reason = "skipped:随机小屋已为蓝色点赞，未输入；不推断本周任务完成"
    logger.event(action="stop", result="success", reason=reason, week=week)
    return True, reason
