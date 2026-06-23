# PC 流程复刻指南

本文记录 MFABD2 适配《棕色尘埃2》PC 客户端时已经验证过的复刻流程。目标是让后续任务复刻有固定入口、固定检查项和固定验证方式，而不是每次重新摸索。

## 适用范围

适用于把已有 Android / 模拟器流程逐步迁移到 PC 客户端的任务，例如快速狩猎、地图采集、邮件领取等。

当前推荐策略是先做够用 MVP，再扩展完整等价流程。不要一开始追求 Android 全流程等价。

## 基本原则

- 优先在 `assets/resource/pc/pipeline/*.json` 写 PC 覆盖，不直接改 `assets/resource/base`。
- 每次只复刻一个最短可闭环链路。
- 未复刻完整前，未知页面、AP 不足、资源耗尽、识别失败都应优先 `StopTask`，避免误走 Android 坐标。
- 识别尽量使用组合条件：页面稳定特征、关键文字 OCR、图标或按钮模板。
- 每个确认过的误判、错误点击或错误状态都要加入 `tests/fixtures` 易错库，并增加可重复运行的检查脚本。
- 真实窗口 harness 验证必须能给出节点序列、识别详情和截图。
- 每个小步通过验证后单独提交，避免把多个流程混在同一次提交里。

## 推荐执行流程

1. 从 `assets/interface.json` 选择下一个任务入口。
2. 读取对应 base pipeline，找出最短可闭环路径。
3. 判断该路径能否在 PC 上复用；不能复用时只做 PC 覆盖节点。
4. 截取当前真实 PC 窗口，保存正例、负例、失败例。
5. 在 `assets/resource/pc/pipeline` 增加 PC 专用节点。
6. 在 `tests/fixtures/<pc_task>` 增加 manifest 和截图样本。
7. 增加结构检查脚本，固定入口、节点顺序、安全停止点和关键坐标。
8. 增加 corpus 检查脚本，验证截图样本不会回归。
9. 用 harness 跑真实窗口，确认节点序列符合预期。
10. 用启动脚本刷新 `install` 并确认 UI 加载本地配置。
11. 通过后提交并推送。

## 常用文件

| 目的 | 路径 |
| --- | --- |
| 任务入口和控制器 | `assets/interface.json` |
| Android/base 流程 | `assets/resource/base/pipeline/*.json` |
| PC 覆盖流程 | `assets/resource/pc/pipeline/*.json` |
| PC 图像模板 | `assets/resource/pc/image` |
| 易错库截图 | `tests/fixtures/<pc_task>` |
| 结构检查脚本 | `scripts/check_pc_<task>_mvp.py` |
| 截图语料检查脚本 | `scripts/check_pc_<task>_corpus.py` |
| 真实窗口 harness | `scripts/pc_quickhunt_harness.py` |
| 本地 UI 启动脚本 | `scripts/start-local-ui.ps1` |
| 项目协作规则 | `AGENTS.md` |

## Harness 结构

每个 PC 流程复刻都应先写清 harness 结构：

- 目标：这个 harness 最终要稳定完成什么。
- 范围：什么页面或状态可以用，什么状态必须停止。
- 输入：当前游戏窗口、截图样本、base/pc resource、任务入口。
- 流程：识别入口、执行动作、等待、识别下一节点、停止或进入下一段。
- 分工：主线程实现和真实验证；subagent 只读审计差距和风险；脚本负责可重复检查。
- 检查点：每一步如何判断可以继续。
- 产出：pipeline 改动、fixture、检查脚本、harness 日志、提交。
- 失败处理：失败截图入库，新增检查，再修复。
- Git 策略：每个通过验证的小步独立提交并推送。

## 验证命令模板

结构检查：

```powershell
.\.venv\Scripts\python.exe .\scripts\check_pc_<task>_mvp.py
```

截图语料检查：

```powershell
.\.venv\Scripts\python.exe .\scripts\check_pc_<task>_corpus.py
```

