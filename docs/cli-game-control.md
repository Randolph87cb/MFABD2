# CLI 本地游戏控制

用户已授权本项目的 CLI 开发线程使用完全访问，并直接调用项目 Python/Win32 游戏控制脚本。
这一路径不需要 ChatGPT/Codex 桌面端、Computer Use 插件、`cua_repl`、`node_repl` 或其原生控制授权。
插件的 `Native computer APIs are disabled` 不代表本地脚本不可用，不能只因该提示停止开发。
先执行下面的 `status`，以实际命令结果判断能力；真实 Windows 拒绝访问、游戏未启动、窗口歧义、
锁屏、截图空白、资源不足或未知页面仍须分别记录，不能虚报权限或 Demo 已通过。

## 入口

在项目根目录使用 `.venv/Scripts/python.exe`，不安装新的桌面控制依赖：

```powershell
.venv/Scripts/python.exe tools/game_control.py status
.venv/Scripts/python.exe tools/game_control.py open --timeout 90
.venv/Scripts/python.exe tools/game_control.py capture
```

每次命令输出 JSON，成功退出 0、失败退出 2，产物保存到 `logs/manual/cli-control/日期/时间/`。
`--log-root` 为全局选项，写在子命令前可指定本次证据目录。`status` 不启动游戏；
`open` 只启动或复用游戏，不登录、不跑日常；`--timeout` 限制本次启动的总等待时间，
超时后仅清理本轮新启动器。此时限不改变正式每日流程的网络无限等待行为。
`capture` 保存原窗口 `window.png`、
原生客户区 `client.png`、对应 `observation.json` 和 `result.json`。

## 观察后执行一个动作

先通过图像查看工具打开 `client.png`，确认页面与目标，再使用同次输出的 observation 路径。
下面的坐标只是格式示例，执行时必须取自实际截图，不能直接照抄。

```powershell
.venv/Scripts/python.exe tools/game_control.py click --observation '实际的/observation.json' --x 100 --y 200
.venv/Scripts/python.exe tools/game_control.py swipe --observation '实际的/observation.json' --x 500 --y 400 --end-x 500 --end-y 200 --duration 0.5
.venv/Scripts/python.exe tools/game_control.py key --observation '实际的/observation.json' --key ESC
.venv/Scripts/python.exe tools/game_control.py close --timeout 15
```

- 坐标是原生客户区像素，不是包含标题栏的窗口像素。不得对缩略显示的截图直接量坐标。
- 输入前核对唯一游戏窗口、进程启动时间、可执行文件、客户区尺寸、截图哈希和时效（5 分钟）。
  过期、窗口重建、进程重启、尺寸改变或图片改动都要重新截图。
- 每份 observation 最多执行一次输入，失败也不自动重放。动作输出 `action.png` 标注和 `after`
  中的新截图、新 observation；查看新截图后才能执行下一动作。输入已发送但后续截图失败时，
  查看日志里的 `input.json`，重新 `capture`，不得直接重试原动作。
- 点击、滑动沿用 `win32_windowpos_click.py`，会暂时移动并恢复游戏窗口。少数列表不响应消息
  滑动时，可对已观察到的同一游戏窗口使用 `swipe --foreground`；它会占用前台和真实鼠标。
- `key` 支持单个英文字母、数字、ESC、ENTER、SPACE、TAB；仅向游戏窗口发送键消息，
  不提供全桌面快捷键或任意文本输入。`close` 只请求关闭精确识别的游戏，不强杀任意进程。
- 脚本成功表示调用完成；点击、按键是否让游戏进入目标状态，必须用返回截图确认。

## 开发与测试边界

已有 `daily_automation.enter_game_logged` / `ensure_home` 可用于获授权的登录后导航 Demo，
但不得调用整套日常、`--force`、强制阶段，或修改/重置今天的每日状态来补截图。
登录渠道、认证弹窗需用户处理；消耗动作、资源上限和新模块接入仍遵守路线图。
本地通道消除了对桌面端插件的依赖，不代替业务识别和 PC 闭环验证。

监督器对新建与续接的 CLI 线程都显式使用 `--dangerously-bypass-approvals-and-sandbox`，
对应完全访问；不再使用会改为 workspace-write 的 `--approve-for-me`。
该设置仅作用于本项目监督器启动的线程，全局配置不需改动。修复线程仍禁止自行启动游戏，
每日增量线程仅可为目标子功能执行获授权的截图与最小 Demo。
