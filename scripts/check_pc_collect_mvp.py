from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PC_CONTROLLERS = {"PC客户端"}
QUICK_CART_OPEN_NODES = (
    "Collect_PC_QuickCart_Open",
    "Collect_PC_QuickCart_Open_ForCard2",
    "Collect_PC_QuickCart_Open_ForCard3",
    "Collect_PC_QuickCart_Open_ForCard4",
)
UNAVAILABLE_NODES = (
    "Collect_PC_FieldSkill2_Unavailable_ToCard2",
    "Collect_PC_FieldSkill2_Unavailable_ToCard3",
    "Collect_PC_FieldSkill2_Unavailable_ToCard4",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def has_quick_cart_icon_template(node: dict) -> bool:
    for item in node.get("all_of", []):
        if isinstance(item, dict) and item.get("template") == ["PC_QuickCartIcon.png"]:
            return True
    return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    failures: list[str] = []

    interface = load_json(ROOT / "assets" / "interface.json")
    pc_controllers = [controller for controller in interface["controller"] if controller.get("name", "").startswith("PC客户端")]
    if [controller.get("name") for controller in pc_controllers] != ["PC客户端"]:
        failures.append("interface must expose only the no-move PC客户端 controller")
    elif pc_controllers[0].get("win32", {}).get("mouse") != "PostMessage" or pc_controllers[0].get("win32", {}).get("keyboard") != "PostMessage":
        failures.append("PC客户端 must use PostMessage to avoid moving the game window")

    collect_tasks = [
        task for task in interface["task"] if task.get("entry") == "Collect_StartGame_HomePage_OnlyOnce"
    ]
    if len(collect_tasks) != 1:
        failures.append(f"expected one Collect_StartGame_HomePage_OnlyOnce task, got {len(collect_tasks)}")
    else:
        controllers = set(collect_tasks[0].get("controller", []))
        missing = sorted(PC_CONTROLLERS - controllers)
        if missing:
            failures.append(f"Collect_StartGame_HomePage_OnlyOnce missing PC controllers: {missing}")
        unsupported = sorted(controller for controller in controllers if controller.startswith("PC客户端") and controller not in PC_CONTROLLERS)
        if unsupported:
            failures.append(f"Collect_StartGame_HomePage_OnlyOnce exposes unsupported PC controllers: {unsupported}")

    pc_collect_path = ROOT / "assets" / "resource" / "pc" / "pipeline" / "Collect_Launcher.json"
    if not pc_collect_path.exists():
        failures.append("missing PC Collect_Launcher.json override")
    else:
        pc_collect = load_json(pc_collect_path)
        for node in (
            "Collect_StartGame_HomePage_OnlyOnce",
            "Collect_PC_Sandplay_Stable",
            "Collect_PC_Field_Stable",
            "Collect_PC_Skill2_Depleted",
            "Collect_PC_Skill2_Available_Clr",
            "Collect_PC_Skill2_Unavailable_Clr",
            "Collect_PC_FieldSkill2_Ready_ToCard2",
            "Collect_PC_FieldSkill2_Ready_ToCard3",
            "Collect_PC_FieldSkill2_Ready_ToCard4",
            "Collect_PC_FieldSkill2_Ready_Final",
            "Collect_PC_FieldSkill2_Unavailable_ToCard2",
            "Collect_PC_FieldSkill2_Unavailable_ToCard3",
            "Collect_PC_FieldSkill2_Unavailable_ToCard4",
            "Collect_PC_FieldSkill2_Unavailable_Final",
            "Collect_PC_FieldSkill2_Limit_Stop",
            "Collect_PC_QuickCart_Open",
            "Collect_PC_QuickCart_Open_ForCard2",
            "Collect_PC_QuickCart_Open_ForCard3",
            "Collect_PC_QuickCart_Open_ForCard4",
            "Collect_PC_QuickCart_Menu",
            "Collect_PC_QuickCart_Menu_ForCard2",
            "Collect_PC_QuickCart_Menu_ForCard3",
            "Collect_PC_QuickCart_Menu_ForCard4",
            "Collect_PC_QuickCart_SelectStoryTab",
            "Collect_PC_QuickCart_SelectStoryTab_ForCard2",
            "Collect_PC_QuickCart_SelectStoryTab_ForCard3",
            "Collect_PC_QuickCart_SelectStoryTab_ForCard4",
            "Collect_PC_QuickCart_StoryCardAvailable",
            "Collect_PC_QuickCart_StoryCard2Available",
            "Collect_PC_QuickCart_StoryCard3Available",
            "Collect_PC_QuickCart_StoryCard4Available",
            "Collect_PC_QuickCart_NoMap_Stop",
            "Collect_PC_Sandplay_SafeStop",
            "Collect_PC_OpenGui_Failed",
        ):
            if node not in pc_collect:
                failures.append(f"PC Collect_Launcher.json missing node: {node}")

        start = pc_collect.get("Collect_StartGame_HomePage_OnlyOnce", {})
        if start.get("action") != "DoNothing":
            failures.append("PC collect entry must override the base PatchBatch action with DoNothing")
        expected_start_next = [
            "Collect_PC_Skill2_Depleted",
            "Collect_PC_FieldSkill2_Ready_ToCard2",
            "Collect_PC_FieldSkill2_Unavailable_ToCard2",
            "Collect_PC_QuickCart_StoryCardAvailable",
            "Collect_PC_QuickCart_SelectStoryTab",
            "Collect_PC_QuickCart_Menu",
            "Collect_PC_QuickCart_Open",
            "Collect_PC_Sandplay_SafeStop",
            "Collect_PC_OpenGui_Failed",
        ]
        if start.get("next") != expected_start_next:
            failures.append("PC collect entry must prefer depleted/field loop, then quick cart entry, then fail closed")

        stable = pc_collect.get("Collect_PC_Sandplay_Stable", {})
        all_of = stable.get("all_of", [])
        sub_names = {item.get("sub_name") for item in all_of if isinstance(item, dict)}
        if stable.get("recognition") != "And":
            failures.append("Collect_PC_Sandplay_Stable must use And recognition")
        if "Collect_PC_Sandplay_Chapter_Ocr" not in sub_names:
            failures.append("Collect_PC_Sandplay_Stable must include chapter OCR")
        if "Collect_PC_Sandplay_HomeIcon_Tpl" not in sub_names:
            failures.append("Collect_PC_Sandplay_Stable must include PC home icon template")

        field = pc_collect.get("Collect_PC_Field_Stable", {})
        if field.get("recognition") != "And":
            failures.append("Collect_PC_Field_Stable must use And recognition")

        skill2 = pc_collect.get("Collect_PC_FieldSkill2_Ready_ToCard2", {})
        if skill2.get("action") != "Click":
            failures.append("Collect_PC_FieldSkill2_Ready_ToCard2 must click the PC absorb skill button")
        if skill2.get("target") != [1015, 596]:
            failures.append("Collect_PC_FieldSkill2_Ready_ToCard2 target changed; update fixtures if intentional")
        if skill2.get("max_hit") != 30:
            failures.append("Collect_PC_FieldSkill2_Ready_ToCard2 must keep a finite safety cap")
        if skill2.get("next") != [
            "Collect_PC_Skill2_Depleted",
            "Collect_PC_FieldSkill2_Unavailable_ToCard2",
            "Collect_PC_FieldSkill2_Ready_ToCard2",
            "Collect_PC_FieldSkill2_Limit_Stop",
        ]:
            failures.append("Collect_PC_FieldSkill2_Ready_ToCard2 must loop until depleted, unavailable, or limit stop")
        if skill2.get("all_of") != ["Collect_PC_Field_Stable", "Collect_PC_Skill2_Available_Clr"]:
            failures.append("Collect_PC_FieldSkill2_Ready_ToCard2 must require a white/available skill2 button")

        unavailable = pc_collect.get("Collect_PC_FieldSkill2_Unavailable_ToCard2", {})
        if unavailable.get("action") != "StopTask":
            failures.append("Collect_PC_FieldSkill2_Unavailable_ToCard2 must StopTask until PC next-map return is verified")
        if unavailable.get("all_of") != ["Collect_PC_Field_Stable", "Collect_PC_Skill2_Unavailable_Clr"]:
            failures.append("Collect_PC_FieldSkill2_Unavailable_ToCard2 must require field stable and unavailable color")
        for node in UNAVAILABLE_NODES:
            next_nodes = pc_collect.get(node, {}).get("next", [])
            bad_edges = [target for target in next_nodes if target.startswith("Collect_PC_QuickCart_Open")]
            if bad_edges:
                failures.append(f"{node} must not jump directly to quick cart from a field page: {bad_edges}")

        depleted = pc_collect.get("Collect_PC_Skill2_Depleted", {})
        if depleted.get("action") != "StopTask":
            failures.append("Collect_PC_Skill2_Depleted must StopTask")
        if depleted.get("only_rec") is not True:
            failures.append("Collect_PC_Skill2_Depleted must be only_rec before StopTask")

        quick_open = pc_collect.get("Collect_PC_QuickCart_Open", {})
        if quick_open.get("action") != "Click":
            failures.append("Collect_PC_QuickCart_Open must click the PC quick cart icon")
        for node in QUICK_CART_OPEN_NODES:
            data = pc_collect.get(node, {})
            if data.get("recognition") != "And":
                failures.append(f"{node} must require PC sandbox stability and the quick cart icon")
            if "Collect_PC_Sandplay_Stable" not in data.get("all_of", []):
                failures.append(f"{node} must only open quick cart from a stable PC sandbox page")
            if not has_quick_cart_icon_template(data):
                failures.append(f"{node} must use the PC quick cart icon template")
            max_hit = data.get("max_hit")
            if not isinstance(max_hit, int) or max_hit <= 0 or max_hit > 6:
                failures.append(f"{node} must have a small finite max_hit to avoid endless click loops")

        story = pc_collect.get("Collect_PC_QuickCart_SelectStoryTab", {})
        if story.get("target") != [240, 584]:
            failures.append("Collect_PC_QuickCart_SelectStoryTab must click the PC story tab")

        card = pc_collect.get("Collect_PC_QuickCart_StoryCardAvailable", {})
        if card.get("target") != [96, 650]:
            failures.append("Collect_PC_QuickCart_StoryCardAvailable must click the first visible story card")

        safe_stop = pc_collect.get("Collect_PC_Sandplay_SafeStop", {})
        if safe_stop.get("action") != "StopTask":
            failures.append("Collect_PC_Sandplay_SafeStop must StopTask as the fail-closed boundary")
        if safe_stop.get("all_of") != ["Collect_PC_Sandplay_Stable"]:
            failures.append("Collect_PC_Sandplay_SafeStop must require Collect_PC_Sandplay_Stable")

        failed = pc_collect.get("Collect_PC_OpenGui_Failed", {})
        if failed.get("action") != "StopTask":
            failures.append("Collect_PC_OpenGui_Failed must StopTask")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
