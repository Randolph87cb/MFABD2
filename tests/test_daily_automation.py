from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import open_game as open_game_module
from adaptive_wait import AdaptivePoll
from daily_automation import (
    DAILY_READY_STATES,
    DOWNLOAD_CONFIRM_CLICK,
    MAX_UNKNOWN_ENTRY_FRAMES,
    MasterLogger,
    can_finish_entry_phase,
    claim_daily_run,
    classify_daily_entry_context,
    enter_game_logged,
    ensure_home,
    game_day_key,
    mute_game_audio,
    open_failure_review,
    overlay_transition_succeeded,
    recognize_daily_entry_state,
    return_home_transition_succeeded,
    run_daily,
    startup_promotion_transition_succeeded,
    update_daily_state,
    wait_for_network,
)
from game_text_recognition import (
    LabelRecognitionSession,
    recognize_arena_cartridge_bar_labels,
    recognize_arena_cartridge_labels,
    recognize_arena_rank_change_labels,
    recognize_entry_status,
    recognize_gacha_target_labels,
    recognize_game_loading_labels,
    recognize_plaza_labels,
    recognize_return_home_control,
    recognize_terms_agreement_labels,
)
from daily_arena import (
    ARENA_DIALOGUE_STATES,
    enter_arena_from_plaza,
    enter_battlefield,
    enter_battle_prep,
    is_gameplay_tab_selected,
    wait_for_cartridge_collection_ready,
)
from business_management import detect_regular_customer_note_notification
from enter_game import TOUCH_CLICK
from free_gacha import (
    ActionResult,
    CLICK_POINTS,
    RETRY_CLICK_POINTS,
    RunLogger,
    _click_ratio,
    _is_reveal_animation_like,
    _resolve_all_free_gacha_availability,
    classify_state,
    click_with_fixed_retry,
    detect_arena_pool_click,
    detect_selected_gacha_target,
    is_free_gacha_confirm_transition,
    run_free_gacha,
    safe_capture_client,
    skip_gacha_animation,
    wait_for_state,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "recognition"


class PositionedTextRecognitionTests(unittest.TestCase):
    def test_terms_dialog_requires_all_three_positioned_labels(self) -> None:
        session = MagicMock()
        session.recognize.return_value = (
            {
                "header": ["同意《棕色尘埃2》使用条款"],
                "agreement": ["全部同意"],
                "start_button": ["开始游戏"],
            },
            {
                "header": ["使用条款"],
                "agreement": ["全部同意"],
                "start_button": ["开始游戏"],
            },
            None,
        )

        matched, _details = recognize_terms_agreement_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)

    def test_mirror_wars_title_and_percentage_are_a_loading_screen(self) -> None:
        session = MagicMock()

        def recognize(groups: dict[str, object]):
            self.assertEqual(groups["title"]["labels"], ("MIRROR", "WARS", "镜中之战"))
            return (
                {"title": ["MIRROR", "WARS", "镜中之战"], "progress": ["0%"]},
                {"title": ["MIRROR", "WARS", "镜中之战"], "progress": []},
                None,
            )

        session.recognize.side_effect = recognize

        matched, details = recognize_game_loading_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)
        self.assertTrue(details["has_progress"])

    def test_arena_rank_drop_text_is_a_rank_change_confirmation(self) -> None:
        session = MagicMock()

        def recognize(groups: dict[str, object]):
            self.assertIn("段位下滑", groups["rank"]["labels"])
            return (
                {
                    "rank": ["白金III", "胜利分 1618", "段位下滑。"],
                    "button": ["确认"],
                },
                {"rank": ["段位下滑"], "button": ["确认"]},
                None,
            )

        session.recognize.side_effect = recognize

        matched, _details = recognize_arena_rank_change_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)

    def test_loading_cartridge_collection_uses_multiple_card_names_without_title(self) -> None:
        session = MagicMock()
        session.recognize.return_value = (
            {
                "title": [],
                "gameplay_cards": ["冒险航线", "末日之书", "黄金竞技场"],
            },
            {
                "title": [],
                "gameplay_cards": ["冒险航线", "末日之书", "黄金竞技场"],
            },
            None,
        )

        matched, details = recognize_arena_cartridge_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)
        self.assertTrue(details["loading"])
        self.assertFalse(details["ready"])

    def test_single_character_ocr_noise_is_not_meaningful_ui_text(self) -> None:
        session = LabelRecognitionSession(Image.new("RGB", (80, 45)))
        with patch.object(
            session,
            "_load",
            return_value=([(0.2, 0.3, "A"), (0.6, 0.7, "M")], None),
        ):
            texts, error = session.meaningful_texts()

        self.assertIsNone(error)
        self.assertEqual(texts, [])

    def test_plaza_uses_bottom_left_chat_input_text(self) -> None:
        session = MagicMock()
        session.recognize.return_value = (
            {"chat_input": ["输入聊天内容（最多100字）"]},
            {"chat_input": []},
            None,
        )

        matched, details = recognize_plaza_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)
        self.assertTrue(details["has_chat_input"])

    def test_gacha_target_uses_top_title_text(self) -> None:
        session = MagicMock()
        session.recognize.return_value = (
            {"title": ["装备抽抽乐", "抽抽乐记录"]},
            {"title": []},
            None,
        )

        target, details = recognize_gacha_target_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertEqual(target, "gear")
        self.assertEqual(details["target"], "gear")

    def test_selected_gacha_target_prefers_title_over_visual_fallback(self) -> None:
        with patch(
            "free_gacha.recognize_gacha_target_labels",
            return_value=("gear", {"target": "gear"}),
        ):
            target = detect_selected_gacha_target(Image.new("RGB", (80, 45)))

        self.assertEqual(target, "gear")

    def test_selected_gacha_target_does_not_guess_when_text_is_available(self) -> None:
        with patch(
            "free_gacha.recognize_gacha_target_labels",
            return_value=(None, {"available": True, "target": None}),
        ):
            target = detect_selected_gacha_target(Image.new("RGB", (80, 45)))

        self.assertIsNone(target)

    def test_unified_blocking_overlay_remains_an_arena_dialogue_state(self) -> None:
        self.assertIn("blocking_ad_overlay", ARENA_DIALOGUE_STATES)


class OpenGameTests(unittest.TestCase):
    def test_starter_log_activity_extends_window_wait(self) -> None:
        clock = [0.0]
        windows = iter((0, 0, 0, 123))
        log_activity = iter(((), (), (("starter.log", 10, 1),)))
        starter = MagicMock()
        starter.exists.return_value = True
        starter.__str__.return_value = "starter.exe"
        starter.parent = "."

        def sleep(seconds: float) -> None:
            clock[0] += seconds * 4

        with (
            patch("open_game.STARTER", starter),
            patch("open_game.subprocess.Popen"),
            patch("open_game.find_game_window", side_effect=lambda: next(windows)),
            patch("open_game._starter_log_activity", side_effect=lambda: next(log_activity)),
            patch("open_game.time.monotonic", side_effect=lambda: clock[0]),
            patch("open_game.time.sleep", side_effect=sleep),
        ):
            hwnd = open_game_module.open_game(timeout=5.0)

        self.assertEqual(hwnd, 123)
        self.assertGreater(clock[0], 5.0)

    def test_starter_wait_stops_after_continuous_inactivity(self) -> None:
        clock = [0.0]
        starter = MagicMock()
        starter.exists.return_value = True
        starter.__str__.return_value = "starter.exe"
        starter.parent = "."

        def sleep(seconds: float) -> None:
            clock[0] += seconds * 3

        with (
            patch("open_game.STARTER", starter),
            patch("open_game.subprocess.Popen"),
            patch("open_game.find_game_window", return_value=0),
            patch("open_game._starter_log_activity", return_value=()),
            patch("open_game.time.monotonic", side_effect=lambda: clock[0]),
            patch("open_game.time.sleep", side_effect=sleep),
        ):
            with self.assertRaisesRegex(
                TimeoutError,
                "starter made no progress for 5s",
            ):
                open_game_module.open_game(timeout=5.0)


