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
