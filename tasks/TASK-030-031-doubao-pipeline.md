# TASK-030 / TASK-031：豆包 doubao-creative-drama 流程落地

日期：2026-09-15
来源：用户要求把豆包「短篇短剧视频全流程」技能（planner→scriptwriter→storyboard→assets→frame→prompt）应用到本平台。

## TASK-030 方法论注入（提示词层）

- `DramaticTone`（hook/three_act）→ `ProjectParams.dramatic_tone`；`update_params` 开放；创建表单选择器
- ScriptAgent 按基调注入双轨剧作法（豆包 scriptwriter.md 内化）：
  - hook：黄金开局 / 高频反转 / 压迫具体化+释放痛快 / 反派合理性动机+片内惩罚铁律 / 台词千人千面
  - three_act：单一核心事件 / 三幕 25-50-25 / 允许留白 / 对立面意象化 / 台词克制
  - 通用：情绪外化、动作客观可拍
- StoryboardAgent：`STORYBOARD_CRAFT_RULES`（豆包 storyboard/frame.md 内化）：POV 归属、
  禁群体量词、方向性物体六要素、情绪外化、光影意图
- AssetAgent：`CHARACTER_CARD_B_PROMPT`（豆包资产卡口径）备用，默认不变

## TASK-031 关键帧阶段（结构层）

豆包流程与平台的最大结构差异 = frame 阶段。六阶段流水线：剧本→资产→分镜→**关键帧**→视频→成片。

- 语义：每段一张开场锚点图；批准后作该段视频 `<Picture 1>`（尾帧接力让位，解决尾帧模糊痛点）；
  缺帧段显式勾选 `allow_tail_fallback` 才回退（不静默降级）
- 领域：Stage.FRAME / FRAME_* 三态 / JobType.FRAME_GEN / SegmentFrameImage / Segment.keyframe_description
- 提示词：compile_keyframe_prompt（确定性编译）；compile_h3_prompt(opening_frame=) 重编译非连续段防编号错位；锚定句中性化
- 解析：_resolve_references 关键帧优先；快照 opening_frame_source；>9 截断保关键帧槽位
- UI：FramesTab + 六阶段 StageNav + 确认门
- E2E：run_e2e.py / run_ep001.py 插入关键帧阶段

## 验证

- 后端 `ruff check server` 全过；`pytest server/tests` 163 项全绿（含 8 项基调法则 + 7 项关键帧编译/槽位 + 全链关键帧优先/尾帧回退双分支）
- 前端 `npm run build` + `npx vitest run` 16 项全绿（六阶段推导/门禁/连跑映射）
- 迁移 0004 已对 data/app.db 执行
- 待办：真实 RunningHub 关键帧生图与视频全链验证（烧币，用户择机跑 run_e2e.py）
