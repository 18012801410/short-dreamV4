"""一次性：给台词密集段加长时长（用后即删）。"""
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'server')
from scripts.test_pipeline import build
from server.domain.entities import StoryboardVersion

worker, ctx, svc, settings = build()
pid = 'p-e6fd455a9702'
sb = ctx.storyboards.list_by_project(pid)[0]
content = sb.model_copy(deep=True).content
for seg in content.segments:
    spoken = sum(len(d.line.strip()) for sh in seg.shots for d in sh.dialogue_refs)
    needed = max(4, min(12, round(-(-spoken // 4) + 1.5)))
    if spoken > seg.duration_sec * 4 and seg.duration_sec != needed:
        print(f'{seg.segment_key}: {seg.duration_sec}s -> {needed}s（{spoken} 字）')
        seg.duration_sec = needed
        step = needed / max(1, len(seg.shots))
        for pos, shot in enumerate(seg.shots, start=1):
            shot.cutpoint_sec = round((pos - 1) * step, 1)
version_no = sb.version_no + 1
version = StoryboardVersion(
    storyboard_version_id=f'sbv-dur-{version_no}',
    project_id=pid,
    version_no=version_no,
    content=content,
)
ctx.storyboards.add(version)
print(f'已保存 v{version_no}')
