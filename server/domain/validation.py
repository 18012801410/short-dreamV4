"""领域校验规则（TASK-003，对齐官方 H3 dialect）：分镜护栏、提示词结构、参考截断。

H3 提示词结构契约来自官方 h3-prompt-writing（base-en / ref-en，MiniMax-AI/MiniMax-H3），
本地权威内化见 short-drama-video-prompts/references/minimax-h3*.md，要点：
- base（无参考图）→ 三段：integrated_multimodal_description / overall_soundscape /
  non_diegetic_music，首个字段顶格开头；
- reference（有参考图，含连续性尾帧）→ 六段：subject_definitions / summary /
  retention_analysis / detailed_description / overall_soundscape / non_diegetic_music；
- 时间线：[Shot 1] 不带时间戳；新镜头行 [Shot N] 必带 At MM:SS.mmm，与 shots 切点一致、
  严格递增且 < 段时长；无时间戳的 [Shot k] 只是行文回指，不构成切点；
- 台词 <d>[语言] 逐字</d> 只出现在画面段时间线字段（三段=首字段，六段=detailed_description）；
- 六段附加：summary 以方括号任务前缀开头；retention_analysis 行用固定关系标记；
  正文引用的 <Picture/Video/Audio/Subject N> 必须在 subject_definitions 定义。
结构字段恒为英文（官方口径）；promptLang 只切正文语言，对白永远逐字原文。
"""

from __future__ import annotations

import re

from server.domain.entities import H3Prompt, ResolvedReference, Segment
from server.domain.errors import ValidationFailedError

H3_PROMPT_MAX_CHARS = 7000
MAX_REFERENCE_IMAGES = 9
# 单段宫格最多格数（=取前 N 个 shot；超出的镜头交给视频模型按提示词发挥）
MAX_GRID_CELLS = 6

BASE_FIELDS = (
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)
REF_FIELDS = (
    "subject_definitions:",
    "summary:",
    "retention_analysis:",
    "detailed_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)
DESCRIPTION_INDEX_IN_REF = 3
VISUAL_MARKERS = (
    "fully_preserved",
    "partially_preserved",
    "attribute_transfer",
    "weak_reference",
)
AUDIO_MARKERS = ("fully_copy", "partially_copy", "reference", "weak_reference")

SHOT_LINE_RE = re.compile(r"\[Shot (\d+)\](?: At (\d{2}):(\d{2})\.(\d{3}))?")
LABEL_RE = re.compile(r"<(Subject|Picture|Video|Audio) (\d+)>")
D_TAG_WITHOUT_LANG_RE = re.compile(r"<d>(?!\[)")

# TASK-032 关键帧纯静态：keyframe_description 是第 0 帧生图依据，台词与
# 口型证据写进去会被静态画面渲染成画面内文字/张嘴表情（S01G02 实证：
# 台词泄漏成衣服上的胸牌文字）
_KEYFRAME_DYNAMIC_RE = re.compile(
    r"<d>|\bsays\b|\bspeaks?\b|\bspeaking\b|lips move|\bshouts\b|\bwhispers?\b",
    re.IGNORECASE,
)
# TASK-045 关键帧无名人物：keyframe_description 里的可见人物必须是具名角色资产。
# "two girls / a third boy / a crowd" 这类写法没有参考卡，图生图模型要么凭空造人、
# 要么把参考卡里的角色复印成好几个（S02G02 双胞胎、S02G04 乙被画成第二个甲，均实证）。
# 命中形态：数词/序数词+人物词、冠词+单个人物词、集合人群名词。
_PEOPLE_NOUNS = (
    r"girls?|boys?|men|women|guys?|kids?|children|persons?|people|"
    r"students?|friends|classmates|pedestrians?|bystanders?|passersby|"
    r"onlookers?|adults?|elders?|strangers?|crowds?|twins?"
)
# 冠词与人物词之间允许出现的外貌形容词（"a chubby boy" 是 S02G04 的真实原句）；
# 形容词白名单只收外貌词，"a tall tree" 这类不会命中（tree 不是人物词）
_APPEARANCE_ADJ = (
    r"chubby|plump|skinny|thin|fat|young|little|old|elderly|teenage|tall|short|"
    r"small|tiny|smiling|crying|sobbing|pale|freckled"
)
_KEYFRAME_UNNAMED_PEOPLE_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|another|\d+)\s+"
    r"(?:other\s+|more\s+)?(?:(?:" + _APPEARANCE_ADJ + r")\s+)?(?:" + _PEOPLE_NOUNS + r")\b"
    r"|\b(?:a|an|the)\s+(?:third|fourth|fifth)\s+(?:" + _PEOPLE_NOUNS + r")\b"
    r"|\b(?:a|an)\s+(?:(?:" + _APPEARANCE_ADJ + r")\s+)?(?:" + _PEOPLE_NOUNS + r")\b"
    r"|\b(?:the\s+)?(?:crowds?|bystanders?|passersby|onlookers?)\b",
    re.IGNORECASE,
)
# placement 必须是英文位置状态（编译进英文提示词，中文会污染语言口径）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def expects_reference_mode(segment: Segment) -> bool:
    """平台口径：绑定了参考图（资产图或连续性尾帧）→ reference 六段；否则 base 三段。

    官方 first_frame / first_last_frame 模式待平台建模「起始帧」槽位后在
    TASK-011 模式矩阵中接入，当前 Segment 没有该槽位。
    """
    return bool(segment.asset_refs) or segment.continuity.enabled


