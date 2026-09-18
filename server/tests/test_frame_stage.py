"""TASK-031 关键帧阶段：提示词编译、开场帧槽位、编号契约。"""

from server.app.h3_compiler import (
    RELAY_ANCHOR_EN,
    compile_h3_prompt,
    compile_keyframe_prompt,
)
from server.domain.entities import Segment
from server.domain.enums import AssetKind
from server.domain.validation import validate_segment
from server.tests.factories import (
    make_asset,
    make_reference_segment,
    make_segment,
)

_CHAR = make_asset("a-face", AssetKind.CHARACTER, "老船工")
_SCENE = make_asset("a-start", AssetKind.SCENE, "夜色渡口")


# --------------------------------------------------------------------------
# compile_keyframe_prompt：生图提示词编译
# --------------------------------------------------------------------------


def test_compile_keyframe_prompt_uses_description_and_locks() -> None:
    segment = make_segment(asset_refs=[{"asset_id": "a-face", "usage_note": ""}])
    segment = segment.model_copy(update={"keyframe_description": (
        "Medium shot: the old ferryman stands at the pier holding a lantern, "
        "river fog behind him, cold blue night light."
    )})
    prompt = compile_keyframe_prompt(
        segment,
        {"a-face": _CHAR},
        style_line="cinematic realistic style, film grain",
    )
    assert prompt.startswith("cinematic realistic style, film grain")
    assert "Cinematic still frame:" in prompt
    assert "holding a lantern" in prompt
    # 在场角色身份锁入文
    assert "老船工" in prompt


def test_compile_keyframe_prompt_falls_back_to_first_shot() -> None:
    """keyframe_description 缺失（存量分镜）→ 回退首镜开场状态。"""
    segment = make_segment()
    prompt = compile_keyframe_prompt(segment, {}, style_line="")
    assert "ferryman at the pier" in prompt


def test_compile_keyframe_prompt_scene_and_prop_locks() -> None:
    segment = make_segment(
        asset_refs=[
            {"asset_id": "a-start", "usage_note": ""},
            {"asset_id": "a-face", "usage_note": ""},
        ]
    )
    prompt = compile_keyframe_prompt(
        segment, {"a-start": _SCENE, "a-face": _CHAR}, style_line=""
    )
    assert "background:" in prompt
    assert "老船工" in prompt


def test_compile_keyframe_prompt_extra_prompt_appended() -> None:
    segment = make_segment()
    prompt = compile_keyframe_prompt(
        segment, {}, style_line="", extra_prompt="wider framing, overcast light"
    )
    assert "wider framing, overcast light" in prompt


# --------------------------------------------------------------------------
# compile_h3_prompt：opening_frame=True 时非连续段也拥有 Picture 1 槽位
# --------------------------------------------------------------------------


def test_non_continuity_segment_with_opening_frame_gets_picture_1_slot() -> None:
    """关键帧段（TASK-031）重编译：Picture 1 = 开场锚点，资产图从 Picture 2 起，
    编号集合恰为 {1..N}。开场帧槽位是产视频时概念（分段契约仍按 continuity 口径），
    这里直接断言编译文本的编号结构。"""
    from server.domain.validation import LABEL_RE

    segment = make_reference_segment(continuity=None)
    assert segment.continuity.enabled is False
    compiled = compile_h3_prompt(
        segment,
        {"a-start": _SCENE, "a-face": _CHAR},
        style_line="cinematic",
        opening_frame=True,
    )
    assert "the exact opening frame of this video" in compiled
    mentioned = sorted(
        {int(n) for kind, n in LABEL_RE.findall(compiled) if kind == "Picture"}
    )
    assert mentioned == [1, 2, 3]  # 开场帧 + 两资产
    # retention_analysis 也声明了开场帧槽位
    assert "<Picture 1> (appears in [Shot 1]): fully_preserved - opening" in compiled


def test_continuity_segment_prompt_keeps_neutral_anchor() -> None:
    """连续段编译结果含中性锚定句（对尾帧/关键帧两来源通用）。"""
    segment = make_reference_segment(
        continuity={"enabled": True, "with_prev_segment_key": "S01G00"}
    )
    compiled = compile_h3_prompt(
        segment,
        {"a-start": _SCENE, "a-face": _CHAR},
        style_line="cinematic",
    )
    assert RELAY_ANCHOR_EN in compiled
    validate_segment(
        segment.model_copy(
            update={"h3_prompt": segment.h3_prompt.model_copy(update={"text": compiled})}
        ),
        known_asset_ids={"a-start", "a-face"},
    )


def test_segment_accepts_keyframe_description() -> None:
    segment: Segment = make_segment().model_copy(
        update={"keyframe_description": "Wide shot of the pier at dawn."}
    )
    assert segment.keyframe_description == "Wide shot of the pier at dawn."


def test_create_asset_at_frame_ready_invalidates_assets(tmp_path) -> None:
    """FRAME_READY 下补漏资产（LLM 漏抽取）→ FR-016 回退 ASSET_READY，不再 409。"""
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.enums import ProjectStatus, Stage  # noqa: F401
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'frame_assets.db'}",
        connect_args={"check_same_thread": False},
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    project = svc.create_project("渡口", "想法", {})
    # 直推状态机到 FRAME_READY（不跑任务，只验证状态门）
    from server.domain.project import apply_action as _apply

    project = ctx.projects.get(project.project_id)
    for action in ("generate_script", "script_generated", "approve_script",
                   "generate_assets", "assets_generated", "approve_assets",
                   "generate_storyboard", "storyboard_generated",
                   "approve_storyboard", "generate_keyframes",
                   "keyframes_generated"):
        project = _apply(project, action)
    ctx.projects.save(project)

    result = svc.dispatch(project.project_id, "create_asset", {
        "kind": "character", "name": "雷震",
        "visual_anchor": "络腮胡寸头大汉",
    })
    rolled = ctx.projects.get(project.project_id)
    assert rolled.status is ProjectStatus.ASSET_READY
    assert result["asset"].name == "雷震"


