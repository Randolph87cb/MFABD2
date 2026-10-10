from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from PIL import Image, ImageDraw


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import activity_rewards


class ActivityDispatchTests(unittest.TestCase):
    def test_activity_badge_region_covers_current_list_edge(self) -> None:
        image = Image.new("RGB", (1000, 600))
        draw = ImageDraw.Draw(image)
        center_x, center_y = 266, 401
        radius = 7
        draw.polygon(
            (
                (center_x, center_y - radius),
                (center_x + radius, center_y),
                (center_x, center_y + radius),
                (center_x - radius, center_y),
            ),
            fill=(220, 25, 45),
        )
        draw.rectangle((center_x, center_y - 3, center_x, center_y), fill="white")
        draw.point((center_x, center_y + 3), fill="white")

        badges = activity_rewards.find_red_exclamation_badges(
            image,
            activity_rewards.ACTIVITY_LIST_BADGE_REGION,
        )

        self.assertEqual(len(badges), 1)

    def test_unselected_activity_badge_accepts_antialiased_flat_tip(self) -> None:
        image = Image.new("RGB", (1000, 600), color=(18, 20, 24))
        draw = ImageDraw.Draw(image)
        center_x, center_y = 266, 401
        draw.polygon(
            (
                (center_x, center_y - 10),
                (center_x + 10, center_y),
                (center_x + 4, center_y + 10),
                (center_x - 3, center_y + 10),
                (center_x - 10, center_y),
            ),
            fill=(220, 25, 45),
        )
        draw.rectangle((center_x, center_y - 4, center_x, center_y + 1), fill="white")
        draw.point((center_x, center_y + 4), fill="white")

        badges = activity_rewards.find_red_exclamation_badges(
            image,
            activity_rewards.ACTIVITY_LIST_BADGE_REGION,
        )

        self.assertEqual(len(badges), 1)

    def test_fixed_coordinates_use_reference_canvas_and_calibrated_scroll(self) -> None:
        self.assertEqual(activity_rewards.HOME_ACTIVITY_POINT, (0.467, 0.925))
        self.assertEqual(
            activity_rewards.ACTIVITY_PAGE_REGION,
            (108 / 1280, 110 / 720, 529 / 1280, 483 / 720),
        )
        self.assertEqual(
            activity_rewards.TOKEN_CONFIRM_POINT,
            ((665 + 85 / 2) / 1280, (403 + 29 / 2) / 720),
        )
        self.assertEqual(
            activity_rewards.TOKEN_EXCHANGE_REGION,
            (0.290, 0.660, 0.115, 0.100),
        )
        self.assertEqual(
            activity_rewards.PUZZLE_UNLOCK_REGION,
            (930 / 1280, 475 / 720, 120 / 1280, 49 / 720),
        )
        self.assertEqual(
            activity_rewards.PUZZLE_UNLOCK_POINT,
            ((930 + 120 / 2) / 1280, (475 + 49 / 2) / 720),
        )
        self.assertAlmostEqual(activity_rewards.TOKEN_EXCHANGE_POINT[0], 0.3475)
        self.assertAlmostEqual(activity_rewards.TOKEN_EXCHANGE_POINT[1], 0.7100)
        self.assertAlmostEqual(activity_rewards.TOKEN_EXCHANGE_BATCH_POINT[0], 0.4625)
        self.assertAlmostEqual(activity_rewards.TOKEN_EXCHANGE_BATCH_POINT[1], 0.7100)
        self.assertLessEqual(
            activity_rewards.TOKEN_EXCHANGE_REGION[0]
            + activity_rewards.TOKEN_EXCHANGE_REGION[2],
            0.405,
        )
        self.assertEqual(
            activity_rewards.SCROLL_BEGIN,
            (0.175, 0.700),
        )
        self.assertEqual(activity_rewards.SCROLL_END, (0.175, 0.280))
        self.assertEqual(
            activity_rewards.CLOTHING_OPEN_POINT,
            (1047 / 1280, 204 / 720),
        )
        self.assertEqual(activity_rewards.REGULAR_CLAIM_POINT, (0.773, 0.717))
        claim_x, claim_y = activity_rewards.REGULAR_CLAIM_POINT
        region_x, region_y, region_width, region_height = (
            activity_rewards.REGULAR_CLAIM_REGION
        )
        self.assertLessEqual(region_x, claim_x)
        self.assertLessEqual(claim_x, region_x + region_width)
        self.assertLessEqual(region_y, claim_y)
        self.assertLessEqual(claim_y, region_y + region_height)
        expected_switch = (
            (1076 + 11 / 2 + 10) / 1280,
            (534 + 13 / 2 + 4) / 720,
        )
        for actual, expected in zip(activity_rewards.DICE_SWITCH_POINT, expected_switch):
            self.assertAlmostEqual(actual, expected)

    def test_dispatches_every_supported_activity_layout(self) -> None:
        cases = {
            "regular_claim": "regular",
            "token_exchange": "token_exchange",
            "dice_auto": "dice_auto",
            "puzzle_unlock": "puzzle",
            "bingo_unlock": "bingo",
            "free_roulette": "free_roulette",
            "token_roulette": "token_roulette",
            "clothing_offer": "free_clothing",
            "clothing_claim_now": "free_clothing_style_1",
            "clothing_button": "free_clothing_style_2",
            "paid_free": "paid_diamonds",
        }
        for group, expected in cases.items():
            with self.subTest(group=group):
                self.assertEqual(
                    activity_rewards.classify_activity_from_matches({group: ["matched"]}),
                    expected,
                )

    def test_regular_claim_beats_broad_free_clothing_copy(self) -> None:
        self.assertEqual(
            activity_rewards.classify_activity_from_matches(
                {
                    "regular_claim": ["全部领取"],
                    "clothing_offer": ["+5强化"],
                }
            ),
            "regular",
        )

    def test_purchase_without_free_offer_is_unsafe_not_unknown(self) -> None:
        kind = activity_rewards.classify_activity_from_matches(
            {"paid_purchase": ["购买"]}
        )
        self.assertEqual(kind, "unsafe_paid")

    def test_paid_confirmation_requires_exact_labels_from_one_ocr_frame(self) -> None:
        exact_details = {
            "texts": {"paid_free": ["免费1次"], "paid_purchase": ["购买"]}
        }
        self.assertTrue(
            activity_rewards.paid_confirmation_is_safe(
                {"paid_free": ["免费1次"], "paid_purchase": ["购买"]},
                exact_details,
            )
        )
        for matches in (
            {},
            {"paid_free": ["免费1次"]},
            {"paid_purchase": ["购买"]},
        ):
            with self.subTest(matches=matches):
                self.assertFalse(
                    activity_rewards.paid_confirmation_is_safe(matches, exact_details)
                )
        self.assertFalse(
            activity_rewards.paid_confirmation_is_safe(
                {"paid_free": ["免费1次"], "paid_purchase": ["购买"]},
                {"texts": {"paid_free": ["付费1次"], "paid_purchase": ["购买"]}},
            )
        )

    def test_paid_offer_requires_exact_free_text_before_opening(self) -> None:
        self.assertTrue(
            activity_rewards.paid_free_offer_is_safe(
                {"paid_free": ["免费1次"]},
                {"texts": {"paid_free": ["免费1次"]}},
            )
        )
        self.assertFalse(
            activity_rewards.paid_free_offer_is_safe(
                {"paid_free": ["付费1次"]},
                {"texts": {"paid_free": ["付费1次"]}},
            )
        )


