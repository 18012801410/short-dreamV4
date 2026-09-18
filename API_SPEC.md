# API/契约规格（API / Contract Specification）—— short-dreamV4

## 原则
- 契约显式、输入输出有类型（Pydantic DTO ↔ TS 类型由手写对应）。
- 错误统一信封；破坏性变更需评审。
- 所有写操作走"命令"语义（POST /commands），UI 不拼装内部状态。

## 通用约定
- Base：`http://127.0.0.1:8000/api`
- 媒体：`http://127.0.0.1:8000/media/{project_id}/...`（静态服务）
- 错误信封：
```json
{ "error": { "code": "STATE_ILLEGAL", "message": "…", "details": {} } }
```
- 错误码：`VALIDATION_ERROR` / `NOT_FOUND` / `STATE_ILLEGAL` / `REF_MISSING`（附缺图资产列表）/ `DEPENDENCY_BLOCKED` / `PROVIDER_ERROR`（附 provider_code）/ `FFMPEG_MISSING` / `VALIDATION_SEGMENT`（附段级错误列表）

## 端点

### API-001 创建项目
`POST /projects`
```json
{ "title": "渡口夜行", "idea": "……一句话想法……",
  "params": { "genre": "悬疑", "style": "电影写实", "dramatic_tone": "hook",
              "target_duration_sec": 90,
              "scene_count": 3, "ratio": "9:16", "resolution": "768P", "prompt_lang": "en" } }
```
`dramatic_tone`：剧作基调，`hook`（短剧钩子驱动，默认）/ `three_act`（微电影三幕式），
决定 ScriptAgent 注入的结构法则（TASK-030）。
响应 `201`：`{ "project": { "id": "…", "status": "CREATED", … } }`
错误：VALIDATION_ERROR。

### API-002 项目列表/详情
`GET /projects?include_archived=false` → `{ "projects": [...] }`
`GET /projects/{pid}` → 项目 + 各阶段 active 版本摘要 + 段状态统计。

### API-003 命令端点（核心）
`POST /projects/{pid}/commands`
```json
{ "type": "generate_script" }
```
命令类型与前置状态（服务端校验，非法 → STATE_ILLEGAL）：
`generate_script`、`edit_script_draft`（payload=剧本 JSON）、`approve_script`、
`generate_assets`、`create_asset`（payload={kind, name, description?, visual_anchor?, image_plan?}）、`edit_asset`（payload={asset_id, name?, description?, visual_anchor?, image_plan?}；两者过资产门后改动 → FR-016 失效回退 ASSET_READY）、`generate_asset_image`（payload={asset_id, view_label, extra_prompt?}）、`upload_asset_image`（multipart）、`approve_asset_image`（payload={asset_image_id, approved}）、`approve_assets`、
`generate_storyboard`、`edit_segment`（payload=段 JSON）、`approve_storyboard`、
`generate_keyframes`（逐段入队关键帧生成；FRAME_APPROVED/VIDEO_READY/COMPOSED 重生成走 FR-016 回退，TASK-031）、
`generate_frame_image`（payload={segment_key, extra_prompt?, seed?}，单段重抽）、
`upload_frame_image`（multipart，payload={segment_key, content, ext}）、
`approve_frame_image`（payload={frame_image_id, approved}）、`delete_frame_image`（payload={frame_image_id}）、
`approve_keyframes`（payload={allow_tail_fallback?}；默认逐段 ≥1 已批准关键帧，缺帧段须显式回退尾帧接力）、
`produce_video`（payload={scope:"all"|"first_only"|"{segment_key}"}；前置 FRAME_APPROVED）、`regenerate_segment`（payload={segment_key, prompt_overrides?}）、
`update_params`（payload={style?, dramatic_tone?}，出图风格/剧作基调即时生效于后续生成；tone 取值非法 → VALIDATION_ERROR）、`compose`（VIDEO_READY 或 COMPOSED 重新合成）、`run_to_next_gate`、`archive_project`。
响应：`202 { "jobs": [...] }` 或同步命令 `200 { "ok": true, "project": {...} }`。
错误：STATE_ILLEGAL、REF_MISSING（produce_video 时）、FFMPEG_MISSING。

### API-004 剧本
`GET /projects/{pid}/script` → `{ "active": {...}|null, "draft": {...}|null, "versions": [...] }`

### API-005 资产
`GET /projects/{pid}/assets` → `{ "assets": [ { …, "images": [ { …, "approved": true } ] } ] }`

