"""Supervise daily automation, recovery, and one improvement per game day."""

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
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.request import Request, urlopen


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_plan import FAST_PRESET, get_current_stages, get_daily_plan  # noqa: E402
from daily_processes import cleanup_after_attempt, snapshot_run_helpers  # noqa: E402
from daily_state import read_state_file, write_state_atomic  # noqa: E402
from maintenance import MAINTENANCE_EXIT_CODE  # noqa: E402


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


def notify_success(
    project_root: Path,
    *,
    report: dict[str, Any],
    logger: SupervisorLogger,
) -> bool:
    """Send to the daily task's independent ntfy topic from local configuration."""
    config_path = project_root / "state" / "ntfy.config.json"
    statuses = report.get("statuses", {})
    completed = sum(status == "completed" for status in statuses.values())
    skipped = sum(status == "skipped" for status in statuses.values())
    message = (
        "棕尘2每日任务正常完成。\n"
        f"游戏日：{report.get('game_day', '未知')}\n"
        f"当前可运行阶段：完成 {completed} 项，跳过 {skipped} 项。\n"
        "游戏与本轮辅助进程已收尾。"
    )
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        ntfy_url = str(config.get("ntfyUrl") or "").strip()
        ntfy_topic = str(config.get("ntfyTopic") or "").strip()
        if not ntfy_url or not ntfy_topic:
            raise ValueError("state/ntfy.config.json 必须设置 ntfyUrl 和 ntfyTopic")
        payload = {
            "topic": ntfy_topic,
            "title": "棕尘2每日任务",
            "message": message,
            "priority": 4,
            "tags": ["computer"],
        }
        request = Request(
            ntfy_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "BrownDust2DailyAutomation/1.0",
            },
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            result = json.load(response)
        if result.get("event") != "message":
            raise SupervisorError("ntfy 未确认通知发送成功")
    except (OSError, ValueError, SupervisorError) as exc:
        logger.event("notification_failed", "每日流程已完成，但完成通知发送失败", error=str(exc))
        return False
    logger.event("notification_sent", "每日完成通知已发送到专用 ntfy topic", notification_id=result.get("id"))
    return True


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
    # Explicit for new and resumed turns: --approve-for-me overrides the user's
    # full-access config with workspace-write and automatic approval review.
    command = [
        str(codex_path),
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    if thread_id:
        command.extend(("resume", thread_id, "-"))
    else:
        command.extend(("-C", str(project_root), "-"))
    result = run_command(
        command,
        cwd=project_root,
        logger=logger,
        source=label,
        stdin_text=local_control_prompt(project_root) + "\n\n" + prompt,
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


def local_control_prompt(project_root: Path) -> str:
    return f"""本项目的 CLI 线程已获用户授权以完全访问运行。
游戏控制采用项目本地 Python/Win32 脚本，不依赖桌面端 Computer Use、cua_repl 或 node_repl。
先读 {project_root / 'docs' / 'cli-game-control.md'}。
使用 .venv/Scripts/python.exe tools/game_control.py status 检查本地控制入口；
需要且获本轮任务允许的 PC 补证，使用该脚本的 open、capture、click、swipe、key、close 子命令。
先用 capture 取得并查看原生 client.png，再用同次 observation.json 执行一个动作，查看返回的新截图。
游戏刚启动可能出现初始化白屏；不要输入，等待渲染并有限次重试只读 capture，不能把首帧空白当作权限拒绝。
该本地入口是用户明确选择的游戏控制方式；即使插件显示 Native computer APIs are disabled，
或没有独立 node_repl，也不能据此认定本项目无法操作游戏而放弃开发。
应实际检查本地脚本；若失败，记录命令、退出码和真实 Windows/游戏错误。
完全访问不扩大业务授权：是否允许启动游戏、消耗资源或修改文件，仍以本轮具体要求为准；
修复线程的“禁止启动游戏”以及开发线程的证据门、免费动作和今日去重规则继续有效。"""


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


def improvement_prompt(
    *,
    project_root: Path,
    reference_root: Path,
    report: dict[str, Any],
    summary_path: Path | None,
    supervisor_log: Path,
    candidates: list[dict[str, Any]],
) -> str:
    return f"""棕尘2游戏日 {report['game_day']} 的当前可运行日常已全部完成并成功收尾。
这是独立的每日增量开发线程：对比参考项目，只实现一个尚缺的模块或一个部分实现模块的最小子功能，让下一游戏日的正式日常使用它。

工作目录：{project_root}
只读参考仓库：{reference_root}
路线图：{project_root / 'docs' / 'daily-automation-roadmap.md'}
今日 summary：{summary_path or '无，请查看每日状态与监督日志'}
监督日志：{supervisor_log}
当前阶段结果：{json.dumps(report.get('statuses', {}), ensure_ascii=False)}
按现有路线图顺序排列的候选差距：
{json.dumps(candidates, ensure_ascii=False, indent=2)}

本地 PC 补证方式：
- 先读 docs/cli-game-control.md 与路线图最新的本地控制验收记录。第 17.3 节已实测本地 CLI 的启动、
  2304×1296 客户区截图、点击、主页往返及收尾；这是控制通道的证据，不代替待开发模块的业务验收。
- 本轮是正式增量开发，不是再次执行控制通道冒烟测试。沿用已授权的本地 Python/Win32 路径，
  实际用 tools/game_control.py status 检查，需要补证时用 open --timeout 90、capture 和单步输入。
- 历史“Native computer APIs are disabled”、缺少 node_repl 或旧线程的工具审批拒绝，不能直接作为
  本轮无法启动游戏或缺少 PC 证据的结论。首帧白屏先等待再只读复采；只有当前本地命令的实际失败、
  资源不足、未知/危险页面等仍无法解除的条件，才能记录为本轮阻塞，并保留命令、退出码和截图。
- 补证前先记录目标模块所需的页面与验收条件，查看实际截图后逐步导航；不因尚未留存该页面截图就
  放弃已授权的定向补证。已有截图先回放，需新证据时只探索目标子功能所需的最短路径，未知即停。
- 资源授权按路线图最新条款执行；已经解除的单次资源限制不能重新作为阻塞，也不能自行扩大授权。
  目标模块 Demo 结束后关闭本轮游戏与新启动器，保留最终页面及精确收尾结果。

要求：
1. 先读项目规则、路线图、今日日志、相关实现和参考仓库；记录双方提交版本、具体业务差距、选定的唯一模块/子功能和验收标准。参考仓库仅用于比较业务语义，禁止修改、拉取或切换它，禁止照搬移动端坐标或 Android 架构。
2. 优先推进列表中的第一个可安全落地的差距；若缺少 PC 证据、白名单或前置能力，在路线图写明原因，继续评估下一个候选。每轮最多交付一个模块/子功能，不附带重构或扩展其他模块。
3. 先使用已有截图做离线识别回放；确需补证据时，只允许目标模块的 PC 截图采集和最小单步 Demo。禁止重跑整套日常、--force 或已完成的消耗动作；不得修改/重置今日 daily_automation.json 来伪造完成或重放阶段。
4. 沿用路线图的 PC 证据门、免费动作和失败即停规则。装备、精炼、分解、商店交易等仍须已有明确白名单与上限；本次增量开发授权不代替这些白名单。资源不足、未知页面或证据不足时保留阻塞记录，不能虚报实现或盲点。
5. 验证通过的模块必须接入 tools/daily_automation.py 的真实执行入口和 tools/daily_plan.py 的 current_runners，并维护阶段状态与顺序；只写文件或改 availability 不算交付。仅补齐子功能时继续标为 partial；无 PC 闭环证据的模块保持关闭。不要修改监督器去增加当日重跑；正式流程由下一游戏日的计划任务加载新代码。
6. 更新同一份路线图，记录本次对比、已实现/部分实现/阻塞、测试与 Demo 证据、下一日会运行的入口、仍待完成的差距；全部候选有阻塞时如实记录，不降低证据门。
7. 只运行与本模块相关的必要离线检查及 --show-plan。完成验证后精确暂存本次相关代码、测试、文档，使用中文提交信息并推送当前 main 分支；禁止提交 logs、state、.external 或其他本地产物。不要自行创建其他开发线程或修改计划任务。
8. 最终报告具体模块、参考差距、实际改动、验证、次日接入、提交推送状态与未验收边界。线程结束不等于模块已完成，离线通过不等于次日正式实测通过。
"""


def improve_after_success(
    *,
    args: argparse.Namespace,
    logger: SupervisorLogger,
    report: dict[str, Any],
    summary_path: Path | None,
    project_was_clean: bool,
) -> None:
    """Claim one development turn per game day; never retry the daily flow here."""
    if args.force_phase:
        logger.event("improvement_skipped", "手动单阶段运行不启动增量开发")
        return
    project_root = args.project_root.resolve()
    game_day = report.get("game_day")
    # Use the same local 08:00 boundary as the daily runner. Stale state is not a trigger.
    current_day = (datetime.now() - timedelta(hours=8)).date().isoformat()
    if not report.get("complete") or game_day != current_day:
        logger.event("improvement_skipped", "没有当前游戏日的完整成功记录", game_day=game_day)
        return
    state_path = project_root / "state" / "daily_improvement.json"
    try:
        state = read_state_file(state_path)
        days = state.setdefault("days", {})
        if not isinstance(days, dict):
            raise ValueError("daily_improvement.days 必须为对象")
        if game_day in days:
            logger.event("improvement_skipped", "本游戏日已启动过增量开发，不重复启动", record=days[game_day])
            return
        clean, status = git_status(project_root)
        if not project_was_clean or not clean:
            logger.event("improvement_blocked", "主仓库存在人工改动，未启动增量开发", git_status=status)
            return
        reference_root = project_root / ".external" / "MFABD2-reference"
        ready, error = reference_is_ready(reference_root)
        if not ready:
            logger.event("improvement_blocked", error)
            return
        # Closing is already handled by the supervisor. Risky modules need explicit policy.
        candidates = [
            stage.to_dict()
            for stage in get_daily_plan(FAST_PRESET)
            if stage.enabled and not stage.implemented
            and stage.id not in {"equipment_daily", "arbitrage", "close_game"}
        ]
        if not candidates:
            logger.event("improvement_skipped", "当前预设没有可自动推进的缺失模块；安全白名单仍按路线图处理")
            return
        record = {
            "status": "running",
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "supervisor_log": str(logger.text_path),
            "candidates": [stage["id"] for stage in candidates],
        }
        days[game_day] = record
        # Persist before starting Codex: a crash must not launch a second turn today.
        write_state_atomic(state_path, state)
        logger.event("improvement_started", "日常已完成，启动独立 Codex 线程推进一个模块", **record)
        starter_before = snapshot_run_helpers()
        try:
            turn = run_codex_turn(
                codex_path=args.codex_path,
                project_root=project_root,
                logger=logger,
                prompt=improvement_prompt(
                    project_root=project_root,
                    reference_root=reference_root,
                    report=report,
                    summary_path=summary_path,
                    supervisor_log=logger.text_path,
                    candidates=candidates,
                ),
                label="codex-improvement",
            )
            record.update(asdict(turn))
            record["status"] = "turn_completed" if turn.ok else "failed"
        except Exception as exc:  # An improvement failure must not replay successful dailies.
            record.update(status="failed", error=str(exc))
        finally:
            cleanup = cleanup_after_attempt(starter_before)
            record["cleanup"] = cleanup
            if not cleanup.get("ok"):
                record["status"] = "failed"
            clean, status = git_status(project_root)
            ready, error = reference_is_ready(reference_root)
            record.update(project_clean=clean, git_status=status, reference_ready=ready, reference_error=error)
            if not clean or not ready:
                record["status"] = "failed"
            record["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            write_state_atomic(state_path, state)
        logger.event("improvement_finished", "增量开发线程结束；正式模块结果见线程与路线图，日常结果保持成功", **record)
    except Exception as exc:
        logger.event("improvement_failed", "增量开发失败，已完成的日常不会重跑", error=str(exc))


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


def wait_for_maintenance_retry(summary_path: Path | None, *, logger: SupervisorLogger) -> None:
    """Keep the game closed until the announced end plus one hour."""
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path else {}
        if summary.get("result") != "maintenance_deferred":
            raise ValueError("missing maintenance summary")
        retry_at = datetime.fromisoformat(summary["maintenance"]["retry_at"])
        if retry_at.tzinfo is None:
            raise ValueError("retry time must include timezone")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SupervisorError(f"无法读取维护重试时间：{exc}") from exc
    logger.event(
        "maintenance_wait",
        f"游戏已退出，等待至 {retry_at.isoformat(timespec='seconds')} 再登录",
        **summary["maintenance"],
    )
    while True:
        remaining = retry_at.timestamp() - time.time()
        if remaining <= 0:
            break
        time.sleep(min(60.0, remaining))
    logger.event("maintenance_retry", "维护等待结束，重新登录并从断点继续")


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
        maintenance_retries = 0
        while True:
            latest_result, latest_cleanup = run_automation_attempt(
                python_path=args.python_path,
                project_root=project_root,
                logger=logger,
                force=args.force if attempt == 0 and maintenance_retries == 0 else False,
                force_phase=args.force_phase if attempt == 0 else None,
                attempt=attempt + 1,
            )
            if latest_result.returncode != MAINTENANCE_EXIT_CODE:
                break
            if not latest_cleanup.get("ok"):
                logger.event(
                    "maintenance_cleanup_failed",
                    "维护期间关闭游戏失败，停止重试",
                    cleanup=latest_cleanup,
                )
                return 2
            wait_for_maintenance_retry(latest_summary(project_root), logger=logger)
            maintenance_retries += 1
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
            notify_success(project_root, report=latest_report, logger=logger)
            improve_after_success(
                args=args,
                logger=logger,
                report=latest_report,
                summary_path=summary_path,
                project_was_clean=project_was_clean,
            )
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
