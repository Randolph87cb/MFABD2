from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PC_CONTROLLERS = {"PC客户端", "PC客户端(CursorPos)"}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    failures: list[str] = []

    interface = load_json(ROOT / "assets" / "interface.json")
    quickhunt_tasks = [
        task for task in interface["task"] if task.get("entry") == "QuickHunt_Start"
    ]
    if len(quickhunt_tasks) != 1:
        failures.append(f"expected one QuickHunt_Start task, got {len(quickhunt_tasks)}")
    else:
        controllers = set(quickhunt_tasks[0].get("controller", []))
        missing = sorted(PC_CONTROLLERS - controllers)
        if missing:
            failures.append(f"QuickHunt_Start missing PC controllers: {missing}")

    pc_battle_path = ROOT / "assets" / "resource" / "pc" / "pipeline" / "Battle.json"
    if not pc_battle_path.exists():
        failures.append("missing PC Battle.json override")
    else:
        pc_battle = load_json(pc_battle_path)
        for node in (
            "QuickHunt_Start",
            "QuickHunt_OpenGui",
            "QuickHunt_PC_HomeShortcut",
            "QuickHunt_PC_MapQuickBattleButton",
            "QuickHunt_FastBattleReward",
            "QuickHunt_NoFreeAP",
            "QuickHunt_PC_QuickHunt_Done",
            "QuickHunt_PC_OpenGui_Failed",
        ):
            if node not in pc_battle:
                failures.append(f"PC Battle.json missing node: {node}")

        open_gui = pc_battle.get("QuickHunt_OpenGui", {})
        open_gui_next = open_gui.get("next", [])
        if not open_gui_next or open_gui_next[0] != "QuickHunt_PC_HomeShortcut":
            failures.append("QuickHunt_OpenGui must try QuickHunt_PC_HomeShortcut first")
        if "QuickHunt_PC_OpenGui_Failed" not in open_gui.get("next", []):
            failures.append("QuickHunt_OpenGui must fail closed through QuickHunt_PC_OpenGui_Failed")

        shortcut = pc_battle.get("QuickHunt_PC_HomeShortcut", {})
        if "Rec_HomePage_PC_Stable" not in shortcut.get("all_of", []):
            failures.append("QuickHunt_PC_HomeShortcut must include Rec_HomePage_PC_Stable")
        if shortcut.get("target") != [1170, 174]:
            failures.append("QuickHunt_PC_HomeShortcut target changed; update tests/fixtures if intentional")
        if "QuickHunt_PC_MapQuickBattleButton" not in shortcut.get("next", []):
            failures.append("QuickHunt_PC_HomeShortcut must continue to QuickHunt_PC_MapQuickBattleButton")

        map_button = pc_battle.get("QuickHunt_PC_MapQuickBattleButton", {})
        if map_button.get("target") != [1096, 662]:
            failures.append("QuickHunt_PC_MapQuickBattleButton target changed; update tests/fixtures if intentional")

        reward = pc_battle.get("QuickHunt_FastBattleReward", {})
        if reward.get("next") != ["QuickHunt_PC_QuickHunt_Done"]:
            failures.append("PC QuickHunt_FastBattleReward must stop after reward via QuickHunt_PC_QuickHunt_Done")

        no_free_ap = pc_battle.get("QuickHunt_NoFreeAP", {})
        if no_free_ap.get("next") != ["QuickHunt_PC_QuickHunt_Done"]:
            failures.append("PC QuickHunt_NoFreeAP must stop instead of entering Android map reset")

    pc_global = load_json(ROOT / "assets" / "resource" / "pc" / "pipeline" / "Global.json")
    stable = pc_global.get("Rec_HomePage_PC_Stable")
    if not stable:
        failures.append("missing Rec_HomePage_PC_Stable")
    elif stable.get("all_of") != ["Rec_HomePage_GA_Ocr", "Rec_HomePage_PC_HomeIcon_Tpl"]:
        failures.append("Rec_HomePage_PC_Stable must keep OCR + PC Home icon composition")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
