"""Safely close BrownDust II and helper processes created by one run."""

from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import psutil

from open_game import STARTER, find_game_window


WM_CLOSE = 0x0010
user32 = ctypes.windll.user32


@dataclass(frozen=True)
class ProcessIdentity:
    """Identity strong enough to avoid acting on a reused Windows PID."""

    pid: int
    create_time: float
    executable: str


def _normalized_executable(path: str | Path) -> str:
    return str(Path(path).resolve(strict=False)).casefold()


def process_identity(pid: int) -> ProcessIdentity | None:
    """Return a process identity, or ``None`` when it cannot be verified."""
    try:
        process = psutil.Process(pid)
        executable = process.exe()
        create_time = process.create_time()
    except (psutil.Error, OSError):
        return None
    if not executable:
        return None
    return ProcessIdentity(
        pid=int(pid),
        create_time=float(create_time),
        executable=_normalized_executable(executable),
    )


def identity_is_current(identity: ProcessIdentity) -> bool:
    """Re-check PID, start time, and executable before terminating it."""
    current = process_identity(identity.pid)
    return current == identity


def snapshot_exact_executable(executable: str | Path) -> dict[int, ProcessIdentity]:
    """Snapshot processes whose executable exactly matches *executable*."""
    expected = _normalized_executable(executable)
    result: dict[int, ProcessIdentity] = {}
    for item in psutil.process_iter(("pid", "exe", "create_time")):
        try:
            path = item.info.get("exe")
            if not path or _normalized_executable(path) != expected:
                continue
            identity = ProcessIdentity(
                pid=int(item.info["pid"]),
                create_time=float(item.info["create_time"]),
                executable=expected,
            )
            result[identity.pid] = identity
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return result


def window_process_id(hwnd: int) -> int | None:
    pid = ctypes.c_ulong(0)
    thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) if thread_id and pid.value else None


def post_close(hwnd: int) -> bool:
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def _stop_verified_process(identity: ProcessIdentity, *, grace_seconds: float) -> str:
    if not identity_is_current(identity):
        return "identity_changed"
    try:
        process = psutil.Process(identity.pid)
        process.terminate()
        process.wait(timeout=grace_seconds)
        return "terminated"
    except psutil.TimeoutExpired:
        if not identity_is_current(identity):
            return "identity_changed"
        try:
            process = psutil.Process(identity.pid)
            process.kill()
            process.wait(timeout=grace_seconds)
            return "killed"
        except (psutil.Error, OSError):
            return "kill_failed"
    except psutil.NoSuchProcess:
        return "exited"
    except (psutil.Error, OSError):
        return "terminate_failed"


def close_game(*, grace_seconds: float = 15.0) -> dict[str, Any]:
    """Close the exact game window and only force its verified owning PID."""
    hwnd = find_game_window()
    if not hwnd:
        return {"ok": True, "action": "not_running"}
    pid = window_process_id(hwnd)
    identity = process_identity(pid) if pid else None
    if identity is None:
        return {"ok": False, "action": "unverified_game_process", "hwnd": int(hwnd)}

    posted = post_close(hwnd)
    if posted:
        try:
            psutil.Process(identity.pid).wait(timeout=grace_seconds)
            return {
                "ok": True,
                "action": "closed",
                "process": asdict(identity),
            }
        except psutil.NoSuchProcess:
            return {
                "ok": True,
                "action": "closed",
                "process": asdict(identity),
            }
        except psutil.TimeoutExpired:
            pass
        except (psutil.Error, OSError):
            pass

    action = _stop_verified_process(identity, grace_seconds=min(grace_seconds, 5.0))
    return {
        "ok": action in {"terminated", "killed", "exited"},
        "action": action,
        "process": asdict(identity),
    }


def close_new_exact_processes(
    executable: str | Path,
    before: Mapping[int, ProcessIdentity],
    *,
    grace_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    """Close only matching executable instances absent from the pre-run snapshot."""
    results: list[dict[str, Any]] = []
    after = snapshot_exact_executable(executable)
    for pid, identity in sorted(after.items()):
        if before.get(pid) == identity:
            continue
        action = _stop_verified_process(identity, grace_seconds=grace_seconds)
        results.append(
            {
                "ok": action in {"terminated", "killed", "exited"},
                "action": action,
                "process": asdict(identity),
            }
        )
    return results


def snapshot_run_helpers() -> dict[int, ProcessIdentity]:
    return snapshot_exact_executable(STARTER)


def cleanup_after_attempt(
    starter_before: Mapping[int, ProcessIdentity],
) -> dict[str, Any]:
    """Close the game and newly-created starter processes after an attempt."""
    game = close_game()
    helpers = close_new_exact_processes(STARTER, starter_before)
    return {
        "ok": bool(game.get("ok")) and all(item.get("ok") for item in helpers),
        "game": game,
        "helpers": helpers,
    }
