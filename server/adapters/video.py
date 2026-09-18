"""VideoProvider 适配器（TASK-007）：RunningHub H3 导演台（ref2va）实现。

工作流 2093160296864116737（初版）/ 2099856521889935362（满血版，去 Refine 二段），
节点 12（MiniMaxH3Director），输出节点 7（SaveVideo）。timeline 模板按
`.env` 的 RUNNINGHUB_WORKFLOW_VIDEO 匹配 workerflow/MiniMaxH3Director_ref2va_<ID>_api.json，
无同名文件时回退初版模板。
仅实现 reference（r2v）模式：平台口径下资产图/连续性尾帧几乎总是存在；
base（纯文生）模式的 task_type 字符串未实测，显式抛 UNSUPPORTED_MODE（见 PROJECT_STATE 开放问题）。
提示词内容由领域校验器（validation.py）按官方 dialect 把关，本模块只组装与运输。
"""

from __future__ import annotations

import copy
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.adapters.ffmpeg_svc import FFmpegService, ProbeResult
from server.adapters.runninghub import RunningHubClient, RunningHubError
from server.domain.providers import GeneratedVideo, MediaUsage
from server.infra.config import PROJECT_ROOT, Settings

VIDEO_NODE_ID = "12"  # MiniMaxH3Director
VIDEO_SAVE_NODE = "7"  # SaveVideo（另一输出节点为预览通道）
WORKFLOW_TEMPLATE_PATH = (
    PROJECT_ROOT / "workerflow" / "MiniMaxH3Director_ref2va_2093160296864116737_api.json"
)


def resolve_template_path(workflow_id: str) -> Path:
    """按 `.env` 工作流 ID 找同名 timeline 模板；无同名文件回退初版模板。

    换工作流 = RunningHub 导入 API JSON 拿新 ID + 存同名模板 + 改 `.env`，代码零改动。
    """
    candidate = PROJECT_ROOT / "workerflow" / f"MiniMaxH3Director_ref2va_{workflow_id}_api.json"
    return candidate if candidate.exists() else WORKFLOW_TEMPLATE_PATH
VIDEO_SUFFIXES = (".mp4", ".webm", ".mov")
FRAMES_PER_SEC = 24
FRAME_BUFFER = 3  # TASK-001 观测：10s → 243 帧（24*10+3）；真实时长以 ffprobe 为准
SECTION_MARKERS = (
    "summary:",
    "retention_analysis:",
    "detailed_description:",
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)


def frames_for_duration(duration_sec: int) -> int:
    return duration_sec * FRAMES_PER_SEC + FRAME_BUFFER


def dims_for_ratio(template_w: int, template_h: int, ratio: str) -> tuple[int, int]:
    """项目画幅 → 视频输出宽高：沿用模板的像素预算（长/短边），方向不同则交换。

    模板两版都硬编码 864×480（16:9）；9:16 竖屏项目若不换向，成片会是横版。
    ratio 非法/缺省时保持模板原样（旧 Job 快照兼容）。
    """
    try:
        w, h = (int(part) for part in str(ratio).split(":", maxsplit=1))
        if w <= 0 or h <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return template_w, template_h
    if (w > h) != (template_w > template_h):
        return template_h, template_w
    return template_w, template_h


def extract_subject_definitions(prompt: str) -> str:
    """提取六段提示词的 subject_definitions 段（作为导演台 global_prompt 身份锁）。"""
    marker = "subject_definitions:"
    start = prompt.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = len(prompt)
    for other in SECTION_MARKERS:
        pos = prompt.find(other, start)
        if pos != -1:
            end = min(end, pos)
    return prompt[start:end].strip()


