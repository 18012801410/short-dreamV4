"""阶段 4 集成测试（TASK-008-012）：用例编排 + 队列 + 假 Provider 全链走通。

覆盖：建项目 → 生成剧本 → 确认 → 资产（含图）→ 确认 → 分镜 → 确认 →
produce_video（连续性依赖链）→ compose → COMPOSED；外加失效回退与 REF_MISSING 分支。
ffmpeg 缺失时跳过（尾帧/concat 是流程必需）。
"""

import shutil
from pathlib import Path

import pytest

from server.adapters.ffmpeg_svc import FFmpegService
from server.app.agents import Agents
from server.app.context import AppContext
from server.app.handlers import build_handlers
from server.app.usecases import WorkbenchService
from server.domain.entities import JobType
from server.domain.enums import ProjectStatus
from server.infra.config import Settings
from server.infra.tables import metadata
from server.tests.factories import make_segment

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --------------------------------------------------------------------------
# 假 Provider
# --------------------------------------------------------------------------


class FakeResult:
    def __init__(self, data) -> None:
        self.data = data
        from server.domain.providers import LlmUsage

        self.usage = LlmUsage(total_tokens=42)
        self.attempts = 1


class FakeLlm:
    """按 Agent 类型脚本化返回（借 system 前缀区分调用来源）。"""

    def __init__(self, script_content, asset_plan, storyboard_builder) -> None:
        self.script_content = script_content
        self.asset_plan = asset_plan
        self.storyboard_builder = storyboard_builder
        self.calls: list[str] = []

    def chat_json(self, *, system: str, user: str, schema, temperature: float):
        if "短剧编剧" in system:
            self.calls.append("script")
            return FakeResult(self.script_content)
        if "视觉设定师" in system:
            self.calls.append("assets")
            return FakeResult(self.asset_plan)
        if "分镜师" in system:
            import json as _json

            # 重试时 user = JSON + 校验反馈后缀，只解析 JSON 部分；
            # 分场景生成：按 payload 里的场景过滤出该场景的段
            payload = user.split("\n\n上一次输出的以下确定性校验未通过")[0]
            body = _json.loads(payload)
            known = {
                a["asset_id"]
                for a in body.get("assets", [])
            }
            # 只挑角色资产：段内 asset_refs 决定 <Picture N> 编号与
            # subject_placements 的角色对账，挑错 kind 会随机闪失败
            character_ids = {
                a["asset_id"]
                for a in body.get("assets", [])
                if a.get("kind") == "character"
            }
            scene_ids = {
                s["id"] for s in body.get("script", {}).get("scenes", [])
            }
            self.calls.append("storyboard")
            sb = self.storyboard_builder(character_ids or known)
            kept = [s for s in sb.segments if s.scene_id in scene_ids]
            from server.domain.entities import StoryboardContent

            return FakeResult(StoryboardContent(segments=kept))
        raise AssertionError("未预期的 LLM 调用")

    def chat(self, **kwargs):
        return "pong"


class FakeImage:
    def generate(self, *, prompt, width, height, dest, seed=None, negative=None,
                 reference_files=None):
        from server.domain.providers import GeneratedImage, MediaUsage

        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake-png")
        return GeneratedImage(
            provider_task_id="img-1",
            seed=7,
            file_url="https://rh.test/x.png",
            dest=Path(dest),
            size_bytes=8,
            usage=MediaUsage(coins="1", task_seconds="2"),
        )


def make_fake_video(real_clip: Path) -> object:
    from server.domain.providers import GeneratedVideo, MediaUsage

    class FakeVideo:
        def generate(
            self,
            *, prompt, duration_sec, reference_files, dest,
            seed=None, reference_video=None, ratio="",
        ):

            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(real_clip, dest)
            return GeneratedVideo(
                provider_task_id="vid-1",
                file_url="https://rh.test/x.mp4",
                dest=Path(dest),
                size_bytes=real_clip.stat().st_size,
                duration_sec=duration_sec + 0.125,
                width=1920,
                height=1088,
                has_audio=True,
                usage=MediaUsage(coins="5", task_seconds="30"),
            )

    return FakeVideo()


# --------------------------------------------------------------------------
# 夹具：合法的剧本 / 资产计划 / 分镜（六段式提示词，与 shots 切点一致）
# --------------------------------------------------------------------------


def make_script_content():
    from server.domain.entities import ScriptContent

    return ScriptContent.model_validate({
        "logline": "老船工最后的摆渡",
        "scenes": [
            {"id": "S1", "title": "渡口", "summary": "夜色渡口", "est_seconds": 8,
             "dialogues": [{"speaker": "老船工", "line": "上船吧。"}]},
            {"id": "S2", "title": "河心", "summary": "船到河心", "est_seconds": 4,
             "dialogues": [{"speaker": "老船工", "line": "河记得一切。"}]},
        ],
        "characters": [{"name": "老船工", "profile": "沉默的摆渡人"}],
        "props": ["船桨"],
    })


