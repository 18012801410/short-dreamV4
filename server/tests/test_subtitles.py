"""台词规范化与成片字幕单测（TASK-019）。"""

from server.app.subtitles import _srt_timecode, build_film_srt
from server.domain.textnorm import clean_dialogue_line, strip_stage_directions
from server.tests.factories import make_reference_segment, make_segment

# --------------------------------------------------------------------------
# 舞台指示剥离
# --------------------------------------------------------------------------


def test_strip_stage_directions_variants() -> None:
    assert strip_stage_directions("（扑通跪下）林总，我错了！") == ("林总，我错了！", True)
    assert strip_stage_directions("(冷笑)晚了。") == ("晚了。", True)
    assert strip_stage_directions("河记得一切。") == ("河记得一切。", False)
    # 中途的指示也能剥
    assert strip_stage_directions("您……（脸色煞白）您是林总？") == ("您……您是林总？", True)


def test_clean_dialogue_line_drops_pure_directions() -> None:
    assert clean_dialogue_line("（把外卖扔地上）放这就行！") == "放这就行！"
    assert clean_dialogue_line("（打电话）") is None


# --------------------------------------------------------------------------
# 场景提示词消毒（人形词会反向引入人物，cfg=1 负向无效）
# --------------------------------------------------------------------------


def test_sanitize_scene_prompt_removes_people_words() -> None:
    from server.domain.textnorm import sanitize_scene_prompt

    dirty = ("empty night pier, no people, a security silhouette by the booth, "
             "wooden boat, crowd waiting, high detail")
    cleaned, removed = sanitize_scene_prompt(dirty)
    assert "people" not in cleaned and "silhouette" not in cleaned and "crowd" not in cleaned
    assert "empty night pier" in cleaned and "wooden boat" in cleaned
    assert len(removed) == 3


def test_sanitize_scene_prompt_chinese_words_and_tidy() -> None:
    from server.domain.textnorm import sanitize_scene_prompt

    cleaned, removed = sanitize_scene_prompt("高档小区入口, 人物剪影, 大理石地面, 保安人体模型")
    assert "人物" not in cleaned and "剪影" not in cleaned and "人体" not in cleaned
    assert "大理石地面" in cleaned and removed


def test_sanitize_scene_prompt_noop_clean_prompt() -> None:
    from server.domain.textnorm import sanitize_scene_prompt

    prompt = "Environment reference card of 深夜渡口, a deserted and empty place, 16:9"
    cleaned, removed = sanitize_scene_prompt(prompt)
    assert cleaned == prompt and removed == []


def test_sanitize_scene_prompt_replaces_staffed_props() -> None:
    """保安亭/门禁闸机自带"应有保安"的语义先验，必须换成中性设施词。"""
    from server.domain.textnorm import sanitize_scene_prompt

    cleaned, removed = sanitize_scene_prompt(
        "高档小区入口, 大理石地面, 保安亭为玻璃结构, 门禁闸机, 绿化带"
    )
    assert "保安亭" not in cleaned and "闸机" not in cleaned
    assert "空置玻璃亭" in cleaned and "出入闸栏" in cleaned
    assert set(removed) == {"保安亭", "门禁闸机"}


def test_scene_negative_blocks_panels_and_people() -> None:
    """TASK-042：场景卡必须是单幅广角空镜——负向通道（稳定版 cfg>1，
    IMAGE_NEGATIVE_NODE=7）同时拦人形词与分格/拼贴/多视图。"""
    from server.domain.enums import AssetKind
    from server.domain.textnorm import negative_for_kind

    scene_negative = negative_for_kind(AssetKind.SCENE)
    for word in ("people", "multiple panels", "collage", "split screen", "contact sheet"):
        assert word in scene_negative
    # 道具/角色卡负向不含分格词（它们不是场景，误加会干扰出图）
    assert "multiple panels" not in negative_for_kind(AssetKind.PROP)
    assert "multiple panels" not in negative_for_kind(AssetKind.CHARACTER)


# --------------------------------------------------------------------------
# SRT 时间轴
# --------------------------------------------------------------------------


def test_srt_timecode_format() -> None:
    assert _srt_timecode(0) == "00:00:00,000"
    assert _srt_timecode(63.5) == "00:01:03,500"
    assert _srt_timecode(3661.25) == "01:01:01,250"


def test_build_film_srt_windows_follow_shot_cutpoints() -> None:
    """段起点 = 前段实测时长累加；台词窗口 = 所在镜头 [切点, 下一镜切点)。"""
    seg1 = make_segment(
        key="S01G01",
        duration=4,
        shots=[
            make_segment_shot(1, 0.0, "河记得一切。"),
            make_segment_shot(2, 2.0, "上船吧。"),
        ],
    )
    seg2 = make_reference_segment(key="S01G02", index=2, duration=7)  # 台词在 #2（切点 3.0s）
    clip1 = make_clip_like("S01G01", 4.125)
    clip2 = make_clip_like("S01G02", 7.03)
    srt = build_film_srt([seg1, seg2], {"S01G01": clip1, "S01G02": clip2})
    # S01G01 #1：0–2s；#2：2–4.125s（末镜到实测段尾）
    assert "00:00:00,000 --> 00:00:02,000" in srt
    assert "河记得一切。" in srt
    assert "00:00:02,000 --> 00:00:04,125" in srt
    assert "上船吧。" in srt
    # S01G02 起点 4.125s，台词镜头窗口 4.125+3.0 → 段尾 11.155s
    assert "00:00:07,125 --> 00:00:11,155" in srt
    assert "the river remembers" in srt


def test_build_film_srt_strips_directions_and_splits_window() -> None:
    seg = make_segment(
        duration=4,
        shots=[
            make_segment_shot(1, 0.0, "（扑通跪下）林总，我错了！", speaker="王强"),
            make_segment_shot(2, 2.0, "（打电话）", speaker="林宇"),
        ],
    )
    srt = build_film_srt([seg], {"S01G01": make_clip_like("S01G01", 4.0)})
    assert "扑通" not in srt and "林总，我错了！" in srt
    # 纯舞台指示台词不产生字幕
    assert "打电话" not in srt


def test_build_film_srt_empty_without_dialogues() -> None:
    seg = make_segment(duration=4, shots=[make_segment_shot(1, 0.0, "")])
    assert build_film_srt([seg], {}) == ""


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def make_segment_shot(no: int, cutpoint: float, line: str, speaker: str = "老船工"):
    from server.domain.entities import Shot, ShotDialogueRef

    return Shot(
        shot_no=no,
        cutpoint_sec=cutpoint,
        camera="static",
        description="at the pier",
        dialogue_refs=[ShotDialogueRef(speaker=speaker, line=line)] if line else [],
    )


def make_clip_like(segment_key: str, duration: float):
    from server.domain.entities import Clip
    from server.domain.enums import VideoMode

    return Clip(
        clip_id=f"clip-{segment_key}",
        project_id="p-1",
        segment_key=segment_key,
        video_job_id="job-1",
        storyboard_version_id="sbv-1",
        file_path=f"{segment_key}.mp4",
        tail_frame_path=f"{segment_key}.jpg",
        duration_sec=duration,
        mode=VideoMode.R2VA,
    )
