"""实体不变量与校验规则单测（TASK-003；H3 结构契约对齐官方 h3-prompt-writing dialect）。"""

import pytest
from pydantic import ValidationError

from server.domain.entities import (
    AssetImage,
    AssetImageStatus,
    Continuity,
    H3Prompt,
    StoryboardContent,
)
from server.domain.enums import AssetKind, PromptLang
from server.domain.errors import ValidationFailedError
from server.domain.validation import (
    expected_picture_count,
    expects_reference_mode,
    has_continuity_tail,
    truncate_references,
    validate_asset_set,
    validate_h3_prompt,
    validate_segment,
)
from server.tests.factories import (
    BASE_H3_TEXT,
    REF_H3_TEXT,
    make_reference,
    make_reference_segment,
    make_segment,
    make_shot,
)

# --------------------------------------------------------------------------
# Segment 模型不变量（构造即拒绝）
# --------------------------------------------------------------------------


def test_valid_segment_constructs() -> None:
    segment = make_segment()
    assert segment.segment_key == "S01G01" and segment.duration_sec == 5


@pytest.mark.parametrize("duration", [3, 16, 0])
def test_segment_duration_out_of_range_rejected(duration: int) -> None:
    with pytest.raises(ValidationError):
        make_segment(duration=duration)


def test_segment_cutpoints_must_be_monotonic_and_within_duration() -> None:
    """切点一致性已从解析期下沉到 validate_segment（LLM 原始输出由规范化修复）。"""
    from server.domain.validation import validate_segment

    def expect_cutpoint_error(seg, keyword):

        with pytest.raises(ValidationFailedError) as ei:
            validate_segment(seg, known_asset_ids=set())
        assert any(keyword in e for e in ei.value.details["errors"]), ei.value.details

    expect_cutpoint_error(
        make_segment(shots=[make_shot(1, 0.5)]), "首镜切点必须为 0.00"
    )
    expect_cutpoint_error(
        make_segment(
            duration=10,
            shots=[make_shot(1, 0.0), make_shot(2, 4.0), make_shot(3, 4.0)],
        ),
        "切点必须严格单调递增",
    )
    expect_cutpoint_error(
        make_segment(shots=[make_shot(1, 0.0), make_shot(2, 5.0)]), "必须小于段时长"
    )


def test_segment_key_mismatch_allowed_at_parse_caught_in_domain_validation() -> None:
    """解析期不强制 key 一致（LLM 原始键由规范化修正）；域校验负责把关。"""
    seg = make_segment(key="S02G01", scene_id="S1", index=1)
    assert seg.segment_key == "S02G01"
    from server.domain.validation import validate_segment

    with pytest.raises(ValidationFailedError, match="校验失败"):
        validate_segment(seg, known_asset_ids=set())


def test_shot_numbers_must_be_consecutive() -> None:
    with pytest.raises(ValidationError, match="连续"):
        make_segment(shots=[make_shot(1, 0.0), make_shot(3, 2.0)])


def test_continuity_requires_prev_segment_key() -> None:
    with pytest.raises(ValidationError):
        Continuity(enabled=True)
    assert Continuity(enabled=True, with_prev_segment_key="S01G01").enabled


def test_storyboard_keys_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="唯一"):
        StoryboardContent(segments=[make_segment(), make_segment()])


def test_asset_image_approval_requires_landed_file() -> None:
    with pytest.raises(ValidationError):
        AssetImage(
            asset_image_id="ai-1",
            asset_id="a-1",
            version_no=1,
            view_label="主设定",
            status=AssetImageStatus.GENERATING,
            approved=True,
        )
    ok = AssetImage(
        asset_image_id="ai-1",
        asset_id="a-1",
        version_no=1,
        view_label="主设定",
        status=AssetImageStatus.READY,
        approved=True,
    )
    assert ok.approved


# --------------------------------------------------------------------------
# 跨实体校验：validate_segment / validate_asset_set
# --------------------------------------------------------------------------


def test_validate_segment_passes_with_known_assets() -> None:
    segment = make_reference_segment()
    validate_segment(segment, known_asset_ids={"a-start", "a-face", "a-2"})