def test_reference_cards_three_in_frame_characters_keep_scene_card(tmp_path) -> None:
    """拼表方案：3 人同框时角色卡全部入选，场景卡不再让位。

    旧口径（TASK-045 实测缺陷，逝去的童年 S02G04）：小伙伴乙没有参考卡 → 直接从
    画面消失，右侧被画成第二个小伙伴甲（同款红条纹衣+同发型），为此牺牲场景卡。
    现在选卡不设上限，由 `_resolve_reference_slots` 把超编卡片拼成设定表。
    """
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.entities import Asset, AssetImage, SubjectPlacement
    from server.domain.enums import AssetImageStatus
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'cards3.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = "p-cards3"

    def add(asset_id: str, kind: AssetKind, name: str, label: str) -> None:
        ctx.assets.add_asset(
            Asset(asset_id=asset_id, project_id=pid, kind=kind, name=name,
                  visual_anchor=f"{name} anchor")
        )
        ctx.assets.add_image(
            AssetImage(
                asset_image_id=f"img-{asset_id}", asset_id=asset_id, version_no=1,
                view_label=label, file_path=f"{pid}/assets/{asset_id}.png",
                status=AssetImageStatus.READY, approved=True,
            )
        )

    add("a-kid1", AssetKind.CHARACTER, "童年陈末", "主设定")
    add("a-kid2", AssetKind.CHARACTER, "小伙伴甲", "主设定")
    add("a-kid3", AssetKind.CHARACTER, "小伙伴乙", "主设定")
    add("a-yard", AssetKind.SCENE, "胡同空地", "空镜")

    segment = make_segment(
        asset_refs=[
            {"asset_id": "a-kid1", "usage_note": ""},
            {"asset_id": "a-kid2", "usage_note": ""},
            {"asset_id": "a-kid3", "usage_note": ""},
            {"asset_id": "a-yard", "usage_note": ""},
        ]
    ).model_copy(
        update={
            "subject_placements": [
                SubjectPlacement(name="童年陈末", placement="sitting cross-legged on the left"),
                SubjectPlacement(name="小伙伴甲", placement="sitting opposite him"),
                SubjectPlacement(name="小伙伴乙", placement="sitting to the right"),
            ]
        }
    )

    cards = svc._segment_reference_cards(pid, segment)
    assert cards == [
        f"{pid}/assets/a-kid1.png",
        f"{pid}/assets/a-kid2.png",
        f"{pid}/assets/a-kid3.png",
        f"{pid}/assets/a-yard.png",
    ]


def test_reference_cards_skip_off_frame_characters_and_props(tmp_path) -> None:
    """TASK-033 图生图参考卡裁剪：只传开场帧可见角色的卡 + 场景卡。

    实测缺陷：把不在画面里的角色卡（或道具卡）送进 Edit 通道，模型会把第二张
    脸也摆进画面、两卡特征互串（坐着的乘客挂上了另一人的旧工牌、背景多出人影）。
    """
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.entities import Asset, AssetImage, SubjectPlacement
    from server.domain.enums import AssetImageStatus
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'cards.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = "p-cards"

    def add(asset_id: str, kind: AssetKind, name: str, label: str) -> None:
        ctx.assets.add_asset(
            Asset(asset_id=asset_id, project_id=pid, kind=kind, name=name,
                  visual_anchor=f"{name} anchor")
        )
        ctx.assets.add_image(
            AssetImage(
                asset_image_id=f"img-{asset_id}", asset_id=asset_id, version_no=1,
                view_label=label, file_path=f"{pid}/assets/{asset_id}.png",
                status=AssetImageStatus.READY, approved=True,
            )
        )

    add("a-zhou", AssetKind.CHARACTER, "周明远", "主设定")
    add("a-chen", AssetKind.CHARACTER, "陈默", "主设定")
    add("a-bus", AssetKind.SCENE, "末班公交车驾驶座", "空镜")
    add("a-badge", AssetKind.PROP, "旧工牌", "主设定")

    segment = make_segment(
        asset_refs=[
            {"asset_id": "a-zhou", "usage_note": ""},
            {"asset_id": "a-chen", "usage_note": ""},
            {"asset_id": "a-bus", "usage_note": ""},
            {"asset_id": "a-badge", "usage_note": ""},
        ]
    ).model_copy(
        update={
            "subject_placements": [
                SubjectPlacement(
                    name="周明远",
                    placement="seated in the aisle-side row, phone in both hands",
                ),
                # 司机只作为远处剪影：不进画面人名单、卡片不进参考图
                SubjectPlacement(
                    name="陈默",
                    placement="in the driver's seat, silhouette at the front",
                    in_frame=False,
                ),
            ]
        }
    )

    cards = svc._segment_reference_cards(pid, segment)
    assert cards == [f"{pid}/assets/a-zhou.png", f"{pid}/assets/a-bus.png"]


def test_reference_cards_legacy_segment_keeps_character_order(tmp_path) -> None:
    """存量分镜无 in_frame 字段：保持旧行为（角色按 asset_refs 顺序取前两张）。"""
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.entities import Asset, AssetImage
    from server.domain.enums import AssetImageStatus
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'legacy.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = "p-legacy"
    for asset_id, kind, name in (
        ("a1", AssetKind.CHARACTER, "甲"),
        ("a2", AssetKind.CHARACTER, "乙"),
        ("a3", AssetKind.SCENE, "渡口"),
    ):
        ctx.assets.add_asset(
            Asset(asset_id=asset_id, project_id=pid, kind=kind, name=name)
        )
        ctx.assets.add_image(
            AssetImage(
                asset_image_id=f"i-{asset_id}", asset_id=asset_id, version_no=1,
                view_label="主设定" if kind is AssetKind.CHARACTER else "空镜",
                file_path=f"{pid}/assets/{asset_id}.png",
                status=AssetImageStatus.READY, approved=True,
            )
        )
    segment = make_segment(
        asset_refs=[{"asset_id": a, "usage_note": ""} for a in ("a1", "a2", "a3")]
    )
    cards = svc._segment_reference_cards(pid, segment)
    assert cards == [f"{pid}/assets/a1.png", f"{pid}/assets/a2.png", f"{pid}/assets/a3.png"]


def test_generate_frame_image_accepts_prompt_and_reference_overrides(tmp_path) -> None:
    """TASK-035：页面可改生图提示词与参考图——prompt 覆盖不再走编译；
    reference_asset_image_ids 按给定顺序解析（未批准/未落盘图被跳过），
    实际使用的参考图记进帧行供页面摊开展示。"""
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.entities import Asset, AssetImage, SubjectPlacement
    from server.domain.enums import AssetImageStatus
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'frame_edit.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    project = svc.create_project("渡口", "想法", {})
    pid = project.project_id

    def add(asset_id: str, kind: AssetKind, name: str, approved: bool = True) -> str:
        ctx.assets.add_asset(
            Asset(asset_id=asset_id, project_id=pid, kind=kind, name=name,
                  visual_anchor=f"{name} anchor")
        )
        image = AssetImage(
            asset_image_id=f"img-{asset_id}", asset_id=asset_id, version_no=1,
            view_label="主设定" if kind is AssetKind.CHARACTER else "空镜",
            file_path=f"{pid}/assets/{asset_id}.png",
            status=AssetImageStatus.READY, approved=approved,
        )
        ctx.assets.add_image(image)
        return image.asset_image_id

    img_face = add("a-face", AssetKind.CHARACTER, "老船工")
    add("a-scene", AssetKind.SCENE, "夜色渡口")
    img_draft = add("a-bad", AssetKind.PROP, "草稿道具", approved=False)

    # 直推状态机到 FRAME 阶段（不跑任务），并写入 active 分镜
    from server.domain.entities import (
        H3Prompt,
        Segment,
        Shot,
        StoryboardContent,
        StoryboardVersion,
    )
    from server.domain.project import apply_action as _apply

    moved = ctx.projects.get(pid)
    for action in ("generate_script", "script_generated", "approve_script",
                   "generate_assets", "assets_generated", "approve_assets",
                   "generate_storyboard", "storyboard_generated", "approve_storyboard",
                   "generate_keyframes"):
        moved = _apply(moved, action)
    ctx.projects.save(moved)
    segment = Segment(
        segment_key="S01G01", scene_id="S1", index=1, duration_sec=5,
        shots=[Shot(shot_no=1, cutpoint_sec=0.0, camera="static shot",
                    description="ferryman at the pier")],
        asset_refs=[{"asset_id": "a-face", "usage_note": ""},
                    {"asset_id": "a-scene", "usage_note": ""}],
        subject_placements=[SubjectPlacement(name="老船工", placement="at the pier")],
        keyframe_description="Wide shot of the pier at night.",
        h3_prompt=H3Prompt(text=""),
    )
    from server.domain.enums import WorkStatus

    ctx.storyboards.add(
        StoryboardVersion(
            storyboard_version_id="sbv-x", project_id=pid, version_no=1,
            content=StoryboardContent(segments=[segment]),
            status=WorkStatus.ACTIVE,
        )
    )

    result = svc.dispatch(pid, "generate_frame_image", {
        "segment_key": "S01G01",
        "prompt": "custom prompt from the page: one ferryman, empty pier",
        "reference_asset_image_ids": [img_face, img_draft, "img-nonexistent"],
    })
    job = result["jobs"][0]
    assert job.payload["prompt"] == "custom prompt from the page: one ferryman, empty pier"
    # 未批准图与不存在的图被跳过，只留已批准已落盘的那张
    assert job.payload["reference_rel_paths"] == [f"{pid}/assets/a-face.png"]
    row = ctx.frames.list_by_segment(pid, "S01G01")[0]
    assert row.prompt == job.payload["prompt"]
    assert row.reference_paths == [f"{pid}/assets/a-face.png"]


