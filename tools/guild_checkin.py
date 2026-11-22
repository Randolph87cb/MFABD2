"""Verify the demonstrated non-member guild page; never join or claim check-in."""

from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np

from chapter_collection import _entries, _one
from free_gacha import RunLogger
from open_game import find_game_window
from reward_flow import _recognize_actionable_home, click_ratio_logged, prepare_actionable_home, wait_for_recognition


def recognize_guild_home(image, *, entry=False):
    found, details = _recognize_actionable_home(image)
    if not found or image.size != (2304, 1296):
        return False, details
    try:
        entries = _entries(image)
        balances = [_one(entries, r"0|[1-9]\d{0,2}(?:,\d{3})*", box) for box in (
            (1200, 40, 1350, 95), (1390, 40, 1570, 95), (1600, 40, 1770, 95))]
        if not all(balances) or (entry and not _one(entries, "公会", (400, 215, 470, 260))):
            return False, {**details, "error": "unverified guild entry or home balances"}
        return True, {**details, "balances": tuple(int(m.group().replace(",", "")) for m in balances)}
    except Exception as exc:
        return False, {"error": repr(exc)}


def recognize_guild_navigation(image):
    """Identify the non-member navigation shell, independently of list contents."""
    if image.size != (2304, 1296):
        return False, {"error": "unverified client size"}
    try:
        entries = _entries(image)
        details = {"texts": entries}
        if any(re.search(r"购买|充值|消耗|不足|确认|取消|是否|签到|已加入|公会管理", text)
               for _, _, text, score in entries if score >= .55):
            return False, {**details, "error": "unsafe, obstructing or unaudited member page"}
        anchors = (
            ("公会", (340, 30, 415, 90)),
            ("推荐公会", (180, 145, 320, 210)),
            ("申请列表", (180, 235, 320, 295)),
            ("创建公会", (180, 320, 320, 380)),
            ("用公会名称搜索", (900, 30, 1140, 85)),
            ("搜索", (1820, 30, 1920, 85)),
            ("刷新", (2000, 30, 2100, 85)),
        )
        arrow = np.asarray(image.convert("RGB"))[49:73, 203:216]
        bright = float(np.mean(np.min(arrow, axis=2) > 210))
        if bright < .20 or not all(_one(entries, ".*" + text + ".*", box) for text, box in anchors):
            return False, {**details, "back_bright": bright, "error": "unverified non-member recommendations"}
        return True, details
    except Exception as exc:
        return False, {"error": repr(exc)}


def recognize_nonmember_guild(image):
    """Require the navigation shell and two join controls, regardless of join policy.

    A recommendation may require approval or permit immediate entry. Neither
    authorizes clicking it. Names, counts and join policy are not page identity.
    """
    found, details = recognize_guild_navigation(image)
    if not found:
        return False, details
    if not all(_one(details["texts"], r".+加入", box) for box in (
            (1230, 185, 1350, 240), (1230, 350, 1350, 410))):
        return False, {**details, "error": "unverified recommendation join controls"}
    return True, {**details, "reading": {"member": False, "checkin": "unavailable"}}


def return_from_guild(hwnd, image, *, logger):
    """Return from a verified navigation shell once; unknown destinations stop."""
    found, _ = recognize_guild_navigation(image)
    if not found:
        return False, "公会返回控件未确认；未输入"
    click_ratio_logged(hwnd, image, (208 / 2304, 60 / 1296),
                       key="guild_return_home", logger=logger)
    ok, _, _ = wait_for_recognition(hwnd, logger=logger, label="guild-recovery-home",
                                    timeout=45, recognize=_recognize_actionable_home)
    return ok, "returned to real_home" if ok else "公会返回主页未确认；停止输入"


def run_guild_nonmember_check(*, dry_run: bool, log_root: Path):
    """Home → two non-member list frames → unchanged home; no check-in record.

    Only the entry and back controls receive input. Joined, empty-list and
    unknown layouts stop for new evidence; none are guessed as already signed.
    """
    logger = RunLogger(log_root, annotate_clicks=True)
    logger.event(action="start", flow="guild_nonmember_check", dry_run=dry_run)
    hwnd = find_game_window()
    if not hwnd:
        return False, "game window not found"

    def wait(recognize, label):
        ok, image, details = wait_for_recognition(
            hwnd, logger=logger, label=label, recognize=recognize, timeout=45)
        if not ok:
            raise RuntimeError(label + " 未确认；停止，不重试输入或签到")
        return image, details

    def click(image, point, key):
        click_ratio_logged(hwnd, image, (point[0] / 2304, point[1] / 1296), key=key, logger=logger)

    try:
        ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
        if not ok:
            raise RuntimeError(reason)
        found, before = recognize_guild_home(image, entry=True)
        if not found:
            raise RuntimeError("公会主页入口/余额未确认；未输入")
        if dry_run:
            return True, "dry-run planned guild non-member check; no input or check-in record"
        click(image, (434, 180), "guild_entry")
        image, first = wait(recognize_nonmember_guild, "guild-nonmember-1")
        time.sleep(2)
        image, second = wait(recognize_nonmember_guild, "guild-nonmember-2")
        if first["reading"] != second["reading"]:
            raise RuntimeError("公会前置状态两帧变化；不继续输入")
        logger.event(action="guild_prerequisite", reading=second["reading"], checkin=False)
        click(image, (208, 60), "guild_return_home")
        image, after = wait(recognize_guild_home, "guild-home")
        if before["balances"] != after["balances"]:
            raise RuntimeError("公会只读返回后主页余额变化；不记安全跳过")
        reason = "skipped:公会推荐列表两帧确认未加入，签到前置不满足；已返回主页，未入会或签到"
        logger.event(action="stop", result="success", reason=reason, checkin=False)
        return True, reason
    except Exception as exc:
        logger.failure(str(exc))
        return False, str(exc)