def test_validate_segment_reports_unknown_asset_and_prompt_errors() -> None:
    segment = make_reference_segment(
        asset_refs=[{"asset_id": "ghost", "usage_note": "x"}],
        h3_text=REF_H3_TEXT.replace("non_diegetic_music:", "music:"),
    )
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_segment(segment, known_asset_ids={"a-1"})
    errors = excinfo.value.details["errors"]
    assert any("ghost" in e for e in errors)
    assert any("non_diegetic_music" in e for e in errors)


def test_validate_asset_set_uniqueness() -> None:
    validate_asset_set([("character", "林晚"), ("prop", "船桨")])
    with pytest.raises(ValidationFailedError, match="重复"):
        validate_asset_set([("character", "林晚"), ("character", "林晚")])


# --------------------------------------------------------------------------
# <Picture> 编号 ↔ 实际参考图一致性（TASK-018 一致性修复）
# --------------------------------------------------------------------------


CONTINUITY_H3_TEXT_OK = (
    "subject_definitions: <Picture 1> is the opening frame continuing seamlessly from "
    "the previous segment, showing the night pier. <Picture 2> is the pier composition "
    "plate. <Subject 1> is the old ferryman in <Picture 3>, with a weathered face and "
    "a dark oilskin coat.\n"
    "summary: [reference generation] The ferryman pushes his boat off and speaks.\n"
    "retention_analysis: <Picture 1> (appears in [Shot 1]): fully_preserved - opening "
    "composition from the previous segment.\n"
    "<Picture 2> (appears in [Shot 1]): fully_preserved - pier framing.\n"
    "<Picture 3> (appears in [Shot 1], [Shot 2]): fully_preserved - identity.\n"
    "detailed_description: [Shot 1] The scene continues exactly as <Picture 1>. "
    "[Shot 2] At 00:03.000, <Subject 1> (S1) speaks; his lips move as he says "
    "<d>[English] the river remembers</d>.\n"
    "overall_soundscape: Water lapping.\n"
    "non_diegetic_music: Sparse low strings."
)

CONTINUITY_H3_TEXT_STALE_NUMBERING = CONTINUITY_H3_TEXT_OK.replace(
    "<Subject 1> is the old ferryman in <Picture 3>",
    "<Subject 1> is the old ferryman in <Picture 2>",
).replace(
    "<Picture 3> (appears in [Shot 1], [Shot 2]): fully_preserved - identity.",
    "<Picture 2> (appears in [Shot 1], [Shot 2]): fully_preserved - identity.",
)


def test_expected_picture_count_counts_tail_frame() -> None:
    from server.domain.entities import Continuity

    segment = make_reference_segment(
        continuity=Continuity(enabled=True, with_prev_segment_key="S01G01")
    )
    assert has_continuity_tail(segment) and expected_picture_count(segment) == 3
    assert not has_continuity_tail(make_reference_segment())
    assert expected_picture_count(make_reference_segment()) == 2


def test_continuity_segment_numbered_from_picture_2_passes() -> None:
    from server.domain.entities import Continuity

    segment = make_reference_segment(
        continuity=Continuity(enabled=True, with_prev_segment_key="S01G01"),
        h3_text=CONTINUITY_H3_TEXT_OK,
    )
    assert validate_h3_prompt(segment.h3_prompt, segment) == []


def test_continuity_segment_stale_numbering_rejected() -> None:
    """旧惯例（连续段资产从 <Picture 1> 编起）必须被拦下——这是变脸根因。"""
    from server.domain.entities import Continuity

    segment = make_reference_segment(
        continuity=Continuity(enabled=True, with_prev_segment_key="S01G01"),
        h3_text=CONTINUITY_H3_TEXT_STALE_NUMBERING,
    )
    errors = validate_h3_prompt(segment.h3_prompt, segment)
    assert any("编号" in e and "Picture" in e for e in errors)
    assert any("<Picture 2>" in e for e in errors)


def test_d_block_with_stage_direction_rejected() -> None:
    """括号舞台指示会被 H3 读出声（「扑通跪下」变台词）——必须拦下。"""
    from server.tests.factories import REF_H3_TEXT

    dirty = REF_H3_TEXT.replace(
        "<d>[English] the river remembers</d>",
        "<d>[Chinese] （扑通跪下）the river remembers</d>",
    )
    segment = make_reference_segment(h3_text=dirty)
    errors = validate_h3_prompt(segment.h3_prompt, segment)
    assert any("舞台指示" in e for e in errors)