def test_generate_frame_image_empty_reference_list_means_text_to_image(tmp_path) -> None:
    """显式传空参考图列表 = 纯文本生图（页面可把参考图全部取消勾选）。"""
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.entities import (
        H3Prompt,
        Segment,
        Shot,
        StoryboardContent,
        StoryboardVersion,
        SubjectPlacement,
    )
    from server.domain.project import apply_action as _apply
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'frame_t2i.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = svc.create_project("渡口", "想法", {}).project_id
    moved = ctx.projects.get(pid)
    for action in ("generate_script", "script_generated", "approve_script",
                   "generate_assets", "assets_generated", "approve_assets",
                   "generate_storyboard", "storyboard_generated", "approve_storyboard",
                   "generate_keyframes"):
        moved = _apply(moved, action)
    ctx.projects.save(moved)
    from server.domain.enums import WorkStatus

    ctx.storyboards.add(
        StoryboardVersion(
            storyboard_version_id="sbv-y", project_id=pid, version_no=1,
            status=WorkStatus.ACTIVE,
            content=StoryboardContent(segments=[Segment(
                segment_key="S01G01", scene_id="S1", index=1, duration_sec=5,
                shots=[Shot(shot_no=1, cutpoint_sec=0.0, camera="static shot",
                            description="pier")],
                subject_placements=[SubjectPlacement(name="无人", placement="empty pier")],
                keyframe_description="Empty pier at night.",
                h3_prompt=H3Prompt(text=""),
            )]),
        )
    )
    result = svc.dispatch(pid, "generate_frame_image", {
        "segment_key": "S01G01", "reference_asset_image_ids": [],
    })
    job = result["jobs"][0]
    assert "reference_rel_paths" not in job.payload
    assert ctx.frames.list_by_segment(pid, "S01G01")[0].reference_paths == []


def test_reference_cards_do_not_chain_previous_frame(tmp_path) -> None:
    """TASK-038（否决）：参考图**不**包含上一段关键帧。

    实测：把上一段已批准关键帧放进参考图（[img0] 或末位都一样），本段构图会被它
    带跑——后视镜特写被画成上一段的侧拍司机。故参考图固定为"画面内角色卡 + 场景卡"，
    场景一致性交给视频阶段与连续段尾帧接力。"""
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.entities import (
        Asset,
        AssetImage,
        H3Prompt,
        Segment,
        SegmentFrameImage,
        Shot,
        StoryboardContent,
        StoryboardVersion,
        SubjectPlacement,
    )
    from server.domain.enums import AssetImageStatus, WorkStatus
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'chain.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = "p-chain"

    for asset_id, kind, name in (
        ("c1", AssetKind.CHARACTER, "司机"),
        ("sc", AssetKind.SCENE, "公交车驾驶座"),
    ):
        ctx.assets.add_asset(
            Asset(asset_id=asset_id, project_id=pid, kind=kind, name=name,
                  visual_anchor=f"{name} anchor")
        )
        ctx.assets.add_image(
            AssetImage(
                asset_image_id=f"i-{asset_id}", asset_id=asset_id, version_no=1,
                view_label="主设定" if kind is AssetKind.CHARACTER else "空镜",
                file_path=f"{pid}/assets/{asset_id}.png",
                status=AssetImageStatus.READY, approved=True,
            )
        )

    def seg(key: str, index: int) -> Segment:
        return Segment(
            segment_key=key, scene_id="S1", index=index, duration_sec=5,
            shots=[Shot(shot_no=1, cutpoint_sec=0.0, camera="static shot",
                        description="driving")],
            asset_refs=[{"asset_id": "c1", "usage_note": ""},
                        {"asset_id": "sc", "usage_note": ""}],
            subject_placements=[SubjectPlacement(name="司机", placement="in the driver's seat")],
            keyframe_description="The driver sits at the wheel.",
            h3_prompt=H3Prompt(text=""),
        )

    first, second = seg("S01G01", 1), seg("S01G02", 2)
    ctx.storyboards.add(
        StoryboardVersion(
            storyboard_version_id="sbv-chain", project_id=pid, version_no=1,
            content=StoryboardContent(segments=[first, second]),
            status=WorkStatus.ACTIVE,
        )
    )
    ctx.frames.add(
        SegmentFrameImage(
            frame_image_id="frm-prev", project_id=pid, segment_key="S01G01",
            version_no=1, prompt="p", file_path=f"{pid}/frames/S01G01.png",
            status=AssetImageStatus.READY, approved=True,
        )
    )

    # 两段都只含角色卡 + 场景卡；前段已批准关键帧不进参考图
    for segment in (first, second):
        picked = svc._segment_reference_cards(pid, segment)
        assert picked == [f"{pid}/assets/c1.png", f"{pid}/assets/sc.png"]
        assert f"{pid}/frames/S01G01.png" not in picked


def test_compile_keyframe_prompt_reference_sheet_wording() -> None:
    """拼合设定表绑定：Picture N 指称变为 side-by-side reference sheet，
    逐卡声明从左到右是谁（与落盘拼图的排列一致）。"""
    from server.app.h3_compiler import ReferenceSheet

    segment = make_segment(asset_refs=[{"asset_id": "a-face", "usage_note": ""}])
    prompt = compile_keyframe_prompt(
        segment,
        {"a-face": _CHAR},
        style_line="",
        reference_bindings=[_CHAR, ReferenceSheet(assets=[_CHAR, _SCENE])],
    )
    assert "Picture 1 is 老船工's character reference card" in prompt
    assert (
        "Picture 2 is a side-by-side reference sheet, from left to right: "
        "老船工's character reference card；"
        "a wide-angle overview of the same location（夜色渡口）"
    ) in prompt


