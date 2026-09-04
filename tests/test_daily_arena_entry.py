from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_arena import (
    _ensure_free_only,
    _free_only_toggle_enabled,
    enter_arena_from_plaza,
    enter_battlefield,
    leave_arena_victory,
    maximize_and_start_auto_battle,
    run_daily_arena,
    wait_and_close_repeat_result,
)


class BattlefieldRestaurantRouteTests(unittest.TestCase):
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state", return_value=("real_home", {}))
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_battlefield_entry_uses_restaurant_game_card_shortcut(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        home_image = Image.new("RGB", (2000, 1000))
        restaurant_image = Image.new("RGB", (2000, 1000))
        cartridge_bar_image = Image.new("RGB", (2000, 1000))
        capture_client.return_value = home_image
        click_with_retry.side_effect = [
            (True, "restaurant_home", restaurant_image, "opened restaurant"),
            (True, "arena_cartridge_bar", cartridge_bar_image, "opened game cards"),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_battlefield(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "opened game cards")
        self.assertEqual(
            [call.args[2] for call in click_with_retry.call_args_list],
            ["home_return_battlefield", "restaurant_game_cards"],
        )
        first_verify = click_with_retry.call_args_list[0].kwargs["verify"]
        self.assertTrue(first_verify("restaurant_home", restaurant_image))
        second_verify = click_with_retry.call_args_list[1].kwargs["verify"]
        self.assertTrue(second_verify("arena_cartridge_bar", cartridge_bar_image))

    @patch("daily_arena.time.sleep")
    @patch("daily_arena._click_ratio")
    @patch("daily_arena.is_gameplay_tab_selected", return_value=True)
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state")
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_cartridge_bar_continues_without_clicking_the_plaza_button(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        classify: MagicMock,
        click_with_retry: MagicMock,
        _gameplay_selected: MagicMock,
        click_ratio: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        bar_image = Image.new("RGB", (2000, 1000))
        selected_image = Image.new("RGB", (2000, 1000))
        lobby_image = Image.new("RGB", (2000, 1000))
        capture_client.side_effect = [bar_image, lobby_image]
        classify.side_effect = [("arena_cartridge_bar", {}), ("arena_lobby", {})]
        click_with_retry.return_value = (
            True,
            "arena_cartridge_bar",
            selected_image,
            "selected gameplay category",
        )

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_arena_from_plaza(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertIn("arena lobby reached", reason)
        self.assertEqual(click_with_retry.call_args.args[2], "cartridge_gameplay_tab")
        self.assertEqual(click_ratio.call_args.args[2], "cartridge_first_gameplay")

    @patch("daily_arena.time.sleep")
    @patch("daily_arena._click_ratio")
    @patch("daily_arena.is_gameplay_tab_selected", return_value=True)
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state")
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_cartridge_selection_accepts_direct_battle_preparation(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        classify: MagicMock,
        click_with_retry: MagicMock,
        _gameplay_selected: MagicMock,
        _click_ratio_mock: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        bar_image = Image.new("RGB", (2000, 1000))
        selected_image = Image.new("RGB", (2000, 1000))
        prep_image = Image.new("RGB", (2000, 1000))
        capture_client.side_effect = [bar_image, prep_image]
        classify.side_effect = [("arena_cartridge_bar", {}), ("arena_battle_prep", {})]
        click_with_retry.return_value = (
            True,
            "arena_cartridge_bar",
            selected_image,
            "selected gameplay category",
        )

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_arena_from_plaza(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertIn("battle preparation reached directly", reason)

    def test_full_arena_skips_pool_when_already_at_battle_preparation(self) -> None:
        image = Image.new("RGB", (2000, 1000))
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("daily_arena.enter_battlefield", return_value=(True, "entered battlefield")),
            patch("daily_arena.find_game_window", return_value=123),
            patch("daily_arena.safe_capture_client", return_value=image),
            patch(
                "daily_arena.classify_state",
                side_effect=[("arena_cartridge_bar", {}), ("arena_battle_prep", {})],
            ),
            patch(
                "daily_arena.enter_arena_from_plaza",
                return_value=(True, "battle preparation reached directly"),
            ),
            patch("daily_arena.enter_battle_prep") as enter_battle_prep,
            patch("daily_arena.open_auto_battle", return_value=(True, "opened auto battle")),
            patch(
                "daily_arena.maximize_and_start_auto_battle",
                return_value=(True, "started auto battle"),
            ),
            patch(
                "daily_arena.wait_and_close_repeat_result",
                return_value=(True, "closed repeat result"),
            ),
            patch("daily_arena.leave_arena_victory", return_value=(True, "left arena")),
            patch(
                "daily_arena.confirm_optional_rank_change",
                return_value=(True, "rank confirmed"),
            ),
        ):
            ok, reason = run_daily_arena(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "rank confirmed")
        enter_battle_prep.assert_not_called()


class ArenaAutoBattleSafetyTests(unittest.TestCase):
    def test_free_only_toggle_uses_knob_position(self) -> None:
        disabled = Image.new("RGB", (1000, 500), (40, 40, 40))
        disabled_draw = ImageDraw.Draw(disabled)
        disabled_draw.ellipse((665, 184, 679, 199), fill=(225, 225, 225))

        enabled = Image.new("RGB", (1000, 500), (40, 40, 40))
        enabled_draw = ImageDraw.Draw(enabled)
        enabled_draw.ellipse((680, 184, 694, 199), fill=(225, 225, 225))

        self.assertFalse(_free_only_toggle_enabled(disabled))
        self.assertTrue(_free_only_toggle_enabled(enabled))

    @patch("daily_arena.time.sleep")
    @patch("daily_arena.classify_state", return_value=("arena_auto_battle_dialog", {}))
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena._click_ratio")
    def test_free_only_toggle_is_clicked_once_and_verified(
        self,
        click_ratio: MagicMock,
        capture_client: MagicMock,
        _classify: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        disabled = Image.new("RGB", (1000, 500), (40, 40, 40))
        ImageDraw.Draw(disabled).ellipse((665, 184, 679, 199), fill=(225, 225, 225))
        enabled = Image.new("RGB", (1000, 500), (40, 40, 40))
        ImageDraw.Draw(enabled).ellipse((680, 184, 694, 199), fill=(225, 225, 225))
        capture_client.return_value = enabled

        ok, verified_image, reason = _ensure_free_only(
            123,
            disabled,
            MagicMock(),
            dry_run=False,
        )

        self.assertTrue(ok)
        self.assertIs(verified_image, enabled)
        self.assertIn("enabled", reason)
        self.assertEqual(click_ratio.call_count, 1)
        self.assertEqual(click_ratio.call_args.args[2], "arena_free_only")

    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena._ensure_free_only", autospec=True)
    @patch("daily_arena._auto_battle_count", return_value=1)
    @patch("daily_arena.classify_state", return_value=("arena_auto_battle_dialog", {}))
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_auto_start_keeps_one_battle_and_never_clicks_max(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify: MagicMock,
        _count: MagicMock,
        ensure_free_only: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        dialog_image = Image.new("RGB", (2000, 1000))
        capture_client.return_value = dialog_image
        ensure_free_only.return_value = (True, dialog_image, "free-only mode enabled")
        click_with_retry.return_value = (True, "unknown", dialog_image, "battle started")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = maximize_and_start_auto_battle(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertIn("selected_count=1", reason)
        self.assertEqual(click_with_retry.call_args.args[2], "arena_auto_start")

    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena._ensure_free_only", autospec=True)
    @patch("daily_arena._auto_battle_count", return_value=193)
    @patch("daily_arena.classify_state", return_value=("arena_auto_battle_dialog", {}))
    @patch("daily_arena.safe_capture_client", return_value=Image.new("RGB", (2000, 1000)))
    @patch("daily_arena.find_game_window", return_value=123)
    def test_auto_start_refuses_more_than_one_battle(
        self,
        _find_window: MagicMock,
        _capture_client: MagicMock,
        _classify: MagicMock,
        _count: MagicMock,
        ensure_free_only: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = maximize_and_start_auto_battle(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertFalse(ok)
        self.assertIn("unsafe arena auto-battle count: 193", reason)
        ensure_free_only.assert_not_called()
        click_with_retry.assert_not_called()


class ArenaManualExitTests(unittest.TestCase):
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state", return_value=("arena_lobby", {}))
    @patch("daily_arena.safe_capture_client", return_value=Image.new("RGB", (2000, 1000)))
    @patch("daily_arena.find_game_window", return_value=123)
    def test_repeat_result_wait_accepts_manual_exit_to_lobby(
        self,
        _find_window: MagicMock,
        _capture_client: MagicMock,
        _classify: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = wait_and_close_repeat_result(
                dry_run=False,
                log_root=Path(temporary),
                timeout=1.0,
            )

        self.assertTrue(ok)
        self.assertIn("manual arena exit reached arena_lobby", reason)
        click_with_retry.assert_not_called()

    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state", return_value=("arena_rank_change", {}))
    @patch("daily_arena.safe_capture_client", return_value=Image.new("RGB", (2000, 1000)))
    @patch("daily_arena.find_game_window", return_value=123)
    def test_victory_leave_hands_rank_change_to_confirmation(
        self,
        _find_window: MagicMock,
        _capture_client: MagicMock,
        _classify: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = leave_arena_victory(
                dry_run=False,
                log_root=Path(temporary),
                timeout=1.0,
            )

        self.assertTrue(ok)
        self.assertIn("arena_rank_change", reason)
        click_with_retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
