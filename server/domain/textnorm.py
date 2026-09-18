"""台词文本规范化（TASK-019）：剥离括号舞台指示。

根因：剧本把「（扑通跪下）」这类动作写进台词后，会被逐字搬进 H3 <d> 块读出声，
也会被烧进字幕。确定性规则：全角/半角括号内的内容视为舞台指示，一律剥离。
"""

from __future__ import annotations

import re

_STAGE_DIRECTION_RE = re.compile(r"（[^（）]*）|\([^()]*\)")


def strip_stage_directions(line: str) -> tuple[str, bool]:
    """剥离台词中的括号舞台指示，返回 (纯台词, 是否发生过剥离)。

    「（扑通跪下）林总，我错了」→「林总，我错了」；
    整句只有舞台指示时返回空串（调用方决定丢弃或保留）。
    """
    cleaned = _STAGE_DIRECTION_RE.sub("", line).strip()
    return cleaned, cleaned != line.strip()


def clean_dialogue_line(line: str) -> str | None:
    """清洗单句台词：剥离舞台指示后为空则丢弃（返回 None）。"""
    cleaned, _changed = strip_stage_directions(line)
    return cleaned or None


def split_tone_from_line(line: str) -> tuple[str, str]:
    """把台词中的括号舞台指示提取为语气（动作归位而不是丢弃）。

    「（声音很低）前面路口，左拐」→（「前面路口，左拐」, 「声音很低」）；
    多段括号内容以空格连接；无括号返回原句与空语气。
    """
    tones = [m.group(0)[1:-1].strip() for m in _STAGE_DIRECTION_RE.finditer(line)]
    cleaned = _STAGE_DIRECTION_RE.sub("", line).strip()
    return cleaned, " ".join(t for t in tones if t)


# --------------------------------------------------------------------------
# 场景图提示词消毒（TASK-024）：文生图工作流 Lightning cfg=1，负向通道无效，
# 提示词里出现任何人形词（哪怕写 "no people"）都会把人引进画面。
# 确定性护栏：场景出图前剥掉全部人形词，只留正向描述。
# --------------------------------------------------------------------------

_SCENE_PEOPLE_WORDS = (
    "people", "persons", "person", "crowds", "crowd", "silhouettes", "silhouette",
    "mannequins", "mannequin", "humans", "human", "figures", "figure",
)
# 自带"应有值班员"语义先验的设施词 → 中性替换（保安亭必出保安，是国产住宅门禁的强先验）
_SCENE_STAFFED_PROPS: dict[str, str] = {
    "保安亭": "空置玻璃亭",
    "岗亭": "空置玻璃亭",
    "门卫室": "空置小屋",
    "值班室": "空置小屋",
    "门禁闸机": "出入闸栏",
    "闸机": "出入闸栏",
    "security booth": "empty glass kiosk",
    "guard booth": "empty glass kiosk",
    "guard house": "empty kiosk",
    "toll booth": "empty kiosk",
}
_NO_PAIR_RE = re.compile(
    r"\bno\s+(?:" + "|".join(_SCENE_PEOPLE_WORDS) + r")\b[,;]?\s*",
    flags=re.IGNORECASE,
)
_PEOPLE_WORD_RE = re.compile(
    r"\b(?:" + "|".join(_SCENE_PEOPLE_WORDS) + r")\b|人物|人群|剪影|人影|人体",
    flags=re.IGNORECASE,
)


def sanitize_scene_prompt(prompt: str) -> tuple[str, list[str]]:
    """剥掉场景提示词中的人形词，并把自带值班员预期的设施词换成中性描述。

    返回 (净化后提示词, 移除/替换的词列表)。
    """
    removed: list[str] = []
    for staffed, neutral in _SCENE_STAFFED_PROPS.items():
        if staffed.lower() in prompt.lower():
            removed.append(staffed)
            pattern = re.compile(re.escape(staffed), flags=re.IGNORECASE)
            prompt = pattern.sub(neutral, prompt)
    removed.extend(m.group(0).strip() for m in _NO_PAIR_RE.finditer(prompt))
    prompt = _NO_PAIR_RE.sub("", prompt)
    for m in _PEOPLE_WORD_RE.finditer(prompt):
        removed.append(m.group(0))
    prompt = _PEOPLE_WORD_RE.sub("", prompt)
    prompt = re.sub(r"\s+([,.])", r"\1", prompt)
    prompt = re.sub(r"([,;])\s*(?:[,;]\s*)+", ", ", prompt)
    prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ,;")
    return prompt, removed


