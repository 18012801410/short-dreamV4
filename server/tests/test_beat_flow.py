"""节拍流（beat flow）测试：剧本画面描述不丢、动作归位不删、编译全注入。

覆盖：旧剧本向后兼容、ScriptAgent 节拍派生与降级回填、h3_compiler 的
action/tone/soundscape/music/style 注入、分镜 beat_refs 认领对账。
"""


from server.app.agents import Agents
from server.app.h3_compiler import (
    DEFAULT_MUSIC,
    DEFAULT_SOUNDSCAPE,
    DEFAULT_STYLE_LINE,
    RELAY_ANCHOR_EN,
    compile_h3_prompt,
)
from server.domain.entities import (
    Beat,
    DialogueLine,
    H3Prompt,
    Scene,
    ScriptContent,
    Segment,
    Shot,
    ShotDialogueRef,
    StoryboardContent,
    SubjectPlacement,
)
from server.domain.enums import AssetKind, BeatType
from server.tests.factories import make_asset


class FakeResult:
    def __init__(self, data) -> None:
        self.data = data
        from server.domain.providers import LlmUsage

        self.usage = LlmUsage(total_tokens=1)
        self.attempts = 1


class OneShotLlm:
    def __init__(self, content) -> None:
        self.content = content

    def chat_json(self, *, system, user, schema, temperature):
        return FakeResult(self.content)

    def chat(self, **kwargs):
        return "pong"


def make_beat_script() -> ScriptContent:
    """EP001 口径剧本：一场戏 action/dialogue/sfx/on_screen_text 节拍齐备。"""
    return ScriptContent(
        logline="老船工最后的摆渡",
        scenes=[
            Scene(
                id="S1",
                title="外 · 夜色渡口 · 夜",
                summary="夜色渡口，老船工系缆候客",
                est_seconds=8,
                beats=[
                    Beat(type=BeatType.ACTION, text="老船工把缆绳在木桩上绕了两圈，抬头望向河面"),
                    Beat(
                        type=BeatType.DIALOGUE,
                        text="（声音很低）上船吧。",
                        speaker="老船工",
                        tone="声音很低",
                    ),
                    Beat(type=BeatType.SFX, text="河水拍打木桩声"),
                    Beat(type=BeatType.ON_SCREEN_TEXT, text="手机屏幕弹出：您有新订单"),
                ],
            ),
        ],
        characters=[{"name": "老船工", "profile": "沉默的摆渡人"}],
        props=["缆绳"],
    )


# --------------------------------------------------------------------------
# 向后兼容：旧剧本没有 beats 字段照常加载
# --------------------------------------------------------------------------


def test_old_script_without_beats_loads() -> None:
    content = ScriptContent.model_validate({
        "logline": "老船工最后的摆渡",
        "scenes": [
            {"id": "S1", "title": "渡口", "summary": "夜色渡口", "est_seconds": 8,
             "dialogues": [{"speaker": "老船工", "line": "上船吧。"}]},
        ],
    })
    assert content.scenes[0].beats == []
    assert content.scenes[0].dialogues[0].tone == ""


# --------------------------------------------------------------------------
# ScriptAgent：dialogues 从节拍派生，括号语气归位不丢弃
# --------------------------------------------------------------------------


def test_run_script_derives_dialogues_from_beats() -> None:
    content = Agents(OneShotLlm(make_beat_script())).run_script(
        "想法", {"target_duration_sec": 20}
    )
    scene = content.scenes[0]
    # 节拍流原样保留
    assert [b.type for b in scene.beats] == [
        BeatType.ACTION, BeatType.DIALOGUE, BeatType.SFX, BeatType.ON_SCREEN_TEXT,
    ]
    assert scene.beats[0].text.startswith("老船工把缆绳")
    # dialogues 从 dialogue 节拍派生；括号舞台指示归位为语气而不是删除
    assert scene.dialogues == [
        DialogueLine(speaker="老船工", line="上船吧。", tone="声音很低")
    ]
    assert scene.title == "外 · 夜色渡口 · 夜"