def make_asset_plan():
    from server.app.agents import AssetExtraction

    return AssetExtraction.model_validate({
        "assets": [
            {"kind": "character", "name": "老船工", "description": "主角",
             "visual_anchor": "灰白山羊胡、深色油皮外套、斗笠",
             "image_plan": [
                 {"view_label": "主设定",
                  "image_prompt": "old ferryman character sheet, oilskin coat, straw hat"}
             ]},
            {"kind": "scene", "name": "夜色渡口", "description": "木栈道渡口",
             "visual_anchor": "冷灰绿夜色、木桩、缆绳",
             "image_plan": [
                 {"view_label": "空镜",
                  "image_prompt": "night wooden pier, cold gray-green palette, empty"}
             ]},
        ]
    })


def six_section_prompt(
    shot2_cut: float | None,
    *,
    continuity: bool,
    shot1_line: str = "",
    shot2_line: str = "河记得一切。",
) -> str:
    shots = "[Shot 1] Cold night pier. The ferryman unties the rope."
    if shot1_line:
        shots += (
            " The old ferryman says, his lips moving slowly as he speaks, "
            f"<d>[中文] {shot1_line}</d>"
        )
    if shot2_cut is not None:
        shots += f" [Shot 2] At 00:0{int(shot2_cut)}.000, he pushes the boat off."
        if shot2_line:
            shots += (
                " He says, his lips moving as he speaks, "
                f"<d>[中文] {shot2_line}</d>"
            )
    if continuity:
        # 连续段：前段尾帧固定 <Picture 1>（0.00s 开场），资产图从 <Picture 2> 起
        subjects = (
            "subject_definitions: <Picture 1> is the opening frame continuing seamlessly "
            "from the previous segment, showing the pier at night. "
            "<Subject 1> is the old ferryman in <Picture 2>, with a "
            "gray goatee and dark oilskin coat.\n"
        )
        retention = (
            "retention_analysis: <Picture 1> (appears in [Shot 1]): "
            "fully_preserved - opening composition from the previous segment.\n"
            "<Picture 2> (appears in [Shot 1], [Shot 2]): "
            "fully_preserved - pier framing and identity.\n"
        )
    else:
        subjects = (
            "subject_definitions: <Subject 1> is the old ferryman in <Picture 1>, with a "
            "gray goatee and dark oilskin coat. <Picture 1> is the opening composition.\n"
        )
        retention = (
            "retention_analysis: <Picture 1> (appears in [Shot 1], [Shot 2]): "
            "fully_preserved - pier framing and identity.\n"
        )
    return (
        subjects
        + "summary: [reference generation] The ferryman pushes off and speaks.\n"
        + retention
        + f"detailed_description: {shots}\n"
        + "overall_soundscape: Water lapping, rope creaking.\n"
        + "non_diegetic_music: Sparse low strings."
    )


def make_storyboard_content(
    known_asset_ids: set[str], shot1_line: str = "上船吧。"
):
    # S02G02 的 shot2 无台词（台词「河记得一切。」只分配给 S01G02）
    from server.domain.entities import StoryboardContent

    asset_id = sorted(known_asset_ids)[0]

    def seg(key, scene, index, dur, with_shot2, prev=None, cut2=None,
            shot2_line="河记得一切。", shot1_dlg=None, shot1_anchor=""):
        shot2 = {"shot_no": 2, "cutpoint_sec": cut2, "camera": "push in",
                 "description": "pushes off",
                 "dialogue_refs": (
                     [{"speaker": "老船工", "line": shot2_line}] if shot2_line else []
                 )}
        return {
            "segment_key": key,
            "scene_id": scene,
            "index": index,
            "duration_sec": dur,
            "shots": [
                {"shot_no": 1, "cutpoint_sec": 0.0, "camera": "static shot",
                 "description": "at the pier",
                 "dialogue_refs": [shot1_dlg] if shot1_dlg else []},
                *([shot2] if with_shot2 else []),
            ],
            "asset_refs": [{"asset_id": asset_id, "usage_note": "主角参考"}],
            "subject_placements": [
                {"name": "老船工",
                 "placement": "standing at the wooden pier, holding the mooring rope"}
            ],
            "continuity": {"enabled": prev is not None, "with_prev_segment_key": prev or ""},
            "h3_prompt": {
                "text": six_section_prompt(
                    cut2 if with_shot2 else None,
                    continuity=prev is not None,
                    shot1_line=shot1_anchor,
                    shot2_line=shot2_line if with_shot2 else "",
                ),
                "lang": "en",
            },
        }

    # 台词逐字对账（分场景）：S1 = 上船吧；S2 = 河记得一切
    s1 = seg("S01G01", "S1", 1, 4, False,
             shot1_dlg={"speaker": "老船工", "line": shot1_line}, shot1_anchor=shot1_line)
    s2 = seg("S01G02", "S1", 2, 4, True, prev="S01G01", cut2=2.0, shot2_line="")
    s3 = seg("S02G01", "S2", 1, 4, False,
             shot1_dlg={"speaker": "老船工", "line": "河记得一切。"},
             shot1_anchor="河记得一切。")
    return StoryboardContent.model_validate({"segments": [s1, s2, s3]})


FIRST_ASSET_ID = "asset-fake-1"


# --------------------------------------------------------------------------
# 全链测试
# --------------------------------------------------------------------------


