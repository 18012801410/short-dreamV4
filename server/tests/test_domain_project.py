"""Project 状态机单测（TASK-003）。"""

import pytest

from server.domain.enums import ProjectStatus, Stage
from server.domain.errors import StateIllegalError
from server.domain.project import (
    allowed_actions,
    apply_action,
    apply_stage_invalidated,
    invalidate_stage,
    transition,
)
from server.tests.factories import make_project

FULL_LIFECYCLE = [
    ("generate_script", ProjectStatus.SCRIPT_DRAFTING),
    ("script_generated", ProjectStatus.SCRIPT_READY),
    ("approve_script", ProjectStatus.SCRIPT_APPROVED),
    ("generate_assets", ProjectStatus.ASSET_DRAFTING),
    ("assets_generated", ProjectStatus.ASSET_READY),
    ("approve_assets", ProjectStatus.ASSET_APPROVED),
    ("generate_storyboard", ProjectStatus.STORYBOARD_DRAFTING),
    ("storyboard_generated", ProjectStatus.STORYBOARD_READY),
    ("approve_storyboard", ProjectStatus.STORYBOARD_APPROVED),
    ("generate_keyframes", ProjectStatus.FRAME_DRAFTING),
    ("keyframes_generated", ProjectStatus.FRAME_READY),
    ("approve_keyframes", ProjectStatus.FRAME_APPROVED),
    ("produce_video", ProjectStatus.VIDEO_PRODUCING),
    ("video_ready", ProjectStatus.VIDEO_READY),
    ("compose", ProjectStatus.COMPOSING),
    ("composed", ProjectStatus.COMPOSED),
    ("archive", ProjectStatus.ARCHIVED),
]


def test_full_lifecycle_walks_every_gate() -> None:
    project = make_project()
    for action, expected in FULL_LIFECYCLE:
        project = apply_action(project, action)
        assert project.status is expected


@pytest.mark.parametrize(
    ("action", "busy"),
    [
        ("generate_script", ProjectStatus.SCRIPT_DRAFTING),
        ("generate_assets", ProjectStatus.ASSET_DRAFTING),
        ("generate_storyboard", ProjectStatus.STORYBOARD_DRAFTING),
        ("generate_keyframes", ProjectStatus.FRAME_DRAFTING),
    ],
)
def test_generation_reentry_from_busy_state_for_failure_recovery(
    action: str, busy: ProjectStatus
) -> None:
    """TASK-032：生成类动作在自己的 drafting 忙碌态允许重入——否则生成 Job
    失败会把项目永久卡死在忙碌态（「超神奶爸」分镜连败后无法重派的实测卡死）。
    「重复派发」由应用层 dispatch 的任务护栏拦住（test_api 有专门用例）。"""
    project = make_project(status=busy)
    assert apply_action(project, action).status is busy


def test_generation_in_progress_blocks_gate_actions() -> None:
    """忙碌态仍挡住确认门（不变量保留：生成中不能确认）。"""
    project = apply_action(make_project(), "generate_script")
    with pytest.raises(StateIllegalError):
        apply_action(project, "approve_script")


def test_regenerate_from_ready_state() -> None:
    project = apply_action(make_project(), "generate_script")
    project = apply_action(project, "script_generated")
    assert apply_action(project, "generate_script").status is ProjectStatus.SCRIPT_DRAFTING


def test_gate_requires_ready_state() -> None:
    project = apply_action(make_project(), "generate_script")
    project = apply_action(project, "script_generated")
    assert apply_action(project, "approve_script").status is ProjectStatus.SCRIPT_APPROVED


def test_compose_failure_returns_to_video_ready() -> None:
    project = make_project()
    for action, _ in FULL_LIFECYCLE[:-3]:  # 走到 VIDEO_READY
        project = apply_action(project, action)
    project = apply_action(project, "compose")
    assert apply_action(project, "compose_failed").status is ProjectStatus.VIDEO_READY


def test_archive_from_any_state_and_terminal() -> None:
    assert apply_action(make_project(), "archive").status is ProjectStatus.ARCHIVED
    composed = make_project()
    for action, _ in FULL_LIFECYCLE[:-1]:
        composed = apply_action(composed, action)
    assert apply_action(composed, "archive").status is ProjectStatus.ARCHIVED


def test_archive_terminal_state_rejects_actions() -> None:
    project = apply_action(make_project(), "archive")
    for action in ("compose", "archive", "generate_script"):
        with pytest.raises(StateIllegalError):
            apply_action(project, action)


def test_transition_unknown_action() -> None:
    with pytest.raises(StateIllegalError):
        transition(ProjectStatus.CREATED, "fly_to_the_moon")


def test_allowed_actions_lists_user_commands() -> None:
    assert "generate_script" in allowed_actions(ProjectStatus.CREATED)
    assert "approve_assets" in allowed_actions(ProjectStatus.ASSET_READY)
    assert allowed_actions(ProjectStatus.ARCHIVED) == []


def test_invalidate_stage_rolls_back_to_stage_ready() -> None:
    project = make_project()
    for action, _ in FULL_LIFECYCLE[:-3]:  # 走到 VIDEO_READY
        project = apply_action(project, action)
    rolled = apply_stage_invalidated(project, Stage.SCRIPT)
    assert rolled.status is ProjectStatus.SCRIPT_READY
    assert apply_stage_invalidated(project, Stage.STORYBOARD).status is (
        ProjectStatus.STORYBOARD_READY
    )
    # 关键帧阶段失效回退（TASK-031）：分镜重生成连带 frame 门重过
    assert apply_stage_invalidated(project, Stage.FRAME).status is ProjectStatus.FRAME_READY


def test_invalidate_stage_rejects_unconfirmed_or_archived() -> None:
    with pytest.raises(StateIllegalError):
        invalidate_stage(ProjectStatus.SCRIPT_READY, Stage.SCRIPT)
    with pytest.raises(StateIllegalError):
        invalidate_stage(ProjectStatus.CREATED, Stage.ASSETS)
    with pytest.raises(StateIllegalError):
        invalidate_stage(ProjectStatus.ARCHIVED, Stage.SCRIPT)
