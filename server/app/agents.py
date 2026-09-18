"""三个 Agent（TASK-008/009/010）：LLM 编排 + 护栏（AI_SPEC）。

- ScriptAgent：想法 → 结构化剧本（温度 0.8；时长偏差 >30% 附 warnings）
- AssetAgent：剧本 → 资产清单 + 生图提示词（温度 0.6；英文提示词含风格；主角必有主设定）
- StoryboardAgent：剧本+资产 → 分镜 + H3 提示词（温度 0.4；确定性校验失败带错误重试 1 次）
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from server.domain.entities import (
    Asset,
    AssetRef,
    Beat,
    DialogueLine,
    EpisodeOutline,
    ScriptContent,
    SeriesOutline,
    StoryboardContent,
)
from server.domain.enums import AssetKind, BeatType, DramaticTone, PromptLang
from server.domain.errors import ValidationFailedError
from server.domain.name_registry import enforce_name_registry, names_match
from server.domain.textnorm import split_tone_from_line, strip_stage_directions
from server.domain.validation import validate_segment

H3_RULES_DIGEST = (
    "H3 提示词硬规则（每段 h3_prompt.text，必须逐条满足）：\n"
    "1. 无参考图段（base，三段）：正文以 \"integrated_multimodal_description:\" 行开头，"
    '后接 "overall_soundscape:" 与 "non_diegetic_music:"（无配乐写 N/A）。\n'
    "2. 有参考图段（reference，六段）：依次 \"subject_definitions:\""
    "（每个在场人物/场景一句 <Subject N> is ... in <Picture M>, with ...，并绑说话人 ID 如 (S1)）。"
    "角色的 <Subject N> 必须把其资产 visual_anchor 的服装/外貌身份锁逐项译成英文写入"
    "（围裙/领型/名牌/发型/配饰等，一项不落），"
    "并显式写 wearing exactly the same outfit and appearance as shown in <Picture M>, "
    "kept identical in every shot of the film——跨段不得增删衣物、不得改变发型；"
    "\"summary:\"（以 [reference generation] 开头）、\"retention_analysis:\""
    "（每条以 <Picture N>/<Subject N> 开头并用固定标记 fully_preserved/partially_preserved）、"
    "\"detailed_description:\"（时间线）、\"overall_soundscape:\"、\"non_diegetic_music:\"。\n"
    "3. 时间线与动作密度（detailed_description 是六段主体，官方口径 350-500 英文词；"
    "base 模式同规则作用于 integrated_multimodal_description）："
    "密度按时长分级——≤8 秒的段 250-350 词，9-12 秒的段 350-450 词，"
    "13-15 秒的段 450-550 词，禁止为凑词数重复描写；"
    "开头先用 1-2 句英文定风格（写在 [Shot 1] 之前），然后 [Shot 1] 无时间戳开场，"
    "之后每个新镜头一行 \"[Shot N] At MM:SS.mmm ...\"，切点=前面镜头秒数累计，"
    "与 shots 的 cutpoint_sec 完全一致且 < duration_sec。"
    "每个镜头块必须把该镜时间窗（本镜切点 → 下一镜切点/段尾）内的动作写成显式渐进时间线："
    "镜头开始时什么状态 → 中途发生什么 → 镜头结束时落在什么状态，"
    "同时写清构图与人物位置、环境与光、运镜（类型+幅度+速度）、镜头内的音效；"
    "禁止缩写成剧情梗概或单句状态快照。\n"
    "4. 台词逐字写进 <d>[语言] 原文</d>，且只出现在画面段时间线字段；"
    "<d> 里只允许角色嘴里说出来的话——原文中括号内的舞台指示（（扑通跪下）/（冷笑）等）"
    "严禁写进 <d>，要剥离成画面动作描述；"
    "每句台词前的同一分句写说话人（<Subject N> (S1) / 名字）+ 口型证据"
    "（his lips move as he says / says in an off-screen voiceover ... "
    "while their lips remain completely closed）。\n"
    "5. 正文引用的每个 <Picture N>/<Subject N> 必须在 subject_definitions 里定义；"
    "正文用英文，禁角色真名（用 the old ferryman 这类通用身份）；台词原文保留中文。\n"
    "6. 参考图编号必须与实际送入顺序逐字对应（实际张数 = asset_refs 数 + 连续段尾帧 1）：\n"
    "   - 连续段（continuity.enabled=true）：前段尾帧固定作为 <Picture 1>"
    "（0.00s 开场画面，承接上段结尾），subject_definitions 第一句必须是强锚定句："
    " \"<Picture 1> is the exact opening frame of this video: frame 0 must match this "
    "image exactly - composition, shot size, character positions and facing, held props "
    "and lighting all start from it; describe only the action and camera movement that "
    "happen after this frame.\"，"
    "资产图从 <Picture 2> 开始按 asset_refs 顺序编号；\n"
    "   - 非连续段：资产图从 <Picture 1> 开始按 asset_refs 顺序编号；\n"
    "   - 正文出现的 <Picture N> 编号集合必须恰为 {1..实际张数}，多号/缺号/跳号都不允许。\n"
    "7. asset_refs 按重要性排序（在场主要角色 > 场景 > 道具）且不超过 9 条；"
    "每个在场主要角色与其所在场景的场景资产都必须出现在 asset_refs 中，"
    "否则人物与场景会跨段漂移。\n"
    "8. 切点设计（让段间切换读作有意图的剪辑而非跳切）：\n"
    "   - 景别交替：相邻两段的第一个镜头景别/机位必须错开——前段以近景收尾，"
    "本段就用中景或全景开场，反之亦然；开场段先以较全景别建立空间关系，"
    "收尾段把镜头推向视觉高潮或悬念定格，不收在平淡静态；\n"
    "   - 状态衔接：每段结尾画面是一个明确的 end state（人物姿态/位置/持物），"
    "连续段第 0 帧从该状态出发只推进动作；detailed_description 的第一个镜头块"
    "开头必须显式呼应该状态（如 picking up exactly where the previous shot ended）；\n"
    "   - 固定陈设一致：同场景内灯具/门窗/货架/柜台等大件陈设的位置与数量跨段不得"
    "增删、移位或消失。"
)

# TASK-030 双轨剧作法（豆包短剧方法论内化）：ScriptAgent 按
# params.dramatic_tone 选择其中一块注入 system。
# TASK-048 提质：hook 轨按 dj-short-drama 方法论 + 行业共识重写
# （黄金开场模板化/情绪密度/爽点前置/口语台词收紧/结尾卡点）。
_SCRIPT_TONE_LAWS = {
    DramaticTone.HOOK.value: (
        "剧作基调：短剧钩子驱动（都市逆袭/掉马甲/爽剧类适用）。在上述硬规则之外追加：\n"
        "H1. 黄金开局：第一场的前 3 个节拍内必须抛出核心矛盾、强悬念或主角的极端处境，"
        "禁止用环境描写/日常寒暄开场。优先套用可拍的开场模板：直接打脸（当众受辱→"
        "隐忍蓄力）、身份反转（被轻视→真实身份立住）、极端困境（绝境/重生起始）、"
        "倒叙悬念（高冲击结果开场再回溯）；开场出场人物 ≤3 人，信息不过载。\n"
        "H2. 高频反转与情绪密度：每场至少一次反转或关键信息增量；只有铺垫没有冲突"
        "推进的节拍一律删除，不留「平静过场」；铺垫类节拍不超过全片的 20%，"
        "观众随时退出都要带着未解的问题。\n"
        "H3. 压迫要具体、释放要痛快：爽点的本质是压抑→释放，前期羞辱/委屈要落在"
        "具体恶劣行径上（可拍摄的动作），第一个爽点不晚于全片 40% 处出现；"
        "主角的反击要有明确的压倒性优势感，围观者的震惊反应是爽感放大器（要写）。\n"
        "H4. 反派惩罚铁律：反派/对立面必须在片内得到实质惩罚或明确失败"
        "（写进具体 action 节拍），禁止「跨集再收拾」式悬置；"
        "反派行为要有合理性动机（在 characters[].profile 里写清他为什么这样做），"
        "避免脸谱化。\n"
        "H5. 台词千人千面且口语化：台词匹配角色身份、教育背景与社会地位；"
        "关键反击台词说「最解气的」而不是「该说的」；单句台词目标 8-20 字，"
        "硬上限 25 字；禁「因为…所以…」解释腔、禁总结陈词式台词；"
        "每个主要角色在 characters[].profile 里写一句口头禅或语言习惯，"
        "让台词不看名字也能认出是谁说的。\n"
        "H6. 结尾卡点：全片最后一个节拍必须落在情绪最高点或悬念定格"
        "（用 transition 节拍写明「定格在…」），禁止平淡收尾与总结式大团圆画面——"
        "结尾决定观众是否看完、是否转发。\n"
    ),
    DramaticTone.THREE_ACT.value: (
        "剧作基调：微电影三幕式（治愈/亲情/社会议题/独立短片适用）。在上述硬规则之外追加：\n"
        "T1. 单一核心事件：全片只承载一个核心事件/一次情绪转变/一个价值追问，"
        "事件可以极小（一次告别、一封未寄出的信），但主角必须因它发生真实变化。\n"
        "T2. 三幕配比：铺陈约 25%（建立处境与情感缺口）→ 事件与冲突约 50%"
        "（中段设一次翻转或撬动情绪的契机）→ 高潮与落点约 25%（内在选择与情感闭环），"
        "按各场 est_seconds 的分配体现。\n"
        "T3. 允许留白：允许少量纯画面节拍（静态凝视/无对白空镜/环境音），"
        "这是情绪沉淀的必需成本；但每场仍须有情绪推进，留白≠没有暗流。\n"
        "T4. 对立面可意象化：不必安排人格化反派，对立面可以是沉默/距离/时间流逝/"
        "过去的自己，用具体物件承载（旧照片、空座位、褪色门牌），"
        "characters 里可只写主角与关键他者。\n"
        "T5. 台词克制：接近生活对白，允许沉默、答非所问、一句话说一半；"
        "禁总结陈词式台词（如「我终于明白了」），结论留给观众。\n"
    ),
}
# 两轨通用写作纪律（豆包方法论：情绪外化 + 客观可拍）
_SCRIPT_COMMON_LAW = (
    "通用写作纪律（两轨都适用）：\n"
    "G1. 情绪靠动作外化：action 节拍禁写「他很悲伤/非常愤怒」这类抽象情绪词，"
    "改写具体身体细节（低头、肩膀微微颤抖、攥紧衣角、眼眶泛红、双拳紧握）。\n"
    "G2. 动作客观可拍：action 只写镜头拍得到的事实，不写心理活动与评价。"
)

# TASK-047 连载模式追加法则：run_episode_script 注入（在基调法则之后）。
# 与单片 hook 轨的关键差异：X3 放开「跨集悬置」，X1/X2 强制钩子落地。
_SERIES_LAWS = (
    "连载模式追加法则（本集是多集短剧中的一集）：\n"
    "X1. 承接上集：开场第一个节拍必须直接回应上一集结尾的卡点——先给半拍进展，"
    "再立刻制造新的张力；禁止跳过悬念装作没发生（观众对「被骗」零容忍）。\n"
    "X2. 落实大纲：本集第一个节拍要拍出「开场钩子」，最后一个节拍必须停在"
    "「集尾卡点」（transition 节拍写明定格画面）；大纲给的爽点必须在片内"
    "可感知地释放。梗概是骨架，节拍是血肉：允许在梗概框架内做更细的戏剧化"
    "展开，不得偏移主线、不得提前抖完后面集数的秘密。\n"
    "X3. 跨集悬置许可：主线大反派的终极惩罚可以留到后面的集数（此条优先于"
    "基调法则中的对应限制），但本集内部必须有至少一次「小对抗释放」——"
    "怼回一句、赢回一局、抢回关键物，观众每一集都要吃到糖。\n"
    "X4. 人物一致性：人物名逐字使用全剧人物表的登记名，严禁另起叫法或用简称/"
    "本名混用（登记「苏母（王秀兰）」就全篇只用它）；只使用人物表中的角色及其"
    "既定身份/称谓/语言习惯；新出场角色必须是梗概 new_characters 登记过的，"
    "profile 写法与人物表一致。\n"
    "X5. 信息配给：上一集的悬念要给新进展，但每集结尾观众必须带着一个未解的"
    "新问题离开；本集只揭示大纲分配给本集的信息量。\n"
)

# TASK-047 大纲生成方法论（内化 dj-short-drama：题材/爽点矩阵/钩子/节奏四阶段/反派分层）
_OUTLINE_LAWS = (
    "爆款方法论（逐条落实）：\n"
    "O1. 题材定位：从大众口味题材库定位——战神归来/赘婿逆袭/霸道总裁/甜宠/重生穿越/"
    "复仇打脸/家庭伦理/古装宫廷/悬疑探案/末日重生/励志逆袭/萌宝/都市情感，可叠加"
    "（如 战神+萌宝）。题材决定主爽点：战神类→身份碾压，甜宠类→情感爆发，"
    "悬疑类→悬念揭秘，逆袭类→逆袭翻盘+打脸复仇。\n"
    "O2. 爽点是燃料：爽点=压抑→释放，压抑越深释放越爽。五大爽点类型轮换："
    "身份碾压/打脸复仇/逆袭翻盘/情感爆发/悬念揭秘；同类型不得连续出现，"
    "全剧至少 3 种；强度递增（小爽→中爽→大爽→终极爽），全剧最强爽点放在决战段。\n"
    "O3. 黄金开场：第 1 集开场钩子用高冲击模板（直接打脸/身份反转/极端困境/倒叙悬念），"
    "前 3 秒抛冲突或身份反差，绝对禁止平静铺陈。\n"
    "O4. 五类钩子轮换做集尾卡点：悬念钩（答案留给下一集）/反转钩（最后一刻颠覆预期）/"
    "情绪钩（情绪推到顶点切断）/信息钩（关键信息只说一半）/危机钩（突发威胁来不及反应）；"
    "连续 3 集不用同类型；钩子必须与主线相关，下一集必须回应。\n"
    "O5. 全剧节奏四阶段：起势段（前 15% 集数：快节奏建立冲突，小爽点尝甜头，"
    "埋至少 3 个未解悬念）→ 攀升段（15-45%：中爽点+小反派被击败+感情线推进）→ "
    "风暴段（45-80%：大爽点密集，主角跌入全剧最低点后绝地反弹，核心秘密揭露）→ "
    "决战段（最后 20%：终极对决+大爆点+收束）。climax_episode（大爆点）放决战段"
    "但不得是最后一集，最后一集留给情感收束。\n"
    "O6. 人物表收敛：具名角色 ≤10、主角组 ≤5；每个角色的 profile 必须写清："
    "公开身份与真实身份、核心动机、口头禅或语言特征、爽点功能（他/她承担什么情绪价值）。"
    "反派分层：小反派（前期炮灰）→中反派（中期对手）→大反派（终极 Boss）→"
    "隐藏反派（反转用），大反派的惩罚可跨集悬置。\n"
    "O7. 每集条目硬要求：synopsis 80-200 字写清冲突起落（谁、要什么、被什么阻、"
    "结果如何反转）；opening_hook/ending_hook 各写一句可直接拍摄的画面；"
    "highlight 写明爽点类型+一句话内容。\n"
)

# TASK-030 分镜细节规则（豆包分镜方法论内化）：StoryboardAgent 写
# shots[].action/description 时逐条满足。
STORYBOARD_CRAFT_RULES = (
    "镜头写作细节规则（shots[].description 与 action 必须逐条满足）：\n"
    "C1. POV 归属：第一人称主观镜头必须在 description 开头标注所属角色"
    "（如 \"Lin Wan POV:\"），POV 镜头内不得出现该角色自己的完整正脸；"
    "POV 与第三人称来回切换时，角色服装、发型、持物、伤痕、光影方向必须连续，"
    "禁止把 POV 写成自拍或过肩镜头。\n"
    "C2. 禁群体量词：同一镜头出现多个角色时必须逐个点名"
    "（如 the vendor, the boy and the old ferryman ），"
    "严禁 the crowd / the three men / several people 等群体量词概括"
    "——AI 视频会把群像糊成一个人。\n"
    "C3. 方向性物体六要素：画面涉及手机/平板/镜子/书本纸张/车门/武器/"
    "带把手的杯子等有明确朝向的物体时，action 必须写清六要素："
    "人物朝向、视线方向、物体朝向（哪一面朝谁）、手部如何接触、"
    "观众能看到物体的哪一面、物体与人物的距离；"
    "不必展示屏幕/纸面内容，优先保证方向与视线正确。\n"
    "C4. 情绪外化：action 禁写 sad / angry / nervous 等抽象情绪词，"
    "改用具体身体细节（head lowered, fists clenched, shoulders trembling, "
    "eyes brimming with tears）。\n"
    "C5. 光影意图：段内有情绪转折或时间变化时，在对应镜头的 action 里写明光效"
    "（夕阳最后一道侧光扫过、灯熄灭前后、逆光剪影、色温由冷转暖），供视频生成参考。\n"
    "C6. 位置状态显式化：凡人物与载具/家具/器械有位置绑定（车内座位、床沿、"
    "柜台后、驾驶位等），action 与 keyframe_description 必须逐人显式写出位置状态——"
    "具体座位或站位（driver's seat / rear seat / behind the counter）、身体朝向、"
    "控制关系（hands on the steering wheel / gripping the handlebar）；"
    "机位短语（from the passenger side）不得与座位短语混写在一处，避免错误绑定；"
    "subject_placements 逐人给出的开场位置必须与 keyframe_description、"
    "首镜 action 开头三处一致。同时逐人标注 in_frame（本段第 0 帧是否可见）："
    "只有开场帧确实出现的人才填 true，画面外/仅远处剪影的人填 false——"
    "否则关键帧会把不该出现的人画进画面。\n"
    "C7. 关键帧纯静态：keyframe_description 是第 0 帧生图依据，"
    "禁止台词块、says/speaks/lips move 等口型与动态过程词——"
    "静态帧会把台词渲染成画面文字（已实测）。\n"
    "C8. 关键帧人物必须全部具名（TASK-045）：keyframe_description 里可见的人物"
    "只能是 subject_placements 中 in_frame=true 的那些角色资产，**逐人写资产名**；"
    "可见人数必须与 in_frame=true 的人数一致。严禁出现没有资产的无名人物——"
    "two girls / a third boy / a crowd / bystanders 这类写法会让图生图模型"
    "凭空造人，或把参考卡里的角色复印成好几个（均已实测）。确实需要路人氛围时，"
    "写成背景且不具体（unrelated pedestrians far in the background, faces not visible），"
    "并保证近景只有具名角色；否则本段会被判校验失败。"
)

# TASK-030 角色卡 B 方案（豆包资产卡口径：左 1/3 正脸特写 + 右 2/3 三视图）。
# 仅作为角色卡 A/B 实验的 B 模板备用；默认口径是**单图正面全身定妆照**
# （见 run_assets 内「主设定」规则，TASK-043），切换需用户拍板后再接参数。
CHARACTER_CARD_B_PROMPT = (
    "character reference sheet: left one-third of the frame is a large ultra-clear "
    "front-facing facial close-up, eyes looking straight into the camera, symmetric "
    "features, neutral expression; right two-thirds is a full-body three-view row "
    "(front, 90-degree side, back) of the exact same character, head-to-toe complete, "
    "no cropping; pure white background; all views share identical face, hairstyle, "
    "body proportions and outfit"
)





class AssetDraft(BaseModel):
    kind: AssetKind
    name: str = Field(min_length=1)
    description: str = ""
    visual_anchor: str = ""
    image_plan: list[dict] = Field(default_factory=list)


class AssetExtraction(BaseModel):
    assets: list[AssetDraft] = Field(min_length=1)


_RELAY_ANCHOR_EN = (
    "<Picture 1> is the exact opening frame of this video: frame 0 must match this "
    "image exactly - composition, shot size, character positions and facing, held "
    "props and lighting all start from it; describe only the action and camera "
    "movement that happen after this frame."
)


def _normalize_segment_keys(content: StoryboardContent) -> None:
    """确定性规范化：index 重排为场景内序号（1 起），segment_key = S{scene}G{index}。

    校验器要求 key 的 G 编号与 index 一致；LLM 常把 index 写成全局流水或
    key 写成与 index 不符，这里按 (scene_id, index) 排序后统一重写，
    并同步修正 continuity 引用。
    """
    ordered = sorted(content.segments, key=lambda s: (s.scene_id, s.index))
    per_scene: dict[str, int] = {}
    mapping: dict[str, str] = {}
    for seg in ordered:
        per_scene[seg.scene_id] = per_scene.get(seg.scene_id, 0) + 1
        idx = per_scene[seg.scene_id]
        scene_num = int(seg.scene_id[1:])
        new_key = f"S{scene_num:02d}G{idx:02d}"
        if seg.segment_key != new_key:
            mapping[seg.segment_key] = new_key
            seg.segment_key = new_key
        if seg.index != idx:
            seg.index = idx
    for seg in content.segments:
        prev = seg.continuity.with_prev_segment_key
        if prev in mapping:
            seg.continuity.with_prev_segment_key = mapping[prev]


def _closest_script_line(text: str, lines: list[str]) -> str | None:
    import difflib

    if not lines:
        return None
    best = max(lines, key=lambda sl: difflib.SequenceMatcher(None, text, sl).ratio())
    ratio = difflib.SequenceMatcher(None, text, best).ratio()
    return best if ratio >= 0.4 else None


_H3_SECTION_MARKERS = (
    "subject_definitions:",
    "summary:",
    "retention_analysis:",
    "detailed_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)


def _normalize_sections(content: StoryboardContent) -> None:
    """确定性章节规范化：把六段标记规范成"各占一行、官方顺序、去重"。

    模型常把整条提示词挤成一行（字段不在行首）或乱序输出；校验器要求字段
    在行首且按官方顺序出现。这里只重排/换行，不改写任何正文。
    """
    import re

    marker_re = re.compile(
        r"(?<![A-Za-z_])(" + "|".join(re.escape(m) for m in _H3_SECTION_MARKERS) + r")"
    )
    for segment in content.segments:
        text = segment.h3_prompt.text
        matches = list(marker_re.finditer(text))
        if len(matches) < 2:
            continue
        sections: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            marker = match.group(1)
            if marker not in sections:
                sections[marker] = text[match.end(): end].strip()
        ordered = [m for m in _H3_SECTION_MARKERS if m in sections]
        at_line_start = all(
            match.start() == 0 or text[match.start() - 1] == "\n"
            for match in matches
        )
        if at_line_start and [m.group(1) for m in matches] == ordered:
            continue
        parts = [f"{marker}\n{sections[marker]}".rstrip() for marker in ordered]
        segment.h3_prompt = segment.h3_prompt.model_copy(
            update={"text": "\n".join(parts)}
        )


def _upgrade_base_to_reference(segment, scene_summary: str) -> None:
    """base(三段)写法 → reference(六段)：确定性改写，正文保留为 detailed_description。"""
    import re as _re

    text = segment.h3_prompt.text
    if "detailed_description:" in text or "integrated_multimodal_description:" not in text:
        return
    match = _re.search(
        r"(?m)^integrated_multimodal_description:\s*(.*?)(?=\n[ A-Za-z_]+:|$)",
        text, flags=_re.S,
    )
    body = (match.group(1).strip() if match else "")
    text = text.replace(match.group(0), "", 1) if match else text
    summary = "summary:" + "\n[reference generation] " + scene_summary
    ret_lines = []
    pic = 2 if segment.continuity.enabled else 1
    if segment.continuity.enabled:
        ret_lines.append(
            "<Picture 1> (appears in [Shot 1]): fully_preserved - "
            "opening frame from the previous segment."
        )
    for _ref in segment.asset_refs:
        ret_lines.append(
            f"<Picture {pic}> (appears in [Shot 1]): "
            "fully_preserved - identity and key features."
        )
        pic += 1
    retention = (
        "retention_analysis:\n" + "\n".join(ret_lines)
    )
    detailed = "detailed_description:" + "\n" + body
    insertion = summary + "\n" + retention + "\n" + detailed + "\n"
    if "overall_soundscape:" in text:
        text = text.replace("overall_soundscape:", insertion + "overall_soundscape:", 1)
    else:
        text = text + "\n" + insertion
    segment.h3_prompt = segment.h3_prompt.model_copy(update={"text": text})


def _autorepair_scene(content: StoryboardContent, scene_script, assets_by_id) -> None:
    """确定性自动修复：LLM 高频违规在 Validate 前无副作用的场景下静默修复。

    1) 台词被改写/缩写：dialogue_refs 与 <d> 块模糊匹配回剧本逐字原文；
    2) subject_definitions 缺失：从 asset_refs + 资产数据确定性构建。
    """
    import re

    script_lines = [
        d.line.strip() for scene in scene_script.scenes for d in scene.dialogues
    ]

    def closest(text: str) -> str | None:
        return _closest_script_line(text.strip(), script_lines)

    # 0) 章节顺序规范化（模型偶尔把六段写乱序/挤成一行，校验器会判"顺序不符合"）
    _normalize_sections(content)
    # 0.5) 切点规范化：首镜归零 + 均布（模型常把切点写成绝对时间或段时长）
    for segment in content.segments:
        shot_count = len(segment.shots)
        if shot_count == 0:
            continue
        step = segment.duration_sec / shot_count
        for position, shot in enumerate(segment.shots, start=1):
            canonical = round((position - 1) * step, 1)
            if abs(shot.cutpoint_sec - canonical) > 0.05:
                shot.cutpoint_sec = canonical

    # 1) dialogue_refs 逐字还原
    for segment in content.segments:
        for shot in segment.shots:
            for d in shot.dialogue_refs:
                m = closest(d.line)
                if m and d.line != m:
                    d.line = m

    # 1.2) 台词装不下 → 自动加长本段：中文舒适语速约 4 字/秒 + 1.5s 起止余量，
    # 上限 12s（H3 支持 4-15s；台词完整性优先于时长收紧）
    for segment in content.segments:
        spoken = sum(
            len(d.line.strip()) for shot in segment.shots for d in shot.dialogue_refs
        )
        needed = -(-spoken // 4) + 1.5  # ceil(spoken/4)+1.5s
        new_duration = min(15, max(segment.duration_sec, round(needed)))
        if new_duration != segment.duration_sec:
            segment.duration_sec = new_duration
            step = new_duration / max(1, len(segment.shots))
            for position, shot in enumerate(segment.shots, start=1):
                shot.cutpoint_sec = round((position - 1) * step, 1)

    # 1.5) 官方 dialect：[Shot 1] 不带时间戳（模型常误加 At MM:SS.mmm）
    for segment in content.segments:
        text = segment.h3_prompt.text
        fixed = re.sub(
            r"(\[Shot 1\])\s*At\s+\d{2}:\d{2}\.\d{3},?\s*", r"\1 ", text
        )
        if fixed != text:
            segment.h3_prompt = segment.h3_prompt.model_copy(update={"text": fixed})

    # 1.8) 在场角色对账自愈（TASK-047 实测：S02G01/G03 两次校验失败的根因）：
    #      placement 名 → 角色资产精确名（别名等价，如「苏母」→「苏母（王秀兰）」）；
    #      asset_refs 缺在场角色卡 → 自动补（六段 <Picture N> 编号随后统一编译）；
    #      匹配不到任何角色资产的 placement 是模型幻觉 → 移除（否则校验必败）
    char_assets = [a for a in assets_by_id.values() if a.kind is AssetKind.CHARACTER]
    for segment in content.segments:
        ref_ids = {ref.asset_id for ref in segment.asset_refs}
        kept_placements = []
        for placement in segment.subject_placements:
            match = next(
                (a for a in char_assets if names_match(a.name, placement.name)),
                None,
            )
            if match is None:
                continue
            placement.name = match.name
            if match.asset_id not in ref_ids:
                segment.asset_refs.append(
                    AssetRef(
                        asset_id=match.asset_id,
                        usage_note="auto: on-screen character (subject placement)",
                    )
                )
                ref_ids.add(match.asset_id)
            kept_placements.append(placement)
        segment.subject_placements = kept_placements

    # 2) <d> 块内容与 refs 同步还原（保留语言标签）
    #    护栏：匹配区间内若含字段标题，说明模型漏写 </d>、正则会跨段吞并，跳过不动
    _FIELD_MARKERS = (
        "subject_definitions:",
        "summary:",
        "retention_analysis:",
        "detailed_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    )
    for segment in content.segments:
        text = segment.h3_prompt.text

        def _fix(m: re.Match[str]) -> str:
            inner = m.group(2)
            if any(marker in inner for marker in _FIELD_MARKERS):
                return m.group(0)
            stripped = inner.strip()
            best = closest(stripped)
            if best and best != stripped:
                return f"<d>{m.group(1) or ''} {best}</d>"
            return m.group(0)

        new_text = re.sub(r"<d>(\[[^\]]+\])?(.*?)</d>", _fix, text, flags=re.S)
        if new_text != text:
            segment.h3_prompt = segment.h3_prompt.model_copy(update={"text": new_text})

    # 3) subject_definitions 缺失 → 从资产数据构建
    scene_summary = next(
        (sc.summary for sc in scene_script.scenes if sc.summary), "The scene unfolds as scripted."
    )
    for segment in content.segments:
        _upgrade_base_to_reference(segment, scene_summary)
        if "subject_definitions:" in segment.h3_prompt.text:
            continue
        lines: list[str] = []
        pic = 2 if segment.continuity.enabled else 1
        if segment.continuity.enabled:
            lines.append(
                "<Picture 1> is the relayed opening frame from the previous shot: "
                "frame 0 of this video must match this image exactly."
            )
        for ref in segment.asset_refs:
            asset = assets_by_id.get(ref.asset_id)
            name = asset.name if asset else ref.asset_id
            anchor = (asset.visual_anchor if asset else "").strip()
            desc = f", with {anchor}" if anchor else ""
            lines.append(f"<Picture {pic}> is {name}{desc}.")
            pic += 1
        block = "subject_definitions:\n" + "\n".join(lines) + "\n"
        text = segment.h3_prompt.text
        segment.h3_prompt = segment.h3_prompt.model_copy(
            update={"text": block + text}
        )


def _ensure_relay_anchor(content: StoryboardContent) -> None:
    """确定性兜底：连续段的 Picture 1 必须带强锚定句，LLM 不写就由代码注入。"""
    for segment in content.segments:
        if not segment.continuity.enabled or "frame 0" in segment.h3_prompt.text:
            continue
        marker = "subject_definitions:"
        idx = segment.h3_prompt.text.find(marker)
        if idx == -1:
            continue
        insert_at = idx + len(marker)
        text = segment.h3_prompt.text
        text = text[:insert_at] + "\n" + _RELAY_ANCHOR_EN + text[insert_at:]
        segment.h3_prompt = segment.h3_prompt.model_copy(update={"text": text})


@dataclass
class AgentResult:
    data: object
    tokens: int
    attempts: int


class Agents:
    def __init__(self, llm) -> None:
        self._llm = llm

    # -- ScriptAgent --------------------------------------------------------

    @staticmethod
    def _script_system_core() -> str:
        """剧本 system 的公共骨架：角色设定 + JSON 结构 + 硬规则（单片/连载共用）。"""
        return (
            "你是短剧编剧，按可拍摄文学剧本的口径创作。只输出一个 JSON 对象，结构："
            '{"logline": str, "scenes": [{"id": "S1", "title": str, "summary": str, '
            '"beats": [{"type": str, "text": str, "speaker": str, "tone": str}], '
            '"est_seconds": int}], '
            '"characters": [{"name": str, "profile": str}], "props": [str], "warnings": []}。'
            "（scenes 不需要输出 dialogues 字段，台词由 dialogue 节拍承载）\n"
            "硬规则：\n"
            "1. scenes[].title 是场景头三要素：「内/外 · 地点 · 日/夜」"
            "（如「外 · 霓虹夜市街头 · 夜」）。\n"
            "2. beats 是这场戏按时间排序的节拍流，必须覆盖每一个视觉事件，"
            "画面不能只有说话：\n"
            "   - action（画面动作，最重要的节拍）：一段一个可拍摄的动作/画面事件，"
            "写清谁、在哪、做什么、怎么做，具体到道具、姿态、方位、环境与光"
            "（如「他弯腰搬电动车，手掌被脚垫上的金属吊坠划出一道细口」）；"
            "每条 20-60 字，一场戏通常 4-12 条；\n"
            "   - dialogue（台词）：text 只能是角色嘴里说出来的话，"
            "严禁动作/神态/心理描写与括号舞台指示；speaker=说话人名；"
            "tone=语气（如 声音很低/不耐烦/大舌头），无则留空；\n"
            "   - sfx（音效）：如 引擎轰鸣远去/刺耳的刹车声/手机震动；\n"
            "   - on_screen_text（画面文字）：手机屏幕弹出的文字、字幕卡等；\n"
            "   - transition（转场）：如 警灯红蓝交替，画面渐暗；"
            "一般每场最多一条、放在结尾。\n"
            "3. 动作与台词交替：先有动作/反应再有台词，禁止把一场戏写成连篇对话。\n"
            "4. summary 是这场戏的一段话梗概（资产与分镜都会读它），"
            "要点名环境与在场人物。\n"
            "5. 关键视觉道具（会被特写或影响剧情的物件）必须写进 action 节拍"
            "并登记进 props。\n"
            "6. id 从 S1 连续编号；est_seconds 按台词字数（约 4 字/秒）"
            "加动作节拍数乘 3 秒估算。\n"
            "7. 台词总量预算：全部场景台词的字数总和 ≤ 目标时长×3（按 4 字/秒"
            "口播、留出动作余量）；单句台词目标 8-20 字、硬上限 25 字。超出就精简"
            "——合并功能重复的对话、删寒暄与不影响理解的句子，保留承担剧情功能"
            "与人物塑造的台词；删台词不删戏，action 节拍一个不能少。\n"
            "8. 人物与道具唯一命名（硬规则）：characters[] 是全剧本唯一人物登记表——"
            "dialogue 的 speaker 必须逐字等于 characters[].name，严禁别名/简称/"
            "称呼混用（登记「苏母（王秀兰）」就全篇只用这个写法）；action 与 "
            "summary 提及人物时同样用登记名；同一人物只登记一条，禁止同一人两个"
            "条目；props 同理——同一道具只登记一次并用登记名贯穿全篇，"
            "不得出现「玉佩」「破旧玉佩」混用的情形。\n"
        )

    @staticmethod
    def _tone_laws_for(params: dict) -> str:
        return _SCRIPT_TONE_LAWS.get(
            str(params.get("dramatic_tone") or DramaticTone.HOOK.value),
            _SCRIPT_TONE_LAWS[DramaticTone.HOOK.value],
        )

    def run_script(self, idea: str, params: dict) -> ScriptContent:
        system = (
            self._script_system_core()
            + self._tone_laws_for(params)
            + "\n"
            + _SCRIPT_COMMON_LAW
            + "\n不要输出 JSON 以外的任何文字。"
        )
        user = (
            f"想法：{idea}\n"
            f"参数：题材={params.get('genre', '')}；风格={params.get('style', '')}；"
            f"剧作基调={params.get('dramatic_tone', 'hook')}；"
            f"目标总时长={params.get('target_duration_sec', 60)} 秒；"
            f"场景数≈{params.get('scene_count', 3)}。"
        )
        result = self._llm.chat_json(
            system=system, user=user, schema=ScriptContent, temperature=0.8
        )
        content: ScriptContent = result.data
        target = int(params.get("target_duration_sec") or 0)
        total = content.total_est_seconds
        if target and abs(total - target) / target > 0.30:
            content.warnings.append(
                f"估算总时长 {total}s 与目标 {target}s 偏差超过 30%"
            )
        self._normalize_beats(content)
        # 人物/道具唯一命名归一（确定性）：同一人物/道具不得出现多个命名
        content.warnings.extend(enforce_name_registry(content))
        # 台词口播总预算（确定性护栏）：分镜要求台词逐字分配，超预算会导致
        # 分镜校验死锁（逐字对账 vs 口播预算二选一都违反），必须在剧本层收敛
        if target:
            spoken = sum(
                len(d.line.strip()) for scene in content.scenes for d in scene.dialogues
            )
            if spoken > target * 3:
                content.warnings.append(
                    f"台词总字数 {spoken} 超过目标时长 {target}s 的口播预算"
                    f"（≈{target * 3} 字），分镜将无法逐字容纳，请精简台词"
                )
        self._strip_script_directions(content)
        return content

    # -- SeriesAgent（TASK-047）：系列大纲 + 逐集剧本 ------------------------

    _OUTLINE_BATCH = 12  # 单次 LLM 调用最大出题集数（JSON 体量与质量平衡）

    def run_series_outline(self, idea: str, params: dict) -> SeriesOutline:
        """想法 → 全剧大纲（剧名/logline/人物表/分集梗概/爆点）。

        集数 >_OUTLINE_BATCH 时分批生成：首批定剧名/人物表/基调，后续批次
        携带既定信息与前情梗概续写，最后确定性合并重编号。
        """
        system = (
            "你是爆款微短剧总编剧，观众是刷短视频的大众：注意力以秒计，"
            "情绪浓度优先于逻辑复杂度。只输出一个 JSON 对象，结构："
            '{"title": str, "logline": str, "genre_tags": [str], '
            '"characters": [{"name": str, "profile": str}], '
            '"episodes": [{"episode_no": int, "title": str, "synopsis": str, '
            '"opening_hook": str, "ending_hook": str, "highlight": str, '
            '"new_characters": [str]}], "climax_episode": int, "warnings": []}。\n'
            + _OUTLINE_LAWS
            + "\n不要输出 JSON 以外的任何文字。"
        )
        total = int(params.get("episode_count") or 12)
        base_user = (
            f"想法：{idea}\n"
            f"参数：题材={params.get('genre', '') or '按大众口味自选强类型题材'}；"
            f"风格={params.get('style', '')}；"
            f"剧作基调={params.get('dramatic_tone', 'hook')}；"
            f"总集数={total}；单集目标时长={params.get('per_episode_sec', 90)} 秒"
            f"（梗概的信息量要与此匹配）；单集场景数≈{params.get('scene_count', 4)}。"
        )
        outline: SeriesOutline | None = None
        episodes: list[EpisodeOutline] = []
        for start in range(1, total + 1, self._OUTLINE_BATCH):
            end = min(start + self._OUTLINE_BATCH - 1, total)
            user = base_user + f"\n本次只生成第 {start}-{end} 集。"
            if outline is not None:
                cast = "；".join(f"{c.name}（{c.profile}）" for c in outline.characters)
                prev = episodes[-1]
                user += (
                    f"\n既定信息（必须沿用，不得更改）：剧名《{outline.title}》；"
                    f"主线：{outline.logline}；人物表：{cast}。\n"
                    f"上一集（第 {prev.episode_no} 集「{prev.title}」）梗概：{prev.synopsis}"
                )
            result = self._llm.chat_json(
                system=system, user=user, schema=SeriesOutline, temperature=0.8
            )
            batch: SeriesOutline = result.data
            if outline is None:
                outline = batch
            episodes.extend(batch.episodes)
        assert outline is not None
        # 确定性规范化：截断超量集、按顺序重排集号（LLM 偶尔跳号/重复）
        episodes = episodes[:total]
        for index, ep in enumerate(episodes, start=1):
            ep.episode_no = index
        return outline.model_copy(update={"episodes": episodes})

    def run_episode_script(
        self,
        outline: SeriesOutline,
        episode: EpisodeOutline,
        prev_ending: str,
        params: dict,
    ) -> ScriptContent:
        """系列模式逐集剧本：全剧人物表 + 本集梗概/钩子/卡点 + 连载法则。

        prev_ending 是上一集大纲里的集尾卡点（空串=第 1 集）。
        """
        system = (
            self._script_system_core()
            + self._tone_laws_for(params)
            + "\n"
            + _SERIES_LAWS
            + "\n"
            + _SCRIPT_COMMON_LAW
            + "\n不要输出 JSON 以外的任何文字。"
        )
        number = episode.episode_no
        total = len(outline.episodes)
        cast = "\n".join(
            f"- {c.name}：{c.profile}" for c in outline.characters
        )
        user = (
            f"全剧：剧名《{outline.title}》；主线：{outline.logline}；"
            f"题材：{'、'.join(outline.genre_tags)}；共 {total} 集，本集是第 {number} 集。\n"
            f"全剧人物表（台词与行为必须与既定身份/语言习惯一致）：\n{cast}\n"
        )
        if prev_ending.strip():
            user += f"上一集（第 {number - 1} 集）结尾卡点：{prev_ending}\n"
        else:
            user += "本集是第 1 集：按开场钩子直接开局，不做任何前情铺垫。\n"
        user += (
            f"本集任务：\n"
            f"- 集标题：{episode.title}\n"
            f"- 开场钩子（第一个节拍要拍出来）：{episode.opening_hook}\n"
            f"- 本集梗概（骨架，可戏剧化展开但不得偏移）：{episode.synopsis}\n"
            f"- 本集爽点（必须可感知地释放）：{episode.highlight}\n"
            f"- 集尾卡点（最后一个节拍停在这里）：\n"
            f"{episode.ending_hook.strip() or '大结局收束：回收全部主线悬念，情感落点给足，不需要再留钩子'}\n"
        )
        nxt = next(
            (e for e in outline.episodes if e.episode_no == number + 1), None
        )
        if nxt is not None:
            user += f"- 下一集走向（仅供衔接，本集不得提前抖完）：{nxt.synopsis}\n"
        else:
            user += "- 本集是全剧最后一集：收束所有主线与感情线，给观众满足感。\n"
        target = int(params.get("per_episode_sec") or params.get("target_duration_sec") or 90)
        user += (
            f"参数：风格={params.get('style', '')}；"
            f"单集目标时长={target} 秒；场景数≈{params.get('scene_count', 4)}。"
        )
        result = self._llm.chat_json(
            system=system, user=user, schema=ScriptContent, temperature=0.8
        )
        content: ScriptContent = result.data
        total_est = content.total_est_seconds
        if target and abs(total_est - target) / target > 0.30:
            content.warnings.append(
                f"估算总时长 {total_est}s 与目标 {target}s 偏差超过 30%"
            )
        self._normalize_beats(content)
        # 人物/道具唯一命名归一（确定性）：先对齐大纲登记名，再合并剧本内重复合名
        content.warnings.extend(
            enforce_name_registry(content, cast=[c.name for c in outline.characters])
        )
        if target:
            spoken = sum(
                len(d.line.strip()) for scene in content.scenes for d in scene.dialogues
            )
            if spoken > target * 3:
                content.warnings.append(
                    f"台词总字数 {spoken} 超过目标时长 {target}s 的口播预算"
                    f"（≈{target * 3} 字），分镜将无法逐字容纳，请精简台词"
                )
        self._strip_script_directions(content)
        return content

    @staticmethod
    def _normalize_beats(content: ScriptContent) -> None:
        """节拍流后处理：dialogues 从 dialogue 节拍派生（括号语气归位不丢弃）；
        LLM 没写节拍时降级回填并附 warning，保证下游节拍认领链路不断。"""
        for scene in content.scenes:
            if scene.beats:
                derived: list[DialogueLine] = []
                for beat in scene.beats:
                    if beat.type is not BeatType.DIALOGUE:
                        continue
                    text, tone = split_tone_from_line(beat.text)
                    # tone 字段与括号可能写同一个语气，包含去重
                    beat_tone = beat.tone.strip()
                    if tone and tone not in beat_tone:
                        tone = " ".join(x for x in (beat_tone, tone) if x)
                    else:
                        tone = beat_tone
                    if not text:
                        content.warnings.append(
                            f"场景 {scene.id} 台词节拍「{beat.text}」是纯舞台指示，已略过"
                        )
                        continue
                    derived.append(
                        DialogueLine(
                            speaker=beat.speaker.strip() or "未署名",
                            line=text,
                            tone=tone,
                        )
                    )
                if derived:
                    scene.dialogues = derived
            elif scene.dialogues:
                scene.beats = [
                    Beat(
                        type=BeatType.DIALOGUE,
                        text=d.line,
                        speaker=d.speaker,
                        tone=d.tone,
                    )
                    for d in scene.dialogues
                ]
                content.warnings.append(
                    f"场景 {scene.id} 缺节拍流，已从台词回退"
                    "（缺画面动作描述，建议重新生成剧本）"
                )
            if not scene.beats and scene.summary:
                scene.beats = [Beat(type=BeatType.ACTION, text=scene.summary)]
                content.warnings.append(
                    f"场景 {scene.id} 无任何节拍，已从概要回退一条动作节拍"
                )

    @staticmethod
    def _strip_script_directions(content: ScriptContent) -> None:
        """确定性兜底：即使 LLM 违规写入括号舞台指示，也在入库前剥掉。"""
        for scene in content.scenes:
            kept = []
            for d in scene.dialogues:
                text, changed = strip_stage_directions(d.line)
                if changed:
                    if text:
                        content.warnings.append(
                            f"场景 {scene.id} 台词「{d.line}」含舞台指示，已剥离动作只留台词"
                        )
                    else:
                        content.warnings.append(
                            f"场景 {scene.id} 台词「{d.line}」是纯舞台指示，"
                            "已移除（动作写进场景概要）"
                        )
                if text:
                    kept.append(
                        type(d)(speaker=d.speaker, line=text, tone=d.tone)
                    )
            scene.dialogues = kept

    # -- AssetAgent ---------------------------------------------------------

    def run_assets(self, script: ScriptContent, style: str, ratio: str = "16:9") -> AssetExtraction:
        from server.app.h3_compiler import DEFAULT_STYLE_LINE

        # TASK-046：风格参数为空时必须落到默认风格线——否则卡片各画各的
        # （实测：空风格项目里角色卡跑成 3D 渲染感、场景卡是扁平插画，成片风格漂移）
        style = (style or "").strip() or DEFAULT_STYLE_LINE
        aspect = (ratio or "16:9").strip() or "16:9"
        portrait = aspect == "9:16"
        orientation = "竖版" if portrait else "横版"
        system = (
            "你是视觉设定师，按 MiniMax H3 官方《3d-animation-short-generator》"
            "资产卡规范出图：每个资产只出一张全信息设定卡（image_plan 恰好 1 项），"
            "所有细节进同一张图。只输出 JSON："
            "{assets: [{kind: 'character'|'scene'|'prop', "
            "name: str, description: str, visual_anchor: str, "
            "image_plan: [{view_label: str, image_prompt: str}]}]}。\n"
            f"- 画幅：所有卡片一律 {aspect}（{orientation}），"
            "提示词结尾标注该画幅，禁止写 16:9。\n"
            "- 角色：view_label=「主设定」，character reference card，"
            "单图只有一幅画面：正面平视**全身定妆照**（full-body portrait）——"
            "人物站直、正对镜头、双臂自然垂放，头到脚完整入画（不裁切头顶与鞋），"
            "整个人物占满画面高度并居中；脸五官清晰对称、表情自然、直视镜头；"
            "服装从头到脚完整可见（上衣/下装/鞋与配饰都要看清）；"
            "同一人物只有一个（严禁拼贴/多视图/三视图/分格/背景小人）；"
            "只画人物本身，不画道具/箱子/载具（道具另有独立白底卡，"
            "但角色随身佩戴的配饰如手表/挂坠/工牌照常画在身上）；"
            "干净浅背景、均匀柔光、高细节；"
            "严禁再拆分面部特写/半身像/侧面像单独出图。\n"
            "- 场景：view_label=「空镜」，environment reference card，"
            "开头第一句就是 a completely deserted and empty place，"
            "整张卡只有一幅画面（不是多视图卡、不是拼贴、不分格）："
            "一台相机、一个机位、一条连续画面；"
            "用广角/超广角镜头（约 16-24mm）从能看见场景全貌的机位拍摄——"
            "室内取角落或略高于视线的机位，街道/室外取道路一端或对面临街位置，"
            "让该空间的绝大部分一次性入画：四面墙、空间两端与全部主要地标同框，"
            "纵深与前后层次清楚（这是关键帧与视频摆放人物位置的唯一空间依据）；"
            "禁止分格/拼贴/多面板/多机位/平面图/线稿；"
            "同一组连续性地标（门/树/柜台/船等）在这幅画面里的位置、数量、大小清晰可辨（防跨段漂移）；"
            "可出现的人工设施（岗亭/岗哨/值班室/车辆驾驶位等）一律画成空置状态；"
            "标注关键光态（时段）与场景内重要道具；"
            "严禁出现任何与人相关的词（people/crowd/silhouette/mannequin/figure 等词"
            "在文生图模型里会反向引入人形，一律不写）。\n"
            "- 道具：view_label=「主设定」，白底道具卡：单个道具居中立于纯白背景上，"
            "棚拍产品图风格、柔光、崭新未使用；正文中严禁出现手/人/背景相关的词"
            "（否定句在文生图里会反向引入，改用 isolated/unworn 等正向描述）；"
            "一次只画一个道具——剧本里的不同小物件各自独立成卡"
            "（如 手机 与 房产证 分成两条资产），严禁把成对/多件物品拼进同一张卡。\n"
            "- kind 分类硬规则：只有「能被人物拿起来或放在桌面的小物件」才是 prop"
            "（碗、怀表、伞、信、手机等）；"
            "凡是环境空间（街道/巷子/江边/天空/城市外景/自然风光等不可被拿起的空间）"
            "一律 kind=scene。"
            "所有 image_prompt 严禁出现 diorama/miniature/scale model/maquette 等"
            "微缩模型词（它们会把街景和道具画成白底模型卡片）。\n"
            "visual_anchor 是身份锁（年龄段/身材/发型/服饰色/签名道具/不可改特征），"
            "image_prompt 必须英文、可直接文生图，把身份锁细节逐项写进去，含风格词与画质词。\n"
            "- anchor 卫生（TASK-032）：visual_anchor 会被原样编译进剧情镜头的"
            "视频与关键帧提示词，只写画面恒定的事实——"
            "角色锚点禁止「直视镜头/表情自然微笑看镜头」等摆拍短语与「白底/定妆/半身」"
            "等卡片取景词（姿势属于参考卡，泄漏进剧情会渲染成看镜头动作）；"
            "场景锚点禁止「空无一人/无人/设施空置」等空镜状态短语"
            "（那些只属于空镜 image_prompt，嵌进有人镜头会图文打架，模型会复制或清空人物），"
            "并且禁止「广角/超广角/机位/视角/构图/全景/多视角」等取景短语"
            "（取景属于空镜卡本身，写进锚点会把后续每个镜头的景别锁成广角远景）"
            "——场景锚点只写空间事实：地标、布局、材质、光态固有属性。"
            "不要输出 JSON 以外的文字。"
        )
        user = {
            "style": style,
            "aspect": aspect,
            "logline": script.logline,
            "scenes": [s.model_dump() for s in script.scenes],
            "characters": [c.model_dump() for c in script.characters],
            "props": script.props,
        }
        result = self._llm.chat_json(
            system=system,
            user=json.dumps(user, ensure_ascii=False),
            schema=AssetExtraction,
            temperature=0.6,
        )
        plan: AssetExtraction = result.data
        # 官方规范：一资产一卡。LLM 违规拆分（面部特写/半身等）时确定性裁掉，
        # 只保留身份锚最强的一张（优先「主设定」/「空镜」）——多余图从不进 H3 参考，
        # 只白耗生图费用。
        for draft in plan.assets:
            labels = [str(p.get("view_label", "")) for p in draft.image_plan]
            preferred = next(
                (i for i, lb in enumerate(labels) if "主设定" in lb or "空镜" in lb),
                None,
            )
            if len(draft.image_plan) > 1:
                draft.image_plan = [draft.image_plan[preferred if preferred is not None else 0]]
        # 护栏：角色缺主设定 → 确定性补一张（不再耗一次 LLM 调用）
        for draft in plan.assets:
            labels = [str(p.get("view_label", "")) for p in draft.image_plan]
            if draft.kind is AssetKind.CHARACTER and not any("主设定" in lb for lb in labels):
                draft.image_plan.append({
                    "view_label": "主设定",
                    "image_prompt": (
                        f"Character reference card of {draft.name}, single front-facing "
                        f"full-body portrait filling the frame, standing straight facing "
                        f"the camera, head to toe completely in frame, {draft.visual_anchor}, "
                        f"{style}, natural expression, looking at the camera, "
                        "even soft lighting, one person only, no collage, no multiple views, "
                        f"clean light background, {aspect}, high detail"
                    ),
                })
        return plan

    # -- StoryboardAgent ----------------------------------------------------

    def run_storyboard(
        self,
        script: ScriptContent,
        assets: list[Asset],
        params: dict,
    ) -> tuple[StoryboardContent, list[str]]:
        """分场景生成分镜（每场景一次 LLM 调用，防单卷输出超限截断）。

        节拍流的 action 段让输出膨胀约 3 倍，整卷单次调用会被 max_tokens
        截断（EP001 实测 25853 字符 json_invalid）；按场景切分后单次输出
        缩到 1/4。每场景确定性校验失败带反馈重试；键名/台词/切点由
        _normalize_segment_keys + _autorepair_scene 确定性修复；六段提示词
        由 h3_compiler 确定性编译。
        """
        # 存量剧本可能含括号舞台指示（会被读出声）：进入分镜前确定性清洗；
        # 手动编辑可能只写 beats 或只写 dialogues，先互推导对齐（警告忽略）
        script = script.model_copy(deep=True)
        self._normalize_beats(script)
        self._strip_script_directions(script)
        system = (
            "你是短剧分镜师与 H3 提示词工程师。只输出一个紧凑的 JSON（不要 markdown "
            "围栏、不要注释；每一段的结构字段都必须完整输出，"
            "即使措辞与其它段相似也不得省略任何字段）："
            '{"segments": [每个段的 JSON]}。每段结构：'
            '{"segment_key": "S01G01", "scene_id": "S1", "index": 1, '
            '"duration_sec": 4-15 整数, '
            '"shots": [{"shot_no": 1, "cutpoint_sec": 0.0, "camera": "官方运镜词", '
            '"beat_refs": [该镜认领的场景节拍序号（1 起，对应剧本 beats 顺序）], '
            '"description": "英文短语 ≤25 词（只写画面要点标签，完整描写写在 action）", '
            '"action": "英文动作时间线段落：本镜时间窗内 开始→中途→结束 的显式渐进，'
            "含构图与人物位置、环境与光、动作细节；≤8 秒的镜 100-180 词，"
            "9-15 秒的镜 180-280 词，禁止缩写成单句状态快照；"
            '台词的说话方式也要在这里铺垫", '
            '"dialogue_refs": [{"speaker": str, "line": 台词原文, '
            '"tone": "说话方式英文短语（如 in a low voice / impatiently），无则空串"}]}], '
            '"asset_refs": [{"asset_id": str, "usage_note": str}], '
            '"continuity": {"enabled": bool, "with_prev_segment_key": str}, '
            '"soundscape": "英文环境声：场景底噪+镜头内音效（剧本 sfx 节拍必须体现）", '
            '"music": "英文配乐意图（情绪/乐器/音量；无配乐写 N/A）", '
            '"keyframe_description": "英文静态画面描述（20-50 词）：本段第 0 帧（开场）的'
            '构图/景别、人物位置与姿态、持物、光效——作为该段开场锚点关键帧的生图依据，'
            '写到达成的稳定状态而非动作过程；纯静态，禁止台词块/口型/过程动作", '
            '"subject_placements": [{"name": "在场角色资产名（逐人一条，一个都不能少）", '
            '"placement": "英文开场空间状态短语：谁在哪个座位/站在哪里、身体朝向、'
            '与载具/家具的控制关系（如 in the driver\'s seat, hands on the steering wheel / '
            'sprawled across the rear seat）", '
            '"in_frame": "布尔：该角色在本段第 0 帧画面里是否可见。可见（含背身/侧身主体）'
            '填 true；本段在场但开场帧看不到（画面外/被遮挡/仅远处剪影）填 false——'
            '关键帧是构图确定的静帧，false 的角色不会进画面也不会进图生图参考图"}], '
            '"h3_prompt": {"text": "", "lang": "en"}}。\n'
            "每次调用只负责用户消息里给出的那一个场景（scenes 数组只有一个元素），"
            "输出的所有段都属于该场景。\n"
            "段 = 一次视频生成单元（4-15 秒），段内镜头切点用 cutpoint_sec 累计；"
            "切段策略按 payload 的 scene_target_sec（本场景目标秒数）与 "
            "scene_segment_target（本场景目标段数）：每段 duration_sec 取 10-15 秒"
            "（**尽可能长**，短于 10 秒只有在该场景剩余秒数不足 10 时才允许）；"
            "每段 shots 1-2 个（硬上限 2：一次生成内切点越多画面越易漂移；"
            "段越长镜越多，一次生成内身份与场景天然连续）；"
            "本场景全部段时长之和与 scene_target_sec 偏差 <15%。\n"
            "节拍认领（beat_refs，画面不丢拍的关键）：每个镜用 beat_refs 认领它负责"
            "呈现的场景节拍；场景中所有 action 与 on_screen_text 节拍必须被至少一个镜头"
            "认领，一个都不能丢；dialogue 节拍由 dialogue_refs 逐字承载；"
            "h3_prompt.text 恒写空串（六段提示词由确定性编译器从上述数据生成，"
            "不要自己写提示词正文）。\n"
            "asset_refs 的 asset_id 必须来自给你的资产清单；"
            "相邻段 continuity.enabled=true 且 with_prev_segment_key=前一段 key"
            "（若给出 previous_last_segment_key，本场景第一段也必须用它续接，"
            "保证整片连贯）。\n"
            + STORYBOARD_CRAFT_RULES
            + "\n"
            + H3_RULES_DIGEST
        )
        known_ids = {a.asset_id for a in assets}
        character_assets = {
            a.asset_id: a.name for a in assets if a.kind.value == "character"
        }
        target = int(params.get("target_duration_sec") or 0)
        est_total = sum(sc.est_seconds for sc in script.scenes) or 1
        ordered_scenes = sorted(script.scenes, key=lambda s: int(s.id[1:]))
        assets_payload = [
            {
                "asset_id": a.asset_id,
                "kind": a.kind.value,
                "name": a.name,
                "visual_anchor": a.visual_anchor,
            }
            for a in assets
        ]
        assets_by_id = {a.asset_id: a for a in assets}
        from server.app.h3_compiler import compile_h3_prompt

        style_line = str(params.get("style") or "").strip()
        all_segments: list = []
        prev_key = str(params.get("previous_last_segment_key") or "")
        for scene in ordered_scenes:
            scene_target = (
                max(6, round(target * scene.est_seconds / est_total)) if target else 0
            )
            scene_count = max(1, round(scene_target / 12)) if target else 0
            scene_script = script.model_copy(update={"scenes": [scene]})
            user_payload = {
                "params": {
                    **params,
                    "scene_target_sec": scene_target,
                    "scene_segment_target": scene_count,
                },
                "script": {
                    "logline": script.logline,
                    "scenes": [json.loads(scene.model_dump_json())],
                },
                "assets": assets_payload,
                "previous_last_segment_key": prev_key,
            }
            content: StoryboardContent | None = None
            feedback = ""
            for attempt in (1, 2):
                user = json.dumps(user_payload, ensure_ascii=False)
                if feedback:
                    user += (
                        "\n\n上一次输出的以下确定性校验未通过，"
                        "请修正后重新输出完整 JSON：\n" + feedback
                    )
                result = self._llm.chat_json(
                    system=system, user=user, schema=StoryboardContent, temperature=0.25
                )
                content = result.data
                for seg in content.segments:
                    seg.scene_id = scene.id  # 防场景号写错
                _normalize_segment_keys(content)
                _autorepair_scene(content, script, assets_by_id)
                # 台词超载段确定性二分（重编号 + continuity 引用修正）
                self._split_overloaded_segments(content)
                # 六段提示词由代码从结构化数据确定性编译（LLM 只贡献镜头/台词/声音
                # 数据），彻底消除格式合规类失败；风格行来自项目 style 参数
                for segment in content.segments:
                    compiled = compile_h3_prompt(
                        segment, assets_by_id, style_line=style_line
                    )
                    segment.h3_prompt = segment.h3_prompt.model_copy(
                        update={"text": compiled, "lang": PromptLang.EN}
                    )
                # 场景段数不硬卡：超载拆段兜底会合法增段，段数只作提示词引导；
                # 时长/台词预算/节拍认领仍是硬校验
                errors = self._validate_content(
                    content,
                    known_ids,
                    script=scene_script,
                    allowed_first_prev=prev_key,
                    character_assets=character_assets,
                )
                if not errors:
                    break
                if attempt == 2:
                    # 节拍认领兜底：重试仍漏拍的，确定性挂到本场景末镜并附
                    # 节拍原文（保画面不丢拍），重编译后复验一次
                    if self._repair_unclaimed_beats(content, scene, assets_by_id, style_line):
                        errors = self._validate_content(
                            content,
                            known_ids,
                            script=scene_script,
                            allowed_first_prev=prev_key,
                            character_assets=character_assets,
                        )
                    if errors:
                        import sys

                        print(
                            f"场景 {scene.id} 分镜确定性校验两次未通过，错误明细：\n- "
                            + "\n- ".join(errors),
                            file=sys.stderr,
                            flush=True,
                        )
                        raise ValidationFailedError(
                            f"场景 {scene.id} 分镜确定性校验两次未通过",
                            details={"errors": errors},
                        )
                    break
                feedback = "\n".join(errors)
            if content is None or not content.segments:
                raise ValidationFailedError(f"场景 {scene.id} 未产出任何分镜段")
            all_segments.extend(content.segments)
            prev_key = content.segments[-1].segment_key

        content = StoryboardContent(segments=all_segments)
        _normalize_segment_keys(content)
        errors = self._validate_content(
            content, known_ids, script=script, character_assets=character_assets
        )
        if errors:
            raise ValidationFailedError(
                "分镜最终校验未通过", details={"errors": errors}
            )
        _ensure_relay_anchor(content)
        warnings: list[str] = []
        total = sum(s.duration_sec for s in content.segments)
        if target and abs(total - target) / target > 0.15:
            warnings.append(f"Σ段时长 {total}s 与目标 {target}s 偏差超过 15%")
        if params.get("prompt_lang") == PromptLang.ZH.value:
            pass  # zh 仅影响正文语言，结构字段恒英文
        return content, warnings

    @staticmethod
    def _split_overloaded_segments(content: StoryboardContent) -> None:
        """单段台词即使延长到 15s 也装不下（>60 字）时，确定性二分拆段。

        台词按原顺序均分到两段；新段挂前段尾帧续接、克隆末镜承载后半台词；
        beat_refs 全部留在前段（认领覆盖不变）。段 key 先用临时号占位，
        函数末尾统一 _normalize_segment_keys 重编号并修正 continuity 引用。
        """

        def spoken_of(dialogues: list) -> int:
            return sum(len(d.line.strip()) for d in dialogues)

        used_temp: set[str] = set()
        changed = True
        guard = 0
        while changed and guard < 10:
            changed = False
            guard += 1
            for segment in list(content.segments):
                refs = [d for shot in segment.shots for d in shot.dialogue_refs]
                if spoken_of(refs) <= 15 * 4:
                    continue
                scene_num = int(segment.scene_id[1:])
                serial = 99
                temp_key = f"S{scene_num:02d}G{serial:02d}"
                while (
                    temp_key in used_temp
                    or any(s.segment_key == temp_key for s in content.segments)
                ):
                    serial -= 1
                    temp_key = f"S{scene_num:02d}G{serial:02d}"
                used_temp.add(temp_key)

                # 前半台词按原顺序凑够一半字符（至少一条），其余给新段
                total = spoken_of(refs)
                rest = list(refs)
                front: list = []
                chars = 0
                while rest and (chars < total / 2 or not front):
                    d = rest.pop(0)
                    front.append(d)
                    chars += len(d.line.strip())
                back = rest
                if not front or not back:
                    raise ValidationFailedError(
                        f"{segment.segment_key}: 单句台词超过 15s 口播上限"
                        "（60 字），请在剧本层拆短该句"
                    )

                source_shot = segment.shots[-1]
                front_ids = {id(d) for d in front}
                for shot in segment.shots:
                    shot.dialogue_refs = [
                        d for d in shot.dialogue_refs if id(d) in front_ids
                    ]
                segment.duration_sec = min(
                    15, max(4, round(spoken_of(front) / 4 + 1.5))
                )
                new_shot = source_shot.model_copy(
                    update={
                        "shot_no": 1,
                        "cutpoint_sec": 0.0,
                        "dialogue_refs": back,
                        "action": (
                            "Continuing directly from the previous shot, same "
                            "framing and character positions, the scene plays on."
                        ),
                        "beat_refs": [],
                    }
                )
                successor = segment.model_copy(
                    update={
                        "segment_key": temp_key,
                        "duration_sec": min(
                            15, max(4, round(spoken_of(back) / 4 + 1.5))
                        ),
                        "shots": [new_shot],
                        "continuity": segment.continuity.model_copy(
                            update={
                                "enabled": True,
                                "with_prev_segment_key": segment.segment_key,
                            }
                        ),
                    }
                )
                position = content.segments.index(segment)
                content.segments.insert(position + 1, successor)
                # 原来以「前段」为续接锚的段，现在改挂新段
                for other in content.segments:
                    if other is successor:
                        continue
                    if other.continuity.with_prev_segment_key == segment.segment_key:
                        other.continuity.with_prev_segment_key = temp_key
                changed = True
        _normalize_segment_keys(content)

    @staticmethod
    def _repair_unclaimed_beats(
        content: StoryboardContent,
        scene: object,
        assets_by_id: dict[str, Asset],
        style_line: str,
    ) -> bool:
        """漏认领的 action/on_screen_text 节拍确定性挂到场景末镜并附原文。

        LLM 长清单认领偶有遗漏（EP001 实测 16/17），重试也不稳定；保画面
        不丢拍的最后防线是代码兜底：refs 补齐 + 节拍原文并入 action
        （中文节拍词与既有中文身份锚同层级，H3 双语正文可消化）。
        返回是否发生了修复。
        """
        from server.app.h3_compiler import compile_h3_prompt

        beats = list(getattr(scene, "beats", []))
        if not beats or not content.segments:
            return False
        must = {
            i + 1
            for i, b in enumerate(beats)
            if b.type in (BeatType.ACTION, BeatType.ON_SCREEN_TEXT)
        }
        claimed: set[int] = set()
        for seg in content.segments:
            for shot in seg.shots:
                claimed.update(shot.beat_refs)
        missing = sorted(must - claimed)
        if not missing:
            return False
        last = content.segments[-1]
        if not last.shots:
            return False
        last_shot = last.shots[-1]
        for ref in missing:
            last_shot.beat_refs.append(ref)
            last_shot.action = (
                last_shot.action.rstrip()
                + f"; also show: {beats[ref - 1].text}"
            ).lstrip()
        compiled = compile_h3_prompt(last, assets_by_id, style_line=style_line)
        last.h3_prompt = last.h3_prompt.model_copy(
            update={"text": compiled, "lang": PromptLang.EN}
        )
        return True

    @staticmethod
    def _validate_content(
        content: StoryboardContent,
        known_asset_ids: set[str],
        target_sec: int = 0,
        script: ScriptContent | None = None,
        count_target: int = 0,
        total_target: int = 0,
        allowed_first_prev: str = "",
        spoken_advisory: bool = False,
        character_assets: dict[str, str] | None = None,
    ) -> list[str]:
        errors: list[str] = []
        try:
            keys = sorted({s.scene_id for s in content.segments})
        except Exception:
            return ["segments 结构无法解析"]
        del keys
        ordered = sorted(content.segments, key=lambda s: (s.scene_id, s.index))
        if [s.segment_key for s in content.segments] != [s.segment_key for s in ordered]:
            errors.append("segments 必须按 (scene_id, index) 升序排列")
        if script is not None:
            from collections import Counter

            want = Counter(
                (d.speaker.strip(), d.line.strip())
                for scene in script.scenes
                for d in scene.dialogues
            )
            got = Counter(
                (d.speaker.strip(), d.line.strip())
                for seg in content.segments
                for shot in seg.shots
                for d in shot.dialogue_refs
            )
            missing = want - got
            extra = got - want
            if missing:
                sample = [f"{sp}: {ln[:20]}" for sp, ln in list(missing.elements())[:5]]
                errors.append(
                    "以下剧本台词未被任何段引用（每句台词必须逐字分配到"
                    f"某个 shot 的 dialogue_refs）：{sample}"
                )
            if extra:
                sample = [f"{sp}: {ln[:20]}" for sp, ln in list(extra.elements())[:5]]
                errors.append(
                    f"dialogue_refs 含剧本中不存在的台词（禁止改写/增删台词）：{sample}"
                )
            # 节拍认领对账：action/on_screen_text 节拍必须被某镜认领，画面不丢拍；
            # 无节拍的旧剧本（beats 为空）跳过该检查
            scenes_by_id = {sc.id: sc for sc in script.scenes}
            claimed: dict[str, set[int]] = {}
            for seg in content.segments:
                scene = scenes_by_id.get(seg.scene_id)
                if scene is None or not scene.beats:
                    continue
                total_beats = len(scene.beats)
                for shot in seg.shots:
                    for ref in shot.beat_refs:
                        if not 1 <= ref <= total_beats:
                            errors.append(
                                f"{seg.segment_key}: shot{shot.shot_no} 的 beat_ref "
                                f"{ref} 越界（场景 {seg.scene_id} 共 {total_beats} 拍）"
                            )
                        else:
                            claimed.setdefault(seg.scene_id, set()).add(ref)
            for scene_id, scene in scenes_by_id.items():
                must_claim = {
                    i + 1
                    for i, b in enumerate(scene.beats)
                    if b.type in (BeatType.ACTION, BeatType.ON_SCREEN_TEXT)
                }
                unclaimed = must_claim - claimed.get(scene_id, set())
                if unclaimed:
                    errors.append(
                        f"场景 {scene_id} 的动作/画面文字节拍未被任何镜头认领："
                        f"{sorted(unclaimed)}（beat_refs 按场景节拍序号认领，"
                        "每个 action/on_screen_text 节拍都要落到某个镜）"
                    )
        if count_target:
            expected = max(1, count_target)
            if not expected - 1 <= len(content.segments) <= expected + 1:
                errors.append(
                    f"段数 {len(content.segments)} 偏离目标段数 {expected}"
                    f"（允许 ±1）：请按每段约 10-15 秒重新切段"
                )
        if total_target:
            total = sum(s.duration_sec for s in content.segments)
            if abs(total - total_target) / total_target > 0.15:
                errors.append(
                    f"Σ段时长 {total}s 与目标 {total_target}s 偏差超过 15%："
                    "按每段 10-15 秒重排后总长应落在 "
                    f"{round(total_target*0.85)}-{round(total_target*1.15)}s"
                )
        for position, segment in enumerate(ordered):
            # 台词完整优先于时长精准（TASK-029）：口播预算触发的自动加长
            # 可到 15s（H3 硬上限 4-15s）；>15 才是硬错误
            if segment.duration_sec > 15:
                errors.append(
                    f"{segment.segment_key}: duration_sec={segment.duration_sec}>15，"
                    "超过 H3 段时长上限，拆成多个 5 秒左右的段"
                )
            if len(segment.shots) > 2:
                errors.append(
                    f"{segment.segment_key}: shots={len(segment.shots)}>2，"
                    "一次生成内切点过多画面易漂移，拆段减少每段镜头数"
                )
            spoken = sum(
                len(d.line.strip()) for shot in segment.shots for d in shot.dialogue_refs
            )
            if spoken > segment.duration_sec * 4 and not spoken_advisory:
                errors.append(
                    f"{segment.segment_key}: 台词口播预算超限——{spoken} 字 > "
                    f"{segment.duration_sec}s × 4 字/秒，H3 念不完会被压缩或吞字，"
                    "删减台词或把该段加长（≤12s）"
                )
            continuity = segment.continuity
            if continuity.enabled:
                prev_key = (
                    ordered[position - 1].segment_key
                    if position
                    else allowed_first_prev
                )
                if prev_key != continuity.with_prev_segment_key:
                    errors.append(
                        f"{segment.segment_key}: continuity.with_prev_segment_key 必须是"
                        f"紧邻前一段的 segment_key"
                        f"（当前：{continuity.with_prev_segment_key or '空'}）"
                    )
            try:
                validate_segment(
                    segment,
                    known_asset_ids=known_asset_ids,
                    character_assets=character_assets,
                )
            except ValidationFailedError as exc:
                errors.extend(f"{segment.segment_key}: {e}" for e in exc.details.get("errors", []))
            except ValidationError as exc:
                errors.append(f"{segment.segment_key}: {exc.errors()[:3]}")
        return errors
