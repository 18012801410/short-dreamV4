"""人物/道具唯一命名归一测试（用户硬要求：同一人物/道具不得出现多个命名）。

覆盖：系列对齐改写、剧本内重复合名合并、说话人归一、正文提及改写、
未登记说话人告警、props 去重与疑似别名、正常输入零改动。
"""

from server.domain.entities import (
    Beat,
    CharacterProfile,
    DialogueLine,
    Scene,
    ScriptContent,
)
from server.domain.enums import BeatType
from server.domain.name_registry import enforce_name_registry


def make_script(characters, beats, props=None, dialogues=None) -> ScriptContent:
    return ScriptContent(
        logline="赘婿陈平回归都市",
        scenes=[
            Scene(
                id="S1",
                title="内 · 苏家寿宴大厅 · 夜",
                summary="寿宴上冲突爆发",
                dialogues=dialogues or [],
                est_seconds=30,
                beats=beats,
            ),
        ],
        characters=characters,
        props=props or [],
    )


def test_duplicate_character_names_merged() -> None:
    """同一人两个条目（含别名）→ 合并为一个规范名，说话人统一改写。"""
    script = make_script(
        characters=[
            CharacterProfile(name="陈平", profile="上门女婿"),
            CharacterProfile(name="苏母（王秀兰）", profile="势利刻薄"),
            CharacterProfile(name="王秀兰", profile="重复条目"),
        ],
        beats=[
            Beat(type=BeatType.ACTION, text="苏母（王秀兰）拍桌而起。"),
            Beat(type=BeatType.DIALOGUE, text="废物！", speaker="王秀兰"),
            Beat(type=BeatType.DIALOGUE, text="你配吗？", speaker="苏母（王秀兰）"),
        ],
    )
    issues = enforce_name_registry(script)
    names = [c.name for c in script.characters]
    assert names == ["陈平", "苏母（王秀兰）"]  # 「王秀兰」条目被合并
    assert any("多个命名" in i and "苏母（王秀兰）" in i for i in issues)
    # 说话人全部归一到规范名（含 dialogue 节拍与 dialogues 镜像）
    for scene in script.scenes:
        for beat in scene.beats:
            if beat.type is BeatType.DIALOGUE:
                assert beat.speaker == "苏母（王秀兰）"
        for line in scene.dialogues:
            assert line.speaker == "苏母（王秀兰）"
    # 正文提及的别名也被改写为规范名
    assert "王秀兰拍桌" not in script.scenes[0].beats[0].text


def test_series_cast_alignment_renames() -> None:
    """系列模式：剧本人物名与大纲登记名别名等价 → 改写为大纲登记名。"""
    script = make_script(
        characters=[
            CharacterProfile(name="陈平", profile="赘婿"),
            CharacterProfile(name="王秀兰", profile="丈母娘"),
        ],
        beats=[
            Beat(type=BeatType.ACTION, text="王秀兰把酒泼在陈平脸上。"),
            Beat(type=BeatType.DIALOGUE, text="滚出去！", speaker="王秀兰"),
        ],
    )
    issues = enforce_name_registry(script, cast=["陈平", "苏母（王秀兰）", "苏瑶"])
    assert script.characters[1].name == "苏母（王秀兰）"
    assert script.scenes[0].beats[0].text == "苏母（王秀兰）把酒泼在陈平脸上。"
    assert any("对齐大纲登记名" in i for i in issues)


def test_unknown_cast_member_warns() -> None:
    """剧本冒出大纲外的人名（李桂芬式漂移）→ 不改写，给 warning 人工决策。"""
    script = make_script(
        characters=[CharacterProfile(name="李桂芬", profile="丈母娘")],
        beats=[Beat(type=BeatType.DIALOGUE, text="滚出去！", speaker="李桂芬")],
    )
    issues = enforce_name_registry(script, cast=["陈平", "苏母（王秀兰）"])
    assert any("李桂芬" in i and "不在大纲人物表" in i for i in issues)
    assert script.characters[0].name == "李桂芬"  # 不擅自改名


def test_unregistered_speaker_warns() -> None:
    script = make_script(
        characters=[CharacterProfile(name="陈平", profile="赘婿")],
        beats=[Beat(type=BeatType.DIALOGUE, text="让开。", speaker="保安甲")],
    )
    issues = enforce_name_registry(script)
    assert any("保安甲" in i and "未在 characters 登记" in i for i in issues)


def test_props_dedup_and_similar_alias() -> None:
    script = make_script(
        characters=[CharacterProfile(name="陈平", profile="赘婿")],
        beats=[Beat(type=BeatType.ACTION, text="陈平攥紧玉佩。")],
        props=["离婚协议", "离婚协议 ", "半块玉佩", "玉佩"],
    )
    issues = enforce_name_registry(script)
    assert script.props == ["离婚协议", "半块玉佩", "玉佩"]
    assert any("离婚协议" in i and "同一道具" in i for i in issues)  # 完全重复合并
    assert any("疑似同一道具" in i for i in issues)  # 半块玉佩/玉佩 子串提示


def test_clean_script_untouched() -> None:
    """命名本来就规范的剧本：零 issue、内容零改动。"""
    script = make_script(
        characters=[
            CharacterProfile(name="陈平", profile="赘婿"),
            CharacterProfile(name="苏母（王秀兰）", profile="丈母娘"),
        ],
        beats=[
            Beat(type=BeatType.ACTION, text="陈平低头擦地。"),
            Beat(type=BeatType.DIALOGUE, text="废物！", speaker="苏母（王秀兰）"),
        ],
        dialogues=[DialogueLine(speaker="苏母（王秀兰）", line="废物！")],
        props=["离婚协议"],
    )
    issues = enforce_name_registry(script, cast=["陈平", "苏母（王秀兰）", "苏瑶"])
    assert issues == []
    assert script.scenes[0].beats[0].text == "陈平低头擦地。"
    assert script.props == ["离婚协议"]
