# 技术规格（Technical Specification）—— short-dreamV4

## 技术栈

| 领域 | 选择 | 原因 |
|---|---|---|
| 前端 | React 18 + Vite 5 + TypeScript + TanStack Query v5 + Zustand + Tailwind CSS + react-router v6 | 轮询型任务 UI 友好；轻量 |
| 后端 | Python 3.12 + FastAPI + Pydantic v2 + Uvicorn | 已拍板；契约即模型 |
| ORM | SQLAlchemy 2.0（Core 风格仓储）+ Alembic 迁移 | 稳定；领域层不 import 它 |
| 数据库 | SQLite（WAL，`data/app.db`） | 单用户零运维 |
| 队列 | jobs 表 + Worker 轮询（独立进程；**可多进程并发**：认领为条件 UPDATE 抢占） | 已拍板；可恢复 |
| HTTP 客户端 | httpx（同步 Client；2026-09-13 修订：Worker handler 为同步协议、逐 Job 串行，async 无收益） | 适配器统一用 |
| 媒体处理 | ffmpeg（系统二进制，subprocess 调用） | 尾帧提取/拼接/探针 |
| 测试 | pytest（后端）、Vitest（前端） | 领域层与适配器可单测 |

## 仓库结构

```
short-dreamV4/
├─ PROJECT_BRIEF.md … PROJECT_STATE.md   # 规格说明包（本目录根）
├─ server/
│  ├─ domain/          # 实体、状态机、不变量、错误（纯 Python）
│  ├─ app/             # 用例/命令处理器、入队编排、流水线推进
│  ├─ adapters/
│  │  ├─ llm/          # LlmProvider 接口 + openai_compatible 实现
│  │  ├─ image/        # ImageProvider 接口 + runninghub_qwen_image 实现
│  │  ├─ video/        # VideoProvider 接口 + runninghub_h3_director 实现
│  │  ├─ media.py      # MediaResolver：本地文件 ↔ base64 data URI、合规校验
│  │  └─ ffmpeg_svc.py # FFmpegService：尾帧提取、concat、probe
│  ├─ infra/           # SQLAlchemy 仓储、SQLite、settings 存储
│  ├─ api/             # FastAPI 路由、DTO、错误映射、静态媒体服务
│  ├─ worker/          # 轮询循环、handler 注册表、恢复逻辑
│  └─ tests/
├─ client/
│  └─ src/{pages,components,api,hooks,stores,styles}
├─ tasks/              # TASK-xxx.md 任务文件
└─ data/               # 运行时数据（gitignore）：app.db、media/
```

归属：`server/domain` 改动须评审；`server/adapters` 新增实现不改接口即可合入；`client` 自由度最高但不得内嵌业务规则。

## 运行环境

- 开发（唯一目标环境）：Windows 10/11，Python 3.12，Node 20+，ffmpeg 在 PATH 或设置指定
- 启动方式：`uvicorn server.api.main:app --port 8000` + `python -m server.worker` + `npm run dev`（client，5173）
- 生产模式（自用）：FastAPI 同时静态托管 `client/dist`，单端口 8000

## 基础设施

无外部中间件。SQLite WAL（连接建立时设 `busy_timeout=30000`，支持多 Worker 并发写）；
媒体目录按 project 分区；日志文件 `data/logs/`。

**Worker 并发模型（TASK-034）**：单件生图/生视频在平台侧要 200s~数分钟，单进程串行时
整批时间 = 件数 × 单件时间。任务认领是条件 UPDATE 抢占（`claim_next_runnable`），
因此可安全跑多进程：`python scripts/run_workers.py --count N`（池启动前跑一次遗留恢复，
池成员以 `--no-recover` 启动，避免互相打断在途任务）。RunningHub 账号并发额度占满时
任务创建被拒（TASK_QUEUE_MAXED）→ 固定长退避重试且不消耗重试预算（池大小略超额度
只表现为偶发重排）。可推进的 `running video_gen` 必须带 `provider_task_id`，
防止并发池下重复执行同一视频任务。

## 配置/环境变量

`.env`（gitignore）：
- `RUNNINGHUB_API_KEY`（视频+图片工作流共用，RunningHub 平台）
- `RUNNINGHUB_WORKFLOW_VIDEO` / `RUNNINGHUB_WORKFLOW_IMAGE`（工作流 ID，可换）
- `LLM_API_KEY`、`LLM_BASE_URL`（默认智谱 `https://open.bigmodel.cn/api/paas/v4`）、`LLM_MODEL`（默认 `glm-4.6`）`[AI-DECIDED]`
- `DATA_DIR`（默认 `./data`）

DB 设置（设置页可改，覆盖 env）：llm/image/video 各环节 provider+model、video 并发数（默认 1）、poll_interval_sec（默认 2s）、ffmpeg_path。

## 第三方依赖

后端：fastapi、uvicorn、sqlalchemy≥2、alembic、pydantic≥2、httpx、python-multipart、python-dotenv、pytest。
前端：react、react-dom、react-router-dom、@tanstack/react-query、zustand、tailwindcss、vite、typescript、vitest。
禁止引入：celery/redis、ORM 之外的 DB 层、重 UI 框架（antd 等与 Tailwind 混用）——如需变更先评审。

## 部署

本地运行，不做部署流水线。提供 `README.md` 一键启动说明（含 ffmpeg 安装提示：`winget install Gyan.FFmpeg`）。

## 可观测性

- 日志：Python logging → `data/logs/server.log`、`worker.log`（滚动）；API Key 一律脱敏（仅显示后 4 位）
- 任务日志：结构化事件写入 jobs.log（JSON 列），UI 任务中心展示
- 成本：provider_calls 表（FR-015）
- 指标/追踪/告警：非目标

## 安全

- 认证：无（仅绑定 127.0.0.1）
- 授权：非目标
- 密钥：.env + DB settings 存原文但 API 返回打码（`sk-***abcd`）；日志禁止输出密钥与完整 Authorization
- 数据保护：本地文件；归档项目提供"清理媒体"入口（显式确认才删）
