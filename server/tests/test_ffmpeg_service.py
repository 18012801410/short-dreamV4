"""FFmpegService 集成单测（TASK-007）：用真实 ffmpeg 生成测试素材（本机免费）。

ffmpeg 未安装时跳过（平台硬依赖，安装提示见 TECH_SPEC）。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from server.adapters.ffmpeg_svc import FFmpegError, FFmpegService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 不在 PATH",
)


def make_clip(dest: Path, seconds: float, color: str) -> Path:
    """生成带音轨的测试视频（正弦音 + 纯色画面）。"""
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x320:d={seconds}:r=24",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(dest),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-300:]
    return dest


@pytest.fixture()
def service() -> FFmpegService:
    return FFmpegService()


def test_probe_reports_duration_dimensions_audio(tmp_path: Path, service: FFmpegService) -> None:
    clip = make_clip(tmp_path / "clip.mp4", 1.0, "navy")
    probed = service.probe(clip)
    assert probed["width"] == 320 and probed["height"] == 320
    assert 0.9 <= probed["duration_sec"] <= 1.5
    assert probed["has_audio"]


def test_extract_tail_frame(tmp_path: Path, service: FFmpegService) -> None:
    clip = make_clip(tmp_path / "clip.mp4", 1.0, "red")
    tail = service.extract_tail_frame(clip, tmp_path / "tail.jpg")
    probed = service.probe(tail)
    assert probed["width"] == 320 and probed["height"] == 320


def test_concat_preserves_duration_and_audio(tmp_path: Path, service: FFmpegService) -> None:
    a = make_clip(tmp_path / "a.mp4", 1.0, "red")
    b = make_clip(tmp_path / "b.mp4", 1.0, "blue")
    film = service.concat([a, b], tmp_path / "film.mp4")
    probed = service.probe(film)
    assert 1.9 <= probed["duration_sec"] <= 2.6
    assert probed["has_audio"]


def test_concat_requires_inputs(tmp_path: Path, service: FFmpegService) -> None:
    with pytest.raises(FFmpegError):
        service.concat([], tmp_path / "film.mp4")


def test_burn_subtitles_proplays_watched_clip(tmp_path: Path, service: FFmpegService) -> None:
    clip = make_clip(tmp_path / "clip.mp4", 1.0, "navy")
    srt = tmp_path / "subs.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:00,800\n河记得一切。\n", encoding="utf-8"
    )
    dest = tmp_path / "film_sub.mp4"
    try:
        service.burn_subtitles(clip, srt, dest)
    except FFmpegError as exc:
        pytest.skip(f"ffmpeg 缺 subtitles 滤镜/libass：{exc}")
    probed = service.probe(dest)
    assert 0.9 <= probed["duration_sec"] <= 1.6
    assert probed["has_audio"]
