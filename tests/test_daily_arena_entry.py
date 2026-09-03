from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_arena import enter_arena_from_plaza, enter_battlefield


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


if __name__ == "__main__":
    unittest.main()
