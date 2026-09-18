"""实体 Pydantic 模型（TASK-003）：内容 schema 与工件，边界处校验。

实体级不变量（段时长、切点、键一致性、镜号连续）在模型构造时即强制；
跨实体规则（资产引用存在、H3 提示词结构）见 validation.py。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.domain.enums import (
    AssetImageStatus,
    AssetKind,
    BeatType,
    DramaticTone,
    FilmStatus,
    JobStatus,
    JobType,
    ProjectStatus,
    PromptLang,
    SegmentStatus,
    SeriesStatus,
    VideoMode,
    WorkStatus,
)

# 场景号允许 1-2 位（LLM 偶尔漏补零写 S1G01），由
# agents._normalize_segment_keys 统一规范化为零填充两位
SEGMENT_KEY_RE = re.compile(r"^S\d{1,2}G\d{2}$")
SCENE_ID_RE = re.compile(r"^S\d+$")
MIN_SEGMENT_SEC = 4
MAX_SEGMENT_SEC = 15
MIN_REF_PX = 256


def utcnow() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    """领域模型基类：禁止未知字段，防止 LLM 输出悄悄带入脏数据。"""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Project
# --------------------------------------------------------------------------


class ProjectParams(DomainModel):
    genre: str = ""
    style: str = ""
    # 剧作基调（TASK-030）：ScriptAgent 按基调注入对应结构法则；存量项目缺省走 hook
    dramatic_tone: DramaticTone = DramaticTone.HOOK
    target_duration_sec: int = Field(default=60, gt=0)
    scene_count: int = Field(default=3, ge=1)
    ratio: str = "9:16"
    resolution: str = "768P"
    prompt_lang: PromptLang = PromptLang.EN


class Project(DomainModel):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    idea: str = ""
    params: ProjectParams = Field(default_factory=ProjectParams)
    status: ProjectStatus = ProjectStatus.CREATED
    # 系列分集（TASK-047）：独立项目两列为空值默认；挂系列时 episode_no 从 1 起
    series_id: str = ""
    episode_no: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# 剧本：ScriptVersion + ScriptContent
# --------------------------------------------------------------------------


class DialogueLine(DomainModel):
    speaker: str = Field(min_length=1)
    line: str = Field(min_length=1)
    tone: str = ""  # 语气（如 声音很低/不耐烦），来自台词节拍，进 H3 口型句


class Beat(DomainModel):
    """剧本节拍（EP001 口径）：按时间排序的画面描述流，一场戏的每个
    视觉事件（动作/台词/音效/画面文字/转场）各占一拍。"""

    type: BeatType
    text: str = Field(min_length=1)  # 动作描述 / 台词原文 / 音效 / 画面文字 / 转场
    speaker: str = ""  # dialogue 专用
    tone: str = ""  # dialogue 专用语气


class Scene(DomainModel):
    id: str = Field(pattern=SCENE_ID_RE.pattern)
    title: str = Field(min_length=1)  # 场景头三要素：内/外 · 地点 · 日/夜
    summary: str = ""
    dialogues: list[DialogueLine] = Field(default_factory=list)
    est_seconds: int = Field(gt=0)
    # 节拍流：新剧本必有（dialogues 由 dialogue 节拍派生，二者台词逐字一致）；
    # 旧剧本/手写剧本允许为空，下游按无节拍路径降级
    beats: list[Beat] = Field(default_factory=list)


class CharacterProfile(DomainModel):
    name: str = Field(min_length=1)
    profile: str = ""


class ScriptContent(DomainModel):
    """ScriptAgent 的结构化输出（AI_SPEC ScriptAgent）。"""

    logline: str = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1)
    characters: list[CharacterProfile] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def total_est_seconds(self) -> int:
        return sum(s.est_seconds for s in self.scenes)


class ScriptVersion(DomainModel):
    script_version_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version_no: int = Field(ge=1)
    content: ScriptContent
    source: str = "llm_call"
    status: WorkStatus = WorkStatus.DRAFT
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# 系列（TASK-047）：Series + SeriesOutline（大纲五要素：剧名/logline/人物表/分集梗概/爆点）
# --------------------------------------------------------------------------


class SeriesParams(DomainModel):
    """系列级参数：逐集下发的 ProjectParams 由它派生（同一系列口径一致）。"""

    genre: str = ""
    style: str = ""
    dramatic_tone: DramaticTone = DramaticTone.HOOK
    episode_count: int = Field(default=12, ge=1)
    # 单集目标时长（秒）：短剧完播率友好区间 60-120
    per_episode_sec: int = Field(default=90, gt=0)
    # 单集场景数：90 秒约 3-5 场
    scene_count: int = Field(default=4, ge=1)
    ratio: str = "9:16"
    resolution: str = "768P"
    prompt_lang: PromptLang = PromptLang.EN


class EpisodeOutline(DomainModel):
    """一集的梗概卡：短剧方法论的落点——每集必有开场钩子与集尾卡点。"""

    episode_no: int = Field(ge=1)
    title: str = Field(min_length=1)
    # 本集故事梗概（150-300 字）：逐集剧本生成的直接输入
    synopsis: str = ""
    # 开场钩子（前 3 秒抛出的冲突/悬念/身份反差，可拍的一句话）
    opening_hook: str = ""
    # 集尾卡点（在本集最后一拍落地的悬念；最后一集允许为空=收尾）
    ending_hook: str = ""
    # 本集爽点/反转说明（类型 + 一句话，如「身份碾压：亮出董事长身份」）
    highlight: str = ""
    # 本集首次出场的角色名（跨集人物表之外新增的）
    new_characters: list[str] = Field(default_factory=list)


class SeriesOutline(DomainModel):
    """SeriesAgent 的结构化输出：先全季大纲后逐集剧本（行业标准打法）。"""

    title: str = Field(min_length=1)  # 剧名
    logline: str = Field(min_length=1)
    genre_tags: list[str] = Field(default_factory=list)  # 题材标签（可叠加，如 战神+萌宝）
    # 全剧人物表：profile 需含 身份/动机/口头禅或语言特征/爽点功能，跨集共用
    characters: list[CharacterProfile] = Field(default_factory=list)
    episodes: list[EpisodeOutline] = Field(min_length=1)
    # 大爆点所在集（全剧最强爽点）；质量门要求不压最后一集，0=未标注
    climax_episode: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class Series(DomainModel):
    series_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    idea: str = ""
    params: SeriesParams = Field(default_factory=SeriesParams)
    outline: SeriesOutline | None = None
    status: SeriesStatus = SeriesStatus.CREATED
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# 资产：Asset + AssetImage
# --------------------------------------------------------------------------


class ImagePlanItem(DomainModel):
    """AssetAgent 产出的每张设定图生图提示词。"""

    view_label: str = Field(min_length=1)
    image_prompt: str = Field(min_length=1)


class Asset(DomainModel):
    asset_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    kind: AssetKind
    name: str = Field(min_length=1)
    description: str = ""
    visual_anchor: str = ""
    image_plan: list[ImagePlanItem] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)


class AssetImage(DomainModel):
    asset_image_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    version_no: int = Field(ge=1)
    view_label: str = Field(min_length=1)
    prompt: str = ""
    provider: str = "minimax-image"
    provider_ref: str = ""
    file_path: str = ""  # POSIX 相对路径，落地后不可变
    status: AssetImageStatus = AssetImageStatus.GENERATING
    approved: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _approved_requires_landed_file(self) -> AssetImage:
        if self.approved and self.status not in {AssetImageStatus.READY, AssetImageStatus.UPLOADED}:
            raise ValueError(f"只有 ready/uploaded 图片可批准（当前 {self.status.value}）")
        return self


# --------------------------------------------------------------------------
# 分镜：StoryboardVersion + Segment
# --------------------------------------------------------------------------


class ShotDialogueRef(DomainModel):
    speaker: str = Field(min_length=1)
    line: str = Field(min_length=1)
    tone: str = ""  # 说话方式英文短语（in a low voice），空则编译器走默认口型句


class Shot(DomainModel):
    shot_no: int = Field(ge=1)
    cutpoint_sec: float = Field(ge=0)
    camera: str = ""
    description: str = ""  # 英文短语标签（≤25 词），用于概览与 summary
    # 该镜时间窗内的英文动作时间线段落（开始→中途→结束），H3
    # detailed_description 的正文主体；为空时回退 description
    action: str = ""
    # 认领的场景节拍序号（1 起，对应 Scene.beats 下标+1）：
    # action/on_screen_text 节拍必须被某镜认领，保证画面描述不丢拍
    beat_refs: list[int] = Field(default_factory=list)
    dialogue_refs: list[ShotDialogueRef] = Field(default_factory=list)


class Continuity(DomainModel):
    enabled: bool = False
    with_prev_segment_key: str = ""

    @model_validator(mode="after")
    def _prev_key_required_when_enabled(self) -> Continuity:
        if self.enabled and not self.with_prev_segment_key:
            raise ValueError("continuity.enabled=true 必须给出 with_prev_segment_key")
        return self


class AssetRef(DomainModel):
    asset_id: str = Field(min_length=1)
    usage_note: str = ""


class SubjectPlacement(DomainModel):
    """段开场时一个在场角色的空间状态（英文短语，TASK-032）。

    位置类缺陷（如驾驶员被画进副驾）的根治：空间状态不再是 shot.action
    里的自由散文，而是结构化产出——校验器强制每个在场角色都有条目，
    编译器把它确定性渲染进关键帧提示词与 <Subject N> 定义，
    保证关键帧图、首帧、视频正文三方对同一位置状态。

    in_frame（TASK-033）：该角色在本段开场帧画面里是否可见。关键帧是构图
    确定的静帧——不在画面里的角色若把身份卡送进图生图通道，模型会把第二张
    脸也摆进画面，并把两张卡的特征互相串（实测：座位旁的乘客卡让坐着的角色
    挂上了另一人的道具、背景多出人影）。false = 本段在场但开场帧看不到
    （画面外/被遮挡/仅远处剪影不需要锁脸）；存量分镜无此字段默认 True。
    """

    name: str = Field(min_length=1)  # 必须与在场角色资产 name 一致
    # 英文位置状态，如 in the driver's seat, hands on the steering wheel
    placement: str = Field(min_length=1)
    in_frame: bool = True


class H3Prompt(DomainModel):
    text: str
    lang: PromptLang = PromptLang.EN
    structure_version: str = "v1"
    # 人工覆盖（TASK-040）：页面手改过正文后置 True——产视频时直接用存储文本，
    # 不再被实时重编译覆盖。清除后回到"编译器为准"的常态。
    manual_override: bool = False


class Segment(DomainModel):
    """一次视频生成的完整单元（嵌入 StoryboardVersion.content）。"""

    segment_key: str = Field(pattern=SEGMENT_KEY_RE.pattern)
    scene_id: str = Field(pattern=SCENE_ID_RE.pattern)
    index: int = Field(ge=1)
    duration_sec: int = Field(ge=MIN_SEGMENT_SEC, le=MAX_SEGMENT_SEC)
    shots: list[Shot] = Field(min_length=1)
    asset_refs: list[AssetRef] = Field(default_factory=list)
    continuity: Continuity = Field(default_factory=Continuity)
    # 声音设计（英文）：来自剧本 sfx 节拍 + 场景氛围 / 配乐意图；
    # 为空时编译器给中性兜底（不再硬编码雨声）
    soundscape: str = ""
    music: str = ""
    # 开场锚点关键帧的英文静态画面描述（TASK-031）：锁定本段第 0 帧
    # 的构图/人物位置姿态/持物/光效；为空时关键帧提示词回退 shot.description。
    # 存量分镜无此字段，默认空串兼容。
    keyframe_description: str = ""
    # 开场时每个在场角色的空间状态（TASK-032）：校验器强制在场角色逐人
    # 给出条目，编译器注入关键帧提示词与 <Subject N> 定义。存量分镜无此
    # 字段，默认空列表兼容（编译器跳过位置行，身份锚点修复仍生效）。
    subject_placements: list[SubjectPlacement] = Field(default_factory=list)
    h3_prompt: H3Prompt
    status: SegmentStatus = SegmentStatus.DRAFT

    @model_validator(mode="after")
    def _structural_invariants(self) -> Segment:
        # 切点/键名的一致性不在此处强制：LLM 原始输出常需先解析再由
        # agents._normalize_segment_keys + _autorepair_scene 规范化，
        # 规范校验由 validation.validate_segment 把关
        for no, shot in enumerate(self.shots, start=1):
            if shot.shot_no != no:
                raise ValueError(f"镜号必须连续（第 {no} 个镜头 shot_no={shot.shot_no}）")
        return self


class StoryboardContent(DomainModel):
    segments: list[Segment] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_unique_and_ordered(self) -> StoryboardContent:
        keys = [s.segment_key for s in self.segments]
        if len(set(keys)) != len(keys):
            raise ValueError("segment_key 必须在版本内唯一")
        return self


class StoryboardVersion(DomainModel):
    storyboard_version_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version_no: int = Field(ge=1)
    content: StoryboardContent
    status: WorkStatus = WorkStatus.DRAFT
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# 关键帧：SegmentFrameImage（TASK-031，分镜确认后、视频前 per 段开场锚点图）
# --------------------------------------------------------------------------

# 宫格分镜板行的 view_label（每段最多一行，approved 后整张作视频 <Picture 1>）
STORYBOARD_GRID_LABEL = "分镜板"


class SegmentFrameImage(DomainModel):
    """一个分镜段的开场锚点关键帧图（镜像 AssetImage，挂 segment_key）。

    段有已批准关键帧 → produce 时作为 <Picture 1> 开场参考（尾帧接力让位）；
    无 → 回退现有尾帧接力。
    """

    frame_image_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    segment_key: str = Field(pattern=SEGMENT_KEY_RE.pattern)
    version_no: int = Field(ge=1)
    view_label: str = "开场锚点"
    # 多宫格格号（1 起；None=非格子行）。宫格分镜板行本身 view_label="分镜板"
    # 且 grid_cell=None；存量单张关键帧行 grid_cell=None，走旧口径不受影响。
    grid_cell: int | None = Field(default=None, ge=1)
    prompt: str = ""
    provider: str = "minimax-image"
    provider_ref: str = ""
    file_path: str = ""  # POSIX 相对路径，落地后不可变
    # 本张图实际使用的参考图（相对路径，按送入顺序，TASK-035）：让页面能
    # 摊开"这张图参考了谁"，也支持按帧追溯与手工指定参考图重抽
    reference_paths: list[str] = Field(default_factory=list)
    status: AssetImageStatus = AssetImageStatus.GENERATING
    approved: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _approved_requires_landed_file(self) -> SegmentFrameImage:
        if self.approved and self.status not in {AssetImageStatus.READY, AssetImageStatus.UPLOADED}:
            raise ValueError(f"只有 ready/uploaded 图片可批准（当前 {self.status.value}）")
        return self


# --------------------------------------------------------------------------
# 视频/成片：Clip + Film
# --------------------------------------------------------------------------


class Clip(DomainModel):
    clip_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    segment_key: str = Field(pattern=SEGMENT_KEY_RE.pattern)
    video_job_id: str = Field(min_length=1)
    storyboard_version_id: str = Field(min_length=1)
    file_path: str = Field(min_length=1)  # POSIX 相对路径
    thumbnail_path: str = ""
    tail_frame_path: str = ""
    duration_sec: float = Field(gt=0)
    mode: VideoMode
    created_at: datetime = Field(default_factory=utcnow)


class Film(DomainModel):
    film_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version_no: int = Field(ge=1)
    file_path: str = ""
    subtitle_path: str = ""  # 烧录字幕版（合成后生成；失败留空，不影响原片）
    segment_keys: list[str] = Field(default_factory=list)
    duration_sec: float = Field(default=0, ge=0)
    status: FilmStatus = FilmStatus.COMPOSING
    error: str = ""
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# 成本/设置：ProviderCall + AppSettings
# --------------------------------------------------------------------------


class ProviderCall(DomainModel):
    call_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = ""
    kind: str = ""  # llm/image/video
    usage: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class AppSettings(DomainModel):
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_model: str = "glm-4.6"
    image_provider: str = "minimax-image"
    image_model: str = "image-01"
    video_model: str = "MiniMax-H3"
    video_resolution: str = "768P"
    video_concurrency: int = Field(default=1, ge=1)
    # 图片生成并发上限（TASK-048：image_gen + frame_gen 同时在跑数，默认 2）
    image_concurrency: int = Field(default=2, ge=1)
    poll_interval_sec: float = Field(default=2.0, gt=0)
    ffmpeg_path: str = "ffmpeg"


# --------------------------------------------------------------------------
# 参考图解析（TASK-011 resolve_references 的数据载体）
# --------------------------------------------------------------------------


class ResolvedReference(DomainModel):
    """一个 segment.asset_refs 解析出的单张 H3 参考图。"""

    asset_id: str = Field(min_length=1)
    kind: AssetKind
    asset_image_id: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    usage_note: str = ""


# --------------------------------------------------------------------------
# Job（实体在 job.py 扩展状态机行为，schema 定义于此）
# --------------------------------------------------------------------------


class JobEvent(DomainModel):
    at: datetime = Field(default_factory=utcnow)
    event: str
    detail: str = ""


class JobError(DomainModel):
    code: str = "UNKNOWN"
    message: str = ""
    provider_code: str = ""


class Job(DomainModel):
    job_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    type: JobType
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    progress: int = Field(default=0, ge=0, le=100)
    phase: str = ""
    priority: int = Field(default=0)
    depends_on: list[str] = Field(default_factory=list)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    provider_task_id: str = ""
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    error: JobError | None = None
    log: list[JobEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def _snapshot_required_for_video(self) -> Job:
        if self.type is JobType.VIDEO_GEN and not self.input_snapshot:
            raise ValueError("video_gen Job 必须持久化 input_snapshot")
        return self
