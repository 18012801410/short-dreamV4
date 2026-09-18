"""逐段验收辅助：对项目内某段的最新 clip 抽 4 帧 + ffprobe 概要。

用法：python scripts/inspect_clip.py <segment_key> [--project-id PID]
输出：data/media/<pid>/inspect/<key>/f01..f04.jpg
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.test_pipeline import build  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("segment_key")
    ap.add_argument("--project-id", default="")
    args = ap.parse_args()
    worker, ctx, svc, settings = build()
    pid = args.project_id or ctx.projects.list_all()[0].project_id
    clips = [c for c in ctx.clips.list_by_project(pid) if c.segment_key == args.segment_key]
    if not clips:
        raise SystemExit(f"无 clip：{args.segment_key}")
    clip = clips[-1]
    src = settings.media_dir / clip.file_path
    out_dir = settings.media_dir / pid / "inspect" / args.segment_key
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f*.jpg"):
        old.unlink()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip() or 0)
    # 均匀抽样 + 末秒加密采样（H3 生成漂移集中在结尾，尾帧也取自最后一帧）
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-vf", f"fps=4/{duration}", str(out_dir / "f%02d.jpg")],
        check=True,
    )
    n = len(list(out_dir.glob("f*.jpg")))
    for i, frac in enumerate((0.90, 0.95, 0.985)):
        at = max(0.0, duration * frac - 0.05)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(src),
             "-frames:v", "1", str(out_dir / f"tail{i + 1}.jpg")],
            check=True,
        )
    print(
        f"{args.segment_key} | {clip.file_path} | {duration:.3f}s | "
        f"frames={n}+3 | tail={clip.tail_frame_path}"
    )
    print(f"frames -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
