"""Audit the shared season normal-1 layout without starting a battle."""

from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np

from chapter_collection import _crop_text, _difference, _entries, _one
from free_gacha import RunLogger
from open_game import find_game_window
from reward_flow import _recognize_actionable_home, click_ratio_logged, prepare_actionable_home, wait_for_recognition


LOCKED_CROPS = {
    "season-first-stars": (366, 156, 475, 200),
    "season-reward-lock": (2005, 197, 2038, 232),
    "season-manual-button": (1510, 1170, 1628, 1210),
}


def _label(entries, pattern, box):
    """Match a single label inside its ROI even when a neighbouring label shifts."""
    left, top, right, bottom = box
    matches = [(re.fullmatch(pattern, text), score) for x, y, text, score in entries
               if left <= x <= right and top <= y <= bottom and re.fullmatch(pattern, text)]
    if len(matches) != 1 or not matches[0][1] >= .95:
        return None
    return matches[0][0]


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
            # The entry artwork changes every season. A countdown and unlock
            # subtitle in the season panel identify navigation, not permission
            # to fight. The destination must still pass its own layout checks.
            countdown = _label(entries, r"D-(?:\d{1,2}|DAY)", (1900, 810, 2120, 895))
            if countdown is None and not any(1900 <= x <= 2120 and 810 <= y <= 895
                                            for x, y, _, _ in entries):
                # The moving artwork can make full-frame OCR omit the badge.
                countdown = _crop_text(image, (1900, 810, 2120, 895), r"D-(?:\d{1,2}|DAY)")
            subtitle = _one(entries, r".+解锁", (1880, 975, 2150, 1035))
            if not countdown or not subtitle:
                return False, {**details, "error": "unverified season entry controls"}
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
                and _label(entries, "活动服装", (1450, 30, 2050, 90))
                and all(one(text, (x - 65, 1120, x + 65, 1180)) for text, x in (
                    ("剧情", 588), ("普通战斗", 813), ("挑战战斗", 1039),
                    ("魔兽追踪者", 1265), ("任务", 1490), ("活动商店", 1716)))):
            return "main", details
        ap = one(r"([0-5])/5", (2080, 40, 2160, 90))
        if (one("普通战斗", (340, 30, 490, 90))
                and one("普通战斗1", (1070, 135, 1260, 200))
                and _label(entries, "活动服装", (1450, 30, 2050, 90))
                and one(r"活动还剩(?:\d+天|\d+小时(?:\d+分钟)?|\d+分钟)", (1650, 30, 2000, 90))
                and one("反复奖励", (1950, 135, 2090, 200))
                and one("通关奖励", (910, 280, 1040, 340))
                and one("在战斗中获胜", (990, 360, 1160, 420))
                and one("胜利且全员生存", (980, 480, 1190, 545))
                and one("在8回合内获胜", (980, 605, 1180, 670))
                and ap
                and all(_difference(image, name, box) < 6 for name, box in LOCKED_CROPS.items())):
            return "normal_locked", {**details, "reading": dict(
                stage=1, ap=int(ap.group(1)), ap_limit=5,
                normal_cost=1, cleared=False, repeat_reward_locked=True, quick_battle=False)}
        return "unknown", {**details, "error": "unverified season or eligibility"}
    except Exception as exc:
        return "unknown", {"error": repr(exc)}


def recognize_season_navigation(image):
    """Identify return navigation only; never infer battle eligibility or cost."""
    prepared, preparation = recognize_mamono_preparation(image)
    if prepared:
        return "mamono_preparation", preparation
    state, details = recognize_season_page(image)
    if state != "unknown":
        return state, details
    if image.size != (2304, 1296) or "texts" not in details:
        return "unknown", details
    # An unsafe overlay or hidden back arrow must not be bypassed by this fallback.
    if details.get("error") != "unverified season or eligibility":
        return "unknown", details
    entries = details["texts"]
    if (_label(entries, r".*帮助.*", (370, 30, 450, 90))
            and _label(entries, r".*行程.*", (580, 30, 660, 90))
            and _label(entries, r".*活动服装.*", (1450, 30, 2050, 90))
            and all(_label(entries, ".*" + text + ".*", (x - 65, 1120, x + 65, 1180))
                    for text, x in (("剧情", 588), ("挑战战斗", 1039),
                                    ("魔兽追踪者", 1265), ("任务", 1490), ("活动商店", 1716)))):
        # The selected menu can glow during transitions. The header and five
        # neighbouring controls identify the back destination independently;
        # this grants only navigation, never a click on the unreadable menu.
        return "main", {**details, "navigation_only": True}
    stage = _one(entries, r"普通战斗([1-9]\d*)", (1070, 135, 1260, 200))
    if (stage and _label(entries, r".*普通战斗.*", (340, 30, 540, 90))
            and _label(entries, r".*活动服装.*", (1450, 30, 2050, 90))
            and all(_label(entries, ".*" + text + ".*", box) for text, box in (
                ("反复奖励", (1950, 135, 2120, 200)),
                ("通关奖励", (910, 280, 1080, 340)),
                ("在战斗中获胜", (990, 360, 1190, 420)),
                ("胜利且全员生存", (980, 480, 1220, 545)),
                ("在8回合内获胜", (980, 605, 1210, 670))))):
        return "normal_selection", {"texts": entries, "navigation_stage": int(stage.group(1))}
    return "unknown", details


