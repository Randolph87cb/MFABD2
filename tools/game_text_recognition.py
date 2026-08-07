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

GACHA_PAGE_LABEL_GROUPS = {
    "title": {
        "region": (0.00, 0.00, 0.36, 0.13),
        "labels": ("服装抽抽乐", "装备抽抽乐", "抽抽乐记录"),
    },
    "tabs": {
        "region": (0.04, 0.16, 0.22, 0.34),
        "labels": ("服装", "装备"),
    },
}

FREE_GACHA_CONFIRM_LABEL_GROUPS = {
    "header": {
        "region": (0.30, 0.32, 0.40, 0.20),
        "labels": ("确认抽抽乐",),
    },
    "buttons": {
        "region": (0.38, 0.53, 0.24, 0.15),
        "labels": ("取消", "确认"),
    },
}

GACHA_ITEM_DETAIL_LABEL_GROUPS = {
    "detail": {
        "region": (0.23, 0.24, 0.55, 0.52),
        "labels": ("抽抽乐券", "查看获取途径"),
    },
}

ARENA_CARTRIDGE_LABEL_GROUPS = {
    "title": {
        "region": (0.05, 0.00, 0.32, 0.10),
        "labels": ("游戏卡珍藏集",),
    },
    "gameplay_cards": {
        "region": (0.04, 0.34, 0.84, 0.36),
        "labels": ("玩法游戏卡", "黄金竞技场", "奇幻广场"),
    },
}

ARENA_CARTRIDGE_BAR_LABEL_GROUPS = {
    "bottom_bar": {
        "region": (0.02, 0.76, 0.96, 0.23),
        "labels": ("店长游戏卡", "剧情游戏卡", "角色游戏卡", "玩法游戏卡", "活动游戏卡"),
    },
}

ARENA_LOBBY_LABEL_GROUPS = {
    "top_left": {
        "region": (0.14, 0.03, 0.28, 0.20),
        "labels": (),
    },
    "resources": {
        "region": (0.38, 0.00, 0.32, 0.12),
        "labels": (),
    },
}

ARENA_BATTLE_PREP_LABEL_GROUPS = {
    "bottom_controls": {
        "region": (0.02, 0.82, 0.96, 0.16),
        "labels": ("阵形设置", "切换画面", "自动战斗", "BATTLE"),
    },
}

ARENA_AUTO_BATTLE_LABEL_GROUPS = {
    "dialog": {
        "region": (0.27, 0.22, 0.46, 0.58),
        "labels": ("自动战斗", "MAX", "取消", "10倍战斗开始"),
    },
}

ARENA_REPEAT_RESULT_LABEL_GROUPS = {
    "dialog": {
        "region": (0.35, 0.25, 0.30, 0.52),
        "labels": ("反复战斗结果", "攻击战绩", "积分变化", "斗魂奖牌总获得量"),
    },
}

ARENA_VICTORY_RESULT_LABEL_GROUPS = {
    "summary": {
        "region": (0.68, 0.02, 0.30, 0.72),
        "labels": ("VICTORY", "胜利分", "获胜"),
    },
    "controls": {
        "region": (0.66, 0.78, 0.32, 0.21),
        "labels": ("REWARD", "战斗", "离开"),
    },
}

