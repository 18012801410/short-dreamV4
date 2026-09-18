# 领域模型（Domain Model）—— short-dreamV4

## 核心实体

### 实体：Project
用途：一次短片生产的顶层聚合根。
标识：`project_id`（uuid）。归属：全局。

字段：
- title、idea（原始想法文本）
- params：genre（题材）、style（风格）、dramatic_tone（剧作基调 hook/three_act，默认 hook，TASK-030）、target_duration_sec、scene_count、ratio（默认 `9:16`）、resolution（默认 `768P`）、prompt_lang（`en`/`zh`，默认 `en`）
- series_id（所属系列，TASK-047；独立项目为空串）、episode_no（系列内集号，从 1 起；独立项目为 0）
- status（见状态机）、created_at、updated_at

关系：拥有 ScriptVersion / Asset / StoryboardVersion / SegmentFrameImage / Job / Clip / Film 各集合。

状态机：
```
CREATED → SCRIPT_DRAFTING → SCRIPT_READY → SCRIPT_APPROVED
        → ASSET_DRAFTING → ASSET_READY → ASSET_APPROVED
        → STORYBOARD_DRAFTING → STORYBOARD_READY → STORYBOARD_APPROVED
        → FRAME_DRAFTING → FRAME_READY → FRAME_APPROVED
        → VIDEO_PRODUCING → VIDEO_READY → COMPOSING → COMPOSED
任意状态 → ARCHIVED
```

合法迁移（由领域命令触发）：
- `generate_script`：CREATED→SCRIPT_DRAFTING；SCRIPT_READY→SCRIPT_DRAFTING（重生成）
- `approve_script`：SCRIPT_READY→SCRIPT_APPROVED
- `generate_assets`：SCRIPT_APPROVED→ASSET_DRAFTING；ASSET_READY→ASSET_DRAFTING
- `approve_assets`：ASSET_READY→ASSET_APPROVED（前置：所有分镜可能引用的关键资产 ≥1 已批准图）
- `generate_storyboard`：ASSET_APPROVED→STORYBOARD_DRAFTING
- `approve_storyboard`：STORYBOARD_READY→STORYBOARD_APPROVED
- `generate_keyframes`：STORYBOARD_APPROVED→FRAME_DRAFTING；FRAME_READY→FRAME_DRAFTING（重生成，TASK-031）
- `approve_keyframes`：FRAME_READY→FRAME_APPROVED（前置：逐段 ≥1 已批准关键帧；显式 `allow_tail_fallback` 才允许缺帧段回退尾帧接力）
- `produce_video`：FRAME_APPROVED→VIDEO_PRODUCING
- `compose`：VIDEO_READY→COMPOSING；合成成功→COMPOSED。COMPOSED→COMPOSING（重新合成，TASK-019：段重生成后可再出一版并烧字幕）
- 下游失效（FR-016）：已确认工件被编辑 → 项目回退到该阶段 READY 态，下游工件标 `stale` 且其批准被撤销

不变量：
- 每阶段至多一个 active 版本；approve 后 active 版本不可变
- 生成进行中（*_DRAFTING）禁止再次入队同阶段生成任务

版本化：工件全部版本化（见下）。

### 实体：Series（TASK-047，2026-09-17）
用途：系列分集剧的顶层聚合：一个想法 → 全剧大纲 → N 个集 Project（每集一条完整流水线）。
标识：`series_id`（uuid）。归属：全局。

字段：
- title、idea
- params（SeriesParams）：genre、style、dramatic_tone、episode_count（默认 12）、
  per_episode_sec（默认 90）、scene_count（默认 4）、ratio/resolution/prompt_lang
- outline（SeriesOutline JSON，可空）：title 剧名、logline、genre_tags、
  characters[]（全剧人物表：身份/动机/口头禅/爽点功能）、
  episodes[]（episode_no/title/synopsis/opening_hook/ending_hook/highlight/new_characters）、
  climax_episode、warnings（确定性质量门结果）
- status（created → outline_drafting → outline_ready → outline_approved）、created_at、updated_at

关系：1 个 Series → 0..n Project（project.series_id + episode_no）。
集 Project 的状态机与单项目完全一致；大纲生成 Job 挂 series 级
（jobs.project_id = series_id，type=series_outline_gen）。

不变量：
- 大纲确认（outline_approved）前不得批量生成分集剧本；硬质量门未过不得确认
  （validate_series_outline，见 AI_SPEC SeriesAgent）
