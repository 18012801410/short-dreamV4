"""H3 提示词编译器单测（TASK-032：身份唯一性 + 空间状态结构化）。

背景：重影（同一角色锚点在 Picture/Subject 两处出现 + 定妆卡摆拍短语泄漏）
与位置错误（关键帧无空间绑定、空镜锚点与有人镜头矛盾）的根治回归。
"""

from __future__ import annotations

import pytest

from server.app.h3_compiler import (
    compile_h3_prompt,
    compile_keyframe_prompt,
    sanitize_character_anchor,
    sanitize_scene_anchor,
)
from server.domain.entities import (
    Asset,
    AssetRef,
    H3Prompt,
    Segment,
    Shot,
    SubjectPlacement,
)
from server.domain.enums import AssetKind
from server.domain.errors import ValidationFailedError
from server.domain.validation import validate_segment
from server.tests.factories import make_segment, make_shot

LU_ANCHOR = (
    "二十多岁中国男性，中等身材，短黑发利落，穿深蓝色代驾工装外套，"
    "左胸有反光条，表情克制平静，直视镜头。"
)
WANG_ANCHOR = (
    "四十多岁中国男性，微胖，短黑发略油，穿深灰色西装外套内搭白色衬衫，直视镜头。"
)
CAR_ANCHOR = (
    "城市主干道夜景，霓虹流动，白色宝马轿车，高档餐厅门口，空无一人，车辆驾驶位空置。"
)

LU_PLACEMENT = "in the driver's seat, both hands on the steering wheel"
WANG_PLACEMENT = "sprawled across the rear seat, phone held to his right ear"


def make_assets() -> dict[str, Asset]:
    return {
        "a-lu": Asset(
            asset_id="a-lu", project_id="p-1", kind=AssetKind.CHARACTER,
            name="陆峥", visual_anchor=LU_ANCHOR,
        ),
        "a-wang": Asset(
            asset_id="a-wang", project_id="p-1", kind=AssetKind.CHARACTER,
            name="王建国", visual_anchor=WANG_ANCHOR,
        ),
        "a-car": Asset(
            asset_id="a-car", project_id="p-1", kind=AssetKind.SCENE,
            name="白色宝马车内", visual_anchor=CAR_ANCHOR,
        ),
    }


def make_driving_segment(
    assets: dict[str, Asset],
    *,
    placements: list[SubjectPlacement] | None = None,
    keyframe_description: str = "",
    with_dialogue: bool = True,
) -> Segment:
    """S02G02 同构段：陆峥（未说话）+ 王建国（说话）在车内，场景+双角色参考。"""
    from server.domain.entities import ShotDialogueRef

    dialogue = (
        [ShotDialogueRef(speaker="王建国", line="你开慢点。", tone="impatiently")]
        if with_dialogue
        else []
    )
    return make_segment(
        key="S02G02",
        scene_id="S2",
        index=2,
        duration=6,
        asset_refs=[
            AssetRef(asset_id="a-car", usage_note="场景"),
            AssetRef(asset_id="a-lu", usage_note="主角"),
            AssetRef(asset_id="a-wang", usage_note="乘客"),
        ],
        shots=[
            make_shot(1, 0.0),
            Shot(
                shot_no=2,
                cutpoint_sec=3.0,
                camera="static shot",
                description="driving at night",
                action=(
                    "Lu Zheng keeps both hands on the wheel in the driver seat; "
                    "Wang Jianguo sprawled in the rear holds his phone to his ear."
                ),
                dialogue_refs=dialogue,
            ),
        ],
    ).model_copy(
        update={
            "subject_placements": placements
            if placements is not None
            else [
                SubjectPlacement(name="陆峥", placement=LU_PLACEMENT),
                SubjectPlacement(name="王建国", placement=WANG_PLACEMENT),
            ],
            "keyframe_description": keyframe_description,
        }
    )


def compile_video(segment: Segment, assets: dict[str, Asset]) -> str:
    return compile_h3_prompt(segment, assets, style_line="Cinematic test style.")


# --------------------------------------------------------------------------
# 视频提示词：身份唯一性（重影根治）
# --------------------------------------------------------------------------


