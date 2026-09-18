"""领域测试工厂（TASK-003）：构造合法实体，避免各测试文件重复拼 JSON。"""

from __future__ import annotations

from server.domain.entities import (
    Asset,
    AssetRef,
    Continuity,
    H3Prompt,
    Project,
    ProjectParams,
    ResolvedReference,
    Segment,
    Shot,
    ShotDialogueRef,
)
from server.domain.enums import AssetKind, PromptLang

# base 三段式样例（无参考图；字段顶格，[Shot 1] 无时间戳，台词带语言标签）
BASE_H3_TEXT = (
    "integrated_multimodal_description: [Shot 1] Cinematic, live-action, cold "
    "gray-green palette. An old ferryman stands at the wooden pier exactly as "
    "envisioned; he slowly pushes the boat off the pier while the camera does a "
    "gentle push in. He says in an off-screen voiceover <d>[English] the river "
    "remembers</d>, while his lips remain completely closed.\n"
    "overall_soundscape: Water lapping against wooden planks, distant night crickets.\n"
    "non_diegetic_music: Sparse low strings, slow tempo."
)

# reference 六段式样例（两张参考图口径：起始构图 + 身份；[Shot 2] 切点须与 shots 一致）
REF_H3_TEXT = (
    "subject_definitions: <Subject 1> is the old ferryman in <Picture 2>, with a "
    "weathered face, gray stubble and a dark oilskin coat. <Picture 1> is the opening "
    "composition of this shot.\n"
    "summary: [reference generation] The ferryman pushes his boat off the night pier "
    "and turns to speak.\n"
    "retention_analysis: <Picture 1> (appears in [Shot 1]): fully_preserved - pier "
    "framing and ferryman placement open the shot exactly as supplied.\n"
    "<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - face shape, "
    "stubble and coat stay identical across both shots.\n"
    "detailed_description: [Shot 1] Cold gray-green night, live-action. The scene "
    "opens exactly as <Picture 1>: the ferryman at the wooden pier, river dark behind "
    "him. He unties the mooring rope. [Shot 2] At 00:03.000, the camera pushes in as "
    "<Subject 1> (S1) steps onto the boat; his lips move as he says <d>[English] the "
    "river remembers</d>.\n"
    "overall_soundscape: Water lapping against wooden planks, rope creaking, distant "
    "night crickets.\n"
    "non_diegetic_music: Sparse low strings, slow tempo."
)


def make_project(**overrides) -> Project:
    params = overrides.pop("params", ProjectParams())
    return Project(project_id="p-1", title="渡口", idea="老船工与河", params=params, **overrides)


def make_shot(no: int = 1, cutpoint: float = 0.0, dialogue_line: str | None = None) -> Shot:
    return Shot(
        shot_no=no,
        cutpoint_sec=cutpoint,
        camera="static shot",
        description="ferryman at the pier",
        dialogue_refs=(
            [ShotDialogueRef(speaker="老船工", line=dialogue_line)] if dialogue_line else []
        ),
    )


def make_segment(
    *,
    key: str = "S01G01",
    scene_id: str = "S1",
    index: int = 1,
    duration: int = 5,
    shots: list[Shot] | None = None,
    h3_text: str = BASE_H3_TEXT,
    lang: PromptLang = PromptLang.EN,
    asset_refs: list[AssetRef] | None = None,
    continuity: Continuity | None = None,
) -> Segment:
    return Segment(
        segment_key=key,
        scene_id=scene_id,
        index=index,
        duration_sec=duration,
        shots=shots or [make_shot(1, 0.0, dialogue_line="the river remembers")],
        asset_refs=asset_refs or [],
        continuity=continuity or Continuity(),
        h3_prompt=H3Prompt(text=h3_text, lang=lang),
    )


def make_reference_segment(**overrides) -> Segment:
    """reference 六段式段：两张参考图 + 双镜头（[Shot 2] At 00:03.000 与切点一致）。"""
    overrides.setdefault("duration", 7)
    overrides.setdefault(
        "shots",
        [
            make_shot(1, 0.0),
            make_shot(2, 3.0, dialogue_line="the river remembers"),
        ],
    )
    overrides.setdefault(
        "asset_refs",
        [
            {"asset_id": "a-start", "usage_note": "开场构图"},
            {"asset_id": "a-face", "usage_note": "身份"},
        ],
    )
    overrides.setdefault("h3_text", REF_H3_TEXT)
    return make_segment(**overrides)


def make_asset(asset_id: str, kind: AssetKind, name: str) -> Asset:
    return Asset(asset_id=asset_id, project_id="p-1", kind=kind, name=name)


def make_reference(
    asset_id: str, kind: AssetKind, image_id: str, file_path: str = "p-1/assets/a.png"
) -> ResolvedReference:
    return ResolvedReference(
        asset_id=asset_id,
        kind=kind,
        asset_image_id=image_id,
        file_path=file_path,
        usage_note="test",
    )