def test_run_script_merges_beat_tone_and_parenthetical() -> None:
    script = make_beat_script()
    script.scenes[0].beats[1].text = "（不耐烦）快点上船。"
    script.scenes[0].beats[1].tone = "压低嗓门"
    content = Agents(OneShotLlm(script)).run_script("想法", {})
    line = content.scenes[0].dialogues[0]
    assert line.line == "快点上船。"
    assert line.tone == "压低嗓门 不耐烦"


def test_run_script_backfills_missing_beats_with_warning() -> None:
    script = ScriptContent(
        logline="旧式剧本",
        scenes=[
            Scene(
                id="S1", title="渡口", summary="夜色渡口", est_seconds=8,
                dialogues=[DialogueLine(speaker="老船工", line="上船吧。")],
            ),
            Scene(id="S2", title="河心", summary="船到河心", est_seconds=4),
        ],
    )
    content = Agents(OneShotLlm(script)).run_script("想法", {"target_duration_sec": 20})
    s1, s2 = content.scenes
    # 有台词无节拍 → 从台词回退 dialogue 节拍
    assert [b.type for b in s1.beats] == [BeatType.DIALOGUE]
    assert s1.beats[0].text == "上船吧。"
    # 无节拍无台词 → 概要回退一条动作节拍
    assert [b.type for b in s2.beats] == [BeatType.ACTION]
    assert s2.beats[0].text == "船到河心"
    assert sum("缺节拍流" in w for w in content.warnings) == 1
    assert sum("无任何节拍" in w for w in content.warnings) == 1


def test_run_script_warns_when_dialogue_exceeds_speech_budget() -> None:
    """台词总字数超过目标时长口播预算（4 字/秒 × 0.75）时必须给出 warning，
    否则分镜阶段「台词逐字对账 vs 口播预算」会死锁。"""
    content = Agents(OneShotLlm(make_beat_script())).run_script(
        "想法", {"target_duration_sec": 1}
    )
    assert any("口播预算" in w for w in content.warnings)
    ok = Agents(OneShotLlm(make_beat_script())).run_script(
        "想法", {"target_duration_sec": 20}
    )
    assert not any("口播预算" in w for w in ok.warnings)


# --------------------------------------------------------------------------
# h3_compiler：action / tone / soundscape / music / style 全部数据注入
# --------------------------------------------------------------------------


def make_compiled_segment(**overrides) -> Segment:
    defaults = dict(
        segment_key="S01G01",
        scene_id="S1",
        index=1,
        duration_sec=7,
        shots=[
            Shot(
                shot_no=1,
                cutpoint_sec=0.0,
                camera="slow push in",
                description="ferryman ties the rope",
                action=(
                    "The old ferryman loops the mooring rope twice around the "
                    "wooden post, glances at the dark river, then turns toward "
                    "the pier steps as the camera slowly pushes in."
                ),
                beat_refs=[1],
                dialogue_refs=[
                    ShotDialogueRef(
                        speaker="老船工",
                        line="上船吧。",
                        tone="in a low voice",
                    )
                ],
            ),
        ],
        asset_refs=[{"asset_id": "a-1", "usage_note": "主角参考"}],
        subject_placements=[
            SubjectPlacement(
                name="老船工",
                placement="standing at the wooden pier, coiling the mooring rope",
            )
        ],
        soundscape="Water lapping against wooden planks, low creak of rope.",
        music="Sparse low strings, slow tempo.",
    )
    defaults.update(overrides)
    return Segment(
        h3_prompt=H3Prompt(text=""),
        **defaults,
    )


def test_compile_injects_action_tone_sound_and_style() -> None:
    segment = make_compiled_segment()
    text = compile_h3_prompt(
        segment, {"a-1": make_asset("a-1", AssetKind.CHARACTER, "老船工")},
        style_line="cyberpunk style, neon lights",
    )
    # 风格行用传入值而不是默认夜景电影感
    assert "cyberpunk style, neon lights" in text
    assert DEFAULT_STYLE_LINE not in text
    # detailed_description 用 action 段落（画面密度来源）
    assert "loops the mooring rope twice" in text
    # 台词语气拼进口型句
    assert "says in Chinese, in a low voice, lips moving as they speak" in text
    assert "<d>[中文] 上船吧。</d>" in text
    # 声音设计按段数据注入，不再硬编码雨声
    assert "Water lapping against wooden planks" in text
    assert "Rain against the glass" not in text
    assert "Sparse low strings, slow tempo." in text