@pytest.fixture()
def flow(tmp_path):
    if not FFMPEG_AVAILABLE:
        pytest.skip("需要 ffmpeg")
    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    import sqlalchemy as sa

    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'flow.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)

    # 真实测试视频（1s，带音轨），供假 VideoProvider 拷贝与尾帧/concat 使用
    real_clip = tmp_path / "seed_clip.mp4"
    result = subprocess_run_lavfi(real_clip)
    assert result == 0, "测试视频生成失败"

    agents = Agents(FakeLlm(
        make_script_content(), make_asset_plan(),
        lambda ids: make_storyboard_content(ids),
    ))
    handlers = build_handlers(
        ctx, agents, image=FakeImage(), video=make_fake_video(real_clip),
        ffmpeg=FFmpegService(),
    )
    from server.worker.engine import EngineConfig, WorkerEngine

    engine_worker = WorkerEngine(
        ctx.jobs, handlers, EngineConfig(poll_interval_sec=0.01, retry_backoff_base_sec=0.01)
    )
    svc = WorkbenchService(ctx)
    return svc, engine_worker, ctx


def subprocess_run_lavfi(dest: Path) -> int:
    import subprocess

    return subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=320x320:d=1:r=24",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(dest),
        ],
        capture_output=True, timeout=60,
    ).returncode


def run_until_idle(worker) -> None:
    for _ in range(30):
        if not worker.run_once():
            break


def test_full_pipeline_to_composed(flow) -> None:
    svc, worker, ctx = flow
    project = svc.create_project(
        "渡口夜行", "老船工最后的摆渡",
        {"scene_count": 2, "ratio": "16:9", "target_duration_sec": 12},
    )

    # 剧本
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    assert ctx.projects.get(project.project_id).status is ProjectStatus.SCRIPT_READY
    svc.dispatch(project.project_id, "approve_script")

    # 资产：asset_extract 生成资产与图片任务，图片任务同批内被消费
    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)
    assert ctx.projects.get(project.project_id).status is ProjectStatus.ASSET_READY
    assets = ctx.assets.list_assets(project.project_id)
    assert {a.kind.value for a in assets} == {"character", "scene"}
    images = ctx.assets.list_images(project_id=project.project_id)
    assert images and all(i.status.value == "ready" for i in images)
    for image in images:  # 资产图逐图批准（人工门）
        svc.dispatch(project.project_id, "approve_asset_image",
                     {"asset_image_id": image.asset_image_id, "approved": True})
    svc.dispatch(project.project_id, "approve_assets")
    assert ctx.projects.get(project.project_id).status is ProjectStatus.ASSET_APPROVED

    # 分镜
    svc.dispatch(project.project_id, "generate_storyboard")
    run_until_idle(worker)
    assert ctx.projects.get(project.project_id).status is ProjectStatus.STORYBOARD_READY
    svc.dispatch(project.project_id, "approve_storyboard")

    # 关键帧（TASK-031）：生成 → 确认门。本测显式走「缺帧回退尾帧接力」分支，
    # 保留连续性依赖链断言；关键帧优先分支见 test_keyframe_priority_over_tail_frame
    svc.dispatch(project.project_id, "generate_keyframes")
    run_until_idle(worker)
    assert ctx.projects.get(project.project_id).status is ProjectStatus.FRAME_READY
    frames = ctx.frames.list_by_project(project.project_id)
    assert len(frames) == 3 and all(f.status.value == "ready" for f in frames)
    # 未批准时确认门必须拦截（不静默降级）
    from server.domain.errors import ReferenceMissingError

    with pytest.raises(ReferenceMissingError):
        svc.dispatch(project.project_id, "approve_keyframes", {})
    svc.dispatch(project.project_id, "approve_keyframes", {"allow_tail_fallback": True})
    assert ctx.projects.get(project.project_id).status is ProjectStatus.FRAME_APPROVED

    # 视频：连续性依赖链（S01G02 依赖 S01G01 的 job）
    svc.dispatch(project.project_id, "produce_video", {"scope": "all"})
    video_jobs = [j for j in ctx.jobs.list_all() if j.type is JobType.VIDEO_GEN]
    assert len(video_jobs) == 3
    seg2 = next(j for j in video_jobs if j.input_snapshot["segment_key"] == "S01G02")
    seg1 = next(j for j in video_jobs if j.input_snapshot["segment_key"] == "S01G01")
    assert seg2.depends_on == [seg1.job_id]
    assert seg2.input_snapshot["continuity_prev_segment_key"] == "S01G01"
    assert seg1.input_snapshot["mode"] == "r2va"
    # 尾帧回退分支：有前段的连续段开场锚点来源 = 尾帧接力；首段为 None
    assert all(
        j.input_snapshot["opening_frame_source"] == (
            "tail_frame" if j.input_snapshot["continuity_prev_segment_key"] else None
        )
        for j in video_jobs
    )

    run_until_idle(worker)
    assert ctx.projects.get(project.project_id).status is ProjectStatus.VIDEO_READY
    # 尾帧已抽取
    clips = ctx.clips.list_by_project(project.project_id)
    assert len(clips) == 3 and all(c.tail_frame_path for c in clips)
    for c in clips:
        assert (Path(ctx.settings.media_dir) / c.tail_frame_path).exists()

    # 合成
    svc.dispatch(project.project_id, "compose")
    worker.run_once()
    assert ctx.projects.get(project.project_id).status is ProjectStatus.COMPOSED
    films = ctx.films.list_by_project(project.project_id)
    assert films and films[0].status.value == "ready"
    assert (Path(ctx.settings.media_dir) / films[0].file_path).exists()
    # 字幕版：生成成功则文件在盘（ffmpeg 缺 libass 时留空不阻断）
    if films[0].subtitle_path:
        assert (Path(ctx.settings.media_dir) / films[0].subtitle_path).exists()


