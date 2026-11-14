"""Read Lugo's probe collection counter and basic probe cost; never cast it."""

from __future__ import annotations

import re
import time
from pathlib import Path

import chapter_collection as chapter
from free_gacha import RunLogger
from open_game import find_game_window
from reward_flow import _recognize_actionable_home, click_ratio_logged, prepare_actionable_home, wait_for_recognition


def recognize_probe_map(image):
    state, details = chapter.recognize_chapter_page(image)
    if state not in {"map_ready", "map_cooldown"}:
        return False, details
    count = chapter._one(details["texts"], r"([0-3])/3", (385, 1185, 460, 1230))
    if not count:
        return False, {**details, "error": "unverified probe collection counter"}
    return True, {**details, "collected": int(count.group(1)), "limit": 3}


def recognize_probe_talent(image):
    if image.size != (2304, 1296):
        return False, {"error": "unverified client size"}
    try:
        entries = chapter._entries(image)
        details = {"texts": entries}
        if any(re.search(r"购买|充值|补充|不足|确认|取消|是否|无法使用", text)
               for _, _, text, score in entries if score >= .55):
            return False, {**details, "error": "unsafe or obstructing page"}
        one = lambda pattern, box: chapter._one(entries, pattern, box)
        balance = one(r"[1-9]\d{0,2}(?:,\d{3})+|0|[1-9]\d{0,2}", (1980, 40, 2170, 95))
        if not (one("天赋技能", (150, 805, 260, 875))
                and one("探查", (1220, 1095, 1320, 1132))
                and one("初级", (1220, 1170, 1320, 1205))
                and chapter._crop_text(image, (1250, 1131, 1292, 1166), "2")
                and balance
                and chapter._difference(image, "chapter-probe-portrait", (1225, 980, 1315, 1050)) < 8):
            return False, {**details, "error": "unverified basic probe identity, cost or balance"}
        return True, {**details, "cost": 2, "balance": int(balance.group().replace(",", ""))}
    except Exception as exc:
        return False, {"error": repr(exc)}


def recognize_probe_return_field(image):
    """Only used for return navigation, never to open or cast a skill.

    The native E key glyph can merge into the quest OCR after the talent page.
    Crop it out while retaining the known town scene and complete quest label.
    """
    state, details = chapter.recognize_chapter_page(image)
    if state == "field":
        return True, details
    if image.size != (2304, 1296) or details.get("error"):
        return False, details
    try:
        entries = details.get("texts", ())
        found = (chapter._one(entries, "再次挑战主线任务", (1880, 175, 2100, 220))
                 and chapter._crop_text(image, (1905, 258, 2140, 305), "立刻前往探索告示板")
                 and min(chapter._difference(image, name, box, shift=True) for name, box in (
                     ("chapter-circle-scene", (500, 300, 900, 460)),
                     ("chapter-board-scene", (450, 450, 650, 600)))) < 10)
        return bool(found), details
    except Exception as exc:
        return False, {"error": repr(exc)}


def recognize_probe_home(image):
    found, details = _recognize_actionable_home(image)
    if not found or image.size != (2304, 1296):
        return False, details
    try:
        entries = chapter._entries(image)
        balance = chapter._one(entries, r"[1-9]\d{0,2}(?:,\d{3})+|0|[1-9]\d{0,2}",
                               (1560, 40, 1750, 95))
        return bool(balance), {**details, "balance": int(balance.group().replace(",", "")) if balance else None}
    except Exception as exc:
        return False, {"error": repr(exc)}


def run_chapter_probe_recheck(*, dry_run: bool, log_root: Path):
    """Home → known town/map → basic probe card → unchanged map/home.

    No collection state is written and no resource, skill card, upgrade or
    configured shortcut is clicked. This audit cannot mark collection done.
    """
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="chapter_probe_recheck", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"

    def stop(reason):
        raise RuntimeError(reason)

    def wait(recognize, label, timeout=30):
        ok, image, details = wait_for_recognition(hwnd, logger=logger, label=label,
                                                recognize=recognize, timeout=timeout)
        if not ok:
            stop(f"{label} 未确认；停止且不重试输入")
        return image, details

    def page(expected):
        def recognize(image):
            state, details = chapter.recognize_chapter_page(image)
            return state in expected, {**details, "state": state}
        return recognize

    def two(recognize, label, fields):
        image, first = wait(recognize, label + "-1")
        time.sleep(2)
        image, second = wait(recognize, label + "-2")
        if any(first.get(key) != second.get(key) for key in fields):
            stop(label + " 两帧读数变化")
        return image, second

    def click(image, point, key):
        click_ratio_logged(hwnd, image, (point[0] / 2304, point[1] / 1296), key=key, logger=logger)

    try:
        ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
        if not ok:
            stop(reason)
        found, home_before = recognize_probe_home(image)
        if not found:
            stop("主页药剂余额或尺寸未确认")
        if dry_run:
            return True, "dry-run planned Lugo probe read-only recheck; no input or state write"
        click(image, (2033, 1170), "probe_collection_entry")
        image, details = wait(page({"collection", "chapter_card"}), "probe-collection")
        if details["state"] == "collection":
            click(image, (2268, 164), "probe_story_scroll")
            image, _ = wait(page({"chapter_card"}), "probe-story-card")
        click(image, (320, 670), "probe_chapter_one")
        image, _ = wait(page({"field"}), "probe-arrival", timeout=90)
        click(image, (247, 201), "probe_map")
        image, before = two(recognize_probe_map, "probe-map", ("collected", "limit"))
        click(image, (208, 60), "probe_map_back")
        image, _ = wait(page({"field"}), "probe-field")
        click(image, (2194, 1215), "probe_tab_three")
        image, details = wait(page({"field"}), "probe-tab-three")
        if not details.get("empty_slot"):
            stop("第3页技能栏非空，不打开已配置技能")
        click(image, (1835, 1063), "probe_talent_page")
        image, talent = two(recognize_probe_talent, "probe-talent", ("cost", "balance"))
        if talent["balance"] != home_before["balance"]:
            stop("探查复核药剂余额与初始主页不同")
        logger.event(action="probe_readonly", collected=before["collected"], limit=before["limit"],
                     cost=talent["cost"], balance=talent["balance"], cast=False)
        # The card label itself casts the skill. Only the top-left back is allowed.
        click(image, (208, 60), "probe_talent_back")
        image, _ = wait(recognize_probe_return_field, "probe-return-field")
        click(image, (247, 201), "probe_verify_map")
        image, after = two(recognize_probe_map, "probe-map-after", ("collected", "limit"))
        if any(before[key] != after[key] for key in ("collected", "limit")):
            stop("探查采集计数发生变化，不记只读完成")
        click(image, (208, 60), "probe_verify_map_back")
        image, _ = wait(recognize_probe_return_field, "probe-final-field")
        click(image, (2008, 1214), "probe_restore_tab_one")
        image, _ = wait(recognize_probe_return_field, "probe-tab-one")
        click(image, (2157, 68), "probe_return_home")
        image, home_after = wait(recognize_probe_home, "probe-home", timeout=45)
        if home_after["balance"] != home_before["balance"]:
            stop("只读返回后药剂余额变化")
        reason = (f"卢戈镇探查只读复核：采集 {before['collected']}/{before['limit']}，"
                  f"初级费用 {talent['cost']}，药剂 {talent['balance']}；已返回主页，未施放或拾取")
        logger.event(action="stop", result="success", reason=reason, cast=False)
        return True, reason
    except Exception as exc:
        logger.failure(str(exc))
        return False, str(exc)
