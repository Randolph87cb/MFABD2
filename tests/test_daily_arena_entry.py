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

from daily_arena import enter_battlefield


class BattlefieldRestaurantRouteTests(unittest.TestCase):
    @patch("daily_arena.leave_cartridge_collection")
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
        leave_collection: MagicMock,
    ) -> None:
        home_image = Image.new("RGB", (2000, 1000))
        restaurant_image = Image.new("RGB", (2000, 1000))
        collection_image = Image.new("RGB", (2000, 1000))
        plaza_image = Image.new("RGB", (2000, 1000))
        capture_client.return_value = home_image
        click_with_retry.side_effect = [
            (True, "restaurant_home", restaurant_image, "opened restaurant"),
            (True, "arena_cartridge_collection", collection_image, "opened game cards"),
        ]
        leave_collection.return_value = (True, "plaza", plaza_image, "returned to plaza")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_battlefield(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to plaza")
        self.assertEqual(
            [call.args[2] for call in click_with_retry.call_args_list],
            ["home_return_battlefield", "restaurant_game_cards"],
        )
        first_verify = click_with_retry.call_args_list[0].kwargs["verify"]
        self.assertTrue(first_verify("restaurant_home", restaurant_image))
        leave_collection.assert_called_once_with(
            123,
            collection_image,
            unittest.mock.ANY,
            dry_run=False,
        )


if __name__ == "__main__":
    unittest.main()