def test_approve_assets_gate_blocks_without_approved_images(flow) -> None:
    from server.domain.errors import ReferenceMissingError

    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)
    with pytest.raises(ReferenceMissingError):
        svc.dispatch(project.project_id, "approve_assets")


def test_produce_video_ref_missing_blocks(flow) -> None:
    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})
    # 直接把项目推到 STORYBOARD_APPROVED（用真实命令链，但把资产图未批准）
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)
    for image in ctx.assets.list_images(project_id=project.project_id):
        svc.dispatch(project.project_id, "approve_asset_image",
                     {"asset_image_id": image.asset_image_id, "approved": True})
    svc.dispatch(project.project_id, "approve_assets")
    svc.dispatch(project.project_id, "generate_storyboard")
    run_until_idle(worker)
    svc.dispatch(project.project_id, "approve_storyboard")
    # 过关键帧门（显式回退尾帧）才能到 produce_video 前置状态 FRAME_APPROVED
    svc.dispatch(project.project_id, "generate_keyframes")
    run_until_idle(worker)
    svc.dispatch(project.project_id, "approve_keyframes", {"allow_tail_fallback": True})
    # 撤销批准 → produce_video 应 REF_MISSING
    for image in ctx.assets.list_images(project_id=project.project_id):
        image.approved = False
        ctx.assets.save_image(image)
    from server.domain.errors import ReferenceMissingError

    with pytest.raises(ReferenceMissingError):
        svc.dispatch(project.project_id, "produce_video", {"scope": "all"})


def test_keyframe_priority_over_tail_frame(flow) -> None:
    """TASK-046 衔接修复：关键帧只作**场景首段**的 <Picture 1>（定开场构图）；
    同场景后继段改用尾帧接力续接前段（continuity_prev 指向前段），
    消灭"每段从静帧冷启动"造成的跳切。跨场景首段仍用关键帧定场景。"""
    svc, worker, ctx = flow
    project = svc.create_project(
        "渡口夜行", "老船工最后的摆渡",
        {"scene_count": 2, "ratio": "16:9", "target_duration_sec": 12},
    )
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)
    for image in ctx.assets.list_images(project_id=project.project_id):
        svc.dispatch(project.project_id, "approve_asset_image",
                     {"asset_image_id": image.asset_image_id, "approved": True})
    svc.dispatch(project.project_id, "approve_assets")
    svc.dispatch(project.project_id, "generate_storyboard")
    run_until_idle(worker)
    svc.dispatch(project.project_id, "approve_storyboard")
    svc.dispatch(project.project_id, "generate_keyframes")
    run_until_idle(worker)
    for frame in ctx.frames.list_by_project(project.project_id):
        svc.dispatch(project.project_id, "approve_frame_image",
                     {"frame_image_id": frame.frame_image_id, "approved": True})
    result = svc.dispatch(project.project_id, "approve_keyframes", {})
    assert result["project"].status is ProjectStatus.FRAME_APPROVED

    svc.dispatch(project.project_id, "produce_video", {"scope": "all"})
    video_jobs = [j for j in ctx.jobs.list_all() if j.type is JobType.VIDEO_GEN]
    assert len(video_jobs) == 3
    sb = ctx.storyboards.list_by_project(project.project_id)[0]
    segs = sb.content.segments
    by_key = {j.input_snapshot["segment_key"]: j.input_snapshot for j in video_jobs}
    for i, seg in enumerate(segs):
        snap = by_key[seg.segment_key]
        scene_head = i == 0 or segs[i - 1].scene_id != seg.scene_id
        assert snap["mode"] == "r2va"
        if scene_head:
            # 场景首段：关键帧占 <Picture 1>（参考图首位）
            assert snap["opening_frame_source"] == "keyframe", seg.segment_key
            assert snap["continuity_prev_segment_key"] is None
            assert snap["reference_paths"], "关键帧段参考图不应为空"
            assert snap["reference_paths"][0].startswith(
                f"{project.project_id}/frames/{snap['segment_key']}_frm-"
            )
        else:
            # 同场景后继段：尾帧接力，关键帧让位（参考图不再含本段关键帧）
            assert snap["opening_frame_source"] == "tail_frame", seg.segment_key
            assert snap["continuity_prev_segment_key"] == segs[i - 1].segment_key
            assert all(
                not p.startswith(f"{project.project_id}/frames/")
                for p in snap["reference_paths"]
            )
    run_until_idle(worker)
    assert ctx.projects.get(project.project_id).status is ProjectStatus.VIDEO_READY


