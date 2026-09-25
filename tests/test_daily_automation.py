from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from PIL import Image, ImageDraw


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import open_game as open_game_module
from adaptive_wait import AdaptivePoll
from daily_automation import (
    CURRENT_PHASE_IDS,
    DAILY_READY_STATES,
    DesktopActivityGuard,
    DOWNLOAD_CONFIRM_CLICK,
    MAX_UNKNOWN_ENTRY_FRAMES,
    MasterLogger,
    can_finish_entry_phase,
    classify_daily_entry_context,
    daily_plan_report,
    enter_game_logged,
    ensure_home,
    game_day_key,
    mute_game_audio,
    overlay_transition_succeeded,
    recognize_daily_entry_state,
    return_home_transition_succeeded,
    run_daily,
    startup_promotion_transition_succeeded,
    wait_for_network,
)
from game_text_recognition import (
    LabelRecognitionSession,
    recognize_arena_auto_battle_labels,
    recognize_arena_cartridge_bar_labels,
    recognize_arena_cartridge_labels,
    recognize_arena_rank_change_labels,
    recognize_entry_status,
    recognize_gacha_animation_labels,
    recognize_gacha_target_labels,
    recognize_game_loading_labels,
    recognize_plaza_labels,
    recognize_quick_hunt_map_labels,
    recognize_quick_hunt_setup_labels,
    recognize_return_home_control,
    recognize_reward_overlay_labels,
    recognize_terms_agreement_labels,
)
from daily_arena import (
    ARENA_DIALOGUE_STATES,
    enter_arena_from_plaza,
    enter_battlefield,
    enter_battle_prep,
    is_gameplay_tab_selected,
    run_daily_arena,
    wait_for_cartridge_collection_ready,
)
from business_management import detect_regular_customer_note_notification
from enter_game import TOUCH_CLICK
from quick_hunt import (
    _quick_hunt_free_resource,
    _select_max_quick_hunt_count,
    detect_selected_quick_hunt_category,
    enter_quick_hunt,
    maximize_and_confirm_quick_hunt,
    run_crystal_cave_cycle,
)
from home_notifications import (
    detect_home_reward_notification,
    detect_notification_badge,
    detect_red_exclamation_badge,
    find_red_exclamation_badges,
)
from pass_rewards import _has_pass_item_popup_close, enter_pass_rewards
from free_gacha import (
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
    def test_home_notification_badges_detect_only_the_expected_icon_corner(self) -> None:
        image = Image.new("RGB", (2570, 1506))
        draw = ImageDraw.Draw(image)
        draw.polygon(((2253, 303), (2263, 313), (2253, 323), (2243, 313)), fill=(220, 25, 45))
        draw.line((2253, 308, 2253, 313), fill="white")
        draw.point((2253, 317), fill="white")

        pass_found, pass_details = detect_home_reward_notification(image, "pass")
        gacha_found, _gacha_details = detect_home_reward_notification(image, "gacha")

        self.assertTrue(pass_found)
        self.assertFalse(gacha_found)
        self.assertGreaterEqual(pass_details["red_pixels"], 100)

    def test_notification_badge_rejects_a_large_red_background_area(self) -> None:
        image = Image.new("RGB", (1000, 600))
        draw = ImageDraw.Draw(image)
        draw.rectangle((865, 130, 895, 160), fill=(220, 25, 45))

        found, details = detect_notification_badge(image, (0.868, 0.200, 0.020, 0.030))

        self.assertFalse(found)
        self.assertGreater(details["largest_component_pixels"], details["maximum_component_pixels"])

    @patch("pass_rewards.recognize_text_at")
    def test_pass_item_popup_uses_fixed_position_text(
        self,
        recognize_text_at: MagicMock,
    ) -> None:
        recognize_text_at.return_value = (True, {"available": True})

        self.assertTrue(_has_pass_item_popup_close(Image.new("RGB", (1000, 600))))
        recognize_text_at.assert_called_once_with(
            unittest.mock.ANY,
            unittest.mock.ANY,
            ("拥有", "使用处", "查看获取途径"),
        )

    def test_red_exclamation_badge_requires_white_mark_inside_red_diamond(self) -> None:
        image = Image.new("RGB", (1000, 600))
        draw = ImageDraw.Draw(image)
        draw.polygon(((210, 190), (220, 180), (230, 190), (220, 200)), fill=(220, 25, 45))
        draw.line((220, 185, 220, 193), fill="white", width=3)
        draw.ellipse((219, 196, 221, 198), fill="white")
        draw.rectangle((210, 260, 230, 270), fill=(220, 25, 45))

        badges = find_red_exclamation_badges(image, (0.200, 0.175, 0.035, 0.430))

        self.assertEqual(len(badges), 1)
        self.assertGreaterEqual(badges[0]["red_pixels"], 100)

    def test_fixed_red_exclamation_region_requires_diamond_and_mark(self) -> None:
        image = Image.new("RGB", (1000, 600))
        draw = ImageDraw.Draw(image)
        draw.polygon(((220, 190), (230, 180), (240, 190), (230, 200)), fill=(220, 25, 45))
        draw.line((230, 185, 230, 193), fill="white", width=2)
        draw.ellipse((229, 196, 231, 198), fill="white")

        found, details = detect_red_exclamation_badge(image, (0.200, 0.275, 0.050, 0.080))

        self.assertTrue(found)
        self.assertGreater(details["red_pixels"], 100)
        self.assertGreaterEqual(details["exclamation_pixels"], 4)

    @patch("free_gacha.click_with_fixed_retry")
    @patch("free_gacha.detect_home_reward_notification", return_value=(False, {}))
    @patch("free_gacha.classify_state", return_value=("real_home", {}))
    @patch("free_gacha.safe_capture_client", return_value=Image.new("RGB", (2000, 1000)))
    @patch("free_gacha.find_game_window", return_value=123)
    def test_free_gacha_does_not_open_without_a_home_badge(
        self,
        _find_game_window: MagicMock,
        _safe_capture_client: MagicMock,
        _classify_state: MagicMock,
        _detect_notification: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_free_gacha(
                targets=["costume"],
                timeout=5.0,
                interval=0.0,
                dry_run=False,
                test_mode=False,
                log_root=Path(temporary),
            )

        self.assertEqual(result.reason, "gacha has no home reward notification")
        click_with_fixed_retry.assert_not_called()

    @patch("quick_hunt.click_with_fixed_retry")
    @patch("quick_hunt.detect_home_reward_notification", return_value=(False, {}))
    @patch("quick_hunt.classify_state", return_value=("real_home", {}))
    @patch("quick_hunt.safe_capture_client", return_value=Image.new("RGB", (2000, 1000)))
    @patch("quick_hunt.find_game_window", return_value=123)
    def test_quick_hunt_does_not_open_without_a_home_badge(
        self,
        _find_game_window: MagicMock,
        _safe_capture_client: MagicMock,
        _classify_state: MagicMock,
        _detect_notification: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_quick_hunt(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "quick_hunt has no home reward notification")
        click_with_fixed_retry.assert_not_called()

    @patch("quick_hunt.click_with_fixed_retry")
    @patch("quick_hunt.detect_home_reward_notification", return_value=(False, {}))
    @patch("quick_hunt.classify_state", return_value=("real_home", {}))
    @patch("quick_hunt.safe_capture_client", return_value=Image.new("RGB", (2000, 1000)))
    @patch("quick_hunt.find_game_window", return_value=123)
    def test_quick_hunt_can_force_entry_without_a_home_badge(
        self,
        _find_game_window: MagicMock,
        _safe_capture_client: MagicMock,
        _classify_state: MagicMock,
        _detect_notification: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        click_with_fixed_retry.return_value = (True, "quick_hunt_map", image, "opened")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_quick_hunt(
                dry_run=False,
                log_root=Path(temporary),
                require_notification=False,
            )

        self.assertTrue(ok)
        self.assertIn("resulting state=quick_hunt_map", reason)
        click_with_fixed_retry.assert_called_once()

    @patch("pass_rewards._capture_until")
    @patch("pass_rewards.click_ratio_logged")
    @patch("pass_rewards.detect_home_reward_notification", return_value=(True, {}))
    @patch("pass_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("pass_rewards.safe_capture_client")
    @patch("pass_rewards.find_game_window", return_value=123)
    def test_pass_entry_clicks_only_after_a_confirmed_notification(
        self,
        _find_game_window: MagicMock,
        safe_capture_client: MagicMock,
        _recognize_home: MagicMock,
        _detect_notification: MagicMock,
        click_ratio_logged: MagicMock,
        capture_until: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.return_value = image
        capture_until.return_value = (True, image, {"available": True})
        with tempfile.TemporaryDirectory() as temporary:
            ok, _reason = enter_pass_rewards(dry_run=False, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(click_ratio_logged.call_args.kwargs["key"], "home_pass")

    def test_fixed_reward_quick_hunt_setup_accepts_any_hunt_count(self) -> None:
        session = MagicMock()
        session.recognize.return_value = (
            {
                "header": ["快速狩猎", "仅使用免费火把", "水之洞穴极难"],
                "body": ["狩猎20次", "将进行100次一般怪物／20次首领怪物战斗。", "固定奖励"],
                "buttons": ["取消", "狩猎120"],
            },
            {"header": ["快速狩猎"], "body": [], "buttons": ["取消"]},
            None,
        )

        matched, details = recognize_quick_hunt_setup_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)
        self.assertEqual(details["requirements"]["body"], "狩猎N次")

    def test_quick_hunt_map_accepts_hunting_locations_without_legacy_sidebar(
        self,
    ) -> None:
        session = MagicMock()
        session.recognize.return_value = (
            {
                "left_categories": [],
                "hunting_ground_locations": ["野猪洞穴", "废弃矿山", "星落洞穴"],
                "start_button": ["快速狩猎"],
            },
            {
                "left_categories": [],
                "hunting_ground_locations": ["野猪洞穴", "废弃矿山", "星落洞穴"],
                "start_button": ["快速狩猎"],
            },
            None,
        )

        matched, details = recognize_quick_hunt_map_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)
        self.assertEqual(len(details["matches"]["hunting_ground_locations"]), 3)

    @patch("quick_hunt.recognize_quick_hunt_map_labels")
    def test_hunting_locations_confirm_selected_category_without_legacy_sidebar(
        self,
        recognize_quick_hunt_map_labels: MagicMock,
    ) -> None:
        recognize_quick_hunt_map_labels.return_value = (
            True,
            {"matches": {"hunting_ground_locations": ["野猪洞穴", "废弃矿山"]}},
        )

        category, scores = detect_selected_quick_hunt_category(
            Image.new("RGB", (2000, 1000))
        )

        self.assertEqual(category, "hunting_ground")
        self.assertEqual(scores["hunting_ground_location_matches"], 2.0)

    @patch("quick_hunt.time.sleep")
    @patch("quick_hunt.time.monotonic", side_effect=[0.0, 0.0, 0.0])
    @patch("quick_hunt.is_quick_hunt_count_at_max", return_value=(True, {}))
    @patch("quick_hunt._quick_hunt_count", return_value=None)
    @patch("quick_hunt.classify_state", return_value=("quick_hunt_setup", {}))
    @patch("quick_hunt.safe_capture_client")
    @patch("quick_hunt._click_ratio")
    def test_quick_hunt_max_uses_the_full_slider_not_the_count_text(
        self,
        _click_ratio: MagicMock,
        safe_capture_client: MagicMock,
        _classify_state: MagicMock,
        _quick_hunt_count: MagicMock,
        _at_max: MagicMock,
        _monotonic: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.return_value = image
        logger = MagicMock()
        logger.save_image.return_value = Path("max.png")

        ok, _reason, _image, count = _select_max_quick_hunt_count(
            123,
            image,
            1,
            dry_run=False,
            logger=logger,
        )

        self.assertTrue(ok)
        self.assertIsNone(count)

    def test_quick_hunt_free_resource_reads_the_resource_counter(self) -> None:
        details = {
            "quick_hunt_setup_text": {
                "texts": {"free_resource": ["0/90 | +8.2K"]},
            }
        }

        self.assertEqual(_quick_hunt_free_resource(details), (0, 90))

    @patch("quick_hunt.click_with_fixed_retry")
    @patch("quick_hunt.is_quick_hunt_count_at_max", return_value=(False, {}))
    @patch("quick_hunt.classify_state")
    @patch("quick_hunt.safe_capture_client")
    @patch("quick_hunt.find_game_window", return_value=123)
    def test_quick_hunt_skips_hunting_ground_when_free_rice_is_exhausted(
        self,
        _find_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        _at_max: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.return_value = image
        classify_state.return_value = (
            "quick_hunt_setup",
            {
                "quick_hunt_setup_text": {
                    "texts": {
                        "body": ["狩猎1次"],
                        "free_resource": ["0/90 | +8.2K"],
                    }
                }
            },
        )
        click_with_fixed_retry.return_value = (
            True,
            "quick_hunt_map",
            image,
            "cancelled empty quick hunt",
        )

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = maximize_and_confirm_quick_hunt(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "skipped: free rice exhausted (0/90)")
        self.assertEqual(click_with_fixed_retry.call_args.args[2], "quick_hunt_cancel")

    @patch("quick_hunt.click_with_fixed_retry")
    @patch("quick_hunt.is_quick_hunt_count_at_max", return_value=(False, {}))
    @patch("quick_hunt.classify_state")
    @patch("quick_hunt.safe_capture_client")
    @patch("quick_hunt.find_game_window", return_value=123)
    def test_crystal_cave_skips_when_free_torches_are_exhausted(
        self,
        _find_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        _at_max: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.return_value = image
        classify_state.side_effect = [
            ("quick_hunt_map", {}),
            (
                "quick_hunt_setup",
                {
                    "quick_hunt_setup_text": {
                        "texts": {
                            "body": ["狩猎1次"],
                            "free_resource": ["0/60+813"],
                        }
                    }
                },
            ),
        ]
        click_with_fixed_retry.side_effect = [
            (True, "quick_hunt_map", image, "selected crystal cave"),
            (True, "quick_hunt_setup", image, "opened setup"),
            (True, "quick_hunt_map", image, "closed empty setup"),
            (True, "real_home", image, "returned home"),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = run_crystal_cave_cycle(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "skipped: free torches exhausted (0/60)")
        self.assertEqual(
            [call.args[2] for call in click_with_fixed_retry.call_args_list],
            [
                "quick_hunt_crystal_cave",
                "quick_hunt_start",
                "quick_hunt_cancel",
                "quick_hunt_back",
            ],
        )

    def test_equipment_reveal_uses_positioned_detail_labels(self) -> None:
        session = MagicMock()

        def recognize(groups: dict[str, object]):
            x, y, width, height = groups["equipment_details"]["region"]
            self.assertLessEqual(x, 0.07)
            self.assertLessEqual(y, 0.15)
            self.assertGreaterEqual(x + width, 0.25)
            self.assertGreaterEqual(y + height, 0.40)
            return (
                {
                    "equipment_details": [
                        "EQUIPMENT TYPE",
                        "WEAPON",
                        "威格 专用装备",
                    ]
                },
                {"equipment_details": ["EQUIPMENT TYPE", "WEAPON", "专用装备"]},
                None,
            )

        session.recognize.side_effect = recognize

        matched, details = recognize_gacha_animation_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)
        self.assertEqual(len(details["matches"]["equipment_details"]), 3)

    def test_costume_reveal_uses_element_type_and_costume_labels(self) -> None:
        session = MagicMock()
        session.recognize.return_value = (
            {
                "equipment_details": [
                    "ELEMENT TYPE",
                    "LIGHT",
                    "艾玛",
                    "服装",
                ]
            },
            {"equipment_details": ["ELEMENT TYPE", "服装"]},
            None,
        )

        matched, details = recognize_gacha_animation_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)
        self.assertEqual(details["matches"]["equipment_details"], ["ELEMENT TYPE", "服装"])

    def test_season_reward_and_return_hint_form_an_actionable_overlay(self) -> None:
        session = MagicMock()

        def recognize(groups: dict[str, object]):
            header_x, header_y, header_width, header_height = groups["header"]["region"]
            footer_x, footer_y, footer_width, footer_height = groups["footer"]["region"]
            self.assertTrue(header_x <= 0.666 <= header_x + header_width)
            self.assertTrue(header_y <= 0.236 <= header_y + header_height)
            self.assertTrue(footer_x <= 0.664 <= footer_x + footer_width)
            self.assertTrue(footer_y <= 0.777 <= footer_y + footer_height)
            return (
                {
                    "header": ["赛季奖励"],
                    "footer": ["点击画面即可返回。"],
                },
                {
                    "header": ["赛季奖励"],
                    "footer": ["点击画面即可返回"],
                },
                None,
            )

        session.recognize.side_effect = recognize

        matched, _details = recognize_reward_overlay_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)

    def test_task_interval_reward_and_return_hint_form_an_actionable_overlay(self) -> None:
        session = MagicMock()
        session.recognize.return_value = (
            {
                "header": ["获得区间奖励"],
                "footer": ["点击画面即可返回。"],
            },
            {
                "header": ["获得区间奖励"],
                "footer": ["点击画面即可返回"],
            },
            None,
        )

        matched, _details = recognize_reward_overlay_labels(
            Image.new("RGB", (80, 45)),
            session=session,
        )

        self.assertTrue(matched)

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

    def test_arena_auto_battle_dialog_accepts_current_start_button_text(self) -> None:
        session = LabelRecognitionSession(Image.new("RGB", (80, 45)))
        observations = [
            (0.50, 0.30, "自动战斗"),
            (0.50, 0.45, "MAX"),
            (0.40, 0.70, "取消"),
            (0.60, 0.70, "战斗开始"),
        ]
        with patch.object(session, "_load", return_value=(observations, None)):
            matched, details = recognize_arena_auto_battle_labels(
                Image.new("RGB", (80, 45)),
                session=session,
            )

        self.assertTrue(matched)
        self.assertEqual(
            set(details["matches"]["dialog"]),
            {"自动战斗", "MAX", "取消", "战斗开始"},
        )

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
            patch("open_game.subprocess.Popen") as start_process,
            patch("open_game.find_game_window", side_effect=lambda: next(windows)),
            patch("open_game._starter_log_activity", side_effect=lambda: next(log_activity)),
            patch("open_game.time.monotonic", side_effect=lambda: clock[0]),
            patch("open_game.time.sleep", side_effect=sleep),
        ):
            hwnd = open_game_module.open_game(timeout=5.0)

        self.assertEqual(hwnd, 123)
        self.assertGreater(clock[0], 5.0)
        start_process.assert_called_once_with(
            ["starter.exe", "browndust2:games/10000002?usn=0"],
            cwd=".",
            stdout=open_game_module.subprocess.DEVNULL,
            stderr=open_game_module.subprocess.DEVNULL,
        )

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

    def test_stalled_starter_is_closed_and_retried_once(self) -> None:
        clock = [0.0]
        launches = [0]
        starter = MagicMock()
        starter.exists.return_value = True
        starter.__str__.return_value = "starter.exe"
        starter.parent = "."
        first_process = MagicMock()
        first_process.poll.return_value = None
        second_process = MagicMock()

        def popen(*_args: object, **_kwargs: object) -> MagicMock:
            launches[0] += 1
            return first_process if launches[0] == 1 else second_process

        def find_window() -> int:
            return 123 if launches[0] >= 2 else 0

        def sleep(seconds: float) -> None:
            clock[0] += seconds * 3

        with (
            patch("open_game.STARTER", starter),
            patch("open_game.subprocess.Popen", side_effect=popen) as start_process,
            patch("open_game.find_game_window", side_effect=find_window),
            patch("open_game._starter_log_activity", return_value=()),
            patch("open_game.time.monotonic", side_effect=lambda: clock[0]),
            patch("open_game.time.sleep", side_effect=sleep),
        ):
            hwnd = open_game_module.open_game(timeout=5.0)

        self.assertEqual(hwnd, 123)
        self.assertEqual(start_process.call_count, 2)
        first_process.terminate.assert_called_once_with()
        first_process.wait.assert_called_once_with(timeout=5.0)
        second_process.terminate.assert_not_called()


class DailyAutomationStateTests(unittest.TestCase):
    @patch("daily_automation._pulse_desktop_activity")
    @patch("daily_automation.threading.Thread")
    def test_desktop_activity_guard_pulses_immediately_and_stops_cleanly(
        self,
        thread_type: MagicMock,
        pulse: MagicMock,
    ) -> None:
        thread = thread_type.return_value
        guard = DesktopActivityGuard(interval=30.0)

        with guard:
            pulse.assert_called_once_with()
            thread.start.assert_called_once_with()

        self.assertTrue(guard._stop.is_set())
        thread.join.assert_called_once_with(timeout=1.0)

    @patch("daily_automation._pulse_desktop_activity")
    def test_desktop_activity_guard_repeats_until_stopped(self, pulse: MagicMock) -> None:
        guard = DesktopActivityGuard(interval=30.0)
        guard._stop = MagicMock()
        guard._stop.wait.side_effect = [False, True]

        guard._run()

        guard._stop.wait.assert_has_calls([unittest.mock.call(30.0), unittest.mock.call(30.0)])
        pulse.assert_called_once_with()

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

    def test_scheduled_launcher_uses_a_visible_python_console(self) -> None:
        script = (TOOLS_DIR / "install_daily_task.ps1").read_text(encoding="utf-8")

        self.assertIn("Get-Command python.exe", script)
        self.assertNotIn("pythonw.exe", script)
        self.assertIn("New-ScheduledTaskTrigger -Daily -At $At", script)
        self.assertIn('[string]$At = "08:30"', script)

    def test_scheduled_runner_can_force_one_available_phase(self) -> None:
        script = (TOOLS_DIR / "run_daily_task.ps1").read_text(encoding="utf-8")

        self.assertIn("[string]$ForcePhase", script)
        self.assertIn('$SupervisorArguments += @("--force-phase", $ForcePhase)', script)

    def test_scheduled_runner_uses_supervisor_without_annotation_wait_loop(self) -> None:
        script = (TOOLS_DIR / "run_daily_task.ps1").read_text(encoding="utf-8")

        self.assertIn('"daily_supervisor.py"', script)
        self.assertNotIn("recognition_review", script)
        self.assertNotIn("while ($true)", script)

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

    def test_plan_report_is_read_only_and_keeps_target_order(self) -> None:
        report = daily_plan_report()

        self.assertEqual(report["compatibility_stage_ids"], list(CURRENT_PHASE_IDS))
        self.assertEqual([item["preset"] for item in report["presets"]], ["fast", "detailed"])
        self.assertEqual(report["presets"][0]["contract_stage_count"], 17)

    @patch("daily_automation.os.chdir")
    @patch("daily_automation.wait_for_network", return_value=True)
    @patch("daily_automation.DesktopActivityGuard")
    @patch("daily_automation._execute_daily_phase", return_value="completed")
    @patch("builtins.print")
    def test_daily_run_uses_current_capabilities_in_target_order(
        self,
        _print: MagicMock,
        execute_phase: MagicMock,
        desktop_guard_type: MagicMock,
        _wait_for_network: MagicMock,
        _chdir: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_daily(
                project_root=Path(temporary), force=False, network_timeout=1.0
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            [call.args[0] for call in execute_phase.call_args_list],
            list(CURRENT_PHASE_IDS),
        )
        self.assertLess(CURRENT_PHASE_IDS.index("quick_hunt"), CURRENT_PHASE_IDS.index("free_gacha"))
        self.assertLess(CURRENT_PHASE_IDS.index("activity_rewards"), CURRENT_PHASE_IDS.index("pass_rewards"))
        desktop_guard_type.return_value.start.assert_called_once_with()
        desktop_guard_type.return_value.stop.assert_called_once_with()

    @patch("daily_automation.os.chdir")
    @patch("daily_automation.wait_for_network")
    @patch("daily_automation.DesktopActivityGuard")
    @patch("builtins.print")
    def test_incomplete_target_preset_is_blocked_before_game_or_network(
        self,
        _print: MagicMock,
        desktop_guard_type: MagicMock,
        wait_for_network: MagicMock,
        _chdir: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_daily(
                project_root=Path(temporary),
                force=False,
                network_timeout=1.0,
                preset="fast",
            )

        self.assertEqual(result, 2)
        wait_for_network.assert_not_called()
        desktop_guard_type.assert_not_called()

    @patch("daily_automation.os.chdir")
    @patch("daily_automation.wait_for_network", return_value=True)
    @patch("daily_automation.DesktopActivityGuard")
    @patch("daily_automation._execute_daily_phase", return_value="completed")
    @patch("builtins.print")
    def test_force_phase_runs_only_the_requested_available_phase(
        self,
        _print: MagicMock,
        execute_phase: MagicMock,
        _desktop_guard_type: MagicMock,
        _wait_for_network: MagicMock,
        _chdir: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_daily(
                project_root=Path(temporary),
                force=False,
                force_phase="quick_hunt",
                network_timeout=1.0,
            )

        self.assertEqual(result, 0)
        execute_phase.assert_called_once()
        self.assertEqual(execute_phase.call_args.args[0], "quick_hunt")

    @patch("daily_automation.os.chdir")
    @patch("daily_automation.wait_for_network", return_value=True)
    @patch("daily_automation.DesktopActivityGuard")
    @patch("daily_automation.find_game_window", return_value=0)
    @patch("daily_automation.enter_game_logged", return_value=(True, "game ready"))
    @patch("daily_automation._execute_daily_phase")
    @patch("builtins.print")
    def test_failed_run_resumes_without_replaying_completed_phases(
        self,
        _print: MagicMock,
        execute_phase: MagicMock,
        enter_game: MagicMock,
        _find_game_window: MagicMock,
        _desktop_guard_type: MagicMock,
        _wait_for_network: MagicMock,
        _chdir: MagicMock,
    ) -> None:
        calls: list[str] = []
        failed_once = False

        def execute(phase_id: str, **_kwargs: object) -> str:
            nonlocal failed_once
            calls.append(phase_id)
            if phase_id == "arena" and not failed_once:
                failed_once = True
                raise RuntimeError("arena stopped")
            return "completed"

        execute_phase.side_effect = execute
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = run_daily(project_root=root, force=False, network_timeout=1.0)
            second = run_daily(project_root=root, force=False, network_timeout=1.0)

        self.assertEqual((first, second), (2, 0))
        self.assertEqual(calls.count("start"), 1)
        self.assertEqual(calls.count("quick_hunt"), 1)
        self.assertEqual(calls.count("free_gacha"), 1)
        self.assertEqual(calls.count("arena"), 2)
        self.assertEqual(calls[-1], "mail_rewards")
        enter_game.assert_called_once()
        self.assertEqual(
            enter_game.call_args.kwargs["log_root"].name,
            "00-resume-game",
        )

    @patch("daily_automation.os.chdir")
    @patch("daily_automation.wait_for_network", return_value=True)
    @patch("daily_automation.DesktopActivityGuard")
    @patch("daily_automation._execute_daily_phase", return_value="completed")
    @patch("builtins.print")
    def test_completed_same_day_run_does_not_inject_desktop_activity_again(
        self,
        _print: MagicMock,
        _execute_phase: MagicMock,
        desktop_guard_type: MagicMock,
        _wait_for_network: MagicMock,
        _chdir: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(run_daily(project_root=root, force=False, network_timeout=1.0), 0)
            desktop_guard_type.reset_mock()
            self.assertEqual(run_daily(project_root=root, force=False, network_timeout=1.0), 0)

        desktop_guard_type.assert_not_called()

    @patch("daily_automation.os.chdir")
    @patch("daily_automation.DesktopActivityGuard")
    @patch("daily_automation.wait_for_network", return_value=False)
    @patch("builtins.print")
    def test_network_wait_failure_does_not_inject_desktop_activity(
        self,
        _print: MagicMock,
        _wait_for_network: MagicMock,
        desktop_guard_type: MagicMock,
        _chdir: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_daily(
                project_root=Path(temporary), force=False, network_timeout=1.0
            )

        self.assertEqual(result, 2)
        desktop_guard_type.assert_not_called()


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
                    "bright_ratio": 0.059812,
                },
                {
                    "mid_ratio": 0.709,
                    "edge_ratio": 0.014631,
                },
            )
        )

    def test_restaurant_ui_fade_is_not_a_gacha_reveal(self) -> None:
        self.assertFalse(
            _is_reveal_animation_like(
                {
                    "edge_ratio": 0.024244,
                    "bright_ratio": 0.0,
                },
                {
                    "bright_ratio": 0.0,
                },
                {
                    "mid_ratio": 0.203245,
                    "edge_ratio": 0.009971,
                },
            )
        )

    @patch("free_gacha.recognize_gacha_animation_labels")
    @patch("game_text_recognition._ocr_engine")
    def test_equipment_reveal_text_and_skip_control_override_background_brightness(
        self,
        ocr_engine: MagicMock,
        recognize_animation: MagicMock,
    ) -> None:
        ocr_result = MagicMock(boxes=None, txts=None, scores=None)
        ocr_engine.return_value = MagicMock(return_value=ocr_result)
        recognize_animation.return_value = (
            True,
            {
                "available": True,
                "matches": {
                    "equipment_details": ["EQUIPMENT TYPE", "WEAPON", "专用装备"]
                },
            },
        )
        image = Image.new("RGB", (2000, 1000), (180, 180, 180))
        draw = ImageDraw.Draw(image)
        draw.line((1840, 30, 1880, 55, 1840, 80), fill="white", width=10)
        draw.line((1880, 30, 1920, 55, 1880, 80), fill="white", width=10)

        state, details = classify_state(image)

        self.assertEqual(state, "gacha_animation")
        self.assertEqual(
            details["classification_rule"],
            "gacha_animation_text_and_skip_control",
        )
        self.assertGreater(details["animation_skip_control"]["edge_ratio"], 0.010)

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

    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state", return_value=("real_home", {}))
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_battlefield_entry_accepts_a_returnable_story_scene(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify: MagicMock,
        click_with_retry: MagicMock,
    ) -> None:
        home_image = Image.new("RGB", (80, 45), color=(10, 10, 10))
        with Image.open(FIXTURES / "entry-story-scene-home-button-v2318.png") as source:
            story_image = source.copy()
        capture_client.return_value = home_image

        def click_effect(*_args: object, **kwargs: object) -> tuple[bool, str, Image.Image, str]:
            accepted = kwargs["verify"]("unknown", story_image)
            return accepted, "unknown", story_image, "opened the last battlefield"

        click_with_retry.side_effect = click_effect

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_battlefield(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "opened the last battlefield")

    @patch("daily_arena.recognize_home_labels", return_value=(True, {"matched": True}))
    @patch("daily_arena.click_with_fixed_retry")
    @patch(
        "daily_arena.classify_state",
        return_value=("blocking_ad_overlay", {"classification_rule": "blocking_overlay_brightness"}),
    )
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_battlefield_entry_accepts_a_brightness_misclassified_home(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify: MagicMock,
        click_with_retry: MagicMock,
        recognize_home: MagicMock,
    ) -> None:
        home_image = Image.new("RGB", (80, 45), color=(240, 240, 240))
        arena_image = Image.new("RGB", (80, 45), color=(20, 20, 20))
        capture_client.return_value = home_image
        click_with_retry.return_value = (
            True,
            "arena_lobby",
            arena_image,
            "opened the arena lobby",
        )

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_battlefield(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "opened the arena lobby")
        recognize_home.assert_called_once_with(home_image)
        self.assertEqual(click_with_retry.call_args.args[2], "home_return_battlefield")

    @patch("daily_arena.recognize_return_home_control", return_value=(True, {"found": True}))
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state", return_value=("real_home", {}))
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_battlefield_entry_accepts_a_brightness_misclassified_arena_scene(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify: MagicMock,
        click_with_retry: MagicMock,
        _recognize_home: MagicMock,
    ) -> None:
        home_image = Image.new("RGB", (80, 45), color=(10, 10, 10))
        arena_image = Image.new("RGB", (80, 45), color=(20, 20, 20))
        capture_client.return_value = home_image

        def click_effect(*_args: object, **kwargs: object) -> tuple[bool, str, Image.Image, str]:
            accepted = kwargs["verify"]("blocking_ad_overlay", arena_image)
            return accepted, "blocking_ad_overlay", arena_image, "opened the arena lobby"

        click_with_retry.side_effect = click_effect

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_battlefield(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "opened the arena lobby")

    @patch("daily_arena.recognize_return_home_control", return_value=(True, {"found": True}))
    @patch("daily_arena.classify_state", return_value=("unknown", {}))
    @patch("daily_arena.safe_capture_client", return_value=Image.new("RGB", (80, 45)))
    @patch("daily_arena.find_game_window", return_value=123)
    def test_battlefield_entry_resumes_from_an_existing_returnable_scene(
        self,
        _find_window: MagicMock,
        _capture_client: MagicMock,
        _classify: MagicMock,
        _recognize_home: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_battlefield(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "already in a returnable battlefield scene")

    @patch("daily_arena.time.sleep")
    @patch("daily_arena._click_ratio")
    @patch("daily_arena.post_quick_cartridge_key")
    @patch("daily_arena.wait_for_state")
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.recognize_return_home_control", return_value=(True, {"found": True}))
    @patch("daily_arena.classify_state")
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_cartridge_route_uses_p_shortcut_from_a_returnable_story_scene(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        classify: MagicMock,
        _recognize_home: MagicMock,
        click_with_retry: MagicMock,
        wait_for_state: MagicMock,
        post_quick_cartridge: MagicMock,
        click_ratio: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        field_image = Image.new("RGB", (80, 45), color=(10, 10, 10))
        bar_image = Image.new("RGB", (80, 45), color=(20, 20, 20))
        gameplay_image = Image.new("RGB", (80, 45), color=(30, 30, 30))
        lobby_image = Image.new("RGB", (80, 45), color=(40, 40, 40))
        capture_client.side_effect = [field_image, lobby_image]
        classify.side_effect = [("unknown", {}), ("arena_lobby", {})]
        wait_for_state.return_value = ("arena_cartridge_bar", bar_image)
        click_with_retry.return_value = (
            True,
            "arena_cartridge_bar",
            gameplay_image,
            "selected gameplay tab",
        )

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_arena_from_plaza(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertIn("arena lobby reached", reason)
        post_quick_cartridge.assert_called_once_with(
            123,
            dry_run=False,
            logger=ANY,
        )
        self.assertEqual(click_with_retry.call_args.args[2], "cartridge_gameplay_tab")
        click_ratio.assert_called_once()

    @patch("daily_arena.time.sleep")
    @patch("daily_arena._click_ratio")
    @patch("daily_arena.post_quick_cartridge_key")
    @patch("daily_arena.wait_for_state")
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.recognize_return_home_control", return_value=(True, {"found": True}))
    @patch("daily_arena.classify_state")
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_cartridge_route_clicks_visible_button_when_p_shortcut_is_ignored(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        classify: MagicMock,
        _recognize_home: MagicMock,
        click_with_retry: MagicMock,
        wait_for_state: MagicMock,
        post_quick_cartridge: MagicMock,
        click_ratio: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        field_image = Image.new("RGB", (80, 45), color=(10, 10, 10))
        bar_image = Image.new("RGB", (80, 45), color=(20, 20, 20))
        gameplay_image = Image.new("RGB", (80, 45), color=(30, 30, 30))
        lobby_image = Image.new("RGB", (80, 45), color=(40, 40, 40))
        capture_client.side_effect = [field_image, lobby_image]
        classify.side_effect = [("unknown", {}), ("arena_lobby", {})]
        wait_for_state.side_effect = [
            ("unknown", field_image),
            ("arena_cartridge_bar", bar_image),
        ]
        click_with_retry.return_value = (
            True,
            "arena_cartridge_bar",
            gameplay_image,
            "selected gameplay tab",
        )

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_arena_from_plaza(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertIn("arena lobby reached", reason)
        post_quick_cartridge.assert_called_once_with(123, dry_run=False, logger=ANY)
        self.assertEqual(wait_for_state.call_count, 2)
        self.assertEqual(click_ratio.call_args_list[0].args[2], "plaza_cartridge")
        self.assertEqual(click_ratio.call_args_list[1].args[2], "cartridge_first_gameplay")

    @patch("daily_arena.confirm_optional_rank_change", return_value=(True, "done"))
    @patch("daily_arena.leave_arena_victory", return_value=(True, "left arena"))
    @patch("daily_arena.wait_and_close_repeat_result", return_value=(True, "closed result"))
    @patch("daily_arena.maximize_and_start_auto_battle", return_value=(True, "started battle"))
    @patch("daily_arena.open_auto_battle", return_value=(True, "opened auto battle"))
    @patch("daily_arena.enter_battle_prep", return_value=(True, "entered battle prep"))
    @patch("daily_arena.enter_arena_from_plaza", return_value=(True, "entered arena"))
    @patch("daily_arena.is_returnable_battlefield", return_value=True)
    @patch("daily_arena.classify_state")
    @patch("daily_arena.safe_capture_client", return_value=Image.new("RGB", (80, 45)))
    @patch("daily_arena.find_game_window", return_value=123)
    @patch("daily_arena.enter_battlefield", return_value=(True, "entered battlefield"))
    def test_daily_arena_routes_a_returnable_story_scene_to_quick_cartridge(
        self,
        _enter_battlefield: MagicMock,
        _find_window: MagicMock,
        _capture_client: MagicMock,
        classify: MagicMock,
        is_returnable: MagicMock,
        enter_arena: MagicMock,
        enter_battle_prep: MagicMock,
        _open_auto_battle: MagicMock,
        _start_auto_battle: MagicMock,
        _close_result: MagicMock,
        _leave_arena: MagicMock,
        _confirm_rank: MagicMock,
    ) -> None:
        classify.side_effect = [("unknown", {}), ("arena_battle_prep", {})]

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = run_daily_arena(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "done")
        is_returnable.assert_called_once()
        enter_arena.assert_called_once()
        enter_battle_prep.assert_not_called()

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
            ) as click_with_retry,
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
        self.assertEqual(click_with_retry.call_args.kwargs["verify_timeout"], 60.0)
        self.assertTrue(click_with_retry.call_args.kwargs["wait_on_unknown_transition"])
        self.assertFalse(click_with_retry.call_args.kwargs["extend_on_visual_progress"])

    @patch("daily_arena.recognize_return_home_control", return_value=(True, {"found": True}))
    @patch("daily_arena.wait_for_state")
    @patch("daily_arena.click_with_fixed_retry")
    @patch("daily_arena.classify_state", return_value=("arena_lobby", {}))
    @patch("daily_arena.safe_capture_client")
    @patch("daily_arena.find_game_window", return_value=123)
    def test_arena_pool_retries_when_lobby_is_misclassified_by_brightness(
        self,
        _find_window: MagicMock,
        capture_client: MagicMock,
        _classify: MagicMock,
        click_with_retry: MagicMock,
        wait_for_state: MagicMock,
        _recognize_home: MagicMock,
    ) -> None:
        image = Image.new("RGB", (1000, 600))
        capture_client.return_value = image
        click_with_retry.side_effect = [
            (False, "blocking_ad_overlay", image, "brightness fallback"),
            (True, "loading", image, "portal click reached loading"),
        ]
        wait_for_state.return_value = ("arena_battle_prep", image)

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = enter_battle_prep(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "entered arena battle preparation after loading")
        self.assertEqual(click_with_retry.call_count, 2)
        self.assertTrue(
            all(call.kwargs["attempts"] == 1 for call in click_with_retry.call_args_list)
        )

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
        self.assertTrue(
            click_with_fixed_retry.call_args.kwargs["wait_on_unknown_transition"]
        )

    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_closes_leftover_quick_hunt_setup_before_returning_home(
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
            ("quick_hunt_setup", {}),
            ("quick_hunt_map", {}),
            ("real_home", {}),
        ]
        click_with_fixed_retry.side_effect = [
            (True, "quick_hunt_map", image, "closed setup"),
            (True, "real_home", image, "returned home"),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(
            [call.args[2] for call in click_with_fixed_retry.call_args_list],
            ["quick_hunt_cancel", "quick_hunt_back"],
        )

    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_accepts_arena_lobby_after_dismissing_season_reward(
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
            ("reward_overlay", {}),
            ("arena_lobby", {}),
            ("real_home", {}),
        ]

        def click_and_verify(
            _hwnd: int,
            current: Image.Image,
            key: str,
            **kwargs: object,
        ) -> tuple[bool, str, Image.Image, str]:
            next_state = "arena_lobby" if key == "reward_overlay_dismiss" else "real_home"
            verify = kwargs["verify"]
            assert callable(verify)
            accepted = verify(next_state, current)
            return accepted, next_state, current, f"{key} verified={accepted}"

        click_with_fixed_retry.side_effect = click_and_verify

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(
            [call.args[2] for call in click_with_fixed_retry.call_args_list],
            ["reward_overlay_dismiss", "arena_home"],
        )

    @patch("daily_automation.recognize_return_home_control")
    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_prefers_home_control_over_generic_overlay_heuristic(
        self,
        _print: MagicMock,
        _find_game_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        click_with_fixed_retry: MagicMock,
        recognize_return_home_control: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.side_effect = [image, image]
        classify_state.side_effect = [
            (
                "blocking_ad_overlay",
                {"classification_rule": "blocking_overlay_brightness"},
            ),
            ("real_home", {}),
        ]
        recognize_return_home_control.return_value = (
            True,
            {"matches": {"home_control": ["H"]}},
        )
        click_with_fixed_retry.return_value = (True, "real_home", image, "returned home")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(click_with_fixed_retry.call_args.args[2], "plaza_home")
        self.assertNotEqual(click_with_fixed_retry.call_args.args[2], "dismiss_overlay")

    @patch("daily_automation.recognize_return_home_control")
    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_uses_home_control_from_an_unknown_field_scene(
        self,
        _print: MagicMock,
        _find_game_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        click_with_fixed_retry: MagicMock,
        recognize_return_home_control: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.side_effect = [image, image]
        classify_state.side_effect = [("unknown", {}), ("real_home", {})]
        recognize_return_home_control.return_value = (
            True,
            {"matches": {"home_control": ["H"]}},
        )
        click_with_fixed_retry.return_value = (True, "real_home", image, "returned home")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(click_with_fixed_retry.call_args.args[2], "plaza_home")

    @patch("daily_automation.click_with_fixed_retry")
    @patch("daily_automation.classify_state")
    @patch("daily_automation.safe_capture_client")
    @patch("open_game.find_game_window", return_value=123)
    @patch("builtins.print")
    def test_ensure_home_waits_through_restaurant_unknown_transition(
        self,
        _print: MagicMock,
        _find_game_window: MagicMock,
        safe_capture_client: MagicMock,
        classify_state: MagicMock,
        click_with_fixed_retry: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        safe_capture_client.side_effect = [image, image]
        classify_state.side_effect = [("restaurant_home", {}), ("real_home", {})]
        click_with_fixed_retry.return_value = (True, "real_home", image, "returned home")

        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = ensure_home(timeout=5.0, log_root=Path(temporary))

        self.assertTrue(ok)
        self.assertEqual(reason, "returned to real_home")
        self.assertEqual(click_with_fixed_retry.call_args.args[2], "restaurant_home")
        self.assertTrue(
            click_with_fixed_retry.call_args.kwargs["wait_on_unknown_transition"]
        )

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
    def test_enter_game_confirms_leftover_arena_rank_change(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        contexts = iter(
            (
                ("arena_rank_change", {}, "unknown", {}),
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
            patch(
                "daily_automation.click_with_fixed_retry",
                return_value=(True, "arena_lobby", image, "confirmed rank change"),
            ) as click_with_fixed_retry,
        ):
            ok, reason = enter_game_logged(
                timeout=30.0,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "game is ready at state=real_home")
        self.assertEqual(click_with_fixed_retry.call_args.args[2], "arena_rank_confirm")

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
            patch("free_gacha.detect_home_reward_notification", return_value=(True, {})),
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

    def test_season_reward_without_return_hint_remains_a_blocking_overlay(self) -> None:
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
            shared_state, shared_details = classify_state(image)

        self.assertEqual(state, "download_waiting")
        self.assertEqual(details["source"], "ocr")
        self.assertEqual(shared_state, "loading")
        self.assertEqual(
            shared_details["classification_rule"],
            "game_loading_text",
        )

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

    @patch("builtins.print")
    def test_free_gacha_confirmation_waits_through_unknown_transition(
        self,
        _print: MagicMock,
    ) -> None:
        image = Image.new("RGB", (2000, 1000))
        states = iter(("confirm_free_gacha", "gacha_result", "gacha_page"))
        click_results = iter(
            (
                (True, "gacha_animation", image, "confirmed free gacha"),
                (True, "gacha_page", image, "returned from result"),
            )
        )

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("free_gacha.find_game_window", return_value=123),
            patch("free_gacha.safe_capture_client", return_value=image),
            patch(
                "free_gacha.classify_state",
                side_effect=lambda _image: (next(states), {}),
            ),
            patch(
                "free_gacha.click_with_fixed_retry",
                side_effect=lambda *_args, **_kwargs: next(click_results),
            ) as click_with_fixed_retry,
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
        confirm_call = click_with_fixed_retry.call_args_list[0]
        self.assertEqual(confirm_call.args[2], "confirm")
        self.assertTrue(confirm_call.kwargs["wait_on_unknown_transition"])


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
