from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import chapter_collection as chapter
import daily_automation as daily
from daily_plan import get_daily_plan

FIXTURES = Path(__file__).parent / "fixtures/recognition"
NOW = datetime(2026, 11, 7, 9, 0)
DAY = "2026-11-07"


def load(name):
    with Image.open(FIXTURES / (name + ".png")) as source:
        return source.convert("RGB")


class ChapterRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readings = {}
        cls.entries = {}
        for name in ("chapter-collection-top", "chapter-field-board", "chapter-field-circle", "chapter-field-ready",
                     "chapter-field-offset", "chapter-field-offset-left", "chapter-field-companions",
                     "chapter-field-companions-moving", "chapter-field-empty-slot-roof",
                     "chapter-collection-playing", "chapter-collection-playing-scrolled",
                     "chapter-map-ready", "chapter-map-cooldown", "chapter-map-cooldown-2",
                     "chapter-map-submenu", "chapter-portal", "chapter-talent-before",
                     "chapter-talent-after", "chapter-talent-after-2",
                     "chapter-talent-before-settled", "chapter-talent-before-settled-2"):
            image = load(name)
            cls.entries[name] = chapter._entries(image)
            cls.readings[name] = chapter.recognize_chapter_page(image)

    def test_native_navigation_and_actual_absorb_result(self):
        expected = {
            "chapter-collection-top": "chapter_card", "chapter-field-board": "field",
            "chapter-collection-playing": "chapter_card", "chapter-collection-playing-scrolled": "collection",
            "chapter-field-circle": "field", "chapter-map-ready": "map_ready",
            "chapter-field-offset": "field",
            "chapter-field-companions": "field",
            "chapter-field-companions-moving": "field",
            "chapter-field-ready": "field",
            "chapter-field-empty-slot-roof": "field",
            "chapter-map-cooldown": "map_cooldown", "chapter-map-cooldown-2": "map_cooldown",
            "chapter-map-submenu": "unknown", "chapter-portal": "portal",
            "chapter-talent-before": "talent", "chapter-talent-after": "talent",
            "chapter-talent-after-2": "talent",
            "chapter-talent-before-settled": "talent", "chapter-talent-before-settled-2": "talent",
        }
        for name, state in expected.items():
            self.assertEqual(self.readings[name][0], state, (name, self.readings[name][1]))
        before = self.readings["chapter-talent-before"][1]
        after = self.readings["chapter-talent-after"][1]
        self.assertEqual((before["used"], before["balance"]), (0, 1212183))
        self.assertEqual((after["used"], after["balance"]), (1, 1212173))
        self.assertEqual({key: self.readings["chapter-talent-after-2"][1][key] for key in ("used", "balance")},
                         {key: after[key] for key in ("used", "balance")})
        for name in ("chapter-talent-before-settled", "chapter-talent-before-settled-2"):
            settled = self.readings[name][1]
            self.assertEqual((settled["used"], settled["other_used"], settled["balance"], settled["cost"]),
                             (0, (0, 0), 1213143, 10))
        self.assertEqual(self.readings["chapter-map-cooldown"][1]["days"], 2)
        self.assertTrue(self.readings["chapter-field-ready"][1]["empty_slot"])
        image = load("chapter-field-ready")
        image.paste("white", (1800, 1030, 1870, 1090))
        self.assertFalse(chapter.recognize_chapter_page(image)[1]["empty_slot"])
        image = load("chapter-field-ready")
        image.paste("white", (1837, 1045, 1838, 1080))
        self.assertFalse(chapter.recognize_chapter_page(image)[1]["empty_slot"])

    def test_companions_require_visible_ground_portal_not_quest_button(self):
        for name in ("chapter-field-companions", "chapter-field-companions-moving"):
            details = self.readings[name][1]
            self.assertEqual(details["scene"], "circle")
            self.assertFalse(details["portal_hand"])
            self.assertTrue(details["ground_portal"], name)
        image = load("chapter-field-companions-moving")
        # Keep the town/scene intact, but hide or move the actionable circle.
        for shifted in (False, True):
            candidate = image.copy()
            if shifted:
                candidate.paste(image.crop((975, 610, 1085, 730)), (955, 610))
            else:
                candidate.paste("black", (955, 610, 1065, 730))
            state, details = chapter.recognize_chapter_page(candidate)
            self.assertEqual(state, "field", details)
            self.assertFalse(details["portal_hand"])
            self.assertFalse(details["ground_portal"])
        for candidate in (ImageEnhance.Brightness(image).enhance(.4), image.resize((1280, 720))):
            self.assertEqual(chapter.recognize_chapter_page(candidate)[0], "unknown")

    def test_empty_slot_accepts_roof_background_but_requires_complete_cross(self):
        name = "chapter-field-empty-slot-roof"
        image = load(name)
        self.assertTrue(self.readings[name][1]["empty_slot"])
        # The failed native frame exceeded the old whole-crop threshold even
        # though the empty cross is intact. Keep it as a real regression input.
        self.assertGreater(min(chapter._difference(image, template, (1800, 1030, 1870, 1090))
                               for template in ("chapter-empty-slot-before", "chapter-empty-slot")), 10)
        cases = []
        for box in ((1800, 1030, 1870, 1090), (1837, 1045, 1838, 1080),
                    (1820, 1061, 1854, 1062)):
            candidate = image.copy()
            candidate.paste("white", box)
            cases.append(candidate)
        candidate = image.copy()
        candidate.paste((128, 128, 128), (1800, 1030, 1870, 1090))
        cases.append(candidate)
        candidate = image.copy()
        # A small configured glyph cannot substitute for the empty plus.
        candidate.paste(image.crop((2004, 1025, 2044, 1065)), (1817, 1042))
        cases.append(candidate)
        real_entries = chapter._entries
        for candidate in cases:
            with patch.object(chapter, "_entries", side_effect=lambda im:
                              self.entries[name] if im.size == image.size else real_entries(im)):
                state, details = chapter.recognize_chapter_page(candidate)
            self.assertEqual(state, "field", details)
            self.assertFalse(details["empty_slot"])

    def test_unknown_dimmed_adjacent_and_ocr_error_do_not_skip(self):
        for name in ("chapter-map-ready", "chapter-talent-before", "chapter-field-circle"):
            image = load(name)
            self.assertEqual(chapter.recognize_chapter_page(ImageEnhance.Brightness(image).enhance(.4))[0], "unknown")
            self.assertEqual(chapter.recognize_chapter_page(image.resize((1280, 720)))[0], "unknown")
        with patch.object(chapter, "_entries", side_effect=RuntimeError("OCR failed")):
            self.assertEqual(chapter.recognize_chapter_page(load("chapter-map-ready"))[0], "unknown")
        for name in ("adventure-gold-map", "adventure-gold-setup-2"):
            self.assertEqual(chapter.recognize_chapter_page(load(name))[0], "unknown")

    def test_current_chapter_overlay_still_requires_exact_card_and_heading(self):
        image = load("chapter-collection-playing")
        for candidate in (ImageEnhance.Brightness(image).enhance(.4), image.resize((1280, 720))):
            self.assertNotEqual(chapter.recognize_chapter_page(candidate)[0], "chapter_card")
        original = self.entries["chapter-collection-playing"]
        image.paste(image.crop((566, 610, 746, 725)), (230, 610))
        with patch.object(chapter, "_entries", return_value=original):
            self.assertEqual(chapter.recognize_chapter_page(image)[0], "collection")
        image = load("chapter-collection-playing")
        heading = next(entry for entry in original if entry[2] == "剧情游戏卡20/20")
        for entries in ([entry for entry in original if entry != heading], original + [heading],
                        [(x, y, text, .94 if text == heading[2] else score) for x, y, text, score in original],
                        original + [(1200, 600, "确认", .99)]):
            with self.subTest(entries=entries), patch.object(chapter, "_entries", return_value=entries):
                self.assertNotEqual(chapter.recognize_chapter_page(image)[0], "chapter_card")

    def test_map_requires_unique_confident_count_and_exact_safe_town(self):
        image = load("chapter-map-ready")
        original = self.entries["chapter-map-ready"]
        for replacement in ("1/3", "3/3", "0/4", "还剩0天", "还剩9天", "未知", None):
            # Change only the absorption field.
            entries = [(x, y, replacement if 275 <= x <= 375 and 1185 <= y <= 1230 else t, s)
                       for x, y, t, s in original
                       if replacement is not None or not (275 <= x <= 375 and 1185 <= y <= 1230)]
            with self.subTest(replacement=replacement), patch.object(chapter, "_entries", return_value=entries):
                self.assertEqual(chapter.recognize_chapter_page(image)[0], "unknown")
        for score in (.94, float("nan")):
            entries = [(x, y, t, score if 275 <= x <= 375 and y > 1185 else s) for x, y, t, s in original]
            with patch.object(chapter, "_entries", return_value=entries):
                self.assertEqual(chapter.recognize_chapter_page(image)[0], "unknown")
        for extra in ((325, 1209, "0/3", .99), (1200, 600, "确认", .99)):
            with patch.object(chapter, "_entries", return_value=original + [extra]):
                self.assertEqual(chapter.recognize_chapter_page(image)[0], "unknown")
        entries = [(x, y, "战斗卢戈森林" if "安全卢戈镇" == t else t, s) for x, y, t, s in original]
        with patch.object(chapter, "_entries", return_value=entries):
            self.assertEqual(chapter.recognize_chapter_page(image)[0], "unknown")

    def test_talent_rejects_unknown_cost_and_ambiguous_counts(self):
        image = load("chapter-talent-before")
        original = self.entries["chapter-talent-before"]
        for replacement in ("每日使用0/8每日使用0/7每日使用0/7", "每日使用0/7", "每日使用8/7每日使用0/7每日使用0/7"):
            entries = [(x, y, replacement if 900 < x < 1040 and 1050 < y < 1100 else t, s)
                       for x, y, t, s in original]
            real = chapter._entries
            with patch.object(chapter, "_entries", side_effect=lambda im: entries if im.size == image.size else real(im)):
                self.assertEqual(chapter.recognize_chapter_page(image)[0], "unknown")
        with patch.object(chapter, "_crop_text", return_value=None):
            self.assertEqual(chapter.recognize_chapter_page(image)[0], "unknown")

    def test_settled_talent_still_requires_exact_portrait(self):
        image = load("chapter-talent-before-settled")
        image.paste(image.crop((937, 980, 1007, 1040)), (790, 980))
        real_entries = chapter._entries
        with patch.object(chapter, "_entries", side_effect=lambda im:
                          self.entries["chapter-talent-before-settled"] if im.size == image.size else real_entries(im)):
            state, _ = chapter.recognize_chapter_page(image)
        self.assertEqual(state, "unknown")

    def test_field_requires_known_scene_within_small_camera_offset(self):
        for name in ("chapter-field-offset", "chapter-field-offset-left"):
            image = load(name)
            entries = self.entries[name]
            self.assertEqual(self.readings[name][0], "field", self.readings[name][1])
            self.assertEqual(self.readings[name][1]["scene"], "circle")
            for candidate in (ImageEnhance.Brightness(image).enhance(.4), image.resize((1280, 720))):
                self.assertEqual(chapter.recognize_chapter_page(candidate)[0], "unknown")
            for shifted in (False, True):
                candidate = image.copy()
                if shifted:
                    candidate.paste(image.crop((540, 230, 980, 530)), (480, 230))
                else:
                    candidate.paste("black", (480, 230, 920, 530))
                with self.subTest(name=name, shifted=shifted), patch.object(chapter, "_entries", return_value=entries):
                    self.assertEqual(chapter.recognize_chapter_page(candidate)[0], "unknown")


class ChapterHourlyCooldownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = [load(name) for name in ("chapter-map-cooldown-hours", "chapter-map-cooldown-hours-2")]
        cls.entries = chapter._entries(cls.images[0])

    def test_native_two_line_countdown(self):
        for image, minute in zip(self.images, (15, 14)):
            state, details = chapter.recognize_chapter_page(image)
            self.assertEqual(state, "map_cooldown", details)
            self.assertIsNone(details["days"])
            self.assertEqual((details["hours"], details["minutes"]), (23, minute))

    def test_invalid_incomplete_or_ambiguous_countdown_is_unknown(self):
        top = next(e for e in self.entries if e[2] == "还剩23小")
        bottom = next(e for e in self.entries if e[2] == "时15分钟")
        cases = [
            [e for e in self.entries if e != top],
            [e for e in self.entries if e != bottom],
            self.entries + [top], self.entries + [bottom],
            self.entries + [(320, 1200, "0/3", .99)],
            self.entries + [(1200, 600, "确认", .99)],
        ]
        for original, replacements in ((top, ("还剩24小", "还剩023小", "还剩23天", "未知")),
                                       (bottom, ("时60分钟", "时015分钟", "时15秒", "未知"))):
            for text in replacements:
                replacement = (original[0], original[1], text, original[3])
                cases.append([replacement if e == original else e for e in self.entries])
        for original in (top, bottom):
            for score in (.94, float("nan")):
                replacement = (*original[:3], score)
                cases.append([replacement if e == original else e for e in self.entries])
        zero = {top[2]: "还剩0小", bottom[2]: "时0分钟"}
        cases.append([(x, y, zero.get(t, t), s) for x, y, t, s in self.entries])
        for entries in cases:
            with self.subTest(entries=entries), patch.object(chapter, "_entries", return_value=entries):
                self.assertEqual(chapter.recognize_chapter_page(self.images[0])[0], "unknown")


class ChapterFourDayCooldownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Native result frames from the 2026-11-11 failed run, not synthetic OCR.
        cls.images = [load(name) for name in ("chapter-map-cooldown-four-days",
                                             "chapter-map-cooldown-four-days-2")]
        cls.entries = chapter._entries(cls.images[0])

    def test_native_four_day_result_is_cooldown(self):
        for image in self.images:
            state, details = chapter.recognize_chapter_page(image)
            self.assertEqual(state, "map_cooldown", details)
            self.assertEqual(details["days"], 4)

    def test_four_day_support_preserves_counter_and_page_guards(self):
        counter = next(e for e in self.entries if e[2] == "还剩4天")
        cases = [self.entries + [counter], [e for e in self.entries if e != counter],
                 self.entries + [(1200, 600, "确认", .99)]]
        for text in ("还剩0天", "还剩5天", "还剩04天", "还剩14天", "还剩4小时"):
            cases.append([(*e[:2], text, e[3]) if e == counter else e for e in self.entries])
        for score in (.94, float("nan")):
            cases.append([(*e[:3], score) if e == counter else e for e in self.entries])
        for entries in cases:
            with self.subTest(entries=entries), patch.object(chapter, "_entries", return_value=entries):
                self.assertEqual(chapter.recognize_chapter_page(self.images[0])[0], "unknown")
        for candidate in (ImageEnhance.Brightness(self.images[0]).enhance(.4),
                          self.images[0].resize((1280, 720))):
            self.assertEqual(chapter.recognize_chapter_page(candidate)[0], "unknown")