def test_edit_script_after_approval_invalidates(flow) -> None:
    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    assert ctx.projects.get(project.project_id).status is ProjectStatus.SCRIPT_APPROVED

    script = ctx.scripts.active(project.project_id)
    payload = script.content.model_dump()
    payload["logline"] = "修改后的一句话"
    result = svc.dispatch(project.project_id, "edit_script_draft", payload)
    rolled = ctx.projects.get(project.project_id)
    assert rolled.status is ProjectStatus.SCRIPT_READY
    assert result["project"].status is ProjectStatus.SCRIPT_READY
    draft = ctx.scripts.latest_draft(project.project_id)
    assert draft.content.logline == "修改后的一句话"


def test_edit_script_from_created_reaches_ready(flow) -> None:
    """CREATED 下手写首稿：不能卡死在 SCRIPT_DRAFTING（TASK-018 回归）。"""
    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {})
    script = make_script_content()
    result = svc.dispatch(project.project_id, "edit_script_draft", script.model_dump())
    rolled = ctx.projects.get(project.project_id)
    assert rolled.status is ProjectStatus.SCRIPT_READY
    assert result["project"].status is ProjectStatus.SCRIPT_READY
    assert ctx.scripts.latest_draft(project.project_id) is not None
    # 且可直接确认（此前会 STATE_ILLEGAL 卡死）
    svc.dispatch(project.project_id, "approve_script")
    assert ctx.projects.get(project.project_id).status is ProjectStatus.SCRIPT_APPROVED


def test_run_script_strips_stage_directions() -> None:
    """LLM 违规把「（扑通跪下）」写进台词时，入库前被确定性剥离。"""
    from server.app.agents import Agents

    dirty = make_script_content()
    dirty.scenes[0].dialogues[0].line = "（扑通跪下）林总，我错了！"
    dirty.scenes[1].dialogues[0].line = "（打电话）"

    class OneShotLlm:
        def chat_json(self, *, system, user, schema, temperature):
            return FakeResult(dirty)

        def chat(self, **kwargs):
            return "pong"

    content = Agents(OneShotLlm()).run_script("想法", {"target_duration_sec": 20})
    assert content.scenes[0].dialogues[0].line == "林总，我错了！"
    # 纯舞台指示台词被移除
    assert content.scenes[1].dialogues == []
    assert sum("舞台指示" in w for w in content.warnings) == 2


def test_run_storyboard_cleans_dirty_script_input() -> None:
    """存量脏剧本进入分镜前被清洗，<d>/dialogue_refs 不会再带舞台指示。"""
    import json as _json

    from server.app.agents import Agents
    from server.domain.enums import AssetKind
    from server.tests.factories import make_asset

    dirty = make_script_content()
    dirty.scenes[0].dialogues[0].line = "（扑通跪下）林总，我错了！"

    class CaptureLlm:
        def __init__(self) -> None:
            self.user = None

        def chat_json(self, *, system, user, schema, temperature):
            self.user = user
            payload = user.split("\n\n上一次输出的以下确定性校验未通过")[0]
            body = _json.loads(payload)
            known = {a["asset_id"] for a in body.get("assets", [])}
            scene_ids = {sc["id"] for sc in body.get("script", {}).get("scenes", [])}
            sb = make_storyboard_content(known, shot1_line="林总，我错了！")
            kept = [x for x in sb.segments if x.scene_id in scene_ids]
            from server.domain.entities import StoryboardContent

            return FakeResult(StoryboardContent(segments=kept))

        def chat(self, **kwargs):
            return "pong"

    llm = CaptureLlm()
    agents = Agents(llm)
    content, _warnings = agents.run_storyboard(
        dirty, [make_asset("a-1", AssetKind.CHARACTER, "老船工")], {"target_duration_sec": 12}
    )
    assert content is not None
    payload = _json.loads(
        llm.user.split("\n\n上一次输出的以下确定性校验未通过")[0]
    )
    dialogues = [d["line"] for s in payload["script"]["scenes"] for d in s["dialogues"]]
    assert dialogues and all("扑通" not in line for line in dialogues)


def test_run_assets_single_card_per_asset() -> None:
    """官方 H3 资产卡规范：一资产一卡；LLM 违规拆分（面部特写/半身）被确定性裁掉。"""
    from server.app.agents import Agents, AssetExtraction

    dirty_plan = AssetExtraction.model_validate({
        "assets": [
            {"kind": "character", "name": "林宇", "description": "主角",
             "visual_anchor": "短发、黄马甲",
             "image_plan": [
                 {"view_label": "面部特写", "image_prompt": "close-up portrait"},
                 {"view_label": "主设定", "image_prompt": "character reference card, turnaround"},
                 {"view_label": "正面半身", "image_prompt": "half body"},
             ]},
            {"kind": "scene", "name": "渡口", "description": "夜",
             "visual_anchor": "木栈道",
             "image_plan": [
                 {"view_label": "全景", "image_prompt": "wide shot"},
                 {"view_label": "空镜", "image_prompt": "empty pier at night"},
             ]},
        ]
    })

    class OneShotLlm:
        def chat_json(self, *, system, user, schema, temperature):
            return FakeResult(dirty_plan)

        def chat(self, **kwargs):
            return "pong"

    plan = Agents(OneShotLlm()).run_assets(make_script_content(), "写实")
    by_name = {a.name: a for a in plan.assets}
    assert [p["view_label"] for p in by_name["林宇"].image_plan] == ["主设定"]
    assert "turnaround" in by_name["林宇"].image_plan[0]["image_prompt"]
    assert [p["view_label"] for p in by_name["渡口"].image_plan] == ["空镜"]