def test_resolve_reference_slots_merges_extras_into_sheet(tmp_path) -> None:
    """>3 个资产：前 2 张各占一槽，其余拼成设定表占第 3 槽；拼图落盘且同组复用。"""
    import sqlalchemy as sa

    from PIL import Image

    from server.app.context import AppContext
    from server.app.h3_compiler import ReferenceSheet
    from server.app.usecases import WorkbenchService
    from server.domain.entities import Asset, AssetImage
    from server.domain.enums import AssetImageStatus
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'slots.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = "p-slots"

    def add(asset_id: str, kind: AssetKind, name: str, label: str) -> None:
        ctx.assets.add_asset(
            Asset(asset_id=asset_id, project_id=pid, kind=kind, name=name,
                  visual_anchor=f"{name} anchor")
        )
        rel = f"{pid}/assets/{asset_id}.png"
        ctx.assets.add_image(
            AssetImage(
                asset_image_id=f"img-{asset_id}", asset_id=asset_id, version_no=1,
                view_label=label, file_path=rel,
                status=AssetImageStatus.READY, approved=True,
            )
        )
        # 落一张真实小图（拼表函数要真开图）
        dest = settings.media_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (768, 1024), (200, 180, 160)).save(dest)

    for i in (1, 2, 3):
        add(f"a-kid{i}", AssetKind.CHARACTER, f"小伙伴{'甲乙丙'[i - 1]}", "主设定")
    add("a-yard", AssetKind.SCENE, "胡同空地", "空镜")

    paths = [f"{pid}/assets/a-kid{i}.png" for i in (1, 2, 3)] + [
        f"{pid}/assets/a-yard.png"
    ]
    slots, bindings = svc._resolve_reference_slots(pid, paths)
    # 前 2 张各占一槽，第 3 槽是拼合设定表
    assert slots[:2] == [f"{pid}/assets/a-kid1.png", f"{pid}/assets/a-kid2.png"]
    assert slots[2].startswith(f"{pid}/refsheets/sheet_")
    # 拼图真实落盘；同一组卡片再解析一次复用同一张拼图
    assert (settings.media_dir / slots[2]).exists()
    slots_again, _ = svc._resolve_reference_slots(pid, paths)
    assert slots_again == slots
    # bindings：独立槽 → Asset，拼合槽 → ReferenceSheet（顺序与拼图左起一致）
    assert [b.name for b in bindings[:2]] == ["小伙伴甲", "小伙伴乙"]
    sheet = bindings[2]
    assert isinstance(sheet, ReferenceSheet)
    assert [a.name for a in sheet.assets] == ["小伙伴丙", "胡同空地"]


# --------------------------------------------------------------------------
# 多宫格分镜板 Step1：SegmentFrameImage.grid_cell 格号字段
# --------------------------------------------------------------------------


class TestGridCellField:
    def test_frame_row_accepts_grid_cell(self, tmp_path) -> None:
        """带格号构造：宫格行按格号 1 起标记。"""
        from server.domain.entities import SegmentFrameImage

        row = SegmentFrameImage(
            frame_image_id="frm-x",
            project_id="p1",
            segment_key="S01G01",
            version_no=1,
            grid_cell=2,
        )
        assert row.grid_cell == 2

    def test_frame_row_default_grid_cell_none(self, tmp_path) -> None:
        """不传格号：存量单张关键帧行 grid_cell=None，走旧口径不受影响。"""
        from server.domain.entities import SegmentFrameImage

        row = SegmentFrameImage(
            frame_image_id="frm-x",
            project_id="p1",
            segment_key="S01G01",
            version_no=1,
        )
        assert row.grid_cell is None


def test_keyframe_description_override_takes_precedence() -> None:
    """description_override 优先于 keyframe_description 与首镜回退。"""
    from server.domain.entities import H3Prompt, Segment, Shot

    seg = Segment(
        segment_key="S01G01",
        scene_id="S1",
        index=1,
        duration_sec=10,
        shots=[
            Shot(shot_no=1, cutpoint_sec=5, camera="wide", description="d1", action="a1"),
            Shot(shot_no=2, cutpoint_sec=10, camera="close", description="d2", action="a2"),
        ],
        keyframe_description="整段开场描述",
        h3_prompt=H3Prompt(text=""),
    )
    prompt = compile_keyframe_prompt(seg, {}, description_override="a2")
    assert "a2" in prompt
    assert "整段开场描述" not in prompt
    assert "a1" not in prompt


def _storyboard_segment() -> "Segment":
    from server.domain.entities import H3Prompt, Segment, Shot

    return Segment(
        segment_key="S01G01",
        scene_id="S1",
        index=1,
        duration_sec=10,
        shots=[Shot(shot_no=1, cutpoint_sec=10, camera="wide", description="d", action="a")],
        h3_prompt=H3Prompt(text=""),
    )


def test_h3_storyboard_sentence_injected() -> None:
    """storyboard_cells>0 时注入宫格声明：实际格数/镜序+首格定开场+防入画护栏。"""
    from server.app.h3_compiler import compile_h3_prompt

    prompt = compile_h3_prompt(_storyboard_segment(), {}, opening_frame=True, storyboard_cells=2)
    assert "storyboard reference of 2 cells" in prompt
    assert "cell 1, cell 2" in prompt
    assert "never appears on screen" in prompt
    # retention 行同步声明实际格数
    assert "opens on the first cell of the 2-cell storyboard" in prompt
    # 宫格口径由 _storyboard_anchor 承担开场帧语义（不再叠加 RELAY 单帧锚定）
    assert "frame 0 must match the first cell" in prompt


def test_h3_storyboard_excludes_relay_anchor() -> None:
    """互斥：宫格分镜板参考不注入 RELAY_ANCHOR_EN 的单帧锚定句。"""
    from server.app.h3_compiler import compile_h3_prompt

    prompt = compile_h3_prompt(_storyboard_segment(), {}, opening_frame=True, storyboard_cells=2)
    assert "exact opening frame" not in prompt
    assert "the action starts from this frame" not in prompt


def test_h3_no_storyboard_sentence_by_default() -> None:
    """缺省（或防御性 cell_count<=0）不注入宫格声明句。"""
    from server.app.h3_compiler import compile_h3_prompt

    prompt = compile_h3_prompt(_storyboard_segment(), {}, opening_frame=True)
    assert "storyboard reference" not in prompt
    # 防御：cell_count<=0（如宫格行 reference_paths 异常为空）不注入
    prompt = compile_h3_prompt(_storyboard_segment(), {}, opening_frame=True, storyboard_cells=0)
    assert "storyboard reference" not in prompt


# --------------------------------------------------------------------------
# 多宫格分镜板 Task4：PIL 宫格拼图 _compose_storyboard_grid
# --------------------------------------------------------------------------