def test_compile_falls_back_to_defaults() -> None:
    segment = make_compiled_segment(soundscape="", music="", shots=[
        Shot(shot_no=1, cutpoint_sec=0.0, camera="static shot", description="at the pier"),
    ])
    text = compile_h3_prompt(segment, {})
    assert DEFAULT_SOUNDSCAPE in text
    assert DEFAULT_MUSIC in text
    # action 为空回退 description（旧数据路径）
    assert "at the pier" in text
    # 无台词就没有 <d> 块
    assert "<d>" not in text


def test_compile_relay_anchor_for_continuity_segment() -> None:
    from server.domain.entities import Continuity

    segment = make_compiled_segment(
        continuity=Continuity(enabled=True, with_prev_segment_key="S01G00"),
    )
    text = compile_h3_prompt(segment, {})
    assert RELAY_ANCHOR_EN in text


# --------------------------------------------------------------------------
# P1：说话人 Subject 化 + in <Picture M> 绑定 + retention 按类型分化
# --------------------------------------------------------------------------


def test_compile_binds_speaker_to_subject_and_picture() -> None:
    segment = make_compiled_segment()
    text = compile_h3_prompt(
        segment, {"a-1": make_asset("a-1", AssetKind.CHARACTER, "老船工")}
    )
    # 官方句式：<Subject N> is ... in <Picture M>（人与参考图挂钩）；
    # TASK-032 后 Picture 定义只声明参考图角色，Subject 行携带开场位置状态
    assert (
        "<Picture 1> is the character identity reference card of 老船工 "
        "(identity only, not an additional on-scene person)." in text
    )
    assert (
        "<Subject 1> is 老船工 (S1) in <Picture 1>, opening placement: "
        "standing at the wooden pier, coiling the mooring rope, "
        "identity kept identical in every shot." in text
    )
    # 台词由 Subject ID 引出（绑脸，防止台词配给画面里唯一可见的嘴），不再用真名
    assert "<Subject 1> (S1) says in Chinese, in a low voice, lips moving as they speak" in text
    assert "老船工 says" not in text


def test_compile_speaker_without_character_asset_keeps_name() -> None:
    segment = make_compiled_segment()
    segment.shots[0].dialogue_refs = [
        ShotDialogueRef(speaker="旁白", line="河记得一切。", tone="")
    ]
    text = compile_h3_prompt(
        segment, {"a-1": make_asset("a-1", AssetKind.CHARACTER, "老船工")}
    )
    # 无角色资产背书（旁白）不造 Subject 标签，避免引用未定义标签；
    # 在场角色资产（老船工）即使不说话也必须有 <Subject N> 身份锚
    assert "旁白 says in Chinese" in text
    assert "<Subject 1> is 老船工" in text


def test_compile_retention_differs_by_asset_kind() -> None:
    assets = {
        "a-1": make_asset("a-1", AssetKind.CHARACTER, "老船工"),
        "a-2": make_asset("a-2", AssetKind.SCENE, "夜色渡口"),
        "a-3": make_asset("a-3", AssetKind.PROP, "缆绳"),
    }
    segment = make_compiled_segment(asset_refs=[
        {"asset_id": "a-1", "usage_note": "主角"},
        {"asset_id": "a-2", "usage_note": "场景"},
        {"asset_id": "a-3", "usage_note": "道具"},
    ])
    text = compile_h3_prompt(segment, assets)
    assert "face shape, hairstyle and outfit identity" in text
    assert "spatial layout and key landmarks" in text
    assert "shape, color and markings" in text


# --------------------------------------------------------------------------
# P0：画幅贯穿（资产卡尺寸 / 视频输出宽高）
# --------------------------------------------------------------------------


def test_asset_card_size_follows_project_ratio() -> None:
    from server.app.media import size_for_asset_card

    assert size_for_asset_card("16:9") == (1920, 1080)
    assert size_for_asset_card("9:16") == (1080, 1920)


