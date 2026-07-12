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
