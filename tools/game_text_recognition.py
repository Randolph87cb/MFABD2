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

GACHA_TARGET_LABEL_GROUPS = {
    "title": {
        "region": (0.00, 0.00, 0.36, 0.13),
        "labels": (),
    },
}

PLAZA_LABEL_GROUPS = {
    "chat_input": {
        "region": (0.01, 0.84, 0.30, 0.15),
        "labels": (),
    },
}

ALL_FREE_GACHA_LABEL_GROUPS = {
    "button": {
        "region": (0.10, 0.83, 0.16, 0.14),
        "ocr_width": 640,
        "labels": ("所有免费抽抽乐",),
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
        "labels": (
            "玩法游戏卡",
            "恶魔城",
            "冒险航线",
            "末日之书",
            "黄金竞技场",
            "魂之盘",
            "奇幻广场",
        ),
    },
}

ARENA_CARTRIDGE_BAR_LABEL_GROUPS = {
    "bottom_bar": {
        "region": (0.02, 0.76, 0.96, 0.23),
        "labels": (
            "店长游戏卡",
            "剧情游戏卡",
            "角色游戏卡",
            "玩法游戏卡",
            "战斗玩法游戏卡带",
            "生活玩法游戏卡带",
            "活动游戏卡",
        ),
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

REWARD_OVERLAY_LABEL_GROUPS = {
    "header": {
        "region": (0.40, 0.28, 0.20, 0.11),
        "labels": ("REWARD",),
    },
    "footer": {
        "region": (0.38, 0.88, 0.25, 0.08),
        "labels": ("点击画面即可返回",),
    },
}

BUSINESS_MANAGEMENT_LABEL_GROUPS = {
    "dialog": {
        "region": (0.32, 0.18, 0.36, 0.64),
        "labels": (
            "餐馆营业额现状",
            "渔笼收获情况",
            "助手工作情况",
            "结算",
            "回收",
            "取消",
            "一键获得",
        ),
    },
}

RESTAURANT_LABEL_GROUPS = {
    "title": {
        "region": (0.04, 0.01, 0.26, 0.12),
        "labels": ("格鲁菲餐厅",),
    },
    "left_controls": {
        "region": (0.04, 0.10, 0.24, 0.28),
        "labels": ("常客笔记", "格鲁TALK", "立即前往游戏卡", "常客", "亲密度"),
    },
    "bottom_controls": {
        "region": (0.04, 0.84, 0.28, 0.16),
        "labels": ("员工", "客人", "成长"),
    },
    "settlement": {
        "region": (0.78, 0.82, 0.20, 0.17),
        "labels": ("结算",),
    },
    "regular_customer_mode": {
        "region": (0.42, 0.50, 0.18, 0.20),
        "labels": ("查看常客",),
    },
    "loading_title": {
        "region": (0.02, 0.52, 0.38, 0.42),
        "labels": ("GLUPY DINER", "格鲁菲餐厅"),
    },
    "loading_progress": {
        "region": (0.88, 0.82, 0.11, 0.17),
        "labels": (),
    },
}

REGULAR_CUSTOMER_NOTES_LABEL_GROUPS = {
    "title": {
        "region": (0.08, 0.00, 0.28, 0.10),
        "labels": ("常客笔记",),
    },
    "rewards": {
        "region": (0.18, 0.08, 0.68, 0.78),
        "labels": ("访问次数", "访问奖励"),
    },
    "claim": {
        "region": (0.80, 0.86, 0.16, 0.11),
        "labels": ("全部获得",),
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


LabelGroupResult = tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, Any] | None,
]


def _match_label_groups(
    grouped_texts: dict[str, list[str]],
    groups: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    for name, config in groups.items():
        actual_texts = grouped_texts[name]
        matches[name] = [
            expected
            for expected in config["labels"]
            if any(_partial_similarity(expected, actual) >= 0.74 for actual in actual_texts)
        ]
    return matches


class LabelRecognitionSession:
    """Run OCR once for one screenshot and reuse its positioned text."""

    def __init__(self, image: Image.Image) -> None:
        self._source = image.convert("RGB")
        self._observations: list[tuple[float, float, str]] | None = None
        self._error: dict[str, Any] | None = None

    def _load(self) -> tuple[list[tuple[float, float, str]], dict[str, Any] | None]:
        if self._observations is not None:
            return self._observations, self._error

        try:
            engine = _ocr_engine()
            result = engine(np.asarray(self._source))
        except (ImportError, ModuleNotFoundError) as exc:
            self._observations = []
            self._error = {"available": False, "error": repr(exc)}
            return self._observations, self._error
        except Exception as exc:  # noqa: BLE001 - recognition failure must be visible to callers.
            self._observations = []
            self._error = {"available": True, "error": repr(exc)}
            return self._observations, self._error

        boxes = result.boxes if result.boxes is not None else ()
        texts = result.txts if result.txts is not None else ()
        scores = result.scores if result.scores is not None else ()
        self._observations = [
            (
                float(np.mean(box[:, 0])) / self._source.width,
                float(np.mean(box[:, 1])) / self._source.height,
                str(text),
            )
            for box, text, score in zip(boxes, texts, scores)
            if float(score) >= 0.55
        ]
        return self._observations, None

    def recognize(self, groups: dict[str, dict[str, Any]]) -> LabelGroupResult:
        observations, error = self._load()
        if error is not None:
            return {}, {}, error

        grouped_texts: dict[str, list[str]] = {name: [] for name in groups}
        for name, config in groups.items():
            x, y, width, height = config["region"]
            for center_x, center_y, text in observations:
                if x <= center_x <= x + width and y <= center_y <= y + height:
                    grouped_texts[name].append(text)
        return grouped_texts, _match_label_groups(grouped_texts, groups), None

    def meaningful_texts(self) -> tuple[list[str], dict[str, Any] | None]:
        """Return OCR text that is substantial enough to represent visible UI copy."""
        observations, error = self._load()
        if error is not None:
            return [], error
        return [
            text
            for _center_x, _center_y, text in observations
            if sum(character.isalnum() for character in text) >= 2
        ], None


def _recognize_label_groups(
    image: Image.Image,
    groups: dict[str, dict[str, Any]],
) -> LabelGroupResult:
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

    return grouped_texts, _match_label_groups(grouped_texts, groups), None


def _recognize_with_session(
    image: Image.Image,
    groups: dict[str, dict[str, Any]],
    session: LabelRecognitionSession | None,
) -> LabelGroupResult:
    if session is not None:
        return session.recognize(groups)
    return _recognize_label_groups(image, groups)


def recognize_home_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize homepage labels only when they appear in their fixed UI regions."""
    grouped_texts, matches, error = _recognize_with_session(image, HOME_LABEL_GROUPS, session)
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


def recognize_gacha_page_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the gacha page from its fixed title and left category labels."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        GACHA_PAGE_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return False, error
    is_gacha_page = bool(matches["title"]) and bool(matches["tabs"])
    return is_gacha_page, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"title": 1, "tabs": 1},
    }


def recognize_gacha_target_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Recognize the selected gacha category from the fixed top-left title."""
    grouped_texts, _matches, error = _recognize_with_session(
        image,
        GACHA_TARGET_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return None, error
    normalized = [_normalize_text(text) for text in grouped_texts["title"]]
    has_costume_title = any("服装抽抽乐" in text for text in normalized)
    has_gear_title = any("装备抽抽乐" in text for text in normalized)
    if has_costume_title and not has_gear_title:
        target = "costume"
    elif has_gear_title and not has_costume_title:
        target = "gear"
    else:
        target = None
    return target, {
        "available": True,
        "texts": grouped_texts,
        "target": target,
        "requirements": {"title": "服装抽抽乐 or 装备抽抽乐"},
    }


def recognize_plaza_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the plaza from its fixed bottom-left chat input label."""
    grouped_texts, _matches, error = _recognize_with_session(
        image,
        PLAZA_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return False, error
    normalized = [_normalize_text(text) for text in grouped_texts["chat_input"]]
    has_chat_input = any("输入聊天内容" in text for text in normalized)
    return has_chat_input, {
        "available": True,
        "texts": grouped_texts,
        "has_chat_input": has_chat_input,
        "requirements": {"chat_input": "输入聊天内容"},
    }


def recognize_all_free_gacha_button(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Recognize the button that is present only while a free draw remains."""
    grouped_texts, matches, error = _recognize_label_groups(
        image,
        ALL_FREE_GACHA_LABEL_GROUPS,
    )
    if error is not None:
        return False, error
    return "所有免费抽抽乐" in matches["button"], {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"button": 1},
    }


def recognize_free_gacha_confirmation_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the all-free gacha confirmation from fixed dialog labels."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        FREE_GACHA_CONFIRM_LABEL_GROUPS,
        session,
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


def recognize_gacha_item_detail_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the item detail opened from a gacha animation or result."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        GACHA_ITEM_DETAIL_LABEL_GROUPS,
        session,
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


def recognize_business_management_state(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[str, dict[str, Any]]:
    """Recognize the management dialog."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        BUSINESS_MANAGEMENT_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return "unknown", error

    supporting_labels = {"助手工作情况", "结算", "回收"} & set(matches["dialog"])
    is_dialog = (
        "取消" in matches["dialog"]
        and "一键获得" in matches["dialog"]
        and bool(supporting_labels)
    )
    state = "business_management_dialog" if is_dialog else "unknown"

    return state, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "state": state,
        "requirements": {
            "dialog": ["取消", "一键获得", "one management label"],
        },
    }


def recognize_restaurant_state(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[str, dict[str, Any]]:
    """Recognize the restaurant loading screen and interactive restaurant home."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        RESTAURANT_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return "unknown", error

    is_home = (
        "格鲁菲餐厅" in matches["title"]
        and len(matches["bottom_controls"]) >= 2
        and "结算" in matches["settlement"]
    )
    is_regular_customer_mode = is_home and "查看常客" in matches["regular_customer_mode"]
    normalized_progress = [
        _normalize_text(text)
        for text in grouped_texts["loading_progress"]
    ]
    has_loading_progress = any(re.fullmatch(r"\d{1,3}", text) for text in normalized_progress)
    is_loading = bool(matches["loading_title"]) and has_loading_progress

    if is_regular_customer_mode:
        state = "restaurant_regular_customer_mode"
    elif is_home:
        state = "restaurant_home"
    elif is_loading:
        state = "restaurant_loading"
    else:
        state = "unknown"
    return state, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "has_loading_progress": has_loading_progress,
        "state": state,
        "requirements": {
            "home": ["格鲁菲餐厅", "two bottom controls", "结算"],
            "loading": ["格鲁菲餐厅", "N%"],
            "regular_customer_mode": ["restaurant home", "查看常客"],
        },
    }


def recognize_regular_customer_notes_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the restaurant regular-customer reward notebook."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        REGULAR_CUSTOMER_NOTES_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return False, error
    is_notes = (
        "常客笔记" in matches["title"]
        and "访问奖励" in matches["rewards"]
        and "全部获得" in matches["claim"]
    )
    return is_notes, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {
            "title": ["常客笔记"],
            "rewards": ["访问奖励"],
            "claim": ["全部获得"],
        },
    }


def recognize_arena_cartridge_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the cartridge collection page from its title and gameplay row."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        ARENA_CARTRIDGE_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return False, error
    title_visible = bool(matches["title"])
    gameplay_match_count = len(matches["gameplay_cards"])
    is_collection = (
        title_visible and gameplay_match_count >= 1
    ) or gameplay_match_count >= 3
    return is_collection, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "ready": title_visible,
        "loading": is_collection and not title_visible,
        "requirements": {
            "ready": {"title": 1, "gameplay_cards": 1},
            "loading": {"gameplay_cards": 3},
        },
    }


def recognize_arena_cartridge_bar_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the in-field cartridge bar from its fixed category labels."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        ARENA_CARTRIDGE_BAR_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return False, error
    gameplay_labels = {
        "玩法游戏卡",
        "战斗玩法游戏卡带",
        "生活玩法游戏卡带",
    }
    is_bar = len(matches["bottom_bar"]) >= 3 and bool(
        gameplay_labels.intersection(matches["bottom_bar"])
    )
    return is_bar, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {
            "bottom_bar": 3,
            "gameplay_card_labels": sorted(gameplay_labels),
        },
    }


