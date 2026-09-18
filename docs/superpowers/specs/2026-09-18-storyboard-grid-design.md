# 设计文档：分镜多宫格分镜板（方案 A：逐格生成 + 程序拼宫格）

日期：2026-09-18
状态：已与用户对齐方向（宫格=视频参考图；视频侧沿用 MiniMax H3）

## 1. 背景与目标

现状问题：
- 单张关键帧信息量不足，视频（MiniMax H3，r2v 模式）质量差。
- 直接让 Qwen-Image-Edit 一条指令画出多宫格，布局与指令遵循不可靠（已实测）。

目标：
- 每个分镜（Segment）产出一张多宫格分镜板（storyboard grid），整张作为视频生成的 `<Picture 1>` 分镜板参考。
- 依据官方口径：ComfyUI 官方 H3 R2V 示例即使用 9 宫格分镜板；官方 R2V 提示词指南明确支持
  `<Picture N> is a storyboard reference for [Shot 1] and [Shot 2], defining their viewpoint, subject placement, and shot order.`

核心思路（方案 A）：不赌模型的宫格指令遵循——段内每个 shot/节拍**逐格独立生成单帧**（沿用现有单帧关键帧口径，资产卡参考、身份锁定全部保留），再用 PIL 程序化拼成宫格。复用 `_compose_reference_sheet` 的成熟拼图基建。

## 2. 总体架构

```
分镜确认（STORYBOARD_APPROVED）
  └─ cmd_generate_keyframes（改造）
       └─ 逐 shot 派生格子任务（入队 FRAME_GEN，格数 = min(shots数, MAX_GRID_CELLS=6)）
            └─ 每格：compile_keyframe_prompt（单帧口径不变）+ 资产卡参考（沿用 _segment_reference_cards/_resolve_reference_slots）
  └─ 全部格子成功后：_compose_storyboard_grid（PIL 3 列 N 行白边拼接）
       └─ 产出 storyboard grid 落 storyboards/，登记 SegmentFrameImage（view_label="分镜板"）
  └─ approve_keyframes（口径改为“宫格已确认”，状态机不变）

cmd_produce_video（改造 _resolve_references）
  └─ 宫格占 <Picture 1> + storyboard 声明句；资产卡顺延 <Picture 2+>
  └─ 同场景后继段：尾帧接力机制保持不变（宫格让位，同现有关键帧让位逻辑）
```

## 3. 组件改动清单

| 层 | 文件 | 改动 |
|---|---|---|
| 领域 | `server/domain/entities.py` | `SegmentFrameImage` 增加 `grid_cell`（int，可空）字段：该行是宫格的第几格；宫格行 `view_label="分镜板"` 且 `grid_cell=None` |
| 领域 | `server/domain/validation.py` | 新增 `MAX_GRID_CELLS = 6`；格内分辨率下限 768px 校验 |
| 数据 | `server/infra/tables.py` | `segment_frame_images` 建表语句与轻量迁移加 `grid_cell INTEGER`（可空） |
| 存储 | `server/infra/repositories.py` | `FrameRepository` 支持 `grid_cell` 读写 |
| 媒体 | `server/infra/media.py` | 新增 `storyboard_rel` 路径规则（`storyboards/`） |
| usecase | `server/app/usecases.py` | 1) `_enqueue_frame_job` 改为逐格派生：按 `segment.shots` 每格取该 shot 的 action/description 作画面描述；2) 新增 `_compose_storyboard_grid`：3 列布局、白边分隔、格子等高缩放，复用 `_compose_reference_sheet` 思路；全部格子 approved 后自动拼板（在 `_maybe_frames_ready` 触发点调用）；3) `_resolve_references`：已批准宫格占 `reference_paths[0]`，资产卡顺延；4) `cmd_produce_video`/`cmd_regenerate_segment`：宫格让位逻辑与现有关键帧一致（场景首段占 Picture 1，后继段尾帧接力优先） |
| 编译 | `server/app/h3_compiler.py` | 1) `compile_keyframe_prompt` 增加可选 `cell_context`（格号/总数/本格对应 shot），格内仍保持 `single frame composition`；2) `compile_h3_prompt` 新增 storyboard 声明句：`<Picture 1> is a storyboard reference for [Shot 1]..[Shot N] of this segment, defining viewpoint, subject placement, and shot order.`（仅当 Picture 1 为宫格时注入）；3) `KEYFRAME_NEGATIVE` 中 `split screen, collage, multiple panels, grid layout` 仅在**格内**描述上下文保留（宫格本身不再被负向词封杀——负向词作用于单帧生成，无需删除，拼宫格是程序行为） |
| worker | `server/app/handlers.py` | `handle_frame_gen` 无需大改（仍是单帧生成）；宫格拼板在 approve/delete 侧触发（`_sync_storyboard_grid`），`_maybe_frames_ready` 不感知宫格 |
| API | `server/api/routes_workbench.py` | frames 列表返回 `grid_cell`；前端可按格展示（前端改动另行评估） |
| 配置 | `server/infra/config.py` | 新增 `storyboard_grid_cols = 3`（可调） |

## 4. 关键决策

1. **格数上限 6**：段内 shots 超过 6 时只取前 6 格，其余交给视频模型按提示词自行发挥；提示词中 storyboard 声明句只声明实际存在的格数。
2. **格子画面描述来源**：优先该 shot 的 `action`，回退 `description`，再回退 `segment.keyframe_description`（与现有关键帧回退链一致）。
3. **宫格拼装时机**：该段全部格子 approved 后自动拼板（upload 外部图同样计入格子）；任一格重抽 → 宫格作废重拼（版本号递增）。
4. **视频侧宫格分辨率**：拼板输出保证总像素 ≥ H3 参考图可读阈值（每格 ≥768px 短边，3 列 16:9 格 → 约 3456×1944 上限内按需缩放）。
5. **旧数据兼容**：存量已批准单张关键帧继续按原逻辑工作（`grid_cell` 为空且 `view_label="开场锚点"` 的行走旧口径），不做强制迁移。

## 5. 错误处理

- 某格生成失败：不影响其他格子；重抽按钮按格操作；宫格在全部格子就绪前不生成。
- 拼板失败（PIL 异常/图片缺失）：任务标记失败并保留格子，可单独重试拼板，不重复扣生图费用。
- 宫格存在但用户上传了手动覆盖图：手动图优先占 `<Picture 1>`（沿用现有 manual_override 语义）。

## 6. 测试计划

- 单元：格子描述派生（shot action→description→keyframe_description 回退链）；`_compose_storyboard_grid` 拼接（3 列、白边、等高、格数≤6）；`grid_cell` 迁移与读写；storyboard 声明句注入与让位逻辑（宫格 vs 尾帧接力 vs 手动覆盖）。
- 流程回归：`test_frame_stage.py` 既有用例按新口径更新；`test_app_flow.py` 尾帧接力用例不变仍须通过。
- 实测验收（人工，花币操作按 AGENTS.md 规则先重启确认）：生成一个多 shot 分镜的宫格 → 产视频，比对 H3 是否按宫格分镜执行。

## 7. 明确不做

- 不做 Qwen 单指令直出宫格（已证不可靠）。
- 不改资产卡体系、尾帧接力机制、H3 六段提示词主体。
- 不做宫格自动裁格送视频（宫格整张作参考是官方支持口径）。
- 前端宫格预览 UI 仅做最小展示调整，复杂交互另立任务。
