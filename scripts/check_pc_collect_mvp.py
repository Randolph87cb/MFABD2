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

    pc_collect_path = ROOT / "assets" / "resource" / "pc" / "pipeline" / "Collect_Launcher.json"
    if not pc_collect_path.exists():
        failures.append("missing PC Collect_Launcher.json override")
    else:
        pc_collect = load_json(pc_collect_path)
        for node in (
            "Collect_StartGame_HomePage_OnlyOnce",
            "Collect_PC_Sandplay_Stable",
            "Collect_PC_Skill1_Ready",
            "Collect_PC_CollectOnce_Done",
            "Collect_PC_Sandplay_SafeStop",
            "Collect_PC_OpenGui_Failed",
        ):
            if node not in pc_collect:
                failures.append(f"PC Collect_Launcher.json missing node: {node}")

        start = pc_collect.get("Collect_StartGame_HomePage_OnlyOnce", {})
        if start.get("action") != "DoNothing":
            failures.append("PC collect entry must override the base PatchBatch action with DoNothing")
        if start.get("next") != ["Collect_PC_Skill1_Ready", "Collect_PC_Sandplay_SafeStop", "Collect_PC_OpenGui_Failed"]:
            failures.append("PC collect entry must try skill1 once, then safe-stop path, then fail closed")

        stable = pc_collect.get("Collect_PC_Sandplay_Stable", {})
        all_of = stable.get("all_of", [])
        sub_names = {item.get("sub_name") for item in all_of if isinstance(item, dict)}
        if stable.get("recognition") != "And":
            failures.append("Collect_PC_Sandplay_Stable must use And recognition")
        if "Collect_PC_Sandplay_Chapter_Ocr" not in sub_names:
            failures.append("Collect_PC_Sandplay_Stable must include chapter OCR")
        if "Collect_PC_Sandplay_HomeIcon_Tpl" not in sub_names:
            failures.append("Collect_PC_Sandplay_Stable must include PC home icon template")

        skill1 = pc_collect.get("Collect_PC_Skill1_Ready", {})
        if skill1.get("action") != "Click":
            failures.append("Collect_PC_Skill1_Ready must click the PC skill button")
        if skill1.get("target") != [1114, 674]:
            failures.append("Collect_PC_Skill1_Ready target changed; update fixtures if intentional")
        if skill1.get("next") != ["Collect_PC_CollectOnce_Done"]:
            failures.append("Collect_PC_Skill1_Ready must stop through Collect_PC_CollectOnce_Done")

        done = pc_collect.get("Collect_PC_CollectOnce_Done", {})
        if done.get("action") != "StopTask":
            failures.append("Collect_PC_CollectOnce_Done must StopTask")

        safe_stop = pc_collect.get("Collect_PC_Sandplay_SafeStop", {})
        if safe_stop.get("action") != "StopTask":
            failures.append("Collect_PC_Sandplay_SafeStop must StopTask until collection skills are ported")
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
