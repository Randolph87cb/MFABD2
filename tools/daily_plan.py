"""Pure data contract for the high-level daily automation plan.

This module intentionally imports no game automation modules.  Runner references
are descriptive strings so inspecting or serializing a plan cannot launch the
game, click the UI, access the network, or write external state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple


FAST_PRESET = "fast"
DETAILED_PRESET = "detailed"
DAILY_PRESETS = (FAST_PRESET, DETAILED_PRESET)

IMPLEMENTED = "implemented"
PARTIAL = "partial"
MISSING = "missing"


@dataclass(frozen=True)
class DailyStage:
    """One immutable stage in a plan for a selected preset."""

    id: str
    name: str
    availability: str
    implemented: bool
    unavailable_reason: str | None
    safety_gate: str
    current_runners: Tuple[str, ...]
    fast_enabled: bool
    detailed_enabled: bool
    enabled: bool

    @property
    def runnable(self) -> bool:
        """Whether the selected preset may execute this fully implemented stage."""

        return self.enabled and self.implemented

    @property
    def current_runnable(self) -> bool:
        """Whether the compatibility runner has a real local capability for this stage."""

        return self.enabled and bool(self.current_runners)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible stage data for a CLI or other presentation layer."""

        return {
            "id": self.id,
            "name": self.name,
            "availability": self.availability,
            "implemented": self.implemented,
            "unavailable_reason": self.unavailable_reason,
            "safety_gate": self.safety_gate,
            "current_runners": list(self.current_runners),
            "enabled_by_preset": {
                FAST_PRESET: self.fast_enabled,
                DETAILED_PRESET: self.detailed_enabled,
            },
            "enabled": self.enabled,
            "runnable": self.runnable,
            "current_runnable": self.current_runnable,
        }


@dataclass(frozen=True)
class _StageDefinition:
    id: str
    name: str
    availability: str
    unavailable_reason: str | None
    safety_gate: str
    current_runners: Tuple[str, ...] = ()
    fast_enabled: bool = True
    detailed_enabled: bool = True

    @property
    def implemented(self) -> bool:
        return self.availability == IMPLEMENTED

    def for_preset(self, preset: str) -> DailyStage:
        enabled = self.fast_enabled if preset == FAST_PRESET else self.detailed_enabled
        return DailyStage(
            id=self.id,
            name=self.name,
            availability=self.availability,
            implemented=self.implemented,
            unavailable_reason=self.unavailable_reason,
            safety_gate=self.safety_gate,
            current_runners=self.current_runners,
            fast_enabled=self.fast_enabled,
            detailed_enabled=self.detailed_enabled,
            enabled=enabled,
        )