def test_run_assets_scene_card_is_single_wide_shot() -> None:
    """TASK-042：场景卡从"多视角拼贴"改为"单幅广角空镜"——一张卡一台相机，
    空间绝大部分一次性入画（关键帧图生图不再在多个面板之间摇摆）。"""
    from server.app.agents import AssetExtraction

    class CaptureLlm:
        def __init__(self) -> None:
            self.system = ""

        def chat_json(self, *, system, user, schema, temperature):
            self.system = system
            return FakeResult(
                AssetExtraction.model_validate(
                    {"assets": [{
                        "kind": "scene", "name": "夜色渡口", "description": "木栈道渡口",
                        "visual_anchor": "冷灰绿夜色、木桩、缆绳",
                        "image_plan": [{"view_label": "空镜", "image_prompt": "empty pier"}],
                    }]}
                )
            )

        def chat(self, **kwargs):
            return "pong"

    llm = CaptureLlm()
    Agents(llm).run_assets(make_script_content(), "写实", "9:16")

    assert "广角" in llm.system and "16-24mm" in llm.system
    assert "整张卡只有一幅画面" in llm.system
    assert "9:16" in llm.system
    # 旧的多视角拼贴口径必须消失（面板/反打视角会把关键帧参考图变成拼贴）
    assert "反打视角" not in llm.system
    assert "三个实景视角面板" not in llm.system


def test_run_assets_character_card_is_full_body() -> None:
    """TASK-043：角色卡从"正面半身定妆像"改为"正面全身定妆照"——头到脚完整，
    服装（含下装与鞋）全有据可依；同时确定性兜底卡也必须是全身。"""
    from server.app.agents import AssetExtraction

    class CaptureLlm:
        def __init__(self) -> None:
            self.system = ""

        def chat_json(self, *, system, user, schema, temperature):
            self.system = system
            # 故意只给一个违规的"面部特写"，触发确定性补卡分支
            return FakeResult(
                AssetExtraction.model_validate(
                    {"assets": [{
                        "kind": "character", "name": "陈默", "description": "司机",
                        "visual_anchor": "三十多岁男性，深蓝色旧夹克",
                        "image_plan": [{"view_label": "面部特写", "image_prompt": "close-up"}],
                    }]}
                )
            )

        def chat(self, **kwargs):
            return "pong"

    llm = CaptureLlm()
    plan = Agents(llm).run_assets(make_script_content(), "写实", "9:16")

    assert "全身定妆照" in llm.system
    assert "full-body portrait" in llm.system
    assert "bust portrait" not in llm.system
    fallback = next(
        p for p in plan.assets[0].image_plan if p["view_label"] == "主设定"
    )
    assert "full-body portrait" in fallback["image_prompt"]
    assert "head to toe" in fallback["image_prompt"]


def test_reextract_assets_replaces_old(flow) -> None:
    """重新抽取资产 = 整体替换（不叠加旧资产卡）；图片行同步清理（TASK-020）。"""
    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)
    first = {a.name for a in ctx.assets.list_assets(project.project_id)}
    first_ids = {a.asset_id for a in ctx.assets.list_assets(project.project_id)}
    assert first and all(
        len(a.image_plan) == 1 for a in ctx.assets.list_assets(project.project_id)
    )

    svc.dispatch(project.project_id, "generate_assets")  # ASSET_READY → 重新抽取
    run_until_idle(worker)
    second = ctx.assets.list_assets(project.project_id)
    assert {a.name for a in second} == first, "资产名集合应一致（整体替换）"
    assert {a.asset_id for a in second}.isdisjoint(first_ids), "旧资产卡不得残留"
    images = ctx.assets.list_images(project_id=project.project_id)
    assert len(images) == len(second), "每资产恰好一张设定卡图"


def test_update_params_style_and_generation_composition(flow) -> None:
    """风格在资产 Tab 确认（update_params）；生成时自动拼入提示词（TASK-022）。"""
    from server.domain.enums import JobType

    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)

    result = svc.dispatch(project.project_id, "update_params",
                          {"style": "3D animation style, Pixar/Disney look"})
    assert result["project"].params.style == "3D animation style, Pixar/Disney look"

    asset = next(
        a for a in ctx.assets.list_assets(project.project_id) if a.kind.value == "character"
    )
    svc.dispatch(project.project_id, "generate_asset_image",
                 {"asset_id": asset.asset_id, "view_label": "主设定"})
    job = next(j for j in ctx.jobs.list_all()
               if j.type is JobType.IMAGE_GEN and j.payload.get("prompt", "").endswith(
                   "style: 3D animation style, Pixar/Disney look"))
    assert "style: 3D animation style" in job.payload["prompt"]


