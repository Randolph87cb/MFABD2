from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, DEFAULT, MagicMock, patch

from PIL import Image, ImageEnhance


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import business_management
from business_management import (
    NO_BUSINESS_MANAGEMENT_REWARDS,
    claim_business_management_rewards,
    detect_business_management_claim_disabled,
    enter_restaurant,
    open_business_management,
    open_regular_customer_rewards,
    return_home_from_restaurant,
    run_business_management,
)
from game_text_recognition import recognize_home_labels, recognize_restaurant_state
from free_gacha import classify_state


TITLE_SPEECH_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures/recognition/restaurant-home-title-speech-bubble-2304x1296.png"
)
DISABLED_CLAIM_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures/recognition/business-management-claim-disabled-2304x1296.png"
)


class RestaurantRecognitionRegressionTests(unittest.TestCase):
    def test_speech_bubble_over_title_keeps_restaurant_home_recognizable(self) -> None:
        with Image.open(TITLE_SPEECH_FIXTURE) as source:
            for size in (source.size, (1920, 1080)):
                with self.subTest(size=size):
                    state, details = classify_state(source.resize(size))
                    self.assertEqual(state, "restaurant_home")
                    self.assertIn(
                        "格鲁菲餐厅", details["restaurant_text"]["matches"]["title"]
                    )
                    self.assertIsNotNone(details["restaurant_text"]["title_fallback"])

    def test_missing_title_still_requires_cropped_title_and_restaurant_controls(self) -> None:
        for left_controls, cropped_title, expected, fallback_calls in (
            (["常客笔记", "格鲁TALK"], ["格鲁菲餐厅"], "restaurant_home", 1),
            (["常客笔记", "格鲁TALK"], [], "unknown", 1),
            (["常客笔记"], ["格鲁菲餐厅"], "unknown", 0),
        ):
            with self.subTest(left_controls=left_controls, cropped_title=cropped_title):
                texts = {
                    "title": [],
                    "left_controls": left_controls,
                    "bottom_controls": ["员工", "客人"],
                    "settlement": ["结算"],
                    "regular_customer_mode": [],
                    "loading_title": [],
                    "loading_progress": [],
                }
                matches = {name: list(values) for name, values in texts.items()}
                with (
                    patch(
                        "game_text_recognition._recognize_with_session",
                        return_value=(texts, matches, None),
                    ),
                    patch(
                        "game_text_recognition._recognize_label_groups",
                        return_value=({"title": cropped_title}, {"title": cropped_title}, None),
                    ) as title_ocr,
                ):
                    state, _details = recognize_restaurant_state(Image.new("RGB", (2000, 1000)))
                self.assertEqual(state, expected)
                self.assertEqual(title_ocr.call_count, fallback_calls)

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


