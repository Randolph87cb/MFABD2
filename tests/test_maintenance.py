from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import daily_automation
import daily_supervisor
from game_text_recognition import recognize_entry_status
from maintenance import MAINTENANCE_EXIT_CODE, MaintenanceDeferred, maintenance_notice


NOTICE = [
    "公告事项", "将于10月8日（四）进行定期维护和更新。",
    "更新时间：2026年10月8日（四）上午7:50", "～11:30（共3小时40分钟）",
    "维护影响：将无法登入及使用游戏", "确认",
]
LOCAL_ZONE = timezone(timedelta(hours=8))


class MaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 8, 8, 30, tzinfo=LOCAL_ZONE)

    def test_real_notice_retries_one_hour_after_announced_end(self) -> None:
        notice = maintenance_notice(NOTICE, now=self.now)
        self.assertEqual(notice["maintenance_end"], "2026-10-08T11:30:00+08:00")
        self.assertEqual(notice["retry_at"], "2026-10-08T12:30:00+08:00")
        self.assertIsNone(notice["fallback"])

    def test_afternoon_and_overnight_ranges(self) -> None:
        cases = [
            ("2026年10月8日下午2:00～4:30", "2026-10-08T17:30:00+08:00"),
            ("2026年10月8日23:00～01:30", "2026-10-09T02:30:00+08:00"),
            ("2026年10月8日23:00～2026年10月9日01:30", "2026-10-09T02:30:00+08:00"),
            ("2026-10-08上午7:50~11:30", "2026-10-08T12:30:00+08:00"),
        ]
        for time_range, expected in cases:
            with self.subTest(time_range=time_range):
                notice = maintenance_notice(["维护时间：" + time_range], now=self.now)
                self.assertEqual(notice["retry_at"], expected)

    def test_unreadable_or_elapsed_notice_retries_after_one_hour(self) -> None:
        for texts, now in [
            (["服务器维护中，结束时间待定"], self.now),
            (["维护时间：2026年10月8日7:50～99:30"], self.now),
            (["维护时间：2026年10月32日7:50～11:30"], self.now),
            (NOTICE, self.now.replace(hour=14)),
        ]:
            with self.subTest(texts=texts, now=now):
                notice = maintenance_notice(texts, now=now)
                self.assertEqual(datetime.fromisoformat(notice["retry_at"]), now + timedelta(hours=1))
                self.assertIsNotNone(notice["fallback"])

    def test_downloads_and_ordinary_announcements_are_not_maintenance(self) -> None:
        for texts in (["正在下载100%"], ["公告事项，新增服装更新"], ["维护内容介绍"]):
            self.assertIsNone(maintenance_notice(texts, now=self.now))

    def test_maintenance_takes_priority_over_startup_and_touch_background(self) -> None:
        for background in ("游戏启动中", "TOUCH TO START"):
            groups = {"maintenance_notice": NOTICE, "status": [background], "confirm_button": ["确认"], "download_progress": []}
            with patch("game_text_recognition._recognize_label_groups", return_value=(groups, {}, None)):
                state, details = recognize_entry_status(Image.new("RGB", (2048, 1152)))
            self.assertEqual(state, "maintenance")
            self.assertIn("retry_at", details["maintenance"])

    def test_entry_defers_immediately_without_clicking_or_waiting(self) -> None:
        notice = maintenance_notice(NOTICE, now=self.now)
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_automation.find_game_window", return_value=123),
            patch("daily_automation.open_game", return_value=123),
            patch("daily_automation.mute_game_audio", return_value=True),
            patch("daily_automation.safe_capture_client", return_value=Image.new("RGB", (80, 45))),
            patch("daily_automation.classify_daily_entry_context", return_value=("entry_screen", {}, "maintenance", {"text": {"maintenance": notice}})),
            patch("daily_automation.click_client") as click,
            patch("daily_automation.time.sleep") as sleep,
            patch("builtins.print"),
        ):
            with self.assertRaises(MaintenanceDeferred):
                daily_automation.enter_game_logged(timeout=240, log_root=Path(temporary))
            click.assert_not_called()
            sleep.assert_not_called()

    def test_daily_runner_persists_deferred_checkpoint_and_stops_activity_guard(self) -> None:
        notice = maintenance_notice(NOTICE, now=self.now)
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_automation.os.chdir"),
            patch("daily_automation.wait_for_network", return_value=True),
            patch("daily_automation.DesktopActivityGuard") as guard,
            patch("daily_automation.enter_game_logged", side_effect=MaintenanceDeferred(notice)),
            patch("builtins.print"),
        ):
            root = Path(temporary)
            result = daily_automation.run_daily(project_root=root, force=False, network_timeout=1)
            summary_path = next((root / "logs" / "daily").glob("*/*/summary.json"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            state = json.loads((root / "state" / "daily_automation.json").read_text(encoding="utf-8"))
        self.assertEqual(result, MAINTENANCE_EXIT_CODE)
        self.assertEqual(summary["result"], "maintenance_deferred")
        self.assertEqual(summary["maintenance"]["retry_at"], notice["retry_at"])
        self.assertEqual(state["phases"]["start"]["status"], "running")
        self.assertIsNone(state["phases"]["start"]["error"])
        guard.return_value.stop.assert_called_once()

    def test_supervisor_waits_in_bounded_intervals_until_retry_time(self) -> None:
        notice = maintenance_notice(NOTICE, now=self.now)
        target = datetime.fromisoformat(notice["retry_at"]).timestamp()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.json"
            path.write_text(json.dumps({"result": "maintenance_deferred", "maintenance": notice}), encoding="utf-8")
            with (
                patch("daily_supervisor.time.time", side_effect=[target - 130, target - 70, target - 10, target]),
                patch("daily_supervisor.time.sleep") as sleep,
            ):
                daily_supervisor.wait_for_maintenance_retry(path, logger=MagicMock())
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 60, 10])

    def test_invalid_deferred_summary_stops_instead_of_guessing_retry_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.json"
            for payload in ({}, {"result": "maintenance_deferred", "maintenance": {"retry_at": "2026-10-08T12:30:00"}}):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(daily_supervisor.SupervisorError):
                    daily_supervisor.wait_for_maintenance_retry(path, logger=MagicMock())

    def test_maintenance_retries_close_game_first_and_do_not_consume_repairs(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_supervisor.prune_old_logs", return_value=[]),
            patch("daily_supervisor.git_status", return_value=(True, "")),
            patch("daily_supervisor.snapshot_run_helpers", return_value={}),
            patch("daily_supervisor.cleanup_after_attempt", return_value={"ok": True}) as cleanup,
            patch("daily_supervisor.run_command", side_effect=[
                daily_supervisor.CommandResult(MAINTENANCE_EXIT_CODE, [], []),
                daily_supervisor.CommandResult(MAINTENANCE_EXIT_CODE, [], []),
                daily_supervisor.CommandResult(0, [], []),
            ]) as run,
            patch("daily_supervisor.wait_for_maintenance_retry") as wait,
            patch("daily_supervisor.latest_summary", return_value=None),
            patch("daily_supervisor.completion_report", return_value={"complete": True}),
            patch("daily_supervisor.run_codex_turn") as repair,
            patch("builtins.print"),
        ):
            def confirm_cleanup(*args, **kwargs):
                self.assertEqual(cleanup.call_count, wait.call_count)
            wait.side_effect = confirm_cleanup
            args = daily_supervisor.build_parser().parse_args([
                "--project-root", temporary, "--codex-path", "codex.cmd", "--max-repair-rounds", "0",
            ])
            result = daily_supervisor.supervise(args)
        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(cleanup.call_count, 3)
        self.assertEqual(wait.call_count, 2)
        repair.assert_not_called()

    def test_failed_cleanup_prevents_delayed_login(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_supervisor.prune_old_logs", return_value=[]),
            patch("daily_supervisor.git_status", return_value=(True, "")),
            patch("daily_supervisor.run_automation_attempt", return_value=(daily_supervisor.CommandResult(MAINTENANCE_EXIT_CODE, [], []), {"ok": False})),
            patch("daily_supervisor.wait_for_maintenance_retry") as wait,
            patch("builtins.print"),
        ):
            args = daily_supervisor.build_parser().parse_args(["--project-root", temporary, "--codex-path", "codex.cmd"])
            self.assertEqual(daily_supervisor.supervise(args), 2)
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