- 已确认大纲被编辑 → 状态回退 outline_ready（重新过门再确认）
- 集剧本生成 = 复用 script_gen Job（payload 带 series_id/episode_no），
  每集独立任务、独立重试

### 实体：ScriptVersion
用途：剧本的不可变版本。
标识：`script_version_id`。归属：Project。

字段：version_no、content（JSON：logline、scenes[{id,title,summary,dialogues[{speaker,line}],est_seconds}]、characters[]、props[]）、source（llm_call 快照 id / manual_edit）、status（draft/active/superseded）。

不变量：active 版本不可变；编辑草稿 = 新建 draft 版本。

### 实体：Asset
用途：从剧本抽取的可复用视觉资产定义。
标识：`asset_id`。归属：Project。

字段：kind（character/scene/prop）、name、description、visual_anchor（外观锚点：发型/服装/配色/材质等一致性描述）、updated_at。

关系：1 个 Asset → 0..n AssetImage。

状态：无独立状态机（由其图片集合的批准状态间接表达）。

不变量：同 Project 内 (kind,name) 唯一；剧本确认后新增资产允许（补充），但分镜校验以当次 storyboard 引用为准。

### 实体：AssetImage
用途：资产的参考图版本（H3 多参考的来源）。
标识：`asset_image_id`。归属：Asset。

字段：version_no、view_label（如 主设定/三视图/空镜/状态变体）、prompt、provider、provider_ref、file_path（本地相对路径，落地后不可变）、status（generating/ready/failed/uploaded）、approved（bool）、created_at。

不变量：
- `ready/uploaded` 才可被批准；approved 图片文件不可删除/覆盖
- 参考图合规约束（来自 H3 官方）：尺寸 ≥256px、宽高比 [0.4,2.5]、JPG/PNG/WEBP——生成后超限自动缩放裁切再落盘

### 实体：StoryboardVersion
用途：分镜的不可变版本；段与镜头的载体。
标识：`storyboard_version_id`。归属：Project。

字段：version_no、status（draft/active/superseded）、content（JSON，见 Segment）。

### 实体：Segment（嵌入 StoryboardVersion.content，非独立表）
用途：一次视频生成的完整单元。
标识：`segment_key`（版本内稳定，格式 `S{scene:02d}G{index:02d}`）。

字段：
- scene_id、index、duration_sec（整数 [4,15]）
- shots[]：{shot_no, cutpoint_sec（累计秒，首镜 0.00）, camera（运镜词）, description, action, beat_refs, dialogue_refs[{speaker,line,tone}]}
- asset_refs[]：{asset_id, usage_note}
- continuity：{enabled（同场景相邻段默认 true）, with_prev_segment_key}
- soundscape、music：声音设计（英文，编译进 H3 提示词最后两段）
- keyframe_description（TASK-031）：英文开场锚点静态描述（第 0 帧构图/人物位置姿态/持物/光效）；空则关键帧生图回退首镜开场状态
- h3_prompt：{text, lang, structure_version}
- status：draft/approved（随 storyboard 版本整体批准）

不变量：
- duration_sec ∈ [4,15]；cutpoint 单调递增且 < duration_sec
- asset_refs 引用的 asset_id 必须存在于当前 active 资产集
- h3_prompt.text ≤7000 字符且结构符合官方 dialect（按模式二选一，结构字段恒英文）：
  无参考图 → 三段（integrated_multimodal_description / overall_soundscape / non_diegetic_music）；
  有参考图（资产图或连续性尾帧）→ 六段（subject_definitions / summary / retention_analysis /
  detailed_description / overall_soundscape / non_diegetic_music）；
  时间线 [Shot 1] 无时间戳、[Shot N] At MM:SS.mmm 与 shots 切点一致；台词 <d>[语言] 逐字</d>
  只在画面段时间线；正文引用标签须在 subject_definitions 定义
- 单段解析后参考图 >9 张时按 角色>场景>道具 截断并显式警告（关键帧占用的开场槽位保留，TASK-031）

### 实体：SegmentFrameImage（TASK-031，关键帧）
用途：一个分镜段的开场锚点关键帧图；段有已批准关键帧 → 产视频时作 `<Picture 1>` 开场参考（尾帧接力让位），无 → 回退尾帧接力。
标识：`frame_image_id`。归属：Project + segment_key。

