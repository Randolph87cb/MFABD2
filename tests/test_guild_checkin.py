from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_automation as daily
import guild_checkin as guild
from daily_plan import get_daily_plan

FIXTURES = Path(__file__).parent / "fixtures/recognition"


class GuildRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Image.open(FIXTURES / "guild-nonmember-1.png").convert("RGB")
        cls.entries = guild._entries(cls.source)

    def test_native_nonmember_home_and_adjacent_pages(self):
        for name, expected in (("guild-nonmember-1", True), ("guild-nonmember-2", True),
                               ("guild-recommendations-approval", True),
                               ("guild-entry-loading", False), ("guild-home-before", False),
                               ("business-management-claim-disabled-2304x1296", False),
                               ("season-locked-1", False)):
            with self.subTest(name=name), Image.open(FIXTURES / (name + ".png")) as image:
                found, details = guild.recognize_nonmember_guild(image)
                self.assertEqual(found, expected, details)
                if found:
                    self.assertEqual(details["reading"], {"member": False, "checkin": "unavailable"})
        for name in ("guild-home-before", "guild-home-after"):
            with Image.open(FIXTURES / (name + ".png")) as image:
                found, details = guild.recognize_guild_home(image, entry=True)
                self.assertTrue(found, details)
                self.assertEqual(details["balances"], (177326, 110945902, 1217793))

    def test_every_nonmember_anchor_is_required_and_uncertainty_rejected(self):
        # Synthetic member/application/empty-list variants, not real member evidence.
        for index, (_, _, text, _) in enumerate(self.entries):
            if text not in {"公会", "推荐公会", "申请列表", "创建公会", "用公会名称搜索", "搜索", "刷新", "立即加入"}:
                continue
            if text == "立即加入" and self.entries[index][1] > 410:
                continue
            for score in (.94, float("nan")):
                entries = list(self.entries)
                x, y, text, _ = entries[index]
                entries[index] = (x, y, text, score)
                with self.subTest(index=index, score=score), patch.object(guild, "_entries", return_value=entries):
                    self.assertFalse(guild.recognize_nonmember_guild(self.source)[0])
            with patch.object(guild, "_entries", return_value=self.entries[:index] + self.entries[index + 1:]):
                self.assertFalse(guild.recognize_nonmember_guild(self.source)[0])
        for entries in (self.entries + [(380, 60, "公会", .99)],
                        self.entries + [(1150, 650, "确认加入", .55)],
                        self.entries + [(1150, 650, "签到", .99)],
                        self.entries + [(1150, 650, "公会管理", .99)]):
            with patch.object(guild, "_entries", return_value=entries):
                self.assertFalse(guild.recognize_nonmember_guild(self.source)[0])

    def test_obscured_back_menu_size_and_ocr_errors_stop(self):
        images = [self.source.resize((1152, 648)), ImageEnhance.Brightness(self.source).enhance(.4)]
        for box in ((203, 49, 216, 73), (170, 145, 330, 385)):
            changed = self.source.copy()
            changed.paste((0, 0, 0), box)
            images.append(changed)
        for image in images:
            self.assertFalse(guild.recognize_nonmember_guild(image)[0])
        with patch.object(guild, "_entries", side_effect=RuntimeError("OCR offline")):
            found, details = guild.recognize_nonmember_guild(self.source)
        self.assertFalse(found)
        self.assertIn("OCR offline", details["error"])

    def test_home_entry_and_all_three_balances_are_required(self):
        with Image.open(FIXTURES / "guild-home-before.png") as image:
            entries = guild._entries(image)
            for region in ((400, 215, 470, 260), (1200, 40, 1350, 95),
                           (1390, 40, 1570, 95), (1600, 40, 1770, 95)):
                left, top, right, bottom = region
                changed = [(x, y, text, .94 if left <= x <= right and top <= y <= bottom else score)
                           for x, y, text, score in entries]
                with patch.object(guild, "_entries", return_value=changed):
                    self.assertFalse(guild.recognize_guild_home(image, entry=True)[0])

    def test_join_policy_and_title_decoration_do_not_define_membership(self):
        for policy in ("立即加入", "审核加入", "申请加入"):
            entries = [(x, y, policy if text == "立即加入" else
                        text + "?帮助" if text == "公会" else text, score)
                       for x, y, text, score in self.entries]
            with self.subTest(policy=policy), patch.object(guild, "_entries", return_value=entries):
                found, details = guild.recognize_nonmember_guild(self.source)
                self.assertTrue(found, details)
                self.assertEqual(details["reading"], {"member": False, "checkin": "unavailable"})

    def test_return_navigation_does_not_infer_membership_from_missing_rows(self):
        entries = [entry for entry in self.entries if entry[2] != "立即加入"]
        with patch.object(guild, "_entries", return_value=entries):
            found, details = guild.recognize_guild_navigation(self.source)
            self.assertTrue(found, details)
            self.assertNotIn("reading", details)
            self.assertFalse(guild.recognize_nonmember_guild(self.source)[0])
        for label in ("公会", "推荐公会", "申请列表", "创建公会", "搜索", "刷新", "用公会名称搜索"):
            entries = [entry for entry in self.entries if entry[2] != label]
            with self.subTest(label=label), patch.object(guild, "_entries", return_value=entries):
                self.assertFalse(guild.recognize_guild_navigation(self.source)[0])