class BusinessManagementEntryRegressionTests(unittest.TestCase):
    @patch("business_management.click_with_fixed_retry")
    @patch(
        "business_management.classify_state",
        return_value=("business_management_dialog", {}),
    )
    @patch(
        "business_management.safe_capture_client",
        return_value=Image.new("RGB", (2000, 1000)),
    )
    @patch("business_management.find_game_window", return_value=123)
    def test_entry_resumes_from_an_already_open_dialog(
        self,
        _find_window: MagicMock,
        _capture_client: MagicMock,
        _classify_state: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = open_business_management(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "business-management dialog already open")
        click_with_retry.assert_not_called()

    @patch("business_management.wait_for_state")
    @patch("business_management.recognize_home_labels")
    @patch("business_management.click_with_fixed_retry")
    @patch(
        "business_management.classify_state",
        return_value=("real_home", {}),
    )
    @patch("business_management.safe_capture_client")
    @patch("business_management.find_game_window", return_value=123)
    def test_entry_waits_through_a_brightness_misclassified_home_loading_frame(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify_state: MagicMock,
        click_with_retry: MagicMock,
        recognize_home: MagicMock,
        wait_for_state: MagicMock,
    ) -> None:
        home_image = Image.new("RGB", (2000, 1000))
        loading_image = Image.new("RGB", (2000, 1000), color=(80, 80, 80))
        dialog_image = Image.new("RGB", (2000, 1000), color=(160, 160, 160))
        capture_client.return_value = home_image
        recognize_home.return_value = (True, {"matched": True})
        wait_for_state.return_value = ("business_management_dialog", dialog_image)

        def click_effect(
            *_args: object,
            **kwargs: object,
        ) -> tuple[bool, str, Image.Image, str]:
            accepted = kwargs["verify"]("blocking_ad_overlay", loading_image)
            return accepted, "blocking_ad_overlay", loading_image, "entry transition"

        click_with_retry.side_effect = click_effect

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = open_business_management(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "business-management dialog opened")
        recognize_home.assert_called_once_with(loading_image)
        wait_for_state.assert_called_once_with(
            123,
            ANY,
            expected={"business_management_dialog"},
            timeout=20.0,
            interval=2.0,
            label="after-business-management-open",
        )


class BusinessManagementClaimRegressionTests(unittest.TestCase):
    def test_recorded_disabled_claim_is_skipped_without_input(self) -> None:
        with Image.open(DISABLED_CLAIM_FIXTURE) as source:
            for size in (source.size, (1920, 1080)):
                with (
                    self.subTest(size=size),
                    tempfile.TemporaryDirectory() as temporary,
                    patch("business_management.find_game_window", return_value=123),
                    patch("business_management.safe_capture_client", return_value=source.resize(size)),
                    patch("business_management.click_with_fixed_retry") as click,
                ):
                    ok, reason = claim_business_management_rewards(
                        dry_run=False, log_root=Path(temporary)
                    )
                    self.assertTrue(ok, reason)
                    self.assertEqual(reason, NO_BUSINESS_MANAGEMENT_REWARDS)
                    click.assert_not_called()

    def test_enabled_claim_still_requires_reward_overlay(self) -> None:
        fixture = DISABLED_CLAIM_FIXTURE.with_name("business-management-dialog-2567x1446.png")
        with (
            Image.open(fixture) as image,
            tempfile.TemporaryDirectory() as temporary,
            patch("business_management.find_game_window", return_value=123),
            patch("business_management.safe_capture_client", return_value=image),
            patch(
                "business_management.click_with_fixed_retry",
                return_value=(True, "reward_overlay", image, "claimed"),
            ) as click,
        ):
            ok, reason = claim_business_management_rewards(
                dry_run=False, log_root=Path(temporary)
            )
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "business-management rewards claimed")
        click.assert_called_once()
        verify = click.call_args.kwargs["verify"]
        self.assertFalse(verify("business_management_dialog", image))
        self.assertTrue(verify("reward_overlay", image))

    def test_darkened_dialog_is_not_evidence_of_a_disabled_claim(self) -> None:
        with Image.open(DISABLED_CLAIM_FIXTURE) as image:
            darkened = ImageEnhance.Brightness(image).enhance(0.4)
        disabled, _details = detect_business_management_claim_disabled(darkened)
        self.assertFalse(disabled)

    def test_disabled_colour_does_not_bypass_dialog_recognition(self) -> None:
        with (
            Image.open(DISABLED_CLAIM_FIXTURE) as image,
            tempfile.TemporaryDirectory() as temporary,
            patch("business_management.find_game_window", return_value=123),
            patch("business_management.safe_capture_client", return_value=image),
            patch("business_management.classify_state", return_value=("unknown", {})),
            patch("business_management.click_with_fixed_retry") as click,
        ):
            ok, _reason = claim_business_management_rewards(
                dry_run=False, log_root=Path(temporary)
            )
        self.assertFalse(ok)
        click.assert_not_called()


