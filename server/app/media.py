"""媒体路径助手（TASK-008）：POSIX 相对路径 ↔ 绝对路径（红线：DB 只存相对路径）。"""

from __future__ import annotations

from pathlib import Path

from server.infra.config import Settings


def asset_image_rel(project_id: str, asset_id: str, image_id: str, ext: str = ".png") -> str:
    return f"{project_id}/assets/{asset_id}_{image_id}{ext}"


def segment_video_rel(project_id: str, segment_key: str, version_no: int) -> str:
    return f"{project_id}/segments/{segment_key}_v{version_no}.mp4"


def segment_tail_rel(project_id: str, segment_key: str, version_no: int) -> str:
    return f"{project_id}/frames/{segment_key}_v{version_no}_tail.jpg"


def segment_frame_rel(project_id: str, segment_key: str, frame_image_id: str) -> str:
    """段关键帧图落盘路径（TASK-031；与尾帧同目录不同前缀）。"""
    return f"{project_id}/frames/{segment_key}_{frame_image_id}.png"


def uploaded_frame_rel(project_id: str, frame_image_id: str, ext: str) -> str:
    return f"{project_id}/frames/uploaded_{frame_image_id}{ext}"


def film_rel(project_id: str, version_no: int) -> str:
    return f"{project_id}/films/film_v{version_no}.mp4"


def film_sub_rel(project_id: str, version_no: int) -> str:
    return f"{project_id}/films/film_v{version_no}_sub.mp4"


def film_srt_rel(project_id: str, version_no: int) -> str:
    return f"{project_id}/films/film_v{version_no}.srt"


def uploaded_image_rel(project_id: str, image_id: str, ext: str) -> str:
    return f"{project_id}/assets/uploaded_{image_id}{ext}"


def abs_media_path(settings: Settings, rel: str) -> Path:
    return settings.media_dir / rel


def size_for_ratio(ratio: str) -> tuple[int, int]:
    """项目画幅 → 生图像素（QWEN 工作流口径，长边 1920）。"""
    mapping = {
        "9:16": (1080, 1920),
        "16:9": (1920, 1080),
        "3:4": (1440, 1920),
        "4:3": (1920, 1440),
        "1:1": (1440, 1440),
    }
    return mapping.get(ratio, (1920, 1080))


def size_for_asset_card(ratio: str = "16:9") -> tuple[int, int]:
    """资产设定卡尺寸跟项目画幅（MiniMax H3 官方 3d-animation-short-generator 口径：
    参考图与成片同画幅时身份/构图迁移最稳；9:16 竖屏短剧出竖版卡，
    场景卡三面板布局由 AssetAgent 提示词按画幅自适应）。
    参考图本身不决定视频输出画幅，H3 只取身份与风格。"""
    return size_for_ratio(ratio)
