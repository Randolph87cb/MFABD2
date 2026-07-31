"""Position-aware OCR helpers for stable game UI labels."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image


HOME_LABEL_GROUPS = {
    "left_menu": {
        "region": (0.04, 0.08, 0.31, 0.42),
        "labels": (
            "我的小屋",
            "好友",
            "公会",
            "经营管理",
            "格鲁TALK",
            "亲密度",
            "街机游戏",
        ),
    },
    "quick_hunt": {
        "region": (0.84, 0.16, 0.14, 0.22),
        "labels": ("快速狩猎",),
    },
    "bottom_nav": {
        "region": (0.04, 0.83, 0.60, 0.16),
        "labels": (
            "抽抽乐",
            "伙伴",
            "背包",
            "剧情",
            "珍藏集",
            "任务",
            "成就",
            "活动",
            "商店",
            "付费商店",
        ),
    },
}

QUICK_HUNT_MAP_LABEL_GROUPS = {
    "left_categories": {
        "region": (0.04, 0.10, 0.18, 0.42),
        "labels": ("狩猎场", "金币", "史莱姆", "圣石洞穴"),
    },
    "start_button": {
        "region": (0.76, 0.82, 0.22, 0.16),
        "labels": ("快速狩猎",),
    },
}

QUICK_HUNT_SETUP_LABEL_GROUPS = {
    "header": {
        "region": (0.30, 0.24, 0.38, 0.11),
        "labels": ("快速狩猎",),
    },
    "body": {
        "region": (0.34, 0.34, 0.32, 0.30),
        "labels": ("狩猎1次", "随机奖励"),
    },
    "buttons": {
        "region": (0.36, 0.68, 0.28, 0.10),
        "labels": ("取消", "狩猎"),
    },
}

QUICK_HUNT_RESULT_LABEL_GROUPS = {
    "header": {
        "region": (0.40, 0.28, 0.20, 0.11),
        "labels": ("REWARD",),
    },
    "footer": {
        "region": (0.38, 0.88, 0.25, 0.08),
        "labels": ("点击画面即可返回",),
    },
}


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    from rapidocr import RapidOCR

    return RapidOCR()


def _normalize_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).upper()


def _partial_similarity(expected: str, actual: str) -> float:
    expected = _normalize_text(expected)
    actual = _normalize_text(actual)
    if not expected or not actual:
        return 0.0
    if expected in actual or actual in expected:
        return min(len(expected), len(actual)) / max(len(expected), len(actual))

    sizes = range(max(1, len(expected) - 1), min(len(actual), len(expected) + 1) + 1)
    candidates = (
        actual[index : index + size]
        for size in sizes
        for index in range(0, len(actual) - size + 1)
    )
    return max(
        (SequenceMatcher(None, expected, candidate).ratio() for candidate in candidates),
        default=SequenceMatcher(None, expected, actual).ratio(),
    )


def _point_in_region(
    point: tuple[float, float],
    region: tuple[float, float, float, float],
) -> bool:
    x, y = point
    rx, ry, rw, rh = region
    return rx <= x <= rx + rw and ry <= y <= ry + rh


def _recognize_label_groups(
    image: Image.Image,
    groups: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, Any] | None]:
    try:
        result = _ocr_engine()(np.asarray(image.convert("RGB")))
    except (ImportError, ModuleNotFoundError) as exc:
        return {}, {}, {"available": False, "error": repr(exc)}
    except Exception as exc:  # noqa: BLE001 - recognition failure must be visible to callers.
        return {}, {}, {"available": True, "error": repr(exc)}

    width, height = image.size
    grouped_texts: dict[str, list[str]] = {name: [] for name in groups}
    boxes = result.boxes if result.boxes is not None else ()
    texts = result.txts if result.txts is not None else ()
    scores = result.scores if result.scores is not None else ()
    for box, text, score in zip(boxes, texts, scores):
        if float(score) < 0.55:
            continue
        center_x = float(np.mean(box[:, 0])) / width
        center_y = float(np.mean(box[:, 1])) / height
        for name, config in groups.items():
            if _point_in_region((center_x, center_y), config["region"]):
                grouped_texts[name].append(str(text))

    matches: dict[str, list[str]] = {}
    for name, config in groups.items():
        actual_texts = grouped_texts[name]
        matches[name] = [
            expected
            for expected in config["labels"]
            if any(_partial_similarity(expected, actual) >= 0.74 for actual in actual_texts)
        ]
    return grouped_texts, matches, None


def recognize_home_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize homepage labels only when they appear in their fixed UI regions."""
    grouped_texts, matches, error = _recognize_label_groups(image, HOME_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_home = (
        "快速狩猎" in matches["quick_hunt"]
        and len(matches["left_menu"]) >= 3
        and len(matches["bottom_nav"]) >= 4
    )
    return is_home, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {
            "quick_hunt": 1,
            "left_menu": 3,
            "bottom_nav": 4,
        },
    }


def recognize_quick_hunt_map_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the quick-hunt map from stable labels at fixed positions."""
    grouped_texts, matches, error = _recognize_label_groups(image, QUICK_HUNT_MAP_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_quick_hunt_map = (
        "快速狩猎" in matches["start_button"]
        and len(matches["left_categories"]) >= 3
    )
    return is_quick_hunt_map, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {
            "start_button": 1,
            "left_categories": 3,
        },
    }


def recognize_quick_hunt_setup_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the quick-hunt setup dialog from its fixed text groups."""
    grouped_texts, matches, error = _recognize_label_groups(image, QUICK_HUNT_SETUP_LABEL_GROUPS)
    if error is not None:
        return False, error
    has_hunt_button = any(
        _normalize_text(text).startswith("狩猎")
        for text in grouped_texts["buttons"]
    )
    is_quick_hunt_setup = (
        "快速狩猎" in matches["header"]
        and len(matches["body"]) >= 1
        and "取消" in matches["buttons"]
        and has_hunt_button
    )
    return is_quick_hunt_setup, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {
            "header": 1,
            "body": 1,
            "cancel_button": 1,
            "hunt_button_prefix": "狩猎",
        },
    }


def recognize_quick_hunt_result_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the quick-hunt reward screen from its fixed heading and footer."""
    grouped_texts, matches, error = _recognize_label_groups(image, QUICK_HUNT_RESULT_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_quick_hunt_result = (
        "REWARD" in matches["header"]
        and "点击画面即可返回" in matches["footer"]
    )
    return is_quick_hunt_result, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {
            "header": 1,
            "footer": 1,
        },
    }
