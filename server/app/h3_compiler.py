"""H3 提示词确定性编译器（TASK-029/031/032；节拍流增强）。

哲学（沿用 V3）：LLM 只写结构化创作数据（shots/台词/asset_refs/声音设计/
subject_placements），六段式 reference 提示词由本模块从已通过校验的结构化
数据确定性编译——结构合规"构造即合法"，不再依赖 LLM 对格式规范的自觉。

- 画面密度来自 Shot.action：分镜 LLM 认领剧本节拍（Shot.beat_refs）后写出的
  英文动作时间线段落，为空时回退 description（旧数据路径）；
- 台词语气来自 ShotDialogueRef.tone（英文短语），拼进口型证据句；
- 声音设计来自 Segment.soundscape / Segment.music（源自剧本 sfx 节拍），
  不再硬编码雨声与氛围垫乐；
- 风格行来自项目 style 参数（风格预设本身是英文），不再恒定夜景电影感；
- 身份唯一性与空间状态（TASK-032，重影与位置错误的根治）：
  * 角色外貌锚点全文只出现一次——在 <Subject N> 定义里；<Picture N> 只声明
    参考图的角色（identity card），锚点写两遍会被执行端读成两个人
    （实测 S03G03 三克隆、S01G02 双风衣男）；
  * 在场角色（含不开口者）逐一建立 <Subject N>（官方口径"不留没名字的
    那个人"），说话人追加 (Sx) 台词 ID；
  * 开场空间状态来自 Segment.subject_placements，注入 <Subject N> 与关键帧
    提示词——关键帧图、首帧接力、视频正文三方对同一位置状态，位置错误
    （驾驶员被画进副驾）在便宜的关键帧阶段暴露，而不是昂贵的视频阶段；
  * 锚点消毒：角色锚点剥离"直视镜头/白底/定妆"等卡片摆拍短语（姿势属于
    参考卡，泄漏进剧情镜头会渲染成看镜头动作）；场景锚点剥离"空无一人/
    空置"等空镜状态短语（与有人镜头直接矛盾，模型自行消解时复制或清空
    人物）。存量数据在编译期兜底，新数据由 agents 资产/分镜规则从源头保证。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from server.domain.entities import Asset, Segment
from server.domain.validation import _cutpoint_label

DEFAULT_STYLE_LINE = (
    "Cinematic realistic style, 35mm film grain, natural lighting, high detail."
)
DEFAULT_SOUNDSCAPE = (
    "Natural scene ambience and quiet room tone, with footsteps and cloth "
    "sounds following the on-screen action."
)
DEFAULT_MUSIC = "Soft ambient underscore, low volume."
_LIPSYNC = "lips moving as they speak"  # 说话人口型证据（官方要求）

# 开场帧槽位（<Picture 1>）强锚定句（与 agents._ensure_relay_anchor 同源）。
# 措辞对来源中性：既用于连续段的尾帧接力，也用于关键帧（TASK-031）——
# 对模型真正要紧的是"frame 0 必须与该图完全一致，只描述其后发生的动作"。
RELAY_ANCHOR_EN = (
    "<Picture 1> is the exact opening frame of this video: "
    "frame 0 must match this image exactly - composition, "
    "shot size, character positions and facing, held props and lighting "
    "all start from it; describe only the action and camera movement that "
    "happen after this frame."
)


def _storyboard_anchor(cell_count: int) -> str:
    """宫格分镜板声明句（<Picture 1>=宫格时按实际格数生成）。

    官方 R2V 口径——声明视角/站位/镜序，并声明**实际格数与镜序**（shots 超出
    MAX_GRID_CELLS 时只拼了前 6 格，声明"全部 shots"会误导 H3 的镜序理解，
    设计 §4.1：声明句只声明实际存在的格数），再加"宫格本身不上画"护栏
    （防止 H3 把多格布局当成画面内容渲染成分屏）。
    与 RELAY_ANCHOR_EN 互斥：宫格口径不叠加单帧锚定，由首格承担开场帧语义。
    """
    cells = ", ".join(f"cell {i}" for i in range(1, cell_count + 1))
    return (
        f"<Picture 1> is a storyboard reference of {cell_count} cells for the "
        "shots of this segment, defining the viewpoint, subject placement, "
        f"and shot order ({cells} from left to right, top to bottom); "
        "frame 0 must match the first cell of <Picture 1>, and the grid "
        "layout of <Picture 1> itself never appears on screen."
    )


# retention_analysis 按参考图类型分化（官方口径：逐图声明"管什么、不管什么"，
# 避免角色板顺带钉死构图、场景板顺带决定人物长相）
_RETENTION_BY_KIND = {
    "character": (
        "fully_preserved - face shape, hairstyle and outfit identity stay "
        "identical in every shot; pose, expression and on-scene position "
        "follow this shot, not the reference."
    ),
    "scene": (
        "fully_preserved - spatial layout and key landmarks stay consistent; "
        "lighting, weather and minor props follow this shot."
    ),
    "prop": (
        "fully_preserved - the prop's shape, color and markings stay identical."
    ),
}
_RETENTION_GENERIC = "fully_preserved - identity and key features."


def _mmss(cutpoint: float) -> str:
    return _cutpoint_label(cutpoint)

# ---- 锚点消毒（TASK-032）-------------------------------------------------
# 子句切分：中英文逗号/分号/中英句末标点一视同仁；重组统一用逗号。
# 不切英文句号，避免误伤缩写与小数。
_CLAUSE_SPLIT_RE = re.compile(r"[，,；;。！？!?]")
# 定妆卡摆拍/卡片语义：姿势与取景属于参考卡本身（retention 已声明 pose
# follows this shot），留在锚点里会在每个剧情镜头重复渲染"看镜头/卡片取景"。
# TASK-043 起角色卡是正面全身定妆照，全身/立绘/full-body 这类**取景词**同样
# 只属于卡片本身，写进锚点会把剧情镜头锁成全身站姿。
_CARD_POSE_HINT_RE = re.compile(
    r"镜头|camera|白底|棚拍|定妆|半身|全身|立绘|三视图|portrait|white background|"
    r"reference card|character sheet|bust|full[- ]?body|full[- ]?length|"
    r"head[- ]?to[- ]?toe",
    re.IGNORECASE,
)
# 空镜状态：空镜卡的"没有人/设施空置"声明与有人镜头直接矛盾——模型面对
# 矛盾时的消解方式不可控（复制人物补人数、或把人挪出正确位置）
_EMPTY_STATE_HINT_RE = re.compile(
    r"无人|空无一人|没有人|不见人影|空置|空着|闲置|"
    r"no (?:people|persons?|one|crowd|body)|nobody|empty of people|"
    r"unpopulated|deserted|vacant|unoccupied|unattended|empty place",
    re.IGNORECASE,
)
# 场景取景词（TASK-042）：空镜卡改为单幅广角全貌后，广角/机位/视角这类描述
# 属于"卡片怎么拍"，不属于"空间长什么样"。留在场景锚点里会被嵌进每一个
# 剧情镜头的提示词，把特写/中景一律拽成广角远景（与镜头文字取景打架）。
_SCENE_FRAMING_HINT_RE = re.compile(
    r"广角|超广角|鱼眼|机位|取景|构图|视角|全景|俯视|平面图|线稿|拼贴|分格|"
    r"wide[- ]angle|ultra[- ]wide|fisheye|establishing shot|camera angle|"
    r"viewpoint|floor plan|lens|panorama|\b\d{1,3}\s?mm\b",
    re.IGNORECASE,
)
# 道具卡取景词：白底/棚拍/崭新未使用属于卡片本身；泄漏进剧情镜头会把场景
# 推向白底产品图（S01G03 实测"白底定妆照闪现 1s"的同类污染）。身份细节
# （形状/颜色/标记/挂坠）保留。
_PROP_CARD_HINT_RE = re.compile(
    r"白底|纯白|棚拍|崭新|未使用|产品图|静物台|"
    r"white background|pure white|product shot|studio|brand new|unused",
    re.IGNORECASE,
)
# 服装词：两人同框时模型容易让两张角色卡的服装互相串（实测：司机穿成乘客的
# 西装、乘客穿成司机的蓝夹克）。画面人名单里给每个角色带一个"短服装标签"
# （只取锚点里的衣着子句，最多两条），把区分度最高的视觉特征放在名字旁边；
# 长身份串仍不进正文。
_GARMENT_HINT_RE = re.compile(
    r"夹克|外套|西装|衬衫|衬衣|风衣|大衣|工装|制服|卫衣|毛衣|T恤|马甲|背心|"
    r"裙|旗袍|围裙|领带|眼镜|帽|鞋"
)
# 静态帧污染：台词块与口语动作（关键帧是第 0 帧生图依据，写进口型/台词
# 会被静态画面渲染成画面文字或张嘴表情——S01G02 台词泄漏成胸牌文字）
_DYNAMIC_BLOCK_RE = re.compile(r"<d>.*?</d>", re.DOTALL)
_SPEECH_HINT_RE = re.compile(
    r"\bsays?\b|\bspeaks?\b|\bspeaking\b|lips move|\bshouts?\b|"
    r"\bwhispers?\b|\bmurmurs?\b",
    re.IGNORECASE,
)


def _sanitize_anchor(anchor: str, noise: re.Pattern[str]) -> str:
    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(anchor) if c.strip()]
    kept = [c for c in clauses if not noise.search(c)]
    if not kept:
        return ""
    return "，".join(kept)


def sanitize_character_anchor(anchor: str) -> str:
    """角色锚点消毒：剥离定妆卡摆拍短语（直视镜头/白底/定妆/半身等）。"""
    return _sanitize_anchor(anchor, _CARD_POSE_HINT_RE)


def sanitize_scene_anchor(anchor: str) -> str:
    """场景锚点消毒：剥离空镜状态短语（空无一人/空置/deserted 等）与取景短语
    （广角/机位/视角/构图 等）。

    空镜策略把"没有人/设施空置"写进场景 visual_anchor（agents 空镜卡规则），
    原样嵌进有人镜头即图文打架；取景词（TASK-042 起空镜卡是单幅广角全貌）
    留在锚点里则会把后续每个镜头的景别锁成广角。本函数是存量数据的编译期兜底。
    """
    return _sanitize_anchor(
        _sanitize_anchor(anchor, _EMPTY_STATE_HINT_RE), _SCENE_FRAMING_HINT_RE
    )


def sanitize_prop_anchor(anchor: str) -> str:
    """道具锚点消毒：剥离白底/棚拍/崭新未使用等卡片取景词，保留身份细节。

    道具没有 <Subject N> 定义，锚点是它唯一的文本身份载体（挂坠/裂纹等
    标记必须在），所以只做取景词消毒而不整体省略。
    """
    return _sanitize_anchor(anchor, _PROP_CARD_HINT_RE)


def _composition_line(aspect: str) -> str:
    """按画幅给构图细则（豆包 frame.md「构图规则」）。"""
    portrait = any(r in aspect for r in ("9:16", "3:4", "2:3", "4:5"))
    if portrait:
        return (
            "竖屏构图：人脸与上半身落在画面安全区内，关键道具放在画面中部或下三分之一，"
            "双人同框保持左右空间关系，重要元素不贴边"
        )
    return (
        "横屏构图：三分法构图，视线前方留出呼吸空间，用前景/中景/远景建立纵深，"
        "对话或对峙场景保持 180 度轴线，重要元素不贴边"
    )


def _strip_dynamic_noise(text: str) -> str:
    """剥台词块与口语子句，把任意镜头文本降为纯静态画面描述。"""
    text = _DYNAMIC_BLOCK_RE.sub(" ", text)
    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(text) if c.strip()]
    kept = [c for c in clauses if not _SPEECH_HINT_RE.search(c)]
    return ", ".join(kept).strip(" ,.")


def _short_outfit(anchor: str) -> str:
    """从角色锚点里取"短服装标签"（衣着子句，最多两条）。

    两人同框时，模型会拿错角色卡的衣服；把区分度最高的衣着信息放在名字旁边，
    比整段身份串更有效，也不会把提示词写成定妆卡。
    """
    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(anchor) if c.strip()]
    picked = [c for c in clauses if _GARMENT_HINT_RE.search(c)]
    return "，".join(picked[:2])


def _placement_by_name(segment: Segment) -> dict[str, str]:
    """subject_placements → {角色名: 英文位置状态}；同名取首条，去句末标点。"""
    out: dict[str, str] = {}
    for placement in segment.subject_placements:
        name = placement.name.strip()
        phrase = placement.placement.strip().rstrip("。.")
        if name and phrase and name not in out:
            out[name] = phrase
    return out


def in_frame_by_name(segment: Segment) -> dict[str, bool]:
    """subject_placements → {角色名: 本段开场帧是否可见}（TASK-033）。

    无条目（存量分镜）时缺省 True——编译层不擅自改变旧行为，参考图裁剪
    由 usecases 侧按同一口径处理。
    """
    out: dict[str, bool] = {}
    for placement in segment.subject_placements:
        name = placement.name.strip()
        if name and name not in out:
            out[name] = placement.in_frame
    return out


def _character_owner(speaker: str, assets_by_id: dict[str, Asset]) -> Asset | None:
    return next(
        (
            a
            for a in assets_by_id.values()
            if a.kind.value == "character" and a.name in speaker
        ),
        None,
    )


_PICTURE_ROLES = {
    "character": (
        "character identity reference card of {name} "
        "(identity only, not an additional on-scene person)"
    ),
    "scene": "location reference card of {name}",
    "prop": "prop identity reference card of {name}",
}


def _picture_definition(pic_no: int, asset: Asset | None) -> str:
    """<Picture N> 定义：只声明参考图是什么，不重复外貌锚点。

    官方口径"每张图只管一件事"：角色卡管身份、场景卡管空间——同一段
    外貌锚点在 Picture 与 Subject 两处出现会被读成两个人。场景布局锚点
    （消毒后）保留在场景 Picture 定义里：空间关系对构图有用，且没有
    "第二个人"式复制风险。
    """
    if asset is None:
        return f"<Picture {pic_no}> is a story element reference."
    if asset.kind.value == "scene":
        layout = sanitize_scene_anchor(asset.visual_anchor or "")
        if layout:
            return (
                f"<Picture {pic_no}> is the location reference of "
                f"{asset.name}: {layout}."
            )
        return f"<Picture {pic_no}> is the location reference of {asset.name}."
    if asset.kind.value == "prop":
        # 道具没有 <Subject N>，锚点（形状/颜色/标记）是唯一的文本身份载体，
        # 保留但剥掉白底/棚拍等卡片取景词
        detail = sanitize_prop_anchor(asset.visual_anchor or "")
        if detail:
            return (
                f"<Picture {pic_no}> is the prop identity reference card of "
                f"{asset.name}: {detail}."
            )
        return f"<Picture {pic_no}> is the prop identity reference card of {asset.name}."
    role = _PICTURE_ROLES.get(asset.kind.value, "story element reference of {name}")
    return f"<Picture {pic_no}> is the {role.format(name=asset.name)}."


def compile_h3_prompt(
    segment: Segment,
    assets_by_id: dict[str, Asset],
    scene_summary: str = "",
    style_line: str = "",
    opening_frame: bool | None = None,
    storyboard_cells: int = 0,
) -> str:
    """从 Segment 结构化数据编译六段式 reference 提示词（英文正文+中文台词）。

    opening_frame：显式声明本段是否带开场帧槽位（<Picture 1>）。缺省跟随
    segment.continuity.enabled；关键帧段（TASK-031）在产视频时用
    opening_frame=True 重编译，让非连续段也拥有 Picture 1 槽位且编号不错位。

    storyboard_cells：<Picture 1> 是多宫格分镜板（方案A）时的**实际格数**
    （拼板格子数，≤MAX_GRID_CELLS）；>0 时在 subject_definitions 注入官方
    R2V 宫格声明句（实际格数/镜序 + "宫格本身不上画"护栏），retention 行
    同步声明格数；0（缺省，含防御性非正值）不注入——普通开场帧不注入。
    """
    relay = segment.continuity.enabled if opening_frame is None else opening_frame
    pictures: list[tuple[int, str, Asset | None]] = []
    if relay:
        pictures.append((1, "前段尾帧", None))
    for ref in segment.asset_refs:
        pictures.append((len(pictures) + 1, ref.asset_id, assets_by_id.get(ref.asset_id)))
    # 说话人绑定其参考图（官方句式 <Subject N> is ... in <Picture M>）
    pic_by_asset: dict[str, int] = {
        asset.asset_id: pic_no for pic_no, _, asset in pictures if asset is not None
    }

    # 说话人编号 (S1)(S2)...：按本段台词出场顺序分配
    speakers: dict[str, str] = {}
    for shot in segment.shots:
        for d in shot.dialogue_refs:
            speakers.setdefault(d.speaker, f"(S{len(speakers) + 1})")
    speaker_by_name = {k.strip(): v for k, v in speakers.items()}
    placements = _placement_by_name(segment)

    # ---- subject_definitions ----
    subj: list[str] = []
    if relay:
        if storyboard_cells > 0:
            # 宫格分镜板参考：官方 R2V 口径不叠加单帧锚定，
            # 换成"首格定开场、宫格布局不上画"的宫格口径（声明实际格数）
            subj.append(_storyboard_anchor(storyboard_cells))
        else:
            subj.append(RELAY_ANCHOR_EN)
    for pic_no, _, asset in pictures:
        if pic_no == 1 and relay:
            continue
        subj.append(_picture_definition(pic_no, asset))

    # 在场角色逐一建立 <Subject N>（官方：不留"没名字的那个人"——只给
    # 说话人建 Subject 时，不开口的角色只能靠 Picture 定义带锚点，容易被
    # 读成无名路人甚至复制一份）。外貌锚点全文只在此处出现一次；
    # 开场空间状态注入同一行，与关键帧提示词逐字对应。
    speaker_by_asset: dict[str, str] = {}
    for speaker, sid in speaker_by_name.items():
        owner = _character_owner(speaker, assets_by_id)
        if owner is not None:
            speaker_by_asset.setdefault(owner.asset_id, sid)
    speaker_label: dict[str, str] = {}
    subject_no = 0
    for _, _, asset in pictures:
        if asset is None or asset.kind.value != "character":
            continue
        subject_no += 1
        sid = speaker_by_asset.get(asset.asset_id, "")
        sid_part = f" {sid}" if sid else ""
        binding = pic_by_asset.get(asset.asset_id)
        bound = f" in <Picture {binding}>" if binding else ""
        anchor = sanitize_character_anchor(asset.visual_anchor or "")
        anchor_part = f", with {anchor}," if anchor else ","
        placement = placements.get(asset.name.strip())
        placement_part = f" opening placement: {placement}," if placement else ""
        tail = (
            "identity kept identical in every shot."
            if placement
            else "kept identical in every shot."
        )
        subj.append(
            f"<Subject {subject_no}> is {asset.name}{sid_part}{bound}"
            f"{anchor_part}{placement_part} {tail}"
        )
        if sid:
            speaker_label[speaker] = f"<Subject {subject_no}> {sid}"

    # ---- summary ----
    beat = " ".join(
        shot.description.strip().rstrip(".") for shot in segment.shots if shot.description
    )
    summary = (
        "[reference generation] " + (beat or scene_summary or "The scene unfolds as scripted.")
    )

    # ---- retention_analysis ----
    ret: list[str] = []
    if relay:
        if storyboard_cells > 0:
            ret.append(
                f"<Picture 1> (appears in [Shot 1]): fully_preserved - frame 0 "
                f"opens on the first cell of the {storyboard_cells}-cell "
                "storyboard, the shot order follows the remaining cells, and "
                "the grid layout itself never appears on screen."
            )
        else:
            ret.append(
                "<Picture 1> (appears in [Shot 1]): fully_preserved - opening "
                "composition, character placement and lighting exactly as supplied; "
                "the action starts from this frame."
            )
    for pic_no, _, asset in pictures:
        if pic_no == 1 and relay:
            continue
        kind = asset.kind.value if asset is not None else ""
        line = _RETENTION_BY_KIND.get(kind, _RETENTION_GENERIC)
        ret.append(f"<Picture {pic_no}> (appears in [Shot 1]): {line}")

    # ---- detailed_description ----
    dd: list[str] = [style_line.strip() or DEFAULT_STYLE_LINE]
    for shot in segment.shots:
        head = (
            f"[Shot {shot.shot_no}]"
            if shot.shot_no == 1
            else f"[Shot {shot.shot_no}] At {_mmss(shot.cutpoint_sec)},"
        )
        body = shot.action.strip() or shot.description.strip()
        line = f"{head} {shot.camera or 'static shot'}. {body}"
        for d in shot.dialogue_refs:
            sid = speaker_by_name.get(d.speaker.strip(), "")
            who = speaker_label.get(d.speaker.strip(), d.speaker.strip())
            tone = d.tone.strip()
            manner = f", {tone}" if tone else ""
            line += (
                f" {who} says in Chinese{manner}, "
                f"{_LIPSYNC}: <d>[中文] {d.line}</d>"
            )
        dd.append(line)
    detailed = "\n".join(dd)

    # ---- 组装（官方六段顺序） ----
    parts = [
        "subject_definitions:\n" + "\n".join(subj),
        "summary:\n" + summary,
        "retention_analysis:\n" + "\n".join(ret),
        "detailed_description:\n" + detailed,
        "overall_soundscape:\n" + (segment.soundscape.strip() or DEFAULT_SOUNDSCAPE),
        "non_diegetic_music:\n" + (segment.music.strip() or DEFAULT_MUSIC),
    ]
    return "\n\n".join(parts) + "\n"


# 关键帧负向词（经工作流负向通道，不写进正向提示词——Lightning cfg=1 实测
# 正向否定句会反向引入）：拦字幕/文字/水印/LOGO、多格拼贴与复制人
KEYFRAME_NEGATIVE = (
    "text, caption, subtitle, watermark, logo, signature, border, "
    "split screen, collage, multiple panels, grid layout, low quality, "
    "deformed hands, extra fingers, blurry, "
    "duplicate person, clone face, twin, extra people"
)


@dataclass
class ReferenceSheet:
    """拼合参考图绑定（Edit 工作流只有 3 个参考槽时的超编资产载体）。

    多张资产卡横向拼成一张「设定表」占一个 Picture 槽，提示词里逐个声明
    sheet 从左到右是谁，身份锁定句沿用同一槽位指称。
    """

    assets: list[Asset]


def _ref_card_phrase(asset: Asset) -> str:
    """单张参考卡在 I2I 指令里的指称短语（Picture N is ...）。"""
    if asset.kind.value == "character":
        return f"{asset.name}'s character reference card"
    if asset.kind.value == "scene":
        return f"a wide-angle overview of the same location（{asset.name}）"
    return f"the reference card of {asset.name}"


def compile_keyframe_prompt(
    segment: Segment,
    assets_by_id: dict[str, Asset],
    style_line: str = "",
    extra_prompt: str = "",
    include_identity_anchors: bool = True,
    reference_bindings: list | None = None,
    aspect: str = "",
    description_override: str = "",
) -> str:
    """编译段开场锚点关键帧的 **I2I 编辑指令**（TASK-031/036/045）。

    TASK-045（依据 Qwen-Image-Edit-2509 官方文档/源码与社区实证调研，见
    tasks/TASK-045-keyframe-qwen-edit.md）：提示词是给**扩散模型**的画面描述，
    不是给导演的工作指令——
    - 参考图指称必须用 **Picture 1/2/3**（`TextEncodeQwenImageEditPlus` 按槽位
      把参考图编码成该标签，`[img0]` 这类占位符不会被解析，等于噪声）；
    - 人数用**正向声明**（"Exactly N people …, no other people"）：负向通道对
      该模型基本无效（官方示例负向词只给一个空格）；
    - 逐人**具名**（角色资产名）+ 服装短标签，全文用名字指称；
    - 身份锁定一句话（"Keep … exactly the same as in their Picture reference"）；
    - 删掉一切元指令（"写清…""必须方向自洽""景别按文字来"）——扩散模型不执行
      指令，只当噪声，还会稀释真正的画面描述。

    组织顺序：风格 → 画面描述 → 人数+逐人点名 → 背景 → 道具 → 参考图指称 →
    身份锁定 → 构图与画幅。无参考图（纯文生图）时退化为自足的画面描述 + 身份锚点。

    多宫格逐格生成时传对应 shot 的动作描述（方案A：逐格生成+程序拼宫格）。
    """
    style = style_line.strip()
    description = (description_override or segment.keyframe_description).strip()
    if not description:
        # 存量分镜/LLM 漏写：回退首镜的开场状态（action 开头或 action 前段）
        first = segment.shots[0]
        description = first.action.strip() or first.description.strip()
    description = _strip_dynamic_noise(description)
    if extra_prompt.strip():
        description = f"{extra_prompt.strip()}，{description}"

    bindings = list(reference_bindings or [])
    placements = _placement_by_name(segment)
    in_frame = in_frame_by_name(segment)
    people: list[str] = []
    off_frame: list[str] = []
    scene_lines: list[str] = []
    prop_lines: list[str] = []
    for ref in segment.asset_refs:
        asset = assets_by_id.get(ref.asset_id)
        if asset is None:
            continue
        kind = asset.kind.value
        if kind == "character":
            if not in_frame.get(asset.name.strip(), True):
                # 本段在场但开场帧看不到：不进画面人名单
                off_frame.append(asset.name)
                continue
            anchor = sanitize_character_anchor(asset.visual_anchor or "")
            placement = placements.get(asset.name.strip())
            outfit = _short_outfit(anchor) if anchor else ""
            tag = f"（{outfit}）" if outfit else ""
            if placement:
                people.append(f"{asset.name}{tag}{placement}")
            elif anchor:
                people.append(f"{asset.name}（{anchor}）")
            else:
                people.append(asset.name)
            if include_identity_anchors and anchor and placement:
                people[-1] = f"{people[-1]}（{anchor}）"
        elif kind == "scene":
            # 只取本段第一个场景作地点（此前把所有场景都写成 setting，双场景段
            # 会出现两条互相打架的地点行）
            if not scene_lines:
                anchor = sanitize_scene_anchor(asset.visual_anchor or "")
                scene_lines.append(anchor or asset.name)
        elif (asset.visual_anchor or "").strip():
            prop_detail = sanitize_prop_anchor(asset.visual_anchor or "")
            if prop_detail:
                prop_lines.append(prop_detail)

    parts: list[str] = []
    if style:
        # 首句风格限定词与剧本头一字不差，带头定调
        parts.append(style)

    # —— 画面主体：分镜产出的静态画面描述（构图/景别/人物/持物/光效）——
    parts.append(f"Cinematic still frame: {description}")

    # —— 人数正向声明 + 逐人点名（TASK-045）——
    # 社区共识与 issue #120 实测：负向通道拦不住"多余的人"，正向声明人数才有效；
    # 给参考图里的人起名字并全文用名字指称，多人编辑精度明显提升。
    if people:
        head = (
            f"Exactly {len(people)} {'person' if len(people) == 1 else 'people'} "
            "in the frame, no other people: "
        )
        line = head + "；".join(people)
        if off_frame:
            line += f"；{'、'.join(off_frame)} 在画面外，其余空间为空"
        parts.append(line)
    elif off_frame:
        parts.append(f"画面内无人；{'、'.join(off_frame)} 在画面外，空间为空")
    else:
        parts.append("画面内无人")

    # —— 参考图指称（TASK-045）：必须用 Picture N ——
    # ComfyUI 的 TextEncodeQwenImageEditPlus 不解析 [img0] 这类占位符：它把参考图
    # 按槽位顺序编码为 "Picture 1/2/3" 再原样拼接正文，指称参考图只有与它自身的
    # 标签一致才可被理解（comfy_extras/nodes_qwen.py；官方示例也是散文式指称）。
    # 只描述每张参考图"是什么"，不给"空间结构不变"式锁（空座位会被锁住，TASK-037），
    # 也不写"景别按文字来"之类的元指令——那是对导演说的话，扩散模型只当噪声。
    ref_map: list[str] = []
    for index, binding in enumerate(bindings):
        label = f"Picture {index + 1}"
        if isinstance(binding, ReferenceSheet):
            # 拼合设定表：逐卡声明从左到右的顺序，与落盘拼图的实际排列一致
            entries = "；".join(_ref_card_phrase(a) for a in binding.assets)
            ref_map.append(
                f"{label} is a side-by-side reference sheet, "
                f"from left to right: {entries}"
            )
        else:
            ref_map.append(f"{label} is {_ref_card_phrase(binding)}")
    if scene_lines:
        parts.append(f"background: {scene_lines[0]}")
    if prop_lines:
        parts.append("visible props: " + "；".join(prop_lines))
    if ref_map:
        parts.append("；".join(ref_map))

    # —— 身份锁定（一句话，官方/社区惯用句式）——
    if bindings and people:
        parts.append(
            "Keep every character's face, hairstyle and clothing exactly the "
            "same as in their Picture reference."
        )
    elif include_identity_anchors and people:
        # 纯文本生图（没有角色卡锁脸）时才把身份描写写进正文
        parts.append("人物脸部与服装必须与上述描写完全一致")

    parts.append(_composition_line(aspect))
    if aspect:
        parts.append(f"{aspect} 画幅")
    parts.append("single frame composition, photorealistic detail, high detail")
    return "，".join(parts)
