# 开发计划（Development Plan）—— short-dreamV4

> 任务文件在开工时于 `tasks/` 目录实例化（TASK-xxx.md），本表是唯一任务索引。

## 阶段 0 — Spike：供应商契约实测
状态：NOT_STARTED
- TASK-001 MiniMax 契约实测：image-01 生图参数 + H3 V2 单次端到端（文生视频 + 1 张参考图视频 + 轮询 + 下载 + ffmpeg 抽尾帧）。产出：`tasks/TASK-001` 报告 + AI_SPEC 附录 A 勘误（如有出入）。
验证：真实 API 各调通 1 次，产物落盘。

## 阶段 1 — 项目脚手架
状态：NOT_STARTED
- TASK-002 monorepo 脚手架：FastAPI 骨架（/api/system/health）、React+Vite 骨架、SQLite+Alembic、.env 加载、ffmpeg 检测、`data/` 目录布局。
验证：三进程可启动；health 返回 ffmpeg/providers 状态。

## 阶段 2 — 领域与任务系统
状态：DONE（TASK-003、TASK-004 已完成）
- TASK-003 领域层：实体 Pydantic 模型、Project 状态机、Job 状态机、不变量与错误类型（纯 Python + 单测）。【已完成 2026-09-12】
- TASK-004 仓储与 jobs 队列：SQLAlchemy 表、Worker 轮询循环、依赖排序、退避重试、中断恢复、FakeProvider 演示全链。【已完成 2026-09-12，9 项队列全链测试全绿，迁移 0002 往返通过】
验证：FakeProvider 跑通"入队→执行→成功/失败→重试→恢复"单测。✅

## 阶段 3 — 适配器
状态：DONE（TASK-005/006/007 已完成）
- TASK-005 LlmProvider（OpenAI 兼容）+ JSON mode 结构化输出 + 重试。【已完成 2026-09-13，DeepSeek 真实冒烟通过】
- TASK-006 ImageProvider（RunningHub QWEN 文生图工作流，见 AI_SPEC 附录 B）+ 媒体落地。【已完成 2026-09-13，7 项单测 + 真实冒烟通过（23 币/113s）】
- TASK-007 VideoProvider（RunningHub H3 导演台：单段 timeline 组装 + 提交/轮询/下载/probe）+ MediaUploader（并入 RunningHubClient）+ FFmpegService（probe/尾帧/concat）。【已完成 2026-09-13，10 项单测 + 真实冒烟通过（10.125s/1088p/有音轨）】
验证：契约合规单测（三段/六段模式分支）；TASK-001 已完成真实调用冒烟（附录 B 实测记录）。✅

## 阶段 4 — 用例与流水线
状态：DONE（2026-09-13，TASK-008-012）
- 用例编排（server/app/usecases.py，17 个领域命令）+ 三个 Agent（agents.py）+ Worker handlers（handlers.py）+ REST 全端点（routes_workbench.py，含错误信封）+ 设置存储。
- 验证：FakeProvider 全链集成测试（建项目→剧本→资产→分镜→视频依赖链→合成→COMPOSED）+ REST 冒烟，后端 100 测试全绿。✅

## 阶段 5 — 工作台 UI
状态：DONE（2026-09-13，TASK-013-016）
- 三栏工作台（StageNav 管线导航 / 五 Tab 工作区 / 任务中心）、确认门与一键连跑、剧本草稿编辑、资产卡片墙（逐图批准/上传/重生成）、段表格 + H3 提示词编辑器（≤7000 计数）、视频墙（首段试跑/单段重生成/尾帧标注）、成片播放与历史版本、设置页（key 打码 + 测试连接）。
- 验证：tsc 零错误、vite build 通过、vitest 14 项（流水线推导纯函数 + StageNav 组件）。✅

## 阶段 6 — 端到端与打磨
状态：DONE（2026-09-13，TASK-017/018；段数 7 距 ≥8 目标差 1，见 PROJECT_STATE 已知问题）
- TASK-017 真实端到端：scripts/run_e2e.py + resume_e2e.py——「渡口夜行」想法 → 9 资产 16 图 → 7 段视频（分镜确定性校验首败后退避重试成功）→ 合成成片 70.9s（1920×1088、含音轨、Σ误差 0.03s），总耗 607 RH 币，视频生产 31 分钟。
- TASK-018：README 启动文档、错误文案人话化（任务中心 provider_code 提示）、成本可见（provider_calls 按币记录 + 任务中心展示）。✅

## 当前里程碑
M1（阶段 0-2）：FakeProvider 全链可跑 —— 证明架构成立。
M2（阶段 3-4）：真实供应商单环节可跑。
M3（阶段 5）：工作台可操作全流程。
M4（阶段 6）：真实想法 → 成片交付。

## 下一步任务
1. TASK-001（Spike：MiniMax 契约实测）
2. TASK-002（脚手架，可与 TASK-001 并行）