class ActivityPaidSafetyTests(unittest.TestCase):
    @patch("activity_rewards.click_ratio_logged")
    @patch("activity_rewards.recognize_activity_controls")
    def test_fuzzy_paid_offer_match_does_not_click_the_opener(
        self,
        recognize: MagicMock,
        click: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1200, 675))
        recognize.return_value = (
            {"paid_free": ["免费1次"]},
            {"available": True, "texts": {"paid_free": ["付费1次"]}},
        )

        ok, _image, reason = activity_rewards._handle_paid_diamonds(
            123,
            image,
            logger=MagicMock(),
            dry_run=False,
        )

        self.assertFalse(ok)
        self.assertIn("offer was not clicked", reason)
        click.assert_not_called()

    @patch("activity_rewards._click_then_return")
    @patch("activity_rewards.recognize_activity_controls")
    @patch("activity_rewards._wait_for_image")
    @patch("activity_rewards.click_ratio_logged")
    def test_safe_paid_confirmation_clicks_purchase(
        self,
        click: MagicMock,
        wait: MagicMock,
        recognize: MagicMock,
        click_then_return: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1200, 675))
        wait.return_value = (True, image)
        recognize.return_value = (
            {"paid_free": ["免费1次"], "paid_purchase": ["购买"]},
            {
                "available": True,
                "texts": {"paid_free": ["免费1次"], "paid_purchase": ["购买"]},
            },
        )
        click_then_return.return_value = (True, image, "closed")

        ok, _image, _reason = activity_rewards._handle_paid_diamonds(
            123,
            image,
            logger=MagicMock(),
            dry_run=False,
        )

        self.assertTrue(ok)
        click.assert_not_called()
        click_then_return.assert_called_once()
        self.assertEqual(
            click_then_return.call_args.kwargs["key"],
            "activity_paid_free_confirm",
        )

    @patch("activity_rewards._click_then_return")
    @patch("activity_rewards.recognize_activity_controls")
    @patch("activity_rewards._wait_for_image")
    @patch("activity_rewards.click_ratio_logged")
    def test_ambiguous_paid_confirmation_never_clicks_purchase(
        self,
        click: MagicMock,
        wait: MagicMock,
        recognize: MagicMock,
        click_then_return: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1200, 675))
        wait.return_value = (True, image)
        recognize.side_effect = [
            (
                {"paid_free": ["免费1次"]},
                {"available": True, "texts": {"paid_free": ["免费1次"]}},
            ),
            (
                {"paid_purchase": ["购买"]},
                {"available": True, "texts": {"paid_purchase": ["购买"]}},
            ),
        ]

        ok, _image, reason = activity_rewards._handle_paid_diamonds(
            123,
            image,
            logger=MagicMock(),
            dry_run=False,
        )

        self.assertFalse(ok)
        self.assertIn("purchase was not clicked", reason)
        # The only click is the harmless free-offer opener; the purchase helper
        # is never reached when the same-frame proof is incomplete.
        click.assert_called_once()
        click_then_return.assert_not_called()

    @patch("activity_rewards.click_ratio_logged")
    def test_ordinary_paid_state_stops_without_any_click(self, click: MagicMock) -> None:
        image = Image.new("RGB", (1200, 675))
        ok, _image, reason = activity_rewards._handle_activity(
            "unsafe_paid",
            123,
            image,
            logger=MagicMock(),
            dry_run=False,
        )
        self.assertFalse(ok)
        self.assertIn("purchase was not clicked", reason)
        click.assert_not_called()


