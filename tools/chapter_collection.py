"""One absorb in the demonstrated normal, safe Lugo town; never cross maps.

The talent card itself casts the skill. Empty shortcut slots are only used to
open that page; no shortcut configuration, upgrades or resource purchases occur.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from free_gacha import RunLogger
from game_text_recognition import _ocr_engine, recognize_home_labels
from open_game import find_game_window
from reward_flow import click_ratio_logged, prepare_actionable_home, wait_for_recognition


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / "state/chapter_collection"
ASSETS = Path(__file__).parent / "assets"
GAME_ZONE = timezone(timedelta(hours=8))


def game_day_key(now=None):
    local = now or datetime.now(GAME_ZONE)
    local = local.replace(tzinfo=GAME_ZONE) if local.tzinfo is None else local.astimezone(GAME_ZONE)
    return (local - timedelta(hours=8)).date().isoformat()


def _write_record(path, record, *, reserve=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path if reserve else path.with_suffix(".tmp")
    with target.open("x" if reserve else "w", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    if not reserve:
        target.replace(path)


def _entries(image):
    result = _ocr_engine()(np.asarray(image.convert("RGB")))
    boxes = result.boxes if result.boxes is not None else ()
    texts = result.txts if result.txts is not None else ()
    scores = result.scores if result.scores is not None else ()
    if not len(boxes) == len(texts) == len(scores):
        raise ValueError("incomplete OCR")
    return [(float(np.mean(box[:, 0])), float(np.mean(box[:, 1])),
             "".join(str(text).split()), float(score))
            for box, text, score in zip(boxes, texts, scores)]


def _one(entries, pattern, box):
    left, top, right, bottom = box
    matches = [(text, score) for x, y, text, score in entries
               if left <= x <= right and top <= y <= bottom]
    if len(matches) != 1 or not matches[0][1] >= .95:
        return None
    return re.fullmatch(pattern, matches[0][0])


def _crop_text(image, box, pattern):
    crop = image.crop(box)
    entries = _entries(crop.resize((crop.width * 3, crop.height * 3)))
    return _one(entries, pattern, (0, 0, crop.width * 3, crop.height * 3))


def _difference(image, name, box, *, shift=False):
    with Image.open(ASSETS / (name + ".png")) as source:
        template = np.asarray(source.convert("RGB"), dtype=float)
    left, top, right, bottom = box
    # Camera settling also shifts the same town horizontally on chapter entry.
    horizontal_offsets = range(-9, 10, 3) if shift else (0,)
    offsets = range(-60, 61, 3) if shift else (0,)
    return min(float(np.abs(np.asarray(image.crop((left + dx, top + dy, right + dx, bottom + dy)),
                                       dtype=float) - template).mean())
               for dx in horizontal_offsets for dy in offsets)


def recognize_chapter_page(image):
    """Only observed 2304x1296 PC layouts; unknowns never become safe skips."""
    if image.size != (2304, 1296):
        return "unknown", {"error": "unverified client size"}
    try:
        entries = _entries(image)
        details = {"texts": entries}
        if any(re.search(r"购买|充值|补充|不足|确认|取消|自动移动|是否|无法使用", text)
               for _, _, text, score in entries if score >= .55):
            return "unknown", {**details, "error": "unsafe or obstructing page"}
        one = lambda pattern, box: _one(entries, pattern, box)
        if one("游戏卡珍藏集", (285, 30, 505, 90)):
            # The current chapter has a play icon / 游戏中 overlay on its art.
            # Match that observed variant without relaxing the card identity.
            if (one("剧情游戏卡20/20", (185, 480, 410, 545))
                    and min(_difference(image, name, (230, 610, 410, 725))
                            for name in ("chapter-card-one", "chapter-card-one-playing")) < 8):
                return "chapter_card", details
            return "collection", details
        if (one("安全卢戈镇", (280, 30, 490, 90))
                and one("普通", (580, 30, 655, 90))
                and one("世界地图", (1900, 1170, 2130, 1240))
                and one("此地区没有正在进行的探索。", (1100, 90, 1430, 150))
                and _difference(image, "chapter-town-map", (590, 720, 790, 860)) < 8):
            # Reject the nonfunctional magic-circle submenu and any other map overlay.
            if any(text in {"战斗区", "魔法阵"} for _, _, text, score in entries if score >= .55):
                return "unknown", {**details, "error": "map submenu open"}
            value = one(r"([0-3])/3|还剩([1-3])天", (275, 1185, 375, 1230))
            if not value or not one(r"[0-3]/3", (385, 1185, 460, 1230)):
                return "unknown", {**details, "error": "map collection counter missing"}
            if value.group(2):
                return "map_cooldown", {**details, "days": int(value.group(2))}
            if value.group(1) == "0":
                return "map_ready", details
            return "unknown", {**details, "error": "unverified partial collection"}
        if (one("移动魔法阵", (300, 30, 510, 90))
                and one("安全卢戈镇", (1040, 1150, 1260, 1220))):
            return "portal", details
        if one("天赋技能", (150, 805, 260, 875)):
            # PC OCR joins the three adjacent counts into one line. Require all
            # three in their exact order, rather than guessing a split digit.
            count = one(r"每日使用([0-7])/7每日使用([0-7])/7每日使用([0-7])/7",
                        (900, 1050, 1040, 1100))
            cost = _crop_text(image, (810, 1134, 845, 1160), "10")
            balance = one(r"[1-9]\d{0,2}(?:,\d{3})+|0|[1-9]\d{0,2}", (1980, 40, 2170, 95))
            available = _difference(image, "chapter-absorb-portrait", (790, 980, 860, 1040)) < 8
            used_card = _difference(image, "chapter-absorb-portrait-used", (790, 980, 860, 1040)) < 8
            if (count and cost and balance
                    and one("吸收", (780, 1095, 870, 1132))
                    and one("传说", (780, 1170, 870, 1220))
                    and (available or used_card)):
                return "talent", {**details, "used": int(count.group(1)),
                                  "available": available,
                                  "other_used": tuple(map(int, count.groups()[1:])),
                                  "balance": int(balance.group().replace(",", "")), "cost": 10}
            return "unknown", {**details, "error": "unverified absorb identity, count or cost"}
        if (one("再次挑战主线任务", (1880, 175, 2100, 220))
                and _crop_text(image, (1880, 258, 2140, 305), "立刻前往探索告示板")):
            circle = _difference(image, "chapter-circle-scene", (500, 300, 900, 460), shift=True)
            board = _difference(image, "chapter-board-scene", (450, 450, 650, 600), shift=True)
            if min(circle, board) < 10:
                scene = "circle" if circle < board else "board"
                empty = min(_difference(image, name, (1800, 1030, 1870, 1090))
                            for name in ("chapter-empty-slot-before", "chapter-empty-slot")) < 10
                # The empty-slot cross is opaque RGB(128,128,128). Requiring
                # both complete strokes prevents a small configured skill
                # glyph from passing a whole-crop average and being cast.
                pixels = np.asarray(image.convert("RGB"), dtype=float)
                strokes = np.concatenate((pixels[1045:1080, 1837], pixels[1061, 1820:1854]))
                empty = empty and float(np.mean(np.max(np.abs(strokes - 128), axis=1) <= 2)) >= .98
                hand = _difference(image, "chapter-portal-hand", (1660, 1030, 1725, 1090)) < 10
                return "field", {**details, "scene": scene, "empty_slot": empty, "portal_hand": hand}
        return "unknown", details
    except Exception as exc:
        return "unknown", {"error": repr(exc)}


def run_chapter_absorb(*, dry_run: bool, log_root: Path, state_root=None, now=None):
    """Known chapter → two map reads → optional one cast → verified home.

    An unresolved intent blocks later days too. Completion is saved only after
    the return home. A manual result must never be inferred from a pending file.
    """
    logger = RunLogger(log_root, annotate_clicks=True)
    state_root = Path(state_root) if state_root is not None else STATE_ROOT
    day = game_day_key(now)
    record_path = state_root / (day + ".json")
    try:
        for path in state_root.glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            if (type(value.get("version")) is not int or value["version"] != 1
                    or value.get("status") not in {"completed", "skipped"}
                    or value.get("target") != "卢戈镇" or value.get("game_day") != path.stem
                    or value.get("returned_home") is not True or not value.get("home_screenshot")):
                raise ValueError(f"unresolved collection record: {path.name}")
        if record_path.exists():
            return True, "skipped:本游戏日单章采集已记录，不重复输入"
    except Exception as exc:
        logger.failure(str(exc))
        return False, f"单章采集记录未决，停止：{exc}"
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"
    if dry_run:
        return True, "dry-run planned Lugo absorb; no input or state write"

    def stop(reason):
        raise RuntimeError(reason)

    def wait(expected, label, timeout=30):
        def recognize(candidate):
            state, details = recognize_chapter_page(candidate)
            return state in expected, {**details, "state": state}
        ok, image, details = wait_for_recognition(
            hwnd, logger=logger, label=label, recognize=recognize, timeout=timeout)
        if not ok:
            stop(f"{label} 未确认；停止且不重试输入")
        return image, details

    def click(image, point, key):
        click_ratio_logged(hwnd, image, (point[0] / 2304, point[1] / 1296), key=key, logger=logger)

    def two(expected, label, fields):
        readings = []
        for index in range(2):
            if index:
                time.sleep(2)
            image, details = wait(expected, f"{label}-{index + 1}")
            readings.append({key: details.get(key) for key in fields})
        if readings[0] != readings[1]:
            stop(f"{label} 两帧读数变化")
        return image, details

    record = {"version": 1, "game_day": day, "target": "卢戈镇", "status": "action_pending"}
    try:
        ok, image, reason = prepare_actionable_home(hwnd, logger=logger)
        if not ok:
            stop(reason)
        click(image, (2033, 1170), "chapter_collection_entry")
        image, details = wait({"collection", "chapter_card"}, "chapter-collection")
        if details["state"] == "collection":
            click(image, (2268, 164), "chapter_story_scroll")
            image, details = wait({"chapter_card"}, "chapter-story-card")
        click(image, (320, 670), "chapter_one")
        image, details = wait({"field"}, "chapter-arrival", timeout=90)
        click(image, (247, 201), "chapter_map")
        image, details = two({"map_ready", "map_cooldown"}, "chapter-map", ("state", "days"))
        skipped = details["state"] == "map_cooldown"
        record["map_before"] = str(logger.save_image(image, "chapter-map-before.png"))
        click(image, (208, 60), "chapter_map_back")
        image, details = wait({"field"}, "chapter-field")
        if not skipped:
            # The two whitelisted spawns have independently demonstrated portal inputs.
            if details["scene"] == "board":
                click(image, (748, 1235), "chapter_visible_portal")
            elif details["portal_hand"]:
                click(image, (1694, 1064), "chapter_portal_hand")
            else:
                stop("缺少可用传送点交互控件")
            image, _ = wait({"portal"}, "chapter-portal")
            click(image, (162, 60), "chapter_portal_back")
            image, _ = wait({"field"}, "chapter-portal-return")
            click(image, (2194, 1215), "chapter_talent_tab_three")
            image, details = wait({"field"}, "chapter-tab-three")
            if not details["empty_slot"]:
                stop("技能栏非空，不覆盖或施放已配置技能")
            click(image, (1835, 1063), "chapter_talent_page")
            image, before = two({"talent"}, "chapter-before", ("used", "other_used", "balance", "cost", "available"))
            if not before["available"] or before["used"] >= 7 or before["balance"] < before["cost"]:
                stop("吸收次数耗尽或药剂不足，不施放/购买/补充")
            if game_day_key(now) != day:
                stop("游戏日已变化，不施放")
            record["before"] = {key: before[key] for key in ("used", "other_used", "balance", "cost")}
            record["before_screenshot"] = str(logger.save_image(image, "chapter-absorb-before.png"))
            _write_record(record_path, record, reserve=True)
            logger.event(action="absorb_intent", record=record)
            # This card CASTS. Never click it again, including after capture failure.
            click(image, (823, 1118), "chapter_cast_absorb_once")
            image, details = wait({"field"}, "chapter-after-cast")
            if not details["empty_slot"]:
                stop("施放后技能栏发生未知变化")
            click(image, (1835, 1063), "chapter_verify_talent")
            image, after = two({"talent"}, "chapter-after", ("used", "other_used", "balance", "cost"))
            if (after["used"] != before["used"] + 1 or after["balance"] != before["balance"] - 10
                    or after["other_used"] != before["other_used"]):
                stop("吸收结果未验证；保留意图，禁止重放")
            record["after"] = {key: after[key] for key in ("used", "balance", "cost")}
            record["after_screenshot"] = str(logger.save_image(image, "chapter-absorb-after.png"))
            click(image, (208, 60), "chapter_talent_back")
            image, _ = wait({"field"}, "chapter-after-talent")
            click(image, (247, 201), "chapter_result_map")
            image, _ = two({"map_cooldown"}, "chapter-result", ("state", "days"))
            record["map_after"] = str(logger.save_image(image, "chapter-map-after.png"))
            click(image, (208, 60), "chapter_result_map_back")
            image, _ = wait({"field"}, "chapter-result-field")
            click(image, (2008, 1214), "chapter_restore_tab_one")
            image, _ = wait({"field"}, "chapter-tab-one")
        click(image, (2157, 68), "chapter_return_home")
        ok, home_image, _ = wait_for_recognition(hwnd, logger=logger, label="chapter-home",
                                         recognize=recognize_home_labels, timeout=45)
        if not ok:
            stop("单章采集后未确认主页；保留未决记录")
        if game_day_key(now) != day:
            stop("返回后游戏日已变化；不标记完成")
        record.update(returned_home=True, home_screenshot=str(logger.save_image(home_image, "chapter-home.png")))
        record["status"] = "skipped" if skipped else "completed"
        _write_record(record_path, record, reserve=skipped)
        reason = ("skipped:卢戈镇吸收处于已识别冷却，两帧复核并返回主页" if skipped else
                  "卢戈镇仅施放一次吸收：次数+1、药剂-10、地图冷却均两帧验证并返回主页")
        logger.event(action="stop", result="success", record=record, reason=reason)
        return True, reason
    except Exception as exc:
        logger.failure(str(exc))
        return False, str(exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", type=Path, default=ROOT / "logs/chapter_collection" /
                        datetime.now().strftime("%Y-%m-%d/%H%M%S"))
    args = parser.parse_args()
    ok, reason = run_chapter_absorb(dry_run=args.dry_run, log_root=args.log_root)
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
