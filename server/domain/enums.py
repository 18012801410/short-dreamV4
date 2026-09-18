"""领域枚举（TASK-003）：与 DOMAIN_MODEL.md 逐项对应。"""

from __future__ import annotations

from enum import StrEnum


class ProjectStatus(StrEnum):
    """Project 十四态流水线 + 终态 ARCHIVED（DOMAIN_MODEL 状态机）。"""

    CREATED = "created"
    SCRIPT_DRAFTING = "script_drafting"
    SCRIPT_READY = "script_ready"
    SCRIPT_APPROVED = "script_approved"
    ASSET_DRAFTING = "asset_drafting"
    ASSET_READY = "asset_ready"
    ASSET_APPROVED = "asset_approved"
    STORYBOARD_DRAFTING = "storyboard_drafting"
    STORYBOARD_READY = "storyboard_ready"
    STORYBOARD_APPROVED = "storyboard_approved"
    FRAME_DRAFTING = "frame_drafting"
    FRAME_READY = "frame_ready"
    FRAME_APPROVED = "frame_approved"
    VIDEO_PRODUCING = "video_producing"
    VIDEO_READY = "video_ready"
    COMPOSING = "composing"
    COMPOSED = "composed"
    ARCHIVED = "archived"


class Stage(StrEnum):
    """流水线阶段名（下游失效 FR-016 的回退目标与阶段命名）。"""

    SCRIPT = "script"
    ASSETS = "assets"
    STORYBOARD = "storyboard"
    FRAME = "frame"
    VIDEO = "video"
    FILM = "film"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(StrEnum):
    SCRIPT_GEN = "script_gen"
    ASSET_EXTRACT = "asset_extract"
    IMAGE_GEN = "image_gen"
    STORYBOARD_GEN = "storyboard_gen"
    FRAME_GEN = "frame_gen"
    VIDEO_GEN = "video_gen"
    TAIL_EXTRACT = "tail_extract"
    COMPOSE = "compose"
    # 系列分集（TASK-047）：series 级大纲生成任务；jobs.project_id 存 series_id
    SERIES_OUTLINE_GEN = "series_outline_gen"


class SeriesStatus(StrEnum):
    """系列（分集剧）状态：大纲驱动；集内制作走各自 Project 状态机。

    - CREATED：已建档，还没生成大纲
    - OUTLINE_DRAFTING：大纲生成中
    - OUTLINE_READY：大纲草稿可编辑/评审（质量门结果在 outline.warnings）
    - OUTLINE_APPROVED：大纲已确认，可批量生成分集剧本
    """

    CREATED = "created"
    OUTLINE_DRAFTING = "outline_drafting"
    OUTLINE_READY = "outline_ready"
    OUTLINE_APPROVED = "outline_approved"


class AssetKind(StrEnum):
    CHARACTER = "character"
    SCENE = "scene"
    PROP = "prop"


class DramaticTone(StrEnum):
    """剧作基调（TASK-030 双轨剧作法）：ScriptAgent 按基调切换结构法则。

    - hook：短剧钩子驱动（黄金开局/高频反转/反派片内受罚）
    - three_act：微电影三幕式（单一核心事件/三幕配比/允许留白）
    """

    HOOK = "hook"
    THREE_ACT = "three_act"


class WorkStatus(StrEnum):
    """版本化工件的版本状态（剧本/分镜/图片通用）。"""

    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class AssetImageStatus(StrEnum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    UPLOADED = "uploaded"


class SegmentStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class FilmStatus(StrEnum):
    COMPOSING = "composing"
    READY = "ready"
    FAILED = "failed"


class VideoMode(StrEnum):
    """H3 生成模式（模式矩阵在 TASK-011 组装请求时使用）。"""

    T2VA = "t2va"
    I2VA = "i2va"
    R2VA = "r2va"


class PromptLang(StrEnum):
    EN = "en"
    ZH = "zh"


class BeatType(StrEnum):
    """剧本节拍类型（Scene.beats 有序承载的画面描述流）。"""

    ACTION = "action"  # 画面动作：谁、在哪、做什么、怎么做的
    DIALOGUE = "dialogue"  # 台词：text=台词原文，speaker/tone 配套
    SFX = "sfx"  # 音效：刹车声、手机震动等
    ON_SCREEN_TEXT = "on_screen_text"  # 画面文字：手机弹字、字幕卡
    TRANSITION = "transition"  # 转场提示