def _make_grid_settings(tmp_path):
    """与既有用例同款夹具：tmp 数据目录 + media 目录（格子图/宫格都落真实数据目录）。"""
    from server.infra.config import Settings

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _make_grid_cells(settings, count: int = 4) -> list[str]:
    """造 count 张 160x90 纯色格图写入 media 目录，返回相对路径列表。"""
    from PIL import Image

    rels = []
    for i in range(count):
        rel = f"p1/cells/cell{i}.png"
        dest = settings.media_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (160, 90), (i * 40, 0, 0)).save(dest)
        rels.append(rel)
    return rels


def test_grid_layout_three_cols(tmp_path) -> None:
    """4 张 16:9 格子 → 3 列 2 行白底宫格：宽=3*160+2*12=504，高=2*90+12=192。"""
    from PIL import Image

    from server.app.media import abs_media_path
    from server.app.usecases import _compose_storyboard_grid

    settings = _make_grid_settings(tmp_path)
    cell_paths = _make_grid_cells(settings)

    rel = _compose_storyboard_grid(settings, "p1", "S01G01", cell_paths)

    assert "storyboards/" in rel
    assert "S01G01" in rel
    img = Image.open(abs_media_path(settings, rel))
    assert img.size == (504, 192)


def test_grid_reuse_same_digest(tmp_path) -> None:
    """同组格子两次拼图：返回同一 rel，第二次命中 digest 不重复落盘。"""
    from server.app.media import abs_media_path
    from server.app.usecases import _compose_storyboard_grid

    settings = _make_grid_settings(tmp_path)
    cell_paths = _make_grid_cells(settings)

    rel1 = _compose_storyboard_grid(settings, "p1", "S01G01", cell_paths)
    dest = abs_media_path(settings, rel1)
    stat_before = dest.stat()

    rel2 = _compose_storyboard_grid(settings, "p1", "S01G01", cell_paths)

    assert rel1 == rel2
    stat_after = dest.stat()
    assert (stat_after.st_mtime_ns, stat_after.st_size) == (
        stat_before.st_mtime_ns,
        stat_before.st_size,
    )


def test_grid_digest_changes_with_cols(tmp_path) -> None:
    """digest 掺入列数配置：同组格子、不同 cols 返回不同宫格（不复用旧列数拼图）。"""
    from server.app.usecases import _compose_storyboard_grid

    settings = _make_grid_settings(tmp_path)
    cell_paths = _make_grid_cells(settings)

    rel3 = _compose_storyboard_grid(settings, "p1", "S01G01", cell_paths)
    settings.storyboard_grid_cols = 2
    rel2 = _compose_storyboard_grid(settings, "p1", "S01G01", cell_paths)

    assert rel3 != rel2


def test_grid_empty_cell_list_raises(tmp_path) -> None:
    """空格子列表：入口直接拒绝（防 min() 空序列 ValueError 与空组文件名摘要）。"""
    import pytest

    from server.app.usecases import _compose_storyboard_grid

    settings = _make_grid_settings(tmp_path)
    with pytest.raises(ValueError, match="至少一张格子图"):
        _compose_storyboard_grid(settings, "p1", "S01G01", [])


# --------------------------------------------------------------------------
# 多宫格分镜板 Task5：入队逐格化（批量/单抽/上传带格号；重抽格作废旧宫格行）
# --------------------------------------------------------------------------


def _two_shot_grid_segment() -> "Segment":
    """2-shot 段夹具：两镜 action 文案互斥（第 1 镜独有 unties the rope，
    第 2 镜独有 steps onto the boat），编译后仍保留，可作 prompt 泄漏断言。"""
    from server.domain.entities import H3Prompt, Segment, Shot

    return Segment(
        segment_key="S01G01",
        scene_id="S1",
        index=1,
        duration_sec=10,
        shots=[
            Shot(shot_no=1, cutpoint_sec=4.0, camera="wide",
                 description="at the pier",
                 action="the old ferryman unties the rope at the pier"),
            Shot(shot_no=2, cutpoint_sec=10.0, camera="close",
                 description="pushes off",
                 action="the old ferryman steps onto the boat"),
        ],
        keyframe_description="",
        h3_prompt=H3Prompt(text=""),
    )


def _single_shot_grid_segment() -> "Segment":
    """1-shot 段夹具：走旧单帧口径（grid_cell 必须为 None）。"""
    from server.domain.entities import H3Prompt, Segment, Shot

    return Segment(
        segment_key="S01G01",
        scene_id="S1",
        index=1,
        duration_sec=5,
        shots=[Shot(shot_no=1, cutpoint_sec=0.0, camera="wide",
                    description="at the pier")],
        keyframe_description="Wide shot of the pier at night.",
        h3_prompt=H3Prompt(text=""),
    )


def _grid_enqueue_fixture(tmp_path, segments, *, through_keyframes: bool = False):
    """逐格入队夹具：tmp 库 + 状态直推分镜已批准（可选再过关键帧门）+ active 分镜。"""
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.enums import WorkStatus
    from server.domain.project import apply_action as _apply
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'grid_enqueue.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = svc.create_project("渡口", "想法", {}).project_id
    moved = ctx.projects.get(pid)
    actions = ["generate_script", "script_generated", "approve_script",
               "generate_assets", "assets_generated", "approve_assets",
               "generate_storyboard", "storyboard_generated", "approve_storyboard"]
    if through_keyframes:
        actions.append("generate_keyframes")
    for action in actions:
        moved = _apply(moved, action)
    ctx.projects.save(moved)
    from server.domain.entities import StoryboardContent, StoryboardVersion

    ctx.storyboards.add(
        StoryboardVersion(
            storyboard_version_id="sbv-grid", project_id=pid, version_no=1,
            content=StoryboardContent(segments=list(segments)),
            status=WorkStatus.ACTIVE,
        )
    )
    return svc, ctx, pid


def test_batch_generates_one_job_per_shot(tmp_path) -> None:
    """2-shot 段批量生成 → 2 个 frame_gen 任务、行 grid_cell={1,2}；
    第 2 格提示词只含第 2 镜动作，第 1 镜独有内容不泄漏。"""
    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    result = svc.dispatch(pid, "generate_keyframes", {})
    assert len(result["jobs"]) == 2
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    assert sorted(r.grid_cell for r in rows) == [1, 2]
    row1 = next(r for r in rows if r.grid_cell == 1)
    row2 = next(r for r in rows if r.grid_cell == 2)
    assert "unties the rope" in row1.prompt
    assert "steps onto the boat" in row2.prompt
    assert "unties the rope" not in row2.prompt


def test_single_shot_segment_keeps_legacy(tmp_path) -> None:
    """1-shot 段批量 → 1 个任务、行 grid_cell=None（旧单帧口径完全不变）。"""
    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_single_shot_grid_segment()])
    result = svc.dispatch(pid, "generate_keyframes", {})
    assert len(result["jobs"]) == 1
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    assert len(rows) == 1
    assert rows[0].grid_cell is None


