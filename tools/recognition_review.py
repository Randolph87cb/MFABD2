"""Local web UI for reviewing recognition fixtures and failed-run screenshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image

from free_gacha import STATE_NAMES, classify_state, detect_selected_gacha_target
from quick_hunt import detect_selected_quick_hunt_category, is_quick_hunt_count_at_max


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAGE_PATH = Path(__file__).resolve().with_name("recognition_review.html")
ANNOTATION_FILENAME = "recognition_feedback.json"
MAX_FAILED_RUNS = 5
MAX_SCREENSHOTS_PER_RUN = 3
STATE_LABELS = {
    **STATE_NAMES,
    "arena_repeat_battle_result": "竞技场连续战斗结果",
}
GACHA_TARGET_LABELS = {
    "costume": "人物抽卡",
    "gear": "装备抽卡",
}
QUICK_HUNT_CATEGORY_LABELS = {
    "hunting_ground": "狩猎场",
    "gold": "哥布林遗迹",
    "slime": "史莱姆王国",
    "crystal_cave": "圣石洞穴",
}
STAGE_LABELS = {
    "daily": "每日任务",
    "network": "网络检查",
    "enter_game": "进入游戏",
    "prepare_home": "返回主页",
    "free_gacha": "免费抽卡",
    "return_home": "抽卡后返回主页",
    "quick_hunt_entry": "进入快速狩猎",
    "hunting_ground_setup": "设置狩猎场",
    "hunting_ground_confirm": "执行狩猎场",
    "crystal_cave_cycle": "执行圣石洞穴",
    "daily_arena": "每日竞技场",
    "business_management_home": "经营管理前返回主页",
    "business_management": "经营管理收益",
}
ACTION_LABELS = {
    "quick-hunt entry": "进入快速狩猎",
    "arena cartridge selection": "选择竞技场卡带",
    "open game-card collection from restaurant": "从餐厅打开卡带栏",
    "enter battlefield from home": "从主页进入战场",
    "return home from restaurant": "从餐厅返回主页",
    "MAX": "点击最大次数",
}


def _state_label(state: str | None) -> str | None:
    if not state:
        return None
    return STATE_LABELS.get(state, "其他界面")


def _failure_reason_label(reason: str) -> str:
    """Turn common internal failure messages into concise Chinese review copy."""
    raw = reason.strip()
    stage_label = "任务"
    detail = raw
    if ":" in raw:
        stage, candidate = raw.split(":", 1)
        if stage in STAGE_LABELS:
            stage_label = STAGE_LABELS[stage]
            detail = candidate.strip()

    match = re.fullmatch(r"(.+) requires ([a-z0-9_]+), got ([a-z0-9_]+)", detail)
    if match:
        action, expected, actual = match.groups()
        return (
            f"{stage_label}失败：{ACTION_LABELS.get(action, stage_label)}前需要处于"
            f"{_state_label(expected)}，实际识别为{_state_label(actual)}。"
        )

    match = re.fullmatch(r"unsupported state after (.+): ([a-z0-9_]+)", detail)
    if match:
        action, actual = match.groups()
        return (
            f"{stage_label}失败：{ACTION_LABELS.get(action, '上一步操作')}后出现了"
            f"尚未支持的{_state_label(actual)}。"
        )

    match = re.fullmatch(
        r"(.+) changed from ([a-z0-9_]+) to unexpected state ([a-z0-9_]+); retry cancelled",
        detail,
    )
    if match:
        action, before, after = match.groups()
        return (
            f"{stage_label}失败：{ACTION_LABELS.get(action, '操作')}时，界面从"
            f"{_state_label(before)}变成了非预期的{_state_label(after)}，已停止重试。"
        )

    match = re.fullmatch(
        r"(.+) changed to unexpected state ([a-z0-9_]+); retry cancelled",
        detail,
    )
    if match:
        action, after = match.groups()
        return (
            f"{stage_label}失败：{ACTION_LABELS.get(action, '操作')}后出现了非预期的"
            f"{_state_label(after)}，已停止重试。"
        )

    match = re.fullmatch(r"(.+) did not take effect after (\d+) clicks", detail)
    if match:
        action, count = match.groups()
        return f"{stage_label}失败：{ACTION_LABELS.get(action, '操作')}点击 {count} 次后仍未生效。"

    match = re.fullmatch(r"unknown or unsupported state: ([a-z0-9_]+)", detail)
    if match:
        return f"{stage_label}失败：当前界面是{_state_label(match.group(1))}，暂时无法继续。"

    match = re.fullmatch(r"(.+) timed out after (\d+(?:\.\d+)?) seconds", detail)
    if match:
        action, seconds = match.groups()
        return f"{stage_label}失败：{ACTION_LABELS.get(action, '当前操作')}等待 {seconds} 秒后超时。"

    return f"{stage_label}失败：暂时没有更具体的中文说明，请结合截图标注。"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _stable_id(source: str, path: Path) -> str:
    digest = hashlib.sha1(f"{source}|{path}".encode("utf-8")).hexdigest()[:16]
    return f"{source}-{digest}"


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


@dataclass(frozen=True)
class ReviewItem:
    id: str
    source: str
    path: Path
    name: str
    group: str
    expected_state: str | None = None
    recorded_state: str | None = None
    reason: str = ""
    run_started_at: str | None = None
    captured_at: str | None = None
    stage_label: str | None = None
    sequence_index: int | None = None
    sequence_total: int | None = None
    expected_target: str | None = None
    expected_quick_hunt_category: str | None = None
    expected_quick_hunt_max: bool | None = None

    def as_json(self, root: Path, annotation: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "name": self.name,
            "group": self.group,
            "path": _display_path(self.path, root),
            "image_url": f"/api/image?id={self.id}",
            "expected_state": self.expected_state,
            "expected_state_label": _state_label(self.expected_state),
            "recorded_state": self.recorded_state,
            "recorded_state_label": _state_label(self.recorded_state),
            "reason": self.reason,
            "run_started_at": self.run_started_at,
            "captured_at": self.captured_at,
            "stage_label": self.stage_label,
            "sequence_index": self.sequence_index,
            "sequence_total": self.sequence_total,
            "expected_target": self.expected_target,
            "expected_quick_hunt_category": self.expected_quick_hunt_category,
            "expected_quick_hunt_max": self.expected_quick_hunt_max,
            "annotation": annotation,
        }


class ReviewStore:
    def __init__(self, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve()
        self.manifest_path = self.project_root / "tests" / "recognition_cases.json"
        self.logs_root = self.project_root / "logs" / "daily"
        self.annotation_path = self.project_root / "state" / ANNOTATION_FILENAME
        self._lock = threading.Lock()

    def _test_items(self) -> list[ReviewItem]:
        cases = _read_json(self.manifest_path, [])
        items: list[ReviewItem] = []
        for case in cases if isinstance(cases, list) else []:
            raw_path = case.get("path")
            if not raw_path:
                continue
            path = (self.manifest_path.parent / raw_path).resolve()
            items.append(
                ReviewItem(
                    id=_stable_id("test", path),
                    source="test",
                    path=path,
                    name=path.name,
                    group="测试截图",
                    expected_state=case.get("expected"),
                    reason=str(case.get("reason", "")),
                    expected_target=case.get("expected_target"),
                    expected_quick_hunt_category=case.get(
                        "expected_quick_hunt_category"
                    ),
                    expected_quick_hunt_max=case.get("expected_quick_hunt_max"),
                )
            )
        return items

    @staticmethod
    def _states_by_screenshot(run_root: Path) -> dict[Path, str]:
        states: dict[Path, str] = {}
        for event_path in run_root.rglob("events.jsonl"):
            try:
                lines = event_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                screenshot = event.get("screenshot")
                state = event.get("state")
                if screenshot and state:
                    states[Path(screenshot).resolve()] = str(state)
        return states

    @staticmethod
    def _failure_event(run_root: Path) -> dict[str, Any]:
        events_path = run_root / "events.jsonl"
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = ()
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(event, dict)
                and event.get("status") == "error"
                and event.get("stage") != "daily"
            ):
                events.append(event)
        return events[-1] if events else {}

    @staticmethod
    def _failure_screenshot_root(run_root: Path, event: dict[str, Any]) -> Path:
        raw_root = event.get("log_root")
        if not raw_root:
            return run_root
        candidate = Path(str(raw_root)).resolve()
        try:
            candidate.relative_to(run_root)
        except ValueError:
            return run_root
        return candidate if candidate.is_dir() else run_root

    def _daily_items(self) -> list[ReviewItem]:
        summaries = sorted(
            self.logs_root.rglob("summary.json") if self.logs_root.is_dir() else (),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        items: list[ReviewItem] = []
        failed_runs = 0
        for summary_path in summaries:
            summary = _read_json(summary_path, {})
            if not isinstance(summary, dict) or summary.get("result") == "success":
                continue
            failed_runs += 1
            if failed_runs > MAX_FAILED_RUNS:
                break
            run_root = summary_path.parent.resolve()
            states = self._states_by_screenshot(run_root)
            started_at = str(summary.get("started_at", ""))
            group_time = started_at.replace("T", " ")[:19] or run_root.name
            failure_event = self._failure_event(run_root)
            stage = str(failure_event.get("stage", "daily"))
            stage_label = STAGE_LABELS.get(stage, "未命名环节")
            reason_source = str(summary.get("reason", ""))
            if stage in STAGE_LABELS and not reason_source.startswith(f"{stage}:"):
                reason_source = f"{stage}: {reason_source}"
            reason = _failure_reason_label(reason_source)
            screenshot_root = self._failure_screenshot_root(run_root, failure_event)
            screenshots = sorted(
                screenshot_root.rglob("*.png"),
                key=lambda item: item.stat().st_mtime,
            )[-MAX_SCREENSHOTS_PER_RUN:]
            for index, path in enumerate(screenshots, start=1):
                captured_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
                items.append(
                    ReviewItem(
                        id=_stable_id("daily", path.resolve()),
                        source="daily",
                        path=path.resolve(),
                        name=path.name,
                        group=f"{group_time} · {stage_label}",
                        recorded_state=states.get(path.resolve()),
                        reason=reason,
                        run_started_at=started_at or None,
                        captured_at=captured_at.isoformat(timespec="seconds"),
                        stage_label=stage_label,
                        sequence_index=index,
                        sequence_total=len(screenshots),
                    )
                )
        return items

    def items(self) -> list[ReviewItem]:
        return self._test_items() + self._daily_items()

    def item_map(self) -> dict[str, ReviewItem]:
        return {item.id: item for item in self.items()}

    def annotations(self) -> dict[str, dict[str, Any]]:
        payload = _read_json(self.annotation_path, {})
        raw_items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(raw_items, list):
            return {}
        return {
            str(item["id"]): item
            for item in raw_items
            if isinstance(item, dict) and item.get("id")
        }

    def catalog(self) -> dict[str, Any]:
        annotations = self.annotations()
        items = self.items()
        known_states = sorted(
            {
                state
                for item in items
                for state in (item.expected_state, item.recorded_state)
                if state
            }
            | {
                str(annotation.get("correct_state"))
                for annotation in annotations.values()
                if annotation.get("correct_state")
            }
        )
        return {
            "items": [
                item.as_json(self.project_root, annotations.get(item.id))
                for item in items
                if item.path.is_file()
            ],
            "known_states": known_states,
            "state_labels": {
                state: _state_label(state)
                for state in known_states
            },
            "annotation_count": len(annotations),
            "annotation_file": _display_path(self.annotation_path, self.project_root),
            "daily_limits": {
                "failed_runs": MAX_FAILED_RUNS,
                "screenshots_per_run": MAX_SCREENSHOTS_PER_RUN,
            },
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def save_annotation(
        self,
        item_id: str,
        correct_state: str,
        note: str,
    ) -> dict[str, Any]:
        item = self.item_map().get(item_id)
        if item is None or not item.path.is_file():
            raise KeyError("找不到这张截图，请刷新列表")
        correct_state = correct_state.strip()
        note = note.strip()
        if not correct_state and not note:
            raise ValueError("请填写正确界面或批注")

        with self._lock:
            annotations = self.annotations()
            annotation = {
                "id": item.id,
                "source": item.source,
                "image_path": _display_path(item.path, self.project_root),
                "name": item.name,
                "group": item.group,
                "expected_state": item.expected_state,
                "expected_state_label": _state_label(item.expected_state),
                "recorded_state": item.recorded_state,
                "recorded_state_label": _state_label(item.recorded_state),
                "correct_state": correct_state,
                "correct_state_label": _state_label(correct_state),
                "note": note,
                "run_started_at": item.run_started_at,
                "failure_reason": item.reason if item.source == "daily" else "",
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            annotations[item.id] = annotation
            self._write_annotations(annotations)
        return annotation

    def delete_annotation(self, item_id: str) -> bool:
        with self._lock:
            annotations = self.annotations()
            removed = annotations.pop(item_id, None) is not None
            if removed:
                self._write_annotations(annotations)
        return removed

    def _write_annotations(self, annotations: dict[str, dict[str, Any]]) -> None:
        self.annotation_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "items": sorted(annotations.values(), key=lambda item: item["updated_at"]),
        }
        temporary = self.annotation_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.annotation_path)

    def classify(self, item_id: str) -> dict[str, Any]:
        item = self.item_map().get(item_id)
        if item is None or not item.path.is_file():
            raise KeyError("找不到这张截图，请刷新列表")
        with Image.open(item.path) as image:
            state, _details = classify_state(image)
            target = detect_selected_gacha_target(image)
            quick_hunt_category = detect_selected_quick_hunt_category(image)[0]
            quick_hunt_max = is_quick_hunt_count_at_max(image)[0]
        return {
            "state": state,
            "state_label": _state_label(state),
            "target": target,
            "target_label": GACHA_TARGET_LABELS.get(target) if target else None,
            "quick_hunt_category": quick_hunt_category,
            "quick_hunt_category_label": (
                QUICK_HUNT_CATEGORY_LABELS.get(quick_hunt_category)
                if quick_hunt_category
                else None
            ),
            "quick_hunt_max": quick_hunt_max,
        }


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server: "ReviewServer"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[网页] {self.address_string()} {format % args}")

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: HTTPStatus) -> None:
        self._json({"error": message}, status)

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求内容不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是对象")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_page()
            return
        if parsed.path == "/api/catalog":
            self._json(self.server.store.catalog())
            return
        if parsed.path == "/api/image":
            item_id = parse_qs(parsed.query).get("id", [""])[0]
            self._serve_image(item_id)
            return
        if parsed.path == "/health":
            self._json({"status": "ok"})
            return
        self._error("页面不存在", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/classify":
            self._error("接口不存在", HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_body()
            self._json(self.server.store.classify(str(payload.get("id", ""))))
        except KeyError as exc:
            self._error(str(exc.args[0]), HTTPStatus.NOT_FOUND)
        except (ValueError, OSError) as exc:
            self._error(str(exc), HTTPStatus.BAD_REQUEST)

    def do_PUT(self) -> None:
        if urlparse(self.path).path != "/api/annotation":
            self._error("接口不存在", HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_body()
            annotation = self.server.store.save_annotation(
                str(payload.get("id", "")),
                str(payload.get("correct_state", "")),
                str(payload.get("note", "")),
            )
            self._json({"annotation": annotation})
        except KeyError as exc:
            self._error(str(exc.args[0]), HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._error(str(exc), HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/annotation":
            self._error("接口不存在", HTTPStatus.NOT_FOUND)
            return
        item_id = parse_qs(parsed.query).get("id", [""])[0]
        if not item_id:
            self._error("缺少截图编号", HTTPStatus.BAD_REQUEST)
            return
        self._json({"removed": self.server.store.delete_annotation(item_id)})

    def _serve_page(self) -> None:
        try:
            body = PAGE_PATH.read_bytes()
        except OSError:
            self._error("找不到网页文件", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_image(self, item_id: str) -> None:
        item = self.server.store.item_map().get(item_id)
        if item is None or not item.path.is_file():
            self._error("找不到图片", HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(item.path.name)[0] or "application/octet-stream"
        body = item.path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=60")
        self.end_headers()
        self.wfile.write(body)


class ReviewServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: ReviewStore) -> None:
        super().__init__(address, ReviewRequestHandler)
        self.store = store


def main() -> None:
    parser = argparse.ArgumentParser(description="打开游戏界面识别标注工具")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = ReviewServer((args.host, args.port), ReviewStore())
    url = f"http://{args.host}:{server.server_port}/"
    print(f"界面标注工具已启动：{url}")
    print(f"标注文件：{server.store.annotation_path}")
    print("关闭此窗口即可停止工具。")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n界面标注工具已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
