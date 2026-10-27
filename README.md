# BrownDust II PC 自动化实验

这是一个面向 BrownDust II PC 客户端的最小自动化实验仓库，目前只保留已验证的窗口识别、后台截图、后台点击、启动游戏和进入卡带内场景相关代码。

## 环境

- Windows
- Python 3.10+
- BrownDust II PC 客户端

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

## 每日定时任务

在 Windows PowerShell 中安装或更新计划任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_daily_task.ps1
```

安装脚本会创建项目内 `.venv`、安装 `requirements.txt`，并注册每天 `08:30` 运行的
`BrownDust2DailyAutomation`。网络检查仍会无限等待网络恢复，不设置任务执行时限。

Codex CLI 在每次运行时重新读取系统和用户保存的 PATH，按 PATH 顺序选择默认 CLI，
避免计划任务继承旧环境后继续调用旧版本。更新命令行 CLI 或调整 PATH 后，下次运行即可生效。
默认计划任务不保存具体 CLI 路径；已有任务需重新运行安装脚本以移除旧的固定路径：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_daily_task.ps1 -SkipDependencyInstall
```

如需固定某个 CLI，可以显式指定（此时后续运行不再自动选择）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_daily_task.ps1 -SkipDependencyInstall -CodexPath "$env:APPDATA\npm\codex.cmd"
```

安装和每次运行时都会检查 CLI 能否启动，并输出实际路径和版本；模型沿用本机 Codex 配置。
监督器为新建和续接的修复／开发线程显式启用完全访问，不再使用 `--approve-for-me`。
游戏操作通过项目本地 Python/Win32 入口完成，无需桌面端控制插件；使用方式与证据规则见
[`docs/cli-game-control.md`](docs/cli-game-control.md)。

每轮运行由 `tools\daily_supervisor.py` 监督：

- 登录时检测维护公告，关闭游戏和本轮启动器，等待公告结束时间后一小时再登录并续跑；
  例如公告为 `07:50～11:30`，则在 `12:30` 重试。结束时间无法识别或已过期时，关闭后每小时重试；
  维护等待不占用自动修复次数，等待期间计划任务保持运行，避免并发启动；
- 正常流程结束后关闭精确识别到的游戏进程，以及本轮新建的官方启动器进程；
- 全部可运行阶段完成并成功收尾后，发送标题为“棕尘2每日任务”的 ntfy 通知。
  独立订阅配置保存在本机 `state/ntfy.config.json`，包含 `ntfyUrl`（服务器地址）
  和 `ntfyTopic`（每日任务专用 topic），该文件不提交到 Git。
  发送失败会记录在监督日志中，
  不改变每日任务完成结果，也不触发游戏重跑；
- 不启动识别标注网站；
- 仅保留 `logs\daily`、`daily-check`、`supervisor`、`recovery` 中最近 7 天的日期日志；
- 当前可运行阶段没有全部完成时，在当前目录创建 Codex 线程，读取日志并参考
  `.external\MFABD2-reference`；修复后使用正常状态机从失败阶段继续到最后阶段，最多修复两轮；
- 自动修复前若主仓库已有未提交改动，监督器会停止自动修改，避免覆盖人工工作。
- 每日流程正常完成（包括修复后成功续跑）并成功收尾后，先发送完成通知，再新建一个独立
  Codex 开发线程，对比 `.external\MFABD2-reference`，推进一个缺失模块或部分模块的子功能。
  按 `docs/daily-automation-roadmap.md` 的差距顺序选择，缺少证据或前置条件时评估下一项；
  开发提示词指定使用已实测的本地 CLI 通道补 PC 证据，初始化白屏等待后只读复采，
  不沿用旧线程的桌面插件权限结论停止开发；控制通道通过仍不能代替目标模块验收。
  装备、交易等仍受已有白名单限制。通过必要测试及 PC 闭环验证后，接入真实每日入口，
  提交并推送，让下一游戏日的 `08:30` 计划任务加载新代码；不会因此重跑当天整套日常。
- 增量开发记录保存在本机 `state/daily_improvement.json`，按每天 `08:00` 的游戏日计数。
  同一游戏日最多启动一次，失败或中断也不重复启动；手动 `--force-phase` 不触发开发。
  主仓库有未提交改动或参考仓库不可用时暂停开发，不影响当天日常成功结果。
  开发线程结束只代表该轮结束，模块是否可启用与次日实测结果分别记录在路线图和每日日志；
  证据不足的功能保持关闭，开发失败也不会触发当天日常重跑。

只检查监督器、Codex 和本地参考仓库，不启动游戏：

```powershell
$Task = Get-ScheduledTask -TaskName BrownDust2DailyAutomation
$Check = Start-Process -FilePath $Task.Actions[0].Execute -ArgumentList ($Task.Actions[0].Arguments + " -Check") -NoNewWindow -Wait -PassThru
$Check.ExitCode
```

### 完成通知配置与订阅

新机器需要先创建 `state/ntfy.config.json`，填入自己的服务器地址和每日任务专用 topic：

```json
{
  "ntfyUrl": "https://ntfy.example.com",
  "ntfyTopic": "browndust2-daily-example"
}
```

上面的值仅为示例，应替换为实际配置。该配置独立于 Codex 线程通知，保留在本机。

在手机 ntfy 首页点“＋”添加订阅，选择自定义服务器：服务器填写 `ntfyUrl`，
仅包含服务器地址；主题（topic）单独填写 `ntfyTopic`。显示名称可设为“棕尘2每日任务”。
保存订阅后，每日流程正常完成且游戏收尾成功时会发送通知。

## 已实现

打开游戏：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\open_game.ps1
```

该入口仅打开游戏，不执行日常；也可直接调用现有 Python 入口：

```powershell
python tools\open_game.py --timeout 90
```

验证后台截图：

```powershell
python tools\probe_printwindow.py
```

后台点击客户区坐标：

```powershell
python tools\win32_windowpos_click.py --x 950 --y 505
```

从标题页点击 `TOUCH TO START`，并确认进入上一次保存的卡带内 Field/地图场景：

```powershell
python tools\enter_game.py --timeout 90
```

静音游戏：

```powershell
python tools\mute_browndust.py
```

取消静音：

```powershell
python tools\mute_browndust.py --unmute
```

## 文档

- `docs/open-game.md`：打开游戏的成功流程和失败点。
- `docs/enter-game.md`：识别标题页、点击进入、识别 `pack_field` 的记录。
- `docs/game-context.md`：BrownDust II 概念与自动化词表。

## 当前限制

- `win32_windowpos_click.py` 会短暂移动游戏窗口到鼠标下方再恢复，这是从 MaaFramework `PostMessageWithWindowPos` 思路抽出的最小可用单元。
- 目前还没有实现真正的主页、Pack Collection、日常任务路径识别。
