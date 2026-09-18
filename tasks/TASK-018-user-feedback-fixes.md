# TASK-018 —— 用户实测反馈三连修：剧本可改 / 资产可增改 + 分镜参考图可视化 / 视频一致性

日期：2026-09-13 ｜ 模式：缺陷修复 + 有界新功能 ｜ 状态：已完成

## 背景（用户反馈原话）

1. 剧本生成后无法修改。
2. 资产图片无法修改和新增，LLM 未识别到的资产无法新增；分镜中无法直观看到每个分镜参考了哪些参考图。
3. 生成的视频人物变脸，场景不统一。

## 根因

### 问题 3（最重）：`<Picture N>` 编号整体错位
- AI_SPEC 早已约定「连续段尾帧 = `<Picture 1>`（0.00s 开场），资产图从 `<Picture 2>` 起」，
  但 `H3_RULES_DIGEST`（分镜 Agent 唯一能看到的规则）从未包含该约定，Agent 一律从
  `<Picture 1>` 开始给资产图编号。
- 生产时 handler 把尾帧插到参考图第 0 位 → 实际送入顺序 `[尾帧, 资产1, 资产2, …]` 与
  提示词编号全部差一位 → 人物身份绑到错误图片上 → 变脸、场景漂移。
- 次因：`truncate_references` >9 张时按 角色>场景>道具 **重排**，同样破坏编号；
  `_resolve_references` 取 `approved[-1]`（最新批准图）作身份锚，后上传的随手图会顶掉设定图。

### 问题 1：剧本编辑
- 后端 `edit_script_draft`（含 FR-016 失效回退）一直在，但 UI 只给「草稿」提供编辑，
  剧本确认后（正常主路径）没有任何编辑入口；且编辑器是裸 JSON 文本框。
- BUG：CREATED 状态下手写首稿会因 `apply_action` 返回值被丢弃 + `script_generated`
  非法迁移而直接抛 STATE_ILLEGAL（卡死）。

### 问题 2：资产与分镜可视化
- `create_asset` / `edit_asset` 命令从未实现（API_SPEC 已列 `edit_asset`，属规格-实现漂移）；
  资产唯一来源是 LLM 抽取，漏拆即死路。`generate_asset_image` 支持 `extra_prompt` 但 UI 未暴露。
- 分镜路由只算 ref_missing/ok，不解析实际参考图；分镜 Tab 零参考图展示。

## 修复

| 层 | 改动 |
|---|---|
| `domain/validation.py` | 新增 `has_continuity_tail` / `expected_picture_count`；`validate_h3_prompt` 增加「mentioned `<Picture N>` 集合 == {1..实际张数}」硬校验（含修正指引文案）；`truncate_references` 改为保序保留前 9（不重排） |
| `app/agents.py` | `H3_RULES_DIGEST` 增补规则 6（尾帧占位与编号逐字对应）与规则 7（asset_refs 排序/≤9/场景必引）；`_validate_content` 增加跨段校验（segments 按 (scene_id,index) 排序、continuity 前段 key 必须紧邻） |
| `app/usecases.py` | `_pick_primary_image`：角色优先「主设定」、场景优先「空镜」，确定性取图；`_resolve_references` 沿用；新增 `create_asset` / `edit_asset` 命令（资产门后改动 → FR-016 回退 ASSET_READY）；`segment_reference_previews`（与生产同规则的参考图预览）；修复 `edit_script_draft` 的 CREATED 卡死 |
| `infra/repositories.py` | `AssetRepo.save_asset` |
| `api/routes_workbench.py` | 命令响应支持 `asset` 序列化；`GET /storyboard` 每段附加 `references`（图片 URL/`<Picture N>` 编号/尾帧标记/编号错位标志） |
| 前端 | `ScriptTab`：已确认版本可「编辑（重新进入草稿）」，结构化编辑器（场景/台词增删改 + JSON 模式）；`AssetsTab`：新增资产表单、资产设定编辑（含 image_plan）、自定义提示词生成；`StoryboardTab`：每段参考图缩略图条（P1=尾帧 0.00s 徽标、缺失红框、「⚠ 编号错位」徽标） |
| 测试 | 截断保序、编号校验（连续段正确/旧惯例被拦/base 多余 Picture）、CREATED 手写首稿到 READY、create/edit_asset 全链（104 后端 + 14 前端全绿） |

## 决策记录
- 截断从「按优先级重排」改为「保序保留前 9」：任何重排都会破坏提示词已写死的编号；
  重要性排序上移到分镜阶段（规则 7：asset_refs 按主要角色>场景>道具排序且 ≤9）。
- 存量项目（如「渡口夜行」）的分镜仍是旧编号惯例：打开分镜 Tab 会看到「⚠ 编号错位」，
  修复方式是对该项目「重新生成分镜」（分镜 Agent 现在知道编号规则并被硬校验拦截）。

## 验收
- `python -m pytest server/tests -q` → 104 passed；`ruff check server` → clean
- `npx tsc -b` → clean；`npx vitest run` → 14 passed
