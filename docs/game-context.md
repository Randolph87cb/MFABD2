# BrownDust II 概念与自动化词表

本文用于记录后续自动化沟通中的统一叫法。游戏资料会随版本变化，本文件只记录当前对脚本有用的基础概念；具体按钮位置以后仍以实际截图为准。

## 页面与状态命名

| 脚本状态 | 中文叫法 | 含义 | 当前自动化用途 |
| --- | --- | --- | --- |
| `title` / `touch_ready` | 标题页 | 显示游戏标题和 `TOUCH TO START` 的启动后页面。 | 点击进入游戏。 |
| `home` | 主页 | 游戏真正的 Home 页面。不要和卡带内地图混用。 | 后续需要单独识别。 |
| `pack_collection` | Pack Collection / 卡带选择 | 管理和选择 Story Pack、Character Pack 等内容的页面。 | 后续从主页或菜单切换日常内容时可能用到。 |
| `pack_field` | 卡带内 Field / 地图场景 | 点击标题页后，客户端可能直接恢复到上一次所在的游戏卡带内地图/场景。当前截图属于这一类。 | 进入游戏步骤的成功态。 |
| `battle` | 战斗页 | 以回合制站位、技能和敌方布阵为核心的战斗界面。 | 后续战斗/扫荡/自动战斗识别。 |

## 当前已确认的游戏结构

- BrownDust II 的内容组织核心是 `Pack`。官方指南把 `Pack Collection` 作为游戏指南的一部分，说明游戏内容不是只有一个固定大厅入口。
- `Story Pack` 是主线剧情类 Pack。进入游戏后如果上次停留在某个 Pack 的地图里，客户端可能直接回到该地图，而不是先到主页。
- Field/地图场景中会出现小地图、角色移动、交互点、任务/战斗入口等 UI，因此它和主页、Pack Collection 都应分开识别。
- Field 内容里会区分 Safety Zone、Battle Zone、Hunting Field、Shop、Inn、Finds、Investigation 等概念。后续做日常脚本时，优先根据当前 UI 状态判断所在场景，再决定点击路线。
- Safety Zone 中通常没有怪物，会有商店、酒馆、NPC、隐藏调查点等奖励/交互对象。
- Battle Zone 是 Pack 内的危险区域，不同 Pack 会有不同地图属性和怪物，也可能有陷阱。
- `Today's Quest` 来自各村 Safety Zone 的任务板，清理后可提升村庄声望并获得奖励；官方说明每日 5 A.M. 初始化。
- 战斗系统是 BrownDust2 自动化的重要独立模块：官方战斗说明里，双方轮流攻防，攻击轮可以预设技能、移动角色、改变攻击顺序；防守轮主要调整站位。后续若要处理日常战斗，应单独识别队伍布阵、敌方格子、自动战斗/跳过/结果按钮等状态。

## 自动化约定

- “进入游戏”这一步只负责：启动 PC 客户端、识别标题页、后台点击 `TOUCH TO START`、确认进入 `pack_field` 或后续定义的稳定落点。
- 不把 `pack_field` 当作主页。需要回主页时，应新增一条“从卡带内场景回到主页”的步骤。
- 后续每个步骤都先写清楚“输入状态”和“输出状态”，例如：
  - 输入：`pack_field`
  - 动作：点击主页/菜单入口
  - 输出：`home`
- 识别优先级建议：
  1. 固定窗口和 DPI 正确性。
  2. 当前大状态：标题页 / 主页 / Pack Collection / 卡带内 Field / 战斗 / 弹窗。
  3. 当前步骤需要的按钮或区域。

## 资料来源

- 官方网站：[BrownDust II](https://www.browndust2.com/)
- 官方 GitBook 指南：[Pack / Story Pack](https://browndust2.gitbook.io/guide_en/game-guideline/pack/story-pack)
- 官方 GitBook 指南：[Pack Collection](https://browndust2.gitbook.io/guide_en/game-guideline/pack/pack-collection)
- 官方 GitBook 指南：[Battle](https://browndust2.gitbook.io/guide_en/game-guideline/battle/battle)
- 官方 GitBook 指南：[Battle Order](https://browndust2.gitbook.io/guide_en/game-guideline/battle/battle-order)
- 官方 GitBook 指南：[Placement](https://browndust2.gitbook.io/guide_en/game-guideline/battle/placement)
- 官方 GitBook 指南：[Safety Zone](https://browndust2.gitbook.io/guide_en/game-guideline/field/safety-zone)
- 官方 GitBook 指南：[Battle Zone](https://browndust2.gitbook.io/guide_en/game-guideline/field/battle-zone)
- 官方 GitBook 指南：[Today's Quest](https://browndust2.gitbook.io/guide_en/game-guideline/quest/todays-quest)

## 待实测确认

- 主页的稳定视觉特征与返回入口。
- `pack_field` 到 `home` 的最短后台点击路径。
- Pack Collection 与 Event/Season/日常入口的稳定视觉特征。
- 日常任务实际包括哪些固定动作，以及哪些能用游戏内自动战斗/扫荡能力完成。