_STAGES = (
    _StageDefinition(
        id="start",
        name="启动游戏",
        availability=IMPLEMENTED,
        unavailable_reason=None,
        safety_gate="必须通过单实例、每日一次状态领取和网络检查，并在继续前确认已返回主页。",
        current_runners=(
            "daily_automation.enter_game_logged",
            "daily_automation.ensure_home",
        ),
    ),
    _StageDefinition(
        id="quick_hunt",
        name="快速狩猎扫荡",
        availability=PARTIAL,
        unavailable_reason="现有流程只覆盖狩猎场和圣石洞穴，尚未覆盖冒险航线与米饭复核。",
        safety_gate="仅使用已解锁目标和每日免费次数；无红点时必须可安全跳过。",
        current_runners=(
            "quick_hunt.enter_quick_hunt",
            "quick_hunt.start_selected_quick_hunt",
            "quick_hunt.maximize_and_confirm_quick_hunt",
            "quick_hunt.run_crystal_cave_cycle",
        ),
    ),
    _StageDefinition(
        id="equipment_daily",
        name="装备制作与炼制日常",
        availability=MISSING,
        unavailable_reason="当前代码没有装备制作、强化、分解和精炼流程。",
        safety_gate="实现前必须限定低价装备和精炼白名单，禁止处理锁定或高价值装备。",
    ),
    _StageDefinition(
        id="free_gacha",
        name="免费抽卡",
        availability=IMPLEMENTED,
        unavailable_reason=None,
        safety_gate="仅允许角色与装备的免费抽取；无免费标记时不得点击付费抽取。",
        current_runners=("free_gacha.run_free_gacha",),
    ),
    _StageDefinition(
        id="chapter_collection",
        name="单章地图采集",
        availability=MISSING,
        unavailable_reason="当前代码没有单章地图采集流程。",
        safety_gate="只能进入已解锁且传送点可用的地图，达到日常采集目标后立即停止。",
    ),
    _StageDefinition(
        id="arena",
        name="竞技场",
        availability=IMPLEMENTED,
        unavailable_reason=None,
        safety_gate="仅消耗可识别的免费竞技场次数，并必须验证战斗结算或可安全退出状态。",
        current_runners=("daily_arena.run_daily_arena",),
    ),
    _StageDefinition(
        id="season_activity",
        name="赛季活动快速战斗",
        availability=MISSING,
        unavailable_reason="当前代码没有赛季活动识别与战斗流程。",
        safety_gate="仅能执行已解锁的免费战斗，禁止购买体力、刷新次数或自动消耗付费货币。",
    ),
    _StageDefinition(
        id="weekly",
        name="周常任务",
        availability=MISSING,
        unavailable_reason="当前代码没有街机、小屋点赞、末日之书和周常钓鱼流程。",
        safety_gate="必须按游戏周期幂等记录，同一周期不得重复消耗资源。",
    ),
    _StageDefinition(
        id="daily_claims",
        name="日常任务领取",
        availability=PARTIAL,
        unavailable_reason=(
            "现有流程仅覆盖经营管理收益和餐厅常客奖励，"
            "未完整覆盖公会签到、餐馆日常和广场祈求。"
        ),
        safety_gate="只有所有日常领取子项均可验证完成或安全跳过后，才能标记为已实现。",
        current_runners=("business_management.run_business_management",),
    ),
    _StageDefinition(
        id="task_rewards",
        name="任务页奖励（每日/每周）",
        availability=IMPLEMENTED,
        unavailable_reason=None,
        safety_gate="仅点击可领取的每日与每周奖励，无可领奖励时安全返回主页。",
        current_runners=("task_rewards.run_task_rewards",),
    ),
    _StageDefinition(
        id="activity_rewards",
        name="活动页奖励",
        availability=IMPLEMENTED,
        unavailable_reason=None,
        safety_gate="只处理可验证的领奖入口和结算弹窗，无红点时允许跳过。",
        current_runners=("activity_rewards.run_activity_rewards",),
    ),
    _StageDefinition(
        id="pass_rewards",
        name="通行证奖励",
        availability=IMPLEMENTED,
        unavailable_reason=None,
        safety_gate="只领取已解锁的免费或已拥有通行证奖励，不得购买通行证。",
        current_runners=("pass_rewards.run_pass_rewards",),
    ),
    _StageDefinition(
        id="mail_rewards",
        name="邮件奖励",
        availability=IMPLEMENTED,
        unavailable_reason=None,
        safety_gate="仅执行普通邮件和商品邮件的全部领取，无红点时允许跳过。",
        current_runners=("mail_rewards.run_mail_rewards",),
    ),
    _StageDefinition(
        id="roguelike",
        name="肉鸽塔每日快速战斗",
        availability=MISSING,
        unavailable_reason="当前代码没有肉鸽塔识别与战斗流程。",
        safety_gate="仅使用每日免费 AP，不得为补充次数消耗付费货币。",
        fast_enabled=False,
    ),
    _StageDefinition(
        id="arbitrage",
        name="商店套利",
        availability=MISSING,
        unavailable_reason="当前代码没有商店价格识别、进货、售出或料理制作流程。",
        safety_gate="涉及买卖的高风险阶段必须显式开启，并设置商品白名单、价格阈值与最大花费。",
    ),
    _StageDefinition(
        id="full_map_collection",
        name="完整地图采集",
        availability=MISSING,
        unavailable_reason="当前代码没有跨卡带的完整地图采集流程。",
        safety_gate="必须限定为已解锁传送点的地图，并保存每周幂等进度以防重复扫图。",
    ),
    _StageDefinition(
        id="close_game",
        name="结束游戏",
        availability=MISSING,
        unavailable_reason="当前每日主流程完成后不会关闭游戏。",
        safety_gate="只能关闭已确认的 BrownDust II 窗口或进程，禁止按模糊名称终止其他进程。",
    ),
)


DAILY_STAGE_IDS = tuple(stage.id for stage in _STAGES)


def _validate_preset(preset: str) -> None:
    if preset not in DAILY_PRESETS:
        choices = ", ".join(DAILY_PRESETS)
        raise ValueError(f"unknown daily preset {preset!r}; expected one of: {choices}")


def get_daily_plan(preset: str) -> Tuple[DailyStage, ...]:
    """Return all 17 stages in fixed order for ``preset``."""

    _validate_preset(preset)
    return tuple(stage.for_preset(preset) for stage in _STAGES)


def get_runnable_stages(preset: str) -> Tuple[DailyStage, ...]:
    """Return enabled stages that have a complete local implementation."""

    return tuple(stage for stage in get_daily_plan(preset) if stage.runnable)


def get_current_stages(preset: str) -> Tuple[DailyStage, ...]:
    """Return enabled stages backed by any existing local runner, including partial ones."""

    return tuple(stage for stage in get_daily_plan(preset) if stage.current_runnable)


def daily_plan_to_dict(preset: str, *, runnable_only: bool = False) -> dict[str, Any]:
    """Serialize a selected plan into a stable JSON-compatible dictionary."""

    full_plan = get_daily_plan(preset)
    returned_plan = (
        tuple(stage for stage in full_plan if stage.runnable)
        if runnable_only
        else full_plan
    )
    return {
        "preset": preset,
        "runnable_only": runnable_only,
        "contract_stage_count": len(full_plan),
        "enabled_stage_count": sum(stage.enabled for stage in full_plan),
        "runnable_stage_count": sum(stage.runnable for stage in full_plan),
        "current_runnable_stage_count": sum(stage.current_runnable for stage in full_plan),
        "returned_stage_count": len(returned_plan),
        "stages": [stage.to_dict() for stage in returned_plan],
    }
