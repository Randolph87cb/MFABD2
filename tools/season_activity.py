"""Audit the shared season normal-1 layout without starting a battle."""

from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np

from chapter_collection import _crop_text, _difference, _entries, _one
from free_gacha import RunLogger, classify_state
from open_game import find_game_window
from reward_flow import _recognize_actionable_home, click_ratio_logged, prepare_actionable_home, wait_for_recognition


LOCKED_CROPS = {
    "season-first-stars": (366, 156, 475, 200),
    "season-reward-lock": (2005, 197, 2038, 232),
    "season-manual-button": (1510, 1170, 1628, 1210),
}


def _label(entries, pattern, box, *, minimum_score=.95):
    """Match a single label inside its ROI even when a neighbouring label shifts."""
    left, top, right, bottom = box
    matches = [(re.fullmatch(pattern, text), score) for x, y, text, score in entries
               if left <= x <= right and top <= y <= bottom and re.fullmatch(pattern, text)]
    if len(matches) != 1 or not matches[0][1] >= minimum_score:
        return None
    return matches[0][0]


def _wait_for_season_recognition(hwnd, *, logger, label, recognize, timeout=45):
    def destination(frame):
        found, details = recognize(frame)
        if not found:
            state, _ = classify_state(frame)
            details = {**details, "network_loading": state in {"loading", "restaurant_loading"}}
        return found, details

    return wait_for_recognition(hwnd, logger=logger, label=label,
                                recognize=destination, timeout=timeout)


# Value centres exclude resource icons; crop padding below preserves glyph edges.
HOME_BALANCE_BOXES = ((1220, 45, 1350, 95), (1390, 45, 1570, 95), (1630, 45, 1770, 95))
HOME_BALANCE_PATTERN = r"0|[1-9]\d{0,2}(?:,\d{3})*"


def _home_balance(image, entries, box):
    """Separate value text from resource icons and re-read omitted/merged OCR."""
    left, top, right, bottom = box
    candidates = [e for e in entries if left <= e[0] <= right and top <= e[1] <= bottom]
    numbers = [e for e in candidates if re.fullmatch(HOME_BALANCE_PATTERN, e[2])]
    if len(numbers) > 1:
        return None  # Conflicting or duplicate values must not be selected.
    if len(candidates) == 1 and numbers:
        return _one(entries, HOME_BALANCE_PATTERN, box)  # Retain confidence gate.
    return _crop_text(image, (left - 10, top - 5, right + 10, bottom + 5), HOME_BALANCE_PATTERN)


