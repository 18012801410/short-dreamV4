# 任务 TASK-003 — 领域层：实体、状态机、不变量、错误

## 目标
实现 `server/domain/`：全部实体的 Pydantic 模型、Project 状态机、Job 状态机、不变量与错误类型。纯 Python + Pydantic（不 import FastAPI/SQLAlchemy/httpx），配套单测。

## 背景
DEVELOPMENT_PLAN 阶段 2 首个任务。状态机与不变量是三确认门流水线的地基，后续应用层（TASK-004/008-012）只调用本层，不重复实现规则。依据：DOMAIN_MODEL.md（唯一事实来源）、AI_SPEC 附录 A（H3 契约红线）、ARCHITECTURE.md §2/§5。

## 范围
- `server/domain/errors.py`：DomainError 基类 + STATE_ILLEGAL / VALIDATION_FAILED / REF_MISSING / DEPENDENCY_BLOCKED / JOB_NOT_RETRYABLE / ARTIFACT_IMMUTABLE。
- `server/domain/enums.py`：ProjectStatus（14 态 + ARCHIVED）、Stage、JobStatus、JobType、AssetKind、WorkStatus、AssetImageStatus、SegmentStatus、FilmStatus、VideoMode、PromptLang。
- `server/domain/entities.py`：ProjectParams、ScriptContent（Scene/DialogueLine/CharacterProfile）、Asset/AssetImage、StoryboardContent（Segment/Shot/Continuity/H3Prompt）、Clip、Film、ProviderCall、AppSettings、ResolvedReference；实体级不变量（段时长 [4,15]、切点单调、segment_key 一致性、镜号连续）。
- `server/domain/project.py`：合法迁移表（generate_script/approve_script/generate_assets/approve_assets/generate_storyboard/approve_storyboard/produce_video/compose/archive + 流水线推进 script_generated/assets_generated/storyboard_generated/video_ready/composed/compose_failed）+ invalidate_stage（FR-016 下游失效回退到该阶段 READY 态）。
- `server/domain/job.py`：Job 实体 + 状态机（依赖门 DEPENDENCY_BLOCKED、重试 failed→pending 仅限 attempts<max_attempts、succeeded 不可变、Worker 重启恢复规则）。
- `server/domain/validation.py`：分镜跨实体校验（资产引用存在、(kind,name) 唯一）、H3 提示词结构校验（≤7000、四段结构、[Shot k] 对齐、台词逐字进 <d> 块）、参考图 >9 按 角色>场景>道具 截断。

## 非目标
- 仓储/队列持久化（TASK-004）；模式矩阵与 resolve_references 用例（TASK-011）；REST 映射（TASK-008 起）。

## 需求
1. 领域层禁 import FastAPI/SQLAlchemy/httpx（ARCHITECTURE 不变量 2）。
2. 所有非法迁移/不变量破坏抛结构化领域错误，不返回 bool。
3. 时间戳一律 UTC；媒体路径只存 POSIX 相对路径字符串。

## 验收标准
- [ ] 合法全生命周期迁移：CREATED→…→COMPOSED 逐步可走通
- [ ] 非法迁移（如 SCRIPT_DRAFTING 再次 generate_script）抛 STATE_ILLEGAL
- [ ] 失效回退：VIDEO_READY 上 invalidate_script → SCRIPT_READY
- [ ] Job 依赖未满足抛 DEPENDENCY_BLOCKED；succeeded 重试抛 JOB_NOT_RETRYABLE
- [ ] 段时长越界/切点回退/键不一致在模型构造即被拒
- [ ] H3 提示词缺段落/台词不在 <d> 块/>7000 字符被 VALIDATION_FAILED 拒绝
- [ ] 10 张参考图按 角色>场景>道具 截断为 9 并记录 warning

## 验证
- [ ] `python -m ruff check server` 全绿
- [ ] `python -m pytest server/tests` 全绿（新增 ≥3 个测试文件）
