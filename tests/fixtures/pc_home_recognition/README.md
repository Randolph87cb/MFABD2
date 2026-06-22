# PC Home Recognition Fixtures

这个目录保存 PC 客户端主页识别的易错截图，用来验证 `Rec_HomePage_PC_Stable` 不会把非主页误判成主页，也不会漏掉已经回到主页但主页动画或右下浮层不同的情况。

运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\check_pc_home_recognition_corpus.py
```

新增样本时，把截图放进本目录，并在 `manifest.json` 增加一项。`expected_home` 表示 `Rec_HomePage_PC_Stable` 的预期结果；`expected_ocr` 和 `expected_home_icon` 可选，用于约束子识别节点。
