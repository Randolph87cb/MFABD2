"""Run the BrownDust II free gacha flow.

The script is intentionally conservative: it captures the current client,
classifies the visible state, clicks only the expected safe control for that
state, and stops with a saved screenshot when the state is unknown.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from adaptive_wait import AdaptivePoll
from enter_game import capture_client, recognize_home_screen
from game_text_recognition import (
    recognize_arena_auto_battle_labels,
    recognize_arena_battle_prep_labels,
    recognize_arena_cartridge_bar_labels,
    recognize_arena_cartridge_labels,
    recognize_arena_lobby_labels,
    recognize_arena_repeat_result_labels,
    recognize_arena_rank_change_labels,
    recognize_arena_victory_result_labels,
    recognize_all_free_gacha_button,
    recognize_business_management_state,
    recognize_free_gacha_confirmation_labels,
    recognize_gacha_item_detail_labels,
    recognize_gacha_page_labels,
    recognize_home_labels,
    recognize_quick_hunt_map_labels,
    recognize_reward_overlay_labels,
    recognize_quick_hunt_setup_labels,
    recognize_regular_customer_notes_labels,
    recognize_restaurant_state,
)
from open_game import find_game_window
from win32_windowpos_click import click_client


user32 = ctypes.windll.user32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_H = 0x48
SW_SHOWNOACTIVATE = 4
UNBOUNDED_LOADING_STATES = {"loading", "restaurant_loading"}

CLICK_POINTS = {
    "home_business_management": (0.085, 0.235),
    "business_management_claim_all": (0.567, 0.752),
    "reward_overlay_dismiss": (0.500, 0.800),
    "business_management_cancel": (0.432, 0.752),
    "business_management_restaurant": (0.603, 0.278),
    "restaurant_home": (0.935, 0.055),
    "restaurant_regular_customer": (0.145, 0.262),
    "restaurant_regular_customer_notes": (0.088, 0.141),
    "restaurant_notes_back": (0.090, 0.045),
    "restaurant_regular_customer_claim_all": (0.877, 0.930),
    "home_gacha": (0.086, 0.925),
    "home_return_battlefield": (0.787, 0.913),
    "plaza_cartridge": (0.413, 0.933),
    "cartridge_gameplay_tab": (0.521, 0.814),
    "cartridge_first_gameplay": (0.078, 0.901),
    "arena_pool": (0.414, 0.592),
    "arena_auto_battle": (0.791, 0.910),
    "arena_auto_max": (0.642, 0.588),
    "arena_auto_start": (0.547, 0.749),
    "arena_repeat_result_close": (0.617, 0.284),
    "arena_victory_leave": (0.895, 0.937),
    "arena_rank_confirm": (0.500, 0.933),
    "arena_dialogue_advance": (0.138, 0.565),
    "quick_hunt": (0.918, 0.255),
    "quick_hunt_start": (0.855, 0.918),
    "quick_hunt_max": (0.609, 0.471),
    "quick_hunt_confirm": (0.540, 0.725),
    "quick_hunt_crystal_cave": (0.091, 0.440),
    "quick_hunt_back": (0.090, 0.045),
    "plaza_home": (0.935, 0.055),
    "arena_home": (0.935, 0.055),
    # Home promotions use a bright center panel and a dimmed, non-interactive margin.
    "dismiss_overlay": (0.138, 0.565),
    # Category labels are not a reliable hit target. Click the icon above each label.
    "costume_tab": (0.086, 0.285),
    "gear_tab": (0.086, 0.385),
    "all_free": (0.178, 0.895),
    "confirm": (0.548, 0.598),
    "startup_promotion": (0.74, 0.70),
    "skip_animation": (0.930, 0.055),
    "result_back": (0.090, 0.045),
}

RETRY_CLICK_POINTS = {
    # Keep retries inside the icon while avoiding an identical second click.
    "costume_tab": (0.086, 0.295),
    "gear_tab": (0.086, 0.395),
}


TARGET_LABELS = {
    "costume": "costume_tab",
    "gear": "gear_tab",
}

FLOW_NAMES = {
    "enter_game": "进入游戏",
    "ensure_home": "返回主页",
    "free_gacha": "免费抽卡",
    "quick_hunt_entry": "进入快速狩猎",
    "quick_hunt_start": "打开狩猎设置",
    "quick_hunt_max_and_confirm": "执行普通狩猎场",
    "quick_hunt_crystal_cave_cycle": "执行圣石洞穴",
    "finish_crystal_cave_cycle": "结束圣石洞穴",
    "business_management_entry": "进入经营管理",
    "business_management_claim": "领取经营管理收益",
    "reward_overlay_dismiss": "关闭奖励结算",
    "business_management_cancel": "关闭经营管理",
    "business_management_restaurant": "前往餐厅",
    "business_management": "经营管理收益",
    "restaurant_entry": "进入餐厅",
    "restaurant_regular_customer": "领取餐厅常客奖励",
    "restaurant_regular_customer_notes": "打开常客笔记奖励",
    "restaurant_regular_customer_claim_all": "领取全部常客奖励",
    "restaurant_regular_customer_notes_back": "离开常客笔记",
    "restaurant_return_home": "从餐厅返回主页",
    "daily_arena_enter_battlefield": "进入竞技场",
    "daily_arena_cartridge_route": "选择竞技场卡带",
    "daily_arena_enter_battle_prep": "进入竞技场战斗准备",
    "daily_arena_open_auto_battle": "打开竞技场自动战斗",
    "daily_arena_maximize_and_start": "启动竞技场自动战斗",
    "daily_arena_wait_and_close_result": "等待竞技场战斗完成",
    "daily_arena_leave_victory": "离开竞技场结算",
    "daily_arena_confirm_rank_change": "确认竞技场段位变化",
}

STATE_NAMES = {
    "unknown": "无法识别",
    "loading": "加载中",
    "entry_screen": "游戏开始界面",
    "touch_ready": "点击开始界面",
    "download_waiting": "正在下载",
    "download_confirmation": "等待确认下载",
    "real_home": "主页",
    "home_overlay": "弹窗页面",
    "blocking_ad_overlay": "广告弹窗",
    "plaza": "广场",
    "gacha_page": "抽卡页面",
    "confirm_free_gacha": "免费抽卡确认",
    "gacha_animation": "抽卡动画",
    "gacha_result": "抽卡结果",
    "gacha_item_overlay": "抽卡物品详情",
    "arena_lobby": "竞技场大厅",
    "arena_battle_prep": "竞技场战斗准备",
    "arena_auto_battle_dialog": "竞技场自动战斗设置",
    "arena_repeat_result": "竞技场连续战斗结果",
    "arena_victory_result": "竞技场结算",
    "arena_rank_change": "竞技场段位变化",
    "arena_cartridge_bar": "卡带选择栏",
    "arena_cartridge_collection": "卡带收藏页面",
    "quick_hunt_map": "快速狩猎地图",
    "quick_hunt_setup": "快速狩猎设置",
    "reward_overlay": "奖励结算",
    "business_management_dialog": "经营管理",
    "restaurant_loading": "餐厅加载中",
    "restaurant_home": "餐厅",
    "restaurant_regular_customer_mode": "餐厅查看常客",
    "restaurant_regular_customer_notes": "餐厅常客笔记",
}

CLICK_NAMES = {
    "touch_to_start": "开始游戏",
    "confirm_download": "下载",
    "dismiss_overlay": "关闭弹窗",
    "reward_overlay_dismiss": "关闭奖励结算",
    "plaza_home": "主页",
    "arena_home": "竞技场右上角主页",
    "home_gacha": "抽抽乐",
    "costume_tab": "人物抽卡",
    "gear_tab": "装备抽卡",
    "all_free": "免费抽卡",
    "confirm": "确认抽卡",
    "startup_promotion": "继续新卡展示",
    "skip_animation": "跳过抽卡动画",
    "result_back": "返回",
    "quick_hunt": "快速狩猎",
    "quick_hunt_start": "狩猎",
    "quick_hunt_max": "最大次数",
    "quick_hunt_confirm": "确认狩猎",
    "quick_hunt_crystal_cave": "圣石洞穴",
    "quick_hunt_back": "返回主页",
    "home_return_battlefield": "返回战场",
    "plaza_cartridge": "卡带菜单",
    "cartridge_gameplay_tab": "玩法卡带",
    "cartridge_first_gameplay": "竞技场卡带",
    "arena_pool": "竞技场入口",
    "arena_dialogue_advance": "继续对话",
    "arena_auto_battle": "自动战斗",
    "arena_auto_max": "最大次数",
    "arena_auto_start": "开始十倍自动战斗",
    "arena_repeat_result_close": "关闭连续战斗结果",
    "arena_victory_leave": "离开竞技场",
    "arena_rank_confirm": "确认段位变化",
    "home_business_management": "经营管理",
    "business_management_claim_all": "一键获得",
    "business_management_restaurant": "餐馆立即前往",
    "restaurant_home": "餐厅右上角主页",
    "restaurant_regular_customer": "常客",
    "restaurant_regular_customer_notes": "常客笔记",
    "restaurant_notes_back": "返回餐厅",
    "restaurant_regular_customer_claim_all": "全部获得",
}


def _state_name(state: Any) -> str:
    value = str(state or "unknown")
    return STATE_NAMES.get(value, value)


def _console_event_message(payload: dict[str, Any]) -> str | None:
    action = str(payload.get("action", ""))
    if action == "start":
        return "开始执行"
    if action == "startup_gate":
        mode = "继续处理已打开的游戏" if payload.get("existing_window") else "启动游戏"
        return mode
    if action == "mute_game_audio":
        result = payload.get("result")
        if result == "success":
            return "游戏已静音"
        if result == "waiting":
            return "等待游戏声音加载后静音"
        return "游戏静音失败，稍后重试"
    if action == "classify":
        state = _state_name(payload.get("state"))
        entry_state = payload.get("entry_state")
        if entry_state and entry_state != "unknown":
            state = f"{state} / {_state_name(entry_state)}"
        return f"识别到：{state}"
    if action == "click":
        key = str(payload.get("key", ""))
        target = CLICK_NAMES.get(key, key)
        attempt = payload.get("attempt")
        suffix = f"（第 {attempt} 次）" if attempt else ""
        return f"点击：{target}{suffix}"
    if action == "verify_click":
        result = "已生效" if payload.get("succeeded") else "未生效，准备重试"
        return f"检查点击结果：{result}，当前为{_state_name(payload.get('state'))}"
    if action.startswith("wait_") or action in {"capture_retry", "restore_minimized_window"}:
        return "等待游戏响应"
    if action == "capture_recovered":
        return "游戏窗口已恢复，可以继续识别"
    if action == "failure_written":
        return f"失败详情已保存到：{payload.get('path')}"
    if action == "stop":
        result = payload.get("result")
        if result == "success":
            return "步骤完成"
        return f"步骤停止，当前为{_state_name(payload.get('state'))}"
    if action == "target_complete":
        target = str(payload.get("target", ""))
        return f"已完成：{'人物抽卡' if target == 'costume' else '装备抽卡'}"
    return None


@dataclass
class ActionResult:
    state: str
    action: str
    reason: str


class RunLogger:
    def __init__(self, root: Path, *, annotate_clicks: bool = False) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self._click_count = 0
        self.annotate_clicks = annotate_clicks
        self.console_label = self.root.name

    def event(self, **payload: Any) -> None:
        payload.setdefault("time", datetime.now().isoformat(timespec="seconds"))
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        if payload.get("action") == "start":
            flow = str(payload.get("flow", ""))
            self.console_label = FLOW_NAMES.get(flow, self.console_label)
        message = _console_event_message(payload)
        if message:
            clock = str(payload["time"])[11:19]
            print(f"[{clock}] [{self.console_label}] {message}", flush=True)

    def next_click_index(self) -> int:
        self._click_count += 1
        return self._click_count

    def save_image(self, image: Image.Image, name: str) -> Path:
        path = self.root / name
        image.copy().save(path)
        return path

    def save_click_image(
        self,
        image: Image.Image,
        name: str,
        *,
        x: int,
        y: int,
        key: str,
        dry_run: bool,
    ) -> Path:
        marked = image.convert("RGB").copy()
        draw = ImageDraw.Draw(marked)
        width, height = marked.size
        radius = max(18, min(width, height) // 45)
        line = max(4, min(width, height) // 260)
        color = (255, 0, 0)

        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=line)
        draw.line((x - radius * 2, y, x + radius * 2, y), fill=color, width=line)
        draw.line((x, y - radius * 2, x, y + radius * 2), fill=color, width=line)

        label = f"{key} ({x},{y}) dry_run={dry_run}"
        text_x = min(max(8, x + radius + 10), max(8, width - 520))
        text_y = min(max(8, y - radius - 36), max(8, height - 48))
        box = (text_x - 6, text_y - 6, min(width - 8, text_x + 500), min(height - 8, text_y + 34))
        draw.rectangle(box, fill=(0, 0, 0), outline=color, width=2)
        draw.text((text_x, text_y), label, fill=(255, 255, 255))

        path = self.root / name
        marked.save(path)
        return path

    def failure(self, reason: str) -> Path:
        path = self.root / "failure.txt"
        path.write_text(reason + "\n", encoding="utf-8")
        self.event(action="failure_written", path=str(path), reason=reason)
        return path


def safe_capture_client(
    hwnd: int,
    *,
    logger: RunLogger | None = None,
    attempts: int = 8,
    delay: float = 1.0,
    min_size: tuple[int, int] = (1000, 600),
) -> Image.Image:
    last_error: Exception | None = None
    min_width, min_height = min_size
    recovered = False
    for attempt in range(1, attempts + 1):
        try:
            image = capture_client(hwnd).convert("RGB").copy()
            image.load()
            if image.width < min_width or image.height < min_height:
                raise ValueError(f"invalid client capture size: {image.width}x{image.height}")
            if recovered and logger:
                logger.event(
                    action="capture_recovered",
                    attempt=attempt,
                    width=image.width,
                    height=image.height,
                )
            return image
        except Exception as exc:  # noqa: BLE001 - logged and retried by design.
            last_error = exc
            window_state = _capture_window_state(hwnd)
            if logger:
                logger.event(
                    action="capture_retry",
                    attempt=attempt,
                    error=repr(exc),
                    window=window_state,
                )
            if window_state["minimized"]:
                user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
                recovered = True
                if logger:
                    logger.event(
                        action="restore_minimized_window",
                        attempt=attempt,
                        command="SW_SHOWNOACTIVATE",
                    )
            time.sleep(delay)
    raise RuntimeError(f"failed to capture valid client image after {attempts} attempts: {last_error!r}")


def _capture_window_state(hwnd: int) -> dict[str, Any]:
    rect = wintypes.RECT()
    has_client_rect = bool(user32.GetClientRect(hwnd, ctypes.byref(rect)))
    return {
        "valid": bool(user32.IsWindow(hwnd)),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "minimized": bool(user32.IsIconic(hwnd)),
        "client_width": max(0, rect.right - rect.left) if has_client_rect else 0,
        "client_height": max(0, rect.bottom - rect.top) if has_client_rect else 0,
    }


def _gray(frame: np.ndarray) -> np.ndarray:
    return (
        frame[:, :, 0].astype(np.float32) * 0.299
        + frame[:, :, 1].astype(np.float32) * 0.587
        + frame[:, :, 2].astype(np.float32) * 0.114
    )


def _roi(frame: np.ndarray, spec: tuple[float, float, float, float]) -> np.ndarray:
    height, width = frame.shape[:2]
    x, y, w, h = spec
    x0 = max(0, min(width - 1, int(width * x)))
    y0 = max(0, min(height - 1, int(height * y)))
    x1 = max(x0 + 1, min(width, int(width * (x + w))))
    y1 = max(y0 + 1, min(height, int(height * (y + h))))
    return frame[y0:y1, x0:x1]


def _stats(region: np.ndarray) -> dict[str, float]:
    if region.size == 0 or region.shape[0] < 2 or region.shape[1] < 2:
        return {
            "mean": 0.0,
            "dark_ratio": 0.0,
            "mid_ratio": 0.0,
            "bright_ratio": 0.0,
            "edge_ratio": 0.0,
            "contrast": 0.0,
        }
    gray = _gray(region)
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    return {
        "mean": float(np.mean(gray)),
        "dark_ratio": float(np.mean(gray < 90)),
        "mid_ratio": float(np.mean((gray >= 90) & (gray <= 220))),
        "bright_ratio": float(np.mean(gray > 220)),
        "edge_ratio": (float(np.mean(dx > 35)) + float(np.mean(dy > 35))) / 2,
        "contrast": float(np.std(gray)),
    }


def _is_reveal_animation_like(
    animation_top_right: dict[str, float],
    animation_bottom_reveal: dict[str, float],
) -> bool:
    # Bright equipment-draw scenes can put a little more light behind the
    # top-right playback controls than costume-draw scenes do.
    return (
        animation_top_right["edge_ratio"] > 0.012
        and animation_top_right["bright_ratio"] < 0.11
        and animation_bottom_reveal["mid_ratio"] > 0.20
        and animation_bottom_reveal["edge_ratio"] < 0.020
    )


def _mean_region_difference(
    before: Image.Image,
    after: Image.Image,
    spec: tuple[float, float, float, float] = (0.20, 0.18, 0.60, 0.66),
) -> float:
    if before.size != after.size:
        return float("inf")
    before_frame = np.asarray(before.convert("RGB"), dtype=np.float32)
    after_frame = np.asarray(after.convert("RGB"), dtype=np.float32)
    before_region = _roi(before_frame, spec)
    after_region = _roi(after_frame, spec)
    return float(np.mean(np.abs(before_region - after_region)))


def detect_selected_gacha_target(image: Image.Image) -> str | None:
    frame = np.asarray(image.convert("RGB"))
    costume = _stats(_roi(frame, (0.055, 0.24, 0.075, 0.12)))
    gear = _stats(_roi(frame, (0.055, 0.35, 0.075, 0.12)))
    if costume["bright_ratio"] > gear["bright_ratio"] + 0.015:
        return "costume"
    if gear["bright_ratio"] > costume["bright_ratio"] + 0.015:
        return "gear"
    if costume["mean"] > gear["mean"] + 8:
        return "costume"
    if gear["mean"] > costume["mean"] + 8:
        return "gear"
    return None


def _resolve_all_free_gacha_availability(
    label_match: bool,
    recognition_details: dict[str, Any],
    button_stats: dict[str, float],
) -> str:
    if label_match:
        return "available"
    if not recognition_details.get("available"):
        return "unknown"
    if button_stats["edge_ratio"] < 0.012 and button_stats["bright_ratio"] < 0.015:
        return "used"
    return "unknown"


def detect_all_free_gacha_availability(image: Image.Image) -> tuple[str, dict[str, Any]]:
    frame = np.asarray(image.convert("RGB"))
    button_stats = _stats(_roi(frame, (0.10, 0.83, 0.16, 0.14)))
    label_match, recognition_details = recognize_all_free_gacha_button(image)
    availability = _resolve_all_free_gacha_availability(
        label_match,
        recognition_details,
        button_stats,
    )
    return availability, {
        "button_stats": button_stats,
        "recognition": recognition_details,
    }


def classify_state(image: Image.Image) -> tuple[str, dict[str, Any]]:
    frame = np.asarray(image.convert("RGB"))
    full = _stats(frame)
    center = _stats(_roi(frame, (0.28, 0.24, 0.44, 0.46)))
    modal = _stats(_roi(frame, (0.33, 0.34, 0.34, 0.32)))
    confirm_buttons = _stats(_roi(frame, (0.40, 0.55, 0.22, 0.11)))
    top_left_back = _stats(_roi(frame, (0.00, 0.015, 0.12, 0.09)))
    gacha_button = _stats(_roi(frame, (0.10, 0.84, 0.15, 0.12)))
    home_bottom_nav = _stats(_roi(frame, (0.04, 0.86, 0.58, 0.12)))
    home_right_events = _stats(_roi(frame, (0.78, 0.22, 0.19, 0.68)))
    home_top_right = _stats(_roi(frame, (0.78, 0.02, 0.19, 0.12)))
    left_tabs = _stats(_roi(frame, (0.055, 0.24, 0.075, 0.27)))
    top_title = _stats(_roi(frame, (0.11, 0.02, 0.22, 0.10)))
    plaza_joystick = _stats(_roi(frame, (0.10, 0.72, 0.12, 0.20)))
    plaza_actions = _stats(_roi(frame, (0.72, 0.68, 0.25, 0.28)))
    plaza_top_right = _stats(_roi(frame, (0.84, 0.02, 0.14, 0.10)))
    animation_left_margin = _stats(_roi(frame, (0.00, 0.00, 0.23, 1.00)))
    animation_top_right = _stats(_roi(frame, (0.82, 0.01, 0.15, 0.12)))
    animation_bottom_reveal = _stats(_roi(frame, (0.40, 0.84, 0.20, 0.14)))

    details: dict[str, Any] = {
        "full": full,
        "center": center,
        "modal": modal,
        "confirm_buttons": confirm_buttons,
        "top_left_back": top_left_back,
        "gacha_button": gacha_button,
        "home_bottom_nav": home_bottom_nav,
        "home_right_events": home_right_events,
        "home_top_right": home_top_right,
        "left_tabs": left_tabs,
        "top_title": top_title,
        "plaza_joystick": plaza_joystick,
        "plaza_actions": plaza_actions,
        "plaza_top_right": plaza_top_right,
        "animation_left_margin": animation_left_margin,
        "animation_top_right": animation_top_right,
        "animation_bottom_reveal": animation_bottom_reveal,
    }

    low_information_frame = (
        full["contrast"] < 2
        and full["edge_ratio"] < 0.0005
        and (full["bright_ratio"] > 0.995 or full["dark_ratio"] > 0.995)
    )
    details["low_information_frame"] = low_information_frame
    if low_information_frame:
        return "loading", details

    reward_overlay_candidate = (
        full["dark_ratio"] > 0.85
        and center["mean"] > full["mean"] + 10
        and modal["edge_ratio"] > 0.003
    )
    if reward_overlay_candidate:
        reward_overlay_match, reward_overlay_text = recognize_reward_overlay_labels(image)
        details["reward_overlay_text"] = reward_overlay_text
        if reward_overlay_match:
            return "reward_overlay", details

    regular_customer_notes_candidate = (
        full["dark_ratio"] > 0.75
        and top_title["edge_ratio"] > 0.015
        and modal["edge_ratio"] > 0.015
    )
    if regular_customer_notes_candidate:
        notes_match, notes_text = recognize_regular_customer_notes_labels(image)
        details["regular_customer_notes_text"] = notes_text
        if notes_match:
            return "restaurant_regular_customer_notes", details

    restaurant_loading_candidate = (
        full["dark_ratio"] < 0.60
        and full["edge_ratio"] < 0.012
        and full["contrast"] > 25
    )
    if restaurant_loading_candidate:
        restaurant_state, restaurant_text = recognize_restaurant_state(image)
        details["restaurant_text"] = restaurant_text
        if restaurant_state != "unknown":
            return restaurant_state, details

    business_management_candidate = (
        full["dark_ratio"] > 0.85
        and center["mean"] > full["mean"] + 10
        and modal["edge_ratio"] > 0.008
    )
    if business_management_candidate:
        business_state, business_text = recognize_business_management_state(image)
        details["business_management_text"] = business_text
        if business_state != "unknown":
            return business_state, details

    arena_repeat_result_candidate = (
        full["dark_ratio"] > 0.85
        and modal["mean"] > full["mean"] + 10
        and modal["edge_ratio"] > 0.012
    )
    if arena_repeat_result_candidate:
        arena_repeat_result_match, arena_repeat_result_text = recognize_arena_repeat_result_labels(
            image
        )
        details["arena_repeat_result_text"] = arena_repeat_result_text
        if arena_repeat_result_match:
            return "arena_repeat_battle_result", details

    arena_victory_candidate = (
        full["dark_ratio"] > 0.70
        and home_bottom_nav["dark_ratio"] > 0.95
        and home_right_events["dark_ratio"] > 0.90
    )
    if arena_victory_candidate:
        arena_victory_match, arena_victory_text = recognize_arena_victory_result_labels(image)
        details["arena_victory_text"] = arena_victory_text
        if arena_victory_match:
            return "arena_victory_result", details

    arena_auto_candidate = (
        full["dark_ratio"] > 0.75
        and center["mean"] > full["mean"] + 15
        and modal["edge_ratio"] > 0.008
    )
    if arena_auto_candidate:
        arena_auto_battle_match, arena_auto_battle_text = recognize_arena_auto_battle_labels(image)
        details["arena_auto_battle_text"] = arena_auto_battle_text
        if arena_auto_battle_match:
            return "arena_auto_battle_dialog", details

    large_activity_overlay_like = (
        full["dark_ratio"] > 0.65
        and center["mean"] > 120
        and center["bright_ratio"] > 0.08
        and modal["contrast"] > 45
        and home_bottom_nav["dark_ratio"] > 0.95
        and home_right_events["dark_ratio"] > 0.90
    )
    if large_activity_overlay_like:
        return "home_overlay", details

    blocking_ad_overlay_like = (
        full["dark_ratio"] > 0.55
        and center["mean"] > 150
        and center["bright_ratio"] > 0.20
        and modal["bright_ratio"] > 0.25
        and confirm_buttons["bright_ratio"] > 0.20
        and home_bottom_nav["dark_ratio"] > 0.95
        and home_right_events["dark_ratio"] > 0.90
    )
    if blocking_ad_overlay_like:
        return "blocking_ad_overlay", details

    dark_confirm_like = (
        full["dark_ratio"] > 0.90
        and modal["dark_ratio"] < 0.94
        and modal["edge_ratio"] > 0.012
        and confirm_buttons["mean"] > modal["mean"] + 20
        and confirm_buttons["edge_ratio"] > 0.015
    )
    if dark_confirm_like:
        confirm_match, confirm_text = recognize_free_gacha_confirmation_labels(image)
        details["free_gacha_confirm_text"] = confirm_text
        if confirm_match:
            return "confirm_free_gacha", details

    quick_hunt_setup_candidate = (
        full["dark_ratio"] > 0.90
        and center["mean"] > full["mean"] + 15
        and center["edge_ratio"] > 0.008
    )
    if quick_hunt_setup_candidate:
        quick_hunt_setup_match, quick_hunt_setup_text = recognize_quick_hunt_setup_labels(image)
        details["quick_hunt_setup_text"] = quick_hunt_setup_text
        if quick_hunt_setup_match:
            return "quick_hunt_setup", details

    dark_item_overlay_like = (
        full["dark_ratio"] > 0.95
        and center["mean"] > full["mean"] + 25
        and center["edge_ratio"] > 0.012
        and modal["edge_ratio"] > 0.012
        and home_bottom_nav["dark_ratio"] > 0.98
    )
    if dark_item_overlay_like:
        return "home_overlay", details

    gacha_item_detail_candidate = (
        full["dark_ratio"] > 0.95
        and 40 < full["mean"] < 80
        and abs(center["mean"] - full["mean"]) < 15
        and modal["edge_ratio"] > 0.004
    )
    if gacha_item_detail_candidate:
        item_detail_match, item_detail_text = recognize_gacha_item_detail_labels(image)
        details["gacha_item_detail_text"] = item_detail_text
        if item_detail_match:
            return "gacha_item_overlay", details

    loading_like = full["mean"] < 45 and full["dark_ratio"] > 0.92 and full["edge_ratio"] < 0.005
    if loading_like:
        arena_rank_match, arena_rank_text = recognize_arena_rank_change_labels(image)
        details["arena_rank_change_text"] = arena_rank_text
        if arena_rank_match:
            return "arena_rank_change", details
        return "loading", details

    restaurant_home_candidate = (
        full["dark_ratio"] < 0.65
        and left_tabs["edge_ratio"] > 0.015
        and top_title["edge_ratio"] > 0.025
    )
    if restaurant_home_candidate:
        restaurant_state, restaurant_text = recognize_restaurant_state(image)
        details["restaurant_text"] = restaurant_text
        if restaurant_state in {"restaurant_home", "restaurant_regular_customer_mode"}:
            return restaurant_state, details

    gacha_like = (
        full["dark_ratio"] < 0.65
        and left_tabs["edge_ratio"] > 0.015
        and top_title["edge_ratio"] > 0.030
    )
    if gacha_like:
        quick_hunt_map_match, quick_hunt_map_text = recognize_quick_hunt_map_labels(image)
        details["quick_hunt_map_text"] = quick_hunt_map_text
        if quick_hunt_map_match:
            return "quick_hunt_map", details
        gacha_page_match, gacha_page_text = recognize_gacha_page_labels(image)
        details["gacha_page_text"] = gacha_page_text
        if gacha_page_match:
            return "gacha_page", details

    confirm_like = (
        full["dark_ratio"] > 0.45
        and modal["mid_ratio"] > 0.45
        and modal["contrast"] > 20
        and confirm_buttons["bright_ratio"] > 0.15
    )
    if confirm_like:
        confirm_match, confirm_text = recognize_free_gacha_confirmation_labels(image)
        details["free_gacha_confirm_text"] = confirm_text
        if confirm_match:
            return "confirm_free_gacha", details

    dark_animation_like = (
        animation_left_margin["dark_ratio"] > 0.98
        and animation_left_margin["contrast"] < 10
        and animation_top_right["edge_ratio"] > 0.010
        and animation_bottom_reveal["mid_ratio"] > 0.20
    )
    reveal_animation_like = _is_reveal_animation_like(
        animation_top_right,
        animation_bottom_reveal,
    )
    if dark_animation_like or reveal_animation_like:
        return "gacha_animation", details

    overlay_like = (
        full["dark_ratio"] > 0.45
        and center["mean"] > full["mean"] + 35
        and center["contrast"] > 35
        and home_bottom_nav["dark_ratio"] > 0.85
    )
    if overlay_like:
        return "home_overlay", details

    home_labels_match, home_text = recognize_home_labels(image)
    details["home_text"] = home_text
    if home_labels_match:
        return "real_home", details

    bright_scene = full["bright_ratio"] > 0.62 and full["dark_ratio"] < 0.10
    if bright_scene:
        details["classification_rule"] = "bright_scene"
        if top_left_back["mean"] < 220 and top_left_back["mid_ratio"] > 0.25:
            return "gacha_result", details
        return "gacha_animation", details

    plaza_bright_joystick_like = (
        plaza_joystick["mid_ratio"] > 0.88
        and plaza_joystick["bright_ratio"] < 0.06
        and plaza_joystick["edge_ratio"] > 0.010
    )
    plaza_dark_joystick_like = (
        plaza_joystick["dark_ratio"] > 0.80
        and plaza_joystick["mid_ratio"] < 0.12
        and plaza_joystick["edge_ratio"] > 0.018
    )
    plaza_like = (
        (plaza_bright_joystick_like or plaza_dark_joystick_like)
        and plaza_actions["edge_ratio"] > 0.035
        and plaza_actions["contrast"] > 35
        and plaza_top_right["edge_ratio"] > 0.050
    )
    if plaza_like:
        return "plaza", details

    arena_battle_prep_match, arena_battle_prep_text = recognize_arena_battle_prep_labels(image)
    details["arena_battle_prep_text"] = arena_battle_prep_text
    if arena_battle_prep_match:
        return "arena_battle_prep", details

    arena_lobby_match, arena_lobby_text = recognize_arena_lobby_labels(image)
    details["arena_lobby_text"] = arena_lobby_text
    if arena_lobby_match:
        return "arena_lobby", details

    arena_cartridge_bar_match, arena_cartridge_bar_text = recognize_arena_cartridge_bar_labels(image)
    details["arena_cartridge_bar_text"] = arena_cartridge_bar_text
    if arena_cartridge_bar_match:
        return "arena_cartridge_bar", details

    arena_cartridge_match, arena_cartridge_text = recognize_arena_cartridge_labels(image)
    details["arena_cartridge_text"] = arena_cartridge_text
    if arena_cartridge_match:
        return "arena_cartridge_collection", details

    is_home, home_scores = recognize_home_screen(image)
    details["home_scores"] = home_scores
    if is_home:
        return "ambiguous_home", details

    return "unknown", details


def detect_arena_pool_click(image: Image.Image) -> tuple[float, float] | None:
    """Locate the arena's high-red portal, including when it is partly off-screen."""
    source_width, source_height = image.size
    width = min(768, source_width)
    height = round(source_height * width / source_width)
    resized = image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR)
    hsv = np.asarray(resized.convert("HSV"))
    red_mask = (
        (hsv[:, :, 0] >= 235)
        & (hsv[:, :, 1] >= 100)
        & (hsv[:, :, 2] >= 45)
    )

    kernel_size = max(3, min(width, height) // 70)
    if kernel_size % 2 == 0:
        kernel_size += 1
    red_mask = np.asarray(
        Image.fromarray(red_mask.astype(np.uint8) * 255)
        .filter(ImageFilter.MaxFilter(kernel_size))
        .filter(ImageFilter.MinFilter(kernel_size))
    ) > 0

    visited = np.zeros_like(red_mask, dtype=bool)
    candidates: list[tuple[int, float, float]] = []
    for start_y, start_x in zip(*np.nonzero(red_mask)):
        if visited[start_y, start_x]:
            continue
        queue = deque([(int(start_x), int(start_y))])
        visited[start_y, start_x] = True
        area = 0
        sum_x = 0
        sum_y = 0
        min_x = max_x = int(start_x)
        min_y = max_y = int(start_y)
        while queue:
            x, y = queue.pop()
            area += 1
            sum_x += x
            sum_y += y
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            neighbors = ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            for next_x, next_y in neighbors:
                if (
                    0 <= next_x < width
                    and 0 <= next_y < height
                    and red_mask[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    queue.append((next_x, next_y))

        center_x = sum_x / area
        center_y = sum_y / area
        component_width = max_x - min_x + 1
        component_height = max_y - min_y + 1
        rx = center_x / width
        ry = center_y / height
        relative_width = component_width / width
        relative_height = component_height / height
        if (
            area >= width * height * 0.002
            and 0.05 <= relative_width <= 0.20
            and 0.08 <= relative_height <= 0.16
            and 0.10 <= rx <= 1.00
            and 0.18 <= ry <= 0.75
        ):
            candidates.append((area, rx, ry))

    if not candidates:
        return None
    _area, rx, ry = max(candidates)
    return rx, ry


def _click_ratio(
    hwnd: int,
    image: Image.Image,
    key: str,
    *,
    dry_run: bool,
    logger: RunLogger,
    attempt: int = 1,
) -> None:
    point_variant = "retry" if attempt > 1 and key in RETRY_CLICK_POINTS else "primary"
    detected_point = detect_arena_pool_click(image) if key == "arena_pool" else None
    if detected_point is not None:
        rx, ry = detected_point
        point_variant = "detected"
    else:
        rx, ry = RETRY_CLICK_POINTS[key] if point_variant == "retry" else CLICK_POINTS[key]
    width, height = image.size
    x = int(width * rx)
    y = int(height * ry)
    marked_path: Path | None = None
    if logger.annotate_clicks:
        click_index = logger.next_click_index()
        marked_path = logger.save_click_image(
            image,
            f"click-{click_index:03d}-{key}.png",
            x=x,
            y=y,
            key=key,
            dry_run=dry_run,
        )
    logger.event(
        action="click",
        key=key,
        x=x,
        y=y,
        point_variant=point_variant,
        dry_run=dry_run,
        screenshot=str(marked_path) if marked_path else None,
    )
    if not dry_run:
        click_client(hwnd, x, y)


def post_home_key(hwnd: int, *, dry_run: bool, logger: RunLogger) -> None:
    logger.event(action="post_key", key="H", vk=VK_H, dry_run=dry_run)
    if dry_run:
        return
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_H, 0)
    time.sleep(0.08)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_H, 0)


def wait_for_state(
    hwnd: int,
    logger: RunLogger,
    *,
    expected: set[str],
    timeout: float,
    interval: float,
    label: str,
) -> tuple[str, Image.Image]:
    # Kept for caller compatibility; polling now follows the shared adaptive schedule.
    _ = interval
    deadline = time.monotonic() + timeout
    last_state = "unknown"
    last_image: Image.Image | None = None
    sample = 0
    poll = AdaptivePoll()
    while True:
        sample += 1
        image = safe_capture_client(hwnd, logger=logger)
        state, details = classify_state(image)
        image_path = logger.save_image(image, f"{label}-{sample:02d}-{state}.png")
        logger.event(
            action="wait_state",
            label=label,
            sample=sample,
            state=state,
            expected=sorted(expected),
            screenshot=str(image_path),
            details=details,
        )
        if state != last_state:
            poll.reset()
        last_state = state
        last_image = image
        if state in expected:
            return state, image
        now = time.monotonic()
        if state in UNBOUNDED_LOADING_STATES:
            deadline = now + timeout
        elif now >= deadline:
            break
        delay = poll.next_delay(remaining=deadline - now)
        logger.event(
            action="wait_state_delay",
            label=label,
            sample=sample,
            state=state,
            next_check_seconds=delay,
            timeout_suspended=state in UNBOUNDED_LOADING_STATES,
        )
        time.sleep(delay)
    if last_image is None:
        raise RuntimeError(f"no state captured while waiting for {sorted(expected)}")
    return last_state, last_image


def return_home_from_plaza(
    hwnd: int,
    image: Image.Image,
    *,
    dry_run: bool,
    logger: RunLogger,
    interval: float,
) -> tuple[bool, str]:
    expected = {"real_home", "home_overlay"}

    for attempt in range(1, 3):
        logger.event(action="return_home_attempt", method="plaza_home_click", attempt=attempt)
        _click_ratio(hwnd, image, "plaza_home", dry_run=dry_run, logger=logger)
        if dry_run:
            return True, "dry-run planned plaza_home click"
        state, image = wait_for_state(
            hwnd,
            logger,
            expected=expected,
            timeout=10.0,
            interval=interval,
            label=f"after-plaza-click-{attempt}",
        )
        if state in expected:
            return True, f"returned home by plaza_home click, state={state}"
        if state in {"loading", "unknown"}:
            state, image = wait_for_state(
                hwnd,
                logger,
                expected=expected | {"plaza"},
                timeout=8.0,
                interval=interval,
                label=f"after-plaza-click-extra-wait-{attempt}",
            )
            if state in expected:
                return True, f"returned home by plaza_home click after loading, state={state}"
        if state not in {"plaza", "loading", "unknown", "ambiguous_home"}:
            return False, f"unexpected state after plaza_home click: {state}"

    for attempt in range(1, 3):
        logger.event(action="return_home_attempt", method="post_h_key", attempt=attempt)
        post_home_key(hwnd, dry_run=dry_run, logger=logger)
        if dry_run:
            return True, "dry-run planned background H key"
        state, image = wait_for_state(
            hwnd,
            logger,
            expected=expected,
            timeout=10.0,
            interval=interval,
            label=f"after-post-h-{attempt}",
        )
        if state in expected:
            return True, f"returned home by background H key, state={state}"
        if state in {"loading", "unknown"}:
            state, image = wait_for_state(
                hwnd,
                logger,
                expected=expected | {"plaza"},
                timeout=8.0,
                interval=interval,
                label=f"after-post-h-extra-wait-{attempt}",
            )
            if state in expected:
                return True, f"returned home by background H key after loading, state={state}"
        if state not in {"plaza", "loading", "unknown", "ambiguous_home"}:
            return False, f"unexpected state after background H key: {state}"

    return False, "failed to return from plaza by background click or background H key"


def _target_tab(target: str) -> str:
    try:
        return TARGET_LABELS[target]
    except KeyError as exc:
        raise ValueError(f"unsupported target: {target}") from exc


def is_free_gacha_confirm_transition(state: str) -> bool:
    return state in {"gacha_page", "gacha_animation", "gacha_result", "loading"}


def click_with_fixed_retry(
    hwnd: int,
    image: Image.Image,
    key: str,
    *,
    verify: Callable[[str, Image.Image], bool],
    description: str,
    dry_run: bool,
    logger: RunLogger,
    verify_timeout: float = 20.0,
    attempts: int = 2,
) -> tuple[bool, str, Image.Image, str]:
    current_image = image
    source_state, _ = classify_state(image)
    current_state = source_state
    for attempt in range(1, attempts + 1):
        _click_ratio(hwnd, current_image, key, dry_run=dry_run, logger=logger, attempt=attempt)
        if dry_run:
            return True, current_state, current_image, f"dry-run planned {description}"

        deadline = time.monotonic() + verify_timeout
        poll = AdaptivePoll()
        sample = 0
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            delay = poll.next_delay(remaining=deadline - now)
            logger.event(
                action="wait_click_effect",
                key=key,
                description=description,
                attempt=attempt,
                sample=sample + 1,
                next_check_seconds=delay,
            )
            time.sleep(delay)
            sample += 1
            current_image = safe_capture_client(hwnd, logger=logger)
            current_state, details = classify_state(current_image)
            stamp = datetime.now().strftime("%H%M%S-%f")
            verify_path = logger.save_image(
                current_image,
                f"verify-{stamp}-{key}-attempt-{attempt}-sample-{sample}-{current_state}.png",
            )
            succeeded = verify(current_state, current_image)
            logger.event(
                action="verify_click",
                key=key,
                description=description,
                attempt=attempt,
                sample=sample,
                state=current_state,
                succeeded=succeeded,
                screenshot=str(verify_path),
                details=details,
            )
            if succeeded:
                return True, current_state, current_image, f"{description} succeeded on attempt {attempt}"
            if current_state in UNBOUNDED_LOADING_STATES:
                deadline = time.monotonic() + verify_timeout
                continue
            if current_state != source_state:
                reason = (
                    f"{description} changed from {source_state} to unexpected state "
                    f"{current_state}; retry cancelled"
                )
                return False, current_state, current_image, reason

        if attempt < attempts:
            logger.event(
                action="retry_click",
                key=key,
                description=description,
                previous_attempts=attempt,
                verify_timeout=verify_timeout,
            )

    reason = f"{description} did not take effect after {attempts} clicks"
    return False, current_state, current_image, reason


def skip_gacha_animation(
    hwnd: int,
    image: Image.Image,
    *,
    dry_run: bool,
    logger: RunLogger,
    interval: float,
    attempts: int = 2,
    effect_timeout: float = 30.0,
) -> tuple[bool, str, Image.Image, str]:
    current_image = image
    state = "gacha_animation"
    for attempt in range(1, attempts + 1):
        _click_ratio(
            hwnd,
            current_image,
            "skip_animation",
            dry_run=dry_run,
            logger=logger,
            attempt=attempt,
        )
        if dry_run:
            return True, "gacha_animation", current_image, "dry-run planned skip gacha animation"

        state, current_image = wait_for_state(
            hwnd,
            logger,
            expected={"gacha_result", "loading"},
            timeout=effect_timeout,
            interval=interval,
            label=f"after-skip-animation-{attempt}",
        )
        if state in {"gacha_result", "loading"}:
            return True, state, current_image, f"skip gacha animation succeeded on attempt {attempt}"
        if state not in {"gacha_animation", "unknown"}:
            reason = f"skip gacha animation reached unexpected state {state}"
            return False, state, current_image, reason

    reason = f"skip gacha animation did not reach a result after {attempts} clicks"
    return False, state, current_image, reason


def run_free_gacha(
    *,
    targets: list[str],
    timeout: float,
    interval: float,
    dry_run: bool,
    test_mode: bool,
    log_root: Path,
) -> ActionResult:
    hwnd = find_game_window()
    logger = RunLogger(log_root, annotate_clicks=test_mode)
    logger.event(
        action="start",
        flow="free_gacha",
        targets=targets,
        timeout=timeout,
        dry_run=dry_run,
        test_mode=test_mode,
    )
    if not hwnd:
        reason = "game window not found"
        logger.event(action="stop", result="error", reason=reason)
        return ActionResult("missing_window", "stop", reason)

    target_index = 0
    switched: set[str] = set()
    result_back_target: str | None = None
    last_progress_at = time.monotonic()
    previous_state: str | None = None
    step = 0
    loading_poll = AdaptivePoll()

    while time.monotonic() - last_progress_at < timeout:
        step += 1
        try:
            image = safe_capture_client(hwnd, logger=logger)
        except Exception as exc:  # noqa: BLE001
            reason = f"capture failed: {exc!r}"
            logger.failure(reason)
            logger.event(action="stop", result="error", reason=reason)
            return ActionResult("capture_error", "stop", reason)
        state, details = classify_state(image)
        if previous_state is not None and state != previous_state:
            last_progress_at = time.monotonic()
            loading_poll.reset()
            logger.event(
                action="progress",
                reason="recognized_state_changed",
                previous_state=previous_state,
                state=state,
                stall_timeout=timeout,
            )
        previous_state = state
        try:
            image_path = logger.save_image(image, f"step-{step:03d}-{state}.png")
        except Exception as exc:  # noqa: BLE001
            reason = f"debug image save failed: {exc!r}"
            logger.failure(reason)
            logger.event(action="stop", result="error", reason=reason)
            return ActionResult(state, "stop", reason)
        current_target = targets[target_index] if target_index < len(targets) else None
        logger.event(
            action="classify",
            step=step,
            state=state,
            current_target=current_target,
            screenshot=str(image_path),
            details=details,
        )

        if target_index >= len(targets):
            reason = "all requested free gacha targets completed"
            logger.event(action="stop", result="success", reason=reason)
            return ActionResult(state, "stop", reason)

        if result_back_target is not None and state == "gacha_page":
            logger.event(action="target_complete", target=result_back_target)
            target_index += 1
            result_back_target = None
            last_progress_at = time.monotonic()
            logger.event(
                action="progress",
                reason="gacha_target_completed",
                completed_targets=target_index,
                stall_timeout=timeout,
            )
            current_target = targets[target_index] if target_index < len(targets) else None
            if current_target is None:
                reason = "all requested free gacha targets completed"
                logger.event(action="stop", result="success", reason=reason)
                return ActionResult(state, "stop", reason)

        if state == "loading":
            logger.event(action="wait_loading", step=step)
            last_progress_at = time.monotonic()
            time.sleep(loading_poll.next_delay())
            continue

        if state in {"home_overlay", "blocking_ad_overlay", "gacha_item_overlay"}:
            overlay_before = image.copy()
            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "dismiss_overlay",
                verify=lambda next_state, next_image: (
                    next_state not in {"home_overlay", "blocking_ad_overlay", "gacha_item_overlay"}
                    or _mean_region_difference(overlay_before, next_image) >= 2.5
                ),
                description="dismiss home overlay",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            if not dry_run:
                last_progress_at = time.monotonic()
            continue

        if state == "real_home":
            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "home_gacha",
                verify=lambda next_state, _next_image: next_state in {"gacha_page", "loading"},
                description="open gacha from home",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            if not dry_run:
                last_progress_at = time.monotonic()
            continue

        if state == "plaza":
            ok, reason = return_home_from_plaza(
                hwnd,
                image,
                dry_run=dry_run,
                logger=logger,
                interval=interval,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason)
                return ActionResult(state, "stop", reason)
            if not dry_run:
                last_progress_at = time.monotonic()
            time.sleep(interval)
            continue

        if state == "gacha_page":
            assert current_target is not None
            if current_target not in switched:
                selected_target = detect_selected_gacha_target(image)
                logger.event(
                    action="detect_selected_gacha_target",
                    expected=current_target,
                    selected=selected_target,
                )
                if selected_target == current_target:
                    switched.add(current_target)
                    last_progress_at = time.monotonic()
                else:
                    ok, _, _, reason = click_with_fixed_retry(
                        hwnd,
                        image,
                        _target_tab(current_target),
                        verify=lambda next_state, next_image: (
                            next_state == "gacha_page"
                            and detect_selected_gacha_target(next_image) == current_target
                        ),
                        description=f"select {current_target} gacha tab",
                        dry_run=dry_run,
                        logger=logger,
                    )
                    if not ok:
                        logger.failure(reason)
                        logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                        return ActionResult(state, "stop", reason)
                    switched.add(current_target)
                    if not dry_run:
                        last_progress_at = time.monotonic()
                continue

            availability, availability_details = detect_all_free_gacha_availability(image)
            logger.event(
                action="detect_all_free_gacha_availability",
                target=current_target,
                availability=availability,
                details=availability_details,
            )
            if availability == "used":
                logger.event(
                    action="target_complete",
                    target=current_target,
                    reason="free_gacha_already_used",
                )
                target_index += 1
                last_progress_at = time.monotonic()
                logger.event(
                    action="progress",
                    reason="free_gacha_target_already_used",
                    completed_targets=target_index,
                    stall_timeout=timeout,
                )
                continue
            if availability == "unknown":
                reason = f"cannot safely determine whether free gacha remains for {current_target}"
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)

            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "all_free",
                verify=lambda next_state, _next_image: next_state == "confirm_free_gacha",
                description=f"open all-free confirmation for {current_target}",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            if not dry_run:
                last_progress_at = time.monotonic()
            continue

        if state == "confirm_free_gacha":
            ok, next_state, next_image, reason = click_with_fixed_retry(
                hwnd,
                image,
                "confirm",
                verify=lambda candidate, _next_image: is_free_gacha_confirm_transition(candidate),
                description="confirm free gacha",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            if not dry_run and next_state == "gacha_page":
                next_state, next_image = wait_for_state(
                    hwnd,
                    logger,
                    expected={"gacha_animation", "gacha_result", "loading"},
                    timeout=30.0,
                    interval=interval,
                    label="after-confirm-network-transition",
                )
                if next_state not in {"gacha_animation", "gacha_result", "loading"}:
                    reason = (
                        "free gacha confirmation stayed on gacha_page for 30 seconds; "
                        "no second click was attempted"
                    )
                    logger.failure(reason)
                    logger.event(
                        action="stop",
                        result="error",
                        reason=reason,
                        screenshot=str(image_path),
                    )
                    return ActionResult(next_state, "stop", reason)
            if not dry_run:
                last_progress_at = time.monotonic()
            continue

        if state == "gacha_animation":
            ok, _, _, reason = skip_gacha_animation(
                hwnd,
                image,
                dry_run=dry_run,
                logger=logger,
                interval=interval,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            if not dry_run:
                last_progress_at = time.monotonic()
            continue

        if state == "gacha_result":
            ok, _, _, reason = click_with_fixed_retry(
                hwnd,
                image,
                "result_back",
                verify=lambda next_state, _next_image: next_state in {"gacha_page", "loading"},
                description="return from gacha result",
                dry_run=dry_run,
                logger=logger,
            )
            if not ok:
                logger.failure(reason)
                logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
                return ActionResult(state, "stop", reason)
            result_back_target = current_target
            if not dry_run:
                last_progress_at = time.monotonic()
            continue

        reason = f"unknown or unsupported state: {state}"
        logger.failure(reason)
        logger.event(action="stop", result="error", reason=reason, screenshot=str(image_path))
        return ActionResult(state, "stop", reason)

    reason = f"free gacha made no progress for {timeout:.0f}s"
    logger.failure(reason)
    logger.event(action="stop", result="error", reason=reason)
    return ActionResult("timeout", "stop", reason)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BrownDust II free gacha automation.")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true", help="classify and log without clicking")
    parser.add_argument("--test-mode", action="store_true", help="save annotated screenshots before every click")
    parser.add_argument("--targets", nargs="+", default=["costume", "gear"], choices=sorted(TARGET_LABELS))
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_root = Path(args.log_root) if args.log_root else Path.cwd() / "logs" / "free_gacha" / stamp
    result = run_free_gacha(
        targets=args.targets,
        timeout=args.timeout,
        interval=args.interval,
        dry_run=args.dry_run,
        test_mode=args.test_mode,
        log_root=log_root,
    )
    print(f"state={result.state}")
    print(f"action={result.action}")
    print(f"reason={result.reason}")
    print(f"log_root={log_root}")
    if result.reason != "all requested free gacha targets completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