def has_continuity_tail(segment: Segment) -> bool:
    """生产时是否会把前段尾帧插到参考图第 0 位（与 usecases/handler 口径一致）。

    Continuity 模型构造时已强制 enabled → with_prev_segment_key 非空；
    首段没有前段，生产命令层会按位置忽略，这里对齐生产语义。
    """
    return segment.continuity.enabled and bool(segment.continuity.with_prev_segment_key)


def expected_picture_count(segment: Segment) -> int:
    """实际送入 H3 的参考图张数 = 资产图 + 连续性尾帧（尾帧固定 <Picture 1>）。"""
    return len(segment.asset_refs) + (1 if has_continuity_tail(segment) else 0)


def _field_spans(body: str, fields: tuple[str, ...]) -> list[int] | None:
    """各字段标记的位置；字段缺失或顺序错误返回 None。"""
    positions: list[int] = []
    for field in fields:
        found = re.search(rf"(?m)^{re.escape(field)}", body)
        if found is None:
            return None
        positions.append(found.start())
    if any(
        later <= earlier for earlier, later in zip(positions, positions[1:], strict=False)
    ):
        return None
    return positions


def _section_text(body: str, fields: tuple[str, ...], index: int) -> str:
    spans = _field_spans(body, fields)
    if spans is None:
        return ""
    start = spans[index] + len(fields[index])
    end = spans[index + 1] if index + 1 < len(fields) else len(body)
    return body[start:end]