def return_from_season(hwnd, image, *, logger):
    """Use each observed back control once, verifying both destinations."""
    def main_page(frame):
        state, details = recognize_season_navigation(frame)
        return state == "main", details

    state, _ = recognize_season_navigation(image)
    if state in {"normal_selection", "normal_locked", "mamono_preparation"}:
        click_ratio_logged(hwnd, image, (208 / 2304, 60 / 1296),
                           key="season_normal_back", logger=logger)
        ok, image, _ = wait_for_recognition(
            hwnd, logger=logger, label="season-recovery-main", timeout=45,
            recognize=main_page)
        if not ok:
            return False, "赛季返回主菜单未确认；停止输入"
        state = "main"
    if state != "main":
        return False, "赛季返回控件未确认；未输入"
    click_ratio_logged(hwnd, image, (208 / 2304, 60 / 1296),
                       key="season_return_home", logger=logger)
    ok, _, _ = wait_for_recognition(hwnd, logger=logger, label="season-recovery-home",
                                    timeout=45, recognize=_recognize_actionable_home)
    return ok, "returned to real_home" if ok else "赛季返回主页未确认；停止输入"


def run_season_battle_recheck(*, dry_run: bool, log_root: Path):
    """Home → season → two selection reads → season → unchanged home.

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

    def page(expected, *, entry=False):
        def recognize(image):
            # Entering normal battle needs the readable menu itself. The more
            # tolerant identity is only for auditing selections and going back.
            state, details = recognize_season_page(image) if entry else recognize_season_navigation(image)
            found = state == expected or (expected == "normal_locked" and state == "normal_selection")
            return found, details
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
        image, _ = wait(page("main", entry=True), "season-main")
        click(image, (813, 1040), "season_normal_page")
        image, first = wait(page("normal_locked"), "season-locked-1")
        time.sleep(2)
        image, second = wait(page("normal_locked"), "season-locked-2")
        if ((first.get("reading"), first.get("navigation_stage"))
                != (second.get("reading"), second.get("navigation_stage"))):
            raise RuntimeError("赛季关卡/资格/AP两帧变化；停止，不进入战斗")
        logger.event(action="eligibility_readonly", reading=second.get("reading"),
                     navigation_stage=second.get("navigation_stage"), battle=False)
        click(image, (208, 60), "season_normal_back")
        image, _ = wait(page("main"), "season-main-return")
        click(image, (208, 60), "season_return_home")
        image, after = wait(recognize_season_home, "season-home")
        if before["balances"] != after["balances"]:
            raise RuntimeError("赛季只读返回后主页余额变化")
        if "reading" not in second:
            reason = (f"skipped:赛季普通{second['navigation_stage']}选择页已确认，战斗资格未经验证；"
                      "已返回主页，未战斗")
            logger.event(action="stop", result="success", reason=reason, battle=False)
            return True, reason
        reason = (f"skipped:赛季普通1未通关，重复奖励锁定，AP {second['reading']['ap']}/5，"
                  "普通战斗费用1；快速战斗未解锁，已只读复核并返回主页，未战斗")
        logger.event(action="stop", result="success", reason=reason, battle=False)
        return True, reason
    except Exception as exc:
        logger.failure(str(exc))
        return False, str(exc)


def recognize_mamono_preparation(image):
    """Recognize the demonstrated locked preparation page, never a battle permit."""
    if image.size != (2304, 1296):
        return False, {"error": "unverified client size"}
    try:
        entries = _entries(image)
        details = {"texts": entries}
        if any(re.search(r"购买|充值|补充|不足|确认|取消|是否", text)
               for _, _, text, score in entries if score >= .55):
            return False, {**details, "error": "dialog or resource risk"}
        arrow = np.asarray(image.convert("RGB"))[49:73, 203:216]
        if float(np.mean(np.min(arrow, axis=2) > 210)) < .20:
            return False, {**details, "error": "back control obscured"}
        # Days become hours/minutes on the last preparation day. Keep the
        # stable prefix and complete duration, independently of season artwork.
        duration = r"(?:[1-9]\d*天|(?:[01]?\d|2[0-3])小时(?:[0-5]?\d分钟)?|[1-5]?\d分钟)"
        countdown = _one(entries, "距离开始还剩(" + duration + ")", (1630, 30, 2040, 90))
        if countdown is None and (not any(1630 <= x <= 2040 and 30 <= y <= 90
                                          for x, y, _, _ in entries)
                                  or any(1630 <= x <= 2040 and 30 <= y <= 90 and score >= .95
                                         and text.startswith("距离开始还剩")
                                         and not re.fullmatch("距离开始还剩(" + duration + ")", text)
                                         for x, y, text, score in entries)):
            countdown = _crop_text(image, (1630, 30, 2040, 90), "距离开始还剩(" + duration + ")")
        anchors = (("魔兽", (330, 25, 420, 90)),
                   ("我的名次与猎人段位", (880, 10, 1220, 55)),
                   ("暂无排名信息", (880, 55, 1100, 100)),
                   ("通关奖励", (1900, 150, 2110, 210)),
                   ("编辑队伍", (1130, 1150, 1350, 1230)),
                   ("去战斗", (1740, 1150, 1950, 1230)))
        if (not countdown or not all(_one(entries, text, box) for text, box in anchors)
                or not all(_one(entries, f"队伍{i}", (930, y - 35, 1060, y + 35))
                           and _one(entries, "从狩猎期间开始解除", (1390, y, 1660, y + 60))
                           for i, y in ((1, 440), (2, 598), (3, 756)))):
            return False, {**details, "error": "unverified preparation locks/countdown"}
        text = countdown.group(1)
        parts = {unit: int(value) for value, unit in re.findall(r"(\d+)(天|小时|分钟)", text)}
        minutes = parts.get("天", 0) * 1440 + parts.get("小时", 0) * 60 + parts.get("分钟", 0)
        if minutes <= 0:
            return False, {**details, "error": "preparation countdown expired"}
        precision = 1 if "分钟" in parts else 60 if "小时" in parts else 1440
        return True, {**details, "days": parts.get("天"), "countdown": text,
                      "minutes": minutes, "precision": precision, "battle": False}
    except Exception as exc:
        return False, {"error": repr(exc)}


def run_mamono_preparation_recheck(*, dry_run: bool, log_root: Path):
    """Home → practice-only season → locked teams twice → unchanged home."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="mamono_preparation_recheck", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"

    def wait(recognize, label):
        ok, image, details = wait_for_recognition(
            hwnd, logger=logger, label=label, recognize=recognize, timeout=45)
        if not ok:
            raise RuntimeError(label + " 未确认；停止，不进入战斗")
        return image, details

    def main(image):
        state, details = recognize_season_page(image)
        return (state == "main" and bool(_one(details.get("texts", []), "可练习战斗",
                                              (1170, 890, 1360, 950)))), details

    def click(image, x, y, key):
        click_ratio_logged(hwnd, image, (x / 2304, y / 1296), key=key, logger=logger)

    try:
        ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
        if not ok:
            raise RuntimeError(reason)
        found, before = recognize_season_home(image, entry=True)
        if not found:
            raise RuntimeError("魔兽复核主页入口/余额未确认；未输入")
        if dry_run:
            return True, "dry-run planned mamono preparation audit; no input"
        click(image, 2010, 935, "mamono_season_entry")
        image, _ = wait(main, "mamono-practice-menu")
        click(image, 1265, 1038, "mamono_entry")
        image, first = wait(recognize_mamono_preparation, "mamono-preparation-1")
        first_at = time.monotonic()
        time.sleep(2)
        image, second = wait(recognize_mamono_preparation, "mamono-preparation-2")
        elapsed = (time.monotonic() - first_at) / 60
        # Compare the represented time intervals, allowing natural minute and
        # unit rollovers, but rejecting increases or unrelated countdowns.
        if (second["minutes"] > first["minutes"] + first["precision"] - 1
                or second["minutes"] + second["precision"] <= first["minutes"] - elapsed):
            raise RuntimeError("魔兽准备期两帧变化；停止")
        logger.event(action="preparation_readonly", countdown=second["countdown"], battle=False)
        click(image, 208, 60, "mamono_back")
        image, _ = wait(main, "mamono-menu-return")
        click(image, 208, 60, "mamono_home")
        _, after = wait(recognize_season_home, "mamono-home-return")
        if before["balances"] != after["balances"]:
            raise RuntimeError("魔兽只读复核返回主页余额变化")
        reason = f"skipped:魔兽准备期，距离开始{second['countdown']}，三队锁定；已返回主页，未战斗"
        logger.event(action="stop", result="success", reason=reason, battle=False)
        return True, reason
    except Exception as exc:
        logger.failure(str(exc))
        return False, str(exc)
