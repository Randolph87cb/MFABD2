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
        for node in ("QuickHunt_Start", "QuickHunt_OpenGui", "QuickHunt_PC_OpenGui_Failed"):
            if node not in pc_battle:
                failures.append(f"PC Battle.json missing node: {node}")

        open_gui = pc_battle.get("QuickHunt_OpenGui", {})
        if "QuickHunt_PC_OpenGui_Failed" not in open_gui.get("next", []):
            failures.append("QuickHunt_OpenGui must fail closed through QuickHunt_PC_OpenGui_Failed")

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