def _cutpoint_label(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    mm, rem = divmod(total_ms, 60_000)
    ss, mmm = divmod(rem, 1000)
    return f"{mm:02d}:{ss:02d}.{mmm:03d}"


def _check_shot_timeline(description: str, segment: Segment) -> list[str]:
    errors: list[str] = []
    shots = [
        (int(no), None if hh == "" else int(hh) * 60 + int(mm) + int(sss) / 1000)
        for no, hh, mm, sss in SHOT_LINE_RE.findall(description)
    ]
    if not shots or shots[0][0] != 1:
        return [f"画面段必须以 [Shot 1] 开头（当前首个镜头标记：{shots[0][0] if shots else '无'}）"]
    if shots[0][1] is not None:
        errors.append("[Shot 1] 是开场镜头，不携带 At MM:SS.mmm 切点")
    cutpoints = {shot.shot_no: shot.cutpoint_sec for shot in segment.shots}
    seen: set[int] = {1}
    expected_next = 2
    previous = -1.0
    for number, seconds in shots[1:]:
        if seconds is None:
            if number not in seen:
                errors.append(f"[Shot {number}] 是新镜头行，必须带 At MM:SS.mmm 切点")
            continue  # 已出现过的镜头号是行文回指，不算切点
        if number != expected_next:
            errors.append(f"[Shot {number}] 镜头序号断裂，应为 [Shot {expected_next}]")
        if seconds <= previous:
            errors.append(f"[Shot {number}] 切点 {seconds}s 未严格晚于上一镜头")
        if seconds >= segment.duration_sec:
            errors.append(
                f"[Shot {number}] 切点 {seconds}s 落在段时长 {segment.duration_sec}s 之外"
            )
        expected = cutpoints.get(number)
        if expected is None or abs(seconds - expected) > 0.0005:
            errors.append(
                f"[Shot {number}] 切点 {seconds}s 与分镜 shot 切点不一致"
                f"（应为 {_cutpoint_label(expected) if expected is not None else '无此镜头'}）"
            )
        seen.add(number)
        previous = seconds
        expected_next = number + 1
    return errors


def validate_h3_prompt(prompt: H3Prompt, segment: Segment) -> list[str]:
    """按官方 dialect 结构校验，返回错误列表（空 = 通过）。确定性规则，不调 LLM。"""
    errors: list[str] = []
    text = prompt.text
    if len(text) > H3_PROMPT_MAX_CHARS:
        errors.append(f"h3_prompt 超长：{len(text)} > {H3_PROMPT_MAX_CHARS} 字符")

    reference_mode = expects_reference_mode(segment)
    fields = REF_FIELDS if reference_mode else BASE_FIELDS
    spans = _field_spans(text, fields)
    if spans is None:
        missing = [f for f in fields if not re.search(rf"(?m)^{re.escape(f)}", text)]
        errors.append(
            "H3 字段缺失或顺序不符合官方 "
            f"{'六段(reference)' if reference_mode else '三段(base)'} 结构：{missing}"
        )
        return errors
    if spans[0] != 0:
        errors.append(
            f"{fields[0].rstrip(':')} 必须顶格开头"
            "（base/reference 模式没有对齐首行；对齐语义由 subject_definitions/retention 承担）"
        )

    description_index = DESCRIPTION_INDEX_IN_REF if reference_mode else 0
    description = _section_text(text, fields, description_index)
    errors.extend(_check_shot_timeline(description, segment))

    # 切点一致性（解析期不强制，规范化后在域校验把关）
    cutpoints = [shot.cutpoint_sec for shot in segment.shots]
    if cutpoints[0] != 0.0:
        errors.append(f"首镜切点必须为 0.00（当前 {cutpoints[0]}）")
    for prev, cur in zip(cutpoints, cutpoints[1:], strict=False):
        if cur <= prev:
            errors.append(f"切点必须严格单调递增（{prev} -> {cur}）")
            break
    if cutpoints and cutpoints[-1] >= segment.duration_sec:
        errors.append(
            f"末镜切点 {cutpoints[-1]} 必须小于段时长 {segment.duration_sec}"
        )

    # segment_key 必须与 scene_id/index 一致（解析期不强制，规范化后在域校验把关）
    canonical_key = f"S{int(segment.scene_id[1:]):02d}G{segment.index:02d}"
    if segment.segment_key != canonical_key:
        errors.append(
            f"segment_key {segment.segment_key} 与 scene_id/index 不一致（应为 {canonical_key}）"
        )

    music = _section_text(text, fields, len(fields) - 1).strip()
    if not music:
        errors.append("non_diegetic_music 不得留空（无配乐写 N/A）")

    if D_TAG_WITHOUT_LANG_RE.search(text):
        errors.append('存在缺少语言标签的 <d> 块：台词必须写成 <d>[语言] 逐字台词</d>')

    misplaced = [
        field.rstrip(":")
        for index, field in enumerate(fields)
        if index != description_index and "<d>" in _section_text(text, fields, index)
    ]
    if misplaced:
        errors.append(f"<d> 台词只能写在画面段时间线（{'、'.join(misplaced)} 中出现）")

    dialogues = [d for shot in segment.shots for d in shot.dialogue_refs]
    if dialogues and not re.findall(r"<d>.*?</d>", description, flags=re.DOTALL):
        errors.append("段内有台词但画面段缺少 <d> 块")
    d_blocks = re.findall(r"<d>(.*?)</d>", description, flags=re.DOTALL)
    for d in dialogues:
        if not any(d.line in block for block in d_blocks):
            errors.append(f"台词未逐字进入画面段 <d> 块：{d.speaker}: {d.line[:30]}…")
    # 反向对账：<d> 里的每句台词必须由某个 dialogue_ref 背书，
    # 否则本段会读出剧本中分配给其他段的台词（成片里重复说两遍）
    for block in d_blocks:
        if not any(d.line in block for d in dialogues):
            errors.append(
                f"<d> 块台词没有对应的 dialogue_ref（会提前/重复读出其他段的台词）："
                f"{block.strip()[:30]}…"
            )
    dirty_blocks = [b for b in d_blocks if "（" in b or "(" in b]
    if dirty_blocks:
        errors.append(
            f"<d> 块含括号舞台指示，会被 H3 当台词读出声：{dirty_blocks[0][:40]}…；"
            "<d> 只装说出口的话，括号里的动作改写成画面描述"
        )

    expected_pictures = expected_picture_count(segment)
    mentioned_pictures = {
        int(number) for kind, number in LABEL_RE.findall(text) if kind == "Picture"
    }
    expected_set = set(range(1, expected_pictures + 1))
    if mentioned_pictures != expected_set:
        if has_continuity_tail(segment):
            convention = (
                "连续段：前段尾帧固定是 <Picture 1>（0.00s 开场画面），"
                f"资产图从 <Picture 2> 开始按 asset_refs 顺序编号，共 {expected_pictures} 张"
            )
        else:
            convention = (
                f"非连续段：资产图从 <Picture 1> 开始按 asset_refs 顺序编号，"
                f"共 {expected_pictures} 张"
            )
        errors.append(
            f"<Picture> 编号与实际参考图不一致：实际送入 {expected_pictures} 张，"
            f"正文使用了 {sorted(mentioned_pictures) if mentioned_pictures else '无'}；"
            f"编号集合必须恰为 {{1..{expected_pictures}}}。{convention}"
        )

    if reference_mode:
        summary = _section_text(text, fields, 1).strip()
        if summary and not summary.startswith("["):
            errors.append("summary 必须以方括号任务前缀开头（如 [reference generation]）")
        retention = _section_text(text, fields, 2)
        markers = VISUAL_MARKERS + AUDIO_MARKERS
        bad_lines = [
            line.strip()
            for line in retention.splitlines()
            if line.strip().startswith("<") and not any(m in line for m in markers)
        ]
        if bad_lines:
            errors.append(
                f"retention_analysis 条目必须使用固定关系标记（{'/'.join(markers)}）："
                f"{bad_lines[0][:50]}…"
            )
        defined = set(LABEL_RE.findall(_section_text(text, fields, 0)))
        used = set(LABEL_RE.findall(text[spans[1]:]))
        undefined = sorted(f"<{kind} {number}>" for kind, number in used - defined)
        if undefined:
            errors.append(f"引用了未在 subject_definitions 定义的标签：{', '.join(undefined)}")
    return errors


def _check_subject_placements(
    segment: Segment, character_assets: dict[str, str] | None
) -> list[str]:
    """在场角色的空间状态对账（TASK-032，仅分镜生成闭环强制）。

    character_assets 为空（手动编辑/存量重生成路径）时跳过——存量分镜
    无 subject_placements 字段，不能把历史数据挡死在编辑与重出门外；
    新分镜由 agents 的校验-反馈-重试闭环保证字段完整。
    """
    if character_assets is None:
        return []
    errors: list[str] = []
    character_refs = {
        character_assets[ref.asset_id]
        for ref in segment.asset_refs
        if ref.asset_id in character_assets
    }
    seen: dict[str, str] = {}
    for placement in segment.subject_placements:
        name = placement.name.strip()
        if not name:
            errors.append("subject_placements 存在空 name 条目")
            continue
        if name in seen:
            errors.append(
                f"subject_placements 中 {name} 重复出现，每个角色只允许一条"
            )
            continue
        seen[name] = placement.placement.strip()
        if not seen[name]:
            errors.append(f"subject_placements 中 {name} 的 placement 为空")
        elif _CJK_RE.search(placement.placement):
            errors.append(
                f"{name} 的 placement 必须是英文位置状态短语"
                f"（如 in the driver's seat），当前含中文：{placement.placement[:30]}…"
            )
    missing = sorted(character_refs - set(seen))
    if missing:
        errors.append(
            "在场角色缺少开场空间状态（subject_placements）："
            f"{'、'.join(missing)}——每个在场角色逐人一条英文位置状态"
            "（谁坐在哪个座位/站在哪里、身体朝向、控制部位如 hands on the steering wheel），"
            "载具/家具/器械场景必须写到具体座位或站位"
        )
    orphan = sorted(set(seen) - character_refs)
    if orphan:
        errors.append(
            f"subject_placements 含不在本段 asset_refs 中的角色：{'、'.join(orphan)}"
            "（条目必须与在场角色资产一一对应，检查名字拼写）"
        )
    return errors


def validate_segment(
    segment: Segment,
    *,
    known_asset_ids: set[str],
    character_assets: dict[str, str] | None = None,
) -> None:
    """跨实体校验：资产引用必须存在于当前 active 资产集 + H3 提示词结构。

    段内结构不变量（时长/切点/键一致性）由 Segment 模型构造时强制。
    校验失败抛 VALIDATION_FAILED，携带全部错误供 LLM 重试反馈。
    character_assets：角色资产 asset_id→name 映射；提供时强制在场角色
    逐人给出 subject_placements 开场空间状态（TASK-032），仅分镜生成
    闭环传入（存量编辑/重生成路径传 None 保持兼容）。
    """
    errors: list[str] = []
    for ref in segment.asset_refs:
        if ref.asset_id not in known_asset_ids:
            errors.append(f"asset_refs 引用了不存在的资产：{ref.asset_id}")
    if segment.keyframe_description and _KEYFRAME_DYNAMIC_RE.search(
        segment.keyframe_description
    ):
        errors.append(
            "keyframe_description 必须是纯静态画面（第 0 帧生图依据），"
            "禁止台词块 <d>、says/speaks/lips move 等动态与口型描述——"
            "静态帧会把台词渲染成画面文字（已实测）"
        )
    if segment.keyframe_description and _KEYFRAME_UNNAMED_PEOPLE_RE.search(
        segment.keyframe_description
    ):
        errors.append(
            "keyframe_description 出现无名人物（如 two girls / a third boy / a crowd）。"
            "可见人物只能是 subject_placements 里 in_frame=true 的角色资产，逐人写资产名，"
            "可见人数与 in_frame 人数一致——没有参考卡的人物会被图生图模型凭空造人"
            "或复制成参考里角色的复制品（S02G02 双胞胎、S02G04 实测）；"
            "确需路人氛围时写 unrelated pedestrians far in the background, "
            "faces not visible，近景只保留具名角色"
        )
    errors.extend(_check_subject_placements(segment, character_assets))
    errors.extend(validate_h3_prompt(segment.h3_prompt, segment))
    if errors:
        raise ValidationFailedError(
            f"分镜段 {segment.segment_key} 校验失败",
            details={"errors": errors},
        )


def validate_asset_set(kinds_and_names: list[tuple[str, str]]) -> None:
    """同 Project 内 (kind, name) 唯一（DOMAIN_MODEL Asset 不变量）。"""
    seen: set[tuple[str, str]] = set()
    dupes: list[tuple[str, str]] = []
    for pair in kinds_and_names:
        if pair in seen:
            dupes.append(pair)
        seen.add(pair)
    if dupes:
        raise ValidationFailedError(
            "资产 (kind, name) 重复",
            details={"duplicates": [f"{k}:{n}" for k, n in dupes]},
        )


def truncate_references(
    refs: list[ResolvedReference],
) -> tuple[list[ResolvedReference], list[str]]:
    """解析后参考图 >9 张时保留前 9 张（asset_refs 顺序，即提示词 <Picture> 顺序）。

    不按优先级重排——任何重排都会破坏提示词已写死的 <Picture N> 编号。
    重要性排序是分镜阶段的约束（asset_refs 按主要角色>场景>道具排列且 ≤9）。
    """
    if len(refs) <= MAX_REFERENCE_IMAGES:
        return refs, []
    kept = refs[:MAX_REFERENCE_IMAGES]
    dropped = refs[MAX_REFERENCE_IMAGES:]
    warnings = [
        "参考图超过 9 张上限，已按 asset_refs 顺序保留前 "
        f"{MAX_REFERENCE_IMAGES} 张，丢弃（编号在提示词中已失效，请回分镜精简 asset_refs）："
        + ", ".join(f"{r.kind.value}:{r.asset_id}" for r in dropped)
    ]
    return kept, warnings
