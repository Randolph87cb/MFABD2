"""Parse maintenance notices and pass a deferred login to the supervisor."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any


MAINTENANCE_EXIT_CODE = 75
DATE_PATTERN = re.compile(
    r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日|(\d{4})-(\d{1,2})-(\d{1,2})"
)
TIME_PATTERN = re.compile(r"(上午|下午|晚上|凌晨|中午)?(\d{1,2}):(\d{2})")


def maintenance_notice(texts: list[str], *, now: datetime | None = None) -> dict[str, Any] | None:
    """Require maintenance context; ordinary resource downloads are not maintenance."""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", " ".join(texts)))
    text = text.translate(str.maketrans({
        "維": "维", "護": "护", "時": "时", "間": "间",
        "無": "无", "將": "将", "結": "结",
    }))
    if "维护" not in text or not any(
        marker in text for marker in ("维护中", "维护时间", "更新时间", "无法登入", "无法登录", "维护结束")
    ):
        return None
    current = now or datetime.now().astimezone()
    end = parse_maintenance_end(text, now=current)
    target = end + timedelta(hours=1) if end else None
    fallback = target is None or target <= current
    retry_at = current + timedelta(hours=1) if fallback else target
    return {
        "notice_text": text,
        "maintenance_end": end.isoformat(timespec="seconds") if end else None,
        "retry_at": retry_at.isoformat(timespec="seconds"),
        "fallback": "missing_or_elapsed_end_time" if fallback else None,
    }


def parse_maintenance_end(text: str, *, now: datetime) -> datetime | None:
    """Read the date and end of a Chinese maintenance time range."""
    marker = re.search(r"(?:更新|维护)时间[:：]?", text)
    if marker is None:
        return None
    window = re.split(r"共|维护影响|维护内容", text[marker.end():], maxsplit=1)[0]
    times = list(TIME_PATTERN.finditer(window))
    if len(times) != 2:
        return None
    parsed: list[datetime] = []
    inherited_period = None
    try:
        for match in times:
            period, hour_text, minute_text = match.groups()
            hour, minute = int(hour_text), int(minute_text)
            if hour > 23 or minute > 59:
                return None
            inherited_period = period or inherited_period
            if inherited_period in {"下午", "晚上", "中午"} and hour < 12:
                hour += 12
            elif inherited_period in {"上午", "凌晨"} and hour == 12:
                hour = 0
            dates = list(DATE_PATTERN.finditer(window[:match.start()]))
            if dates:
                year, month, day, iso_year, iso_month, iso_day = dates[-1].groups()
                date = now.replace(
                    year=int(year or iso_year or now.year),
                    month=int(month or iso_month), day=int(day or iso_day),
                )
            else:
                date = parsed[0] if parsed else now
            parsed.append(date.replace(hour=hour, minute=minute, second=0, microsecond=0))
        start, end = parsed
        if end <= start:
            end += timedelta(days=1)
        return end if end - start <= timedelta(days=1) else None
    except ValueError:
        return None


class MaintenanceDeferred(RuntimeError):
    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details
        super().__init__(f"游戏正在维护，延后至 {details['retry_at']} 再登录")