class GuildFlowTests(unittest.TestCase):
    def test_recovery_uses_back_once_and_requires_verified_home(self):
        image = Image.new("RGB", (2304, 1296))
        for known, home, count in ((True, True, 1), (True, False, 1), (False, False, 0)):
            with self.subTest(known=known, home=home), ExitStack() as stack:
                stack.enter_context(patch.object(guild, "recognize_guild_navigation", return_value=(known, {})))
                stack.enter_context(patch.object(guild, "wait_for_recognition", return_value=(home, image, {})))
                click = stack.enter_context(patch.object(guild, "click_ratio_logged"))
                ok, _ = guild.return_from_guild(123, image, logger=None)
                self.assertEqual(ok, known and home)
                self.assertEqual(click.call_count, count)
                if count:
                    self.assertEqual(click.call_args.args[2], (208 / 2304, 60 / 1296))
                    self.assertEqual(click.call_args.kwargs["key"], "guild_return_home")

    def test_ensure_home_does_not_probe_or_operate_guild(self):
        image = Image.new("RGB", (2304, 1296))
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch("open_game.find_game_window", return_value=123))
            stack.enter_context(patch.object(daily, "safe_capture_client", return_value=image))
            stack.enter_context(patch.object(daily, "classify_state", return_value=("unknown", {})))
            stack.enter_context(patch.object(daily, "recognize_season_navigation", return_value=("unknown", {})))
            stack.enter_context(patch.object(daily, "recognize_return_home_control", return_value=(False, {})))
            probe = stack.enter_context(patch.object(guild, "recognize_guild_navigation"))
            recover = stack.enter_context(patch.object(guild, "return_from_guild"))
            ok, reason = daily.ensure_home(timeout=5, log_root=Path(directory))
            self.assertFalse(ok)
            self.assertIn("unknown", reason)
            probe.assert_not_called()
            recover.assert_not_called()

    def flow(self, *, failure=None, changed=False, home_changed=False, dry_run=False, bad_start=False):
        image = Image.new("RGB", (2304, 1296))
        labels = []

        def wait(hwnd, *, label, **kwargs):
            labels.append(label)
            member = changed and label == "guild-nonmember-2"
            return failure != label, image, dict(reading={"member": member, "checkin": "unavailable"},
                balances=(1, 2, 4 if home_changed and label == "guild-home" else 3))

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in (("find_game_window", 123),
                                ("prepare_actionable_home", (True, image, "home")),
                                ("recognize_guild_home", (not bad_start, {"balances": (1, 2, 3)}))):
                stack.enter_context(patch.object(guild, name, return_value=value))
            stack.enter_context(patch.object(guild, "wait_for_recognition", side_effect=wait))
            stack.enter_context(patch.object(guild.time, "sleep"))
            click = stack.enter_context(patch.object(guild, "click_ratio_logged"))
            result = guild.run_guild_nonmember_check(dry_run=dry_run, log_root=Path(directory))
            keys = [call.kwargs["key"] for call in click.call_args_list]
            points = [call.args[2] for call in click.call_args_list]
        return result, keys, labels, points

    def test_nonmember_is_skipped_without_any_join_claim_or_state_write(self):
        (ok, reason), keys, labels, points = self.flow()
        self.assertTrue(ok, reason)
        self.assertTrue(reason.startswith("skipped:"))
        self.assertIn("未入会或签到", reason)
        self.assertEqual(keys, ["guild_entry", "guild_return_home"])
        self.assertEqual(points, [(434 / 2304, 180 / 1296), (208 / 2304, 60 / 1296)])
        self.assertEqual(labels, ["guild-nonmember-1", "guild-nonmember-2", "guild-home"])

    def test_unknown_joined_or_failed_observation_never_retries_or_joins(self):
        for label, count in (("guild-nonmember-1", 1), ("guild-nonmember-2", 1), ("guild-home", 2)):
            with self.subTest(label=label):
                (ok, _), keys, _, _ = self.flow(failure=label)
                self.assertFalse(ok)
                self.assertEqual(len(keys), count)
        for kwargs, count in ((dict(changed=True), 1), (dict(home_changed=True), 2)):
            (ok, _), keys, _, _ = self.flow(**kwargs)
            self.assertFalse(ok)
            self.assertEqual(len(keys), count)

    def test_dry_run_and_bad_start_send_no_input(self):
        for dry_run, bad_start in ((True, False), (True, True), (False, True)):
            (ok, _), keys, labels, _ = self.flow(dry_run=dry_run, bad_start=bad_start)
            self.assertEqual(ok, not bad_start)
            self.assertEqual((keys, labels), ([], []))

    def test_real_daily_entry_order_recovery_and_partial_plan(self):
        operations = ("ensure_home", "run_business_management", "run_restaurant_decor_recheck",
                      "run_plaza_prayer")
        for fail in (None, *operations):
            calls = []
            roots = []

            def operation(name):
                def run(**kwargs):
                    calls.append(name)
                    roots.append(kwargs["log_root"].name)
                    return name != fail, name
                return run

            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                for name in operations:
                    stack.enter_context(patch.object(daily, name, operation(name)))
                root = Path(directory)
                if fail:
                    with self.assertRaises(daily.DailyRunError):
                        daily._execute_daily_phase("daily_claims", master=daily.MasterLogger(root), run_root=root)
                else:
                    reason = daily._execute_daily_phase("daily_claims", master=daily.MasterLogger(root), run_root=root)
                    self.assertFalse(reason.startswith("skipped:"))  # Other claims still completed.
                    self.assertEqual(roots[-1], "04-plaza-prayer")
            expected = list(operations)
            if fail == "ensure_home":
                expected = ["ensure_home"]
            elif fail and fail != operations[-1]:
                expected.insert(operations.index(fail) + 1, "ensure_home")
            self.assertEqual(calls, expected)
        for preset in ("fast", "detailed"):
            plan = get_daily_plan(preset)
            stage = next(s for s in plan if s.id == "daily_claims")
            self.assertEqual(stage.availability, "partial")
            self.assertFalse(stage.implemented)
            self.assertTrue(stage.current_runnable)
            self.assertFalse(any("guild" in runner for runner in stage.current_runners))
            self.assertEqual([s.id for s in plan][7:10], ["weekly", "daily_claims", "task_rewards"])


if __name__ == "__main__":
    unittest.main()