def recognize_season_home(image, *, entry=False):
    found, details = _recognize_actionable_home(image)
    if not found or image.size != (2304, 1296):
        return False, details
    try:
        entries = _entries(image)
        details = {**details, "texts": entries}
        balances = [_home_balance(image, entries, box) for box in HOME_BALANCE_BOXES]
        if not all(balances):
            return False, {**details, "error": "unverified home balances",
                           "unverified_balance_boxes": [box for box, value in zip(HOME_BALANCE_BOXES, balances)
                                                        if value is None]}
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
    mamono_state, mamono_details = recognize_mamono_page(image)
    if mamono_state != "unknown":
        return "mamono_" + mamono_state, mamono_details
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
    if state in {"normal_selection", "normal_locked", "mamono_preparation", "mamono_active", "mamono_selection"}:
        click_ratio_logged(hwnd, image, (208 / 2304, 60 / 1296),
                           key="season_normal_back", logger=logger)
        ok, image, _ = _wait_for_season_recognition(
            hwnd, logger=logger, label="season-recovery-main", timeout=45,
            recognize=main_page)
        if not ok:
            return False, "赛季返回主菜单未确认；停止输入"
        state = "main"
    if state != "main":
        return False, "赛季返回控件未确认；未输入"
    click_ratio_logged(hwnd, image, (208 / 2304, 60 / 1296),
                       key="season_return_home", logger=logger)
    ok, _, _ = _wait_for_season_recognition(hwnd, logger=logger, label="season-recovery-home",
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
        ok, image, details = _wait_for_season_recognition(
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
        logger.event(action="season_home_check", found=found, details=before)
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


def recognize_mamono_page(image):
    """Separate common return navigation, period evidence and battle permission."""
    if image.size != (2304, 1296):
        return "unknown", {"error": "unverified client size"}
    try:
        entries = _entries(image)
        details = {"texts": entries, "battle": False}
        if any(re.search(r"购买|充值|补充|不足|确认|取消|是否", text)
               for _, _, text, score in entries if score >= .55):
            return "unknown", {**details, "error": "dialog or resource risk"}
        arrow = np.asarray(image.convert("RGB"))[49:73, 203:216]
        if float(np.mean(np.min(arrow, axis=2) > 210)) < .20:
            return "unknown", {**details, "error": "back control obscured"}
        # The period, boss artwork, rank value and team contents change. These
        # fixed controls identify the page for reading and returning only.
        anchors = (("魔兽", (330, 25, 420, 90)),
                   ("我的名次与猎人段位", (880, 10, 1220, 55)),
                   ("通关奖励", (1900, 150, 2110, 210)),
                   ("编辑队伍", (1130, 1150, 1350, 1230)),
                   ("去战斗", (1740, 1150, 1950, 1230)))
        def label(text, box):
            pattern = ".*" + text + ".*"
            left, top, right, bottom = box
            if any(left <= x <= right and top <= y <= bottom and re.fullmatch(pattern, word)
                   for x, y, word, _ in entries):
                # Do not replace conflicting or low-confidence readings.
                return _label(entries, pattern, box, minimum_score=.90 if text == "魔兽" else .95)
            return _crop_text(image, box, pattern)

        if (not all(label(text, box)
                    for text, box in anchors)
                or not all(_one(entries, f"队伍{i}", (930, y - 35, 1060, y + 35))
                           for i, y in ((1, 440), (2, 598), (3, 756)))):
            return "unknown", {**details, "error": "unverified mamono controls"}
        # Days become hours/minutes on the last preparation day. Keep the
        # stable prefix and complete duration, independently of season artwork.
        duration = r"(?:[1-9]\d*天|(?:[01]?\d|2[0-3])小时(?:[0-5]?\d分钟)?|[1-5]?\d分钟)"
        pattern = "距离(开始|结束)还剩(" + duration + ")"
        countdown = _one(entries, pattern, (1630, 30, 2040, 90))
        if countdown is None and (not any(1630 <= x <= 2040 and 30 <= y <= 90
                                          for x, y, _, _ in entries)
                                  or any(1630 <= x <= 2040 and 30 <= y <= 90 and score >= .95
                                         and re.match("距离(?:开始|结束)还剩", text)
                                         and not re.fullmatch(pattern, text)
                                         for x, y, text, score in entries)):
            countdown = _crop_text(image, (1630, 30, 2040, 90), pattern)
        if not countdown:
            return "selection", {**details, "error": "unverified mamono countdown"}
        period = "preparation" if countdown.group(1) == "开始" else "active"
        locks = [_one(entries, "从狩猎期间开始解除", (1390, y, 1660, y + 60))
                 for y in (440, 598, 756)]
        if ((period == "preparation" and not all(locks))
                or (period == "active" and any("狩猎期间开始解除" in text
                                               for _, _, text, score in entries if score >= .55))):
            return "selection", {**details, "error": "unverified or conflicting mamono locks/period"}
        text = countdown.group(2)
        parts = {unit: int(value) for value, unit in re.findall(r"(\d+)(天|小时|分钟)", text)}
        minutes = parts.get("天", 0) * 1440 + parts.get("小时", 0) * 60 + parts.get("分钟", 0)
        if minutes <= 0:
            return "selection", {**details, "error": "mamono countdown expired"}
        precision = 1 if "分钟" in parts else 60 if "小时" in parts else 1440
        return period, {**details, "period": period, "days": parts.get("天"), "countdown": text,
                        "minutes": minutes, "precision": precision}
    except Exception as exc:
        return "unknown", {"error": repr(exc)}


def recognize_mamono_preparation(image):
    state, details = recognize_mamono_page(image)
    return state == "preparation", details


def recognize_mamono_menu(image):
    """Separate menu identity from the optional preparation-period marker."""
    state, details = recognize_season_page(image)
    if state != "main":
        return False, details
    box = (1170, 890, 1360, 950)
    marker = _one(details["texts"], "可练习战斗", box)
    reread = marker is None
    if reread:
        marker = _crop_text(image, box, "可练习战斗")
    return True, {**details, "preparation_marker": marker is not None,
                  "marker_crop_reread": reread, "battle": False}


def run_mamono_preparation_recheck(*, dry_run: bool, log_root: Path):
    """Read the current period twice; never edit teams or start a battle."""
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="mamono_preparation_recheck", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"

    def wait(recognize, label):
        ok, image, details = _wait_for_season_recognition(
            hwnd, logger=logger, label=label, recognize=recognize, timeout=45)
        if not ok:
            raise RuntimeError(label + " 未确认；停止，不进入战斗")
        return image, details

    def click(image, x, y, key):
        click_ratio_logged(hwnd, image, (x / 2304, y / 1296), key=key, logger=logger)

    def mamono_page(image):
        state, details = recognize_mamono_page(image)
        return state != "unknown", {**details, "period_state": state}

    try:
        ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
        if not ok:
            raise RuntimeError(reason)
        found, before = recognize_season_home(image, entry=True)
        logger.event(action="mamono_home_check", found=found, details=before)
        if not found:
            raise RuntimeError("魔兽复核主页入口/余额未确认；未输入")
        if dry_run:
            return True, "dry-run planned mamono preparation audit; no input"
        click(image, 2010, 935, "mamono_season_entry")
        image, menu = wait(recognize_mamono_menu, "mamono-practice-menu")
        # The optional practice badge is a hint, not a navigation prerequisite
        # or proof of an active period. Click only the current menu text.
        targets = [(x, y) for x, y, text, score in menu["texts"]
                   if text == "魔兽追踪者" and score >= .95]
        if len(targets) != 1:
            raise RuntimeError("魔兽追踪者当前文字目标缺失或歧义；未进入")
        click(image, *targets[0], "mamono_entry")
        image, first = wait(mamono_page, "mamono-period-1")
        first_at = time.monotonic()
        time.sleep(2)
        image, second = wait(mamono_page, "mamono-period-2")
        elapsed = (time.monotonic() - first_at) / 60
        if (first["period_state"] not in {"preparation", "active"}
                or second["period_state"] != first["period_state"]):
            raise RuntimeError("魔兽页面已确认，但两帧时期/倒计时/锁定未验证或发生变化；"
                               "未编辑队伍、未战斗，保留失败并交由主页恢复")
        # Compare the represented time intervals, allowing natural minute and
        # unit rollovers, but rejecting increases or unrelated countdowns.
        if (second["minutes"] > first["minutes"] + first["precision"] - 1
                or second["minutes"] + second["precision"] <= first["minutes"] - elapsed):
            raise RuntimeError("魔兽时期倒计时两帧变化；停止")
        logger.event(action="mamono_period_readonly", period=second["period_state"],
                     countdown=second["countdown"], battle=False)
        click(image, 208, 60, "mamono_back")
        image, _ = wait(recognize_mamono_menu, "mamono-menu-return")
        click(image, 208, 60, "mamono_home")
        _, after = wait(recognize_season_home, "mamono-home-return")
        if before["balances"] != after["balances"]:
            raise RuntimeError("魔兽只读复核返回主页余额变化")
        if second["period_state"] == "preparation":
            reason = f"skipped:魔兽准备期，距离开始{second['countdown']}，三队锁定；已返回主页，未战斗"
        else:
            reason = (f"skipped:魔兽结束倒计时{second['countdown']}两帧已确认，准备期检查不适用；"
                      "正式队伍与免费费用尚未验证，正式战斗未实现；已返回主页，未战斗")
        logger.event(action="stop", result="success", reason=reason, battle=False)
        return True, reason
    except Exception as exc:
        logger.failure(str(exc))
        return False, str(exc)
