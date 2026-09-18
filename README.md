# short-dreamV4

个人自用的 AI 短视频生产台：输入一句话想法，沿「**剧本 → 资产 → 分镜 → 关键帧 → 视频 → 成片**」流水线自动生成中间工件，每个阶段可审阅、编辑、重新生成，人工确认后推进。

- 剧本/资产/分镜由 LLM（OpenAI 兼容接口，已适配 DeepSeek/智谱 GLM）生成
- 资产设定图由 RunningHub「QWEN 文生图」工作流生成
- 段视频由 RunningHub「MiniMax H3 导演台（ref2va）」工作流生成，**自带台词音轨**
- 提示词遵循 MiniMax H3 官方 dialect（三段/六段结构，见 `AI_SPEC.md`）
- 成片由 ffmpeg 拼接（含音轨）

## 环境要求

- Windows 10/11（其他平台未验证），Python 3.12+，Node 20+
- ffmpeg / ffprobe 在 PATH（`winget install Gyan.FFmpeg`）
- API Key：
  - `RUNNINGHUB_API_KEY`（RunningHub 开放平台，生图+视频共用，按量计费）
  - `LLM_API_KEY` + `LLM_BASE_URL` + `LLM_MODEL`（任一 OpenAI 兼容服务）

## 快速开始

```bash
# 1. 配置
copy .env.example .env   # 填入 RUNNINGHUB_API_KEY 与 LLM 三项

# 2. 后端（首次自动建库：data/app.db）
pip install -r requirements.txt
alembic upgrade head
uvicorn server.api.main:app --port 8000

# 3. Worker（任务执行，独立进程）
#    并发池：生图/生视频单件在平台侧要 200s~数分钟，池化后整批时间 ≈ 件数/并发数 × 单件时间
python scripts/run_workers.py --count 3
#    单进程（含启动恢复）：python -m server.worker
#    池状态 / 停止：python scripts/run_workers.py --status / --stop

# 4. 前端
cd client && npm install && npm run dev   # http://localhost:5173
```

生产模式：`cd client && npm run build` 后仅运行 uvicorn（8000 端口同时托管前端与 `/media`）。

并发 Worker（TASK-034）：任务认领是条件 UPDATE 抢占，多进程安全；池成员以 `--no-recover`
启动，启动恢复只由 `run_workers.py` 在池启动前跑一次——否则新起的 Worker 会把其他进程的
在途任务判为中断并重排（实测白费一张图）。RunningHub 账号并发额度占满时任务创建被拒
（TASK_QUEUE_MAXED），引擎按固定长退避重试且**不消耗重试预算**，因此池大小略超额度只会
偶发重排，不会失败。

## 使用流程

1. 项目列表 → 新建项目（标题 + 一句话想法 + 剧作基调 + 参数）
2. 工作台内逐阶段：
   - **剧本**：生成 → 预览/编辑草稿 → 确认
   - **资产**：抽取角色/场景/道具 → 逐图生成/上传 → 批准 → 确认（角色与场景须各有 ≥1 张批准图）
   - **分镜**：生成段/镜头/H3 提示词 → 编辑（含六段提示词编辑器与 7000 字符计数）→ 确认
   - **关键帧**：逐段生成开场锚点图 → 逐图批准/上传 → 确认（已批准关键帧作该段视频开场参考，替代尾帧接力）
   - **视频**：首段试跑验证风格 → 生成全部段 → 单段重生成
   - **成片**：合成 → 播放/下载/历史版本
3. 顶栏「生成剧本/抽取资产/…」按钮等价于一键执行下一个自动步骤；确认门永远人工操作

## 成本提示

- 视频按 RunningHub 实际 GPU 用量计费（实测同规格 10s 段 51~111 RH 币波动），任务中心与 `provider_calls` 表记录每笔实际消耗
- 任务失败自动退避重试（≤3 次）；「重新生成」创建新任务，旧产物保留，不会对已成功任务重复计费

## 仓库结构

```
├─ AI_SPEC.md / ARCHITECTURE.md / DOMAIN_MODEL.md / API_SPEC.md / UI_SPEC.md / TECH_SPEC.md
│   ← 规格说明包（唯一事实来源）；AI_SPEC 附录 B 为 RunningHub 契约实测记录
├─ server/
│  ├─ domain/      # 实体、Project/Job 状态机、H3 提示词校验（纯 Python，禁框架依赖）
│  ├─ app/         # 用例编排、三个 Agent（剧本/资产/分镜）、Worker handlers
│  ├─ adapters/    # RunningHub 客户端、Llm/Image/Video Provider、ffmpeg 服务、FakeProvider
│  ├─ infra/       # SQLite(WAL) + Core 仓储 + Alembic
│  ├─ api/         # FastAPI 路由 + 错误信封
│  └─ worker/      # 轮询引擎：依赖排序、指数退避、中断恢复
├─ client/         # React 18 + Vite + TanStack Query + Tailwind（深色三栏工作台）
├─ workerflow/     # RunningHub 工作流 JSON（视频/图片各一，含输入节点说明）
├─ spike/          # 契约实测脚本（TASK-001，保留备查）
└─ scripts/        # run_e2e.py 真实端到端 / resume_e2e.py 断点续跑
```

## 测试

```bash
python -m ruff check server spike
python -m pytest server/tests          # 后端 100+ 项（FakeProvider 全链）
cd client && npx vitest run            # 前端（流水线状态推导 + StageNav 组件）
```

## 已知边界

- H3 导演台工作流仅接了 reference（r2v）模式；纯文生（t2v）的 task_type 字符串未实测，无参考图的段会显式报 `UNSUPPORTED_MODE`
- 首帧/首尾帧三段模式仍未接（关键帧走 reference 模式的 `<Picture 1>` 槽位，TASK-031）；「起始帧」独立槽位建模保留
- Worker 为单进程串行消费（视频并发 1）；多任务并行属增量变更
