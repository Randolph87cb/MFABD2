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

import mail_rewards
import task_rewards


def frame(name: str) -> Image.Image:
    image = Image.new("RGB", (1000, 600))
    image.info["name"] = name
    return image


def named(image: Image.Image) -> str:
    return str(image.info.get("name"))


class TaskRewardTests(unittest.TestCase):
    @patch("task_rewards.recognize_text_at")
    def test_daily_title_is_not_accepted_as_weekly(self, recognize: MagicMock) -> None:
        recognize.return_value = (True, {"texts": ["每日任务"]})
        weekly, _details = task_rewards._recognize_weekly_page(frame("daily"))
        self.assertFalse(weekly)

    def test_weekly_badge_region_covers_current_client_position(self) -> None:
        image = Image.new("RGB", (1000, 600))
        draw = ImageDraw.Draw(image)
        center_x, center_y = 223, 116
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

        found, _details = task_rewards.detect_red_exclamation_badge(
            image,
            task_rewards.WEEKLY_BADGE_REGION,
        )

        self.assertTrue(found)

        daily_found, _details = task_rewards.detect_red_exclamation_badge(
            image,
            task_rewards.DAILY_BADGE_REGION,
        )
        self.assertFalse(daily_found)

    @patch("task_rewards.click_ratio_logged")
    @patch("task_rewards.detect_home_reward_notification", return_value=(False, {}))
    @patch("task_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("task_rewards.safe_capture_client", return_value=frame("home"))
    @patch("task_rewards.find_game_window", return_value=123)
    def test_no_home_badge_is_successful_skip(
        self,
        _window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        _badge: MagicMock,
        click: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = task_rewards.run_task_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )
        self.assertTrue(ok)
        self.assertTrue(reason.startswith("skipped:"))
        click.assert_not_called()

    @patch("task_rewards.click_ratio_logged")
    @patch(
        "task_rewards.detect_red_exclamation_badge",
        return_value=(True, {"center": (0.223, 0.193)}),
    )
    @patch("task_rewards.detect_home_reward_notification", return_value=(True, {}))
    @patch("task_rewards.return_to_home", return_value=(True, "returned home"))
    @patch("task_rewards.recognize_reward_overlay_labels")
    @patch("task_rewards._recognize_claim_all")
    @patch("task_rewards._recognize_weekly_page")
    @patch("task_rewards._recognize_daily_page")
    @patch("task_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("task_rewards.safe_capture_client")
    @patch("task_rewards.find_game_window", return_value=123)
    def test_daily_weekly_settlement_and_return_are_processed(
        self,
        _window: MagicMock,
        capture: MagicMock,
        _home: MagicMock,
        daily_page: MagicMock,
        weekly_page: MagicMock,
        claim: MagicMock,
        overlay: MagicMock,
        return_home: MagicMock,
        _home_badge: MagicMock,
        _weekly_badge: MagicMock,
        click: MagicMock,
    ) -> None:
        images = [
            frame("home"),
            frame("daily"),
            frame("daily-overlay"),
            frame("daily-after"),
            frame("daily-after"),
            frame("weekly"),
            frame("weekly-after"),
            frame("weekly-after"),
        ]
        capture.side_effect = images
        daily_page.side_effect = lambda image: (named(image) in {"daily", "daily-after"}, {})
        weekly_page.side_effect = lambda image: (named(image) in {"weekly", "weekly-after"}, {})
        claim.side_effect = lambda image: (named(image) in {"daily", "weekly"}, {})
        overlay.side_effect = lambda image: ("overlay" in named(image), {})

        with tempfile.TemporaryDirectory() as temporary, patch(
            "task_rewards.CLICK_SETTLE_SECONDS", 0
        ):
            ok, reason = task_rewards.run_task_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok, reason)
        self.assertTrue(reason.startswith("completed:"))
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertEqual(
            keys,
            [
                "home_tasks",
                "task_daily_claim_all",
                "task_reward_overlay_dismiss",
                "weekly_task_tab",
                "task_weekly_claim_all",
            ],
        )
        return_home.assert_called_once()

    @patch("task_rewards.click_ratio_logged")
    @patch("task_rewards.detect_red_exclamation_badge", return_value=(False, {}))
    @patch("task_rewards.detect_home_reward_notification", return_value=(True, {}))
    @patch("task_rewards.return_to_home", return_value=(True, "returned home"))
    @patch("task_rewards._recognize_claim_all", return_value=(False, {}))
    @patch("task_rewards._recognize_daily_page", return_value=(True, {}))
    @patch("task_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("task_rewards.safe_capture_client", side_effect=[frame("home"), frame("daily")])
    @patch("task_rewards.find_game_window", return_value=123)
    def test_empty_daily_page_returns_without_claiming(
        self,
        _window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        _page: MagicMock,
        _claim: MagicMock,
        return_home: MagicMock,
        _home_badge: MagicMock,
        _weekly_badge: MagicMock,
        click: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "task_rewards.CLICK_SETTLE_SECONDS", 0
        ):
            ok, reason = task_rewards.run_task_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )
        self.assertTrue(ok, reason)
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertEqual(keys, ["home_tasks"])
        return_home.assert_called_once()

    @patch("task_rewards.time.sleep")
    @patch("task_rewards.recognize_reward_overlay_labels")
    @patch("task_rewards._recognize_claim_all", return_value=(True, {}))
    @patch("task_rewards._recognize_daily_page", return_value=(True, {}))
    @patch("task_rewards.safe_capture_client")
    def test_claim_wait_does_not_accept_same_page_before_late_overlay(
        self,
        capture: MagicMock,
        _page: MagicMock,
        _claim: MagicMock,
        overlay: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        capture.side_effect = [frame("same-page"), frame("late-overlay")]
        overlay.side_effect = [(False, {}), (True, {})]
        with tempfile.TemporaryDirectory() as temporary, patch(
            "task_rewards.time.monotonic", side_effect=[0.0, 1.0]
        ):
            logger = task_rewards.RunLogger(Path(temporary))
            ok, _image, result = task_rewards._wait_after_click(
                123,
                logger=logger,
                label="claim",
                recognize_page=task_rewards._recognize_daily_page,
                wait_for_claim=True,
                timeout=5,
            )
        self.assertTrue(ok)
        self.assertEqual(result, "reward_overlay")
        self.assertEqual(capture.call_count, 2)

    @patch("task_rewards.time.sleep")
    @patch("task_rewards.recognize_reward_overlay_labels", return_value=(False, {}))
    @patch("task_rewards._recognize_claim_all", return_value=(True, {}))
    @patch("task_rewards._recognize_daily_page", return_value=(True, {}))
    @patch("task_rewards.safe_capture_client", return_value=frame("same-page"))
    def test_unchanged_task_claim_fails_at_step_timeout(
        self,
        capture: MagicMock,
        _page: MagicMock,
        _claim: MagicMock,
        _overlay: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "task_rewards.time.monotonic", side_effect=[0.0, 5.0]
        ):
            logger = task_rewards.RunLogger(Path(temporary))
            ok, _image, result = task_rewards._wait_after_click(
                123,
                logger=logger,
                label="claim",
                recognize_page=task_rewards._recognize_daily_page,
                wait_for_claim=True,
                timeout=5,
            )
        self.assertFalse(ok)
        self.assertIn("领取文字仍存在", result)
        self.assertIn("点击未生效", result)
        capture.assert_called_once()

    @patch("task_rewards.click_ratio_logged")
    @patch("task_rewards._claim_page_once")
    @patch("task_rewards._recognize_daily_page", return_value=(True, {}))
    @patch("task_rewards.detect_red_exclamation_badge", return_value=(True, {}))
    @patch("task_rewards.detect_home_reward_notification", return_value=(True, {}))
    @patch("task_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("task_rewards.safe_capture_client", side_effect=[frame("home"), frame("daily")])
    @patch("task_rewards.find_game_window", return_value=123)
    def test_task_flow_persists_unchanged_claim_failure(
        self,
        _window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        _badge: MagicMock,
        _tab_badge: MagicMock,
        _page: MagicMock,
        claim: MagicMock,
        _click: MagicMock,
    ) -> None:
        reason = "每日任务：领取文字仍存在，点击未生效"
        claim.return_value = (False, frame("daily"), reason)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ok, actual_reason = task_rewards.run_task_rewards(dry_run=False, log_root=root)
            failure = (root / "failure.txt").read_text(encoding="utf-8")
        self.assertFalse(ok)
        self.assertEqual(actual_reason, reason)
        self.assertIn("点击未生效", failure)

    @patch("task_rewards.click_ratio_logged")
    @patch("task_rewards._wait_after_click")
    @patch("task_rewards.recognize_reward_overlay_labels", return_value=(True, {}))
    def test_reward_overlay_loop_has_a_hard_limit(
        self,
        _overlay: MagicMock,
        wait_after: MagicMock,
        click: MagicMock,
    ) -> None:
        image = frame("overlay")
        wait_after.return_value = (True, image, "reward_overlay")
        with tempfile.TemporaryDirectory() as temporary, patch(
            "task_rewards.MAX_REWARD_OVERLAYS", 2
        ):
            logger = task_rewards.RunLogger(Path(temporary), annotate_clicks=True)
            ok, _image, reason = task_rewards._dismiss_reward_overlays(
                123,
                image,
                logger=logger,
                recognize_page=task_rewards._recognize_daily_page,
            )
        self.assertFalse(ok)
        self.assertIn("limit reached", reason)
        self.assertEqual(click.call_count, 2)

    @patch("task_rewards._wait_after_click")
    @patch("task_rewards.click_ratio_logged")
    @patch("task_rewards.recognize_reward_overlay_labels")
    def test_task_reward_overlay_retries_one_dropped_click(
        self,
        recognize: MagicMock,
        click: MagicMock,
        wait_after: MagicMock,
    ) -> None:
        overlay = frame("overlay")
        page = frame("page")
        recognize.side_effect = [(True, {}), (True, {}), (False, {})]
        wait_after.side_effect = [
            (False, overlay, "still overlay"),
            (True, page, "page"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            ok, _image, reason = task_rewards._dismiss_reward_overlays(
                123,
                overlay,
                logger=task_rewards.RunLogger(Path(temporary)),
                recognize_page=task_rewards._recognize_daily_page,
            )
        self.assertTrue(ok, reason)
        self.assertEqual(click.call_count, 2)

    @patch("task_rewards._dismiss_reward_overlays")
    @patch("task_rewards._wait_after_click")
    @patch("task_rewards.click_ratio_logged")
    @patch("task_rewards._recognize_claim_all", return_value=(True, {}))
    def test_task_claim_retries_one_dropped_click_while_claim_page_is_unchanged(
        self,
        recognize_claim: MagicMock,
        click: MagicMock,
        wait_after: MagicMock,
        dismiss: MagicMock,
    ) -> None:
        unchanged = frame("unchanged-claim-page")
        overlay = frame("reward-overlay")
        wait_after.side_effect = [
            (False, unchanged, "click had no effect"),
            (True, overlay, "reward_overlay"),
        ]
        dismiss.return_value = (True, frame("page"), "dismissed reward overlay")
        recognize_page = MagicMock(return_value=(True, {}))

        with tempfile.TemporaryDirectory() as temporary:
            ok, _image, reason = task_rewards._claim_page_once(
                123,
                unchanged,
                logger=task_rewards.RunLogger(Path(temporary)),
                page_name="daily",
                recognize_page=recognize_page,
            )

        self.assertTrue(ok, reason)
        self.assertEqual(click.call_count, 2)
        self.assertEqual(wait_after.call_count, 2)
        self.assertGreaterEqual(recognize_claim.call_count, 2)

    @patch("task_rewards.safe_capture_client", return_value=frame("unknown"))
    def test_page_wait_has_per_step_timeout(self, _capture: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            logger = task_rewards.RunLogger(Path(temporary))
            ok, _image, reason = task_rewards._wait_for_page(
                123,
                logger=logger,
                label="daily",
                recognize_page=lambda _image: (False, {}),
                timeout=0,
            )
        self.assertFalse(ok)
        self.assertIn("within 0 seconds", reason)

    @patch("task_rewards.time.sleep")
    @patch("task_rewards.safe_capture_client")
    def test_default_page_wait_retries_after_slow_first_ocr(
        self,
        capture: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        capture.side_effect = [frame("transition"), frame("daily")]
        recognize = MagicMock(side_effect=[(False, {}), (True, {})])
        with tempfile.TemporaryDirectory() as temporary, patch(
            "task_rewards.time.monotonic", side_effect=[0.0, 13.0]
        ):
            logger = task_rewards.RunLogger(Path(temporary))
            ok, image, reason = task_rewards._wait_for_page(
                123,
                logger=logger,
                label="daily",
                recognize_page=recognize,
            )
        self.assertTrue(ok, reason)
        self.assertEqual(named(image), "daily")
        self.assertEqual(capture.call_count, 2)


class MailRewardTests(unittest.TestCase):
    @patch("mail_rewards.recognize_home_labels", return_value=(True, {"available": True}))
    @patch("mail_rewards._wait_for_mail_page")
    @patch("mail_rewards.click_ratio_logged")
    def test_mail_entry_retries_once_when_first_click_is_dropped(
        self,
        click: MagicMock,
        wait: MagicMock,
        _home: MagicMock,
    ) -> None:
        home = frame("home")
        mail = frame("mail")
        wait.side_effect = [
            (False, home, "mail page not found"),
            (True, mail, "mail page confirmed"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            ok, image, reason = mail_rewards._open_mail_page(
                123,
                home,
                logger=mail_rewards.RunLogger(Path(temporary)),
                dry_run=False,
            )

        self.assertTrue(ok, reason)
        self.assertIs(image, mail)
        self.assertEqual(click.call_count, 2)
        self.assertEqual(wait.call_count, 2)

    @patch("mail_rewards._wait_after_click")
    @patch("mail_rewards.click_ratio_logged")
    @patch("mail_rewards.recognize_reward_overlay_labels")
    def test_mail_reward_overlay_retries_one_dropped_click(
        self,
        recognize: MagicMock,
        click: MagicMock,
        wait_after: MagicMock,
    ) -> None:
        overlay = frame("overlay")
        page = frame("page")
        recognize.side_effect = [(True, {}), (True, {}), (False, {})]
        wait_after.side_effect = [
            (False, overlay, "still overlay"),
            (True, page, "page"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            ok, _image, reason = mail_rewards._dismiss_reward_overlays(
                123,
                overlay,
                logger=mail_rewards.RunLogger(Path(temporary)),
            )
        self.assertTrue(ok, reason)
        self.assertEqual(click.call_count, 2)

    @patch("mail_rewards.time.sleep")
    @patch("mail_rewards._recognize_mail_page")
    @patch("mail_rewards.safe_capture_client")
    def test_default_page_wait_retries_after_slow_first_ocr(
        self,
        capture: MagicMock,
        recognize: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        capture.side_effect = [frame("transition"), frame("mail")]
        recognize.side_effect = [(False, {}), (True, {})]
        with tempfile.TemporaryDirectory() as temporary, patch(
            "mail_rewards.time.monotonic", side_effect=[0.0, 13.0]
        ):
            logger = mail_rewards.RunLogger(Path(temporary))
            ok, image, reason = mail_rewards._wait_for_mail_page(
                123,
                logger=logger,
                label="mail",
            )
        self.assertTrue(ok, reason)
        self.assertEqual(named(image), "mail")
        self.assertEqual(capture.call_count, 2)

    @patch("mail_rewards.click_ratio_logged")
    @patch("mail_rewards.detect_home_reward_notification", return_value=(False, {}))
    @patch("mail_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("mail_rewards.safe_capture_client", return_value=frame("home"))
    @patch("mail_rewards.find_game_window", return_value=123)
    def test_no_mail_badge_is_successful_skip(
        self,
        _window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        _badge: MagicMock,
        click: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = mail_rewards.run_mail_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )
        self.assertTrue(ok)
        self.assertTrue(reason.startswith("skipped:"))
        click.assert_not_called()

    @patch("mail_rewards.click_ratio_logged")
    @patch("mail_rewards.detect_red_exclamation_badge")
    @patch("mail_rewards.detect_home_reward_notification", return_value=(True, {}))
    @patch("mail_rewards.return_to_home", return_value=(True, "returned home"))
    @patch("mail_rewards.recognize_reward_overlay_labels")
    @patch("mail_rewards._recognize_claim_all")
    @patch("mail_rewards._recognize_mail_page")
    @patch("mail_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("mail_rewards.safe_capture_client")
    @patch("mail_rewards.find_game_window", return_value=123)
    def test_general_and_product_mail_with_consecutive_settlements(
        self,
        _window: MagicMock,
        capture: MagicMock,
        _home: MagicMock,
        mail_page: MagicMock,
        claim: MagicMock,
        overlay: MagicMock,
        return_home: MagicMock,
        _home_badge: MagicMock,
        product_badge: MagicMock,
        click: MagicMock,
    ) -> None:
        images = [
            frame("home"),
            frame("general"),
            frame("overlay-1"),
            frame("overlay-2"),
            frame("general-after"),
            frame("product"),
            frame("product-after"),
            frame("product-after"),
        ]
        capture.side_effect = images
        mail_page.side_effect = lambda image: (
            named(image) in {"general", "general-after", "product", "product-after"},
            {},
        )
        claim.side_effect = lambda image: (named(image) in {"general", "product"}, {})
        overlay.side_effect = lambda image: (named(image).startswith("overlay"), {})
        product_badge.side_effect = [
            (True, {"stage": "general"}),
            (True, {"stage": "product-before"}),
            (False, {"stage": "product-after"}),
        ]

        with tempfile.TemporaryDirectory() as temporary, patch(
            "mail_rewards.CLICK_SETTLE_SECONDS", 0
        ):
            ok, reason = mail_rewards.run_mail_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok, reason)
        self.assertTrue(reason.startswith("completed:"))
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertEqual(
            keys,
            [
                "home_mail",
                "mail_general_claim_all",
                "mail_reward_overlay_dismiss",
                "mail_reward_overlay_dismiss",
                "product_mail_tab",
                "mail_product_claim_all",
            ],
        )
        return_home.assert_called_once()

    @patch("mail_rewards.click_ratio_logged")
    @patch("mail_rewards.detect_red_exclamation_badge", return_value=(False, {}))
    @patch("mail_rewards.detect_home_reward_notification", return_value=(True, {}))
    @patch("mail_rewards.return_to_home", return_value=(True, "returned home"))
    @patch("mail_rewards._recognize_claim_all", return_value=(False, {}))
    @patch("mail_rewards._recognize_mail_page", return_value=(True, {}))
    @patch("mail_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("mail_rewards.safe_capture_client", side_effect=[frame("home"), frame("mail")])
    @patch("mail_rewards.find_game_window", return_value=123)
    def test_empty_mail_page_returns_without_claiming(
        self,
        _window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        _page: MagicMock,
        _claim: MagicMock,
        return_home: MagicMock,
        _home_badge: MagicMock,
        _product_badge: MagicMock,
        click: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "mail_rewards.CLICK_SETTLE_SECONDS", 0
        ):
            ok, reason = mail_rewards.run_mail_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )
        self.assertTrue(ok, reason)
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertEqual(keys, ["home_mail"])
        return_home.assert_called_once()

    @patch("mail_rewards.time.sleep")
    @patch("mail_rewards.recognize_reward_overlay_labels", return_value=(False, {}))
    @patch("mail_rewards._recognize_claim_all")
    @patch("mail_rewards._recognize_mail_page", return_value=(True, {}))
    @patch("mail_rewards.safe_capture_client")
    def test_mail_claim_waits_past_same_page_until_button_disappears(
        self,
        capture: MagicMock,
        _page: MagicMock,
        claim: MagicMock,
        _overlay: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        capture.side_effect = [frame("same-page"), frame("claim-gone"), frame("claim-gone")]
        claim.side_effect = [(True, {}), (False, {}), (False, {})]
        with tempfile.TemporaryDirectory() as temporary, patch(
            "mail_rewards.time.monotonic", side_effect=[0.0, 1.0, 2.0]
        ):
            logger = mail_rewards.RunLogger(Path(temporary))
            ok, _image, result = mail_rewards._wait_after_click(
                123,
                logger=logger,
                label="mail-claim",
                wait_for_claim=True,
                timeout=5,
            )
        self.assertTrue(ok)
        self.assertEqual(result, "claim_button_gone_stable")
        self.assertEqual(capture.call_count, 3)

    @patch("mail_rewards.time.sleep")
    @patch("mail_rewards.recognize_reward_overlay_labels", return_value=(False, {}))
    @patch("mail_rewards._recognize_claim_all", return_value=(True, {}))
    @patch("mail_rewards._recognize_mail_page", return_value=(True, {}))
    @patch("mail_rewards.safe_capture_client", return_value=frame("same-page"))
    def test_unchanged_mail_claim_fails_at_step_timeout(
        self,
        capture: MagicMock,
        _page: MagicMock,
        _claim: MagicMock,
        _overlay: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "mail_rewards.time.monotonic", side_effect=[0.0, 5.0]
        ):
            logger = mail_rewards.RunLogger(Path(temporary))
            ok, _image, reason = mail_rewards._wait_after_click(
                123,
                logger=logger,
                label="mail-claim",
                wait_for_claim=True,
                timeout=5,
            )
        self.assertFalse(ok)
        self.assertIn("领取文字仍存在", reason)
        self.assertIn("点击未生效", reason)
        capture.assert_called_once()

    @patch("mail_rewards.click_ratio_logged")
    @patch("mail_rewards._claim_mail_once")
    @patch("mail_rewards._recognize_mail_page", return_value=(True, {}))
    @patch("mail_rewards.detect_red_exclamation_badge", return_value=(True, {}))
    @patch("mail_rewards.detect_home_reward_notification", return_value=(True, {}))
    @patch("mail_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("mail_rewards.safe_capture_client", side_effect=[frame("home"), frame("mail")])
    @patch("mail_rewards.find_game_window", return_value=123)
    def test_mail_flow_persists_unchanged_claim_failure(
        self,
        _window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        _badge: MagicMock,
        _tab_badge: MagicMock,
        _page: MagicMock,
        claim: MagicMock,
        _click: MagicMock,
    ) -> None:
        reason = "一般邮件：领取文字仍存在，点击未生效"
        claim.return_value = (False, frame("mail"), reason)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ok, actual_reason = mail_rewards.run_mail_rewards(dry_run=False, log_root=root)
            failure = (root / "failure.txt").read_text(encoding="utf-8")
        self.assertFalse(ok)
        self.assertEqual(actual_reason, reason)
        self.assertIn("点击未生效", failure)

    @patch("mail_rewards.time.sleep")
    @patch("mail_rewards.detect_red_exclamation_badge")
    @patch("mail_rewards._recognize_mail_page", return_value=(True, {}))
    @patch("mail_rewards.safe_capture_client")
    def test_product_tab_waits_until_notification_clears(
        self,
        capture: MagicMock,
        _page: MagicMock,
        badge: MagicMock,
        sleep: MagicMock,
    ) -> None:
        capture.side_effect = [frame("same-mail-page"), frame("product-mail")]
        badge.side_effect = [(True, {}), (False, {})]
        with tempfile.TemporaryDirectory() as temporary, patch(
            "mail_rewards.time.monotonic", side_effect=[0.0, 1.0]
        ):
            logger = mail_rewards.RunLogger(Path(temporary))
            ok, _image, reason = mail_rewards._wait_for_product_tab(
                123,
                logger=logger,
                timeout=5,
            )
            events = (Path(temporary) / "events.jsonl").read_text(encoding="utf-8")
        self.assertTrue(ok, reason)
        self.assertEqual(capture.call_count, 2)
        sleep.assert_called()
        self.assertIn('"before_red": true', events)
        self.assertIn('"after_red": false', events)

    @patch("mail_rewards.click_ratio_logged")
    @patch("mail_rewards.detect_home_reward_notification", return_value=(True, {}))
    @patch("mail_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("mail_rewards.safe_capture_client", return_value=frame("home"))
    @patch("mail_rewards.find_game_window", return_value=123)
    def test_dry_run_stops_after_safe_entry_plan(
        self,
        _window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        _badge: MagicMock,
        click: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = mail_rewards.run_mail_rewards(
                dry_run=True,
                log_root=Path(temporary),
            )
        self.assertTrue(ok)
        self.assertTrue(reason.startswith("completed:"))
        click.assert_called_once()
        self.assertTrue(click.call_args.kwargs["dry_run"])


if __name__ == "__main__":
    unittest.main()