def test_reroll_with_cell_no(tmp_path) -> None:
    """单抽重生成带 cell_no=2 → 新行 grid_cell=2，提示词含第 2 镜动作描述。"""
    svc, ctx, pid = _grid_enqueue_fixture(
        tmp_path, [_two_shot_grid_segment()], through_keyframes=True
    )
    result = svc.dispatch(pid, "generate_frame_image",
                          {"segment_key": "S01G01", "cell_no": 2})
    assert len(result["jobs"]) == 1
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    assert len(rows) == 1
    assert rows[0].grid_cell == 2
    assert "steps onto the boat" in rows[0].prompt


def test_upload_with_cell_no(tmp_path) -> None:
    """上传关键帧行带 cell_no=1 → 行 grid_cell=1（页面上传格子图通道）；
    预置宫格行时上传换格同样作废旧宫格（与生成路径口径一致）。"""
    from server.domain.entities import STORYBOARD_GRID_LABEL, SegmentFrameImage
    from server.domain.enums import AssetImageStatus

    svc, ctx, pid = _grid_enqueue_fixture(
        tmp_path, [_two_shot_grid_segment()], through_keyframes=True
    )
    ctx.frames.add(
        SegmentFrameImage(
            frame_image_id="frm-grid", project_id=pid, segment_key="S01G01",
            version_no=1, view_label=STORYBOARD_GRID_LABEL,
            prompt="storyboard grid", provider="pillow",
            file_path=f"{pid}/storyboards/S01G01_grid_old.jpg",
            status=AssetImageStatus.READY, approved=True,
        )
    )
    svc.dispatch(pid, "upload_frame_image", {
        "segment_key": "S01G01", "content": b"png-bytes", "ext": ".png", "cell_no": 1,
    })
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    assert all(r.view_label != STORYBOARD_GRID_LABEL for r in rows)
    assert [r.grid_cell for r in rows] == [1]


def test_reroll_invalidates_grid(tmp_path) -> None:
    """段内已有分镜板宫格行时对格子重抽 → 宫格行被删除（T4 挂账修复）。"""
    from server.domain.entities import STORYBOARD_GRID_LABEL, SegmentFrameImage
    from server.domain.enums import AssetImageStatus

    svc, ctx, pid = _grid_enqueue_fixture(
        tmp_path, [_two_shot_grid_segment()], through_keyframes=True
    )
    ctx.frames.add(
        SegmentFrameImage(
            frame_image_id="frm-grid", project_id=pid, segment_key="S01G01",
            version_no=1, view_label=STORYBOARD_GRID_LABEL,
            prompt="storyboard grid", provider="pillow",
            file_path=f"{pid}/storyboards/S01G01_grid_old.jpg",
            status=AssetImageStatus.READY, approved=True,
        )
    )
    svc.dispatch(pid, "generate_frame_image", {"segment_key": "S01G01", "cell_no": 1})
    remaining = ctx.frames.list_by_segment(pid, "S01G01")
    assert all(r.view_label != STORYBOARD_GRID_LABEL for r in remaining)
    assert any(r.grid_cell == 1 for r in remaining)  # 重抽格本身还在


def test_cell_no_out_of_range_raises(tmp_path) -> None:
    """2-shot 段单抽 cell_no=3 → 抛 DomainError，不入队不建行。"""
    import pytest

    from server.domain.errors import DomainError

    svc, ctx, pid = _grid_enqueue_fixture(
        tmp_path, [_two_shot_grid_segment()], through_keyframes=True
    )
    with pytest.raises(DomainError) as exc_info:
        svc.dispatch(pid, "generate_frame_image",
                     {"segment_key": "S01G01", "cell_no": 3})
    assert exc_info.value.code == "INVALID_CELL_NO"
    assert ctx.frames.list_by_segment(pid, "S01G01") == []


def test_cell_no_zero_raises(tmp_path) -> None:
    """单抽 cell_no=0（格号 1 起）→ 抛 DomainError，不入队不建行。"""
    import pytest

    from server.domain.errors import DomainError

    svc, ctx, pid = _grid_enqueue_fixture(
        tmp_path, [_two_shot_grid_segment()], through_keyframes=True
    )
    with pytest.raises(DomainError) as exc_info:
        svc.dispatch(pid, "generate_frame_image",
                     {"segment_key": "S01G01", "cell_no": 0})
    assert exc_info.value.code == "INVALID_CELL_NO"
    assert ctx.frames.list_by_segment(pid, "S01G01") == []


# 多宫格分镜板 Task6：宫格同步 _sync_storyboard_grid + 锚点选择 _approved_anchor_frame


def _grid_sync_add_cell(ctx, pid: str, key: str, *, cell_no: int, version_no: int,
                        approved: bool = True):
    """造一张已落盘的格子行（PIL 真图写 media，字段口径同 T5 上传路径）。

    file_path 必须指向真实存在的图：同步函数会跳过未落盘的格子。
    """
    from PIL import Image

    from server.app.media import abs_media_path
    from server.domain.entities import SegmentFrameImage
    from server.domain.enums import AssetImageStatus

    rel = f"{pid}/frames/{key}_cell{cell_no}_v{version_no}.png"
    dest = abs_media_path(ctx.settings, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (cell_no * 40, 0, 0)).save(dest)
    row = SegmentFrameImage(
        frame_image_id=f"frm-cell{cell_no}v{version_no}",
        project_id=pid,
        segment_key=key,
        version_no=version_no,
        grid_cell=cell_no,
        provider="upload",
        file_path=rel,
        status=AssetImageStatus.UPLOADED,
        approved=approved,
    )
    ctx.frames.add(row)
    return row


def _first_grid_segment(svc, pid: str):
    """active 分镜的第一段（S01G01）。"""
    return next(
        s for s in svc._active_segments(pid)[1] if s.segment_key == "S01G01"
    )


def test_all_cells_approved_composes_grid_row(tmp_path) -> None:
    """2 格全批准（approve 命令流）→ 段内出现 view_label="分镜板" 的 approved 行，
    file_path 落盘 storyboards/，reference_paths 按格号 1..2 排序，锚点取宫格。"""
    from server.app.media import abs_media_path
    from server.domain.entities import STORYBOARD_GRID_LABEL

    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    c1 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1, approved=False)
    c2 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=2, version_no=2, approved=False)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c1.frame_image_id})
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c2.frame_image_id})
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    grids = [r for r in rows if r.view_label == STORYBOARD_GRID_LABEL]
    assert len(grids) == 1
    grid = grids[0]
    assert grid.approved is True
    assert "storyboards/" in grid.file_path
    assert grid.reference_paths == [c1.file_path, c2.file_path]
    assert abs_media_path(ctx.settings, grid.file_path).exists()
    anchor = svc._approved_anchor_frame(pid, "S01G01")
    assert anchor is not None and anchor.frame_image_id == grid.frame_image_id


