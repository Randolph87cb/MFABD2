# Skill Backlog

## PC 流程复刻项目 skill

### 已发生事实

- 已完成快速狩猎 PC MVP，并用 fixture、检查脚本和真实窗口 harness 验证。
- 已为地图采集单章增加 PC 安全骨架，暴露并修复了 base `PatchBatch` action 残留问题。
- 已形成固定流程：base/pc 差异审计、PC 覆盖、易错库、结构检查、corpus 检查、真实窗口 harness、启动脚本校验、提交推送。

### 当前判断

暂不直接做全局 skill。流程强依赖 MFABD2 目录结构、MaaFramework pipeline、PC 客户端截图和项目内 harness。跨项目边界还不稳定。

### 推荐沉淀层

先作为项目公共文档和 skill backlog 保留。继续复刻 2 到 3 个流程后，再判断是否升级为项目 skill。

### 还缺什么

- 至少再复刻一个包含真实点击动作的 PC 非战斗流程。
- 抽出通用 harness 命名和输出规范。
- 抽出 `check_pc_<task>_mvp.py` 与 `check_pc_<task>_corpus.py` 的模板。
- 明确 fixture manifest 的统一字段和命名规范。

### 下一步最小动作

后续继续复刻地图采集技能时，沿用 `docs/zh_cn/PC流程复刻指南.md`，并把新的误判和验证命令补回指南。若第二个完整流程也稳定，再创建项目 skill。
