# 进入游戏并回到主界面记录

## 成功标准

- 已有 `BrownDust II` / `UnityWndClass` 游戏窗口，或能通过 `tools/open_game.py` 打开。
- 后台截图能取得完整窗口画面，而不是 DPI 缩放后的左上角局部。
- 能识别标题页或已在主界面。
- 在标题页时，点击 `TOUCH TO START` 区域后能进入主界面。

## 当前实现

脚本：

```powershell
python tools\enter_game.py --timeout 90
```

逻辑：

1. 查找游戏窗口；没有则调用启动器协议打开。
2. 使用 `PrintWindow` 截图。
3. 裁剪客户区，保存调试图：
   - `enter_game_before_window.png`
   - `enter_game_before_client.png`
   - `enter_game_after_window.png`
   - `enter_game_after_client.png`
4. 识别状态：
   - 标题页：优先识别 Logo / `TOUCH TO START` 区域。
   - 兜底标题页：`PrintWindow` 可能抓不到 Logo / Touch 叠层，因此额外识别左侧版本号、电源、齿轮区域。
   - 主界面：识别左上 UI / 小地图区域。
5. 如果已在主界面，直接返回成功。
6. 如果在标题页，点击客户区相对坐标 `(0.74, 0.70)`，即 `TOUCH TO START` 区域附近。
7. 轮询等待主界面识别成功。

## 本次验证结果

### 点击验证

在标题页时，使用坐标点击：

```powershell
python tools\win32_windowpos_click.py --x 950 --y 505
```

结果：成功从标题页进入主界面。

### 主界面识别验证

在主界面运行：

```powershell
python tools\enter_game.py --timeout 10
```

结果：

- `title_screen=False`
- `home_screen=True`
- `already_home=True`
- `enter_game_ok=True`

## 失败点与修正

### 失败点：截图尺寸不对，只抓到左上角

现象：

- 初始截图只有 `1293x756`。
- 用户截图显示窗口应为更大的完整画面。
- 实际表现为只截到了左上角局部，导致 Logo / `TOUCH TO START` 区域不在截图内。

原因：

- Python 进程未声明 DPI 感知。
- Win32 返回了 DPI 虚拟化后的逻辑尺寸，`PrintWindow` 位图大小建小了。

修正：

- 新增 `tools/win32_dpi.py`。
- 在 `probe_printwindow.py`、`open_game.py`、`win32_windowpos_click.py` 中调用 `enable_dpi_awareness()`。

修正后：

- 当前完整窗口截图尺寸为 `2586x1512`。
- 当前客户区调试截图尺寸为 `2560x1441`。
- 后台截图已能覆盖完整游戏画面。

### 失败点：PrintWindow 可能抓不到标题 Logo / Touch 叠层

现象：

- 截图中能看到背景、版本号、电源、齿轮。
- 但 Logo / `TOUCH TO START` 叠层可能缺失。

处理：

- 识别策略不只依赖 Logo / Touch。
- 增加左侧标题页 HUD 兜底识别。

## 下一步

后续如果要提高标题页识别稳定性，可以补：

- Logo 模板图匹配。
- `TOUCH TO START` 模板图匹配。
- 截图方式改成 MaaFramework 类似的 `FramePool` / Windows Graphics Capture。
