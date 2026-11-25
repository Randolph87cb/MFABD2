"""Home resource icons and whole-frame OCR must not decide numeric identity."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import season_activity as season

FIXTURES = Path(__file__).parent / "fixtures/recognition"


class SeasonHomeResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = Image.open(FIXTURES / "season-home-resource-icon-noise.png").convert("RGB")
        cls.entries = season._entries(cls.image)

    def test_retained_failure_and_entry_badge_are_read_without_artwork_dependency(self):
        with patch.object(season, "_entries", return_value=self.entries):
            ok, details = season.recognize_season_home(self.image, entry=True)
        self.assertTrue(ok, details)
        self.assertEqual(details["balances"], (180486, 116148612, 1225993))
        covered = self.image.copy()
        covered.paste("black", (1900, 810, 2120, 895))
        with patch.object(season, "_entries", return_value=self.entries):
            self.assertFalse(season.recognize_season_home(covered, entry=True)[0])

    def test_missing_and_merged_balances_are_re_read_in_each_value_roi(self):
        for box, expected in zip(season.HOME_BALANCE_BOXES, ("180,486", "116,148,612", "1,225,993")):
            left, top, right, bottom = box
            entries = [(x, y, text + "+adjacent" if left <= x <= right and top <= y <= bottom else text, score)
                       for x, y, text, score in self.entries]
            for reading in (entries, []):
                with self.subTest(box=box, omitted=not reading):
                    match = season._home_balance(self.image, reading, box)
                    self.assertIsNotNone(match)
                    self.assertEqual(match.group(), expected)

    def test_uncertain_and_duplicate_values_do_not_retry_into_success(self):
        box = season.HOME_BALANCE_BOXES[0]
        for entries in ([(1279, 68, "180,486", .94)], [(1279, 68, "180,486", float("nan"))],
                        [(1279, 68, "180,486", .99)] * 2):
            with patch.object(season, "_crop_text") as crop:
                self.assertIsNone(season._home_balance(self.image, entries, box))
                crop.assert_not_called()

    def test_value_crops_work_on_an_earlier_season(self):
        with Image.open(FIXTURES / "season-home-before.png") as image:
            for box, expected in zip(season.HOME_BALANCE_BOXES, ("176,606", "110,404,802", "1,217,083")):
                with self.subTest(box=box):
                    match = season._home_balance(image, [], box)
                    self.assertIsNotNone(match)
                    self.assertEqual(match.group(), expected)

    def test_crop_retains_complete_numeric_format_confidence_and_uniqueness(self):
        for text, score, count in (("180,486", .94, 1), ("180,486", float("nan"), 1),
                                   ("0180,486", .99, 1), ("180,486+6", .99, 1),
                                   ("180486", .99, 1), ("180,486", .99, 2)):
            with self.subTest(text=text, score=score, count=count), patch(
                    "chapter_collection._entries", return_value=[(50, 50, text, score)] * count):
                self.assertIsNone(season._home_balance(self.image, [], season.HOME_BALANCE_BOXES[0]))

    def test_both_recheck_flows_reach_verified_entry_and_log_field_failure(self):
        for runner, action in ((season.run_season_battle_recheck, "season_home_check"),
                               (season.run_mamono_preparation_recheck, "mamono_home_check")):
            for failed in (False, True):
                with tempfile.TemporaryDirectory() as directory, \
                        patch.object(season, "find_game_window", return_value=123), \
                        patch.object(season, "prepare_actionable_home", return_value=(True, self.image, "home")), \
                        patch.object(season, "_entries", return_value=self.entries), \
                        patch.object(season, "click_ratio_logged") as click:
                    original = season._home_balance
                    with patch.object(season, "_home_balance", side_effect=lambda *args: None if failed else original(*args)):
                        ok, reason = runner(dry_run=True, log_root=Path(directory))
                    self.assertEqual(ok, not failed, reason)
                    click.assert_not_called()
                    events = [json.loads(line) for line in (Path(directory) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
                    check = next(event for event in events if event["action"] == action)
                    self.assertEqual(check["found"], not failed)
                    if failed:
                        self.assertEqual(len(check["details"]["unverified_balance_boxes"]), 3)


if __name__ == "__main__":
    unittest.main()
