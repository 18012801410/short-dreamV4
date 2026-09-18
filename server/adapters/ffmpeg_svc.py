"""ffmpeg 服务（TASK-002 检测 + TASK-007 探针/尾帧/拼接）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict


class FFmpegInfo(TypedDict):
    found: bool
    path: str | None
    version: str | None


class ProbeResult(TypedDict):
    width: int
    height: int
    duration_sec: float
    has_audio: bool


def detect_ffmpeg(override: str = "ffmpeg") -> FFmpegInfo:
    exe = shutil.which(override) or shutil.which("ffmpeg")
    if exe is None:
        return FFmpegInfo(found=False, path=None, version=None)
    try:
        result = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, timeout=5
        )
        version = result.stdout.splitlines()[0] if result.stdout else None
    except (OSError, subprocess.TimeoutExpired):
        return FFmpegInfo(found=False, path=exe, version=None)
    return FFmpegInfo(found=True, path=exe, version=version)


class FFmpegError(Exception):
    pass


def _find_pair(ffmpeg_path: str) -> tuple[str | None, str | None]:
    """解析 ffmpeg 与 ffprobe；FFMPEG_PATH 指自定义路径时优先其同目录的 ffprobe。"""
    ffmpeg = shutil.which(ffmpeg_path) or shutil.which("ffmpeg")
    if ffmpeg is None:
        return None, None
    suffix = ".exe" if os.name == "nt" else ""
    candidates = [
        Path(ffmpeg).with_name(f"ffprobe{suffix}"),
        Path(ffmpeg).with_name("ffprobe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ffmpeg, str(candidate)
    return ffmpeg, shutil.which("ffprobe")


class FFmpegService:
    """尾帧提取 / 媒体探针 / 成片拼接（同步 subprocess）。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self._ffmpeg, self._ffprobe = _find_pair(ffmpeg_path)

    def available(self) -> bool:
        return self._ffmpeg is not None and self._ffprobe is not None

    def _require(self) -> tuple[str, str]:
        if not self.available():
            raise FFmpegError("ffmpeg/ffprobe 不可用（检查 PATH 或设置页 FFMPEG_PATH）")
        assert self._ffmpeg and self._ffprobe
        return self._ffmpeg, self._ffprobe

    def probe(self, media: Path) -> ProbeResult:
        _, ffprobe = self._require()
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(media),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise FFmpegError(f"probe 失败：{result.stderr[-300:]}")
        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0.0)
        return ProbeResult(
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            duration_sec=duration,
            has_audio=audio is not None,
        )

    def decode_check(self, media: Path) -> tuple[bool, str]:
        """全流解码校验：容器 header 正常但码流损坏（下载截断等）在此拦截。"""
        ffmpeg, _ = self._require()
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(media), "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
        bad = result.returncode != 0 or "Error" in result.stderr or "Invalid" in result.stderr
        return (not bad, result.stderr[-300:])

    def extract_tail_frame(self, video: Path, dest: Path, *, offset_sec: float = 0.1) -> Path:
        """截取结尾帧（offset_sec 为从结尾回退的秒数）。"""
        ffmpeg, _ = self._require()
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                ffmpeg, "-y", "-sseof", f"-{offset_sec}", "-i", str(video),
                "-frames:v", "1", str(dest),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0 or not dest.exists():
            raise FFmpegError(f"尾帧提取失败：{result.stderr[-300:]}")
        return dest

    def compose_film(
        self, clips: list[Path], dest: Path, *, fade_sec: float = 0.25
    ) -> tuple[float, list[float]]:
        """电影化合成：统一调色 + 切点交叉淡化 + 连续雨声环境音床。

        相比 concat 的 -c copy 硬拼：
        - 每段画面统一轻度调色（同一 eq 滤镜），消除"每段一个色调"的割裂感；
        - 相邻段 0.25s 交叉淡化（xfade），切点从硬跳变为柔和过渡；
        - 段音轨交叉淡化（acrossfade）后，叠一条连续的粉噪声雨床，
          遮盖各段独立生成环境音在切点的跳变。
        返回 (成片时长秒, 各段在新时间轴上的起点)——字幕时间轴按此对齐。
        """
        if not clips:
            raise FFmpegError("compose_film 需要至少一个输入")
        ffmpeg, _ = self._require()
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if len(clips) == 1:
            shutil.copyfile(clips[0], dest)
            probed = self.probe(dest)
            return probed["duration_sec"], [0.0]

        infos = [self.probe(c) for c in clips]
        durations = [i["duration_sec"] for i in infos]
        has_audio = [i["has_audio"] for i in infos]
        fade = min(fade_sec, min(durations) / 3)
        if fade <= 0:
            self.concat(clips, dest)
            return sum(durations), [
                sum(durations[:k]) for k in range(len(clips))
            ]
        starts = [0.0]
        for d in durations[:-1]:
            starts.append(starts[-1] + d - fade)
        total = starts[-1] + durations[-1]

        n = len(clips)
        cmd: list[str] = [ffmpeg, "-y"]
        for c in clips:
            cmd += ["-i", str(c)]
        # 无音轨的段补静音源（保证音频图完整）
        audio_in: dict[int, int] = {}
        extra = n
        for k in range(n):
            if has_audio[k]:
                audio_in[k] = k
            else:
                cmd += [
                    "-f", "lavfi", "-t", f"{durations[k]:.3f}",
                    "-i", "anullsrc=r=44100:cl=stereo",
                ]
                audio_in[k] = extra
                extra += 1
        bed_in = extra
        cmd += [
            "-f", "lavfi", "-t", f"{total + 1:.3f}",
            "-i", "anoisesrc=color=pink:amplitude=0.05:seed=2026",
        ]

        f: list[str] = []
        for k in range(n):
            f.append(
                f"[{k}:v]fps=24,settb=AVTB,"
                f"eq=contrast=1.03:saturation=1.06,format=yuv420p[v{k}]"
            )
        prev = "v0"
        for k in range(1, n):
            out = f"x{k}"
            f.append(
                f"[{prev}][v{k}]xfade=transition=fade:duration={fade:.3f}:"
                f"offset={starts[k]:.3f}[{out}]"
            )
            prev = out
        for k in range(n):
            d = durations[k]
            f.append(
                f"[{audio_in[k]}:a]aresample=44100,"
                f"afade=t=in:st=0:d=0.15,"
                f"afade=t=out:st={max(0.0, d - 0.15):.3f}:d=0.15[a{k}]"
            )
        prev_a = "a0"
        for k in range(1, n):
            out = f"c{k}"
            f.append(
                f"[{prev_a}][a{k}]acrossfade=d={fade:.3f}:c1=tri:c2=tri[{out}]"
            )
            prev_a = out
        f.append(f"[{bed_in}:a]highpass=f=250,lowpass=f=3200,volume=0.28[bed]")
        f.append(
            f"[{prev_a}][bed]amix=inputs=2:duration=first:normalize=0[mixa]"
        )
        f.append("[mixa]alimiter=limit=0.95[aout]")

        cmd += [
            "-filter_complex", ";".join(f),
            "-map", f"[{prev}]", "-map", "[aout]",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-r", "24",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(dest),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0 or not dest.exists():
            raise FFmpegError(f"compose_film 失败：{result.stderr[-400:]}")
        return total, starts

    def concat(self, clips: list[Path], dest: Path) -> Path:
        """按顺序拼接（concat demuxer + -c copy：要求各段同编码参数，H3 导演台产物满足）。"""
        ffmpeg, _ = self._require()
        if not clips:
            raise FFmpegError("concat 需要至少一个输入")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        list_file = dest.with_suffix(".concat.txt")
        lines = []
        for clip in clips:
            posix_path = Path(clip).resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{posix_path}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = subprocess.run(
            [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c", "copy", str(dest),
            ],
            capture_output=True, text=True, timeout=600,
        )
        list_file.unlink(missing_ok=True)
        if result.returncode != 0 or not dest.exists():
            raise FFmpegError(f"concat 失败：{result.stderr[-300:]}")
        return dest

    def burn_subtitles(self, video: Path, srt: Path, dest: Path) -> Path:
        """烧录字幕（重编码视频，音频直拷）。以视频所在目录为工作目录，
        SRT 用相对文件名传给 subtitles 滤镜，绕开 Windows 盘符转义。"""
        ffmpeg, _ = self._require()
        video, srt, dest = Path(video), Path(srt), Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        style = "FontName=Microsoft YaHei,Fontsize=9,Outline=1,Shadow=0,MarginV=24"
        result = subprocess.run(
            [
                ffmpeg, "-y", "-i", video.name,
                "-vf", f"subtitles={srt.name}:force_style='{style}'",
                "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-c:a", "copy", str(dest.resolve()),
            ],
            capture_output=True, text=True, timeout=1800,
            cwd=str(video.parent),
        )
        if result.returncode != 0 or not dest.exists():
            raise FFmpegError(f"字幕烧录失败：{result.stderr[-300:]}")
        return dest
