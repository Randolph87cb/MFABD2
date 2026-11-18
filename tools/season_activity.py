"""Audit Blood Royale normal battle 1 eligibility; never start any battle."""

from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np

from chapter_collection import _difference, _entries, _one
from free_gacha import RunLogger
from open_game import find_game_window
from reward_flow import _recognize_actionable_home, click_ratio_logged, prepare_actionable_home, wait_for_recognition


LOCKED_CROPS = {
    "season-first-stars": (366, 156, 475, 200),
    "season-reward-lock": (2005, 197, 2038, 232),
    "season-manual-button": (1510, 1170, 1628, 1210),
}


def recognize_season_home(image, *, entry=False):
    found, details = _recognize_actionable_home(image)
    if not found or image.size != (2304, 1296):
        return False, details
    try:
        entries = _entries(image)
        balances = [_one(entries, r"0|[1-9]\d{0,2}(?:,\d{3})*", box) for box in (
            (1200, 40, 1350, 95), (1390, 40, 1570, 95), (1600, 40, 1770, 95))]
        if not all(balances):
            return False, {**details, "error": "unverified home balances"}
        if entry:
            # The stylized BLOOD lettering has unstable OCR confidence even
            # on an intact entry. Verify its native artwork, keeping the
            # adjacent text and balance confidence requirements unchanged.
            logo_difference = _difference(image, "season-home-blood-logo", (1910, 880, 2110, 935))
            details = {**details, "blood_logo_difference": logo_difference}
            if not (logo_difference < 10
                    and _one(entries, "ROYALE", (1970, 940, 2050, 980))
                    and _one(entries, "魔兽追踪者解锁", (1900, 980, 2120, 1025))):
                return False, {**details, "error": "unverified Blood Royale home entry"}
        return True, {**details, "balances": tuple(int(m.group().replace(",", "")) for m in balances)}
    except Exception as exc:
        return False, {"error": repr(exc)}


def recognize_season_page(image):
    """Only the demonstrated season main page and locked normal-1 layout."""
    if image.size != (2304, 1296):
        return "unknown", {"error": "unverified client size"}
    try:
        entries = _entries(image)
        details = {"texts": entries}
        if any(re.search(r"购买|充值|补充|不足|确认|取消|是否|快速战斗", text)
               for _, _, text, score in entries if score >= .55):
            return "unknown", {**details, "error": "unsafe, obstructing or unaudited battle page"}
        one = lambda pattern, box: _one(entries, pattern, box)
        arrow = np.asarray(image.convert("RGB"))[49:73, 203:216]
        if float(np.mean(np.min(arrow, axis=2) > 210)) < .20:
            return "unknown", {**details, "error": "back control obscured"}
        if (one("帮助", (370, 30, 450, 90))
                and one("行程", (580, 30, 660, 90))
                and (one("活动服装", (1680, 30, 1770, 90))
                     # Observed when the last-day countdown expands to hours/minutes.
                     or one("活动服装", (1540, 30, 1690, 90))
                     or one("活动服装", (1910, 30, 2020, 90)))
                and all(one(text, (x - 65, 1120, x + 65, 1180)) for text, x in (
                    ("剧情", 588), ("普通战斗", 813), ("挑战战斗", 1039),
                    ("魔兽追踪者", 1265), ("任务", 1490), ("活动商店", 1716)))
                and _difference(image, "season-blood-royale-logo", (185, 395, 800, 705)) < 8):
            return "main", details
        ap = one(r"([0-5])/5", (2080, 40, 2160, 90))
        if (one("普通战斗", (340, 30, 490, 90))
                and one("普通战斗1", (1070, 135, 1260, 200))
                and one("活动服装", (1540, 30, 1690, 90))
                and one(r"活动还剩[1-9]\d?天", (1740, 30, 1940, 90))
                and one("反复奖励", (1950, 135, 2090, 200))
                and one("通关奖励", (910, 280, 1040, 340))
                and one("在战斗中获胜", (990, 360, 1160, 420))
                and one("胜利且全员生存", (980, 480, 1190, 545))
                and one("在8回合内获胜", (980, 605, 1180, 670))
                and one("BLOOD", (90, 1140, 200, 1200))
                and one("ROYALE", (90, 1200, 200, 1240))
                and ap
                and all(_difference(image, name, box) < 6 for name, box in LOCKED_CROPS.items())):
            return "normal_locked", {**details, "reading": dict(
                season="Blood Royale", stage=1, ap=int(ap.group(1)), ap_limit=5,
                normal_cost=1, cleared=False, repeat_reward_locked=True, quick_battle=False)}
        return "unknown", {**details, "error": "unverified season or eligibility"}
    except Exception as exc:
        return "unknown", {"error": repr(exc)}


def run_season_battle_recheck(*, dry_run: bool, log_root: Path):
    """Home → season → two locked-1 reads → season → unchanged home.

    A successful audit is a skipped battle, never battle completion. There are
    no skill, battle, reward, AP toggle or purchase inputs and no state writes.
    """
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="season_battle_recheck", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"

    def wait(recognize, label):
        ok, image, details = wait_for_recognition(
            hwnd, logger=logger, label=label, recognize=recognize, timeout=45)
        if not ok:
            raise RuntimeError(label + " 未确认；不重试输入，不进入战斗")
        return image, details

    def page(expected):
        def recognize(image):
            state, details = recognize_season_page(image)
            return state == expected, details
        return recognize

    def click(image, point, key):
        click_ratio_logged(hwnd, image, (point[0] / 2304, point[1] / 1296), key=key, logger=logger)

    try:
        ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
        if not ok:
            raise RuntimeError(reason)
        found, before = recognize_season_home(image, entry=True)
        if not found:
            raise RuntimeError("赛季入口/主页余额未确认；未输入")
        if dry_run:
            return True, "dry-run planned season eligibility audit; no input or state write"
        click(image, (2010, 935), "season_entry")
        image, _ = wait(page("main"), "season-main")
        click(image, (813, 1040), "season_normal_page")
        image, first = wait(page("normal_locked"), "season-locked-1")
        time.sleep(2)
        image, second = wait(page("normal_locked"), "season-locked-2")
        if first["reading"] != second["reading"]:
            raise RuntimeError("赛季资格/AP两帧变化；停止，不进入战斗")
        logger.event(action="eligibility_readonly", reading=second["reading"], battle=False)
        click(image, (208, 60), "season_normal_back")
        image, _ = wait(page("main"), "season-main-return")
        click(image, (208, 60), "season_return_home")
        image, after = wait(recognize_season_home, "season-home")
        if before["balances"] != after["balances"]:
            raise RuntimeError("赛季只读返回后主页余额变化")
        reason = (f"skipped:Blood Royale普通1未通关，重复奖励锁定，AP {second['reading']['ap']}/5，"
                  "普通战斗费用1；快速战斗未解锁，已只读复核并返回主页，未战斗")
        logger.event(action="stop", result="success", reason=reason, battle=False)
        return True, reason
    except Exception as exc:
        logger.failure(str(exc))
        return False, str(exc)