# --------------------------------------------------------------------------
# 稳定版出图工作流（cfg>1，负向通道有效）的负向词：按资产类型区分。
# 场景版含人形词——极速版负向通道无效只能靠正向措辞规避，稳定版可直接拦截；
# 同时拦分格/拼贴/多视图（TASK-042：场景卡必须是单幅广角空镜）。
# --------------------------------------------------------------------------

IMAGE_NEGATIVE_QUALITY = (
    "blurry, low quality, lowres, jpeg artifacts, watermark, signature, "
    "text overlay, deformed, disfigured, extra limbs, extra fingers, "
    "bad anatomy, cropped, out of frame"
)
# 场景卡（TASK-042）：单幅广角空镜，负向拦住分格/拼贴/多视图——三视图空镜卡
# 会让后续关键帧的图生图模型在多个机位之间摇摆（每个面板几何不同），
# 视频阶段也会把"图片拼贴"当成一张参考图。
IMAGE_NEGATIVE_SCENE = (
    IMAGE_NEGATIVE_QUALITY
    + ", people, person, crowd, silhouette, mannequin, human figure"
    + ", split screen, collage, multiple panels, panel grid, diptych, triptych, "
    "comic panels, storyboard sheet, contact sheet, letterbox bars, black bars"
)
# 道具卡：道具要"独占画面"——手/人/场景/桌面/穿戴全部进负向
IMAGE_NEGATIVE_PROP = (
    IMAGE_NEGATIVE_QUALITY + ", hands, fingers, arms, human, person, people, "
    "mannequin, worn, held by someone, scene background, environment, room, "
    "table, floor, clutter"
)


def negative_for_kind(kind) -> str:
    """按资产类型取负向词：场景拦人形，道具拦手/人/场景杂物。"""
    from server.domain.enums import AssetKind

    if kind is AssetKind.SCENE:
        return IMAGE_NEGATIVE_SCENE
    if kind is AssetKind.PROP:
        return IMAGE_NEGATIVE_PROP
    return IMAGE_NEGATIVE_QUALITY

# 道具提示词里会出现的人形/场景词（英文词边界匹配，中文匹配整个人形词）；
# diorama/miniature 等微缩模型词会把真实道具画成白底模型卡片，一并剥掉
_PROP_CONTAMINATION_RE = re.compile(
    r"\b(?:hands?|fingers?|arms?|holding|held|worn|wearing|person|people|humans?|"
    r"mannequins?|silhouettes?|background scene|on a table|on the table|on the floor|"
    r"diorama|dioramas|maquette|scale model|miniature(?: diorama)?(?: look)?)\b"
    r"|手持|拿着|握着|戴着|穿着|人物|人形|微缩模型|模型景观",
    flags=re.IGNORECASE,
)


def sanitize_prop_prompt(prompt: str) -> tuple[str, list[str]]:
    """道具卡提示词消毒：道具必须独占画面。

    剥掉人形/持握/穿戴/场景词（它们会把背景和手引进画面），
    并确保有 isolated 纯白底描述。返回 (净化后提示词, 移除的词列表)。
    """
    removed = [m.group(0).strip() for m in _PROP_CONTAMINATION_RE.finditer(prompt)]
    cleaned = _PROP_CONTAMINATION_RE.sub("", prompt)
    cleaned = re.sub(r"\s+([,.])", r"\1", cleaned)
    cleaned = re.sub(r"([,;])\s*(?:[,;]\s*)+", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;")
    if "isolated" not in cleaned:
        cleaned = cleaned.rstrip(" .,") + ", isolated on a pure white background"
    return cleaned, removed
