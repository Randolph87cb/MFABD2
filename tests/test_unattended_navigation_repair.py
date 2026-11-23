"""Retained November 23 failures and source-gated navigation regressions."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import business_management as business
import reward_flow as flow
import season_activity as season
import weekly_cabin as cabin
import daily_automation as daily
from free_gacha import classify_state
from plaza_prayer import recognize_prayer_page
from weekly_cabin import recognize_cabin_page

FIXTURES = Path(__file__).parent / "fixtures/recognition"


class RetainedFailureTests(unittest.TestCase):
    def test_missing_countdown_is_read_from_its_badge(self):
        with Image.open(FIXTURES / "season-home-countdown-omitted.png") as image:
            ok, details = season.recognize_season_home(image, entry=True)
            self.assertTrue(ok, details)
            self.assertEqual(details["balances"], (180116, 114908392, 1223493))
            covered = image.copy()
            covered.paste("black", (1900, 810, 2120, 895))
            self.assertFalse(season.recognize_season_home(covered, entry=True)[0])

    def test_npc_overlap_is_restaurant_but_covered_title_is_not(self):
        with Image.open(FIXTURES / "restaurant-title-npc-overlap.png") as image:
            for size in (image.size, (1920, 1080)):
                self.assertEqual(classify_state(image.resize(size))[0], "restaurant_home")
            covered = image.copy()
            covered.paste("black", (90, 10, 690, 160))
            self.assertNotEqual(classify_state(covered)[0], "restaurant_home")

    def test_dropped_entries_remain_source_not_destination(self):
        for name, destination in (("cabin", recognize_cabin_page),
                                  ("prayer", recognize_prayer_page),
                                  ("business", lambda image: (classify_state(image)[0], {}))):
            with self.subTest(name=name), Image.open(FIXTURES / f"{name}-entry-unchanged-home.png") as image:
                self.assertTrue(flow._recognize_actionable_home(image)[0])
                self.assertIn(destination(image)[0], {"unknown", "real_home"})


class NavigationTransitionTests(unittest.TestCase):
    def test_only_fresh_source_allows_one_retry(self):
        image = Image.new("RGB", (100, 60))
        for source in (True, False):
            click, wait = MagicMock(), MagicMock(side_effect=[(False, image, {}), (True, image, {})])
            with self.subTest(source=source), patch.object(flow, "safe_capture_client", return_value=image), \
                    patch.object(flow, "_recognize_actionable_home", return_value=(source, {})):
                ok, _, _ = flow.navigate_from_home(
                    123, image, (.1, .2), key="entry", recognize=lambda _: (False, {}),
                    label="destination", logger=MagicMock(), click=click, wait=wait)
            self.assertEqual(ok, source)
            self.assertEqual(click.call_count, 2 if source else 1)

    def test_destination_after_timeout_prevents_second_click(self):
        image = Image.new("RGB", (100, 60))
        click = MagicMock()
        with patch.object(flow, "safe_capture_client", return_value=image):
            ok, _, _ = flow.navigate_from_home(
                123, image, (.1, .2), key="entry", recognize=lambda _: (True, {}),
                label="destination", logger=MagicMock(), click=click,
                wait=MagicMock(return_value=(False, image, {})))
        self.assertTrue(ok)
        self.assertEqual(click.call_count, 1)

    def test_business_fade_back_to_home_retries_only_confirmed_source(self):
        image = Image.new("RGB", (100, 60))
        for source in (True, False):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(business, "find_game_window", return_value=123), \
                    patch.object(business, "safe_capture_client", return_value=image), \
                    patch.object(business, "classify_state", return_value=("real_home", {})), \
                    patch.object(business, "click_with_fixed_retry", return_value=(True, "loading", image, "fade")), \
                    patch.object(business, "_wait_out_loading", return_value=("real_home", image)), \
                    patch.object(business, "_recognize_actionable_home", return_value=(source, {})), \
                    patch.object(business, "navigate_from_home", return_value=(True, image, {})) as retry:
                ok, _ = business.open_business_management(dry_run=False, log_root=Path(tmp))
            self.assertEqual(ok, source)
            self.assertEqual(retry.call_count, int(source))

    def test_network_loading_extends_wait_without_input(self):
        image = Image.new("RGB", (100, 60))
        with patch.object(flow, "safe_capture_client", return_value=image), \
                patch.object(flow.time, "monotonic", side_effect=[0, 1000]), \
                patch.object(flow.time, "sleep"), patch.object(flow, "click_ratio_logged") as click:
            ok, _, _ = flow.wait_for_recognition(
                123, logger=MagicMock(), label="network", timeout=1,
                recognize=MagicMock(side_effect=[(False, {"network_loading": True}), (True, {})]))
        self.assertTrue(ok)
        click.assert_not_called()


class CabinIdentityRecoveryTests(unittest.TestCase):
    def test_unreadable_owner_permits_navigation_but_never_a_like(self):
        with Image.open(FIXTURES / "cabin-unreadable-owner.png") as image:
            state, details = cabin.recognize_cabin_page(image)
            self.assertEqual(state, "unknown")
            self.assertTrue(details["identity_missing"])
            self.assertTrue(details["count_verified"])
            self.assertEqual(cabin.recognize_cabin_navigation(image)[0], "visit_random")
            for box in ((203, 49, 216, 73), (330, 25, 450, 90), (1850, 1160, 2150, 1240)):
                covered = image.copy()
                covered.paste("black", box)
                self.assertEqual(cabin.recognize_cabin_navigation(covered)[0], "unknown")
        with Image.open(FIXTURES / "season-home-countdown-omitted.png") as image:
            self.assertEqual(cabin.recognize_cabin_navigation(image)[0], "unknown")

    def test_unreadable_cabin_recovery_precedes_brightness_overlay_inputs(self):
        with Image.open(FIXTURES / "cabin-unreadable-owner.png") as image, \
                tempfile.TemporaryDirectory() as directory, \
                patch("open_game.find_game_window", return_value=123), \
                patch.object(daily, "safe_capture_client", return_value=image), \
                patch.object(daily, "classify_state", return_value=("blocking_ad_overlay", {})), \
                patch.object(daily, "return_from_cabin", return_value=(True, "home")) as recover, \
                patch.object(daily, "click_with_fixed_retry") as dismiss:
            self.assertEqual(daily.ensure_home(timeout=5, log_root=Path(directory)), (True, "home"))
        recover.assert_called_once()
        dismiss.assert_not_called()

    def test_cabin_back_requires_navigation_and_confirmed_home(self):
        image = Image.new("RGB", (2304, 1296))
        for state, home in (("unknown", False), ("visit_random", False), ("visit_random", True)):
            with self.subTest(state=state, home=home), \
                    patch.object(cabin, "recognize_cabin_navigation", return_value=(state, {})), \
                    patch.object(cabin, "click_ratio_logged") as click, \
                    patch.object(cabin, "wait_for_recognition", return_value=(home, image, {})):
                ok, _ = cabin.return_from_cabin(123, image, logger=MagicMock())
            self.assertEqual(ok, home)
            self.assertEqual(click.call_count, int(state != "unknown"))

    def test_browse_is_bounded_and_never_sends_a_like(self):
        unreadable = {"identity_missing": True, "count_verified": True}
        with Image.open(FIXTURES / "cabin-unreadable-owner.png") as image:
            for found in (True, False):
                with self.subTest(found=found), \
                        patch.object(cabin, "recognize_cabin_navigation", return_value=("visit_random", {})), \
                        patch.object(cabin, "click_ratio_logged") as click, \
                        patch.object(cabin, "wait_for_recognition", return_value=(found, image, unreadable)):
                    ok, _, _ = cabin.next_readable_cabin(123, image, unreadable, logger=MagicMock())
                self.assertEqual(ok, found)
                self.assertEqual(click.call_count, 1 if found else 2)
                self.assertEqual({call.kwargs["key"] for call in click.call_args_list},
                                 {"cabin_next_unreadable_owner"})
            for details, covered in (({}, False), ({"identity_missing": True}, False), (unreadable, True)):
                candidate = image.copy()
                if covered:
                    candidate.paste("black", (1420, 1160, 1505, 1240))
                with patch.object(cabin, "recognize_cabin_navigation", return_value=("visit_random", {})), \
                        patch.object(cabin, "click_ratio_logged") as click:
                    self.assertFalse(cabin.next_readable_cabin(123, candidate, details, logger=MagicMock())[0])
                click.assert_not_called()
