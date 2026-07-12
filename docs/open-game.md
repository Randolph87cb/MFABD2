# 打开游戏记录

## 成功标准

- 能启动 `BrownDust II.exe` 进程。
- 能找到主窗口标题 `BrownDust II`。
- 主窗口类名为 `UnityWndClass`。
- 能通过 `tools/probe_printwindow.py` 抓到游戏画面。

## 成功流程

使用 Neowiz 启动器并带协议参数启动：

```powershell
Start-Process -FilePath 'C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe' -ArgumentList 'browndust2:games/10000001?usn=0' -WorkingDirectory 'C:\ProgramData\Neowiz\Browndust2Starter'
```

本次验证结果：

- 启动后出现游戏进程：`BrownDust II.exe`
- 进程路径：`D:\Neowiz\Browndust2\Browndust2_10000001\BrownDust II.exe`
- 窗口标题：`BrownDust II`
- 窗口类名：`UnityWndClass`
- 本次窗口句柄：`0x911E8`
- 客户端画面截图验证：`python tools\probe_printwindow.py` 返回 `printwindow_ok=True`

## 失败点

### 直接启动主程序失败

命令：

```powershell
Start-Process -FilePath 'D:\Neowiz\Browndust2\Browndust2_10000001\BrownDust II.exe' -WorkingDirectory 'D:\Neowiz\Browndust2\Browndust2_10000001' -WindowStyle Hidden
```

现象：

- `BrownDust II.exe` 进程出现。
- `UnityCrashHandler64.exe` 子进程出现。
- 轮询约 45 秒后，`MainWindowHandle` 仍为 `0x0`。
- 没有可用的游戏窗口标题。

判断：

- 直接启动主程序不是可靠入口。
- 后续打开游戏应使用启动器协议参数。

### 不带参数启动器不可靠

命令：

```powershell
Start-Process -FilePath 'C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe' -WorkingDirectory 'C:\ProgramData\Neowiz\Browndust2Starter'
```

现象：

- 只看到 `Browndust2Starter` 进程，窗口句柄为 `0x0`。
- 没有快速产生可用的 `BrownDust II` 游戏窗口。

判断：

- 不带 `browndust2:games/10000001?usn=0` 参数不适合作为自动化入口。

## 下一步可复用逻辑

后续脚本打开游戏时，应：

1. 先检查是否已有 `BrownDust II` / `UnityWndClass` 窗口。
2. 如果没有，则用启动器协议参数启动。
3. 轮询等待窗口标题和类名。
4. 使用 `tools/probe_printwindow.py` 或等价截图函数确认画面可抓取。

已实现最小复用脚本：

```powershell
python tools\open_game.py --timeout 90
```

当前已验证：游戏已打开时，该脚本能直接识别现有窗口并输出 `game_window=0x911E8 title=BrownDust II class=UnityWndClass`，不会重复启动。
