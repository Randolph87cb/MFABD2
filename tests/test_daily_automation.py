from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import open_game as open_game_module
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
    overlay_transition_succeeded,
    recognize_daily_entry_state,
    return_home_transition_succeeded,
    run_daily,
    startup_promotion_transition_succeeded,
    update_daily_state,
)
from game_text_recognition import recognize_return_home_control
from daily_arena import enter_battle_prep, is_gameplay_tab_selected
from business_management import detect_regular_customer_note_notification
from enter_game import TOUCH_CLICK
from free_gacha import (
    ActionResult,
    CLICK_POINTS,
    RETRY_CLICK_POINTS,
    RunLogger,
    _click_ratio,
    _is_reveal_animation_like,
    classify_state,
    detect_arena_pool_click,
    is_free_gacha_confirm_transition,
    run_free_gacha,
    safe_capture_client,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "recognition"


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

    def test_animation_skip_uses_the_safe_left_margin(self) -> None:
        self.assertEqual(CLICK_POINTS["skip_animation"], CLICK_POINTS["dismiss_overlay"])

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
    def test_ensure_home_stops_after_continuous_inactivity(
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
            patch("daily_automation.classify_state", return_value=("loading", {})),
        ):
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertFalse(ok)
        self.assertEqual(reason, "returning home made no progress for 5 seconds")

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
                "gacha_result",
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
            patch("free_gacha.click_with_fixed_retry", side_effect=click_success),
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

    def test_season_reward_overlay_sequence_is_recorded_as_a_regression(self) -> None:
        with Image.open(FIXTURES / "arena-season-reward-overlay-2567x1446.png") as overlay:
            overlay_state, _details = classify_state(overlay)
        with Image.open(FIXTURES / "arena-lobby-2567x1446.png") as lobby:
            lobby_state, _details = classify_state(lobby)

        self.assertEqual(overlay_state, "home_overlay")
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
            state, _details, entry_state, _entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "unknown")
        self.assertEqual(entry_state, "unknown")
        self.assertTrue(return_home_transition_succeeded(state))

    def test_known_home_overlay_wins_over_title_image_fallback(self) -> None:
        with Image.open(FIXTURES / "entry-home-signin-overlay-v2318.png") as image:
            state, _details, entry_state, entry_details = classify_daily_entry_context(image)

        self.assertEqual(state, "home_overlay")
        self.assertEqual(entry_state, "unknown")
        self.assertEqual(entry_details["source"], "deferred")

    def test_changed_stacked_overlay_counts_as_a_successful_dismissal(self) -> None:
        with Image.open(FIXTURES / "entry-home-item-detail-v2318.png") as before:
            with Image.open(FIXTURES / "entry-home-signin-overlay-v2318.png") as after:
                succeeded = overlay_transition_succeeded(before, "home_overlay", after)

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