def build_timeline(
    timeline_template: dict[str, Any],
    *,
    prompt: str,
    duration_sec: int,
    ref_file_names: list[str],
    total_frames: int,
    reference_video_file: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """以工作流 timeline 为底做最小修改：单段、换提示词与参考图（TASK-007 组装器）。

    reference_video_file：上一段成片的 ComfyUI 输入文件名（视频续接），
    写入段级 referenceVideo 槽位并置 continuityFromPrev，无则维持原样。
    width/height：项目画幅换算的输出尺寸，写入 timeline 顶层与 output 节点
    （模板硬编码 864×480 横版，9:16 项目必须换向，否则成片是横的）；None 保持模板值。
    """
    timeline = copy.deepcopy(timeline_template)
    segment = timeline["segments"][0]
    segment["prompt"] = prompt
    segment["continuityFromPrev"] = False
    segment["refs"] = []
    # 模板 segment 自带 10s/243 帧默认值，工作流以 segment 字段为准——
    # 不同步的话任何时长都会出 10s（多计费且成片节奏失真）
    segment["start"] = 0
    segment["length"] = total_frames
    segment["frameCount"] = total_frames
    segment["durationSec"] = duration_sec
    timeline["segments"] = [segment]
    timeline["totalFrames"] = total_frames
    timeline["durationSec"] = duration_sec
    timeline["video"]["sourceFrameCount"] = total_frames
    if width is not None and height is not None:
        timeline["width"] = width
        timeline["height"] = height
        output = timeline.setdefault("output", {})
        output["width"] = width
        output["height"] = height
    if reference_video_file:
        # 官方"续接"入口：上一段成片作为参考视频，continuityFromPrev 打开
        rv = {"videoFile": reference_video_file, "fileName": "", "type": "input", "subfolder": ""}
        segment["referenceVideo"] = dict(rv)
        segment["continuityFromPrev"] = True
        timeline["global"]["referenceVideo"] = dict(rv)
        timeline["global"]["continuousReference"] = True
    timeline["global"]["prompt"] = extract_subject_definitions(prompt)
    timeline["global"]["refs"] = [
        {
            "index": index,
            "imageFile": name,
            "fileName": "",
            "type": "input",
            "subfolder": "",
        }
        for index, name in enumerate(ref_file_names)
    ]
    timeline["output"]["audioMode"] = "generate"  # 无源视频，成片音轨由 H3 生成
    if "r2v" in timeline.get("batchWorkspaces", {}):
        r2v = timeline["batchWorkspaces"]["r2v"]
        r2v["segments"] = [dict(segment)]
        r2v["runSelectEnabled"] = True
        r2v["runSelection"] = [0]
        r2v["selectedIndex"] = 0
        r2v["globalCommon"] = {
            "commonEnabled": True,
            "commonCollapsed": True,
            "prompt": timeline["global"]["prompt"],
            "refs": [dict(r) for r in timeline["global"]["refs"]],
            "refAudios": [],
            "refVideos": [],
        }
    timeline["runSelectEnabled"] = True
    timeline["runSelection"] = [0]
    return json.dumps(timeline, ensure_ascii=False)


def _pick_video(outputs: list[dict]) -> dict | None:
    candidates = [
        item
        for item in outputs or []
        if str(item.get("fileUrl", "")).lower().endswith(VIDEO_SUFFIXES)
    ]
    for item in candidates:  # 最终成片节点优先，预览通道靠后
        if str(item.get("nodeId")) == VIDEO_SAVE_NODE:
            return item
    return candidates[0] if candidates else None


class RunningHubVideo:
    """同步实现，与 Worker 同步 handler 协议一致。"""

    def __init__(
        self,
        *,
        client: RunningHubClient,
        workflow_id: str,
        timeline_template: dict[str, Any],
        probe: Callable[[Path], ProbeResult],
        seed_range: tuple[int, int] = (1, 2**31),
        verify: Callable[[Path], tuple[bool, str]] | None = None,
    ) -> None:
        self._client = client
        self._workflow_id = workflow_id
        self._timeline_template = timeline_template
        self._probe = probe
        self._seed_range = seed_range
        self._verify = verify

    def generate(
        self,
        *,
        prompt: str,
        duration_sec: int,
        reference_files: list[Path],
        dest: Path,
        seed: int | None = None,
        reference_video: Path | None = None,
        ratio: str = "",
    ) -> GeneratedVideo:
        if not reference_files:
            raise RunningHubError(
                "UNSUPPORTED_MODE",
                "base（纯文生）模式在该工作流上未实测（无 t2v task_type 字符串），"
                "请为该段绑定资产参考图或连续性尾帧",
            )
        seed = seed if seed is not None else random.randint(*self._seed_range)
        ref_names = [self._client.upload(Path(f)) for f in reference_files]
        # 视频续接：上一段成片作为 referenceVideo（导演台官方"续接"入口）；
        # 上传失败不阻断——尾帧图参考仍兜底
        reference_video_file: str | None = None
        if reference_video is not None and Path(reference_video).exists():
            try:
                reference_video_file = self._client.upload(Path(reference_video))
            except RunningHubError as exc:
                import sys

                print(
                    f"[reference-video] 上传失败，退回尾帧锚定：{exc}",
                    file=sys.stderr,
                    flush=True,
                )
        total_frames = frames_for_duration(duration_sec)
        template_w = int(self._timeline_template.get("width") or 864)
        template_h = int(self._timeline_template.get("height") or 480)
        out_w, out_h = dims_for_ratio(template_w, template_h, ratio)
        timeline_data = build_timeline(
            self._timeline_template,
            prompt=prompt,
            duration_sec=duration_sec,
            ref_file_names=ref_names,
            total_frames=total_frames,
            reference_video_file=reference_video_file,
            width=out_w,
            height=out_h,
        )
        node_info_list = [
            {"nodeId": VIDEO_NODE_ID, "fieldName": "timeline_data", "fieldValue": timeline_data},
            {"nodeId": VIDEO_NODE_ID, "fieldName": "total_frames", "fieldValue": total_frames},
            {
                "nodeId": VIDEO_NODE_ID,
                "fieldName": "global_prompt",
                "fieldValue": extract_subject_definitions(prompt),
            },
            {"nodeId": VIDEO_NODE_ID, "fieldName": "seed", "fieldValue": seed},
        ]
        task_id = self._client.create_task(self._workflow_id, node_info_list)
        outputs = self._client.wait_for_success(task_id, label="video")
        file = _pick_video(outputs)
        if file is None:
            raise RunningHubError(
                "BAD_RESPONSE",
                "outputs 中没有视频产物",
                details={"outputs": str(outputs)[:500]},
            )
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = self._client.download(file["fileUrl"])
        dest.write_bytes(content)
        if self._verify is not None:
            ok, detail = self._verify(dest)
            if not ok:
                # 下载截断/码流损坏：重下一次，仍坏则显式失败（不落半成品）
                content = self._client.download(file["fileUrl"])
                dest.write_bytes(content)
                ok, detail2 = self._verify(dest)
                if not ok:
                    raise RunningHubError(
                        "BAD_RESPONSE",
                        "产物视频解码校验失败（重下载仍损坏）",
                        details={"stderr": detail2},
                    )
        probed = self._probe(dest)
        return GeneratedVideo(
            provider_task_id=task_id,
            file_url=file["fileUrl"],
            dest=dest,
            size_bytes=len(content),
            duration_sec=probed["duration_sec"],
            width=probed["width"],
            height=probed["height"],
            has_audio=probed["has_audio"],
            usage=MediaUsage(
                coins=str(file.get("consumeCoins") or "0"),
                task_seconds=str(file.get("taskCostTime") or "0"),
                raw=file,
            ),
        )


def build_video_provider_from_settings(settings: Settings) -> RunningHubVideo:
    if not settings.runninghub_api_key:
        raise RunningHubError("AUTH", "RUNNINGHUB_API_KEY 未配置（.env 或环境变量）")
    client = RunningHubClient(
        base_url=settings.runninghub_base_url,
        api_key=settings.runninghub_api_key,
        poll_interval=settings.poll_interval_sec * 2 or 5.0,
        poll_timeout=2400.0,
    )
    template = json.loads(
        resolve_template_path(settings.runninghub_workflow_video).read_text(encoding="utf-8")
    )
    timeline_template = json.loads(
        template[VIDEO_NODE_ID]["inputs"]["timeline_data"]
    )
    return RunningHubVideo(
        client=client,
        workflow_id=settings.runninghub_workflow_video,
        timeline_template=timeline_template,
        probe=FFmpegService(settings.ffmpeg_path).probe,
        verify=FFmpegService(settings.ffmpeg_path).decode_check,
    )


__all__ = [
    "RunningHubVideo",
    "build_timeline",
    "build_video_provider_from_settings",
    "dims_for_ratio",
    "extract_subject_definitions",
    "frames_for_duration",
    "resolve_template_path",
]
