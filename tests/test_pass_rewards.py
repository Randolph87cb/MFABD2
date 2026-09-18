from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import pass_rewards
from free_gacha import RunLogger


def _image(value: int = 0) -> Image.Image:
    return Image.new("RGB", (1000, 600), (value, value, value))


def _badge(y: float = 0.3) -> dict[str, object]:
    return {"center": (0.21, y), "red_pixels": 30}


def _recognition(found: bool) -> tuple[bool, dict[str, object]]:
    return found, {"available": True}


class PassRewardEntryTests(unittest.TestCase):
    @patch("pass_rewards.click_ratio_logged")
    @patch("pass_rewards.detect_home_reward_notification", return_value=(False, {}))
    @patch("pass_rewards.recognize_home_labels", return_value=(True, {}))
    @patch("pass_rewards.safe_capture_client", return_value=_image())
    @patch("pass_rewards.find_game_window", return_value=123)
    def test_no_home_red_badge_is_skipped(
        self,
        _find_window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        _notification: MagicMock,
        click: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = pass_rewards.run_pass_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertTrue(reason.startswith("skipped:"))
        click.assert_not_called()

    @patch("pass_rewards.detect_home_reward_notification")
    @patch("pass_rewards.recognize_home_labels", return_value=(False, {}))
    @patch("pass_rewards.safe_capture_client", return_value=_image())
    @patch("pass_rewards.find_game_window", return_value=123)
    def test_home_text_is_required_before_checking_the_badge(
        self,
        _find_window: MagicMock,
        _capture: MagicMock,
        _home: MagicMock,
        notification: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ok, reason = pass_rewards.run_pass_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )

        self.assertFalse(ok)
        self.assertIn("home OCR", reason)
        notification.assert_not_called()

    @patch("pass_rewards.recognize_text_at", return_value=(True, {}))
    def test_pass_page_confirmation_uses_only_fixed_title_ocr(
        self,
        recognize_text_at: MagicMock,
    ) -> None:
        found, _details = pass_rewards._confirm_pass_reward_page(_image())

        self.assertTrue(found)
        recognize_text_at.assert_called_once_with(
            unittest.mock.ANY,
            pass_rewards.PASS_TITLE_REGION,
            ("通行证",),
        )

    def test_upstream_coordinates_use_1280_by_720(self) -> None:
        self.assertEqual(
            pass_rewards.PASS_TITLE_REGION,
            (179 / 1280, 6 / 720, 148 / 1280, 65 / 720),
        )
        self.assertEqual(
            pass_rewards.PASS_CLAIM_ALL_REGION,
            (757 / 1280, 514 / 720, 361 / 1280, 66 / 720),
        )
        self.assertEqual(pass_rewards.PASS_SWIPE_BEGIN, (216 / 1280, 518 / 720))
        self.assertEqual(pass_rewards.PASS_SWIPE_END, (214 / 1280, 190 / 720))
        self.assertEqual(pass_rewards.HOME_PASS_POINT, (0.873, 0.255))
        self.assertEqual(pass_rewards.PASS_CARD_SELECT_X, 224 / 1280)
        self.assertEqual(pass_rewards.PASS_TASK_LIST_POINT, (0.830, 0.629))
        self.assertEqual(pass_rewards.PASS_CLAIM_ALL_POINT, (0.744, 0.709))

    def test_red_badge_only_supplies_card_row_for_click_point(self) -> None:
        self.assertEqual(
            pass_rewards._pass_card_click_point({"center": (0.266, 0.463)}),
            (pass_rewards.PASS_CARD_SELECT_X, 0.463),
        )

    def test_select_action_uses_fixed_card_x_and_passes_identity_to_confirmation(self) -> None:
        badge = _badge(0.463)
        click = MagicMock()
        wait = MagicMock(return_value=(True, _image(), {"state": "task_page"}))
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch("pass_rewards.find_game_window", return_value=123))
            stack.enter_context(patch("pass_rewards.safe_capture_client", return_value=_image()))
            stack.enter_context(
                patch("pass_rewards._confirm_pass_reward_page", return_value=_recognition(True))
            )
            stack.enter_context(patch("pass_rewards._find_pass_badges", return_value=[badge]))
            stack.enter_context(
                patch(
                    "pass_rewards._read_pass_card_identity",
                    return_value=(["Moonrise"], {"available": True}),
                )
            )
            stack.enter_context(patch("pass_rewards._click_logged", click))
            stack.enter_context(patch("pass_rewards._wait_for_pass_selection", wait))

            ok, _reason = pass_rewards.select_pass_with_notification(
                log_root=Path(temporary),
            )

        self.assertTrue(ok)
        self.assertEqual(click.call_args.args[2], (pass_rewards.PASS_CARD_SELECT_X, 0.463))
        self.assertEqual(wait.call_args.kwargs["expected_identity"], ["Moonrise"])

    @patch("pass_rewards._confirm_claim_all", return_value=_recognition(True))
    @patch("pass_rewards._confirm_selected_pass_panel", return_value=_recognition(True))
    def test_claim_text_does_not_misclassify_overview_as_task_page(
        self,
        _panel: MagicMock,
        _claim: MagicMock,
    ) -> None:
        found, details = pass_rewards._confirm_pass_task_page(_image())

        self.assertFalse(found)
        self.assertTrue(details["overview_text_found"])

    @patch("pass_rewards._confirm_claim_all", return_value=_recognition(True))
    @patch("pass_rewards._confirm_selected_pass_panel", return_value=_recognition(False))
    def test_task_page_is_confirmed_by_fixed_text_without_brightness_ratio(
        self,
        _panel: MagicMock,
        _claim: MagicMock,
    ) -> None:
        found, details = pass_rewards._confirm_pass_task_page(_image())

        self.assertTrue(found)
        self.assertTrue(details["claim_text_found"])

    @patch("pass_rewards._confirm_pass_reward_page", return_value=_recognition(True))
    @patch("pass_rewards._confirm_selected_pass_panel", return_value=_recognition(True))
    @patch("pass_rewards._read_texts_at", return_value=(["SPECIAL"], {"available": True}))
    def test_mismatched_detail_identity_does_not_confirm_card_selection(
        self,
        _detail: MagicMock,
        _panel: MagicMock,
        _page: MagicMock,
    ) -> None:
        badge = _badge()

        def evaluate(_hwnd: int, *, recognize: object, **_kwargs: object):
            found, details = recognize(_image())  # type: ignore[operator]
            return found, _image(), details

        with patch("pass_rewards._capture_until", side_effect=evaluate):
            found, _after, _details = pass_rewards._wait_for_pass_selection(
                123,
                badge,
                expected_identity=["UNTAMED赛季通行证"],
                logger=MagicMock(),
                step=1,
            )

        self.assertFalse(found)

    @patch("pass_rewards._confirm_pass_reward_page", return_value=_recognition(True))
    @patch("pass_rewards._confirm_selected_pass_panel", return_value=_recognition(False))
    @patch("pass_rewards._confirm_pass_task_page", return_value=_recognition(True))
    @patch("pass_rewards._read_texts_at", return_value=(["SPECIAL"], {"available": True}))
    def test_selected_card_can_be_confirmed_while_already_on_task_page(
        self,
        _detail: MagicMock,
        _task_page: MagicMock,
        _panel: MagicMock,
        _page: MagicMock,
    ) -> None:
        def evaluate(_hwnd: int, *, recognize: object, **_kwargs: object):
            found, details = recognize(_image())  # type: ignore[operator]
            return found, _image(), details

        with patch("pass_rewards._capture_until", side_effect=evaluate):
            found, _after, details = pass_rewards._wait_for_pass_selection(
                123,
                _badge(),
                expected_identity=["特别赛季通行证", "SPECIAL"],
                logger=MagicMock(),
                step=1,
            )

        self.assertTrue(found)
        self.assertEqual(details["state"], "task_page")

    def test_card_and_detail_identity_tolerate_small_ocr_error(self) -> None:
        self.assertTrue(
            pass_rewards._pass_identity_matches(["UNTAMED赛季通行证"], ["UNTAMEO"])
        )
        self.assertFalse(
            pass_rewards._pass_identity_matches(
                ["启示之梦达丽安角色通行证"],
                ["UNTAMEO"],
            )
        )


