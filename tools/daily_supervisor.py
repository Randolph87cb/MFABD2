"""Supervise daily automation, cleanup, Codex repair, and checkpoint resume."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_plan import FAST_PRESET, get_current_stages  # noqa: E402
from daily_processes import cleanup_after_attempt, snapshot_run_helpers  # noqa: E402
from daily_state import read_state_file  # noqa: E402


SUPERVISOR_MUTEX = r"Local\BrownDust2DailySupervisor"
ERROR_ALREADY_EXISTS = 183
FINISHED_STATUSES = frozenset({"completed", "skipped"})
RETAINED_LOG_GROUPS = ("daily", "daily-check", "supervisor", "recovery")
DATED_DIRECTORY = re.compile(r"^\d{4}(?:-?\d{2}){2}(?:[-_]\d{6})?$")
CURRENT_PHASE_IDS = tuple(stage.id for stage in get_current_stages(FAST_PRESET))


class SupervisorError(RuntimeError):
    pass


class SupervisorMutex:
    def __init__(self, name: str = SUPERVISOR_MUTEX) -> None:
        self.name = name
        self.handle: int | None = None

    def __enter__(self) -> "SupervisorMutex":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise SupervisorError("无法创建每日监督器互斥锁")
        self.handle = int(handle)
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            self.handle = None
            raise SupervisorError("另一个每日监督器仍在运行")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


class SupervisorLogger:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.text_path = run_root / "supervisor.log"
        self.events_path = run_root / "events.jsonl"
        self._lock = threading.Lock()

    def event(self, event: str, message: str, **details: Any) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        payload = {"timestamp": timestamp, "event": event, "message": message, **details}
        line = f"[{timestamp}] [{event}] {message}"
        with self._lock:
            print(line, flush=True)
            with self.text_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def child_line(self, source: str, channel: str, line: str) -> None:
        clean = line.rstrip("\r\n")
        if not clean:
            return
        rendered = f"[{source}:{channel}] {clean}"
        with self._lock:
            print(rendered, flush=True)
            with self.text_path.open("a", encoding="utf-8") as stream:
                stream.write(rendered + "\n")


@dataclass
class CommandResult:
    returncode: int
    stdout: list[str]
    stderr: list[str]


@dataclass
class CodexTurn:
    ok: bool
    thread_id: str | None
    returncode: int
    error: str | None


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    logger: SupervisorLogger,
    source: str,
    stdin_text: str | None = None,
) -> CommandResult:
    """Run a child process while preserving stdout and stderr separately."""
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    stdout: list[str] = []
    stderr: list[str] = []

    def pump(stream: Any, destination: list[str], channel: str) -> None:
        for line in iter(stream.readline, ""):
            destination.append(line.rstrip("\r\n"))
            logger.child_line(source, channel, line)
        stream.close()

    threads = [
        threading.Thread(target=pump, args=(process.stdout, stdout, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    if stdin_text is not None and process.stdin is not None:
        process.stdin.write(stdin_text)
        process.stdin.close()
    returncode = process.wait()
    for thread in threads:
        thread.join()
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


def _latest_mtime(path: Path) -> float:
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def prune_old_logs(
    project_root: Path,
    *,
    retention_days: int = 7,
    now: datetime | None = None,
    active_paths: Iterable[Path] = (),
) -> list[Path]:
    """Remove dated run directories older than the retention window."""
    logs_root = (project_root / "logs").resolve(strict=False)
    cutoff = (now or datetime.now().astimezone()).timestamp() - timedelta(
        days=retention_days
    ).total_seconds()
    protected = {path.resolve(strict=False) for path in active_paths}
    removed: list[Path] = []
    for group_name in RETAINED_LOG_GROUPS:
        group = logs_root / group_name
        if not group.is_dir():
            continue
        for child in group.iterdir():
            if not child.is_dir() or child.is_symlink() or not DATED_DIRECTORY.match(child.name):
                continue
            resolved = child.resolve(strict=False)
            if resolved in protected or any(parent in protected for parent in resolved.parents):
                continue
            if not resolved.is_relative_to(logs_root):
                continue
            try:
                if _latest_mtime(resolved) >= cutoff:
                    continue
                shutil.rmtree(resolved)
                removed.append(resolved)
            except OSError:
                continue
    return removed


def completion_report(project_root: Path) -> dict[str, Any]:
    state_path = project_root / "state" / "daily_automation.json"
    try:
        state = read_state_file(state_path)
    except ValueError as exc:
        return {"complete": False, "error": str(exc), "state_path": str(state_path)}
    phases = state.get("phases") if isinstance(state, dict) else None
    if not isinstance(phases, dict):
        return {
            "complete": False,
            "error": "每日状态文件不存在或缺少 phases",
            "state_path": str(state_path),
        }
    statuses = {
        phase_id: (phases.get(phase_id) or {}).get("status", "missing")
        for phase_id in CURRENT_PHASE_IDS
    }
    incomplete = [
        phase_id for phase_id, status in statuses.items() if status not in FINISHED_STATUSES
    ]
    return {
        "complete": not incomplete,
        "game_day": state.get("game_day"),
        "run_status": state.get("status"),
        "statuses": statuses,
        "incomplete": incomplete,
        "failed_phase": incomplete[0] if incomplete else None,
        "state_path": str(state_path),
    }


def latest_summary(project_root: Path) -> Path | None:
    root = project_root / "logs" / "daily"
    if not root.is_dir():
        return None
    candidates = list(root.glob("*/*/summary.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def git_status(project_root: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip(), result.stdout.strip()


def reference_is_ready(reference_root: Path) -> tuple[bool, str]:
    if not (reference_root / ".git").exists():
        return False, f"参考仓库不存在：{reference_root}"
    clean, status = git_status(reference_root)
    if not clean:
        return False, f"参考仓库不是干净只读副本：{status or 'git status 失败'}"
    return True, ""


def _parse_codex_events(lines: Sequence[str]) -> tuple[str | None, bool, str | None]:
    thread_id: str | None = None
    completed = False
    error: str | None = None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        elif event.get("type") == "turn.completed":
            completed = True
        elif event.get("type") == "turn.failed":
            error = str(event.get("error") or "Codex turn failed")
    return thread_id, completed, error


def run_codex_turn(
    *,
    codex_path: Path,
    project_root: Path,
    logger: SupervisorLogger,
    prompt: str,
    thread_id: str | None = None,
    label: str = "codex",
) -> CodexTurn:
    if thread_id:
        command = [str(codex_path), "exec", "resume", "--json", thread_id, "-"]
    else:
        command = [
            str(codex_path),
            "exec",
            "--json",
            "--approve-for-me",
            "-C",
            str(project_root),
            "-",
        ]
    result = run_command(
        command,
        cwd=project_root,
        logger=logger,
        source=label,
        stdin_text=prompt,
    )
    event_thread_id, completed, error = _parse_codex_events(result.stdout)
    resolved_thread_id = event_thread_id or thread_id
    ok = result.returncode == 0 and completed and error is None and bool(resolved_thread_id)
    if not ok and error is None:
        error = f"Codex 退出码 {result.returncode}，未收到 turn.completed"
    return CodexTurn(
        ok=ok,
        thread_id=resolved_thread_id,
        returncode=result.returncode,
        error=error,
    )


def repair_prompt(
    *,
    project_root: Path,
    reference_root: Path,
    report: dict[str, Any],
    summary_path: Path | None,
    supervisor_log: Path,
    round_number: int,
) -> str:
    return f"""你正在处理棕尘2每日自动化的第 {round_number} 轮无人值守修复。

