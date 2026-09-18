"""Persistent per-phase state for resumable daily automation runs.

This module is intentionally independent from ``daily_automation`` so the
runner can adopt phase checkpoints without coupling state migration to game
automation code.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


STATE_VERSION = 2
PHASE_STATUSES = frozenset(
    {"pending", "running", "completed", "skipped", "failed", "unavailable"}
)
DEFAULT_RESUMABLE_STATUSES = frozenset({"pending", "running", "failed"})
FINISHED_PHASE_STATUSES = frozenset({"completed", "skipped", "unavailable"})


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _validated_phase_ids(phase_ids: Iterable[str]) -> tuple[str, ...]:
    result = tuple(phase_ids)
    if not result:
        raise ValueError("phase_ids must not be empty")
    if any(not isinstance(phase_id, str) or not phase_id.strip() for phase_id in result):
        raise ValueError("phase ids must be non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError("phase ids must be unique")
    return result


def _new_phase() -> dict[str, Any]:
    return {
        "status": "pending",
        "attempts": 0,
        "started_at": None,
        "finished_at": None,
        "error": None,
    }


def new_daily_state(
    game_day: str,
    phase_ids: Iterable[str],
    *,
    now: str | None = None,
    legacy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fresh state document with every requested phase pending."""
    ordered_ids = _validated_phase_ids(phase_ids)
    if not isinstance(game_day, str) or not game_day:
        raise ValueError("game_day must be a non-empty string")
    timestamp = now or _timestamp()
    state: dict[str, Any] = {
        "version": STATE_VERSION,
        "game_day": game_day,
        "status": "pending",
        "phase_order": list(ordered_ids),
        "phases": {phase_id: _new_phase() for phase_id in ordered_ids},
        "started_at": None,
        "finished_at": None,
        "updated_at": timestamp,
    }
    if legacy is not None:
        state["legacy"] = copy.deepcopy(dict(legacy))
    return state


def normalize_daily_state(
    raw_state: Mapping[str, Any] | None,
    *,
    game_day: str,
    phase_ids: Iterable[str],
    now: str | None = None,
) -> dict[str, Any]:
    """Normalize current state, reset another day, or safely migrate legacy state.

    A document without the current phase-based schema is treated as legacy.
    Its data is retained for diagnostics, but no phase completion is inferred
    from a whole-run ``status`` value.
    """
    ordered_ids = _validated_phase_ids(phase_ids)
    timestamp = now or _timestamp()
    raw = dict(raw_state or {})
    is_current = (
        raw.get("version") == STATE_VERSION
        and isinstance(raw.get("phases"), dict)
        and isinstance(raw.get("game_day"), str)
    )

    if not is_current:
        migrated = new_daily_state(
            game_day,
            ordered_ids,
            now=timestamp,
            legacy=raw or None,
        )
        legacy_game_day = raw.get("last_started_game_day") or raw.get("last_started_date")
        if legacy_game_day == game_day and raw.get("status") == "completed":
            for phase_id in ordered_ids:
                migrated = update_phase(
                    migrated,
                    phase_id,
                    "skipped",
                    now=timestamp,
                    error="legacy whole-day run was already completed",
                )
        return migrated
    if raw["game_day"] != game_day:
        return new_daily_state(game_day, ordered_ids, now=timestamp)

    state = copy.deepcopy(raw)
    phases = state["phases"]
    for phase_id in ordered_ids:
        phase = phases.get(phase_id)
        if not isinstance(phase, dict) or phase.get("status") not in PHASE_STATUSES:
            phases[phase_id] = _new_phase()
            continue
        normalized = _new_phase()
        normalized.update(phase)
        try:
            normalized["attempts"] = max(0, int(normalized.get("attempts", 0)))
        except (TypeError, ValueError):
            normalized["attempts"] = 0
        phases[phase_id] = normalized

    state["version"] = STATE_VERSION
    state["game_day"] = game_day
    state["phase_order"] = list(ordered_ids)
    state.setdefault("started_at", None)
    state.setdefault("finished_at", None)
    state.setdefault("updated_at", timestamp)
    _refresh_run_status(state)
    return state


def select_phases(
    state: Mapping[str, Any],
    *,
    force_phase: str | None = None,
) -> tuple[str, ...]:
    """Return phases to execute, honoring checkpoints and an optional force target."""
    phase_order = tuple(state.get("phase_order", ()))
    phases = state.get("phases")
    if not isinstance(phases, Mapping):
        raise ValueError("state has no phase mapping")
    if force_phase is not None:
        if force_phase not in phase_order:
            raise ValueError(f"unknown force phase: {force_phase}")
        return (force_phase,)
    return tuple(
        phase_id
        for phase_id in phase_order
        if isinstance(phases.get(phase_id), Mapping)
        and phases[phase_id].get("status") in DEFAULT_RESUMABLE_STATUSES
    )