ARENA_RANK_CHANGE_LABEL_GROUPS = {
    "rank": {
        "region": (0.33, 0.22, 0.34, 0.56),
        "labels": ("胜利分", "恭喜晋级", "晋级", "降级"),
    },
    "button": {
        "region": (0.42, 0.86, 0.16, 0.12),
        "labels": ("确认",),
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

ENTRY_STATUS_LABEL_GROUPS = {
    "status": {
        "region": (0.52, 0.46, 0.42, 0.38),
        "labels": (
            "TOUCH TO START",
            "正在确认下载容量",
            "正在下载",
            "下载中",
            "确认下载",
            "开始下载",
            "游戏启动中",
            "正在启动",
            "正在登录",
            "连接服务器",
            "加载中",
        ),
    },
    "confirm_button": {
        "region": (0.42, 0.50, 0.32, 0.28),
        "labels": ("确认下载", "开始下载", "下载", "确定"),
    },
    "download_progress": {
        "region": (0.02, 0.84, 0.96, 0.12),
        "labels": ("正在下载", "下载中"),
    },
}

RETURN_HOME_LABEL_GROUPS = {
    "home_control": {
        "region": (0.925, 0.07, 0.035, 0.04),
        "ocr_width": 540,
        "labels": ("H",),
    },
}


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    from rapidocr import RapidOCR

    return RapidOCR(
        params={
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Global.log_level": "error",
        }
    )


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


def _recognize_label_groups(
    image: Image.Image,
    groups: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, Any] | None]:
    source = image.convert("RGB")
    grouped_texts: dict[str, list[str]] = {name: [] for name in groups}
    crops: list[tuple[str, Image.Image]] = []
    for name, config in groups.items():
        x, y, width, height = config["region"]
        crop = source.crop(
            (
                round(source.width * x),
                round(source.height * y),
                round(source.width * (x + width)),
                round(source.height * (y + height)),
            )
        )
        ocr_width = config.get("ocr_width")
        if ocr_width and crop.width < ocr_width:
            scale = ocr_width / crop.width
            crop = crop.resize(
                (ocr_width, round(crop.height * scale)),
                Image.Resampling.LANCZOS,
            )
        if crop.width > 1200:
            scale = 1200 / crop.width
            crop = crop.resize(
                (1200, round(crop.height * scale)),
                Image.Resampling.LANCZOS,
            )
        crops.append((name, crop))

    padding = 12
    atlas_width = max(crop.width for _, crop in crops)
    atlas_height = sum(crop.height for _, crop in crops) + padding * (len(crops) - 1)
    atlas = Image.new("RGB", (atlas_width, atlas_height))
    group_bands: list[tuple[str, int, int]] = []
    cursor_y = 0
    for name, crop in crops:
        atlas.paste(crop, (0, cursor_y))
        group_bands.append((name, cursor_y, cursor_y + crop.height))
        cursor_y += crop.height + padding

    try:
        engine = _ocr_engine()
        result = engine(np.asarray(atlas))
    except (ImportError, ModuleNotFoundError) as exc:
        return {}, {}, {"available": False, "error": repr(exc)}
    except Exception as exc:  # noqa: BLE001 - recognition failure must be visible to callers.
        return {}, {}, {"available": True, "error": repr(exc)}

    boxes = result.boxes if result.boxes is not None else ()
    texts = result.txts if result.txts is not None else ()
    scores = result.scores if result.scores is not None else ()
    for box, text, score in zip(boxes, texts, scores):
        if float(score) < 0.55:
            continue
        center_y = float(np.mean(box[:, 1]))
        for name, start_y, end_y in group_bands:
            if start_y <= center_y <= end_y:
                grouped_texts[name].append(str(text))
                break

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


def recognize_gacha_page_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the gacha page from its fixed title and left category labels."""
    grouped_texts, matches, error = _recognize_label_groups(image, GACHA_PAGE_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_gacha_page = bool(matches["title"]) and bool(matches["tabs"])
    return is_gacha_page, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"title": 1, "tabs": 1},
    }


def recognize_free_gacha_confirmation_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the all-free gacha confirmation from fixed dialog labels."""
    grouped_texts, matches, error = _recognize_label_groups(
        image,
        FREE_GACHA_CONFIRM_LABEL_GROUPS,
    )
    if error is not None:
        return False, error
    is_confirmation = (
        "确认抽抽乐" in matches["header"]
        and "取消" in matches["buttons"]
        and "确认" in matches["buttons"]
    )
    return is_confirmation, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"header": 1, "buttons": 2},
    }


def recognize_gacha_item_detail_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the item detail opened from a gacha animation or result."""
    grouped_texts, matches, error = _recognize_label_groups(
        image,
        GACHA_ITEM_DETAIL_LABEL_GROUPS,
    )
    if error is not None:
        return False, error
    is_item_detail = len(matches["detail"]) == 2
    return is_item_detail, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"detail": 2},
    }


def recognize_arena_cartridge_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the cartridge collection page from its title and gameplay row."""
    grouped_texts, matches, error = _recognize_label_groups(image, ARENA_CARTRIDGE_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_collection = bool(matches["title"]) and bool(matches["gameplay_cards"])
    return is_collection, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"title": 1, "gameplay_cards": 1},
    }


