from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from daily_plan import (  # noqa: E402
    DETAILED_PRESET,
    FAST_PRESET,
    PARTIAL,
    DailyStage,
    daily_plan_to_dict,
    get_daily_plan,
    get_current_stages,
    get_runnable_stages,
)


EXPECTED_STAGE_IDS = (
    "start",
    "quick_hunt",
    "equipment_daily",
    "free_gacha",
    "chapter_collection",
    "arena",
    "season_activity",
    "weekly",
    "daily_claims",
    "task_rewards",
    "activity_rewards",
    "pass_rewards",
    "mail_rewards",
    "roguelike",
    "arbitrage",
    "full_map_collection",
    "close_game",
)


class DailyPlanTests(unittest.TestCase):
    def test_plan_has_the_fixed_unique_17_stage_order(self) -> None:
        plan = get_daily_plan(FAST_PRESET)
        ids = tuple(stage.id for stage in plan)

        self.assertEqual(ids, EXPECTED_STAGE_IDS)
        self.assertEqual(len(ids), 17)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(stage.name for stage in plan))
        self.assertTrue(all(stage.safety_gate for stage in plan))

    def test_fast_and_detailed_presets_match_the_confirmed_switches(self) -> None:
        fast = get_daily_plan(FAST_PRESET)
        detailed = get_daily_plan(DETAILED_PRESET)

        self.assertEqual(tuple(stage.id for stage in fast), EXPECTED_STAGE_IDS)
        self.assertEqual(tuple(stage.id for stage in detailed), EXPECTED_STAGE_IDS)
        self.assertEqual(
            [stage.id for stage in fast if not stage.enabled],
            ["roguelike"],
        )
        self.assertTrue(all(stage.enabled for stage in detailed))
        for fast_stage, detailed_stage in zip(fast, detailed):
            self.assertEqual(fast_stage.fast_enabled, fast_stage.enabled)
            self.assertEqual(detailed_stage.detailed_enabled, detailed_stage.enabled)

    def test_availability_does_not_promote_partial_or_missing_stages(self) -> None:
        plan = {stage.id: stage for stage in get_daily_plan(DETAILED_PRESET)}
        implemented = {
            stage.id for stage in plan.values() if stage.implemented
        }

        self.assertEqual(
            implemented,
            {
                "start",
                "free_gacha",
                "arena",
                "task_rewards",
                "activity_rewards",
                "pass_rewards",
                "mail_rewards",
            },
        )
        daily_claims = plan["daily_claims"]
        self.assertEqual(daily_claims.availability, PARTIAL)
        self.assertFalse(daily_claims.implemented)
        self.assertIn(
            "business_management.run_business_management",
            daily_claims.current_runners,
        )
        self.assertIsNotNone(daily_claims.unavailable_reason)
        quick_hunt = plan["quick_hunt"]
        self.assertEqual(quick_hunt.availability, PARTIAL)
        self.assertIn("冒险航线", quick_hunt.unavailable_reason or "")

    def test_runnable_plan_filters_by_preset_and_complete_implementation(self) -> None:
        fast = get_runnable_stages(FAST_PRESET)
        detailed = get_runnable_stages(DETAILED_PRESET)

        expected = (
            "start",
            "free_gacha",
            "arena",
            "task_rewards",
            "activity_rewards",
            "pass_rewards",
            "mail_rewards",
        )
        self.assertEqual(tuple(stage.id for stage in fast), expected)
        self.assertEqual(tuple(stage.id for stage in detailed), expected)
        self.assertTrue(all(stage.runnable for stage in detailed))

        self.assertEqual(
            tuple(stage.id for stage in get_current_stages(FAST_PRESET)),
            (
                "start",
                "quick_hunt",
                "free_gacha",
                "arena",
                "daily_claims",
                "task_rewards",
                "activity_rewards",
                "pass_rewards",
                "mail_rewards",
            ),
        )

    def test_serialization_is_structured_and_json_compatible(self) -> None:
        payload = daily_plan_to_dict(FAST_PRESET)

        self.assertEqual(payload["preset"], FAST_PRESET)
        self.assertEqual(payload["contract_stage_count"], 17)
        self.assertEqual(payload["enabled_stage_count"], 16)
        self.assertEqual(payload["runnable_stage_count"], 7)
        self.assertEqual(payload["current_runnable_stage_count"], 9)
        self.assertEqual(payload["returned_stage_count"], 17)
        self.assertEqual(payload["stages"][0]["id"], "start")
        self.assertEqual(
            payload["stages"][13]["enabled_by_preset"],
            {FAST_PRESET: False, DETAILED_PRESET: True},
        )
        json.dumps(payload, ensure_ascii=False)

        runnable = daily_plan_to_dict(DETAILED_PRESET, runnable_only=True)
        self.assertEqual(runnable["contract_stage_count"], 17)
        self.assertEqual(runnable["returned_stage_count"], 7)
        self.assertTrue(all(stage["runnable"] for stage in runnable["stages"]))

    def test_contract_is_immutable_and_rejects_unknown_presets(self) -> None:
        stage = get_daily_plan(FAST_PRESET)[0]
        self.assertIsInstance(stage, DailyStage)
        with self.assertRaises(FrozenInstanceError):
            stage.enabled = False  # type: ignore[misc]
        with self.assertRaises(ValueError):
            get_daily_plan("unknown")


if __name__ == "__main__":
    unittest.main()