语法检查：

```powershell
.\.venv\Scripts\python.exe -m py_compile .\scripts\check_pc_<task>_mvp.py .\scripts\check_pc_<task>_corpus.py
```

真实窗口 harness：

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe .\scripts\pc_quickhunt_harness.py --run-task --entry <EntryName> --timeout 30
```

本地 UI 配置刷新和校验：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local-ui.ps1 -NoLaunch -DebugLogs -PcInput CursorPos
```

Git 空白检查：

```powershell
git diff --check
```

## 已验证示例

### 快速狩猎

已完成够用 MVP：

```text
主页快速狩猎入口
-> PC 地图页快速狩猎按钮
-> MAX
-> 开始
-> 奖励页领取
-> 回地图
-> 复位到 1.野猪洞穴
-> StopTask
```

关键经验：

- AP 为 `0/60` 时必须先命中无 AP 保护，再考虑快速狩猎按钮。
- 奖励后先复位默认地图点，再停止，方便下次启动状态稳定。
- 主页识别不要只靠颜色，使用 OCR + 右上 Home 图标组合。

相关文件：

- `assets/resource/pc/pipeline/Battle.json`
- `assets/resource/pc/pipeline/Global.json`
- `scripts/check_pc_quickhunt_mvp.py`
- `scripts/check_pc_quickhunt_corpus.py`
- `scripts/check_pc_home_recognition_corpus.py`
- `tests/fixtures/pc_quickhunt`
- `tests/fixtures/pc_home`

### 地图采集单章

当前已完成 PC 安全骨架：

```text
Collect_StartGame_HomePage_OnlyOnce
-> 识别 PC 箱庭
-> StopTask
```

这一步的目标不是完成采集技能，而是先防止 PC 入口误跑 Android 采集坐标。

关键经验：

- base 的 `Collect_StartGame_HomePage_OnlyOnce` 带有 `PatchBatch` action；PC 覆盖必须显式写 `action: "DoNothing"`，否则 base action 会残留。
- 当前 PC 箱庭识别使用左上 `Ch` OCR + 右上 Home 图标模板。
- 采集技能按钮在 PC 上与 Android 坐标不同，必须单独复刻。

相关文件：

- `assets/resource/pc/pipeline/Collect_Launcher.json`
- `scripts/check_pc_collect_mvp.py`
- `scripts/check_pc_collect_corpus.py`
- `tests/fixtures/pc_collect`

## 易错库规则

出现以下情况时必须补充 fixture：

- 页面被错误识别为目标页面。
- 目标页面漏识别。
- 按钮和停止条件同时命中时顺序错误。
- 点击坐标看似成功但实际没有改变页面。
- base 节点的 action、next 或 on_error 残留影响 PC 覆盖。
- 真实窗口 harness 与离线 corpus 结果不一致。

每个 fixture 至少记录：

- 截图文件。
- 期望命中的节点。
- 期望不命中的节点。
- 这张截图曾经暴露的问题。

## Subagent 使用方式

Subagent 适合做只读审计：

- 对比 base 与 pc pipeline 差异。
- 找出未覆盖节点。
- 指出下一步最小可验证链路。
- 检查风险和需要补充的 fixture。

不要让 subagent 与主线程同时修改同一批文件。主线程负责实现、验证、提交和推送。

## Skill 化判断

当前这套流程已经适合沉淀为项目公共文档，并进入 skill backlog。

暂不建议直接做全局 skill，原因是它强依赖 MFABD2 的目录结构、MAA pipeline、PC 客户端截图和本项目 harness。继续复刻 2 到 3 个流程后，如果触发条件、文件模式、检查脚本模板和失败处理都稳定，可以考虑做项目 skill。

建议的项目 skill 触发语句：

- “继续下一个 PC 流程复刻”
- “用 harness 验证这个 PC 流程”
- “把错误例子加入易错库”
- “对比 base 和 pc pipeline 的差距”