### API-006 分镜
`GET /projects/{pid}/storyboard` → `{ "active": {...}|null, "draft": {...} }`
draft/active 内容含 segments[]（每段：segment_key、duration_sec、shots[]、asset_refs[]、continuity、h3_prompt、resolution_status：`ok` / `ref_missing:[asset_id…]` / `blocked_by:[segment_key…]`）。

### API-006.5 关键帧（TASK-031）
`GET /projects/{pid}/frames` → `{ "frames": [ { "id", "segment_key", "version_no", "view_label", "prompt", "url", "status", "approved" } ], "keyframe_descriptions": {segment_key: str} }`（按 active 分镜段序排列）
`POST /projects/{pid}/segments/{segment_key}/upload_frame`（multipart，字段 `file`）→ `{ "ok": true, "frame_image": {...} }`

### API-007 任务
`GET /projects/{pid}/jobs?status=&type=` → `{ "jobs": [...] }`
`POST /jobs/{job_id}/retry`（仅 failed）→ `202`
`POST /jobs/{job_id}/cancel`（pending/running）→ `202`

### API-008 段与片段
`GET /projects/{pid}/segments` → `{ "segments": [ { "segment_key", "status", "clip": {…}|null, "job": {…}|null } ] }`
`GET /projects/{pid}/film` → `{ "films": [...] }`（最新在前，含 url 与 subtitle_url——合成时自动按分镜台词烧录的字幕版，null=无台词或烧录失败）

### API-009 设置与系统
`GET /settings` → `{ "llm": {..."api_key_masked"}, "image": {...}, "video": {...}, "ffmpeg": {"path","found":true} }`
`PUT /settings` → 200（key 字段传 null 表示不改）
`POST /settings/test` → `{ "llm": {"ok":true}, "image": {"ok":false,"error":"…"} }`
`GET /system/health` → `{ "ffmpeg": true, "providers_configured": {...}, "db": "ok" }`

### API-010 媒体
`GET /media/{pid}/{...path}` —— 本地文件服务（只读）。

### API-011 系列分集（TASK-047，2026-09-17）
`POST /series` → `201 { "series": {...} }`
```json
{ "title": "可选，缺省取想法前 20 字", "idea": "……一句话想法……",
  "params": { "genre": "", "style": "", "dramatic_tone": "hook",
              "episode_count": 12, "per_episode_sec": 90, "scene_count": 4 } }
```
`GET /series` → `{ "series": [ {..., "episode_projects": N, "statuses": [...] } ] }`
`GET /series/{sid}` → `{ "series": { ..., "outline": SeriesOutline|null, "episodes": [ { episode_no, title, opening_hook, synopsis, highlight, ending_hook, "project": Project|null } ] } }`
`GET /series/{sid}/outline-status` → `{ "status", "outline_ready", "warnings": [...] }`（质量门横幅）

`POST /series/{sid}/commands`（系列命令，均为零币 LLM 或纯同步）：
- `generate_series_outline`：入队 `series_outline_gen` Job（Worker 执行，产出后自动跑
  确定性质量门，warnings 写入 outline）；重入护栏：同系列任务未终结时拒绝。
- `edit_series_outline` `{ "outline": {...} }`：保存草稿并重跑质量门；CREATED→READY、
  APPROVED→READY（改大纲需重新确认）。
- `approve_series_outline`：READY→APPROVED；硬质量门未过 → 422 VALIDATION_FAILED（issues 明细）。
- `generate_episode_scripts` `{ "episode_nos": [..] 可缺省=全部 }`：为缺项目的集各建一个
  Project（挂 series_id/episode_no、target_duration_sec=per_episode_sec）并入队 script_gen
  （payload 带 series 上下文 → 连载法则剧本生成）；全部已建档时 STATE_ILLEGAL。
- `inherit_series_assets` `{ "from_episode_no": 1, "to_episode_no": N }`：把来源集资产卡
  （含图片行，同一份磁盘文件）复制到目标集，目标集直接落到 ASSET_READY；
  前置：目标集 SCRIPT_APPROVED、来源集已有资产。零生成成本，跨集形象一致。

## 领域命令
见 DOMAIN_MODEL.md「命令」一节；REST 命令端点是它们唯一的 HTTP 入口。

## 事件
无推送（MVP 轮询）：UI 以 2s 间隔轮询 jobs/segments/film；未来如需 SSE 属增量变更。

## 版本化
- API 前缀 `/api`（隐式 v1）；破坏性变更须升 `/api/v2` 并评审
- 工件版本化在领域层（version_no），API 始终返回 active+draft 双版本