def test_partial_approved_no_grid(tmp_path) -> None:
    """只批 1/2 格 → 无宫格行；_sync_storyboard_grid 返回 None。"""
    from server.domain.entities import STORYBOARD_GRID_LABEL

    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1)
    segment = _first_grid_segment(svc, pid)
    assert svc._sync_storyboard_grid(svc.get_project(pid), segment) is None
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    assert all(r.view_label != STORYBOARD_GRID_LABEL for r in rows)


def test_delete_cell_recomputes(tmp_path) -> None:
    """删格走 cmd_delete_frame_image → 宫格行被作废
    （删后只剩 1 格，不满足就绪条件 → 无宫格行）。"""
    from server.domain.entities import STORYBOARD_GRID_LABEL

    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    c1 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1)
    c2 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=2, version_no=2)
    segment = _first_grid_segment(svc, pid)
    grid = svc._sync_storyboard_grid(svc.get_project(pid), segment)
    assert grid is not None
    svc.dispatch(pid, "delete_frame_image", {"frame_image_id": c2.frame_image_id})
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    assert all(r.view_label != STORYBOARD_GRID_LABEL for r in rows)
    assert not any(r.frame_image_id == c2.frame_image_id for r in rows)
    assert any(r.frame_image_id == c1.frame_image_id for r in rows)


def test_anchor_prefers_grid_over_cells(tmp_path) -> None:
    """锚点确定性：有宫格行返回宫格（即便版本更低）；只有格子行返回最高版本
    格子行；无批准行返回 None。"""
    from server.domain.entities import STORYBOARD_GRID_LABEL, SegmentFrameImage
    from server.domain.enums import AssetImageStatus

    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    assert svc._approved_anchor_frame(pid, "S01G01") is None
    c1 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1)
    c2 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=2, version_no=3)
    anchor = svc._approved_anchor_frame(pid, "S01G01")
    assert anchor is not None and anchor.frame_image_id == c2.frame_image_id
    ctx.frames.add(
        SegmentFrameImage(
            frame_image_id="frm-grid", project_id=pid, segment_key="S01G01",
            version_no=2, view_label=STORYBOARD_GRID_LABEL,
            prompt="storyboard grid", provider="pillow",
            file_path=f"{pid}/storyboards/S01G01_grid_old.jpg",
            status=AssetImageStatus.READY, approved=True,
        )
    )
    anchor = svc._approved_anchor_frame(pid, "S01G01")
    assert anchor is not None and anchor.frame_image_id == "frm-grid"


# --------------------------------------------------------------------------
# 多宫格分镜板 Task7：视频侧接线（宫格占 <Picture 1> + 声明句透传）
# --------------------------------------------------------------------------


def _relay_second_segment() -> "Segment":
    """同场景后继段夹具（S01G02）：2-shot 可拼宫格，continuity 接力 S01G01，
    带一个角色 asset_ref——让"宫格让位"断言非空（参考图剥掉宫格后剩资产图）。"""
    from server.domain.entities import Continuity, H3Prompt, Segment, Shot

    return Segment(
        segment_key="S01G02",
        scene_id="S1",
        index=2,
        duration_sec=10,
        shots=[
            Shot(shot_no=1, cutpoint_sec=4.0, camera="wide",
                 description="on the river",
                 action="the old ferryman rows away from the pier"),
            Shot(shot_no=2, cutpoint_sec=10.0, camera="close",
                 description="looks back",
                 action="the old ferryman looks back at the shrinking pier"),
        ],
        asset_refs=[{"asset_id": "a-lead", "usage_note": "主角参考"}],
        continuity=Continuity(enabled=True, with_prev_segment_key="S01G01"),
        keyframe_description="",
        h3_prompt=H3Prompt(text=""),
    )


def test_resolve_references_prefers_grid(tmp_path) -> None:
    """段有已批准宫格 → Picture 1 = 宫格路径，is_storyboard=True 且带实际格数。"""
    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    c1 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1, approved=False)
    c2 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=2, version_no=2, approved=False)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c1.frame_image_id})
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c2.frame_image_id})
    paths, _mode, keyframe_path, is_storyboard, storyboard_cells = svc._resolve_references(
        svc.get_project(pid), _first_grid_segment(svc, pid), "sbv-grid"
    )
    assert is_storyboard is True
    assert storyboard_cells == 2
    assert paths[0] == keyframe_path
    assert "storyboards/" in paths[0]


def test_production_prompt_contains_storyboard_sentence(tmp_path) -> None:
    """产视频提示词：宫格声明句按实际格数声明（2 格段 → "2 cells"）。"""
    svc, _ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    prompt = svc._compile_production_prompt(
        svc.get_project(pid),
        _first_grid_segment(svc, pid),
        opening_frame=True,
        storyboard_reference=True,
        storyboard_cell_count=2,
    )
    assert "storyboard reference of 2 cells" in prompt
    assert "never appears on screen" in prompt


def test_relay_segment_grid_yields_to_tail_frame(tmp_path) -> None:
    """同场景后继段（尾帧接力）→ 宫格让位：Picture 1 留给运行时尾帧，
    参考图不含本段宫格（与现有关键帧让位机制等价，TASK-046）。"""
    from server.domain.entities import Asset, AssetImage, JobType
    from server.domain.enums import AssetImageStatus, AssetKind
    from server.domain.project import apply_action

    svc, ctx, pid = _grid_enqueue_fixture(
        tmp_path, [_two_shot_grid_segment(), _relay_second_segment()],
        through_keyframes=True,
    )
    ctx.assets.add_asset(
        Asset(asset_id="a-lead", project_id=pid, kind=AssetKind.CHARACTER,
              name="老船工", visual_anchor="灰白山羊胡、深色油皮外套")
    )
    ctx.assets.add_image(
        AssetImage(asset_image_id="img-a-lead", asset_id="a-lead", version_no=1,
                   view_label="主设定", file_path=f"{pid}/assets/a-lead.png",
                   status=AssetImageStatus.READY, approved=True)
    )
    # 两段各 2 格；version_no 加偏移避免 _grid_sync_add_cell 的 row id 跨段撞车
    for offset, key in enumerate(("S01G01", "S01G02")):
        c1 = _grid_sync_add_cell(
            ctx, pid, key, cell_no=1, version_no=offset * 2 + 1, approved=False
        )
        c2 = _grid_sync_add_cell(
            ctx, pid, key, cell_no=2, version_no=offset * 2 + 2, approved=False
        )
        svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c1.frame_image_id})
        svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c2.frame_image_id})
    moved = apply_action(ctx.projects.get(pid), "keyframes_generated")
    ctx.projects.save(moved)
    svc.dispatch(pid, "approve_keyframes", {})
    svc.dispatch(pid, "produce_video", {"scope": "all"})

    video_jobs = [j for j in ctx.jobs.list_all() if j.type is JobType.VIDEO_GEN]
    assert len(video_jobs) == 2
    by_key = {j.input_snapshot["segment_key"]: j.input_snapshot for j in video_jobs}
    head, relay = by_key["S01G01"], by_key["S01G02"]
    # 场景首段：宫格占 <Picture 1>（开场锚点）
    assert head["opening_frame_source"] == "keyframe"
    assert "storyboards/" in head["reference_paths"][0]
    # 同场景后继段：尾帧接力，宫格让位——参考图剥掉宫格后只剩资产图
    assert relay["opening_frame_source"] == "tail_frame"
    assert relay["continuity_prev_segment_key"] == "S01G01"
    assert relay["reference_paths"] == [f"{pid}/assets/a-lead.png"]
    # 宫格声明互斥：接力段绝不注入宫格声明；首段宫格声明已注入且声明实际格数
    assert "storyboard reference" not in relay["prompt"]
    assert "storyboard reference of 2 cells" in head["prompt"]