def test_base_segment_with_picture_label_rejected() -> None:
    base_with_picture = BASE_H3_TEXT.replace(
        "[Shot 1] Cinematic", "<Picture 1> guides framing. [Shot 1] Cinematic"
    )
    segment = make_segment(h3_text=base_with_picture)
    errors = validate_h3_prompt(segment.h3_prompt, segment)
    assert any("编号" in e for e in errors)


# --------------------------------------------------------------------------
# H3 提示词结构校验 · 模式选择
# --------------------------------------------------------------------------


def test_mode_selection_by_inputs() -> None:
    assert not expects_reference_mode(make_segment())
    assert expects_reference_mode(make_segment(asset_refs=[{"asset_id": "a-1"}]))
    assert expects_reference_mode(
        make_segment(continuity=Continuity(enabled=True, with_prev_segment_key="S01G00"))
    )


# --------------------------------------------------------------------------
# H3 提示词结构校验 · base 三段式
# --------------------------------------------------------------------------


def test_base_prompt_valid() -> None:
    assert validate_h3_prompt(H3Prompt(text=BASE_H3_TEXT), make_segment()) == []


def test_base_prompt_missing_field_rejected() -> None:
    text = BASE_H3_TEXT.replace("overall_soundscape: Water", "soundscape: Water")
    errors = validate_h3_prompt(H3Prompt(text=text), make_segment())
    assert any("三段(base)" in e and "overall_soundscape:" in e for e in errors)


def test_base_prompt_must_open_with_first_field() -> None:
    text = (
        "For the target video, at 0.00 seconds into the target video, <Picture 1> "
        "(from [Shot 1]) is fully referenced.\n" + BASE_H3_TEXT
    )
    errors = validate_h3_prompt(H3Prompt(text=text), make_segment())
    assert any("顶格开头" in e for e in errors)


def test_base_prompt_dialogue_without_lang_tag_rejected() -> None:
    text = BASE_H3_TEXT.replace("<d>[English] the river", "<d>the river")
    errors = validate_h3_prompt(H3Prompt(text=text), make_segment())
    assert any("语言标签" in e for e in errors)


# --------------------------------------------------------------------------
# H3 提示词结构校验 · reference 六段式
# --------------------------------------------------------------------------


def test_reference_prompt_valid() -> None:
    assert validate_h3_prompt(H3Prompt(text=REF_H3_TEXT), make_reference_segment()) == []


def test_reference_prompt_summary_prefix_required() -> None:
    text = REF_H3_TEXT.replace(
        "summary: [reference generation] The ferryman", "summary: The ferryman"
    )
    errors = validate_h3_prompt(H3Prompt(text=text), make_reference_segment())
    assert any("方括号任务前缀" in e for e in errors)


def test_reference_prompt_retention_marker_required() -> None:
    text = REF_H3_TEXT.replace(
        "<Picture 1> (appears in [Shot 1]): fully_preserved - pier",
        "<Picture 1> (appears in [Shot 1]): keeps - pier",
    )
    errors = validate_h3_prompt(H3Prompt(text=text), make_reference_segment())
    assert any("retention_analysis" in e for e in errors)


def test_reference_prompt_undefined_label_rejected() -> None:
    text = REF_H3_TEXT.replace(
        "<Picture 1> (appears in [Shot 1]): fully_preserved - pier",
        "<Picture 7> (appears in [Shot 1]): fully_preserved - pier",
    )
    errors = validate_h3_prompt(H3Prompt(text=text), make_reference_segment())
    assert any("未在 subject_definitions 定义" in e and "<Picture 7>" in e for e in errors)


def test_reference_prompt_shot_cutpoint_must_match_storyboard() -> None:
    text = REF_H3_TEXT.replace("[Shot 2] At 00:03.000", "[Shot 2] At 00:04.000")
    errors = validate_h3_prompt(H3Prompt(text=text), make_reference_segment())
    assert any("切点不一致" in e and "00:03.000" in e for e in errors)


def test_reference_prompt_first_shot_must_not_carry_timestamp() -> None:
    text = REF_H3_TEXT.replace("[Shot 1] Cold", "[Shot 1] At 00:00.000, cold")
    errors = validate_h3_prompt(H3Prompt(text=text), make_reference_segment())
    assert any("[Shot 1]" in e and "不携带" in e for e in errors)