def test_character_anchor_appears_exactly_once_in_video_prompt() -> None:
    """锚点全文只允许出现在 <Subject N> 定义一处——Picture/Subject 双写会被
    执行端读成两个人（S03G03 三克隆、S01G02 双风衣男根因）。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_video(segment, assets)

    assert text.count("短黑发利落") == 1
    assert text.count("微胖") == 1


def test_character_picture_definition_declares_identity_card_role() -> None:
    """角色 <Picture N> 只声明参考图角色，不重复外貌锚点。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_video(segment, assets)

    assert (
        "<Picture 2> is the character identity reference card of 陆峥 "
        "(identity only, not an additional on-scene person)." in text
    )
    picture_line = next(
        line for line in text.splitlines() if line.startswith("<Picture 2>")
    )
    assert "短黑发利落" not in picture_line


def test_card_pose_phrases_stripped_from_anchors() -> None:
    """「直视镜头」等定妆卡摆拍短语不得进入剧情提示词。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_video(segment, assets)

    assert "直视镜头" not in text
    assert sanitize_character_anchor(LU_ANCHOR).endswith("表情克制平静")


def test_full_body_card_framing_stripped_from_anchor() -> None:
    """TASK-043：角色卡改为正面全身定妆照后，全身/立绘/full-body 这类取景词
    只属卡片本身；留在锚点会把剧情镜头锁成全身站姿。（既有 LLM 产出里
    "身高中等、身形挺拔"这类身材事实不受影响。）"""
    anchor = (
        "三十岁男性，中等身材偏瘦，黑色短发，穿深灰风衣，"
        "全身立绘正面照，full-body standing pose，head to toe in frame"
    )
    cleaned = sanitize_character_anchor(anchor)
    assert "全身" not in cleaned
    assert "立绘" not in cleaned
    assert "full-body" not in cleaned.lower()
    assert "head to toe" not in cleaned.lower()
    # 身材与服装事实保留
    assert "中等身材偏瘦" in cleaned
    assert "穿深灰风衣" in cleaned


def test_silent_character_gets_subject_definition_and_speaker_gets_id() -> None:
    """在场角色逐一建立 <Subject N>（不留没名字的人）；说话人追加 (Sx)
    且 detailed_description 用 <Subject N> (Sx) 引出台词。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_video(segment, assets)

    assert "<Subject 1> is 陆峥 in <Picture 2>" in text
    assert "<Subject 2> is 王建国 (S1) in <Picture 3>" in text
    assert "<Subject 2> (S1) says in Chinese" in text


def test_prop_anchor_keeps_identity_drops_card_staging() -> None:
    """道具没有 <Subject N>，锚点是唯一身份载体：形状/标记必须保留，
    白底/崭新未使用等卡片取景词必须剥离（否则剧情镜头会被推向白底产品图）。"""
    assets = make_assets()
    assets["a-key"] = Asset(
        asset_id="a-key", project_id="p-1", kind=AssetKind.PROP,
        name="真皮钥匙包",
        visual_anchor="棕色真皮钥匙包，带小貔貅挂坠，崭新未使用，白底。",
    )
    segment = make_driving_segment(assets)
    segment = segment.model_copy(
        update={
            "asset_refs": [
                *segment.asset_refs,
                AssetRef(asset_id="a-key", usage_note="道具"),
            ]
        }
    )
    text = compile_video(segment, assets)
    assert "带小貔貅挂坠" in text
    assert "白底" not in text
    assert "崭新未使用" not in text

    keyframe = compile_keyframe_prompt(segment, assets)
    assert "visible props: 棕色真皮钥匙包，带小貔貅挂坠" in keyframe
    assert "白底" not in keyframe


