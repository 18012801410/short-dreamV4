"""Project 状态机（TASK-003）：唯一合法迁移表 + 领域命令 + 下游失效回退。

规则来源：DOMAIN_MODEL.md「Project 状态机」。非法迁移一律抛 STATE_ILLEGAL；
流水线推进事件（*_generated/video_ready/composed/compose_failed）由应用层在
对应 Job 终态时触发，不是用户命令。
"""

from __future__ import annotations

from server.domain.entities import Project, utcnow
from server.domain.enums import ProjectStatus, Stage
from server.domain.errors import StateIllegalError

# 每个阶段：(drafting, ready, approved)。video/film 无人工批准门。
_STAGE_STATES: dict[Stage, tuple[ProjectStatus, ProjectStatus, ProjectStatus | None]] = {
    Stage.SCRIPT: (
        ProjectStatus.SCRIPT_DRAFTING,
        ProjectStatus.SCRIPT_READY,
        ProjectStatus.SCRIPT_APPROVED,
    ),
    Stage.ASSETS: (
        ProjectStatus.ASSET_DRAFTING,
        ProjectStatus.ASSET_READY,
        ProjectStatus.ASSET_APPROVED,
    ),
    Stage.STORYBOARD: (
        ProjectStatus.STORYBOARD_DRAFTING,
        ProjectStatus.STORYBOARD_READY,
        ProjectStatus.STORYBOARD_APPROVED,
    ),
    Stage.FRAME: (
        ProjectStatus.FRAME_DRAFTING,
        ProjectStatus.FRAME_READY,
        ProjectStatus.FRAME_APPROVED,
    ),
    Stage.VIDEO: (ProjectStatus.VIDEO_PRODUCING, ProjectStatus.VIDEO_READY, None),
    Stage.FILM: (ProjectStatus.COMPOSING, ProjectStatus.COMPOSED, None),
}

# 线性流水线顺序（ARCHIVED 不参与失效回退）
_PIPELINE_ORDER: list[ProjectStatus] = [
    ProjectStatus.CREATED,
    ProjectStatus.SCRIPT_DRAFTING,
    ProjectStatus.SCRIPT_READY,
    ProjectStatus.SCRIPT_APPROVED,
    ProjectStatus.ASSET_DRAFTING,
    ProjectStatus.ASSET_READY,
    ProjectStatus.ASSET_APPROVED,
    ProjectStatus.STORYBOARD_DRAFTING,
    ProjectStatus.STORYBOARD_READY,
    ProjectStatus.STORYBOARD_APPROVED,
    ProjectStatus.FRAME_DRAFTING,
    ProjectStatus.FRAME_READY,
    ProjectStatus.FRAME_APPROVED,
    ProjectStatus.VIDEO_PRODUCING,
    ProjectStatus.VIDEO_READY,
    ProjectStatus.COMPOSING,
    ProjectStatus.COMPOSED,
]