工作目录：{project_root}
监督日志：{supervisor_log}
本轮 summary：{summary_path or '没有生成 summary.json，请从监督日志和状态文件排查'}
状态文件：{report.get('state_path')}
当前阶段状态：{json.dumps(report.get('statuses', {}), ensure_ascii=False)}
首个未完成阶段：{report.get('failed_phase')}
只读参考仓库：{reference_root}

要求：
1. 先查看日志、状态和相关本地代码，定位真实失败原因并做最小修复。
2. 可以查询只读参考仓库，主要参考它的 Android 业务流程语义；实现必须沿用当前项目的 Python、桌面窗口和状态机方式，禁止照搬 Android 架构。
3. 禁止修改参考仓库，禁止启动游戏或执行真实每日流程；只运行离线测试、静态检查和 --show-plan 等无副作用验证。
4. 保持网络无限等待行为不变。不要引入模糊进程终止；只能处理精确识别的本次运行进程。
5. 正常续跑入口会自动执行 failed/running/pending 阶段，因此不要把自动恢复改成 --force-phase。
6. 完成离线验证后，只提交与本轮自动修复直接相关的代码和测试，使用中文提交信息并推送当前 main 分支；不要提交 logs、state、.external 或其他本地运行产物。清楚说明改动、验证和提交推送结果。
"""


def followup_prompt(
    *,
    report: dict[str, Any],
    summary_path: Path | None,
    supervisor_log: Path,
    round_number: int,
) -> str:
    return f"""第 {round_number} 次真实续跑仍未完成，请继续同一线程修复。
