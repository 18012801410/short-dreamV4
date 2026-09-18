"""成片字幕（TASK-019）：按分镜 shots 切点 + Clip 实测时长生成 SRT。

时间轴口径：段在成片中的起点 = 前面各段 Clip 实测时长累加（concat 按此顺序拼接）；
段内一句台词的显示窗口 = 它所属镜头的 [cutpoint, 下一镜头 cutpoint)（末镜到段尾）；
同镜多句台词均分该窗口。台词文本经 strip_stage_directions 清洗，舞台指示不上字幕。
"""

from __future__ import annotations

from server.domain.entities import Clip, Segment
from server.domain.textnorm import strip_stage_directions


def _srt_timecode(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_film_srt(
    segments: list[Segment],
    clips: dict[str, Clip],
    starts: list[float] | None = None,
) -> str:
    """按段顺序生成完整 SRT 文本；无任何台词返回空串。

    starts 给出各段在新时间轴上的起点（交叉淡化合成会缩短总时长），
    传了就按它对齐；不传则按 concat 口径用段实测时长累加。
    """
    entries: list[tuple[float, float, str]] = []
    offset = 0.0
    for position, segment in enumerate(segments):
        clip = clips.get(segment.segment_key)
        window_end_total = clip.duration_sec if clip else float(segment.duration_sec)
        base = starts[position] if starts else offset
        cutpoints = [shot.cutpoint_sec for shot in segment.shots]
        for index, shot in enumerate(segment.shots):
            start = base + cutpoints[index]
            end = base + (
                cutpoints[index + 1] if index + 1 < len(cutpoints) else window_end_total
            )
            lines = [
                text
                for d in shot.dialogue_refs
                if (text := strip_stage_directions(d.line)[0])
            ]
            if not lines:
                continue
            slice_sec = (end - start) / len(lines)
            for position_line, text in enumerate(lines):
                entries.append(
                    (
                        start + position_line * slice_sec,
                        start + (position_line + 1) * slice_sec,
                        text,
                    )
                )
        offset += window_end_total
    if not entries:
        return ""
    blocks = [
        f"{number}\n{_srt_timecode(start)} --> {_srt_timecode(end)}\n{text}\n"
        for number, (start, end, text) in enumerate(entries, start=1)
    ]
    return "\n".join(blocks)