def test_scene_anchor_empty_state_stripped() -> None:
    """「空无一人/空置」等空镜状态与有人镜头矛盾，编译时确定性剥离；
    布局信息保留。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_video(segment, assets)

    assert "空无一人" not in text
    assert "空置" not in text
    assert "高档餐厅门口" in text
    assert sanitize_scene_anchor(CAR_ANCHOR) == (
        "城市主干道夜景，霓虹流动，白色宝马轿车，高档餐厅门口"
    )


def test_scene_anchor_framing_phrases_stripped() -> None:
    """TASK-042：场景卡改为单幅广角全貌后，取景词（广角/机位/视角/构图）
    属于卡片本身；留在锚点会被嵌进每个剧情镜头，把景别锁成广角远景。"""
    anchor = (
        "超广角机位拍摄的江边夜市，从街道一端取景，木栈道与烧烤摊分列两侧，"
        "冷蓝夜色，wide-angle lens view, 24mm"
    )
    cleaned = sanitize_scene_anchor(anchor)
    assert "广角" not in cleaned
    assert "机位" not in cleaned
    assert "取景" not in cleaned
    assert "wide-angle" not in cleaned.lower()
    # 空间事实（地标/布局/光态）保留
    assert "木栈道与烧烤摊分列两侧" in cleaned
    assert "冷蓝夜色" in cleaned


def test_keyframe_scene_line_declares_wide_angle_reference() -> None:
    """TASK-042/045：空镜卡在关键帧提示词里被描述为"同一空间的广角全貌"，
    用 Picture N 指称且不给构图锁——取景由画面描述承担。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_keyframe_prompt(
        segment,
        assets,
        reference_bindings=[assets["a-lu"], assets["a-car"]],
    )
    # TASK-045：参考图用 Picture N 指称（节点把参考图编码为 Picture 1/2/3），
    # 场景卡描述为"同一空间的广角全貌"；仍不给"空间结构不变"式锁定
    # （空座位会被锁住，TASK-037 实测）
    assert "Picture 2 is a wide-angle overview of the same location（白色宝马车内）" in text
    assert "空间结构" not in text
    assert "保持不变" not in text