class ChapterFlowTests(unittest.TestCase):
    def run_flow(self, root, *, cooldown=False, fail=None, change=None, dry_run=False,
                 used=0, balance=1212183, scene="circle", day_change=False,
                 collection_images=None, arrival_image=None, map_images=None, clicks=None,
                 talent_field_image=None, talent_images=None):
        image = Image.new("RGB", (2304, 1296))
        events = []
        checks = []
        field = dict(state="field", scene=scene, empty_slot=True, portal_hand=True, ground_portal=False)
        def wait(hwnd, *, label, recognize, **_):
            checks.append(label)
            if talent_images and label in {"chapter-before-1", "chapter-before-2"}:
                candidate = talent_images[int(label[-1]) - 1]
                found, values = recognize(candidate)
                return found, candidate, values
            if talent_field_image is not None and label in {"chapter-tab-three", "chapter-after-cast"}:
                found, values = recognize(talent_field_image)
                return found, talent_field_image, values
            if map_images and label in {"chapter-map-1", "chapter-map-2", "chapter-result-1", "chapter-result-2"}:
                candidate = map_images[int(label[-1]) - 1]
                found, values = recognize(candidate)
                return found, candidate, values
            if arrival_image is not None and label in {"chapter-arrival", "chapter-field"}:
                found, values = recognize(arrival_image)
                return found, arrival_image, values
            if collection_images and label in {"chapter-collection", "chapter-story-card"}:
                candidate = collection_images[0 if label == "chapter-collection" else -1]
                found, values = recognize(candidate)
                return found, candidate, values
            values = dict(field)
            if label == "chapter-collection":
                values = dict(state="collection")
            elif label == "chapter-story-card":
                values = dict(state="chapter_card")
            elif label.startswith("chapter-map-") or label.startswith("chapter-result-") and label[-1:].isdigit():
                values = dict(state="map_cooldown" if cooldown or label.startswith("chapter-result-") else "map_ready", days=2 if cooldown or label.startswith("chapter-result-") else None)
            elif label == "chapter-portal":
                values = dict(state="portal")
            elif label.startswith("chapter-before-"):
                values = dict(state="talent", used=used, other_used=(0, 0), balance=balance, cost=10)
            elif label in {"chapter-after-1", "chapter-after-2"}:
                values = dict(state="talent", used=used + 1, other_used=(0, 0), balance=balance - 10, cost=10)
            if change and change[0] == label:
                values.update(change[1])
            return label != fail, image, values
        def click(hwnd, im, point, *, key, **_):
            if clicks is not None:
                clicks.append((key, point))
            if key == "chapter_cast_absorb_once":
                record = json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "action_pending")
                self.assertEqual(record["before"]["used"], used)
            events.append(key)
        with ExitStack() as stack:
            stack.enter_context(patch.object(chapter, "find_game_window", return_value=123))
            stack.enter_context(patch.object(chapter, "prepare_actionable_home", return_value=(True, image, "home")))
            stack.enter_context(patch.object(chapter, "wait_for_recognition", side_effect=wait))
            stack.enter_context(patch.object(chapter, "click_ratio_logged", side_effect=click))
            stack.enter_context(patch.object(chapter.time, "sleep"))
            if day_change:
                stack.enter_context(patch.object(chapter, "game_day_key", side_effect=[DAY, DAY, "2026-11-08"]))
            result = chapter.run_chapter_absorb(dry_run=dry_run, log_root=root / "logs", state_root=root / "state", now=NOW)
        return result, events, checks

    def test_roof_background_opens_talent_and_casts_once_then_records_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (ok, reason), events, _ = self.run_flow(
                root, talent_field_image=load("chapter-field-empty-slot-roof"))
            self.assertTrue(ok, reason)
            self.assertEqual(events.count("chapter_talent_page"), 1)
            self.assertEqual(events.count("chapter_cast_absorb_once"), 1)
            self.assertEqual(events[-1], "chapter_return_home")
            record = json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "completed")
            self.assertTrue(record["returned_home"])

    def test_settled_unused_talent_casts_once_and_same_day_resume_does_not_replay(self):
        images = [load(name) for name in ("chapter-talent-before-settled", "chapter-talent-before-settled-2")]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (ok, reason), events, checks = self.run_flow(root, balance=1213143, talent_images=images)
            self.assertTrue(ok, reason)
            self.assertIn("chapter-before-2", checks)
            self.assertEqual(events.count("chapter_cast_absorb_once"), 1)
            self.assertEqual(events[-1], "chapter_return_home")
            record = json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "completed")
            self.assertEqual(record["after"]["used"], 1)
            self.assertEqual(record["after"]["balance"], 1213133)
            self.assertTrue(record["returned_home"])
            (ok, reason), events, _ = self.run_flow(root)
            self.assertTrue(ok, reason)
            self.assertEqual(events, [])

    def test_companions_use_ground_circle_once_and_require_portal_before_cast(self):
        # Second real run: a roaming companion overlaps the old template edge.
        image = load("chapter-field-companions-moving")
        for fail in (None, "chapter-portal"):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                clicks = []
                (ok, reason), events, _ = self.run_flow(root, arrival_image=image, fail=fail, clicks=clicks)
                self.assertEqual(ok, fail is None, reason)
                self.assertEqual(events.count("chapter_visible_portal"), 1)
                self.assertIn(("chapter_visible_portal", (1010 / 2304, 670 / 1296)), clicks)
                self.assertNotIn("chapter_portal_hand", events)
                if fail:
                    self.assertNotIn("chapter_talent_page", events)
                    self.assertNotIn("chapter_cast_absorb_once", events)
                    self.assertFalse((root / "state" / (DAY + ".json")).exists())
                else:
                    self.assertEqual(events.count("chapter_cast_absorb_once"), 1)
                    record = json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))
                    self.assertEqual(record["status"], "completed")
                    self.assertTrue(record["returned_home"])

    def test_current_chapter_overlay_navigates_with_native_recognition(self):
        playing = load("chapter-collection-playing")
        scrolled = load("chapter-collection-playing-scrolled")
        for frames in ((playing,), (scrolled, playing)):
            with self.subTest(scrolled=len(frames) == 2), tempfile.TemporaryDirectory() as temporary:
                (ok, reason), events, _ = self.run_flow(Path(temporary), cooldown=True, collection_images=frames)
                self.assertTrue(ok, reason)
                self.assertEqual(events.count("chapter_story_scroll"), len(frames) - 1)
                self.assertEqual(events.count("chapter_one"), 1)
                self.assertNotIn("chapter_cast_absorb_once", events)

    def test_offset_arrival_reaches_map_cooldown_and_returns_home(self):
        for name in ("chapter-field-offset", "chapter-field-offset-left"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, reason), events, _ = self.run_flow(
                    root, cooldown=True, arrival_image=load(name))
                self.assertTrue(ok, reason)
                self.assertTrue(reason.startswith("skipped:"))
                self.assertEqual(events.count("chapter_map"), 1)
                self.assertEqual(events[-1], "chapter_return_home")
                self.assertNotIn("chapter_cast_absorb_once", events)
                record = json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "skipped")
                self.assertTrue(record["returned_home"])
                (_, reason), resumed_events, _ = self.run_flow(root, cooldown=True)
                self.assertTrue(reason.startswith("skipped:"))
                self.assertEqual(resumed_events, [])

    def test_one_cast_intent_precedes_input_and_only_complete_after_home(self):
        for scene in ("circle", "board"):
            with self.subTest(scene=scene), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, reason), events, _ = self.run_flow(root, scene=scene)
                self.assertTrue(ok, reason)
                self.assertEqual(events.count("chapter_cast_absorb_once"), 1)
                self.assertEqual(events[-1], "chapter_return_home")
                record = json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "completed")
                self.assertTrue(record["returned_home"])
                self.assertEqual(record["after"]["used"], 1)

    def test_hourly_cooldown_minute_tick_skips_cast_and_records_after_home(self):
        frames = [load(name) for name in ("chapter-map-cooldown-hours", "chapter-map-cooldown-hours-2")]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (ok, reason), events, checks = self.run_flow(root, map_images=frames)
            self.assertTrue(ok, reason)
            self.assertTrue(reason.startswith("skipped:"))
            self.assertIn("chapter-map-2", checks)
            self.assertNotIn("chapter_talent_page", events)
            self.assertNotIn("chapter_cast_absorb_once", events)
            self.assertEqual(events[-1], "chapter_return_home")
            record = json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "skipped")
            self.assertTrue(record["returned_home"])
            (_, reason), events, _ = self.run_flow(root, map_images=frames)
            self.assertTrue(reason.startswith("skipped:"))
            self.assertEqual(events, [])

    def test_cooldown_does_not_open_talent_or_cast(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (ok, reason), events, _ = self.run_flow(root, cooldown=True)
            self.assertTrue(ok)
            self.assertTrue(reason.startswith("skipped:"))
            self.assertNotIn("chapter_cast_absorb_once", events)
            self.assertNotIn("chapter_talent_page", events)
            self.assertNotIn("chapter_portal_hand", events)
            (ok, reason), events, _ = self.run_flow(root)
            self.assertTrue(ok)
            self.assertEqual(events, [])

    def test_any_navigation_failure_stops_without_repeated_input(self):
        for label in ("chapter-collection", "chapter-story-card", "chapter-arrival", "chapter-map-1",
                      "chapter-map-2", "chapter-field", "chapter-portal", "chapter-portal-return",
                      "chapter-tab-three", "chapter-before-1", "chapter-before-2",
                      "chapter-after-cast", "chapter-after-1", "chapter-after-2", "chapter-after-talent",
                      "chapter-result-1", "chapter-result-2", "chapter-result-field", "chapter-tab-one", "chapter-home"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, _), events, _ = self.run_flow(root, fail=label)
                self.assertFalse(ok)
                self.assertLessEqual(events.count("chapter_cast_absorb_once"), 1)
                if "chapter_cast_absorb_once" in events:
                    record = json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))
                    self.assertEqual(record["status"], "action_pending")

    def test_exhausted_insufficient_or_nonempty_slot_never_cast(self):
        for options in (dict(used=7), dict(balance=9),
                        dict(change=("chapter-tab-three", dict(empty_slot=False))),
                        dict(change=("chapter-field", dict(portal_hand=False)))):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, _), events, _ = self.run_flow(root, **options)
                self.assertFalse(ok)
                self.assertNotIn("chapter_cast_absorb_once", events)
                self.assertFalse((root / "state" / (DAY + ".json")).exists())

    def test_two_frame_changes_or_incorrect_delta_remain_pending(self):
        for label, value in (("chapter-map-2", dict(state="map_cooldown", days=2)),
                             ("chapter-map-2", dict(state="map_ready", hours=23)),
                             ("chapter-before-2", dict(balance=1212182)),
                             ("chapter-after-2", dict(used=2)),
                             ("chapter-after-1", dict(other_used=(1, 0))),
                             ("chapter-after-2", dict(balance=1212172)),
                             ("chapter-result-2", dict(days=1))):
            with self.subTest(label=label, value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (ok, _), events, _ = self.run_flow(root, change=(label, value))
                self.assertFalse(ok)
                self.assertLessEqual(events.count("chapter_cast_absorb_once"), 1)

    def test_pending_corrupt_and_older_day_intents_block_before_window_access(self):
        for raw in ("{", "[]", json.dumps(dict(target="卢戈镇", status="action_pending")),
                    json.dumps(dict(target="卢戈镇", status="completed"))):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "2026-11-06.json").write_text(raw, encoding="utf-8")
                with patch.object(chapter, "find_game_window") as window:
                    self.assertFalse(chapter.run_chapter_absorb(dry_run=False, log_root=root / "logs", state_root=root, now=NOW)[0])
                    window.assert_not_called()

    def test_same_day_pending_verifies_native_cooldown_and_returns_without_recasting(self):
        frames = [load(name) for name in ("chapter-map-cooldown-four-days", "chapter-map-cooldown-four-days-2")]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (ok, _), events, _ = self.run_flow(root, fail="chapter-result-1", balance=1213593)
            self.assertFalse(ok)
            self.assertEqual(events.count("chapter_cast_absorb_once"), 1)
            path = root / "state" / (DAY + ".json")
            pending = json.loads(path.read_text(encoding="utf-8"))
            (ok, reason), events, checks = self.run_flow(root, map_images=frames, balance=1213593)
            self.assertTrue(ok, reason)
            self.assertFalse(reason.startswith("skipped:"))
            self.assertNotIn("chapter_cast_absorb_once", events)
            self.assertNotIn("chapter_portal_hand", events)
            self.assertNotIn("chapter-before-1", checks)
            self.assertIn("chapter-after-2", checks)
            self.assertIn("chapter-result-2", checks)
            self.assertEqual(events[-1], "chapter_return_home")
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((record["status"], record["returned_home"]), ("completed", True))
            for key in ("before", "before_screenshot", "map_before"):
                self.assertEqual(record[key], pending[key])
            self.assertEqual(record["after"]["balance"], 1213583)
            (ok, reason), events, _ = self.run_flow(root)
            self.assertTrue(ok, reason)
            self.assertEqual(events, [])

    def test_pending_recovery_mismatch_or_navigation_failure_preserves_intent(self):
        options = [dict(cooldown=False),
                   dict(used=1), dict(balance=1212184),
                   dict(change=("chapter-after-1", dict(used=0))),
                   dict(change=("chapter-after-2", dict(balance=1212172))),
                   dict(change=("chapter-after-1", dict(other_used=(1, 0)))),
                   dict(change=("chapter-tab-three", dict(empty_slot=False))),
                   dict(change=("chapter-map-2", dict(days=1)))]
        options.extend(dict(fail=label) for label in (
            "chapter-map-1", "chapter-map-2", "chapter-after-1", "chapter-after-2",
            "chapter-result-1", "chapter-result-2", "chapter-home"))
        for changes in options:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.run_flow(root, fail="chapter-result-1")
                path = root / "state" / (DAY + ".json")
                original = path.read_bytes()
                (ok, _), events, _ = self.run_flow(root, **(dict(cooldown=True) | changes))
                self.assertFalse(ok)
                self.assertNotIn("chapter_cast_absorb_once", events)
                self.assertEqual(path.read_bytes(), original)

    def test_pending_dry_run_rollover_and_invalid_record_never_cast(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.run_flow(root, fail="chapter-result-1")
            path = root / "state" / (DAY + ".json")
            original = path.read_bytes()
            (ok, _), events, _ = self.run_flow(root, dry_run=True)
            self.assertTrue(ok)
            self.assertEqual(events, [])
            self.assertEqual(path.read_bytes(), original)
            with patch.object(chapter, "game_day_key", side_effect=[DAY, "2026-11-08"]):
                (ok, _), events, _ = self.run_flow(root, cooldown=True)
            self.assertFalse(ok)
            self.assertNotIn("chapter_cast_absorb_once", events)
            self.assertEqual(path.read_bytes(), original)
            # Even a fully formed intent cannot be resumed on a later game day.
            with patch.object(chapter, "find_game_window") as window:
                self.assertFalse(chapter.run_chapter_absorb(
                    dry_run=False, log_root=root / "later", state_root=root / "state",
                    now=datetime(2026, 11, 8, 9))[0])
                window.assert_not_called()
            for change in (dict(used=True), dict(used=7), dict(balance=9), dict(cost=20),
                           dict(other_used=[0]), dict(other_used=[0, True]), dict(other_used=[0, 8])):
                record = json.loads(original)
                record["before"].update(change)
                path.write_text(json.dumps(record), encoding="utf-8")
                with self.subTest(change=change), patch.object(chapter, "find_game_window") as window:
                    self.assertFalse(chapter.run_chapter_absorb(
                        dry_run=False, log_root=root / "invalid", state_root=root / "state", now=NOW)[0])
                    window.assert_not_called()

    def test_dry_run_and_day_rollover(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (ok, _), events, _ = self.run_flow(root, dry_run=True)
            self.assertTrue(ok)
            self.assertEqual(events, [])
            self.assertFalse((root / "state").exists())
            (ok, _), events, _ = self.run_flow(root, day_change=True)
            self.assertFalse(ok)
            self.assertEqual(events.count("chapter_cast_absorb_once"), 1)
            self.assertEqual(json.loads((root / "state" / (DAY + ".json")).read_text(encoding="utf-8"))["status"], "action_pending")
        self.assertEqual(chapter.game_day_key(datetime(2026, 11, 7, 7, 59)), "2026-11-06")
        self.assertEqual(chapter.game_day_key(datetime(2026, 11, 7, 8)), DAY)
        self.assertEqual(chapter.game_day_key(datetime(2026, 11, 7, 0, tzinfo=timezone.utc)), DAY)

    def test_formal_entry_failure_propagation_skip_and_partial_plan(self):
        for success in (True, False):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch.object(daily, "ensure_home", return_value=(True, "home")) as home, \
                     patch.object(daily, "run_chapter_absorb", return_value=(success, "skipped:cooldown")) as runner:
                    if success:
                        reason = daily._execute_daily_phase("chapter_collection", master=daily.MasterLogger(root), run_root=root)
                        self.assertTrue(reason.startswith("skipped:"))
                    else:
                        with self.assertRaises(daily.DailyRunError):
                            daily._execute_daily_phase("chapter_collection", master=daily.MasterLogger(root), run_root=root)
                    home.assert_called_once()
                    runner.assert_called_once()
        for preset in ("fast", "detailed"):
            plan = get_daily_plan(preset)
            stage = next(s for s in plan if s.id == "chapter_collection")
            self.assertEqual(stage.availability, "partial")
            self.assertFalse(stage.implemented)
            self.assertTrue(stage.current_runnable)
            self.assertEqual(stage.current_runners, ("chapter_collection.run_chapter_absorb",))
            self.assertLess([s.id for s in plan].index(stage.id), [s.id for s in plan].index("arena"))


if __name__ == "__main__":
    unittest.main()