class PassRewardFlowTests(unittest.TestCase):
    def _run(
        self,
        *,
        captures: list[Image.Image],
        badges: list[list[dict[str, object]]],
        selected: bool = True,
        selected_state: str = "overview",
        claim: bool = True,
        task_page: bool | None = None,
        overlays: list[bool] | None = None,
    ) -> tuple[bool, str, MagicMock, MagicMock, MagicMock, MagicMock]:
        click = MagicMock()
        swipe = MagicMock()
        dismiss = MagicMock(return_value=(True, _image(), "dismissed 1 reward overlays"))
        home = MagicMock(return_value=(True, "returned home after 1 back clicks"))
        overlay_values = iter(overlays or [])

        def overlay(_candidate: Image.Image) -> tuple[bool, dict[str, object]]:
            return _recognition(next(overlay_values, False))

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch("pass_rewards.find_game_window", return_value=123))
            stack.enter_context(patch("pass_rewards.safe_capture_client", side_effect=captures))
            stack.enter_context(
                patch("pass_rewards.detect_home_reward_notification", return_value=(True, {}))
            )
            stack.enter_context(patch("pass_rewards.recognize_home_labels", return_value=(True, {})))
            stack.enter_context(
                patch("pass_rewards._confirm_pass_reward_page", return_value=_recognition(True))
            )
            stack.enter_context(
                patch(
                    "pass_rewards._wait_for_pass_selection",
                    return_value=(
                        selected,
                        _image(),
                        {"selected": selected, "state": selected_state},
                    ),
                )
            )
            stack.enter_context(
                patch("pass_rewards._confirm_claim_all", return_value=_recognition(claim))
            )
            stack.enter_context(
                patch(
                    "pass_rewards._confirm_pass_task_page",
                    return_value=_recognition(claim if task_page is None else task_page),
                )
            )
            stack.enter_context(patch("pass_rewards._is_reward_overlay", side_effect=overlay))
            stack.enter_context(patch("pass_rewards._has_pass_item_popup_close", return_value=False))
            badge_values = iter(badges)
            stack.enter_context(
                patch(
                    "pass_rewards._find_pass_badges",
                    side_effect=lambda _image: next(badge_values, []),
                )
            )
            stack.enter_context(
                patch(
                    "pass_rewards._read_pass_card_identity",
                    return_value=(["UNTAMED赛季通行证"], {"available": True}),
                )
            )
            stack.enter_context(patch("pass_rewards._click_logged", click))
            stack.enter_context(patch("pass_rewards.swipe_ratio_logged", swipe))
            stack.enter_context(patch("pass_rewards.dismiss_reward_overlays", dismiss))
            stack.enter_context(patch("pass_rewards.return_to_home", home))
            stack.enter_context(patch("pass_rewards.time.sleep"))
            ok, reason = pass_rewards.run_pass_rewards(
                dry_run=False,
                log_root=Path(temporary),
            )
        return ok, reason, click, swipe, dismiss, home

    def test_one_marked_card_is_claimed_and_settlement_closed(self) -> None:
        ok, reason, click, _swipe, dismiss, home = self._run(
            captures=[_image()] * 20,
            badges=[[_badge()], [], []],
            overlays=[True, True, True],
        )

        self.assertTrue(ok)
        self.assertIn("claimed=1", reason)
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertEqual(
            keys,
            [
                "home_pass",
                "pass_list_notification",
                "pass_task_list",
                "pass_claim_all",
            ],
        )
        dismiss.assert_called_once()
        home.assert_called_once()
        selection_click = next(
            entry for entry in click.call_args_list if entry.kwargs["key"] == "pass_list_notification"
        )
        self.assertEqual(
            selection_click.args[2],
            (pass_rewards.PASS_CARD_SELECT_X, _badge()["center"][1]),
        )

    def test_already_open_task_page_skips_task_tab_click(self) -> None:
        ok, reason, click, _swipe, dismiss, home = self._run(
            captures=[_image()] * 20,
            badges=[[_badge()], [], []],
            selected_state="task_page",
            overlays=[True, True, True],
        )

        self.assertTrue(ok)
        self.assertIn("claimed=1", reason)
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertNotIn("pass_task_list", keys)
        self.assertIn("pass_claim_all", keys)
        dismiss.assert_called_once()
        home.assert_called_once()

    def test_multiple_marked_cards_are_processed(self) -> None:
        first = _badge(0.30)
        second = _badge(0.45)
        ok, reason, click, _swipe, dismiss, home = self._run(
            captures=[_image()] * 24,
            badges=[[first], [first, second], [], []],
            overlays=[True, True, True, True, True, True],
        )

        self.assertTrue(ok)
        self.assertIn("claimed=2", reason)
        claim_clicks = [entry for entry in click.call_args_list if entry.kwargs["key"] == "pass_claim_all"]
        self.assertEqual(len(claim_clicks), 2)
        self.assertEqual(dismiss.call_count, 2)
        home.assert_called_once()

    def test_swipes_up_to_find_a_later_marked_card(self) -> None:
        ok, reason, _click, swipe, _dismiss, _home = self._run(
            captures=[_image()] * 20,
            badges=[[], [], [_badge()], [], []],
            overlays=[True, True, True],
        )

        self.assertTrue(ok)
        self.assertIn("claimed=1", reason)
        self.assertEqual(swipe.call_count, 4)

    def test_unconfirmed_card_selection_is_a_step_failure(self) -> None:
        stale = _badge()
        ok, reason, click, swipe, _dismiss, _home = self._run(
            captures=[_image()] * 6,
            badges=[
                [stale], [stale], [stale], [stale], [stale],
                [stale], [stale], [stale], [stale], [stale],
            ],
            selected=False,
        )

        self.assertFalse(ok)
        self.assertIn("未切换到对应通行证", reason)
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertEqual(keys.count("pass_list_notification"), 1)
        self.assertEqual(swipe.call_count, 0)

    def test_task_page_click_without_claim_text_is_a_step_failure(self) -> None:
        stale = _badge()
        ok, reason, click, _swipe, _dismiss, _home = self._run(
            captures=[_image()] * 7,
            badges=[[stale], [], []],
            claim=False,
        )

        self.assertFalse(ok)
        self.assertIn("页面未切换", reason)
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertIn("pass_task_list", keys)
        self.assertNotIn("pass_claim_all", keys)

    def test_unchanged_overview_after_task_click_is_an_explicit_failure(self) -> None:
        ok, reason, click, _swipe, _dismiss, _home = self._run(
            captures=[_image()] * 7,
            badges=[[_badge()]],
            claim=True,
            task_page=False,
        )

        self.assertFalse(ok)
        self.assertIn("页面未切换", reason)
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertIn("pass_task_list", keys)
        self.assertNotIn("pass_claim_all", keys)

    @patch("pass_rewards._capture_until")
    @patch("pass_rewards._click_logged")
    def test_task_tab_retries_once_when_first_click_is_dropped(
        self,
        click: MagicMock,
        capture: MagicMock,
    ) -> None:
        overview = _image()
        task_page = _image()
        capture.side_effect = [
            (False, overview, {"overview_text_found": True}),
            (True, task_page, {"overview_text_found": False, "claim_text_found": True}),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            found, actual, _details = pass_rewards._open_pass_task_page(
                123,
                overview,
                logger=RunLogger(Path(temporary)),
                label="pass-task-page",
            )

        self.assertTrue(found)
        self.assertIs(actual, task_page)
        self.assertEqual(click.call_count, 2)
        self.assertEqual(
            [entry.kwargs["key"] for entry in click.call_args_list],
            ["pass_task_list", "pass_task_list"],
        )

    @patch("pass_rewards._read_pass_card_identity")
    @patch("pass_rewards._find_pass_badges")
    @patch("pass_rewards._wait_for_pass_selection")
    @patch("pass_rewards._click_logged")
    def test_pass_card_retries_once_when_same_marked_card_remains_unselected(
        self,
        click: MagicMock,
        wait: MagicMock,
        find_badges: MagicMock,
        read_identity: MagicMock,
    ) -> None:
        badge = _badge(0.45)
        overview = _image()
        selected = _image()
        wait.side_effect = [
            (
                False,
                overview,
                {"state": "overview", "identity_matches": False},
            ),
            (
                True,
                selected,
                {"state": "task_page", "identity_matches": True},
            ),
        ]
        find_badges.return_value = [badge]
        read_identity.return_value = (["UNTAMED赛季通行证"], {"available": True})

        with tempfile.TemporaryDirectory() as temporary:
            ok, actual, details = pass_rewards._select_pass_card(
                123,
                overview,
                badge,
                expected_identity=["UNTAMED赛季通行证"],
                logger=RunLogger(Path(temporary)),
                step=4,
            )

        self.assertTrue(ok)
        self.assertIs(actual, selected)
        self.assertEqual(details["state"], "task_page")
        self.assertEqual(click.call_count, 2)
        self.assertEqual(wait.call_count, 2)

    def test_claim_text_remaining_after_click_is_a_failure(self) -> None:
        ok, reason, click, _swipe, dismiss, home = self._run(
            captures=[_image()] * 7,
            badges=[[_badge()]],
            claim=True,
            overlays=[False, False, False, False],
        )

        self.assertFalse(ok)
        self.assertIn("固定位置文字仍存在", reason)
        keys = [entry.kwargs["key"] for entry in click.call_args_list]
        self.assertIn("pass_claim_all", keys)
        dismiss.assert_not_called()
        home.assert_not_called()

    @patch("pass_rewards._confirm_claim_all", return_value=(False, {"available": True}))
    @patch("pass_rewards._is_reward_overlay", return_value=(False, {"available": True}))
    def test_disappeared_claim_text_confirms_click_effect(
        self,
        _overlay: MagicMock,
        _claim: MagicMock,
    ) -> None:
        found, details = pass_rewards._confirm_claim_effect(_image())

        self.assertTrue(found)
        self.assertFalse(details["claim_text_present"])

    @patch("pass_rewards._is_reward_overlay")
    @patch("pass_rewards.dismiss_reward_overlays")
    def test_reward_settlement_delegates_single_click_waiting(
        self,
        dismiss: MagicMock,
        overlay: MagicMock,
    ) -> None:
        overlay.return_value = _recognition(True)
        dismiss.return_value = (True, _image(), "dismissed 1 reward overlays")
        with tempfile.TemporaryDirectory() as temporary:
            ok, _after, count, _reason = pass_rewards._dismiss_reward_results(
                123,
                _image(),
                logger=RunLogger(Path(temporary)),
            )

        self.assertTrue(ok)
        self.assertEqual(count, 0)
        dismiss.assert_called_once_with(
            123,
            logger=unittest.mock.ANY,
            max_overlays=pass_rewards.MAX_REWARD_OVERLAYS,
        )

    @patch("pass_rewards.time.sleep")
    @patch("pass_rewards.swipe_ratio_logged")
    @patch("pass_rewards._confirm_pass_reward_page", return_value=_recognition(True))
    @patch("pass_rewards.safe_capture_client", return_value=_image())
    def test_loop_has_a_hard_upper_bound(
        self,
        _capture: MagicMock,
        _page: MagicMock,
        swipe: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("pass_rewards.MAX_PASS_SWIPES", pass_rewards.MAX_PASS_LOOP_STEPS + 1),
            patch("pass_rewards._find_pass_badges", return_value=[]),
        ):
            ok, reason = pass_rewards._collect_from_pass_page(
                123,
                _image(),
                logger=RunLogger(Path(temporary)),
            )

        self.assertFalse(ok)
        self.assertIn("hard limit", reason)
        self.assertEqual(swipe.call_count, pass_rewards.MAX_PASS_LOOP_STEPS)


if __name__ == "__main__":
    unittest.main()