def test_regenerate_storyboard_after_approval(flow) -> None:
    """分镜确认后（乃至成片阶段）仍可重生成：FR-016 回退 STORYBOARD_READY（TASK-025）。"""
    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)
    for image in ctx.assets.list_images(project_id=project.project_id):
        svc.dispatch(project.project_id, "approve_asset_image",
                     {"asset_image_id": image.asset_image_id, "approved": True})
    svc.dispatch(project.project_id, "approve_assets")
    svc.dispatch(project.project_id, "generate_storyboard")
    run_until_idle(worker)
    svc.dispatch(project.project_id, "approve_storyboard")
    assert ctx.projects.get(project.project_id).status is ProjectStatus.STORYBOARD_APPROVED

    result = svc.dispatch(project.project_id, "generate_storyboard")
    assert result["jobs"], "重生成应入队"
    assert ctx.projects.get(project.project_id).status is ProjectStatus.STORYBOARD_DRAFTING
    run_until_idle(worker)
    rolled = ctx.projects.get(project.project_id)
    assert rolled.status is ProjectStatus.STORYBOARD_READY
    versions = ctx.storyboards.list_by_project(project.project_id)
    assert len(versions) == 2, "旧分镜版本保留，新草稿并排"


def test_delete_asset_image(flow) -> None:
    """生成多版本后可删除旧图（TASK-023）；删库行、留磁盘文件。"""
    from server.domain.errors import DomainError

    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)
    asset = next(
        a for a in ctx.assets.list_assets(project.project_id) if a.kind.value == "character"
    )
    svc.dispatch(project.project_id, "generate_asset_image",
                 {"asset_id": asset.asset_id, "view_label": "主设定"})
    images = ctx.assets.list_images(asset_id=asset.asset_id)
    assert len(images) == 2  # 抽取 1 张 + 再生成 1 张

    svc.dispatch(project.project_id, "delete_asset_image",
                 {"asset_image_id": images[0].asset_image_id})
    remaining = ctx.assets.list_images(asset_id=asset.asset_id)
    assert [i.asset_image_id for i in remaining] == [images[1].asset_image_id]
    # 生成期落盘的文件仍在（可恢复）
    assert all(
        (Path(ctx.settings.media_dir) / i.file_path).exists() for i in remaining
    )
    # 删除不存在的图 → NOT_FOUND
    try:
        svc.dispatch(project.project_id, "delete_asset_image",
                     {"asset_image_id": images[0].asset_image_id})
        raise AssertionError("应抛 NOT_FOUND")
    except DomainError as exc:
        assert exc.code == "NOT_FOUND"


def test_create_and_edit_asset_manual(flow) -> None:
    """LLM 漏拆的资产可人工新增/修订；过资产门后改动触发失效回退（TASK-018）。"""
    from server.domain.errors import DomainError

    svc, worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})
    svc.dispatch(project.project_id, "generate_script")
    worker.run_once()
    svc.dispatch(project.project_id, "approve_script")
    # 资产未抽取时不可人工新增
    with pytest.raises(DomainError) as excinfo:
        svc.dispatch(project.project_id, "create_asset",
                     {"kind": "prop", "name": "船桨"})
    assert excinfo.value.code == "STATE_ILLEGAL"

    svc.dispatch(project.project_id, "generate_assets")
    run_until_idle(worker)
    result = svc.dispatch(project.project_id, "create_asset", {
        "kind": "prop", "name": "船桨", "visual_anchor": "旧木桨、裂纹",
        "image_plan": [{"view_label": "白底", "image_prompt": "old wooden oar, white background"}],
    })
    asset = result["asset"]
    assert asset.name == "船桨"
    assets = ctx.assets.list_assets(project.project_id)
    assert any(a.name == "船桨" for a in assets)

    edited = svc.dispatch(project.project_id, "edit_asset", {
        "asset_id": asset.asset_id, "visual_anchor": "旧木桨、深色包浆",
    })
    assert edited["asset"].visual_anchor == "旧木桨、深色包浆"

    # 资产门后再改 → 回退到 ASSET_READY
    for image in ctx.assets.list_images(project_id=project.project_id):
        svc.dispatch(project.project_id, "approve_asset_image",
                     {"asset_image_id": image.asset_image_id, "approved": True})
    svc.dispatch(project.project_id, "approve_assets")
    assert ctx.projects.get(project.project_id).status is ProjectStatus.ASSET_APPROVED
    svc.dispatch(project.project_id, "edit_asset",
                 {"asset_id": asset.asset_id, "visual_anchor": "再次修订"})
    assert ctx.projects.get(project.project_id).status is ProjectStatus.ASSET_READY


def test_paid_commands_refused_when_process_runs_stale_code(monkeypatch, flow) -> None:
    """TASK-044：进程加载旧代码时付费命令必须被拒（否则按旧口径生成、照旧扣币）。

    实测背景：改完全身卡规则没重启，旧 Worker 按旧口径写了 4 张半身卡 + 2 张多视图
    空镜卡的提示词，照常扣币约 138 币，页面完全看不出异常。
    """
    from server.domain.errors import DomainError
    from server.infra.buildinfo import CodeFreshness

    svc, _worker, _ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})

    stale = CodeFreshness(
        stale=True, process_start_ts=1.0, newest_source_ts=2.0,
        newest_source_path="server/app/agents.py", version="deadbeef",
    )
    monkeypatch.setattr("server.infra.buildinfo.freshness", lambda *a, **k: stale)

    for command in ("generate_assets", "generate_keyframes", "produce_video"):
        with pytest.raises(DomainError) as exc:
            svc.dispatch(project.project_id, command)
        assert exc.value.code == "STALE_CODE"
        assert "重启" in exc.value.message

    # 免费动作不受影响：护栏只拦"会下单扣币"的那几个
    svc.dispatch(project.project_id, "update_params", {"style": "写实"})
    svc.dispatch(project.project_id, "generate_script")