def test_dims_for_ratio_swaps_orientation_only() -> None:
    from server.adapters.video import dims_for_ratio

    assert dims_for_ratio(864, 480, "16:9") == (864, 480)
    assert dims_for_ratio(864, 480, "9:16") == (480, 864)
    assert dims_for_ratio(480, 864, "16:9") == (864, 480)
    assert dims_for_ratio(864, 480, "") == (864, 480)  # 缺省保持模板（旧 Job 兼容）
    assert dims_for_ratio(864, 480, "garbage") == (864, 480)


def test_build_timeline_applies_ratio_dims() -> None:
    import json as _json

    from server.adapters.video import build_timeline
    from server.tests.test_video_provider import PROMPT, make_template

    template = make_template()
    timeline = _json.loads(
        build_timeline(
            template,
            prompt=PROMPT,
            duration_sec=5,
            ref_file_names=["openapi/a.png"],
            total_frames=123,
            width=480,
            height=864,
        )
    )
    assert timeline["width"] == 480 and timeline["height"] == 864
    assert timeline["output"]["width"] == 480 and timeline["output"]["height"] == 864
    # 模板本身不被就地修改（deepcopy）
    assert "width" not in template and template["output"].get("width") is None


# --------------------------------------------------------------------------
# StoryboardAgent 校验：action/on_screen_text 节拍必须被认领
# --------------------------------------------------------------------------


def _run_validation(script: ScriptContent, segments: list[Segment]) -> list[str]:
    # 与产线一致：先节拍派生（run_storyboard 入口行为）+ 确定性编译，再跑校验
    Agents._normalize_beats(script)
    assets_by_id = {"a-1": make_asset("a-1", AssetKind.CHARACTER, "老船工")}
    for seg in segments:
        compiled = compile_h3_prompt(seg, assets_by_id)
        seg.h3_prompt = H3Prompt(text=compiled)
    agents = Agents(OneShotLlm(None))
    return agents._validate_content(
        StoryboardContent(segments=segments), {"a-1"}, script=script
    )


def test_validate_flags_unclaimed_action_beats() -> None:
    script = make_beat_script()
    segment = make_compiled_segment()
    segment.shots[0].beat_refs = []  # 未认领任何节拍
    errors = _run_validation(script, [segment])
    assert any("未被任何镜头认领" in e for e in errors)
    assert not any("越界" in e for e in errors)


def test_validate_flags_out_of_range_beat_refs() -> None:
    script = make_beat_script()
    segment = make_compiled_segment()
    segment.shots[0].beat_refs = [99]
    errors = _run_validation(script, [segment])
    assert any("越界" in e for e in errors)


def test_validate_passes_when_all_action_beats_claimed() -> None:
    script = make_beat_script()
    segment = make_compiled_segment()
    segment.shots[0].beat_refs = [1, 2, 3, 4]
    assert _run_validation(script, [segment]) == []


def test_split_overloaded_segment_balances_dialogue() -> None:
    """76 字台词塞一段（12s 也装不下）→ 确定性二分为两段且对账仍平衡。"""
    from server.app.agents import Agents

    long_lines = [
        ShotDialogueRef(speaker="王建国", line="兄弟帮个忙，你就说当时是你在开，我给你钱"),
        ShotDialogueRef(speaker="陆峥", line="你疯了？人是你撞的！"),
        ShotDialogueRef(speaker="王建国", line="你不答应是吧，那咱们就看看警察信谁"),
        ShotDialogueRef(speaker="王建国", line="我一个月挣的钱比你一年还多，我耽误得起吗"),
    ]
    segment = make_compiled_segment()
    segment.shots[0].dialogue_refs = list(long_lines)
    segment.shots[0].beat_refs = [1]
    content = StoryboardContent(segments=[segment])
    Agents._split_overloaded_segments(content)

    assert len(content.segments) == 2
    first, second = content.segments
    # 新段续接前段，temp key 被重编号为规范 key
    assert second.continuity.enabled is True
    assert second.continuity.with_prev_segment_key == first.segment_key
    # 台词均分后每段都在口播预算内
    for seg in content.segments:
        spoken = sum(len(d.line.strip()) for shot in seg.shots for d in shot.dialogue_refs)
        assert spoken <= seg.duration_sec * 4
    # 台词没丢
    kept = [d.line.strip() for s in content.segments for sh in s.shots for d in sh.dialogue_refs]
    assert kept == [d.line.strip() for d in long_lines]
    # 认领保在前段
    assert first.shots[0].beat_refs == [1]


