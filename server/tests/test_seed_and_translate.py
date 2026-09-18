"""图片种子随机化 + 中文提示词翻译测试（TASK-048 用户反馈）。

- 不传种子重新生成会出一模一样的图（工作流保存的固定种子）→ 未显式给
  种子时后端必须随机化；显式 seed 仍可锁定复现。
- translate_prompt：中文画面描述 → 英文提示词（LLM 免费，注入假 LLM 验证）。
"""

import pytest

from server.app.usecases import WorkbenchService
from server.domain.entities import Asset, AssetImage
from server.domain.errors import ValidationFailedError
from server.infra.config import Settings
from server.infra.tables import metadata
from server.tests.test_series import make_ctx


def make_service(tmp_path):
    _, ctx = make_ctx(tmp_path)
    return WorkbenchService(ctx), ctx


def test_asset_image_seed_randomized_when_absent(tmp_path) -> None:
    svc, ctx = make_service(tmp_path)
    project = svc.create_project("渡口", "想法", {})
    asset = Asset(asset_id="asset-1", project_id=project.project_id, kind="character", name="陈平")
    ctx.assets.add_asset(asset)

    result = svc.dispatch(project.project_id, "generate_asset_image", {"asset_id": "asset-1"})
    seed_a = result["jobs"][0].payload.get("seed")
    result = svc.dispatch(project.project_id, "generate_asset_image", {"asset_id": "asset-1"})
    seed_b = result["jobs"][0].payload.get("seed")

    assert isinstance(seed_a, int) and seed_a >= 0
    assert isinstance(seed_b, int) and seed_b >= 0
    assert seed_a != seed_b  # 2^31 空间，随机撞 Seed 概率可忽略

    # 显式 seed 仍可锁定（复现排查用）
    result = svc.dispatch(
        project.project_id, "generate_asset_image", {"asset_id": "asset-1", "seed": 42}
    )
    assert result["jobs"][0].payload["seed"] == 42


def test_frame_job_seed_randomized_when_absent(tmp_path) -> None:
    """cmd_generate_keyframes 入队的 frame_gen 任务必须带随机种子。"""
    svc, ctx = make_service(tmp_path)
    project = svc.create_project("渡口", "想法", {})
    # 直接造一段（绕过分镜链路，只验证入队 payload 的种子）
    from server.domain.entities import (
        H3Prompt,
        Scene,
        ScriptContent,
        Segment,
        Shot,
        StoryboardContent,
        StoryboardVersion,
    )
    from server.domain.enums import WorkStatus

    segment = Segment(
        segment_key="S01G01",
        scene_id="S1",
        index=1,
        duration_sec=5,
        shots=[Shot(shot_no=1, cutpoint_sec=0.0, description="test shot")],
        h3_prompt=H3Prompt(text=""),
    )
    version = StoryboardVersion(
        storyboard_version_id="sbv-1",
        project_id=project.project_id,
        version_no=1,
        content=StoryboardContent(segments=[segment]),
        status=WorkStatus.ACTIVE,
    )
    ctx.storyboards.add(version)
    # 直接置为目标状态（本测试只关心 frame_gen 入队 payload 的种子）
    from server.domain.enums import ProjectStatus

    ctx.projects.save(project.model_copy(update={"status": ProjectStatus.STORYBOARD_APPROVED}))

    result = svc.dispatch(project.project_id, "generate_keyframes")
    seeds = [job.payload.get("seed") for job in result["jobs"]]
    assert seeds and all(isinstance(s, int) and s >= 0 for s in seeds)


def test_translate_prompt(tmp_path) -> None:
    svc, ctx = make_service(tmp_path)
    project = svc.create_project("渡口", "想法", {})

    class FakeChatLlm:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def chat(self, *, system, user, temperature):
            self.calls.append({"system": system, "user": user, "temperature": temperature})
            return '"a weathered old ferryman in a dark oilskin coat, standing on a night pier, cinematic"'

        def chat_json(self, **kwargs):  # pragma: no cover
            raise AssertionError("翻译应走 chat 而不是 chat_json")

    fake = FakeChatLlm()
    svc._prompt_llm = fake

    prompt = svc.translate_prompt(
        project.project_id, "夜色渡口，穿深色油布雨衣的老船工站在木栈桥上回头"
    )
    # 剥掉了引号；system 含 Qwen-Image 官方公式 + 画面内文字保留中文的规则；user 带中文原文
    assert prompt.startswith("a weathered old ferryman")
    assert "Qwen-Image" in fake.calls[0]["system"]
    assert "主体" in fake.calls[0]["system"]  # 官方公式：主体→场景→风格→镜头语言
    assert "中文原文" in fake.calls[0]["system"]
    assert "绝不翻译" in fake.calls[0]["system"]
    assert "夜色渡口" in fake.calls[0]["user"]

    # edit 模式：Qwen-Image-Edit 编辑指令规则（动词开头 + 保留其余不变）
    prompt_edit = svc.translate_prompt(project.project_id, "把背景换成雪夜", mode="edit")
    assert prompt_edit.startswith("a weathered old ferryman")  # 假 LLM 返回值，仅验证通路
    assert "编辑动词" in fake.calls[-1]["system"]
    assert "Keep everything else" in fake.calls[-1]["system"]

    # 智能修改模式：带 current_prompt 时走融合分支（system 换成编辑助手规则）
    svc.translate_prompt(
        project.project_id,
        "场景中有2个桌子",
        current_prompt="a banquet hall with a single round table, warm yellow light",
    )
    merge_call = fake.calls[-1]
    assert "提示词编辑助手" in merge_call["system"]
    assert "逐字保留" in merge_call["system"]
    assert "a banquet hall with a single round table" in merge_call["user"]
    assert "场景中有2个桌子" in merge_call["user"]

    with pytest.raises(ValidationFailedError):
        svc.translate_prompt(project.project_id, "   ")
