from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, DEFAULT, MagicMock, patch

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import business_management
from business_management import (
    enter_restaurant,
    open_regular_customer_rewards,
    return_home_from_restaurant,
    run_business_management,
)
from game_text_recognition import recognize_home_labels, recognize_restaurant_state


class RestaurantRecognitionRegressionTests(unittest.TestCase):
    @patch("game_text_recognition._recognize_label_groups")
    def test_new_regular_customer_mode_uses_fixed_title_notes_and_settlement(
        self,
        recognize_groups: MagicMock,
    ) -> None:
        grouped_texts = {
            "title": ["格鲁菲餐厅"],
            "left_controls": ["常客笔记", "格鲁TALK"],
            "bottom_controls": ["员工"],
            "settlement": ["结算"],
            "regular_customer_mode": [],
            "loading_title": [],
            "loading_progress": [],
        }
        matches = {
            "title": ["格鲁菲餐厅"],
            "left_controls": ["常客笔记", "格鲁TALK"],
            "bottom_controls": ["员工"],
            "settlement": ["结算"],
            "regular_customer_mode": [],
            "loading_title": [],
            "loading_progress": [],
        }
        recognize_groups.return_value = grouped_texts, matches, None

        state, details = recognize_restaurant_state(Image.new("RGB", (2000, 1000)))

        self.assertEqual(state, "restaurant_regular_customer_mode")
        self.assertEqual(
            details["matches"]["left_controls"],
            ["常客笔记", "格鲁TALK"],
        )

    @patch("game_text_recognition._recognize_label_groups")
    def test_complete_restaurant_home_is_not_reclassified_as_new_mode(
        self,
        recognize_groups: MagicMock,
    ) -> None:
        grouped_texts = {
            "title": ["格鲁菲餐厅"],
            "left_controls": ["常客笔记", "格鲁TALK"],
            "bottom_controls": ["员工", "客人", "成长"],
            "settlement": ["结算"],
            "regular_customer_mode": [],
            "loading_title": [],
            "loading_progress": [],
        }
        matches = {name: list(texts) for name, texts in grouped_texts.items()}
        recognize_groups.return_value = grouped_texts, matches, None

        state, _details = recognize_restaurant_state(Image.new("RGB", (2000, 1000)))

        self.assertEqual(state, "restaurant_home")

    @patch("game_text_recognition._recognize_label_groups")
    def test_full_restaurant_home_accepts_full_settlement_state(
        self,
        recognize_groups: MagicMock,
    ) -> None:
        grouped_texts = {
            "title": ["格鲁菲餐厅"],
            "left_controls": ["常客", "亲密度"],
            "bottom_controls": ["员工", "客人"],
            "settlement": ["已满", "24:00:00"],
            "regular_customer_mode": [],
            "loading_title": [],
            "loading_progress": [],
        }
        matches = {name: list(texts) for name, texts in grouped_texts.items()}
        recognize_groups.return_value = grouped_texts, matches, None

        state, _details = recognize_restaurant_state(Image.new("RGB", (2000, 1000)))

        self.assertEqual(state, "restaurant_home")


class RestaurantReturnRegressionTests(unittest.TestCase):
    @patch("business_management.wait_for_recognition")
    @patch("business_management._click_ratio")
    @patch(
        "business_management.classify_state",
        return_value=("restaurant_regular_customer_mode", {}),
    )
    @patch("business_management.safe_capture_client")
    @patch("business_management.find_game_window", return_value=123)
    def test_return_home_clicks_once_then_waits_only_for_fixed_home_text(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        classify_state: MagicMock,
        click_ratio: MagicMock,
        wait_for_recognition: MagicMock,
    ) -> None:
        restaurant_image = Image.new("RGB", (2000, 1000))
        home_image = Image.new("RGB", (2000, 1000))
        capture_client.return_value = restaurant_image
        wait_for_recognition.return_value = True, home_image, {"matches": {}}

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = return_home_from_restaurant(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "returned home from restaurant")
        classify_state.assert_called_once_with(restaurant_image)
        click_ratio.assert_called_once_with(
            123,
            restaurant_image,
            "restaurant_home",
            dry_run=False,
            logger=ANY,
        )
        wait_for_recognition.assert_called_once_with(
            123,
            logger=ANY,
            label="after-restaurant-home",
            recognize=recognize_home_labels,
            timeout=20.0,
        )


class RestaurantFlowRegressionTests(unittest.TestCase):
    def test_business_management_skips_regular_customer_claim_when_no_notification(
        self,
    ) -> None:
        with patch.multiple(
            business_management,
            open_business_management=DEFAULT,
            claim_business_management_rewards=DEFAULT,
            dismiss_business_management_reward=DEFAULT,
            enter_restaurant=DEFAULT,
            open_regular_customer_rewards=DEFAULT,
            open_regular_customer_note_rewards=DEFAULT,
            claim_all_regular_customer_rewards=DEFAULT,
            dismiss_regular_customer_reward=DEFAULT,
            leave_regular_customer_notes=DEFAULT,
            return_home_from_restaurant=DEFAULT,
        ) as mocks:
            for name in (
                "open_business_management",
                "claim_business_management_rewards",
                "dismiss_business_management_reward",
                "enter_restaurant",
                "open_regular_customer_rewards",
            ):
                mocks[name].return_value = (True, "ok")
            mocks["open_regular_customer_note_rewards"].return_value = (
                True,
                "regular-customer notes have no reward notification",
            )
            mocks["claim_all_regular_customer_rewards"].return_value = (
                False,
                "claim should have been skipped",
            )
            mocks["return_home_from_restaurant"].return_value = (
                True,
                "returned home from restaurant",
            )

            with tempfile.TemporaryDirectory() as temporary:
                ok, reason = run_business_management(
                    dry_run=False,
                    log_root=Path(temporary),
                )

        self.assertTrue(ok)
        self.assertEqual(reason, "returned home from restaurant")
        mocks["claim_all_regular_customer_rewards"].assert_not_called()
        mocks["dismiss_regular_customer_reward"].assert_not_called()
        mocks["leave_regular_customer_notes"].assert_not_called()
        mocks["return_home_from_restaurant"].assert_called_once()

    @patch("business_management.click_with_fixed_retry")
    @patch(
        "business_management.classify_state",
        return_value=("business_management_dialog", {}),
    )
    @patch("business_management.safe_capture_client")
    @patch("business_management.find_game_window", return_value=123)
    def test_restaurant_entry_accepts_new_regular_customer_mode(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify_state: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        dialog_image = Image.new("RGB", (2000, 1000))
        restaurant_image = Image.new("RGB", (2000, 1000))
        capture_client.return_value = dialog_image
        click_with_retry.return_value = (
            True,
            "restaurant_regular_customer_mode",
            restaurant_image,
            "entered restaurant",
        )

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_restaurant(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "restaurant home reached")
        verify = click_with_retry.call_args.kwargs["verify"]
        self.assertTrue(verify("restaurant_regular_customer_mode", restaurant_image))

    @patch("business_management.click_with_fixed_retry")
    @patch(
        "business_management.classify_state",
        return_value=("restaurant_regular_customer_mode", {}),
    )
    @patch("business_management.safe_capture_client")
    @patch("business_management.find_game_window", return_value=123)
    def test_open_regular_customer_is_idempotent_for_new_mode(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify_state: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        capture_client.return_value = Image.new("RGB", (2000, 1000))

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = open_regular_customer_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "regular-customer mode already open")
        click_with_retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