def test_keyframe_identity_line_declares_reference_is_identity_only() -> None:
    """TASK-043/045：有参考图时身份锁定是一句话（脸/发型/服装与 Picture
    参考完全一致），不再写 [imgX] 元指令。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_keyframe_prompt(
        segment,
        assets,
        reference_bindings=[assets["a-lu"], assets["a-car"]],
    )
    # TASK-045：身份锁定改为一句话（含脸部与服装），不再用 [imgX] 与元指令
    assert (
        "Keep every character's face, hairstyle and clothing exactly the "
        "same as in their Picture reference." in text
    )
    assert "[img" not in text
    assert "角色参考图只用于身份" not in text


# --------------------------------------------------------------------------
# 视频提示词：开场位置注入（位置错误根治）
# --------------------------------------------------------------------------


def test_opening_placement_injected_into_subject_definition() -> None:
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_video(segment, assets)

    assert f"opening placement: {LU_PLACEMENT}," in text
    assert f"opening placement: {WANG_PLACEMENT}," in text
    assert "identity kept identical in every shot." in text


def test_segment_without_placements_still_compiles() -> None:
    """存量分镜无 subject_placements：跳过位置行，其余修复照常生效。"""
    assets = make_assets()
    segment = make_driving_segment(assets, placements=[])
    text = compile_video(segment, assets)

    assert "opening placement" not in text
    assert "kept identical in every shot." in text
    assert text.count("短黑发利落") == 1
    assert "空无一人" not in text


# --------------------------------------------------------------------------
# 关键帧提示词：单实例声明 + 位置绑定 + 静态纯净
# --------------------------------------------------------------------------


def test_keyframe_prompt_binds_characters_to_placements() -> None:
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_keyframe_prompt(segment, assets, style_line="Cinematic test style.")

    assert "Exactly 2 people in the frame, no other people" in text
    assert f"陆峥（穿深蓝色代驾工装外套）{LU_PLACEMENT}" in text
    assert f"王建国（穿深灰色西装外套内搭白色衬衫）{WANG_PLACEMENT}" in text
    # 画面描述式：不再出现"每个角色只出现一次"这类元指令（TASK-036）
    assert "each appearing exactly once" not in text
    assert "in the stated position" not in text
    assert "空无一人" not in text
    assert "空置" not in text


def test_keyframe_prompt_excludes_off_frame_character() -> None:
    """TASK-033：本段在场但开场帧看不到的角色不进人名单，改为正向状态句——
    这一帧的画面人数被锁死，模型不会自行补人（实测背景多出人影的根因）。"""
    assets = make_assets()
    segment = make_driving_segment(
        assets,
        placements=[
            SubjectPlacement(name="陆峥", placement=WANG_PLACEMENT),
            SubjectPlacement(name="王建国", placement=LU_PLACEMENT, in_frame=False),
        ],
    )
    text = compile_keyframe_prompt(segment, assets, style_line="")

    assert "Exactly 1 person in the frame, no other people" in text
    assert "王建国 在画面外" in text
    assert "其余空间为空" in text
    # 画面外角色不进画面人名单
    lock_line = text[text.find("Exactly 1 person"):]
    assert "微胖" not in lock_line.split("王建国 在画面外")[0]


def test_keyframe_prompt_all_characters_off_frame() -> None:
    """纯环境开场帧（角色全在画面外）：不造人名单，直接声明画面无人。"""
    assets = make_assets()
    segment = make_driving_segment(
        assets,
        placements=[
            SubjectPlacement(name="陆峥", placement=LU_PLACEMENT, in_frame=False),
            SubjectPlacement(name="王建国", placement=WANG_PLACEMENT, in_frame=False),
        ],
    )
    text = compile_keyframe_prompt(segment, assets, style_line="")

    assert "画面内无人" in text
    assert "在画面外" in text
    assert "空间为空" in text


def test_keyframe_fallback_strips_dialogue_and_speech() -> None:
    """回退路径（存量段无 keyframe_description）必须把台词块与口型句剥成
    纯静态描述——静态帧会把台词渲染成画面文字（S01G02 胸牌文字实证）。"""
    assets = make_assets()
    segment = make_driving_segment(assets, placements=[])
    first = segment.shots[0].model_copy(
        update={
            "action": (
                "Medium interior shot of the moving white BMW. Wang Jianguo says "
                "in Chinese, impatiently, <d>[中文] 你开慢点。</d> Lu Zheng keeps "
                "both hands on the wheel, lips moving as they speak."
            )
        }
    )
    segment = segment.model_copy(
        update={"shots": [first, segment.shots[1]], "keyframe_description": ""}
    )
    text = compile_keyframe_prompt(segment, assets, style_line="")

    assert "<d>" not in text
    assert "你开慢点" not in text
    assert "says" not in text
    assert "lips moving" not in text
    assert "Lu Zheng keeps both hands on the wheel" in text


# --------------------------------------------------------------------------
# 校验闭环：placement 完整性 + 关键帧纯静态（分镜生成时强制）
# --------------------------------------------------------------------------


def _character_assets(assets: dict[str, Asset]) -> dict[str, str]:
    return {
        a.asset_id: a.name for a in assets.values() if a.kind is AssetKind.CHARACTER
    }


def test_validate_segment_requires_placement_for_present_characters() -> None:
    assets = make_assets()
    segment = make_driving_segment(assets, placements=[])
    with pytest.raises(ValidationFailedError) as exc:
        validate_segment(
            segment,
            known_asset_ids=set(assets),
            character_assets=_character_assets(assets),
        )
    errors = "；".join(exc.value.details["errors"])
    assert "subject_placements" in errors
    assert "陆峥" in errors and "王建国" in errors


def test_validate_segment_flags_unnamed_people_in_keyframe_description() -> None:
    """TASK-045：keyframe_description 里的可见人物必须逐人具名。

    "Two girls…A third girl…"（S02G02 实测原句）没有参考卡，图生图模型把参考里
    的小伙伴乙复印成两个一模一样的、第三个消失；"A chubby boy"（S02G04 原句）
    则被画成另一个参考角色的复制品。这类描述在分镜阶段就要打回重写。
    """
    assets = make_assets()
    segment = make_driving_segment(
        assets,
        keyframe_description=(
            "Medium shot of the hutong open ground. Two girls stand facing each "
            "other, and a chubby boy sits opposite them."
        ),
    )
    with pytest.raises(ValidationFailedError) as exc:
        validate_segment(
            segment,
            known_asset_ids=set(assets),
            character_assets=_character_assets(assets),
        )
    errors = "；".join(exc.value.details["errors"])
    assert "无名人物" in errors


def test_validate_segment_accepts_named_only_keyframe_description() -> None:
    """逐人具名的描述不受影响；"two bicycles / her two hands" 这类非人物词不误伤。"""
    assets = make_assets()
    segment = make_driving_segment(
        assets,
        keyframe_description=(
            "Medium shot: 陆峥 sits in the driver's seat with both hands on the "
            "steering wheel; 王建国 sprawls across the rear seat. Two bicycles "
            "lean against the wall outside."
        ),
    )
    segment = segment.model_copy(
        update={"h3_prompt": H3Prompt(text=compile_video(segment, assets))}
    )
    validate_segment(
        segment,
        known_asset_ids=set(assets),
        character_assets=_character_assets(assets),
    )


def test_validate_segment_flags_orphan_and_cjk_placements() -> None:
    assets = make_assets()
    segment = make_driving_segment(
        assets,
        placements=[
            SubjectPlacement(name="路人甲", placement="behind the counter"),
            SubjectPlacement(name="陆峥", placement="坐在驾驶座"),
        ],
    )
    with pytest.raises(ValidationFailedError) as exc:
        validate_segment(
            segment,
            known_asset_ids=set(assets),
            character_assets=_character_assets(assets),
        )
    errors = "；".join(exc.value.details["errors"])
    assert "不在本段 asset_refs" in errors
    assert "英文位置状态" in errors


def test_validate_segment_accepts_complete_placements() -> None:
    assets = make_assets()
    segment = make_driving_segment(assets)
    segment = segment.model_copy(
        update={
            "h3_prompt": H3Prompt(text=compile_video(segment, assets)),
        }
    )
    validate_segment(
        segment,
        known_asset_ids=set(assets),
        character_assets=_character_assets(assets),
    )


def test_validate_segment_legacy_path_skips_placement_check() -> None:
    """存量编辑/重生成路径不传 character_assets：无 placements 不拦截。"""
    assets = make_assets()
    segment = make_driving_segment(assets, placements=[])
    segment = segment.model_copy(
        update={"h3_prompt": H3Prompt(text=compile_video(segment, assets))}
    )
    validate_segment(segment, known_asset_ids=set(assets))


def test_validate_segment_flags_dynamic_keyframe_description() -> None:
    assets = make_assets()
    segment = make_driving_segment(
        assets,
        keyframe_description=(
            "Lu Zheng sits in the driver seat and says in Chinese: 你好. "
            "His lips move clearly."
        ),
    )
    segment = segment.model_copy(
        update={"h3_prompt": H3Prompt(text=compile_video(segment, assets))}
    )
    with pytest.raises(ValidationFailedError) as exc:
        validate_segment(
            segment,
            known_asset_ids=set(assets),
            character_assets=_character_assets(assets),
        )
    errors = "；".join(exc.value.details["errors"])
    assert "纯静态" in errors


def test_keyframe_prompt_omits_identity_anchors_when_references_used() -> None:
    """TASK-036：走图生图通道（有角色卡参考图）时正文不写身份串——身份交给
    参考图，提示词专注画面内容；关掉参考图（纯文本生图）时身份串必须回来。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    with_refs = compile_keyframe_prompt(segment, assets, include_identity_anchors=False)
    without_refs = compile_keyframe_prompt(segment, assets, include_identity_anchors=True)

    assert "短黑发利落" not in with_refs
    assert "Exactly 2 people in the frame, no other people" in with_refs
    assert "短黑发利落" in without_refs
    assert "人物脸部与服装必须与上述描写完全一致" in without_refs