字段：segment_key、version_no（段内递增）、view_label（默认「开场锚点」）、prompt（由 compile_keyframe_prompt 确定性编译：风格行 + keyframe_description + 在场资产 visual_anchor 逐项锁 + 画幅）、provider、provider_ref、file_path（POSIX 相对路径）、status（generating/ready/failed/uploaded）、approved、created_at。

不变量：approved 需 status ∈ {ready, uploaded}；同段多版本并存，产视频确定性取最高批准版本。

### 实体：Job
用途：一切异步工作的统一持久化记录（即任务队列）。
标识：`job_id`。归属：Project。

字段：type（script_gen/asset_extract/image_gen/storyboard_gen/frame_gen/video_gen/tail_extract/compose）、payload（JSON）、status、progress（0-100 + phase 文案）、priority、depends_on（job_id 列表）、attempts、max_attempts、provider_task_id（video 类）、input_snapshot（JSON，video 类必填；含 opening_frame_source: keyframe/tail_frame，TASK-031）、error（结构化：code/message/provider_code）、log（结构化事件列表）、created/started/finished_at。

状态机：`pending → running → succeeded | failed | cancelled`；`failed → pending`（重试 = 同 Job 重置，仅限 attempts < max_attempts；"重新生成"= 新 Job）。

不变量：
- running Job 在 Worker 重启后的处置：video_gen 且有 provider_task_id → 恢复轮询；其余 → 标 failed（reason=interrupted）
- depends_on 未全部 succeeded 的 Job 不得进入 running
- succeeded Job 不可变更；重试计费安全：provider 调用成功前的失败不产生新费用

### 实体：Clip
用途：一个段成功生成的视频产物。
标识：`clip_id`。归属：Project（经 video job 关联 segment_key）。

字段：segment_key、video_job_id、storyboard_version_id、file_path、thumbnail_path、tail_frame_path（ffmpeg 抽取，可为 null）、duration_sec、mode（t2va/i2va/r2va）、created_at。

不变量：文件只增不删（重生成保留旧 Clip 供对比）；tail_frame 尺寸合规（同参考图约束）。

### 实体：Film
用途：成片产物。
标识：`film_id`。归属：Project。字段：version_no、file_path、segment_keys（有序）、duration_sec、status（composing/ready/failed）、error。

### 实体：ProviderCall
用途：成本日志（FR-015）。字段：job_id、provider、model、kind、usage（JSON：视频 total_seconds、图片张数、LLM tokens）、ok、created_at。

### 实体：AppSettings
用途：全局配置（单行/键值）。字段：llm（base_url/model/api_key_ref）、image（provider/model）、video（model=MiniMax-H3、resolution、并发数默认 1）、ffmpeg_path、poll_interval_sec。

## 定义与实例

- **定义（Definition）**：Asset（角色"林晚"及其外观锚点）。
- **实例（Instance）**：AssetImage（"林晚-主设定图 v3，已批准"）。
- **配置（Configuration）**：Project.params、AppSettings。
- **执行（Execution）**：Job。
- **结果（Result）**：ScriptVersion/StoryboardVersion/Clip/Film。

## 命令

CreateProject、GenerateScript、EditScriptDraft、ApproveScript、GenerateAssets、GenerateAssetImage、UploadAssetImage、ApproveAssetImage、GenerateStoryboard、EditSegment、ApproveStoryboard、ProduceVideo（all|first_only|segment_key）、RegenerateSegment、ComposeFilm、RetryJob、CancelJob、RunToNextGate、ArchiveProject、UpdateSettings。

规则：命令由应用层校验前置状态后执行；非法迁移返回 `STATE_ILLEGAL`。

## 事件

Job 状态变更（pending/running/succeeded/failed/cancelled）、项目状态变更、Clip 就绪、Film 就绪。MVP 以 DB 轮询暴露给 UI（不引入事件总线）；事件日志写入 job.log。

## 资产/结果

- 剧本/分镜：版本化 JSON（DB）
- 图片/视频/帧/成片：文件系统 `data/media/{project_id}/…`，DB 存相对路径
- H3 视频请求输入快照：VideoJob.input_snapshot（JSON，含参考图版本 id）

## 序列化

- SQLite 表：projects、script_versions、assets、asset_images、storyboard_versions、jobs、clips、films、provider_calls、settings
- content/payload/snapshot 用 JSON 列；schema 由 Pydantic 模型定义并在边界校验
- 媒体路径统一 POSIX 风格相对路径（`{pid}/segments/xxx.mp4`），由仓储层解析为绝对路径
