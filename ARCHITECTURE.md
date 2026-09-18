# 架构（Architecture）—— short-dreamV4

## 1. 系统概览

单机运行的 AI 短视频生产平台。三个进程协作：

```
┌────────────────────────────────────────────────────────────┐
│ 浏览器：React SPA（工作台）                                  │
└──────────────┬─────────────────────────────────────────────┘
               │ REST + 轮询
┌──────────────▼─────────────────────────────────────────────┐
│ FastAPI 服务（单体）：用例/命令、状态机、REST、静态媒体        │
│     SQLite（项目/工件/任务/设置）  本地媒体目录 data/media/   │
└──────────────┬─────────────────────────────────────────────┘
               │ 轮询 jobs 表
┌──────────────▼─────────────────────────────────────────────┐
│ Worker 进程：任务执行器                                       │
│   ├─ LlmProvider 适配器  → 剧本/资产抽取/分镜/H3 提示词       │
│   ├─ ImageProvider 适配器 → 资产设定图                        │
│   ├─ VideoProvider 适配器 → RunningHub 工作流（H3 导演台，提交+轮询） │
│   └─ ffmpeg 服务         → 尾帧提取 / 成片拼接               │
└────────────────────────────────────────────────────────────┘
```

边界：UI 不直接调用任何外部供应商；领域逻辑不依赖 FastAPI/SQLAlchemy 类型（用例层可脱离 Web 框架测试）；供应商细节止步于适配器。

## 2. 模块

### server/domain（领域层）
职责：实体、状态机、不变量、领域命令。纯 Python，无框架依赖。
依赖：无。
拥有：Project 状态机、Job 状态机、分镜校验规则、参考解析规则、H3 请求模式矩阵。

### server/app（应用层）
职责：用例编排（命令处理器）、事务边界、任务入队、流水线推进。
依赖：domain。
拥有：命令处理、审批门逻辑、下游失效联动。

### server/adapters（基础设施/适配器层）
职责：外部世界封装。
依赖：domain 定义的 Provider 接口。
拥有：`LlmProvider`（OpenAI 兼容实现）、`ImageProvider`（RunningHub QWEN 文生图工作流实现）、`VideoProvider`（RunningHub MiniMax H3 导演台工作流实现）、`MediaUploader`（参考图经 RunningHub 上传 API 落到可引用 fileName）、`FFmpegService`（尾帧提取/拼接/探针）、SQLite 仓储。

### server/api（接口层）
职责：REST 端点、请求/响应模型、错误映射、媒体文件服务。
依赖：app。

### server/worker（任务执行器）
职责：轮询 jobs 表、按类型分发执行、依赖排序、退避重试、恢复中断任务。
依赖：app、adapters。

### client（UI 层）
职责：工作台界面、阶段工作区、任务中心、设置。仅与 server/api 通信。
依赖：无后端内部。

## 3. 数据流

### 3.1 剧本阶段
`创建项目(CREATED) → 命令 generate_script → 入队 script_gen Job → Worker 调 LlmProvider → 结构化校验（失败自动重试一次）→ ScriptVersion(draft) → 项目 SCRIPT_READY → 用户编辑(可选) → approve_script → 锁定 active 版本，项目 SCRIPT_APPROVED`

### 3.2 资产阶段
`generate_assets → asset_extract Job（LlmProvider）→ Asset 集合 + 每资产 image_gen Job（ImageProvider）→ 图片下载落地 data/media/{pid}/assets/ → 项目 ASSET_READY → 用户预览/替换/批准 → approve_assets（全部关键资产须有 ≥1 已批准图）→ ASSET_APPROVED`

### 3.3 分镜阶段
`generate_storyboard → storyboard_gen Job（LlmProvider，输入=active 剧本+资产）→ 校验（段时长 [4,15]、资产引用存在、H3 提示词结构与 ≤7000 字符）→ StoryboardVersion(draft) → STORYBOARD_READY → 用户编辑/重生成提示词 → approve_storyboard → STORYBOARD_APPROVED`

### 3.4 视频生产
`命令 produce_video（全部/首段试跑）→ 按分镜顺序为每段建 video_gen Job：
  1. 解析参考图（approved 资产图；缺 → REF_MISSING 拒绝）
  2. continuity 段建立对前段的依赖边（depends_on）
  3. 按 H3 模式矩阵组装请求（尾帧→reference_image[0] 或 first_frame）
→ Worker 依依赖序提交：VideoProvider 返回 task_id → 轮询至终态 → 下载视频落地 → ffmpeg 抽尾帧 → Clip 就绪
全部段就绪 → 项目 VIDEO_READY`