def _add_manual_frame_row(ctx, pid: str, key: str, *, version_no: int,
                          approved: bool = False):
    """造一张无格号手动覆盖行（已落盘真图，模拟前端整段上传/重抽，无 cell_no）。"""
    from PIL import Image

    from server.app.media import abs_media_path
    from server.domain.entities import SegmentFrameImage
    from server.domain.enums import AssetImageStatus

    rel = f"{pid}/frames/{key}_manual_v{version_no}.png"
    dest = abs_media_path(ctx.settings, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (10, 10, 10)).save(dest)
    row = SegmentFrameImage(
        frame_image_id=f"frm-manual-v{version_no}",
        project_id=pid,
        segment_key=key,
        version_no=version_no,
        view_label="上传",
        provider="upload",
        file_path=rel,
        status=AssetImageStatus.UPLOADED,
        approved=approved,
    )
    ctx.frames.add(row)
    return row


def test_manual_row_approval_supersedes_grid(tmp_path) -> None:
    """手动覆盖行（无格号）批准 → 宫格作废且不重拼（手动图优先占 Picture 1，
    设计 §5）；锚点回退手动行而非旧宫格（旧宫格不得遮蔽手动覆盖图）。"""
    from server.domain.entities import STORYBOARD_GRID_LABEL

    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    c1 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1, approved=False)
    c2 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=2, version_no=2, approved=False)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c1.frame_image_id})
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c2.frame_image_id})
    assert any(
        r.view_label == STORYBOARD_GRID_LABEL
        for r in ctx.frames.list_by_segment(pid, "S01G01")
    )
    # 用户整段上传（无 cell_no）并批准 → 宫格让位
    manual = _add_manual_frame_row(ctx, pid, "S01G01", version_no=4)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": manual.frame_image_id})
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    assert all(r.view_label != STORYBOARD_GRID_LABEL for r in rows)
    anchor = svc._approved_anchor_frame(pid, "S01G01")
    assert anchor is not None and anchor.frame_image_id == manual.frame_image_id


def test_manual_row_delete_restores_grid(tmp_path) -> None:
    """手动覆盖行删除 → 重新同步 → 全格仍就绪且无覆盖 → 宫格恢复重拼。"""
    from server.domain.entities import STORYBOARD_GRID_LABEL

    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    c1 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1, approved=False)
    c2 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=2, version_no=2, approved=False)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c1.frame_image_id})
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c2.frame_image_id})
    manual = _add_manual_frame_row(ctx, pid, "S01G01", version_no=4)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": manual.frame_image_id})
    assert all(
        r.view_label != STORYBOARD_GRID_LABEL
        for r in ctx.frames.list_by_segment(pid, "S01G01")
    )
    svc.dispatch(pid, "delete_frame_image", {"frame_image_id": manual.frame_image_id})
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    grids = [r for r in rows if r.view_label == STORYBOARD_GRID_LABEL]
    assert len(grids) == 1
    anchor = svc._approved_anchor_frame(pid, "S01G01")
    assert anchor is not None and anchor.frame_image_id == grids[0].frame_image_id


def test_unapprove_manual_row_restores_grid(tmp_path) -> None:
    """撤销批准手动覆盖行 → 宫格恢复重拼（恰 1 行、锚点为宫格行）。

    回归：撤销后该行已非 approved、宫格此前已被让位作废、手动行无格号，
    旧的 has_grid/has_manual/grid_cell 三条件触发判定全 False → 同步不触发
    → 宫格永不恢复；现口径改为"非宫格行批准/撤销批准即触发"。
    """
    from server.domain.entities import STORYBOARD_GRID_LABEL

    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    c1 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1, approved=False)
    c2 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=2, version_no=2, approved=False)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c1.frame_image_id})
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c2.frame_image_id})
    manual = _add_manual_frame_row(ctx, pid, "S01G01", version_no=4)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": manual.frame_image_id})
    assert all(
        r.view_label != STORYBOARD_GRID_LABEL
        for r in ctx.frames.list_by_segment(pid, "S01G01")
    )
    # 撤销批准手动行（approved=False 方向）→ 全格仍就绪且无让位 → 宫格恢复
    svc.dispatch(
        pid,
        "approve_frame_image",
        {"frame_image_id": manual.frame_image_id, "approved": False},
    )
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    grids = [r for r in rows if r.view_label == STORYBOARD_GRID_LABEL]
    assert len(grids) == 1
    anchor = svc._approved_anchor_frame(pid, "S01G01")
    assert anchor is not None and anchor.frame_image_id == grids[0].frame_image_id


def test_unapproved_cell_reroll_does_not_supersede_manual(tmp_path) -> None:
    """重抽产生未批准格子新版本（版本比已批准手动行新）→ 不得压过手动行
    触发让位误判；再触发任意 sync 后宫格仍缺席（未批准重抽不能把手动图
    遮回去），手动行仍是最新批准非宫格行。

    回归：旧口径 cell_vers 收录所有格子行（含未批准），重抽新版本行会抬高
    让位阈值 → 手动行不再"比所有格子新" → sync 重拼宫格遮蔽手动图。
    """
    from server.domain.entities import STORYBOARD_GRID_LABEL

    svc, ctx, pid = _grid_enqueue_fixture(tmp_path, [_two_shot_grid_segment()])
    c1 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=1, approved=False)
    c2 = _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=2, version_no=2, approved=False)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c1.frame_image_id})
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c2.frame_image_id})
    manual = _add_manual_frame_row(ctx, pid, "S01G01", version_no=4)
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": manual.frame_image_id})
    assert all(
        r.view_label != STORYBOARD_GRID_LABEL
        for r in ctx.frames.list_by_segment(pid, "S01G01")
    )
    # 重抽格子 1 → 未批准新版本行（version_no=5 比手动行 4 新）
    _grid_sync_add_cell(ctx, pid, "S01G01", cell_no=1, version_no=5, approved=False)
    # 触发任意 sync：再次"批准"已批准的格子行 2（幂等命令流触发同步）
    svc.dispatch(pid, "approve_frame_image", {"frame_image_id": c2.frame_image_id})
    rows = ctx.frames.list_by_segment(pid, "S01G01")
    assert all(r.view_label != STORYBOARD_GRID_LABEL for r in rows)
    # 手动行必须仍是最新批准非宫格行（锚点不被重抽版本抢走）
    anchor = svc._approved_anchor_frame(pid, "S01G01")
    assert anchor is not None and anchor.frame_image_id == manual.frame_image_id