def _refresh_run_status(state: dict[str, Any]) -> None:
    phase_order = state.get("phase_order", ())
    phases = state.get("phases", {})
    statuses = [
        phases[phase_id].get("status", "pending")
        for phase_id in phase_order
        if isinstance(phases.get(phase_id), dict)
    ]
    if statuses and all(status in FINISHED_PHASE_STATUSES for status in statuses):
        state["status"] = "completed"
    elif "failed" in statuses:
        state["status"] = "failed"
    elif "running" in statuses:
        state["status"] = "running"
    else:
        state["status"] = "pending"


def update_phase(
    state: Mapping[str, Any],
    phase_id: str,
    status: str,
    *,
    now: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Return a copied state with one phase transition applied."""
    if status not in PHASE_STATUSES:
        raise ValueError(f"unsupported phase status: {status}")
    result = copy.deepcopy(dict(state))
    phases = result.get("phases")
    if not isinstance(phases, dict) or phase_id not in phases:
        raise ValueError(f"unknown phase: {phase_id}")
    phase = phases[phase_id]
    if not isinstance(phase, dict):
        raise ValueError(f"invalid phase state: {phase_id}")

    timestamp = now or _timestamp()
    if status == "pending":
        phase.update(_new_phase())
    elif status == "running":
        phase["status"] = status
        phase["attempts"] = max(0, int(phase.get("attempts", 0))) + 1
        phase["started_at"] = timestamp
        phase["finished_at"] = None
        phase["error"] = None
        result["started_at"] = result.get("started_at") or timestamp
        result["finished_at"] = None
    else:
        phase["status"] = status
        phase["finished_at"] = timestamp
        phase["error"] = error

    result["updated_at"] = timestamp
    _refresh_run_status(result)
    if result["status"] == "completed":
        result["finished_at"] = timestamp
    elif result["status"] in {"pending", "running"}:
        result["finished_at"] = None
    return result


def read_state_file(path: Path) -> dict[str, Any]:
    """Read a JSON object from *path*, returning an empty mapping if absent."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read daily state: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("daily state must be a JSON object")
    return payload


def write_state_atomic(path: Path, state: Mapping[str, Any]) -> None:
    """Persist *state* by atomically replacing the destination file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(dict(state), file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


class DailyStateStore:
    """Small persistence wrapper around the pure state transition functions."""

    def __init__(self, path: Path, state: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self._state = copy.deepcopy(dict(state))

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        game_day: str,
        phase_ids: Sequence[str],
        now: str | None = None,
    ) -> "DailyStateStore":
        raw = read_state_file(path)
        normalized = normalize_daily_state(
            raw,
            game_day=game_day,
            phase_ids=phase_ids,
            now=now,
        )
        if normalized != raw:
            write_state_atomic(path, normalized)
        return cls(path, normalized)

    @property
    def state(self) -> dict[str, Any]:
        """Return a snapshot so callers cannot bypass atomic persistence."""
        return copy.deepcopy(self._state)

    def phase_status(self, phase_id: str) -> str:
        try:
            return str(self._state["phases"][phase_id]["status"])
        except KeyError as exc:
            raise ValueError(f"unknown phase: {phase_id}") from exc

    def phases_to_run(self, *, force_phase: str | None = None) -> tuple[str, ...]:
        return select_phases(self._state, force_phase=force_phase)

    def set_phase(
        self,
        phase_id: str,
        status: str,
        *,
        now: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        updated = update_phase(
            self._state,
            phase_id,
            status,
            now=now,
            error=error,
        )
        write_state_atomic(self.path, updated)
        self._state = updated
        return self.state

    def mark_running(self, phase_id: str, *, now: str | None = None) -> dict[str, Any]:
        return self.set_phase(phase_id, "running", now=now)

    def mark_completed(self, phase_id: str, *, now: str | None = None) -> dict[str, Any]:
        return self.set_phase(phase_id, "completed", now=now)

    def mark_skipped(self, phase_id: str, *, now: str | None = None) -> dict[str, Any]:
        return self.set_phase(phase_id, "skipped", now=now)

    def mark_failed(
        self,
        phase_id: str,
        error: str,
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        return self.set_phase(phase_id, "failed", now=now, error=error)

    def mark_unavailable(
        self,
        phase_id: str,
        reason: str | None = None,
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        return self.set_phase(phase_id, "unavailable", now=now, error=reason)
