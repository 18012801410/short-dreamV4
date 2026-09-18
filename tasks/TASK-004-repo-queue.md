# 任务 TASK-004 — 仓储与 jobs 队列：表、Worker 轮询、依赖、重试、恢复

## 目标
落地持久化与任务系统：SQLAlchemy 全量表结构 + 迁移、Core 风格仓储、Worker 轮询引擎（依赖排序、退避重试、中断恢复）、FakeProvider 演示"入队→执行→成功/失败→重试→恢复"全链单测。

## 背景
M1 里程碑的最后一环（FakeProvider 全链可跑 = 证明架构成立）。依据：DOMAIN_MODEL.md「序列化」、ARCHITECTURE.md §3.4/§5、TECH_SPEC.md。

## 范围
- `server/infra/tables.py`：projects/script_versions/assets/asset_images/storyboard_versions/jobs/clips/films/provider_calls 十表 Core 定义（settings 已在 0001）。
- `migrations/versions/0002_domain_tables.py`：以 tables.metadata 为单一事实来源建表。
- `server/infra/repositories.py`：ProjectRepo / JobRepo / ClipRepo / ProviderCallRepo（领域模型 ↔ 行映射，JSON 列）；其余实体仓储随用例任务（008-012）补充。
- `server/worker/engine.py`：WorkerEngine——领取可运行 pending Job（依赖门 + next_attempt_at 退避）、执行 handler、终态落库；running video_gen 按 provider_task_id 推进；启动时恢复遗留 running Job。
- `server/adapters/fake.py`：FakeProvider + make_fake_handlers（可配置失败次数/轮询完成），演示与单测共用。
- `server/worker/__main__.py`：从心跳占位改为接入 WorkerEngine。

## 领域层补充（需评审确认）
- Job 实体新增持久化排程列 `next_attempt_at`（仅存在于 jobs 表行，不进领域模型）：退避重试的下一次可领取时间。ARCHITECTURE §8 允许重试退避参数级细节自行调整。
- WorkerEngine 的 handler 协议：`Handler = Callable[[Job], Outcome]`，Outcome ∈ succeeded/running(+provider_task_id)/failed(+JobError)；真实 Provider handler 在 TASK-005-007 接入。

## 非目标
- 真实 Provider handler（TASK-005/006/007）；REST 与用例编排（TASK-008 起）；Script/Asset/Storyboard/Film 仓储。

## 验收标准
- [ ] 十表可建（迁移升级/降级可执行）
- [ ] 依赖未满足的 Job 不会被领取；依赖全部 succeeded 后按 priority→created_at 领取
- [ ] 失败自动重试：同 Job 重置 pending，attempts 保留，退避时间递增；用尽后终态 failed
- [ ] Worker 重启：running video_gen 有 provider_task_id → 恢复推进；其余 → failed(INTERRUPTED) 且可重试
- [ ] FakeProvider 全链单测覆盖上述六种路径
- [ ] 领域层零改动仍通过（TASK-003 的 40 测试不回归）

## 验证
- [ ] `python -m ruff check server`
- [ ] `python -m pytest server/tests`
- [ ] `alembic upgrade head` + `alembic downgrade -1` + 再 `upgrade head` 往返