class DailyAutomationStateTests(unittest.TestCase):
    @patch("daily_automation.subprocess.Popen")
    @patch("daily_automation.webbrowser.open")
    @patch("daily_automation.urllib.request.urlopen")
    def test_failure_review_reuses_running_server(
        self,
        urlopen: MagicMock,
        browser_open: MagicMock,
        popen: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"items": []}'
        urlopen.return_value = response

        result = open_failure_review(Path("D:/project"))

        self.assertEqual(result, "reused")
        browser_open.assert_called_once_with("http://127.0.0.1:8787/?filter=daily")
        popen.assert_not_called()

    @patch("daily_automation.subprocess.Popen")
    @patch("daily_automation.time.sleep")
    @patch("daily_automation.webbrowser.open")
    @patch("daily_automation.urllib.request.urlopen", side_effect=OSError("not running"))
    def test_failure_review_starts_server_when_needed(
        self,
        _urlopen: MagicMock,
        browser_open: MagicMock,
        sleep: MagicMock,
        popen: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "tools" / "recognition_review.py"
            script.parent.mkdir()
            script.touch()

            result = open_failure_review(root)

        self.assertEqual(result, "started")
        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "recognition_review.py")
        self.assertIn("--no-browser", command)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(root))
        sleep.assert_called_once_with(0.4)
        browser_open.assert_called_once_with("http://127.0.0.1:8787/?filter=daily")

    def test_adaptive_poll_uses_fibonacci_like_delays_and_caps_at_eight(self) -> None:
        poll = AdaptivePoll()

        self.assertEqual([poll.next_delay() for _ in range(7)], [1, 2, 3, 5, 8, 8, 8])
        poll.reset()
        self.assertEqual(poll.next_delay(), 1)

    @patch("daily_automation.time.sleep")
    @patch("daily_automation.urllib.request.getproxies_environment")
    @patch(
        "daily_automation.urllib.request.getproxies_registry",
        side_effect=[{}, {}, {"https": "http://127.0.0.1:7897"}],
    )
    @patch("daily_automation.urllib.request.build_opener")
    def test_network_wait_uses_vpn_proxy_added_after_process_start(
        self,
        build_opener: MagicMock,
        getproxies_registry: MagicMock,
        getproxies_environment: MagicMock,
        sleep: MagicMock,
    ) -> None:
        getproxies_environment.return_value = {"lark_cli_no": "1"}
        response = MagicMock()
        response.__enter__.return_value.getcode.return_value = 204
        opener = MagicMock()
        opener.open.side_effect = [
            urllib.error.URLError("offline"),
            urllib.error.URLError("VPN is still starting"),
            response,
        ]
        build_opener.return_value = opener
        logger = MagicMock()

        self.assertTrue(wait_for_network(logger, timeout=None))

        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(getproxies_registry.call_count, 3)
        self.assertEqual(getproxies_environment.call_count, 3)
        self.assertEqual(build_opener.call_count, 3)
        proxy_snapshots = [call.args[0].proxies for call in build_opener.call_args_list]
        self.assertEqual(proxy_snapshots, [{}, {}, {"https": "http://127.0.0.1:7897"}])
        requested_urls = [call.args[0].full_url for call in opener.open.call_args_list]
        self.assertEqual(requested_urls, ["https://www.google.com/generate_204"] * 3)

    def test_game_day_rolls_over_at_eight_in_the_morning(self) -> None:
        self.assertEqual(game_day_key(datetime(2026, 8, 8, 7, 59, 59)), "2026-08-07")
        self.assertEqual(game_day_key(datetime(2026, 8, 8, 8, 0, 0)), "2026-08-08")

    def test_run_is_allowed_again_after_the_eight_oclock_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            first_day = game_day_key(datetime(2026, 8, 8, 7, 59, 59))
            next_day = game_day_key(datetime(2026, 8, 8, 8, 0, 0))

            first, _state = claim_daily_run(
                state_path,
                run_date=first_day,
                run_root=root / "logs" / "before-reset",
                force=False,
                started_at="2026-08-08T07:59:59",
            )
            second, current = claim_daily_run(
                state_path,
                run_date=next_day,
                run_root=root / "logs" / "after-reset",
                force=False,
                started_at="2026-08-08T08:00:00",
            )

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(current["last_started_game_day"], "2026-08-08")

    def test_old_midnight_based_state_is_migrated_from_its_start_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "last_started_date": "2026-08-08",
                        "started_at": "2026-08-08T02:49:34",
                        "status": "completed",
                    }
                ),
                encoding="utf-8",
            )

            claimed, _previous = claim_daily_run(
                state_path,
                run_date="2026-08-07",
                run_root=root / "logs" / "retry-before-reset",
                force=False,
                started_at="2026-08-08T07:30:00",
            )

        self.assertFalse(claimed)

    def test_scheduled_launcher_uses_a_visible_python_console(self) -> None:
        script = (TOOLS_DIR / "install_daily_task.ps1").read_text(encoding="utf-8")

        self.assertIn("Get-Command python.exe", script)
        self.assertNotIn("pythonw.exe", script)

    @patch("builtins.print")
    def test_master_logger_prints_each_event_to_the_visible_console(
        self,
        print_mock: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            logger = MasterLogger(Path(temporary))

            logger.event("enter_game", "waiting", "等待游戏响应")

        printed = print_mock.call_args.args[0]
        self.assertIn("[等待] [进入游戏] 等待游戏响应", printed)
        self.assertTrue(print_mock.call_args.kwargs["flush"])

    @patch("builtins.print")
    def test_step_logger_explains_recognition_and_clicks_in_chinese(
        self,
        print_mock: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            logger = RunLogger(Path(temporary))
            logger.event(action="start", flow="ensure_home")
            logger.event(action="classify", state="arena_lobby")
            logger.event(action="click", key="arena_home", attempt=1)

        output = "\n".join(str(call.args[0]) for call in print_mock.call_args_list)
        self.assertIn("[返回主页] 开始执行", output)
        self.assertIn("识别到：竞技场大厅", output)
        self.assertIn("点击：竞技场右上角主页（第 1 次）", output)

    @patch("daily_automation.os.chdir")
    @patch("daily_automation.claim_daily_run", return_value=(True, {}))
    @patch("daily_automation.wait_for_network", return_value=True)
    @patch("daily_automation._require_phase")
    @patch("daily_automation.run_free_gacha")
    @patch("daily_automation.update_daily_state")
    @patch("builtins.print")
    def test_daily_run_returns_home_before_starting_gacha(
        self,
        _print: MagicMock,
        _update_daily_state: MagicMock,
        run_free_gacha: MagicMock,
        require_phase: MagicMock,
        _wait_for_network: MagicMock,
        _claim_daily_run: MagicMock,
        _chdir: MagicMock,
    ) -> None:
        run_free_gacha.return_value = ActionResult(
            "gacha_page",
            "stop",
            "all requested free gacha targets completed",
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = run_daily(
                project_root=Path(temporary),
                force=True,
                network_timeout=1.0,
            )

        stages = [call.args[1] for call in require_phase.call_args_list]
        self.assertEqual(result, 0)
        self.assertEqual(stages[:2], ["enter_game", "prepare_home"])
        self.assertEqual(stages[-2:], ["business_management_home", "business_management"])

    def test_second_start_on_same_day_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            run_root = root / "logs" / "first"

            first, _state = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=run_root,
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            second, previous = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "second",
                force=False,
                started_at="2026-07-31T09:00:00",
            )

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(previous["run_root"], str(run_root))

    def test_force_allows_same_day_manual_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "first",
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            claimed, current = claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "forced",
                force=True,
                started_at="2026-07-31T09:00:00",
            )

            self.assertTrue(claimed)
            self.assertEqual(current["run_root"], str(root / "logs" / "forced"))

    def test_failure_status_keeps_daily_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            claim_daily_run(
                state_path,
                run_date="2026-07-31",
                run_root=root / "logs" / "run",
                force=False,
                started_at="2026-07-31T08:00:00",
            )
            update_daily_state(state_path, status="failed", error="login required")
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(state["last_started_date"], "2026-07-31")
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["error"], "login required")