class RestaurantFlowRegressionTests(unittest.TestCase):
    def test_empty_business_rewards_continue_to_restaurant_without_dismissing(self) -> None:
        with (
            Image.open(DISABLED_CLAIM_FIXTURE) as image,
            tempfile.TemporaryDirectory() as temporary,
            patch("business_management.find_game_window", return_value=123),
            patch("business_management.safe_capture_client", return_value=image),
            patch("business_management.click_with_fixed_retry") as click,
            patch.multiple(
                business_management,
                open_business_management=DEFAULT,
                dismiss_business_management_reward=DEFAULT,
                enter_restaurant=DEFAULT,
                open_regular_customer_rewards=DEFAULT,
                open_regular_customer_note_rewards=DEFAULT,
                return_home_from_restaurant=DEFAULT,
            ) as mocks,
        ):
            for name in ("open_business_management", "enter_restaurant", "open_regular_customer_rewards"):
                mocks[name].return_value = (True, "ok")
            mocks["open_regular_customer_note_rewards"].return_value = (
                True, business_management.NO_REGULAR_CUSTOMER_REWARDS
            )
            mocks["return_home_from_restaurant"].return_value = (True, "returned home")
            ok, reason = run_business_management(dry_run=False, log_root=Path(temporary))
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "returned home")
        click.assert_not_called()
        mocks["dismiss_business_management_reward"].assert_not_called()
        mocks["enter_restaurant"].assert_called_once()
        mocks["open_regular_customer_rewards"].assert_called_once()
        mocks["open_regular_customer_note_rewards"].assert_called_once()
        mocks["return_home_from_restaurant"].assert_called_once()

    def test_regular_customer_step_accepts_recorded_speech_bubble_frame(self) -> None:
        with Image.open(TITLE_SPEECH_FIXTURE) as image:
            with (
                tempfile.TemporaryDirectory() as temporary,
                patch("business_management.find_game_window", return_value=123),
                patch("business_management.safe_capture_client", return_value=image),
                patch(
                    "business_management.click_with_fixed_retry",
                    return_value=(True, "restaurant_home", image, "reward claimed"),
                ) as click,
            ):
                ok, reason = open_regular_customer_rewards(
                    dry_run=False, log_root=Path(temporary)
                )
        self.assertTrue(ok, reason)
        click.assert_called_once()
        self.assertEqual(click.call_args.args[2], "restaurant_regular_customer")

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

    @patch("business_management.wait_for_state")
    @patch("business_management.click_with_fixed_retry")
    @patch(
        "business_management.classify_state",
        return_value=("business_management_dialog", {}),
    )
    @patch("business_management.safe_capture_client")
    @patch("business_management.find_game_window", return_value=123)
    def test_restaurant_entry_waits_through_home_transition(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify_state: MagicMock,
        click_with_retry: MagicMock,
        wait_for_state: MagicMock,
    ) -> None:
        dialog_image = Image.new("RGB", (2000, 1000))
        home_image = Image.new("RGB", (2000, 1000), color=(80, 80, 80))
        restaurant_image = Image.new("RGB", (2000, 1000), color=(160, 160, 160))
        capture_client.return_value = dialog_image

        def click_effect(
            *_args: object,
            **kwargs: object,
        ) -> tuple[bool, str, Image.Image, str]:
            accepted = kwargs["verify"]("real_home", home_image)
            return accepted, "real_home", home_image, "restaurant transition"

        click_with_retry.side_effect = click_effect
        wait_for_state.return_value = ("restaurant_home", restaurant_image)

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_restaurant(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "restaurant home reached")
        wait_for_state.assert_called_once_with(
            123,
            ANY,
            expected={"restaurant_home", "restaurant_regular_customer_mode"},
            timeout=90.0,
            interval=3.0,
            label="restaurant-transition",
        )

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

    @patch("business_management.click_with_fixed_retry")
    @patch(
        "business_management.classify_state",
        return_value=("restaurant_home", {}),
    )
    @patch("business_management.safe_capture_client")
    @patch("business_management.find_game_window", return_value=123)
    def test_regular_customer_reward_claim_can_remain_on_restaurant_home(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify_state: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        restaurant_image = Image.new("RGB", (2000, 1000))
        capture_client.return_value = restaurant_image

        def claim_effect(
            *_args: object,
            **kwargs: object,
        ) -> tuple[bool, str, Image.Image, str]:
            accepted = kwargs["verify"]("restaurant_home", restaurant_image)
            return accepted, "restaurant_home", restaurant_image, "reward claimed"

        click_with_retry.side_effect = claim_effect

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = open_regular_customer_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "regular-customer rewards checked")


if __name__ == "__main__":
    unittest.main()