def recognize_arena_lobby_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the arena lobby from season text and cocktail capacity."""
    grouped_texts, _matches, error = _recognize_with_session(
        image,
        ARENA_LOBBY_LABEL_GROUPS,
        session,
    )
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


def recognize_arena_battle_prep_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the arena battle preparation page from bottom controls."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        ARENA_BATTLE_PREP_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return False, error
    is_prep = "自动战斗" in matches["bottom_controls"] and "BATTLE" in matches["bottom_controls"]
    return is_prep, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {"bottom_controls": ["自动战斗", "BATTLE"]},
    }


def recognize_arena_auto_battle_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the arena auto-battle dialog from its fixed controls."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        ARENA_AUTO_BATTLE_LABEL_GROUPS,
        session,
    )
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


def recognize_arena_repeat_result_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the completed repeated-battle result dialog."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        ARENA_REPEAT_RESULT_LABEL_GROUPS,
        session,
    )
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


def recognize_arena_victory_result_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the arena victory page shown after closing repeated-battle results."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        ARENA_VICTORY_RESULT_LABEL_GROUPS,
        session,
    )
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


def recognize_arena_rank_change_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize an optional arena promotion or demotion confirmation page."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        ARENA_RANK_CHANGE_LABEL_GROUPS,
        session,
    )
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
    compact_texts = [
        re.sub(r"\s+", "", text)
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
    elif any("PICKUP" in text and "抽抽乐" in text for text in normalized):
        state = "startup_promotion"
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
    elif any(
        re.fullmatch(r"\d{1,3}(?:\.\d+)?%", text)
        for text in compact_texts
    ):
        state = "startup_waiting"
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


def recognize_quick_hunt_map_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the quick-hunt map from stable labels at fixed positions."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        QUICK_HUNT_MAP_LABEL_GROUPS,
        session,
    )
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


def recognize_quick_hunt_setup_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize the quick-hunt setup dialog from its fixed text groups."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        QUICK_HUNT_SETUP_LABEL_GROUPS,
        session,
    )
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


def recognize_reward_overlay_labels(
    image: Image.Image,
    *,
    session: LabelRecognitionSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Recognize any reward overlay from its shared heading and return hint."""
    grouped_texts, matches, error = _recognize_with_session(
        image,
        REWARD_OVERLAY_LABEL_GROUPS,
        session,
    )
    if error is not None:
        return False, error
    is_reward_overlay = (
        "REWARD" in matches["header"]
        and "点击画面即可返回" in matches["footer"]
    )
    return is_reward_overlay, {
        "available": True,
        "texts": grouped_texts,
        "matches": matches,
        "requirements": {
            "header": 1,
            "footer": 1,
        },
    }