def test_validate_skips_beat_checks_for_legacy_script() -> None:
    legacy = ScriptContent(
        logline="旧剧本",
        scenes=[Scene(id="S1", title="渡口", summary="夜色渡口", est_seconds=8)],
    )
    segment = make_compiled_segment()
    errors = _run_validation(legacy, [segment])
    assert not any("认领" in e for e in errors)


# --------------------------------------------------------------------------
# 端到端：带节拍剧本 → run_storyboard 编译出注入完整提示词
# --------------------------------------------------------------------------


def test_run_storyboard_compiles_beat_backed_prompt() -> None:
    script = make_beat_script()
    assets = [make_asset("a-1", AssetKind.CHARACTER, "老船工")]

    class BeatStoryboardLlm:
        def chat_json(self, *, system, user, schema, temperature):
            segment = make_compiled_segment()
            segment.shots[0].beat_refs = [1, 2, 3, 4]
            return FakeResult(StoryboardContent(segments=[segment]))

        def chat(self, **kwargs):
            return "pong"

    content, _warnings = Agents(BeatStoryboardLlm()).run_storyboard(
        script, assets, {"target_duration_sec": 6, "style": "cyberpunk style"}
    )
    text = content.segments[0].h3_prompt.text
    assert "cyberpunk style" in text
    assert "loops the mooring rope twice" in text
    assert "in a low voice" in text
    assert "Water lapping against wooden planks" in text
    assert "Sparse low strings" in text


def test_run_storyboard_retries_on_unclaimed_beats() -> None:
    script = make_beat_script()
    assets = [make_asset("a-1", AssetKind.CHARACTER, "老船工")]
    attempts: list[str] = []

    class LazyThenGoodLlm:
        def __init__(self) -> None:
            self.second_user = ""

        def chat_json(self, *, system, user, schema, temperature):
            attempts.append(user)
            segment = make_compiled_segment()
            if len(attempts) == 1:
                segment.shots[0].beat_refs = []  # 第一次偷懒不认领
            else:
                segment.shots[0].beat_refs = [1, 2, 3, 4]
                self.second_user = user
            return FakeResult(StoryboardContent(segments=[segment]))

        def chat(self, **kwargs):
            return "pong"

    llm = LazyThenGoodLlm()
    content, _warnings = Agents(llm).run_storyboard(
        script, assets, {"target_duration_sec": 6}
    )
    assert len(attempts) == 2
    # 重试请求里带着认领反馈
    assert "未被任何镜头认领" in llm.second_user
    assert content.segments[0].shots[0].beat_refs == [1, 2, 3, 4]


def test_run_storyboard_repairs_unclaimed_beats_deterministically() -> None:
    """LLM 两次都漏认领时，兜底把缺拍挂到末镜并附原文（画面不丢拍）。"""
    script = make_beat_script()
    assets = [make_asset("a-1", AssetKind.CHARACTER, "老船工")]

    class AlwaysLazyLlm:
        def chat_json(self, *, system, user, schema, temperature):
            segment = make_compiled_segment()
            segment.shots[0].beat_refs = []
            return FakeResult(StoryboardContent(segments=[segment]))

        def chat(self, **kwargs):
            return "pong"

    content, _warnings = Agents(AlwaysLazyLlm()).run_storyboard(
        script, assets, {"target_duration_sec": 6}
    )
    shot = content.segments[0].shots[0]
    assert sorted(shot.beat_refs) == [1, 4]
    assert "; also show: 老船工把缆绳" in shot.action
    assert "; also show: 手机屏幕弹出：您有新订单" in shot.action
    # 兜底补写后提示词已重编译，包含补入的画面内容
    assert "also show" in content.segments[0].h3_prompt.text
