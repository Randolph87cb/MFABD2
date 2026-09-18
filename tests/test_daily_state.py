from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_state import DailyStateStore, new_daily_state, write_state_atomic


PHASES = ("network", "enter_game", "free_gacha")


class DailyStateTests(unittest.TestCase):
    def test_initialization_creates_pending_phase_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state" / "daily.json"
            store = DailyStateStore.load(
                path,
                game_day="2026-09-18",
                phase_ids=PHASES,
                now="2026-09-18T08:00:00+08:00",
            )

            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(store.phases_to_run(), PHASES)
        self.assertEqual(persisted["game_day"], "2026-09-18")
        self.assertEqual(persisted["phase_order"], list(PHASES))
        self.assertEqual(
            [persisted["phases"][phase]["status"] for phase in PHASES],
            ["pending", "pending", "pending"],
        )

    def test_same_day_resume_skips_completed_and_skipped_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.json"
            store = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="start"
            )
            store.mark_completed("network", now="network-done")
            store.mark_skipped("enter_game", now="entry-skipped")

            resumed = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="resume"
            )

        self.assertEqual(resumed.phases_to_run(), ("free_gacha",))

    def test_cross_day_load_resets_all_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.json"
            store = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="day-one"
            )
            for phase_id in PHASES:
                store.mark_completed(phase_id, now=f"done-{phase_id}")

            next_day = DailyStateStore.load(
                path, game_day="2026-09-19", phase_ids=PHASES, now="day-two"
            )

        self.assertEqual(next_day.phases_to_run(), PHASES)
        self.assertEqual(next_day.state["game_day"], "2026-09-19")
        self.assertNotIn("legacy", next_day.state)

    def test_failed_phase_is_retried_without_replaying_completed_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.json"
            store = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="start"
            )
            store.mark_completed("network", now="network-done")
            store.mark_failed("enter_game", "login required", now="failed")

            resumed = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="resume"
            )

        self.assertEqual(resumed.phase_status("enter_game"), "failed")
        self.assertEqual(resumed.phases_to_run(), ("enter_game", "free_gacha"))

    def test_running_phase_is_retried_after_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.json"
            store = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="start"
            )
            store.mark_completed("network", now="network-done")
            store.mark_running("enter_game", now="crashed-run")

            resumed = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="resume"
            )
            resumed.mark_running("enter_game", now="retry")

        self.assertEqual(resumed.phases_to_run(), ("enter_game", "free_gacha"))
        self.assertEqual(resumed.state["phases"]["enter_game"]["attempts"], 2)

    def test_force_phase_runs_only_requested_phase_even_if_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.json"
            store = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="start"
            )
            for phase_id in PHASES:
                store.mark_completed(phase_id, now=f"done-{phase_id}")

            selected = store.phases_to_run(force_phase="enter_game")

        self.assertEqual(selected, ("enter_game",))
        with self.assertRaisesRegex(ValueError, "unknown force phase"):
            store.phases_to_run(force_phase="not-a-phase")

    def test_completed_legacy_state_blocks_a_same_day_duplicate_run(self) -> None:
        legacy = {
            "last_started_game_day": "2026-09-18",
            "status": "completed",
            "started_at": "2026-09-18T08:00:00+08:00",
            "finished_at": "2026-09-18T09:00:00+08:00",
            "error": None,
            "log_dir": "logs/daily/old-run",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")

            migrated = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="migration"
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(migrated.phases_to_run(), ())
        self.assertEqual(persisted["legacy"], legacy)
        self.assertTrue(
            all(
                persisted["phases"][phase_id]["status"] == "skipped"
                for phase_id in PHASES
            )
        )

    def test_completed_legacy_state_from_another_day_does_not_block_today(self) -> None:
        legacy = {
            "last_started_game_day": "2026-09-17",
            "status": "completed",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")

            migrated = DailyStateStore.load(
                path, game_day="2026-09-18", phase_ids=PHASES, now="migration"
            )

        self.assertEqual(migrated.phases_to_run(), PHASES)

    def test_atomic_persistence_replaces_destination_after_temp_file_is_fsynced(self) -> None:
        state = new_daily_state("2026-09-18", PHASES, now="now")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.json"
            path.write_text('{"old": true}\n', encoding="utf-8")
            real_replace = os.replace
            replace_calls: list[tuple[Path, Path]] = []

            def observed_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                source_path = Path(source)
                target_path = Path(target)
                self.assertEqual(target_path, path)
                self.assertNotEqual(source_path, path)
                json.loads(source_path.read_text(encoding="utf-8"))
                replace_calls.append((source_path, target_path))
                real_replace(source_path, target_path)

            with patch("daily_state.os.replace", side_effect=observed_replace):
                write_state_atomic(path, state)

            temporary_files = list(path.parent.glob(f".{path.name}.*.tmp"))
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(len(replace_calls), 1)
        self.assertEqual(temporary_files, [])
        self.assertEqual(persisted, state)


if __name__ == "__main__":
    unittest.main()