def test_reference_prompt_back_reference_without_timestamp_allowed() -> None:
    text = REF_H3_TEXT.replace(
        "He unties the mooring rope.",
        "He unties the mooring rope, staying with the placement from [Shot 1].",
    )
    assert validate_h3_prompt(H3Prompt(text=text), make_reference_segment()) == []


def test_reference_prompt_shot_sequence_gap_rejected() -> None:
    text = REF_H3_TEXT.replace("[Shot 2] At 00:03.000", "[Shot 3] At 00:03.000")
    errors = validate_h3_prompt(H3Prompt(text=text), make_reference_segment())
    assert any("序号断裂" in e for e in errors)


# --------------------------------------------------------------------------
# H3 提示词结构校验 · 通用红线
# --------------------------------------------------------------------------


def test_prompt_dialogue_must_be_verbatim_in_d_block() -> None:
    text = REF_H3_TEXT.replace(
        "<d>[English] the river remembers</d>",
        "<d>[English] the river no longer remembers</d>",
    )
    errors = validate_h3_prompt(H3Prompt(text=text), make_reference_segment())
    assert any("逐字" in e for e in errors)


def test_prompt_dialogue_in_soundscape_rejected() -> None:
    text = REF_H3_TEXT.replace(
        "overall_soundscape: Water lapping",
        "overall_soundscape: He whispers <d>[English] the river remembers</d>; water lapping",
    )
    errors = validate_h3_prompt(H3Prompt(text=text), make_reference_segment())
    assert any("画面段时间线" in e for e in errors)


def test_prompt_music_field_must_not_be_empty() -> None:
    text = BASE_H3_TEXT.replace(
        "non_diegetic_music: Sparse low strings, slow tempo.", "non_diegetic_music:"
    )
    errors = validate_h3_prompt(H3Prompt(text=text), make_segment())
    assert any("N/A" in e for e in errors)


def test_prompt_over_7000_chars_rejected() -> None:
    padded = BASE_H3_TEXT + " a" * 4000
    errors = validate_h3_prompt(H3Prompt(text=padded), make_segment())
    assert any("超长" in e for e in errors)


def test_zh_lang_keeps_english_structure_fields() -> None:
    """结构字段恒英文（官方口径）；promptLang=zh 只切正文语言，对白仍逐字原文。"""
    zh_prose = (
        "integrated_multimodal_description: [Shot 1] 夜色码头，实拍质感。"
        "老船工按构图站在木栈道边缘，缓缓解缆推船，镜头轻推。"
        "画外音 <d>[中文] 河记得一切</d>，唇部完全闭合。\n"
        "overall_soundscape: 河水拍打木桩，远处虫鸣。\n"
        "non_diegetic_music: 低音弦乐，稀疏。"
    )
    segment = make_segment(
        shots=[make_shot(1, 0.0, dialogue_line="河记得一切")],
        h3_text=zh_prose,
        lang=PromptLang.ZH,
    )
    assert validate_h3_prompt(segment.h3_prompt, segment) == []


# --------------------------------------------------------------------------
# 参考图截断：>9 张按 asset_refs 顺序保序保留前 9（不重排，保 <Picture N> 编号）
# --------------------------------------------------------------------------


def test_truncate_references_keeps_original_order() -> None:
    refs = [
        make_reference(f"ch-{i}", AssetKind.CHARACTER, f"img-ch{i}") for i in range(5)
    ] + [make_reference(f"sc-{i}", AssetKind.SCENE, f"img-sc{i}") for i in range(3)] + [
        make_reference(f"pr-{i}", AssetKind.PROP, f"img-pr{i}") for i in range(2)
    ]
    kept, warnings = truncate_references(refs)
    assert len(kept) == 9 and len(warnings) == 1
    # 保序：kept 恰为前 9 张，顺序不变（编号 1..9 仍然有效）
    assert kept == refs[:9]
    assert "pr-1" in warnings[0]
    assert "asset_refs" in warnings[0]


def test_truncate_references_noop_under_limit() -> None:
    refs = [make_reference("a-1", AssetKind.CHARACTER, "img-1")]
    assert truncate_references(refs) == (refs, [])