监督日志：{supervisor_log}
最新 summary：{summary_path or '无'}
当前阶段状态：{json.dumps(report.get('statuses', {}), ensure_ascii=False)}
首个未完成阶段：{report.get('failed_phase')}
继续遵守上一轮约束：先读新日志；只做最小修复；不得启动游戏；不得修改参考仓库。完成离线验证后，按项目规则提交并推送本轮相关改动，不要提交本地运行产物。
"""


def finalize_prompt(*, supervisor_log: Path, summary_path: Path | None) -> str:
    return f"""真实每日流程现已从失败阶段续跑到最后并全部完成。
监督日志：{supervisor_log}
最终 summary：{summary_path or '无'}
请做最终只读核对，确认没有修改只读参考仓库，并查看主仓库 git status 与本次修复的提交、推送状态。若本次修复仍有未提交的代码或测试改动，按项目规则提交并推送当前 main 分支；不要把 logs、state、.external 或其他本地运行产物加入提交。完成后报告核对结果。
"""


def run_automation_attempt(
    *,
    python_path: Path,
    project_root: Path,
    logger: SupervisorLogger,
    force: bool = False,
    force_phase: str | None = None,
    attempt: int,
) -> tuple[CommandResult, dict[str, Any]]:
    command = [
        str(python_path),
        str(project_root / "tools" / "daily_automation.py"),
        "--scheduled",
        "--project-root",
        str(project_root),
    ]
    if force:
        command.append("--force")
    if force_phase:
        command.extend(("--force-phase", force_phase))
    starter_before = snapshot_run_helpers()
    try:
        result = run_command(
            command,
            cwd=project_root,
            logger=logger,
            source=f"automation-{attempt}",
        )
    finally:
        cleanup = cleanup_after_attempt(starter_before)
        logger.event("cleanup", "本轮游戏与辅助进程收尾完成", attempt=attempt, **cleanup)
    return result, cleanup


def supervise(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    stamp = datetime.now().astimezone()
    run_root = project_root / "logs" / "supervisor" / stamp.strftime("%Y-%m-%d") / stamp.strftime("%H%M%S")
    logger = SupervisorLogger(run_root)
    removed = prune_old_logs(
        project_root,
        retention_days=args.retention_days,
        active_paths=(run_root, run_root.parent),
    )
    logger.event("retention", "已清理过期日志", retention_days=args.retention_days, removed=[str(path) for path in removed])

    reference_root = project_root / ".external" / "MFABD2-reference"
    if args.check:
        clean, status = git_status(project_root)
        reference_ready, reference_error = reference_is_ready(reference_root)
        logger.event(
            "check",
            "监督器检查完成",
            python=str(args.python_path),
            codex=str(args.codex_path),
            current_phases=list(CURRENT_PHASE_IDS),
            project_clean=clean,
            project_status=status,
            reference_ready=reference_ready,
            reference_error=reference_error,
        )
        return 0 if args.python_path.is_file() and args.codex_path.is_file() and reference_ready else 2

    project_was_clean, initial_status = git_status(project_root)
    thread_id: str | None = None
    latest_report: dict[str, Any] = {}
    latest_result: CommandResult | None = None
    latest_cleanup: dict[str, Any] = {"ok": False}

    for attempt in range(args.max_repair_rounds + 1):
        latest_result, latest_cleanup = run_automation_attempt(
            python_path=args.python_path,
            project_root=project_root,
            logger=logger,
            force=args.force if attempt == 0 else False,
            force_phase=args.force_phase if attempt == 0 else None,
            attempt=attempt + 1,
        )
        latest_report = completion_report(project_root)
        summary_path = latest_summary(project_root)
        logger.event(
            "attempt_result",
            "每日流程本轮执行结束",
            attempt=attempt + 1,
            exit_code=latest_result.returncode,
            completion=latest_report,
            cleanup_ok=latest_cleanup.get("ok", False),
            summary=str(summary_path) if summary_path else None,
        )
        if latest_result.returncode == 0 and latest_report.get("complete") and latest_cleanup.get("ok"):
            if thread_id:
                final_turn = run_codex_turn(
                    codex_path=args.codex_path,
                    project_root=project_root,
                    logger=logger,
                    prompt=finalize_prompt(supervisor_log=logger.text_path, summary_path=summary_path),
                    thread_id=thread_id,
                    label="codex-finalize",
                )
                logger.event("codex_finalize", "Codex 最终复核结束", **asdict(final_turn))
                if not final_turn.ok:
                    return 2
            logger.event("success", "当前可运行的全部每日流程均已完成")
            return 0

        if attempt >= args.max_repair_rounds:
            break
        if thread_id is None:
            project_is_clean_now, current_status = git_status(project_root)
            if not project_was_clean or not project_is_clean_now:
                logger.event(
                    "repair_blocked",
                    "主仓库在自动修复前已有改动，为避免覆盖用户工作，未启动 Codex",
                    initial_git_status=initial_status,
                    current_git_status=current_status,
                )
                return 2
        reference_ready, reference_error = reference_is_ready(reference_root)
        if not reference_ready:
            logger.event("repair_blocked", reference_error)
            return 2

        round_number = attempt + 1
        prompt = (
            repair_prompt(
                project_root=project_root,
                reference_root=reference_root,
                report=latest_report,
                summary_path=summary_path,
                supervisor_log=logger.text_path,
                round_number=round_number,
            )
            if thread_id is None
            else followup_prompt(
                report=latest_report,
                summary_path=summary_path,
                supervisor_log=logger.text_path,
                round_number=round_number,
            )
        )
        turn = run_codex_turn(
            codex_path=args.codex_path,
            project_root=project_root,
            logger=logger,
            prompt=prompt,
            thread_id=thread_id,
            label=f"codex-repair-{round_number}",
        )
        thread_id = turn.thread_id
        logger.event("codex_repair", "Codex 修复轮次结束", round=round_number, **asdict(turn))
        if not turn.ok:
            return 2
        reference_ready, reference_error = reference_is_ready(reference_root)
        if not reference_ready:
            logger.event("repair_blocked", reference_error)
            return 2

    logger.event(
        "failed",
        "达到自动修复上限后，仍有流程未完成",
        max_repair_rounds=args.max_repair_rounds,
        completion=latest_report,
        automation_exit_code=latest_result.returncode if latest_result else None,
        cleanup=latest_cleanup,
        codex_thread_id=thread_id,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--python-path", type=Path, default=Path(sys.executable))
    parser.add_argument("--codex-path", type=Path, required=True)
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-phase", choices=CURRENT_PHASE_IDS)
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.force and args.force_phase:
        raise SystemExit("--force and --force-phase cannot be used together")
    if args.retention_days < 1 or args.max_repair_rounds < 0:
        raise SystemExit("retention-days must be >= 1 and max-repair-rounds must be >= 0")
    try:
        with SupervisorMutex():
            result = supervise(args)
    except SupervisorError as exc:
        print(f"[每日监督器失败] {exc}", file=sys.stderr, flush=True)
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