class DailyAutomationEntryRecognitionTests(unittest.TestCase):
    @patch("free_gacha._click_ratio")
    @patch("free_gacha.time.sleep")
    @patch("free_gacha.safe_capture_client")
    @patch("free_gacha.classify_state")
    def test_click_verification_checks_after_one_two_then_three_seconds(
        self,
        classify_state: MagicMock,
        safe_capture_client: MagicMock,
        sleep: MagicMock,
        _click_ratio: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        classify_state.side_effect = [
            ("real_home", {}),
            ("real_home", {}),
            ("real_home", {}),
            ("gacha_page", {}),
        ]
        safe_capture_client.return_value = image
        logger = MagicMock()
        logger.save_image.return_value = Path("verify.png")

        ok, state, _image, _reason = click_with_fixed_retry(
            123,
            image,
            "home_gacha",
            verify=lambda candidate, _next_image: candidate == "gacha_page",
            description="open gacha",
            dry_run=False,
            logger=logger,
            attempts=1,
        )

        self.assertTrue(ok)
        self.assertEqual(state, "gacha_page")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 3])

    @patch("free_gacha._click_ratio")
    @patch("free_gacha.time.sleep")
    @patch("free_gacha.safe_capture_client")
    @patch("free_gacha.classify_state")
    def test_click_verification_waits_through_unknown_and_ambiguous_transitions(
        self,
        classify_state: MagicMock,
        safe_capture_client: MagicMock,
        sleep: MagicMock,
        click_ratio: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        classify_state.side_effect = [
            ("plaza", {}),
            ("unknown", {}),
            ("ambiguous_home", {}),
            ("real_home", {}),
        ]
        safe_capture_client.return_value = image
        logger = MagicMock()
        logger.save_image.return_value = Path("verify.png")

        ok, state, _image, _reason = click_with_fixed_retry(
            123,
            image,
            "plaza_home",
            verify=lambda candidate, _next_image: candidate == "real_home",
            description="return home from plaza",
            dry_run=False,
            logger=logger,
            attempts=1,
            wait_on_unknown_transition=True,
        )

        self.assertTrue(ok)
        self.assertEqual(state, "real_home")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 3])
        click_ratio.assert_called_once()

    @patch("free_gacha._click_ratio")
    @patch("free_gacha.safe_capture_client")
    @patch("free_gacha.classify_state")
    def test_click_verification_extends_while_the_screen_keeps_transitioning(
        self,
        classify_state: MagicMock,
        safe_capture_client: MagicMock,
        click_ratio: MagicMock,
    ) -> None:
        source = Image.new("RGB", (2000, 1000), color=(180, 180, 180))
        safe_capture_client.side_effect = [
            Image.new("RGB", source.size, color=(140, 140, 140)),
            Image.new("RGB", source.size, color=(90, 90, 90)),
            Image.new("RGB", source.size, color=(30, 30, 30)),
        ]
        states = iter(("real_home", "real_home", "real_home", "gacha_page"))
        clock = [0.0]
        classify_calls = [0]

        def classify(_image: Image.Image) -> tuple[str, dict[str, object]]:
            classify_calls[0] += 1
            if classify_calls[0] > 1:
                clock[0] += 15.0
            return next(states), {}

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        classify_state.side_effect = classify
        logger = MagicMock()
        logger.save_image.return_value = Path("verify.png")
        with (
            patch("free_gacha.time.monotonic", side_effect=lambda: clock[0]),
            patch("free_gacha.time.sleep", side_effect=sleep),
        ):
            ok, state, _image, _reason = click_with_fixed_retry(
                123,
                source,
                "home_gacha",
                verify=lambda candidate, _next_image: candidate == "gacha_page",
                description="open gacha",
                dry_run=False,
                logger=logger,
                verify_timeout=20.0,
                attempts=2,
                extend_on_visual_progress=True,
            )

        self.assertTrue(ok)
        self.assertEqual(state, "gacha_page")
        click_ratio.assert_called_once()

    @patch("free_gacha._click_ratio")
    @patch("free_gacha.safe_capture_client")
    @patch("free_gacha.classify_state", return_value=("real_home", {}))
    def test_click_verification_stops_at_its_ten_second_limit(
        self,
        _classify_state: MagicMock,
        safe_capture_client: MagicMock,
        _click_ratio: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.return_value = image
        logger = MagicMock()
        logger.save_image.return_value = Path("verify.png")
        clock = [0.0]
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        with (
            patch("free_gacha.time.monotonic", side_effect=lambda: clock[0]),
            patch("free_gacha.time.sleep", side_effect=sleep),
        ):
            ok, state, _image, _reason = click_with_fixed_retry(
                123,
                image,
                "home_gacha",
                verify=lambda _candidate, _next_image: False,
                description="open gacha",
                dry_run=False,
                logger=logger,
                verify_timeout=10.0,
                attempts=1,
            )

        self.assertFalse(ok)
        self.assertEqual(state, "real_home")
        self.assertEqual(sleeps, [1, 2, 3, 4])
        self.assertEqual(clock[0], 10.0)

    @patch("free_gacha.safe_capture_client")
    @patch("free_gacha.classify_state")
    def test_loading_state_suspends_the_wait_limit(
        self,
        classify_state: MagicMock,
        safe_capture_client: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        classify_state.side_effect = [
            ("loading", {}),
            ("loading", {}),
            ("real_home", {}),
        ]
        safe_capture_client.return_value = image
        clock = [0.0]
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        with (
            patch("free_gacha.time.monotonic", side_effect=lambda: clock[0]),
            patch("free_gacha.time.sleep", side_effect=sleep),
        ):
            state, _image = wait_for_state(
                123,
                MagicMock(),
                expected={"real_home"},
                timeout=2.0,
                interval=10.0,
                label="loading-test",
            )

        self.assertEqual(state, "real_home")
        self.assertEqual(sleeps, [1, 2])

    @patch("game_text_recognition._recognize_label_groups")
    def test_split_gameplay_cartridge_labels_are_recognized(
        self,
        recognize_label_groups: MagicMock,
    ) -> None:
        recognized = [
            "店长游戏卡",
            "剧情游戏卡",
            "角色游戏卡",
            "战斗玩法游戏卡带",
            "生活玩法游戏卡带",
            "活动游戏卡",
        ]
        recognize_label_groups.return_value = (
            {"bottom_bar": recognized},
            {"bottom_bar": recognized},
            None,
        )

        matched, details = recognize_arena_cartridge_bar_labels(
            Image.new("RGB", (2000, 1000))
        )

        self.assertTrue(matched)
        self.assertIn("战斗玩法游戏卡带", details["matches"]["bottom_bar"])

    def test_equipment_gacha_animation_allows_recorded_top_right_brightness(self) -> None:
        self.assertTrue(
            _is_reveal_animation_like(
                {
                    "edge_ratio": 0.032396,
                    "bright_ratio": 0.100353,
                },
                {
                    "mid_ratio": 0.709,
                    "edge_ratio": 0.014631,
                },
            )
        )

    def test_unknown_startup_pages_stop_after_three_confirming_frames(self) -> None:
        self.assertEqual(MAX_UNKNOWN_ENTRY_FRAMES, 3)

    @patch("game_text_recognition._recognize_label_groups")
    def test_entry_progress_percentage_is_waited(
        self,
        recognize_label_groups: MagicMock,
    ) -> None:
        recognize_label_groups.return_value = (
            {
                "status": [],
                "confirm_button": [],
                "download_progress": ["0%"],
            },
            {
                "status": [],
                "confirm_button": [],
                "download_progress": [],
            },
            None,
        )

        state, _details = recognize_entry_status(Image.new("RGB", (2000, 1000)))

        self.assertEqual(state, "startup_waiting")

    @patch("game_text_recognition._recognize_label_groups")
    def test_pickup_promotion_text_is_actionable(
        self,
        recognize_label_groups: MagicMock,
    ) -> None:
        recognize_label_groups.return_value = (
            {
                "status": [],
                "confirm_button": [],
                "download_progress": ["推出全新Pickup抽抽乐"],
            },
            {
                "status": [],
                "confirm_button": [],
                "download_progress": [],
            },
            None,
        )

        state, _details = recognize_entry_status(Image.new("RGB", (2000, 1000)))

        self.assertEqual(state, "startup_promotion")

    def test_bright_promotional_screen_is_actionable_during_entry(self) -> None:
        image = Image.new("RGB", (2000, 1000), "white")
        ImageDraw.Draw(image).rectangle((800, 250, 1000, 750), fill="black")

        state, details, entry_state, entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "entry_screen")
        self.assertEqual(details["classification_rule"], "bright_scene")
        self.assertEqual(entry_state, "startup_promotion")
        self.assertEqual(entry_details["source"], "bright_startup_promotion")

    def test_startup_promotion_click_reuses_the_entry_safe_point(self) -> None:
        self.assertEqual(CLICK_POINTS["startup_promotion"], TOUCH_CLICK)

    def test_startup_promotion_requires_a_meaningful_visual_change(self) -> None:
        before = Image.new("RGB", (2000, 1000), "white")
        ImageDraw.Draw(before).rectangle((800, 250, 1000, 750), fill="black")
        after = Image.new("RGB", before.size, "black")

        self.assertFalse(
            startup_promotion_transition_succeeded(
                before,
                "gacha_animation",
                before.copy(),
            )
        )
        self.assertTrue(
            startup_promotion_transition_succeeded(
                before,
                "gacha_animation",
                after,
            )
        )

    @patch("daily_automation.set_mute", return_value=2)
    def test_game_audio_mute_records_active_sessions(self, set_mute: MagicMock) -> None:
        logger = MagicMock()

        self.assertTrue(mute_game_audio(logger, attempt=1))

        set_mute.assert_called_once_with(True)
        logger.event.assert_called_once_with(
            action="mute_game_audio",
            result="success",
            attempt=1,
            muted_sessions=2,
        )

    @patch("daily_automation.set_mute", return_value=0)
    def test_game_audio_mute_waits_for_a_late_audio_session(self, set_mute: MagicMock) -> None:
        logger = MagicMock()

        self.assertFalse(mute_game_audio(logger, attempt=2))

        set_mute.assert_called_once_with(True)
        logger.event.assert_called_once_with(
            action="mute_game_audio",
            result="waiting",
            attempt=2,
            reason="BrownDust II audio session is not available yet",
        )

    def test_animation_skip_uses_the_top_right_fast_forward_control(self) -> None:
        x, y = CLICK_POINTS["skip_animation"]
        self.assertTrue(0.90 <= x <= 0.96)
        self.assertTrue(0.02 <= y <= 0.09)

    def test_animation_skip_waits_through_an_unknown_transition_frame(self) -> None:
        image = Image.new("RGB", (2000, 1000))
        logger = MagicMock()

        with (
            patch("free_gacha._click_ratio") as click_ratio,
            patch(
                "free_gacha.wait_for_state",
                side_effect=[
                    ("unknown", image),
                    ("gacha_result", image),
                ],
            ) as wait_for_state,
        ):
            ok, state, _image, reason = skip_gacha_animation(
                123,
                image,
                dry_run=False,
                logger=logger,
                interval=0.0,
                effect_timeout=1.0,
            )

        self.assertTrue(ok)
        self.assertEqual(state, "gacha_result")
        self.assertEqual(reason, "skip gacha animation succeeded on attempt 2")
        self.assertEqual(click_ratio.call_count, 2)
        self.assertEqual(wait_for_state.call_count, 2)

    def test_return_battlefield_click_stays_in_the_bottom_right_tile(self) -> None:
        x, y = CLICK_POINTS["home_return_battlefield"]
        self.assertTrue(0.73 <= x <= 0.84)
        self.assertTrue(0.87 <= y <= 0.97)

    def test_arena_clicks_stay_in_recorded_controls(self) -> None:
        expected = {
            "arena_home": ((0.91, 0.96), (0.03, 0.08)),
            "plaza_cartridge": ((0.38, 0.45), (0.89, 0.97)),
            "cartridge_gameplay_tab": ((0.46, 0.58), (0.77, 0.86)),
            "cartridge_first_gameplay": ((0.03, 0.13), (0.85, 0.95)),
            "arena_pool": ((0.30, 0.53), (0.48, 0.70)),
            "arena_auto_battle": ((0.75, 0.83), (0.86, 0.95)),
            "arena_auto_max": ((0.60, 0.69), (0.54, 0.64)),
            "arena_auto_start": ((0.48, 0.62), (0.70, 0.80)),
            "arena_repeat_result_close": ((0.58, 0.66), (0.24, 0.33)),
            "arena_victory_leave": ((0.84, 0.94), (0.89, 0.98)),
            "arena_rank_confirm": ((0.46, 0.54), (0.89, 0.98)),
        }
        for key, (x_range, y_range) in expected.items():
            x, y = CLICK_POINTS[key]
            self.assertTrue(x_range[0] <= x <= x_range[1], key)
            self.assertTrue(y_range[0] <= y <= y_range[1], key)

    def test_arena_pool_detector_tracks_camera_shift_and_partial_visibility(self) -> None:
        shifted = Image.new("RGB", (1000, 600))
        ImageDraw.Draw(shifted).ellipse((700, 295, 860, 375), fill=(210, 20, 45))
        partial = Image.new("RGB", (1000, 600))
        ImageDraw.Draw(partial).ellipse((920, 190, 1080, 270), fill=(210, 20, 45))

        shifted_point = detect_arena_pool_click(shifted)
        partial_point = detect_arena_pool_click(partial)

        self.assertIsNotNone(shifted_point)
        self.assertIsNotNone(partial_point)
        assert shifted_point is not None
        assert partial_point is not None
        self.assertAlmostEqual(shifted_point[0], 0.78, delta=0.02)
        self.assertAlmostEqual(shifted_point[1], 0.558, delta=0.02)
        self.assertGreater(partial_point[0], 0.95)

    def test_arena_pool_detector_matches_lobby_but_not_battle_prep(self) -> None:
        with Image.open(FIXTURES / "arena-lobby-2567x1446.png") as lobby:
            lobby_point = detect_arena_pool_click(lobby)
        with Image.open(FIXTURES / "arena-battle-prep-2567x1446.png") as battle_prep:
            battle_prep_point = detect_arena_pool_click(battle_prep)

        self.assertIsNotNone(lobby_point)
        assert lobby_point is not None
        self.assertAlmostEqual(lobby_point[0], 0.422, delta=0.02)
        self.assertAlmostEqual(lobby_point[1], 0.595, delta=0.02)
        self.assertIsNone(battle_prep_point)

    @patch("daily_arena.time.sleep")
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.recognize_arena_cartridge_labels")
    def test_cartridge_collection_waits_for_title_before_returning(
        self,
        recognize_cartridge: MagicMock,
        capture_client: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        loading_image = Image.new("RGB", (80, 45), color=(10, 10, 10))
        ready_image = Image.new("RGB", (80, 45), color=(20, 20, 20))
        recognize_cartridge.side_effect = [
            (True, {"loading": True, "ready": False}),
            (True, {"loading": False, "ready": True}),
        ]
        capture_client.return_value = ready_image
        logger = MagicMock()

        ready, image, reason = wait_for_cartridge_collection_ready(
            123,
            loading_image,
            logger,
        )

        self.assertTrue(ready)
        self.assertIs(image, ready_image)
        self.assertEqual(reason, "cartridge collection finished loading")
        capture_client.assert_called_once_with(123, logger=logger)

    @patch("daily_arena.time.sleep")
    @patch("daily_arena._click_ratio")
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state")
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_cartridge_route_confirms_rank_drop_before_entering_lobby(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        classify: MagicMock,
        click_with_retry: MagicMock,
        click_ratio: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        plaza_image = Image.new("RGB", (80, 45), color=(10, 10, 10))
        bar_image = Image.new("RGB", (80, 45), color=(20, 20, 20))
        gameplay_image = Image.new("RGB", (80, 45), color=(30, 30, 30))
        rank_image = Image.new("RGB", (80, 45), color=(40, 40, 40))
        lobby_image = Image.new("RGB", (80, 45), color=(50, 50, 50))
        capture_client.side_effect = [plaza_image, rank_image, lobby_image]
        classify.side_effect = [
            ("plaza", {}),
            ("arena_rank_change", {}),
            ("arena_lobby", {}),
        ]
        click_with_retry.side_effect = [
            (True, "arena_cartridge_bar", bar_image, "opened cartridge bar"),
            (True, "arena_cartridge_bar", gameplay_image, "selected gameplay tab"),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_arena_from_plaza(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertIn("arena lobby reached", reason)
        click_names = [call.args[2] for call in click_ratio.call_args_list]
        self.assertEqual(
            click_names,
            ["cartridge_first_gameplay", "arena_rank_confirm"],
        )

    @patch("daily_arena.leave_cartridge_collection")
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state", return_value=("real_home", {}))
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_battlefield_entry_continues_from_cartridge_collection(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify: MagicMock,
        click_with_retry: MagicMock,
        leave_collection: MagicMock,
    ) -> None:
        home_image = Image.new("RGB", (80, 45), color=(10, 10, 10))
        collection_image = Image.new("RGB", (80, 45), color=(20, 20, 20))
        plaza_image = Image.new("RGB", (80, 45), color=(30, 30, 30))
        capture_client.return_value = home_image
        click_with_retry.return_value = (
            True,
            "arena_cartridge_collection",
            collection_image,
            "opened cartridge collection",
        )
        leave_collection.return_value = (
            True,
            "plaza",
            plaza_image,
            "returned to plaza",
        )

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_battlefield(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to plaza")
        verify = click_with_retry.call_args.kwargs["verify"]
        self.assertTrue(verify("arena_cartridge_collection", collection_image))
        self.assertTrue(click_with_retry.call_args.kwargs["wait_on_unknown_transition"])
        leave_collection.assert_called_once()

    @patch("builtins.print")
    def test_arena_pool_loading_transition_waits_for_battle_prep(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1000, 600))
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_arena.find_game_window", return_value=123),
            patch("daily_arena.safe_capture_client", return_value=image),
            patch("daily_arena.classify_state", return_value=("arena_lobby", {})),
            patch(
                "daily_arena.click_with_fixed_retry",
                return_value=(True, "loading", image, "portal click reached loading"),
            ),
            patch(
                "daily_arena.wait_for_state",
                return_value=("arena_battle_prep", image),
            ) as wait_for_state,
        ):
            ok, reason = enter_battle_prep(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "entered arena battle preparation after loading")
        wait_for_state.assert_called_once()

    @patch("free_gacha.click_client")
    def test_arena_pool_click_uses_detected_position(
        self,
        click_client: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1000, 600))
        ImageDraw.Draw(image).ellipse((700, 295, 860, 375), fill=(210, 20, 45))

        _click_ratio(
            123,
            image,
            "arena_pool",
            dry_run=False,
            logger=MagicMock(annotate_clicks=False),
        )

        x, y = click_client.call_args.args[1:]
        self.assertAlmostEqual(x, 780, delta=20)
        self.assertAlmostEqual(y, 335, delta=15)

    def test_gameplay_cartridge_tab_highlight_is_detected(self) -> None:
        with Image.open(FIXTURES / "arena-cartridge-bar-gameplay-selected-annotated-2048x1200.png") as image:
            self.assertTrue(is_gameplay_tab_selected(image))

    def test_gacha_category_clicks_stay_on_icons_and_retry_at_an_alternate_point(self) -> None:
        icon_bands = {
            "costume_tab": (0.270, 0.305),
            "gear_tab": (0.370, 0.405),
        }
        for key, (top, bottom) in icon_bands.items():
            primary = CLICK_POINTS[key]
            retry = RETRY_CLICK_POINTS[key]
            self.assertTrue(top <= primary[1] <= bottom)
            self.assertTrue(top <= retry[1] <= bottom)
            self.assertNotEqual(primary, retry)

    @patch("free_gacha.click_client")
    def test_gacha_category_retry_uses_the_alternate_icon_point(self, click_client: MagicMock) -> None:
        logger = MagicMock()
        logger.annotate_clicks = False
        image = Image.new("RGB", (2000, 1000))

        _click_ratio(123, image, "gear_tab", dry_run=False, logger=logger, attempt=2)

        click_client.assert_called_once_with(123, 172, 395)

    def test_daily_run_can_resume_every_supported_gacha_state(self) -> None:
        self.assertTrue(
            {
                "gacha_page",
                "confirm_free_gacha",
                "gacha_animation",
                "gacha_result",
                "gacha_item_overlay",
            }
            <= DAILY_READY_STATES
        )

    def test_daily_run_can_resume_from_arena_lobby_before_returning_home(self) -> None:
        self.assertIn("arena_lobby", DAILY_READY_STATES)

    def test_daily_run_can_resume_from_business_management_before_returning_home(self) -> None:
        self.assertTrue(
            {
                "business_management_dialog",
                "reward_overlay",
                "restaurant_home",
                "restaurant_regular_customer_mode",
                "restaurant_regular_customer_notes",
            }
            <= DAILY_READY_STATES
        )

    def test_regular_customer_note_red_dot_is_detected(self) -> None:
        with Image.open(FIXTURES / "restaurant-regular-customer-mode-2567x1446.png") as image:
            found, details = detect_regular_customer_note_notification(image)

        self.assertTrue(found)
        self.assertGreaterEqual(details["red_pixels"], 80)

    def test_regular_customer_note_without_red_dot_is_skipped(self) -> None:
        with Image.open(FIXTURES / "restaurant-home-2567x1446.png") as image:
            found, details = detect_regular_customer_note_notification(image)

        self.assertFalse(found)
        self.assertLess(details["red_pixels"], 80)

    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_uses_the_arena_home_button_from_arena_lobby(
        self,
        _print: MagicMock,
        _find_game_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.side_effect = [image, image]
        classify_state.side_effect = [("arena_lobby", {}), ("real_home", {})]
        click_with_fixed_retry.return_value = (True, "real_home", image, "returned home")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(click_with_fixed_retry.call_args.args[2], "arena_home")

    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_waits_once_through_plaza_unknown_transition(
        self,
        _print: MagicMock,
        _find_game_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.side_effect = [image, image]
        classify_state.side_effect = [("plaza", {}), ("real_home", {})]
        click_with_fixed_retry.return_value = (True, "real_home", image, "returned home")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=120.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(click_with_fixed_retry.call_args.args[2], "plaza_home")
        self.assertEqual(click_with_fixed_retry.call_args.kwargs["verify_timeout"], 120.0)
        self.assertEqual(click_with_fixed_retry.call_args.kwargs["attempts"], 1)
        self.assertTrue(
            click_with_fixed_retry.call_args.kwargs["wait_on_unknown_transition"]
        )

    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_closes_a_leftover_business_management_dialog(
        self,
        _print: MagicMock,
        _find_game_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.side_effect = [image, image]
        classify_state.side_effect = [("business_management_dialog", {}), ("real_home", {})]
        click_with_fixed_retry.return_value = (True, "real_home", image, "closed dialog")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(click_with_fixed_retry.call_args.args[2], "business_management_cancel")

    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_returns_from_gacha_result_through_gacha_page(
        self,
        _print: MagicMock,
        _find_game_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.side_effect = [image, image, image]
        classify_state.side_effect = [
            ("gacha_result", {}),
            ("gacha_page", {}),
            ("real_home", {}),
        ]
        click_with_fixed_retry.side_effect = [
            (True, "gacha_page", image, "returned to gacha page"),
            (True, "real_home", image, "returned home"),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(
            [call.args[2] for call in click_with_fixed_retry.call_args_list],
            ["result_back", "result_back"],
        )

    @patch("builtins.print")
    def test_ensure_home_timeout_tracks_stalled_progress_not_total_duration(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        clock = [0.0]
        states = iter(("arena_lobby", "home_overlay", "real_home"))
        click_states = iter(("home_overlay", "real_home"))

        def capture_client(*_args: object, **_kwargs: object) -> Image.Image:
            clock[0] += 4.0
            return image

        def click_success(*_args: object, **_kwargs: object) -> tuple[bool, str, Image.Image, str]:
            clock[0] += 4.0
            next_state = next(click_states)
            return True, next_state, image, "verified progress"

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("open_game.find_game_window", return_value=123),
            patch("daily_automation.time.monotonic", side_effect=lambda: clock[0]),
            patch("daily_automation.safe_capture_client", side_effect=capture_client),
            patch(
                "daily_automation.classify_state",
                side_effect=lambda _image: (next(states), {}),
            ),
            patch("daily_automation.click_with_fixed_retry", side_effect=click_success),
        ):
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertGreater(clock[0], 5.0)

    @patch("builtins.print")
    def test_ensure_home_waits_past_limit_while_game_is_loading(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        clock = [0.0]

        def capture_client(*_args: object, **_kwargs: object) -> Image.Image:
            clock[0] += 3.0
            return image

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("open_game.find_game_window", return_value=123),
            patch("daily_automation.time.monotonic", side_effect=lambda: clock[0]),
            patch("daily_automation.time.sleep"),
            patch("daily_automation.safe_capture_client", side_effect=capture_client),
            patch(
                "daily_automation.classify_state",
                side_effect=[("loading", {}), ("loading", {}), ("real_home", {})],
            ),
        ):
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertGreater(clock[0], 5.0)

    @patch("builtins.print")
    def test_enter_game_clicks_startup_promotions_until_home(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        contexts = iter(
            (
                ("entry_screen", {}, "startup_promotion", {}),
                ("entry_screen", {}, "startup_promotion", {}),
                ("real_home", {}, "unknown", {}),
            )
        )
        click_results = iter(
            (
                (True, "gacha_animation", image, "advanced first promotion"),
                (True, "real_home", image, "advanced last promotion"),
            )
        )

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_automation.find_game_window", return_value=123),
            patch("daily_automation.open_game", return_value=123),
            patch("daily_automation.mute_game_audio", return_value=True),
            patch("daily_automation.time.sleep"),
            patch("daily_automation.safe_capture_client", return_value=image),
            patch(
                "daily_automation.classify_daily_entry_context",
                side_effect=lambda _image: next(contexts),
            ),
            patch(
                "daily_automation.click_with_fixed_retry",
                side_effect=lambda *_args, **_kwargs: next(click_results),
            ) as click_with_fixed_retry,
        ):
            ok, reason = enter_game_logged(
                timeout=30.0,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "game is ready at state=real_home")
        self.assertEqual(click_with_fixed_retry.call_count, 2)
        self.assertTrue(
            all(
                call.args[2] == "startup_promotion"
                for call in click_with_fixed_retry.call_args_list
            )
        )

    @patch("builtins.print")
    def test_cold_launch_promotion_can_resume_directly_in_arena_lobby(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        contexts = iter(
            (
                ("entry_screen", {}, "startup_promotion", {}),
                ("arena_lobby", {}, "unknown", {}),
            )
        )

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_automation.find_game_window", return_value=0),
            patch("daily_automation.open_game", return_value=123),
            patch("daily_automation.mute_game_audio", return_value=True),
            patch("daily_automation.time.sleep"),
            patch("daily_automation.safe_capture_client", return_value=image),
            patch(
                "daily_automation.classify_daily_entry_context",
                side_effect=lambda _image: next(contexts),
            ),
            patch(
                "daily_automation.click_with_fixed_retry",
                return_value=(True, "loading", image, "advanced promotion"),
            ),
        ):
            ok, reason = enter_game_logged(
                timeout=30.0,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "game is ready at state=arena_lobby")

    @patch("builtins.print")
    def test_enter_game_accepts_terms_before_continuing(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        contexts = iter(
            (
                ("terms_agreement", {}, "unknown", {}),
                ("real_home", {}, "unknown", {}),
            )
        )

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_automation.find_game_window", return_value=123),
            patch("daily_automation.open_game", return_value=123),
            patch("daily_automation.mute_game_audio", return_value=True),
            patch("daily_automation.time.sleep"),
            patch("daily_automation.safe_capture_client", return_value=image),
            patch(
                "daily_automation.classify_daily_entry_context",
                side_effect=lambda _image: next(contexts),
            ),
            patch("daily_automation._click_logged_ratio") as click_logged,
            patch(
                "daily_automation.click_with_fixed_retry",
                return_value=(True, "loading", image, "accepted terms"),
            ) as click_with_retry,
        ):
            ok, reason = enter_game_logged(
                timeout=30.0,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "game is ready at state=real_home")
        self.assertEqual(click_logged.call_args.kwargs["key"], "terms_all_agree")
        self.assertEqual(click_with_retry.call_args.args[2], "terms_start")

    @patch("builtins.print")
    def test_enter_game_timeout_tracks_progress_not_total_duration(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        clock = [0.0]
        contexts = iter(
            (
                ("loading", {}, "loading", {}),
                ("returnable_scene", {}, "unknown", {}),
                ("real_home", {}, "unknown", {}),
            )
        )

        def capture_client(*_args: object, **_kwargs: object) -> Image.Image:
            clock[0] += 4.0
            return image

        def click_success(*_args: object, **_kwargs: object) -> tuple[bool, str, Image.Image, str]:
            clock[0] += 4.0
            return True, "real_home", image, "verified progress"

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_automation.find_game_window", return_value=123),
            patch("daily_automation.open_game", return_value=123),
            patch("daily_automation.mute_game_audio", return_value=True),
            patch("daily_automation.time.monotonic", side_effect=lambda: clock[0]),
            patch("daily_automation.time.sleep"),
            patch("daily_automation.safe_capture_client", side_effect=capture_client),
            patch("daily_automation.classify_daily_entry_context", side_effect=lambda _image: next(contexts)),
            patch("daily_automation.click_with_fixed_retry", side_effect=click_success),
        ):
            ok, reason = enter_game_logged(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "game is ready at state=real_home")
        self.assertGreater(clock[0], 5.0)

    @patch("builtins.print")
    def test_free_gacha_timeout_tracks_progress_not_total_duration(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        clock = [0.0]
        states = iter(
            (
                "real_home",
                "gacha_page",
                "gacha_page",
                "confirm_free_gacha",
                "gacha_animation",
                "gacha_result",
                "gacha_page",
            )
        )
        click_states = iter(
            (
                "gacha_page",
                "confirm_free_gacha",
                "gacha_animation",
                "gacha_page",
            )
        )

        def capture_client(*_args: object, **_kwargs: object) -> Image.Image:
            clock[0] += 3.0
            return image

        def click_success(*_args: object, **_kwargs: object) -> tuple[bool, str, Image.Image, str]:
            clock[0] += 3.0
            return True, next(click_states), image, "verified progress"

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("free_gacha.find_game_window", return_value=123),
            patch("free_gacha.time.monotonic", side_effect=lambda: clock[0]),
            patch("free_gacha.safe_capture_client", side_effect=capture_client),
            patch("free_gacha.classify_state", side_effect=lambda _image: (next(states), {})),
            patch("free_gacha.detect_selected_gacha_target", return_value="costume"),
            patch("free_gacha.detect_all_free_gacha_availability", return_value=("available", {})),
            patch("free_gacha.click_with_fixed_retry", side_effect=click_success),
            patch(
                "free_gacha.skip_gacha_animation",
                return_value=(True, "gacha_result", image, "skipped animation"),
            ),
        ):
            result = run_free_gacha(
                targets=["costume"],
                timeout=5.0,
                interval=0.0,
                dry_run=False,
                test_mode=False,
                log_root=Path(temporary),
            )

        self.assertEqual(result.reason, "all requested free gacha targets completed")
        self.assertGreater(clock[0], 5.0)

    def test_used_free_gacha_target_is_skipped_without_clicking_paid_draw(self) -> None:
        image = Image.new("RGB", (2000, 1000))

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("free_gacha.find_game_window", return_value=123),
            patch("free_gacha.safe_capture_client", return_value=image),
            patch("free_gacha.classify_state", return_value=("gacha_page", {})),
            patch("free_gacha.detect_selected_gacha_target", return_value="costume"),
            patch(
                "free_gacha.detect_all_free_gacha_availability",
                return_value=("used", {}),
            ),
            patch("free_gacha.click_with_fixed_retry") as click_with_fixed_retry,
        ):
            result = run_free_gacha(
                targets=["costume"],
                timeout=5.0,
                interval=0.0,
                dry_run=False,
                test_mode=False,
                log_root=Path(temporary),
            )

        self.assertEqual(result.reason, "all requested free gacha targets completed")
        click_with_fixed_retry.assert_not_called()

    def test_used_free_gacha_detection_matches_recorded_button_region(self) -> None:
        availability = _resolve_all_free_gacha_availability(
            False,
            {"available": True},
            {
                "edge_ratio": 0.005676,
                "bright_ratio": 0.006589,
            },
        )

        self.assertEqual(availability, "used")

    def test_state_classification_reuses_one_ocr_pass_per_screenshot(self) -> None:
        ocr_result = MagicMock(boxes=None, txts=None, scores=None)
        ocr_engine = MagicMock(return_value=ocr_result)

        with Image.open(FIXTURES / "gacha-page-3421x1927.png") as image:
            with patch("game_text_recognition._ocr_engine", return_value=ocr_engine):
                classify_state(image)

        self.assertEqual(ocr_engine.call_count, 1)

    def test_dark_animated_gacha_page_is_recognized_from_fixed_text(self) -> None:
        with Image.open(
            FIXTURES / "gacha-page-dark-animation-2567x1446.png"
        ) as image:
            state, details = classify_state(image)

        self.assertGreater(details["full"]["dark_ratio"], 0.65)
        self.assertEqual(state, "gacha_page")
        self.assertTrue(details["gacha_page_text"]["matches"]["title"])
        self.assertTrue(details["gacha_page_text"]["matches"]["tabs"])

    def test_positioned_text_interfaces_are_recognized_without_visual_prefilters(
        self,
    ) -> None:
        cases = {
            "business-management-dialog-2567x1446.png": "business_management_dialog",
            "business-management-reward-2567x1446.png": "reward_overlay",
            "restaurant-loading-2567x1446.png": "restaurant_loading",
            "restaurant-home-2567x1446.png": "restaurant_home",
            "restaurant-regular-customer-mode-2567x1446.png": "restaurant_regular_customer_mode",
            "restaurant-regular-customer-notes-2567x1446.png": "restaurant_regular_customer_notes",
            "restaurant-regular-customer-reward-2567x1446.png": "reward_overlay",
            "quick-hunt-map.png": "quick_hunt_map",
            "quick-hunt-setup.png": "quick_hunt_setup",
            "quick-hunt-result.png": "reward_overlay",
            "gacha-confirm-3421x1927.png": "confirm_free_gacha",
            "gacha-item-detail-3421x1927.png": "gacha_item_overlay",
            "arena-auto-battle-dialog-2567x1446.png": "arena_auto_battle_dialog",
            "arena-repeat-battle-result-2567x1446.png": "arena_repeat_battle_result",
            "arena-victory-result-2567x1446.png": "arena_victory_result",
            "arena-rank-change-2567x1446.png": "arena_rank_change",
        }

        for fixture, expected in cases.items():
            with self.subTest(fixture=fixture):
                with Image.open(FIXTURES / fixture) as image:
                    state, _details = classify_state(image)
                self.assertEqual(state, expected)

    def test_season_reward_overlay_uses_the_unified_blocking_state(self) -> None:
        with Image.open(FIXTURES / "arena-season-reward-overlay-2567x1446.png") as overlay:
            overlay_state, _details = classify_state(overlay)
        with Image.open(FIXTURES / "arena-lobby-2567x1446.png") as lobby:
            lobby_state, _details = classify_state(lobby)

        self.assertEqual(overlay_state, "blocking_ad_overlay")
        self.assertEqual(lobby_state, "arena_lobby")

    def test_v2318_touch_screen_is_actionable(self) -> None:
        with Image.open(FIXTURES / "entry-touch-ready-v2318.png") as image:
            state, details = recognize_daily_entry_state(image)

        self.assertEqual(state, "touch_ready")
        self.assertEqual(details["source"], "ocr")

    def test_capacity_check_screen_is_loading_not_actionable(self) -> None:
        with Image.open(FIXTURES / "entry-loading-capacity-v2318.png") as image:
            state, details = recognize_daily_entry_state(image)

        self.assertEqual(state, "download_waiting")
        self.assertEqual(details["source"], "ocr")

    def test_download_dialog_button_overrides_capacity_progress_text(self) -> None:
        with Image.open(FIXTURES / "entry-download-confirm-v2318-2048x1200.png") as image:
            state, details = recognize_daily_entry_state(image)

        self.assertEqual(state, "download_confirmation")
        self.assertEqual(details["source"], "ocr")

    def test_download_click_stays_on_the_lower_confirmation_button(self) -> None:
        x, y = DOWNLOAD_CONFIRM_CLICK
        self.assertTrue(0.50 <= x <= 0.60)
        self.assertTrue(0.69 <= y <= 0.76)

    def test_bottom_download_progress_is_a_waiting_state(self) -> None:
        with Image.open(FIXTURES / "entry-downloading-v2318-2048x1128.png") as image:
            state, details = recognize_daily_entry_state(image)

        self.assertEqual(state, "download_waiting")
        self.assertEqual(details["source"], "ocr")

    def test_blank_cold_start_capture_is_waited(self) -> None:
        with Image.open(FIXTURES / "entry-blank-white-3421x1927.png") as image:
            state, details = classify_state(image)

        self.assertEqual(state, "loading")
        self.assertTrue(details["low_information_frame"])

    def test_game_starting_screen_is_waited(self) -> None:
        with Image.open(FIXTURES / "entry-game-starting-v2318-3421x1927.png") as image:
            state, _details, entry_state, entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "entry_screen")
        self.assertEqual(entry_state, "startup_waiting")
        self.assertEqual(entry_details["source"], "ocr")

    def test_cold_launch_requires_an_entry_screen_before_business_states(self) -> None:
        self.assertFalse(
            can_finish_entry_phase(
                "gacha_animation",
                requires_entry_screen=True,
                touch_screen_seen=False,
            )
        )
        self.assertTrue(
            can_finish_entry_phase(
                "gacha_animation",
                requires_entry_screen=True,
                touch_screen_seen=True,
            )
        )
        self.assertTrue(
            can_finish_entry_phase(
                "gacha_animation",
                requires_entry_screen=False,
                touch_screen_seen=False,
            )
        )

    def test_story_scene_home_button_is_only_a_return_fallback(self) -> None:
        with Image.open(FIXTURES / "entry-story-scene-home-button-v2318.png") as image:
            entry_state, _entry_details = recognize_daily_entry_state(image)
            returnable, details = recognize_return_home_control(image)

        self.assertEqual(entry_state, "unknown")
        self.assertTrue(returnable)
        self.assertIn("H", details["matches"]["home_control"])

    def test_today_plaza_home_button_survives_ocr_variation(self) -> None:
        with Image.open(FIXTURES / "entry-plaza-home-button-v2318-20260803.png") as image:
            state, _details, entry_state, _entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "returnable_scene")
        self.assertEqual(entry_state, "unknown")

    def test_resized_plaza_home_button_overrides_animation_heuristic(self) -> None:
        with Image.open(FIXTURES / "entry-plaza-home-button-3421x1927.png") as image:
            state, _details, entry_state, _entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "returnable_scene")
        self.assertEqual(entry_state, "unknown")

    def test_return_home_transition_frame_is_waited_without_a_second_click(self) -> None:
        with Image.open(FIXTURES / "entry-return-home-transition-3421x1927.png") as image:
            state, details, entry_state, _entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "loading")
        self.assertEqual(details["classification_rule"], "no_meaningful_text")
        self.assertEqual(entry_state, "unknown")
        self.assertTrue(return_home_transition_succeeded(state))

    def test_known_blocking_overlay_wins_over_title_image_fallback(self) -> None:
        with Image.open(FIXTURES / "entry-home-signin-overlay-v2318.png") as image:
            state, _details, entry_state, entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "blocking_ad_overlay")
        self.assertEqual(entry_state, "unknown")
        self.assertEqual(entry_details["source"], "deferred")

    def test_changed_stacked_overlay_counts_as_a_successful_dismissal(self) -> None:
        with Image.open(FIXTURES / "entry-home-item-detail-v2318.png") as before:
            with Image.open(FIXTURES / "entry-home-signin-overlay-v2318.png") as after:
                succeeded = overlay_transition_succeeded(before, "blocking_ad_overlay", after)

        self.assertTrue(succeeded)

    def test_gacha_page_is_a_non_clicking_confirm_transition(self) -> None:
        self.assertTrue(is_free_gacha_confirm_transition("gacha_page"))
        self.assertTrue(is_free_gacha_confirm_transition("gacha_animation"))
        self.assertFalse(is_free_gacha_confirm_transition("confirm_free_gacha"))


class CaptureRecoveryTests(unittest.TestCase):
    @patch("free_gacha.time.sleep")
    @patch("free_gacha.user32")
    @patch("free_gacha.capture_client")
    def test_minimized_window_is_restored_before_capture_retry(
        self,
        capture_client: MagicMock,
        user32: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        capture_client.side_effect = [
            Image.new("RGB", (0, 0)),
            Image.new("RGB", (1000, 600)),
        ]
        user32.GetClientRect.return_value = 1
        user32.IsWindow.return_value = 1
        user32.IsWindowVisible.return_value = 1
        user32.IsIconic.return_value = 1

        image = safe_capture_client(123, attempts=2)

        self.assertEqual(image.size, (1000, 600))
        user32.ShowWindowAsync.assert_called_once_with(123, 4)


if __name__ == "__main__":
    unittest.main()