class ActivitySettlementTests(unittest.TestCase):
    def test_exchange_uses_dynamic_batch_when_balance_is_readable(self) -> None:
        image = Image.new("RGB", (1280, 720))
        cases = (
            (610, activity_rewards.TOKEN_EXCHANGE_BATCH_POINT, "activity_token_exchange_batch"),
            (60, activity_rewards.TOKEN_EXCHANGE_BATCH_POINT, "activity_token_exchange_batch"),
            (10, activity_rewards.TOKEN_EXCHANGE_BATCH_POINT, "activity_token_exchange_batch"),
            (1, activity_rewards.TOKEN_EXCHANGE_BATCH_POINT, "activity_token_exchange_batch"),
            (None, activity_rewards.TOKEN_EXCHANGE_POINT, "balance_unreadable"),
        )
        for balance, expected_point, expected_key in cases:
            with self.subTest(balance=balance):
                with (
                    patch(
                        "activity_rewards._activity_token_balance",
                        return_value=(balance, {"available": True}),
                    ),
                    patch("activity_rewards.click_ratio_logged") as click,
                    patch("activity_rewards._wait_for_image", return_value=(True, image)),
                    patch(
                        "activity_rewards._click_then_return",
                        return_value=(True, image, "done"),
                    ),
                ):
                    ok, _image, _reason = activity_rewards._handle_token_exchange(
                        123, image, logger=MagicMock(), dry_run=False
                    )

                self.assertTrue(ok)
                self.assertEqual(click.call_args.args[2], expected_point)
                self.assertIn(expected_key, click.call_args.kwargs["key"])

    def test_exchange_does_not_click_when_balance_is_zero(self) -> None:
        image = Image.new("RGB", (1280, 720))
        with (
            patch(
                "activity_rewards._activity_token_balance",
                return_value=(0, {"available": True}),
            ),
            patch("activity_rewards.click_ratio_logged") as click,
        ):
            ok, _image, reason = activity_rewards._handle_token_exchange(
                123,
                image,
                logger=MagicMock(),
                dry_run=False,
            )

        self.assertFalse(ok)
        self.assertIn("代币为 0", reason)
        click.assert_not_called()

    @patch("activity_rewards._click_then_return")
    @patch("activity_rewards._read_texts_at", return_value=(["21"], {"available": True}))
    def test_roulette_uses_ten_spin_when_balance_allows_it(
        self,
        _read: MagicMock,
        click_then_return: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        click_then_return.return_value = (True, image, "done")
        ok, _image, _reason = activity_rewards._handle_token_roulette(
            123, image, logger=MagicMock(), dry_run=False
        )
        self.assertTrue(ok)
        self.assertEqual(
            click_then_return.call_args.args[2],
            activity_rewards.TOKEN_ROULETTE_TEN_POINT,
        )

    @patch("activity_rewards._click_then_return")
    @patch("activity_rewards._read_texts_at", return_value=(["1"], {"available": True}))
    def test_roulette_uses_single_spin_for_remaining_tokens(
        self,
        _read: MagicMock,
        click_then_return: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        click_then_return.return_value = (True, image, "done")
        ok, _image, _reason = activity_rewards._handle_token_roulette(
            123, image, logger=MagicMock(), dry_run=False
        )
        self.assertTrue(ok)
        self.assertEqual(
            click_then_return.call_args.args[2],
            activity_rewards.TOKEN_ROULETTE_POINT,
        )

    @patch("activity_rewards._click_then_return")
    @patch("activity_rewards._read_texts_at", return_value=([], {"available": True}))
    def test_roulette_unreadable_balance_only_attempts_single_spin(
        self,
        _read: MagicMock,
        click_then_return: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        click_then_return.return_value = (True, image, "done")
        ok, _image, _reason = activity_rewards._handle_token_roulette(
            123, image, logger=MagicMock(), dry_run=False
        )
        self.assertTrue(ok)
        self.assertEqual(
            click_then_return.call_args.args[2],
            activity_rewards.TOKEN_ROULETTE_POINT,
        )
        self.assertIn("balance_unreadable", click_then_return.call_args.kwargs["key"])

    @patch("activity_rewards._wait_for_image")
    @patch("activity_rewards.click_ratio_logged")
    @patch("activity_rewards.recognize_reward_overlay_labels")
    def test_reward_settlement_is_closed_and_bounded(
        self,
        recognize_overlay: MagicMock,
        click: MagicMock,
        wait: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1200, 675))
        recognize_overlay.side_effect = [
            (True, {"header": "reward"}),
            (False, {}),
        ]
        wait.return_value = (True, image)

        ok, _image, reason = activity_rewards._dismiss_settlements(
            123,
            image,
            logger=MagicMock(),
            dry_run=False,
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "closed 1 settlements")
        click.assert_called_once()

    @patch("activity_rewards._wait_for_image", return_value=(False, Image.new("RGB", (1200, 675))))
    @patch("activity_rewards.click_ratio_logged")
    @patch("activity_rewards.recognize_reward_overlay_labels", return_value=(True, {}))
    def test_reward_settlement_has_single_step_timeout(
        self,
        _recognize_overlay: MagicMock,
        _click: MagicMock,
        _wait: MagicMock,
    ) -> None:
        ok, _image, reason = activity_rewards._dismiss_settlements(
            123,
            Image.new("RGB", (1200, 675)),
            logger=MagicMock(),
            dry_run=False,
        )
        self.assertFalse(ok)
        self.assertIn("timeout", reason)

    @patch("activity_rewards._wait_for_image")
    @patch("activity_rewards.click_ratio_logged")
    @patch("activity_rewards.recognize_reward_overlay_labels")
    def test_reward_settlement_retries_one_dropped_click(
        self,
        recognize_overlay: MagicMock,
        click: MagicMock,
        wait: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1200, 675))
        recognize_overlay.side_effect = [
            (True, {"header": "reward"}),
            (True, {"header": "reward"}),
            (False, {}),
        ]
        wait.side_effect = [(False, image), (True, image)]

        ok, _image, reason = activity_rewards._dismiss_settlements(
            123,
            image,
            logger=MagicMock(),
            dry_run=False,
        )

        self.assertTrue(ok, reason)
        self.assertEqual(click.call_count, 2)


class ActivityDiceTests(unittest.TestCase):
    @patch("activity_rewards._wait_for_image")
    @patch("activity_rewards.click_ratio_logged")
    def test_unknown_dice_switch_stops_without_clicking_switch(
        self,
        click: MagicMock,
        wait: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        wait.return_value = (False, image)

        ok, _image, reason = activity_rewards._handle_dice_auto(
            123, image, logger=MagicMock(), dry_run=False
        )

        self.assertFalse(ok)
        self.assertIn("switch was not clicked", reason)
        self.assertEqual(click.call_count, 1)
        self.assertEqual(click.call_args.kwargs["key"], "activity_dice_auto")

    @patch("activity_rewards._return_to_activity_index")
    @patch("activity_rewards._dice_switch_state", return_value=("off", {}))
    @patch("activity_rewards._wait_for_image")
    @patch("activity_rewards.click_ratio_logged")
    @patch("activity_rewards.time.sleep")
    def test_ocr_confirmed_off_dice_switch_is_enabled_then_waited(
        self,
        _sleep: MagicMock,
        click: MagicMock,
        wait: MagicMock,
        _switch_state: MagicMock,
        return_index: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        wait.side_effect = [(True, image), (True, image), (True, image)]
        return_index.return_value = (True, image, "returned")

        ok, _image, _reason = activity_rewards._handle_dice_auto(
            123, image, logger=MagicMock(), dry_run=False
        )

        self.assertTrue(ok)
        self.assertEqual(
            [entry.kwargs["key"] for entry in click.call_args_list],
            ["activity_dice_auto", "activity_dice_switch_on"],
        )
        self.assertEqual(wait.call_count, 3)
        return_index.assert_called_once()


class ActivityOcrTransitionTests(unittest.TestCase):
    def test_stylized_harvest_title_uses_detail_progress_fallback(self) -> None:
        before = [
            "强化信实之翼服装2／3",
            "请强化Pickup服装3次。",
            "强化信实之翼服装2／4",
        ]
        after = [
            "每日登录1 / 1",
            "请每天登录游戏。",
            "Hanest",
            "结算格鲁菲餐厅营业额1 / 1",
        ]

        self.assertFalse(
            activity_rewards._activity_identity_matches(
                ["Moon", "活动任务"],
                after,
            )
        )
        self.assertTrue(activity_rewards._activity_detail_progressed(before, after))
        self.assertFalse(activity_rewards._activity_detail_progressed(before, before))

    def test_generic_pickup_task_identity_matches_specific_task_copy(self) -> None:
        self.assertTrue(
            activity_rewards._activity_identity_matches(
                ["Pickup活动任务"],
                [
                    "进行无限宇宙之全一·山城恋Pickup抽抽乐1/1",
                    "请获取Pickup服装。",
                ],
            )
        )

    def test_known_short_roulette_identity_matches_detail_title(self) -> None:
        self.assertTrue(
            activity_rewards._activity_identity_matches(
                ["转盘"],
                ["魔防队七番组特别转盘活动"],
            )
        )

    @patch("activity_rewards._read_texts_at", return_value=(["转盘"], {"available": True}))
    def test_card_identity_is_read_below_top_right_badge(self, read: MagicMock) -> None:
        badge = {"center": (0.266, 0.669)}
        texts, _details = activity_rewards._read_activity_card_identity(
            Image.new("RGB", (1280, 720)),
            badge,
        )
        region = read.call_args.args[1]
        self.assertEqual(texts, ["转盘"])
        self.assertGreater(region[1], badge["center"][1])

    @patch("activity_rewards._read_activity_card_identity", return_value=(["EVENTNAME"], {"available": True}))
    @patch(
        "activity_rewards._read_texts_at",
        side_effect=[
            (["1234"], {"available": True}),
            (["EVENTNAME"], {"available": True}),
        ],
    )
    @patch("activity_rewards.recognize_activity_kind", return_value=("regular", {}))
    @patch("activity_rewards._is_activity_page", return_value=(True, {}))
    @patch("activity_rewards.click_ratio_logged")
    def test_list_selection_requires_matching_identity_page_and_type_ocr(
        self,
        _click: MagicMock,
        _page: MagicMock,
        _kind: MagicMock,
        _detail_identity: MagicMock,
        _card_identity: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        badge = {"center": (0.2, 0.3)}

        def evaluate(_hwnd: int, **kwargs: object) -> tuple[bool, Image.Image]:
            predicate = kwargs["predicate"]
            return bool(predicate(image)), image  # type: ignore[operator]

        with patch("activity_rewards._wait_for_image", side_effect=evaluate):
            ok, _image, _reason = activity_rewards._click_marked_activity(
                123,
                image,
                badge,
                logger=MagicMock(),
                dry_run=False,
            )
        self.assertTrue(ok)

    @patch(
        "activity_rewards._read_activity_card_identity",
        return_value=(["Moon", "活动任务"], {"available": True}),
    )
    @patch("activity_rewards.recognize_activity_kind", return_value=("regular", {}))
    @patch("activity_rewards._is_activity_page", return_value=(True, {}))
    @patch("activity_rewards.click_ratio_logged")
    def test_list_selection_accepts_confirmed_detail_progress(
        self,
        _click: MagicMock,
        _page: MagicMock,
        _kind: MagicMock,
        _card_identity: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        badge = {"center": (0.2, 0.3)}
        before = ["强化信实之翼服装2／3", "请强化Pickup服装3次。"]
        after = ["每日登录1 / 1", "请每天登录游戏。", "Hanest"]

        def evaluate(_hwnd: int, **kwargs: object) -> tuple[bool, Image.Image]:
            predicate = kwargs["predicate"]
            return bool(predicate(image)), image  # type: ignore[operator]

        with (
            patch(
                "activity_rewards._read_texts_at",
                side_effect=[
                    (before, {"available": True}),
                    (after, {"available": True}),
                ],
            ),
            patch("activity_rewards._wait_for_image", side_effect=evaluate),
        ):
            ok, _image, _reason = activity_rewards._click_marked_activity(
                123,
                image,
                badge,
                logger=MagicMock(),
                dry_run=False,
            )

        self.assertTrue(ok)

    @patch(
        "activity_rewards._read_activity_card_identity",
        return_value=(["Haestst", "活动兑换所"], {"available": True}),
    )
    @patch(
        "activity_rewards.recognize_activity_kind",
        side_effect=[
            ("regular", {}),
            ("regular", {}),
            ("token_exchange", {}),
        ],
    )
    @patch("activity_rewards._is_activity_page", return_value=(True, {}))
    @patch("activity_rewards.click_ratio_logged")
    def test_list_selection_rejects_stale_detail_that_already_matches_identity(
        self,
        _click: MagicMock,
        _page: MagicMock,
        _kind: MagicMock,
        _card_identity: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        badge = {"center": (0.2, 0.3)}
        stale_detail = [
            "每日登录11",
            "请每天登录游戏。",
            "Hanest",
            "结算格鲁菲餐厅营业额11",
            "兑换所硬币获取",
            "助力任务",
        ]
        selected_detail = [
            "Harvest Moon",
            "非饶之月",
            "兑换所活动",
            "兑换1次",
            "兑换35次",
        ]
        predicate_results: list[bool] = []

        def evaluate(_hwnd: int, **kwargs: object) -> tuple[bool, Image.Image]:
            predicate = kwargs["predicate"]
            predicate_results.append(bool(predicate(image)))  # type: ignore[operator]
            predicate_results.append(bool(predicate(image)))  # type: ignore[operator]
            return predicate_results[-1], image

        with (
            patch(
                "activity_rewards._read_texts_at",
                side_effect=[
                    (stale_detail, {"available": True}),
                    (stale_detail, {"available": True}),
                    (selected_detail, {"available": True}),
                ],
            ),
            patch("activity_rewards._wait_for_image", side_effect=evaluate),
        ):
            ok, _image, _reason = activity_rewards._click_marked_activity(
                123,
                image,
                badge,
                logger=MagicMock(),
                dry_run=False,
            )

        self.assertTrue(ok)
        self.assertEqual(predicate_results, [False, True])

    @patch(
        "activity_rewards._read_activity_card_identity",
        return_value=(["Moon", "活动兑换所"], {"available": True}),
    )
    @patch(
        "activity_rewards.recognize_activity_kind",
        side_effect=[("token_exchange", {}), ("token_exchange", {})],
    )
    @patch("activity_rewards._is_activity_page", return_value=(True, {}))
    @patch("activity_rewards.click_ratio_logged")
    def test_selected_token_exchange_can_be_processed_again_without_page_change(
        self,
        _click: MagicMock,
        _page: MagicMock,
        _kind: MagicMock,
        _card_identity: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        badge = {"center": (0.2, 0.3)}
        detail = [
            "第6次",
            "帮助",
            "剩余数量：100/195",
            "即刻刷新",
            "Havest",
            "Moon",
            "兑换所活动",
            "兑换95次",
        ]

        def evaluate(_hwnd: int, **kwargs: object) -> tuple[bool, Image.Image]:
            predicate = kwargs["predicate"]
            return bool(predicate(image)), image  # type: ignore[operator]

        with (
            patch(
                "activity_rewards._read_texts_at",
                side_effect=[
                    (detail, {"available": True}),
                    (detail, {"available": True}),
                ],
            ),
            patch("activity_rewards._wait_for_image", side_effect=evaluate),
        ):
            ok, _image, _reason = activity_rewards._click_marked_activity(
                123,
                image,
                badge,
                logger=MagicMock(),
                dry_run=False,
            )

        self.assertTrue(ok)

    @patch(
        "activity_rewards._read_activity_card_identity",
        return_value=(["Pickup活动任务"], {"available": True}),
    )
    @patch(
        "activity_rewards.recognize_activity_kind",
        side_effect=[("regular", {}), ("regular", {})],
    )
    @patch("activity_rewards._is_activity_page", return_value=(True, {}))
    @patch("activity_rewards.click_ratio_logged")
    def test_selected_pickup_task_can_be_claimed_without_page_change(
        self,
        _click: MagicMock,
        _page: MagicMock,
        _kind: MagicMock,
        _card_identity: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        badge = {"center": (0.266, 0.266)}
        detail = [
            "进行白缕轻缠木乃伊内肯达莉亚Pickup抽抽乐1/1",
            "请抽取Pickup抽抽乐。",
            "全部领取",
        ]

        def evaluate(_hwnd: int, **kwargs: object) -> tuple[bool, Image.Image]:
            predicate = kwargs["predicate"]
            return bool(predicate(image)), image  # type: ignore[operator]

        with (
            patch(
                "activity_rewards._read_texts_at",
                side_effect=[
                    (detail, {"available": True}),
                    (detail, {"available": True}),
                ],
            ),
            patch("activity_rewards._wait_for_image", side_effect=evaluate),
        ):
            ok, _image, _reason = activity_rewards._click_marked_activity(
                123,
                image,
                badge,
                logger=MagicMock(),
                dry_run=False,
            )

        self.assertTrue(ok)

    def test_paid_exchange_card_is_not_repeatable_as_token_exchange(self) -> None:
        self.assertIsNone(
            activity_rewards._repeatable_selected_activity_kind(
                ["满月付费钻石兑换所活动"],
            )
        )

    @patch("activity_rewards._return_to_activity_index")
    @patch("activity_rewards.recognize_reward_overlay_labels", return_value=(True, {}))
    @patch("activity_rewards.click_ratio_logged")
    def test_action_effect_requires_positive_reward_overlay_ocr(
        self,
        _click: MagicMock,
        _overlay: MagicMock,
        return_index: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1280, 720))
        return_index.return_value = (True, image, "returned")

        def evaluate(_hwnd: int, **kwargs: object) -> tuple[bool, Image.Image]:
            predicate = kwargs["predicate"]
            return bool(predicate(image)), image  # type: ignore[operator]

        with patch("activity_rewards._wait_for_image", side_effect=evaluate):
            ok, _image, _reason = activity_rewards._click_then_return(
                123,
                image,
                activity_rewards.REGULAR_CLAIM_POINT,
                key="activity_regular_claim_all",
                logger=MagicMock(),
                dry_run=False,
            )
        self.assertTrue(ok)
        return_index.assert_called_once()

    def test_pixel_difference_helpers_are_not_part_of_activity_flow(self) -> None:
        self.assertFalse(hasattr(activity_rewards, "_content_difference"))
        self.assertFalse(hasattr(activity_rewards, "_detail_difference"))


class ActivityFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        def prepare_home(
            hwnd: int, *, logger: object, dry_run: bool = False, timeout: float = 30.0
        ) -> tuple[bool, Image.Image, str]:
            image = activity_rewards.safe_capture_client(hwnd, logger=logger)
            is_home, _details = activity_rewards.recognize_home_labels(image)
            reason = (
                "actionable home"
                if is_home
                else "activity entry requires fixed-position home or activity-page OCR"
            )
            return is_home, image, reason

        prepare_home_patch = patch(
            "activity_rewards.prepare_actionable_home", side_effect=prepare_home
        )
        self.prepare_home = prepare_home_patch.start()
        self.addCleanup(prepare_home_patch.stop)

    @patch("activity_rewards.safe_capture_client", side_effect=RuntimeError("boom"))
    @patch("activity_rewards.find_game_window", return_value=123)
    def test_direct_entry_exception_is_persisted(
        self,
        _window: MagicMock,
        _capture: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_root = Path(temporary)
            ok, reason = activity_rewards.run_activity_rewards(
                dry_run=False,
                log_root=log_root,
            )
            failure = (log_root / "failure.txt").read_text(encoding="utf-8")

        self.assertFalse(ok)
        self.assertIn("boom", reason)
        self.assertIn("boom", failure)

    def _run(self, **patches: object) -> tuple[bool, str, MagicMock]:
        image = Image.new("RGB", (1200, 675))
        click = MagicMock()
        defaults = {
            "find_game_window": MagicMock(return_value=123),
            "safe_capture_client": MagicMock(return_value=image),
            "recognize_home_labels": MagicMock(return_value=(True, {})),
            "detect_home_reward_notification": MagicMock(return_value=(True, {})),
            "_wait_for_image": MagicMock(return_value=(True, image)),
            "_is_activity_page": MagicMock(return_value=(True, {})),
            "find_red_exclamation_badges": MagicMock(return_value=[]),
            "click_ratio_logged": click,
            "swipe_ratio_logged": MagicMock(),
            "return_to_home": MagicMock(return_value=(True, "returned home")),
        }
        defaults.update(patches)
        with tempfile.TemporaryDirectory() as temporary:
            with patch.multiple(activity_rewards, **defaults):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )
        return ok, reason, click

    def test_home_without_red_exclamation_is_skipped_without_click(self) -> None:
        notification = MagicMock(return_value=(False, {}))
        ok, reason, click = self._run(detect_home_reward_notification=notification)
        self.assertTrue(ok)
        self.assertTrue(reason.startswith("skipped:"))
        click.assert_not_called()

    def test_blocking_overlay_cannot_be_skipped_as_no_reward(self) -> None:
        self.prepare_home.side_effect = None
        self.prepare_home.return_value = (
            False,
            Image.new("RGB", (1200, 675)),
            "blocking ad overlay remains",
        )
        badge = MagicMock(return_value=(False, {}))
        ok, reason, click = self._run(detect_home_reward_notification=badge)
        self.assertFalse(ok)
        self.assertIn("blocking ad overlay", reason)
        badge.assert_not_called()
        click.assert_not_called()

    def test_non_home_screen_is_rejected_before_badge_detection(self) -> None:
        badge = MagicMock(return_value=(True, {}))
        ok, reason, click = self._run(
            recognize_home_labels=MagicMock(return_value=(False, {})),
            detect_home_reward_notification=badge,
            _is_activity_page=MagicMock(return_value=(False, {})),
        )
        self.assertFalse(ok)
        self.assertIn("home or activity-page OCR", reason)
        badge.assert_not_called()
        click.assert_not_called()

    def test_already_open_activity_page_resumes_without_home_click(self) -> None:
        activity_page = MagicMock(return_value=(True, {"title": ["活动"]}))
        ok, reason, click = self._run(
            recognize_home_labels=MagicMock(return_value=(False, {})),
            _is_activity_page=activity_page,
            _activity_list_at_end=MagicMock(return_value=(True, {})),
        )
        self.assertTrue(ok, reason)
        click.assert_not_called()

    def test_dry_run_on_open_activity_page_never_clicks(self) -> None:
        image = Image.new("RGB", (1200, 675))
        click = MagicMock()
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(False, {})),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.click_ratio_logged", click),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=True,
                    log_root=Path(temporary),
                )
        self.assertTrue(ok, reason)
        self.assertIn("without clicking", reason)
        click.assert_not_called()

    def test_activity_entry_retries_when_badge_is_occluded_but_home_remains(self) -> None:
        image = Image.new("RGB", (1200, 675))
        click = MagicMock()
        wait = MagicMock(side_effect=[(False, image), (True, image)])
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch(
                    "activity_rewards.detect_home_reward_notification",
                    side_effect=[(True, {}), (False, {"reason": "temporarily occluded"})],
                ),
                patch("activity_rewards._wait_for_image", wait),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", return_value=[]),
                patch("activity_rewards._activity_list_at_end", return_value=(True, {})),
                patch("activity_rewards.click_ratio_logged", click),
                patch("activity_rewards.return_to_home", return_value=(True, "returned home")),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )
        self.assertTrue(ok, reason)
        self.assertEqual(
            [entry.kwargs["key"] for entry in click.call_args_list],
            ["home_activity", "home_activity_retry"],
        )

    def test_unchanged_list_after_swipe_is_failure_not_success(self) -> None:
        swipe = MagicMock()
        return_home = MagicMock(return_value=(True, "returned home"))
        image = Image.new("RGB", (1200, 675))
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch("activity_rewards.detect_home_reward_notification", return_value=(True, {})),
                patch("activity_rewards._wait_for_image", return_value=(True, image)),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", return_value=[]),
                patch("activity_rewards._activity_list_at_end", return_value=(False, {})),
                patch("activity_rewards._activity_list_identity", return_value=("EVENTA", "EVENTB")),
                patch("activity_rewards.click_ratio_logged"),
                patch("activity_rewards.swipe_ratio_logged", swipe),
                patch("activity_rewards.return_to_home", return_home),
                patch("activity_rewards.time.sleep"),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )
        self.assertFalse(ok)
        self.assertIn("滑动未生效", reason)
        self.assertEqual(swipe.call_count, 1)
        return_home.assert_not_called()

    def test_bottom_marker_finishes_scan_without_extra_swipe(self) -> None:
        swipe = MagicMock()
        return_home = MagicMock(return_value=(True, "returned home"))
        image = Image.new("RGB", (1200, 675))
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch("activity_rewards.detect_home_reward_notification", return_value=(True, {})),
                patch("activity_rewards._wait_for_image", return_value=(True, image)),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", return_value=[]),
                patch("activity_rewards._activity_list_at_end", return_value=(True, {"texts": ["登录活动"]})),
                patch("activity_rewards.click_ratio_logged"),
                patch("activity_rewards.swipe_ratio_logged", swipe),
                patch("activity_rewards.return_to_home", return_home),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )
        self.assertTrue(ok, reason)
        self.assertTrue(reason.startswith("skipped:"))
        swipe.assert_not_called()
        return_home.assert_called_once()

    def test_empty_scan_fails_when_home_ocr_is_not_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            logger = activity_rewards.RunLogger(Path(temporary))
            with patch(
                "activity_rewards.return_to_home",
                return_value=(False, "home text was not recognized"),
            ):
                ok, reason = activity_rewards._finish_at_home(
                    123,
                    logger=logger,
                    completed=0,
                )
        self.assertFalse(ok)
        self.assertEqual(reason, "home text was not recognized")

    def test_remaining_home_badge_reopens_activity_and_rescans(self) -> None:
        image = Image.new("RGB", (1200, 675))
        click = MagicMock()
        return_home = MagicMock(
            side_effect=[
                (False, "已返回主页，但活动红点仍存在，不能判定该环节完成"),
                (True, "已确认主页活动红点消失"),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch("activity_rewards.detect_home_reward_notification", return_value=(True, {})),
                patch("activity_rewards._wait_for_image", return_value=(True, image)),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", return_value=[]),
                patch("activity_rewards._activity_list_at_end", return_value=(True, {})),
                patch("activity_rewards.click_ratio_logged", click),
                patch("activity_rewards.return_to_home", return_home),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )

        self.assertTrue(ok, reason)
        self.assertEqual(return_home.call_count, 2)
        self.assertEqual(
            [entry.kwargs["key"] for entry in click.call_args_list],
            ["home_activity", "home_activity_reentry"],
        )

    def test_remaining_home_badge_reentry_has_hard_limit(self) -> None:
        image = Image.new("RGB", (1200, 675))
        click = MagicMock()
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.MAX_ACTIVITY_REENTRIES", 1),
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch("activity_rewards.detect_home_reward_notification", return_value=(True, {})),
                patch("activity_rewards._wait_for_image", return_value=(True, image)),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", return_value=[]),
                patch("activity_rewards._activity_list_at_end", return_value=(True, {})),
                patch("activity_rewards.click_ratio_logged", click),
                patch(
                    "activity_rewards.return_to_home",
                    return_value=(
                        False,
                        "已返回主页，但活动红点仍存在，不能判定该环节完成",
                    ),
                ),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )

        self.assertFalse(ok)
        self.assertIn("重新扫描 1 次后仍存在", reason)
        self.assertEqual(
            [entry.kwargs["key"] for entry in click.call_args_list],
            ["home_activity", "home_activity_reentry"],
        )

    def test_processed_activity_uses_completed_prefix(self) -> None:
        image = Image.new("RGB", (1200, 675))
        badge = {"center": (0.2, 0.3)}
        badge_results = [[badge]] + [[]] * 20
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch("activity_rewards.detect_home_reward_notification", return_value=(True, {})),
                patch("activity_rewards._wait_for_image", return_value=(True, image)),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", side_effect=badge_results),
                patch("activity_rewards._activity_list_at_end", return_value=(True, {})),
                patch("activity_rewards._click_marked_activity", return_value=(True, image, "selected")),
                patch("activity_rewards.recognize_activity_kind", return_value=("regular", {})),
                patch("activity_rewards._handle_activity", return_value=(True, image, "claimed")),
                patch("activity_rewards.click_ratio_logged"),
                patch("activity_rewards.swipe_ratio_logged"),
                patch("activity_rewards.return_to_home", return_value=(True, "returned home")),
                patch("activity_rewards.time.sleep"),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )
        self.assertTrue(ok)
        self.assertTrue(reason.startswith("completed:"))

    def test_repeating_one_action_hits_its_own_hard_limit(self) -> None:
        image = Image.new("RGB", (1200, 675))
        badge = {"center": (0.2, 0.3)}
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.MAX_ACTION_REPEATS", 1),
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch("activity_rewards.detect_home_reward_notification", return_value=(True, {})),
                patch("activity_rewards._wait_for_image", return_value=(True, image)),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", return_value=[badge]),
                patch("activity_rewards._click_marked_activity", return_value=(True, image, "selected")),
                patch("activity_rewards.recognize_activity_kind", return_value=("regular", {})),
                patch("activity_rewards._handle_activity", return_value=(True, image, "claimed")),
                patch("activity_rewards.click_ratio_logged"),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )
        self.assertFalse(ok)
        self.assertIn("per-action hard limit 1", reason)

    def test_token_exchange_uses_the_outer_scan_limit(self) -> None:
        image = Image.new("RGB", (1200, 675))
        badge = {"center": (0.2, 0.3)}
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.MAX_ACTION_REPEATS", 1),
                patch("activity_rewards.MAX_SCAN_CYCLES", 3),
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch("activity_rewards.detect_home_reward_notification", return_value=(True, {})),
                patch("activity_rewards._wait_for_image", return_value=(True, image)),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", return_value=[badge]),
                patch("activity_rewards._click_marked_activity", return_value=(True, image, "selected")),
                patch("activity_rewards.recognize_activity_kind", return_value=("token_exchange", {})),
                patch("activity_rewards._handle_activity", return_value=(True, image, "claimed")),
                patch("activity_rewards.click_ratio_logged"),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )
        self.assertFalse(ok)
        self.assertIn("activity scan exceeded hard limit 3", reason)

    def test_hard_scan_limit_is_failure(self) -> None:
        image = Image.new("RGB", (1200, 675))
        badge = {"center": (0.2, 0.3)}
        # Each cycle sees no badge before scrolling but a badge afterwards, so
        # the idle streak never reaches its normal stopping condition.
        badge_results = [[], [badge], [], [badge]]
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("activity_rewards.MAX_SCAN_CYCLES", 2),
                patch("activity_rewards.find_game_window", return_value=123),
                patch("activity_rewards.safe_capture_client", return_value=image),
                patch("activity_rewards.recognize_home_labels", return_value=(True, {})),
                patch("activity_rewards.detect_home_reward_notification", return_value=(True, {})),
                patch("activity_rewards._wait_for_image", return_value=(True, image)),
                patch("activity_rewards._is_activity_page", return_value=(True, {})),
                patch("activity_rewards.find_red_exclamation_badges", side_effect=badge_results),
                patch("activity_rewards.click_ratio_logged"),
                patch("activity_rewards.swipe_ratio_logged"),
                patch("activity_rewards.time.sleep"),
            ):
                ok, reason = activity_rewards.run_activity_rewards(
                    dry_run=False,
                    log_root=Path(temporary),
                )
        self.assertFalse(ok)
        self.assertIn("hard limit 2", reason)
        self.assertFalse(reason.startswith(("skipped:", "completed:")))


if __name__ == "__main__":
    unittest.main()