def test_asset_extract_handler_refused_when_worker_runs_stale_code(monkeypatch, flow) -> None:
    """Worker 侧同样要拦：资产提示词是 AssetAgent 在本进程里现场写的。"""
    from server.app.handlers import Handlers
    from server.domain.entities import Job
    from server.domain.errors import DomainError
    from server.infra.buildinfo import CodeFreshness

    svc, _worker, ctx = flow
    project = svc.create_project("渡口", "想法", {"target_duration_sec": 12})

    handlers = Handlers(ctx, agents=None)  # 护栏在任何 Agent 调用之前生效
    job = Job(job_id="job-stale", project_id=project.project_id, type=JobType.ASSET_EXTRACT)

    stale = CodeFreshness(
        stale=True, process_start_ts=1.0, newest_source_ts=2.0,
        newest_source_path="server/app/agents.py", version="deadbeef",
    )
    monkeypatch.setattr("server.infra.buildinfo.freshness", lambda *a, **k: stale)
    with pytest.raises(DomainError) as exc:
        handlers.handle_asset_extract(job)
    assert exc.value.code == "STALE_CODE"


def test_delete_project_cascades_db_and_archives_media(tmp_path) -> None:
    """删除项目：DB 全部关联行级联清除、媒体目录移入 data/trash、在途任务拒绝。"""
    import sqlalchemy as sa

    from server.domain.entities import (
        Asset,
        AssetImage,
        Job,
        ProviderCall,
        Scene,
        ScriptContent,
        ScriptVersion,
        SegmentFrameImage,
        StoryboardContent,
        StoryboardVersion,
    )
    from server.domain.enums import AssetImageStatus, AssetKind, JobStatus, WorkStatus
    from server.domain.errors import DomainError

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'del.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = svc.create_project("待删", "想法", {}).project_id

    ctx.scripts.add(
        ScriptVersion(
            script_version_id="sv-1", project_id=pid, version_no=1,
            content=ScriptContent(
                logline="x",
                scenes=[Scene(id="S01", title="t", summary="s", est_seconds=5)],
                characters=[{"name": "老船工", "profile": "x"}],
                props=[],
                warnings=[],
            ),
            status=WorkStatus.ACTIVE,
        )
    )
    ctx.storyboards.add(
        StoryboardVersion(
            storyboard_version_id="sbv-1", project_id=pid, version_no=1,
            content=StoryboardContent(segments=[make_segment()]), status=WorkStatus.ACTIVE,
        )
    )
    ctx.assets.add_asset(
        Asset(asset_id="a-1", project_id=pid, kind=AssetKind.CHARACTER, name="老船工")
    )
    ctx.assets.add_image(
        AssetImage(
            asset_image_id="img-1", asset_id="a-1", version_no=1, view_label="主设定",
            file_path=f"{pid}/assets/a-1.png",
            status=AssetImageStatus.READY, approved=True,
        )
    )
    ctx.frames.add(
        SegmentFrameImage(
            frame_image_id="frm-1", project_id=pid, segment_key="S01G01", version_no=1,
        )
    )
    ctx.jobs.insert(
        Job(job_id="job-1", project_id=pid, type=JobType.SCRIPT_GEN, status=JobStatus.SUCCEEDED)
    )
    ctx.calls.add(ProviderCall(call_id="call-1", job_id="job-1", provider="llm"))
    media = settings.media_dir / pid / "assets" / "a-1.png"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"png")

    # 在途任务（pending/running）拒绝删除，避免在途回调写回已删项目
    ctx.jobs.insert(
        Job(job_id="job-2", project_id=pid, type=JobType.IMAGE_GEN, status=JobStatus.RUNNING)
    )
    with pytest.raises(DomainError) as exc:
        svc.delete_project(pid)
    assert exc.value.code == "STATE_ILLEGAL"

    job2 = ctx.jobs.get("job-2")
    job2.status = JobStatus.CANCELLED
    ctx.jobs.save(job2)
    result = svc.delete_project(pid)

    assert result["title"] == "待删"
    assert ctx.projects.get(pid) is None
    assert ctx.scripts.list_by_project(pid) == []
    assert ctx.storyboards.list_by_project(pid) == []
    assert ctx.assets.list_assets(pid) == []
    assert ctx.assets.list_images(project_id=pid) == []
    assert ctx.frames.list_by_project(pid) == []
    assert ctx.clips.list_by_project(pid) == []
    assert [j for j in ctx.jobs.list_all() if j.project_id == pid] == []
    assert result["deleted_rows"]["projects"] == 1
    assert result["deleted_rows"]["asset_images"] == 1
    assert result["deleted_rows"]["provider_calls"] == 1
    # 媒体文件不物理删除：整目录移入 data/trash（可人工恢复）
    assert not media.exists()
    assert (Path(result["media_moved_to"]) / "assets" / "a-1.png").exists()

    # 重复删除 → NOT_FOUND
    with pytest.raises(DomainError) as exc2:
        svc.delete_project(pid)
    assert exc2.value.code == "NOT_FOUND"