_TRANSITIONS: dict[str, dict[ProjectStatus, ProjectStatus]] = {
    # —— 用户命令（三确认门与生成入口）——
    # 生成类动作允许从自己的 drafting 态重入（TASK-032）：生成 Job 终态失败时
    # 项目停在 drafting 忙碌态，若不放开重入就永久卡死（「超神奶爸」实测——
    # 分镜 Job 连败 3 次后无法重新派发）。UI 在忙碌态禁用按钮，重入主要供
    # 失败恢复使用；precedent 见 produce_video / compose 的重入条目。
    "generate_script": {
        ProjectStatus.CREATED: ProjectStatus.SCRIPT_DRAFTING,
        ProjectStatus.SCRIPT_READY: ProjectStatus.SCRIPT_DRAFTING,
        ProjectStatus.SCRIPT_DRAFTING: ProjectStatus.SCRIPT_DRAFTING,
    },
    "approve_script": {ProjectStatus.SCRIPT_READY: ProjectStatus.SCRIPT_APPROVED},
    "generate_assets": {
        ProjectStatus.SCRIPT_APPROVED: ProjectStatus.ASSET_DRAFTING,
        ProjectStatus.ASSET_READY: ProjectStatus.ASSET_DRAFTING,
        ProjectStatus.ASSET_DRAFTING: ProjectStatus.ASSET_DRAFTING,
    },
    "approve_assets": {ProjectStatus.ASSET_READY: ProjectStatus.ASSET_APPROVED},
    "generate_storyboard": {
        ProjectStatus.ASSET_APPROVED: ProjectStatus.STORYBOARD_DRAFTING,
        ProjectStatus.STORYBOARD_READY: ProjectStatus.STORYBOARD_DRAFTING,
        ProjectStatus.STORYBOARD_DRAFTING: ProjectStatus.STORYBOARD_DRAFTING,
    },
    "approve_storyboard": {ProjectStatus.STORYBOARD_READY: ProjectStatus.STORYBOARD_APPROVED},
    # 关键帧阶段（TASK-031）：分镜确认后逐段生成开场锚点图，确认后才可产视频
    "generate_keyframes": {
        ProjectStatus.STORYBOARD_APPROVED: ProjectStatus.FRAME_DRAFTING,
        ProjectStatus.FRAME_READY: ProjectStatus.FRAME_DRAFTING,
        ProjectStatus.FRAME_DRAFTING: ProjectStatus.FRAME_DRAFTING,
    },
    "approve_keyframes": {ProjectStatus.FRAME_READY: ProjectStatus.FRAME_APPROVED},
    "produce_video": {
        ProjectStatus.FRAME_APPROVED: ProjectStatus.VIDEO_PRODUCING,
        # 重入：first_only 先行验收后续产其余段（本项目 TASK-029 实测需要）
        ProjectStatus.VIDEO_PRODUCING: ProjectStatus.VIDEO_PRODUCING,
    },
    "compose": {
        ProjectStatus.VIDEO_READY: ProjectStatus.COMPOSING,
        # 重新合成：段视频重生成/换新分镜版本后，允许对已成片项目再出新一版成片
        ProjectStatus.COMPOSED: ProjectStatus.COMPOSING,
    },
    # —— 流水线推进（Job 终态触发）——
    "script_generated": {ProjectStatus.SCRIPT_DRAFTING: ProjectStatus.SCRIPT_READY},
    "assets_generated": {ProjectStatus.ASSET_DRAFTING: ProjectStatus.ASSET_READY},
    "storyboard_generated": {ProjectStatus.STORYBOARD_DRAFTING: ProjectStatus.STORYBOARD_READY},
    "keyframes_generated": {ProjectStatus.FRAME_DRAFTING: ProjectStatus.FRAME_READY},
    "video_ready": {ProjectStatus.VIDEO_PRODUCING: ProjectStatus.VIDEO_READY},
    "composed": {ProjectStatus.COMPOSING: ProjectStatus.COMPOSED},
    "compose_failed": {ProjectStatus.COMPOSING: ProjectStatus.VIDEO_READY},
}
# archive：任意非归档状态 → ARCHIVED
_TRANSITIONS["archive"] = {s: ProjectStatus.ARCHIVED for s in _PIPELINE_ORDER}


def transition(current: ProjectStatus, action: str) -> ProjectStatus:
    """校验并返回目标状态；非法迁移抛 STATE_ILLEGAL。"""
    table = _TRANSITIONS.get(action)
    if table is None:
        raise StateIllegalError(f"未知动作：{action}")
    target = table.get(current)
    if target is None:
        raise StateIllegalError(
            f"动作 {action} 不允许在状态 {current.value} 执行",
            details={"current": current.value, "action": action},
        )
    return target


def allowed_actions(current: ProjectStatus) -> list[str]:
    return [a for a, table in _TRANSITIONS.items() if current in table]


def invalidate_stage(current: ProjectStatus, stage: Stage) -> ProjectStatus:
    """FR-016 下游失效：已确认工件被编辑，项目回退到该阶段 READY 态。

    允许从该阶段 approved 态或流水线上任何更晚的状态发起；
    上游不得对更早阶段"失效"（那不是失效，是非法回退）。
    """
    if current is ProjectStatus.ARCHIVED:
        raise StateIllegalError("已归档项目不可失效回退")
    approved = _STAGE_STATES[stage][2]
    ready = _STAGE_STATES[stage][1]
    if approved is None:
        raise StateIllegalError(f"阶段 {stage.value} 无确认门，不存在失效回退")
    cur_idx = _PIPELINE_ORDER.index(current)
    gate_idx = _PIPELINE_ORDER.index(approved)
    if cur_idx < gate_idx:
        raise StateIllegalError(
            f"阶段 {stage.value} 尚未确认（当前 {current.value}），无需失效回退"
        )
    return ready


def apply_action(project: Project, action: str) -> Project:
    """对 Project 执行领域命令，返回更新后的新实例（领域层不做持久化）。"""
    target = transition(project.status, action)
    return project.model_copy(update={"status": target, "updated_at": utcnow()})


def apply_stage_invalidated(project: Project, stage: Stage) -> Project:
    target = invalidate_stage(project.status, stage)
    return project.model_copy(update={"status": target, "updated_at": utcnow()})
