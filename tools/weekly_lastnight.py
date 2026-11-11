"""One existing-team Last Night battle per game week on the verified PC layout."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from free_gacha import RunLogger
from game_text_recognition import _ocr_engine
from open_game import find_game_window
from reward_flow import click_ratio_logged, prepare_actionable_home, swipe_ratio_logged, wait_for_recognition
from weekly_arcade import _home
from weekly_cabin import GAME_TIMEZONE, _write_record, game_week_key


STATE_ROOT = Path(__file__).resolve().parents[1] / "state" / "weekly_lastnight"
ASSETS = Path(__file__).parent / "assets"


def _texts(image: Image.Image) -> list[tuple[float, float, str, float]]:
    result = _ocr_engine()(np.asarray(image.convert("RGB")))
    return [(float(np.mean(box[:, 0])), float(np.mean(box[:, 1])),
             "".join(str(text).split()), float(score))
            for box, text, score in zip(result.boxes if result.boxes is not None else (),
                                        result.txts if result.txts is not None else (),
                                        result.scores if result.scores is not None else ())
            if float(score) >= 0.80]


def _crop_matches(image: Image.Image, box: tuple[int, int, int, int], name: str,
                  limit: float = 12.0) -> bool:
    with Image.open(ASSETS / name) as reference:
        actual = np.asarray(image.convert("RGB").crop(box)).astype(float)
        expected = np.asarray(reference.convert("RGB")).astype(float)
    return actual.shape == expected.shape and float(np.mean(np.abs(actual - expected))) <= limit


def recognize_lastnight_page(image: Image.Image) -> tuple[str, dict[str, Any]]:
    """Only observed pages authorize input; a dimmed control or dialog never does."""
    if image.size != (2304, 1296):
        return "unknown", {"error": "unverified client size"}
    try:
        entries = _texts(image)
        details: dict[str, Any] = {"texts": entries}
        if any(re.search(r"购买|充值|消耗|不足|确认|取消|指南", text) for _, _, text, _ in entries):
            return "unknown", {**details, "error": "dialog or resource risk"}

        def one(pattern, region):
            left, top, right, bottom = region
            points = [(x, y, text) for x, y, text, score in entries if score >= 0.95
                      and left <= x <= right and top <= y <= bottom and re.fullmatch(pattern, text)]
            return points[0] if len(points) == 1 else None

        bright = lambda box: float(np.mean(np.min(np.asarray(image.convert("RGB").crop(box)), axis=2) > 210)) > 0.20
        new_damage = one(r"[1-9]\d{0,2}(?:,\d{3})*", (1340, 685, 1740, 745))
        if (one("创造新纪录", (1330, 450, 1740, 530))
                and one("最高伤害", (1370, 610, 1690, 685)) and new_damage
                and one(r"\d{1,2}(?:\.\d+)?%", (1400, 755, 1650, 815))
                and _crop_matches(image, (1495, 550, 1575, 610), "lastnight-new-record-badge.png")):
            return "new_record", {**details, "damage": int(new_damage[2].replace(",", ""))}
        title = one("末日之书", (330, 25, 510, 90))
        back = bright((203, 49, 216, 73))
        if one("游戏卡珍藏集", (280, 20, 550, 95)) and one("查看战场奖励", (1770, 25, 2100, 95)):
            target = one("末日之书", (1220, 350, 1430, 800))
            if target:
                x, y, _ = target
                neighbors = [one(text, (left, y - 12, right, y + 12)) for text, left, right in (
                    ("冒险航线", 900, 1100), ("黄金竞技场", 1520, 1780))]
                if all(neighbors) and one("火力战", (1220, y - 45, 1430, y - 12)):
                    # Click the centre of the observed cartridge, away from its + button.
                    return "collection", {**details, "point": (x, y - 140)}
        if (title and back and one("最高伤害", (1120, 130, 1300, 200))
                and one("每日可获得奖励", (1830, 130, 2140, 200))
                and one("更换出战", (1480, 1150, 1720, 1230))
                and one("去战斗", (1850, 1150, 2100, 1230))
                and _crop_matches(image, (1765, 1160, 2105, 1225), "lastnight-free-battle.png")):
            return "ready", details
        if (one(r"战斗分布\|?\d+(?:\.\d+)?%", (350, 90, 650, 140))
                and one(r"最高伤害\|?[\d,]+", (350, 140, 750, 190))
                and one("SPACE", (1950, 1120, 2110, 1190))
                and bright((2135, 56, 2178, 89))):
            spawn = _crop_matches(image, (170, 125, 325, 275), "lastnight-spawn.png", 18.0)
            return ("field_spawn" if spawn else "field"), details
        if (one(r"20次中(?:[1-9]|1\d|20)次攻击中", (80, 1130, 430, 1210))
                and one("TOTALDAMAGE", (1980, 860, 2250, 960))
                and one("TOPRECORD", (1950, 1120, 2250, 1195))
                and _crop_matches(image, (1885, 42, 1935, 85), "lastnight-skip.png")):
            return "battle", details
        damage = one(r"[1-9]\d{0,2}(?:,\d{3})*", (1600, 205, 2020, 290))
        if (one("I?RESULT", (1600, 30, 1870, 135)) and damage
                and one("最终伤害", (1580, 140, 1820, 200))
                and one("全体最高伤害", (1580, 285, 1880, 370))
                and one("重新挑战", (1720, 1170, 1930, 1250))
                and one("离开", (1980, 1170, 2190, 1250))
                and _crop_matches(image, (1950, 1180, 2185, 1240), "lastnight-leave.png")):
            return "result", {**details, "damage": int(damage[2].replace(",", ""))}
        return "unknown", details
    except Exception as exc:
        return "unknown", {"error": repr(exc)}


def _pending_results(record, week):
    """Re-read both original result captures; an intent alone proves no battle."""
    if not isinstance(record.get("log_root"), str) or not record["log_root"]:
        raise ValueError("pending record lacks original log root")
    reserved = datetime.fromisoformat(record["reserved_at"])
    if reserved.tzinfo is None or game_week_key(reserved) != week:
        raise ValueError("pending evidence belongs to another game week")
    root = Path(record["log_root"])
    samples = [str(root / f"lastnight-result-verified-{index}.png") for index in (1, 2)]
    damages = []
    for sample in samples:
        with Image.open(sample) as image:
            state, details = recognize_lastnight_page(image)
        if state != "result":
            raise ValueError("pending record lacks verified result images")
        damages.append(details["damage"])
    if damages[0] != damages[1]:
        raise ValueError("pending result damage conflict")
    return damages[0], samples


def run_weekly_lastnight(*, dry_run: bool, log_root: Path,
                         state_root: Path | None = None) -> tuple[bool, str]:
    logger = RunLogger(log_root, annotate_clicks=True)
    week = game_week_key()
    path = (STATE_ROOT if state_root is None else state_root) / f"{week}.json"
    logger.event(action="start", flow="weekly_lastnight", week=week, dry_run=dry_run)
    pending_results = None

    def stop(reason):
        logger.failure(reason)
        return False, reason

    if path.exists():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(record, dict) or type(record.get("version")) is not int
                    or record["version"] != 1 or record.get("week") != week
                    or record.get("status") not in ("action_pending", "completed")):
                raise ValueError("invalid weekly Last Night record")
            if record["status"] == "action_pending":
                pending_results = _pending_results(record, week)
            else:
                samples = record.get("result_screenshots")
                if (not isinstance(samples, list) or len(samples) != 2 or samples[0] == samples[1]
                    or not all(isinstance(p, str) and p for p in samples)
                    or type(record.get("damage")) is not int or record["damage"] <= 0
                    or record.get("returned_home") is not True
                    or not isinstance(record.get("home_screenshot"), str) or not record["home_screenshot"]):
                    raise ValueError("completed record lacks result and home evidence")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return stop(f"末日之书周记录异常，禁止输入：{exc}")
        if game_week_key() != week:
            return stop("读取末日之书记录时跨游戏周，停止")
        if pending_results is None:
            return True, f"skipped:末日之书在游戏周 {week} 已验证完成"

    hwnd = find_game_window()
    if not hwnd:
        return stop("game window not found")
    ok, image, reason = prepare_actionable_home(hwnd, logger=logger, dry_run=dry_run)
    if not ok or image.size != (2304, 1296):
        return stop(f"未确认末日之书起点：{reason}")
    if dry_run:
        return True, "dry-run planned Last Night navigation; no input or weekly record"
    if pending_results is not None:
        # The supervisor returns home before retrying weekly. Only finish the
        # already captured battle; never navigate to or start another battle.
        if game_week_key() != week:
            return stop("核对末日之书未决结果后跨游戏周，保留未决")
        damage, samples = pending_results
        try:
            record.update(status="completed", damage=damage, result_screenshots=samples, returned_home=True,
                          home_screenshot=str(logger.save_image(image, "lastnight-recovered-home.png")),
                          completed_at=datetime.now(GAME_TIMEZONE).isoformat())
            _write_record(path, record, reserve=False)
        except OSError as exc:
            return stop(f"末日之书未决结果收尾写盘失败：{exc}")
        reason = "末日之书原始两帧结算已复核并确认主页；未再次战斗"
        logger.event(action="stop", result="success", week=week, reason=reason)
        return True, reason
    record = dict(version=1, week=week, status="action_pending", log_root=str(log_root),
                  reserved_at=datetime.now(GAME_TIMEZONE).isoformat())
    if game_week_key() != week:
        return stop("进入末日之书前跨游戏周，未输入")
    try:
        _write_record(path, record, reserve=True)

        def current_week():
            if game_week_key() != week:
                raise RuntimeError("末日之书执行中跨游戏周，保留未决")

        def click(x, y, key):
            current_week()
            click_ratio_logged(hwnd, image, (x / 2304, y / 1296), key=key, logger=logger)

        def wait(states, label, timeout=45):
            def recognize(frame):
                state, details = recognize_lastnight_page(frame)
                return state in states, {**details, "state": state}
            ok, frame, details = wait_for_recognition(hwnd, logger=logger, label=label,
                                                       recognize=recognize, timeout=timeout)
            if not ok:
                raise RuntimeError(f"末日之书 {label} 未通过验证，不重试输入")
            current_week()
            return frame, details

        click(2030, 1125, "lastnight_open_collection")
        image, details = wait({"collection"}, "lastnight-collection")
        click(*details["point"], "lastnight_select_cartridge")
        image, _ = wait({"field_spawn"}, "lastnight-spawn")
        current_week()
        swipe_ratio_logged(hwnd, image, (1800 / 2304, 350 / 1296), (1800 / 2304, 350 / 1296),
                           key="lastnight_walk_to_battle", duration=2.0, logger=logger)
        image, _ = wait({"ready"}, "lastnight-ready")
        click(1940, 1190, "lastnight_start_once")
        image, details = wait({"battle", "result"}, "lastnight-battle", 90)
        if details["state"] == "battle":
            click(1910, 62, "lastnight_skip_animation")
            image, details = wait({"result"}, "lastnight-result", 120)
        damage = details["damage"]
        samples = [str(logger.save_image(image, "lastnight-result-verified-1.png"))]
        time.sleep(2.0)
        image, details = wait({"result"}, "lastnight-result-stable")
        if details["damage"] != damage:
            raise RuntimeError("末日之书两帧伤害冲突，禁止离开或重放")
        samples.append(str(logger.save_image(image, "lastnight-result-verified-2.png")))
        click(2060, 1210, "lastnight_leave_result")
        image, details = wait({"field_spawn", "field", "new_record"}, "lastnight-return-field")
        if details["state"] == "new_record":
            if details["damage"] != damage:
                raise RuntimeError("末日之书新纪录与结算伤害冲突，保留未决")
            # Dismiss the observed PC presentation by its title, once only.
            click(1535, 490, "lastnight_dismiss_new_record")
            image, _ = wait({"field_spawn", "field"}, "lastnight-after-new-record")
        click(2160, 68, "lastnight_return_home")
        ok, image, _ = wait_for_recognition(hwnd, logger=logger, recognize=_home,
                                             label="lastnight-final-home", timeout=45)
        if not ok:
            raise RuntimeError("末日之书结束后未确认主页，保留未决")
        current_week()
        record.update(status="completed", damage=damage, result_screenshots=samples, returned_home=True,
                      home_screenshot=str(logger.save_image(image, "lastnight-final-home.png")),
                      completed_at=datetime.now(GAME_TIMEZONE).isoformat())
        _write_record(path, record, reserve=False)
    except Exception as exc:
        return stop(f"末日之书停止，禁止自动重放：{exc}")
    reason = "末日之书一次战斗、两帧结算及返回主页已确认；未更换出战或领取奖励"
    logger.event(action="stop", result="success", week=week, reason=reason)
    return True, reason