def test_keyframe_prompt_uses_single_location_line() -> None:
    """TASK-036：本段引用两个场景资产时，地点只写第一个——此前的双 setting
    会让模型收到两条互相打架的地点行。"""
    assets = make_assets()
    assets["a-cabin"] = Asset(
        asset_id="a-cabin", project_id="p-1", kind=AssetKind.SCENE,
        name="末班公交车车厢", visual_anchor="蓝色塑料座椅，扶手杆，吊环，惨白车内灯",
    )
    segment = make_driving_segment(assets)
    segment = segment.model_copy(
        update={
            "asset_refs": [
                *segment.asset_refs,
                AssetRef(asset_id="a-cabin", usage_note="场景二"),
            ]
        }
    )
    text = compile_keyframe_prompt(segment, assets)
    assert text.count("background:") == 1
    assert "高档餐厅门口" in text
    assert "蓝色塑料座椅" not in text


def test_keyframe_prompt_carries_short_outfit_tags() -> None:
    """TASK-036：两人同框时给每个角色带短服装标签（区分度最高的视觉特征），
    防止模型拿错角色卡的衣服；整段身份串仍只在纯文本生图时出现。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_keyframe_prompt(segment, assets, include_identity_anchors=False)

    assert "陆峥（穿深蓝色代驾工装外套）" in text
    assert "王建国（穿深灰色西装外套内搭白色衬衫）" in text
    # 短标签只取衣着子句：身材/表情/姿势描述不进正文
    assert "中等身材" not in text
    assert "表情克制平静" not in text


def test_keyframe_prompt_is_i2i_edit_instruction_with_bindings() -> None:
    """TASK-045：关键帧 I2I 指令用 Picture N 指称参考图、正向声明人数、
    一句话锁定身份——不再使用 [imgX] 占位符（ComfyUI 原生不解析）与元指令。"""
    assets = make_assets()
    assets["a-badge"] = Asset(
        asset_id="a-badge", project_id="p-1", kind=AssetKind.PROP, name="旧工牌",
        visual_anchor="旧工牌，塑料卡套，磨损挂绳，白底。",
    )
    segment = make_driving_segment(assets)
    segment = segment.model_copy(
        update={
            "asset_refs": [
                *segment.asset_refs,
                AssetRef(asset_id="a-badge", usage_note="道具"),
            ]
        }
    )
    bindings = [assets["a-lu"], assets["a-wang"], assets["a-car"]]
    text = compile_keyframe_prompt(
        segment,
        assets,
        style_line="cinematic realistic style",
        include_identity_anchors=False,
        reference_bindings=bindings,
        aspect="9:16",
    )

    # TASK-045：参考图指称必须用 Picture N（TextEncodeQwenImageEditPlus 的槽位
    # 标签），[img0] 占位符原生不被解析，等于噪声
    assert "Picture 1 is 陆峥's character reference card" in text
    assert "Picture 2 is 王建国's character reference card" in text
    assert "Picture 3 is a wide-angle overview of the same location（白色宝马车内）" in text
    assert "[img" not in text
    assert "保持这些人物的身份不变" not in text
    assert "空间结构" not in text
    # 人数正向声明（负向通道对该模型基本无效，issue #120 实测）
    assert "Exactly 2 people in the frame, no other people" in text
    # 一句话身份锁定
    assert (
        "Keep every character's face, hairstyle and clothing exactly the "
        "same as in their Picture reference." in text
    )
    # 顺序：人数与身份 → 背景 → 道具 → 参考图指称
    assert text.index("Exactly 2 people") < text.index("background:")
    assert text.index("background:") < text.index("visible props:")
    assert text.index("visible props:") < text.index("Picture 1 is")
    assert "9:16 画幅" in text
    assert text.endswith("single frame composition, photorealistic detail, high detail")


def test_keyframe_prompt_without_bindings_has_no_keep_lines() -> None:
    """纯文生图（无参考图）时不写 [imgX] 保留项，改为自足的画面描述 + 身份锚点。"""
    assets = make_assets()
    segment = make_driving_segment(assets)
    text = compile_keyframe_prompt(segment, assets, include_identity_anchors=True)
    assert "[img0]" not in text
    assert "保持这些人物的身份不变" not in text
    assert "短黑发利落" in text  # 身份锚点补位


def test_keyframe_prompt_adds_composition_rules_but_no_meta_instructions() -> None:
    """TASK-039 保留构图细则（那是真实的画面描述）；TASK-045 删除方向性物体
    元指令——"写清人物朝向…"是对分镜作者说的话，扩散模型不执行指令，
    只会把提示词稀释成噪声。"""
    assets = make_assets()
    assets["a-phone"] = Asset(
        asset_id="a-phone", project_id="p-1", kind=AssetKind.PROP, name="手机",
        visual_anchor="黑色手机，屏幕朝上，屏幕有裂纹。",
    )
    segment = make_driving_segment(assets)
    segment = segment.model_copy(
        update={
            "asset_refs": [
                *segment.asset_refs,
                AssetRef(asset_id="a-phone", usage_note="道具"),
            ]
        }
    )
    portrait = compile_keyframe_prompt(segment, assets, aspect="9:16")
    landscape = compile_keyframe_prompt(segment, assets, aspect="16:9")

    assert "竖屏构图：人脸与上半身落在画面安全区内" in portrait
    assert "关键道具放在画面中部或下三分之一" in portrait
    assert "横屏构图：三分法构图" in landscape
    assert "180 度轴线" in landscape
    # 方向性物体元指令不再进图像提示词（写画面的方向细节是分镜描述的职责）
    assert "方向性物体" not in portrait
    assert "写清人物朝向" not in portrait


def test_keyframe_prompt_never_carries_directional_meta_rule() -> None:
    """TASK-045：无论画面里有没有方向性物体，元指令都不再进图像提示词。"""
    assets = make_assets()
    assets["a-phone"] = Asset(
        asset_id="a-phone", project_id="p-1", kind=AssetKind.PROP, name="手机",
        visual_anchor="黑色手机，屏幕朝上，屏幕有裂纹。",
    )
    segment = make_driving_segment(assets)
    segment = segment.model_copy(
        update={
            "asset_refs": [
                *segment.asset_refs,
                AssetRef(asset_id="a-phone", usage_note="道具"),
            ]
        }
    )
    text = compile_keyframe_prompt(segment, assets, aspect="9:16")
    assert "方向性物体" not in text
