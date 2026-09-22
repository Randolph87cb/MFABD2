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

每轮运行由 `tools\daily_supervisor.py` 监督：

- 正常流程结束后关闭精确识别到的游戏进程，以及本轮新建的官方启动器进程；
- 不启动识别标注网站；
- 仅保留 `logs\daily`、`daily-check`、`supervisor`、`recovery` 中最近 7 天的日期日志；
- 当前可运行阶段没有全部完成时，在当前目录创建 Codex 线程，读取日志并参考
  `.external\MFABD2-reference`；修复后使用正常状态机从失败阶段继续到最后阶段，最多修复两轮；
- 自动修复前若主仓库已有未提交改动，监督器会停止自动修改，避免覆盖人工工作。

只检查监督器、Codex 和本地参考仓库，不启动游戏：

```powershell
$CodexPath = (Get-Command codex.cmd).Source
python tools\daily_supervisor.py --check --codex-path $CodexPath
```

## 已实现

打开游戏：

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