### 3.5 合成
`compose → composition Job → FFmpegService concat（含音轨）→ data/media/{pid}/films/film_v{n}.mp4 → 项目 COMPOSED`

## 4. 外部服务

| 服务 | 用途 | 适配器 | 备注 |
|---|---|---|---|
| RunningHub 工作流 API（runninghub.cn，ComfyUI 云端托管） | 视频段生成（工作流 `2098713358475288577`：MiniMax H3 导演台 ref2va，自带音轨）+ 资产设定图（工作流 `2098715929763995649`：QWEN 文生图 4 步） | VideoProvider / ImageProvider | 契约见 AI_SPEC 附录 B；create → 轮询 status → outputs；参考图先经上传 API 换 fileName；fileUrl 临时必须立即下载 |
| LLM（OpenAI 兼容 ChatCompletions：GLM/DeepSeek/MiniMax 等） | 剧本/资产抽取/分镜/H3 提示词 | LlmProvider | 可配置 base_url + model + key |
| ffmpeg（本机二进制） | 尾帧提取、成片拼接、媒体探针 | FFmpegService | 启动检测，缺失则视频功能禁用 |

> 历史：2026-09-12 前规格为 MiniMax V2 官方直连（api.minimax.cn）+ image-01 生图，当日用户拍板改走 RunningHub 工作流；官方直连契约保留在 AI_SPEC 附录 A 作备选。H3 模型与提示词方法论不变，变的只是通道。

## 5. 架构不变量

1. UI 永不直接访问外部供应商；供应商调用只发生在 Worker 内的适配器。
2. 领域层（server/domain）不 import FastAPI/SQLAlchemy/httpx。
3. 项目状态只能经领域命令迁移；每个阶段至多一个 active 工件版本，approve 后不可变。
4. VideoJob 持久化完整输入快照（storyboard_version_id、prompt、参考图版本、参数），保证可复现与审计。
5. 参考图解析失败（缺已批准图）必须阻断提交，不允许静默降级。
6. Provider 返回的临时 URL 素材必须立即下载落地本地；平台内部一律使用本地相对路径。
7. 已 succeeded 的 Job 不可重跑；"重试/重生成"一律创建新 Job，旧产物保留。
8. Worker 崩溃恢复：running 任务重启后按类型恢复（video 有 provider_task_id → 恢复轮询；否则标 failed 可重试）。

## 6. 技术决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 后端框架 | Python 3.12 + FastAPI + Pydantic v2 | 已拍板；类型化契约与 Pydantic 校验天然契合 |
| 前端 | React 18 + Vite + TypeScript + TanStack Query + Tailwind | 已拍板 React；轮询友好、轻量 |
| 数据库 | SQLite（WAL 模式） | 单用户零运维；任务表即队列 |
| 队列 | jobs 表 + Worker 轮询（无中间件） | 已拍板方案 A；持久化、可恢复 |
| 视频连续性 | 尾帧经 RunningHub 上传 API 作为 refs[0]（提示词对齐 0.00s）；一次任务只带一个 Segment，不用工作流内置跨段连续性 | 保持领域模型「段=生成单元」与段级重生成；H3 导演台单段驱动 |
| 媒体传递给 H3 | 参考图经 `/openapi/v2/media/upload/binary` 上换取 fileName 填入节点输入 | 官方直连的 Base64 data URI 方案随通道一起作废（见 AI_SPEC 附录 A 备选） |
| 任务状态获取 | 轮询 RunningHub `status`/`outputs`（不用回调） | 免公网回调端点；个人机最简 |
| 媒体文件布局 | data/media/{project_id}/{assets\|frames\|segments\|films}/ | 按 project 分区，路径存 DB 相对路径 |

## 7. 显式非目标

多租户/账号、云部署、对象存储、消息队列中间件、时间线剪辑、配乐混音、移动端、callback 回调、多人协作。

## 8. 架构变更政策

新增供应商适配器、更换数据库、改变任务模型属重大变更，须更新本文件并获批准；模块内实现细节（如重试退避参数）可自行调整。

变更记录：
- 2026-09-12：视频/图片供应商由 MiniMax V2 官方直连改为 RunningHub 工作流 API（用户拍板，提供两个工作流 ID 与 apiKey）；模型仍是 MiniMax H3，领域模型、状态机、确认门、提示词方法论均不变。