def recognize_arena_cartridge_bar_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the in-field cartridge bar from its fixed category labels."""
    grouped_texts, matches, error = _recognize_label_groups(image, ARENA_CARTRIDGE_BAR_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_bar = len(matches["bottom_bar"]) >= 3 and "玩法游戏卡" in matches["bottom_bar"]
    return is_bar, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"bottom_bar": 3, "gameplay_card_label": 1},
    }


def recognize_arena_lobby_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the arena lobby from season text and cocktail capacity."""
    grouped_texts, _matches, error = _recognize_label_groups(image, ARENA_LOBBY_LABEL_GROUPS)
    if error is not None:
        return False, error
    top_left = "".join(_normalize_text(text) for text in grouped_texts["top_left"])
    resource_texts = [_normalize_text(text) for text in grouped_texts["resources"]]
    has_capacity = any(re.search(r"\d{1,2}40", text) for text in resource_texts)
    is_lobby = "赛季" in top_left and "胜利分" in top_left and has_capacity
    return is_lobby, {
        "available": True,
        "texts": grouped_texts,
        "requirements": {"top_left": ["赛季", "胜利分"], "resources": "0-40/40"},
    }


def recognize_arena_battle_prep_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the arena battle preparation page from bottom controls."""
    grouped_texts, matches, error = _recognize_label_groups(image, ARENA_BATTLE_PREP_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_prep = "自动战斗" in matches["bottom_controls"] and "BATTLE" in matches["bottom_controls"]
    return is_prep, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"bottom_controls": ["自动战斗", "BATTLE"]},
    }


def recognize_arena_auto_battle_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the arena auto-battle dialog from its fixed controls."""
    grouped_texts, matches, error = _recognize_label_groups(image, ARENA_AUTO_BATTLE_LABEL_GROUPS)
    if error is not None:
        return False, error
    required = {"自动战斗", "MAX", "取消", "10倍战斗开始"}
    is_dialog = required <= set(matches["dialog"])
    return is_dialog, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"dialog": sorted(required)},
    }


def recognize_arena_repeat_result_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the completed repeated-battle result dialog."""
    grouped_texts, matches, error = _recognize_label_groups(image, ARENA_REPEAT_RESULT_LABEL_GROUPS)
    if error is not None:
        return False, error
    # The stylized title is occasionally read poorly; three independent result
    # labels in this fixed dialog region are a stronger signal than the title alone.
    is_result = len(matches["dialog"]) >= 3
    return is_result, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"dialog": "three result labels"},
    }


def recognize_arena_victory_result_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the arena victory page shown after closing repeated-battle results."""
    grouped_texts, matches, error = _recognize_label_groups(image, ARENA_VICTORY_RESULT_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_result = (
        bool(matches["summary"])
        and "REWARD" in matches["controls"]
        and "离开" in matches["controls"]
    )
    return is_result, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {
            "summary": 1,
            "controls": ["REWARD", "离开"],
        },
    }


def recognize_arena_rank_change_labels(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize an optional arena promotion or demotion confirmation page."""
    grouped_texts, matches, error = _recognize_label_groups(image, ARENA_RANK_CHANGE_LABEL_GROUPS)
    if error is not None:
        return False, error
    is_rank_change = bool(matches["rank"]) and "确认" in matches["button"]
    return is_rank_change, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"rank": 1, "button": ["确认"]},
    }


def recognize_entry_status(image: Image.Image) -> tuple[str, dict[str, Any]]:
    """Recognize actionable and waiting states on the title/download screen."""
    grouped_texts, matches, error = _recognize_label_groups(image, ENTRY_STATUS_LABEL_GROUPS)
    if error is not None:
        return "unknown", error

    normalized = [
        _normalize_text(text)
        for texts in grouped_texts.values()
        for text in texts
    ]
    confirm_texts = [
        _normalize_text(text)
        for text in grouped_texts["confirm_button"]
    ]
    has_download_context = any("下载" in text for text in normalized)
    if any("TOUCHTOSTART" in text for text in normalized):
        state = "touch_ready"
    elif any(
        marker in text
        for text in normalized
        for marker in ("游戏启动中", "正在启动", "正在登录", "连接服务器", "加载中")
    ):
        state = "startup_waiting"
    elif has_download_context and any(
        text in {"确认下载", "开始下载", "下载", "确定"}
        for text in confirm_texts
    ):
        state = "download_confirmation"
    elif any(
        marker in text
        for text in normalized
        for marker in ("正在确认下载容量", "正在下载", "下载中", "检查更新")
    ):
        state = "download_waiting"
    elif any(
        text.startswith("下载") and any(character.isdigit() for character in text)
        for text in normalized
    ):
        state = "download_waiting"
    else:
        state = "unknown"
    return state, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "state": state,
    }


def recognize_return_home_control(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the fixed top-right H control used by returnable game scenes."""
    grouped_texts, matches, error = _recognize_label_groups(image, RETURN_HOME_LABEL_GROUPS)
    if error is not None:
        return False, error
    found = "H" in matches["home_control"]
    return found, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "found": found,
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
