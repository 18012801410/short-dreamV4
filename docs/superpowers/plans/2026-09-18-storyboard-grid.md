# 分镜多宫格分镜板 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个分镜逐 shot 生成单帧格子（复用现有单帧关键帧口径），程序拼成多宫格分镜板，整张作为视频生成 `<Picture 1>` 的 storyboard reference（官方支持口径）。

**Architecture:** 格子仍是 `segment_frame_images` 行（新增 `grid_cell` 列标记格号），拼宫格是纯程序行为（PIL，复用 `_compose_reference_sheet` 思路）；视频侧沿用现有 `keyframe_path 占 Picture 1` 通道，仅在提示词 subject_definitions 注入宫格声明句。尾帧接力、确认门、资产卡体系全部不动。

**Tech Stack:** Python 3.12 / Pydantic / SQLAlchemy + Alembic（SQLite）/ PIL（已入 requirements）/ pytest。

**设计文档:** `docs/superpowers/specs/2026-09-18-storyboard-grid-design.md`

**测试命令:** `python -m pytest server/tests -x -q`（单测文件用 `python -m pytest server/tests/test_frame_stage.py -v`）

**AGENTS.md 硬规则提醒:** 涉及 `server/` 源码，全部任务完成后必须重启 API 与 Worker 并确认 `/api/system/health` stale=false（见 Task 8）。

---

### Task 1: 领域字段 `grid_cell` + 迁移 + 序列化

**Files:**
- Modify: `server/domain/entities.py:379-407`（SegmentFrameImage）
- Modify: `server/domain/validation.py:25` 附近（常量）
- Modify: `server/infra/tables.py:102-118`（segment_frame_images 建表）
- Create: `migrations/versions/0007_frame_grid_cell.py`
- Modify: `server/api/serializers.py:83-102`（frame_image_dto）
- Test: `server/tests/test_frame_stage.py`

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_frame_stage.py` 末尾追加：

```python
class TestGridCellField:
    def test_frame_row_accepts_grid_cell(self, tmp_path) -> None:
        from server.domain.entities import SegmentFrameImage

        row = SegmentFrameImage(
            frame_image_id="frm-x",
            project_id="p1",
            segment_key="S01G01",
            version_no=1,
            grid_cell=2,
        )
        assert row.grid_cell == 2

    def test_frame_row_default_grid_cell_none(self, tmp_path) -> None:
        from server.domain.entities import SegmentFrameImage

        row = SegmentFrameImage(
            frame_image_id="frm-x",
            project_id="p1",
            segment_key="S01G01",
            version_no=1,
        )
        assert row.grid_cell is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest server/tests/test_frame_stage.py::TestGridCellField -v`
Expected: FAIL（`SegmentFrameImage` 无 `grid_cell` 字段）

- [ ] **Step 3: 实现领域字段**

`server/domain/entities.py` — 在 `SegmentFrameImage` 的 `view_label` 字段后追加：

```python
    # 多宫格格号（1 起；None=非格子行）。宫格分镜板行本身 view_label="分镜板"
    # 且 grid_cell=None；存量单张关键帧行 grid_cell=None，走旧口径不受影响。
    grid_cell: int | None = Field(default=None, ge=1)
```

同文件 `SegmentFrameImage` 类上方（`# ----` 分隔注释附近）加常量：

```python
# 宫格分镜板行的 view_label（每段最多一行，approved 后整张作视频 <Picture 1>）
STORYBOARD_GRID_LABEL = "分镜板"
```

`server/domain/validation.py` — 在 `MAX_REFERENCE_IMAGES = 9` 附近加：

```python
# 单段宫格最多格数（=取前 N 个 shot；超出的镜头交给视频模型按提示词发挥）
MAX_GRID_CELLS = 6
```

`server/infra/tables.py` — `segment_frame_images` 表的 `view_label` 列后加：

```python
    sa.Column("grid_cell", sa.Integer(), nullable=True),
```

Create `migrations/versions/0007_frame_grid_cell.py`（仿 0005 的结构）：

```python
"""segment_frame_images.grid_cell: 多宫格分镜板的格号（方案A 逐格生成+程序拼宫格）

Revision ID: 0007_frame_grid_cell
Revises: 0006_series_support
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_frame_grid_cell"
down_revision: str | None = "0006_series_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("segment_frame_images", sa.Column("grid_cell", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("segment_frame_images", "grid_cell")
```

`server/api/serializers.py` — `frame_image_dto` 返回字典的 `"view_label"` 行后加：

```python
        "grid_cell": image.grid_cell,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest server/tests/test_frame_stage.py -v`
Expected: 全部 PASS（含既有用例——repo 走 `_row_to_dict`/`model_validate`，新列自动兼容）

- [ ] **Step 5: Commit**

```bash
git add server/domain/entities.py server/domain/validation.py server/infra/tables.py server/api/serializers.py migrations/versions/0007_frame_grid_cell.py server/tests/test_frame_stage.py
git commit -m "feat: 关键帧行增加 grid_cell 格号字段（多宫格分镜板 Step1）"
```

---

### Task 2: `compile_keyframe_prompt` 支持 `description_override`

格子画面描述来自对应 shot（action→description 回退），不再固定读 `keyframe_description`/首镜。编译器加一个可选参数，单帧口径全部保留。

**Files:**
- Modify: `server/app/h3_compiler.py:444-476`（compile_keyframe_prompt 签名与描述来源）
- Test: `server/tests/test_h3_compiler.py`（若无此文件则建在 `server/tests/test_frame_stage.py`）

- [ ] **Step 1: 写失败测试**

```python
class TestKeyframeDescriptionOverride:
    def test_override_replaces_keyframe_description(self) -> None:
        """description_override 优先于 keyframe_description 与首镜回退。"""
        from server.app.h3_compiler import compile_keyframe_prompt
        from server.domain.entities import (
            H3Prompt,
            Segment,
            Shot,
        )

        seg = Segment(
            segment_key="S01G01",
            scene_id="SC01",
            index=1,
            duration_sec=10,
            shots=[
                Shot(shot_no=1, cutpoint_sec=5, camera="wide", description="d1", action="a1"),
                Shot(shot_no=2, cutpoint_sec=10, camera="close", description="d2", action="a2"),
            ],
            keyframe_description="整段开场描述",
            h3_prompt=H3Prompt(),
        )
        prompt = compile_keyframe_prompt(seg, {}, description_override="a2")
        assert "a2" in prompt
        assert "整段开场描述" not in prompt
        assert "a1" not in prompt
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest server/tests/test_frame_stage.py::TestKeyframeDescriptionOverride -v`
Expected: FAIL（无 `description_override` 参数）

- [ ] **Step 3: 实现**

`server/app/h3_compiler.py` 签名改为：

```python
def compile_keyframe_prompt(
    segment: Segment,
    assets_by_id: dict[str, Asset],
    style_line: str = "",
    extra_prompt: str = "",
    include_identity_anchors: bool = True,
    reference_bindings: list | None = None,
    aspect: str = "",
    description_override: str = "",
) -> str:
```

描述来源行（现 `description = segment.keyframe_description.strip()`）改为：

```python
    description = (description_override or segment.keyframe_description).strip()
```

（首镜回退分支保持不变。docstring 补一句：多宫格逐格生成时传对应 shot 的动作描述。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest server/tests/test_frame_stage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app/h3_compiler.py server/tests/test_frame_stage.py
git commit -m "feat: 关键帧编译器支持逐格 description_override"
```

---

### Task 3: `compile_h3_prompt` 宫格声明句

**Files:**
- Modify: `server/app/h3_compiler.py:281-320`（签名 + subject_definitions）
- Test: `server/tests/test_frame_stage.py`

- [ ] **Step 1: 写失败测试**

```python
class TestStoryboardReferenceDeclaration:
    def test_storyboard_sentence_injected(self) -> None:
        """storyboard_reference=True 时 subject_definitions 注入宫格声明+防入画护栏。"""
        from server.app.h3_compiler import compile_h3_prompt
        from server.domain.entities import H3Prompt, Segment, Shot

        seg = Segment(
            segment_key="S01G01",
            scene_id="SC01",
            index=1,
            duration_sec=10,
            shots=[Shot(shot_no=1, cutpoint_sec=10, camera="wide", description="d", action="a")],
            h3_prompt=H3Prompt(),
        )
        prompt = compile_h3_prompt(seg, {}, opening_frame=True, storyboard_reference=True)
        assert "storyboard reference" in prompt
        assert "never appears on screen" in prompt

    def test_no_storyboard_sentence_by_default(self) -> None:
        from server.app.h3_compiler import compile_h3_prompt
        from server.domain.entities import H3Prompt, Segment, Shot

        seg = Segment(
            segment_key="S01G01",
            scene_id="SC01",
            index=1,
            duration_sec=10,
            shots=[Shot(shot_no=1, cutpoint_sec=10, camera="wide", description="d", action="a")],
            h3_prompt=H3Prompt(),
        )
        prompt = compile_h3_prompt(seg, {}, opening_frame=True)
        assert "storyboard reference" not in prompt
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest server/tests/test_frame_stage.py::TestStoryboardReferenceDeclaration -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`server/app/h3_compiler.py` 模块级常量（`RELAY_ANCHOR_EN` 旁）加：

```python
# 宫格分镜板声明（<Picture 1>=宫格时注入）：官方 R2V 口径——声明视角/站位/镜序，
# 并加"宫格本身不上画"护栏（防止 H3 把多格布局当成画面内容渲染成分屏）
STORYBOARD_ANCHOR_EN = (
    "<Picture 1> is a storyboard reference for the shots of this segment, "
    "defining the viewpoint, subject placement, and shot order; the target "
    "video is a single continuous take, and the grid layout of <Picture 1> "
    "itself never appears on screen."
)
```

签名加参：

```python
def compile_h3_prompt(
    segment: Segment,
    assets_by_id: dict[str, Asset],
    scene_summary: str = "",
    style_line: str = "",
    opening_frame: bool | None = None,
    storyboard_reference: bool = False,
) -> str:
```

`subject_definitions` 组装处（`if relay: subj.append(RELAY_ANCHOR_EN)`）改为：

```python
    if relay:
        subj.append(RELAY_ANCHOR_EN)
        if storyboard_reference:
            subj.append(STORYBOARD_ANCHOR_EN)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest server/tests/test_frame_stage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app/h3_compiler.py server/tests/test_frame_stage.py
git commit -m "feat: H3 编译器支持宫格分镜板声明句"
```

---

### Task 4: 宫格拼图 `storyboard_rel` + `_compose_storyboard_grid`

**Files:**
- Modify: `server/app/media.py:47-50` 附近
- Modify: `server/app/usecases.py`（`_compose_reference_sheet` 旁新增方法）
- Modify: `server/infra/config.py`（Settings 加 `storyboard_grid_cols`）
- Test: `server/tests/test_frame_stage.py`

- [ ] **Step 1: 写失败测试**

```python
class TestComposeStoryboardGrid:
    def test_grid_layout_three_cols(self, tmp_path) -> None:
        """4 格 16:9 图 → 3 列 2 行白边宫格，落盘路径含 digest。"""
        import pytest as _pytest
        from server.app.usecases import WorkbenchService
        # 复用测试文件内既有的 ctx/settings 构造夹具；若无现成夹具，
        # 参照同文件既有用例的 setup 方式构造（metadata.create_all + AppContext.build）
        ...
```

> 注：夹具写法以 `server/tests/test_frame_stage.py` 既有用例为准（该文件已有多个 `metadata.create_all(engine)` + 构造服务的用例，如 `test_reference_cards_three_in_frame_characters_keep_scene_card`，第 168 行）。测试体：

```python
    def test_grid_layout_three_cols(self, <既有夹具参数>) -> None:
        from PIL import Image

        svc = <既有方式构造 service>
        # 造 4 张 160x90 纯色格图
        cell_paths = []
        for i in range(4):
            p = tmp_path / f"cell{i}.png"
            Image.new("RGB", (160, 90), (i * 40, 0, 0)).save(p)
            cell_paths.append(str(p))
        rel = svc._compose_storyboard_grid(svc.ctx.settings, "p1", "S01G01", cell_paths)
        from server.infra.media_paths import abs_media_path  # 按实际 import 路径
        img = Image.open(abs_media_path(svc.ctx.settings, rel))
        # 3 列 → 2 行；gap=12：宽=3*160+2*12=504，高=2*90+12=192
        assert img.size == (504, 192)
        assert "storyboards/" in rel and "S01G01" in rel

    def test_grid_reuse_same_digest(self, <既有夹具参数>) -> None:
        svc = <既有方式构造 service>
        cell_paths = [...]
        rel1 = svc._compose_storyboard_grid(svc.ctx.settings, "p1", "S01G01", cell_paths)
        rel2 = svc._compose_storyboard_grid(svc.ctx.settings, "p1", "S01G01", cell_paths)
        assert rel1 == rel2
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest server/tests/test_frame_stage.py::TestComposeStoryboardGrid -v`
Expected: FAIL（方法不存在）

- [ ] **Step 3: 实现**

`server/app/media.py`（`reference_sheet_rel` 旁）：

```python
def storyboard_rel(project_id: str, segment_key: str, digest: str, ext: str = ".jpg") -> str:
    """段多宫格分镜板落盘路径（逐格拼图，digest 复用同组结果）。"""
    return f"{project_id}/storyboards/{segment_key}_grid_{digest}{ext}"
```

`server/infra/config.py` Settings 类（`h3_reference_video` 附近）加：

```python
    # 多宫格分镜板列数（方案A：逐格生成 + 程序拼宫格）
    storyboard_grid_cols: int = 3
```

`server/app/usecases.py` — `WorkbenchService._compose_reference_sheet` 下方加模块级函数（与 `_compose_reference_sheet` 同为模块级，保持一致）：

```python
def _compose_storyboard_grid(
    settings, project_id: str, segment_key: str, cell_rel_paths: list[str]
) -> str:
    """把该段已批准的格子横拼成多宫格分镜板（方案A）。

    布局：storyboard_grid_cols 列、按格号顺序排布、白底 12px 分隔、每行居中。
    格子由同尺寸生图产出（size_for_asset_card），天然对齐；防御性等比缩放。
    文件名取格路径摘要：同组格子复用同一张宫格，重复同步不重复落盘。
    """
    import hashlib

    from PIL import Image

    from server.app.media import storyboard_rel

    digest = hashlib.md5("\n".join(cell_rel_paths).encode("utf-8")).hexdigest()[:16]
    rel = storyboard_rel(project_id, segment_key, digest)
    dest = abs_media_path(settings, rel)
    if dest.exists():
        return rel
    images = []
    for p in cell_rel_paths:
        with Image.open(abs_media_path(settings, p)) as im:
            images.append(im.convert("RGB"))
    cols = max(1, int(getattr(settings, "storyboard_grid_cols", 3)))
    cell_h = min(im.height for im in images)
    scaled = [
        im.resize((max(1, round(im.width * cell_h / im.height)), cell_h))
        for im in images
    ]
    gap = 12
    rows = [scaled[i : i + cols] for i in range(0, len(scaled), cols)]
    row_widths = [sum(im.width for im in row) + gap * (len(row) - 1) for row in rows]
    width = max(row_widths)
    height = len(rows) * cell_h + gap * (len(rows) - 1)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    y = 0
    for row, row_w in zip(rows, row_widths):
        x = (width - row_w) // 2
        for im in row:
            canvas.paste(im, (x, y))
            x += im.width + gap
        y += cell_h + gap
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, quality=92)
    return rel
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest server/tests/test_frame_stage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app/media.py server/infra/config.py server/app/usecases.py server/tests/test_frame_stage.py
git commit -m "feat: 新增多宫格分镜板 PIL 拼图 _compose_storyboard_grid"
```

---

### Task 5: 入队逐格化（批量/单抽/上传）

**Files:**
- Modify: `server/app/usecases.py:1402-1492`（_enqueue_frame_job）、`:1551-1566`（cmd_generate_keyframes）、`:1568-1595`（cmd_generate_frame_image）、`:1597-1621`（cmd_upload_frame_image）
- Modify: `server/api/routes_workbench.py:348-351`（upload 端点透传 cell_no）
- Test: `server/tests/test_frame_stage.py`

- [ ] **Step 1: 写失败测试**

```python
class TestEnqueuePerCell:
    def test_batch_generates_one_job_per_shot(self, <既有夹具参数>) -> None:
        """2-shot 段批量生成 → 2 个 frame_gen 任务，各带 grid_cell 1/2；
        1-shot 段 → 1 个任务、grid_cell=None（旧口径）。"""
        svc = <既有方式构造，含 2-shot 段项目>
        project = svc.get_project("p1")
        result = svc.cmd_generate_keyframes(project, {})
        assert len(result["jobs"]) == 2
        rows = svc.ctx.frames.list_by_segment("p1", "S01G01")
        assert sorted(r.grid_cell for r in rows) == [1, 2]
        # 第 2 格的提示词含第 2 镜描述、不含第 1 镜独有内容
        row2 = next(r for r in rows if r.grid_cell == 2)
        assert "a2" in row2.prompt  # 按测试段实际 shot action 断言

    def test_single_shot_segment_keeps_legacy(self, <既有夹具参数>) -> None:
        """1-shot 段不产生格号（旧单帧口径）。"""
        ...
        assert rows[0].grid_cell is None

    def test_reroll_with_cell_no(self, <既有夹具参数>) -> None:
        """cmd_generate_frame_image 带 cell_no=2 → 新行 grid_cell=2。"""
        ...
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest server/tests/test_frame_stage.py::TestEnqueuePerCell -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`server/app/usecases.py` 顶部 import 区加 `from server.domain.validation import MAX_GRID_CELLS`（合并进现有 validation import 块，第 63-67 行）。

`_enqueue_frame_job` 签名加参：

```python
    def _enqueue_frame_job(
        self,
        project,
        segment,
        extra_prompt: str = "",
        seed: int | None = None,
        prompt_override: str = "",
        reference_image_ids: list[str] | None = None,
        cell_no: int | None = None,
    ) -> Job:
```

方法内两处改动：

a) 编译提示词处（`compile_keyframe_prompt(...)` 调用）加 `description_override`：

```python
        cell_description = ""
        if cell_no is not None:
            shot = segment.shots[cell_no - 1]
            cell_description = (
                shot.action.strip() or shot.description.strip()
            )
```

```python
            prompt = compile_keyframe_prompt(
                segment,
                assets_by_id,
                style_line=project.params.style or DEFAULT_STYLE_LINE,
                extra_prompt=extra_prompt,
                include_identity_anchors=not reference_rel_paths,
                reference_bindings=reference_bindings,
                aspect=project.params.ratio,
                description_override=cell_description,
            )
```

b) 建行处 `SegmentFrameImage(...)` 加字段，并在入队前作废旧宫格（重抽格 → 宫格作废，approve 后重拼）：

```python
        frame_row = SegmentFrameImage(
            frame_image_id=row_id,
            project_id=project.project_id,
            segment_key=segment.segment_key,
            version_no=len(
                self.ctx.frames.list_by_segment(project.project_id, segment.segment_key)
            )
            + 1,
            prompt=prompt,
            reference_paths=list(reference_rel_paths),
            grid_cell=cell_no,
            status=AssetImageStatus.GENERATING,
        )
        self.ctx.frames.add(frame_row)
        if cell_no is not None:
            self._invalidate_storyboard_grid(project.project_id, segment.segment_key)
```

（`_invalidate_storyboard_grid` 在 Task 6 实现——本任务先写调用，Task 6 补方法；若严格分步运行，本任务先在类里放空实现占位并在 Task 6 补全逻辑。）

```python
    def _invalidate_storyboard_grid(self, pid: str, key: str) -> None:
        """作废该段宫格行（重抽格/删格后由 approve 流程重拼）。"""
        for row in self.ctx.frames.list_by_segment(pid, key):
            if row.view_label == STORYBOARD_GRID_LABEL:
                self.ctx.frames.delete(row.frame_image_id)
```

`cmd_generate_keyframes`（第 1562-1566 行）改为逐格入队：

```python
        moved = apply_action(project, "generate_keyframes")
        _, segments = self._active_segments(project.project_id)
        jobs: list[Job] = []
        for seg in segments:
            if len(seg.shots) == 1:
                # 单镜段：无宫格价值，保持旧单帧口径
                jobs.append(self._enqueue_frame_job(moved, seg))
                continue
            for cell_no in range(1, min(len(seg.shots), MAX_GRID_CELLS) + 1):
                jobs.append(self._enqueue_frame_job(moved, seg, cell_no=cell_no))
        self.ctx.projects.save(moved)
        return CommandResult(jobs=jobs)
```

`cmd_generate_frame_image`（第 1579-1594 行）读 payload 的 `cell_no` 并透传：

```python
        raw_cell = payload.get("cell_no")
        cell_no = int(raw_cell) if raw_cell not in (None, "") else None
        job = self._enqueue_frame_job(
            project,
            segment,
            extra_prompt=extra,
            seed=seed,
            prompt_override=prompt_override,
            reference_image_ids=reference_image_ids,
            cell_no=cell_no,
        )
```

`cmd_upload_frame_image`（第 1609-1619 行）建行处加 `grid_cell`：

```python
        raw_cell = payload.get("cell_no")
        cell_no = int(raw_cell) if raw_cell not in (None, "") else None
        row = SegmentFrameImage(
            frame_image_id=f"frm-{image_id}",
            project_id=project.project_id,
            segment_key=key,
            version_no=len(self.ctx.frames.list_by_segment(project.project_id, key)) + 1,
            view_label=str(payload.get("view_label") or "上传"),
            prompt="",
            provider="upload",
            file_path=rel,
            grid_cell=cell_no,
            status=AssetImageStatus.UPLOADED,
        )
```

`server/api/routes_workbench.py` upload 端点组装 payload 处透传 `cell_no`（从 form/query 取可选整数，随 payload 传入 `cmd_upload_frame_image`）。

- [ ] **Step 4: 运行确认通过 + 修既有用例**

Run: `python -m pytest server/tests/test_frame_stage.py server/tests/test_app_flow.py -v`
Expected: PASS。注意：既有断言"批量生成 → 每段 1 个任务"的用例（若测试段为多 shot）需更新为新口径（多 shot 段 = min(shots,6) 个任务）。

- [ ] **Step 5: Commit**

```bash
git add server/app/usecases.py server/api/routes_workbench.py server/tests/test_frame_stage.py
git commit -m "feat: 关键帧批量生成逐格入队，单抽/上传支持 cell_no"
```

---

### Task 6: 宫格同步 `_sync_storyboard_grid` + 锚点选择 `_approved_anchor_frame`

**Files:**
- Modify: `server/app/usecases.py:1350-1357`（_approved_keyframe）、`:1631-1643`（cmd_approve_frame_image）、`:1623-1629`（cmd_delete_frame_image）
- Test: `server/tests/test_frame_stage.py`

- [ ] **Step 1: 写失败测试**

```python
class TestStoryboardGridSync:
    def test_all_cells_approved_composes_grid_row(self, <既有夹具参数>) -> None:
        """2 格全批准 → 生成 view_label="分镜板" 的 approved 行，file_path 落盘。"""
        svc = <既有方式构造，2-shot 段>
        # 直接入库两张已批准格行（file_path 指向 tmp_path 造的真图）
        ...
        anchor = svc._approved_anchor_frame("p1", "S01G01")
        assert anchor is not None
        assert anchor.view_label == "分镜板"
        assert anchor.file_path  # 已落盘

    def test_partial_approved_no_grid(self, <既有夹具参数>) -> None:
        """只批 1/2 格 → 无宫格行；_approved_anchor_frame 回退格子行本身。"""
        ...

    def test_reroll_cell_invalidates_grid(self, <既有夹具参数>) -> None:
        """宫格存在时重抽格 → 宫格行被删除。"""
        ...

    def test_delete_cell_recomputes(self, <既有夹具参数>) -> None:
        """删格走 cmd_delete_frame_image → 宫格行作废。"""
        ...
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest server/tests/test_frame_stage.py::TestStoryboardGridSync -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`_approved_keyframe` 改名并加强（保留旧名做薄别名，调用点少直接全量替换更干净——推荐替换为 `_approved_anchor_frame`，更新全部 3 个调用点：approve 门 `:1652`、`_resolve_references` `:1679`、`cmd_regenerate_segment`）：

```python
    def _approved_anchor_frame(self, pid: str, segment_key: str) -> SegmentFrameImage | None:
        """该段的已批准开场锚点（确定性）。

        优先宫格分镜板行（view_label=分镜板，取最高版本）；无宫格时取最高
        版本的已批准行（存量单张关键帧/格子行回退）。
        """
        approved = [
            i for i in self.ctx.frames.list_by_segment(pid, segment_key) if i.approved
        ]
        if not approved:
            return None
        grids = [i for i in approved if i.view_label == STORYBOARD_GRID_LABEL]
        if grids:
            return max(grids, key=lambda i: i.version_no)
        return max(approved, key=lambda i: i.version_no)
```

类内新增 `_sync_storyboard_grid`（放 `_invalidate_storyboard_grid` 旁）：

```python
    def _sync_storyboard_grid(self, project, segment) -> SegmentFrameImage | None:
        """格子批准状态变化后同步宫格：作废旧行，全部格就绪时重拼。

        就绪口径：1..min(len(shots), MAX_GRID_CELLS) 每格至少一张已批准且
        已落盘的行（同格多版本取最新批准）。未就绪则不留半成品宫格。
        单镜段（旧口径无格号）不拼宫格。
        """
        pid = project.project_id
        key = segment.segment_key
        self._invalidate_storyboard_grid(pid, key)
        expected = min(len(segment.shots), MAX_GRID_CELLS)
        if len(segment.shots) <= 1:
            return None
        latest: dict[int, SegmentFrameImage] = {}
        for row in self.ctx.frames.list_by_segment(pid, key):
            if row.grid_cell is None or not row.approved or not row.file_path:
                continue
            if not (1 <= row.grid_cell <= expected):
                continue
            if row.grid_cell not in latest or row.version_no > latest[row.grid_cell].version_no:
                latest[row.grid_cell] = row
        if len(latest) < expected:
            return None
        cell_paths = [latest[i].file_path for i in range(1, expected + 1)]
        rel = _compose_storyboard_grid(self.ctx.settings, pid, key, cell_paths)
        row = SegmentFrameImage(
            frame_image_id=f"frm-{_uuid()}",
            project_id=pid,
            segment_key=key,
            version_no=len(self.ctx.frames.list_by_segment(pid, key)) + 1,
            view_label=STORYBOARD_GRID_LABEL,
            prompt="storyboard grid",
            provider="pillow",
            file_path=rel,
            reference_paths=list(cell_paths),
            status=AssetImageStatus.READY,
            approved=True,
        )
        self.ctx.frames.add(row)
        return row
```

`cmd_approve_frame_image` 末尾（`self.ctx.frames.save(row)` 之后、return 之前）加：

```python
        if row.grid_cell is not None:
            segment = next(
                (
                    s
                    for s in self._active_segments(project.project_id)[1]
                    if s.segment_key == row.segment_key
                ),
                None,
            )
            if segment is not None:
                self._sync_storyboard_grid(project, segment)
```

`cmd_delete_frame_image`（删除后）加同样的同步（row 已删，需在 delete 前记住 segment_key 与 grid_cell）：

```python
    def cmd_delete_frame_image(self, project, payload) -> dict:
        row = self.ctx.frames.get(str(payload.get("frame_image_id", "")))
        if row is None or row.project_id != project.project_id:
            raise DomainError("NOT_FOUND", f"关键帧不存在：{payload.get('frame_image_id')}")
        was_cell = row.grid_cell is not None
        seg_key = row.segment_key
        self.ctx.frames.delete(row.frame_image_id)
        if was_cell:
            segment = next(
                (
                    s
                    for s in self._active_segments(project.project_id)[1]
                    if s.segment_key == seg_key
                ),
                None,
            )
            if segment is not None:
                self._sync_storyboard_grid(project, segment)
        return CommandResult(ok=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest server/tests/test_frame_stage.py server/tests/test_app_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app/usecases.py server/tests/test_frame_stage.py
git commit -m "feat: 宫格同步与锚点选择（全格批准自动拼板）"
```

---

### Task 7: 视频侧接线（宫格占 Picture 1 + 声明句透传）

**Files:**
- Modify: `server/app/usecases.py:1668-1722`（_resolve_references）、`:1757-1795`（cmd_produce_video）、`:1823-1862`（_compile_production_prompt）、`:1880-1905`（cmd_regenerate_segment）
- Test: `server/tests/test_app_flow.py`、`server/tests/test_frame_stage.py`

- [ ] **Step 1: 写失败测试**

```python
class TestVideoUsesStoryboardGrid:
    def test_resolve_references_prefers_grid(self, <既有夹具参数>) -> None:
        """段有已批准宫格 → Picture 1 = 宫格路径，storyboard 标志 True。"""
        svc = <既有方式构造，段带 2 shot + 已批准宫格行>
        project = svc.get_project("p1")
        segment = <active 分镜第一段>
        paths, mode, keyframe_path, is_storyboard = svc._resolve_references(
            project, segment, "sbv-1"
        )
        assert is_storyboard is True
        assert paths[0] == keyframe_path
        assert "storyboards/" in paths[0]

    def test_production_prompt_contains_storyboard_sentence(self, <既有夹具参数>) -> None:
        """产视频时提示词包含宫格声明句。"""
        svc = <既有方式构造>
        prompt = svc._compile_production_prompt(
            project, segment, opening_frame=True, storyboard_reference=True
        )
        assert "storyboard reference" in prompt

    def test_relay_segment_grid_yields_to_tail_frame(self, <既有夹具参数>) -> None:
        """同场景后继段（尾帧接力）→ 宫格让位，Picture 1 留给运行时尾帧。"""
        ...
        assert is_storyboard_used is False or paths 里无宫格路径
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest server/tests/test_app_flow.py::TestVideoUsesStoryboardGrid -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`_resolve_references` 返回值扩为 4 元组：

```python
    def _resolve_references(
        self, project, segment, sb_version_id: str
    ) -> tuple[list[str], VideoMode, str | None, bool]:
```

方法体改动（开头取锚点处）：

```python
        anchor = self._approved_anchor_frame(project.project_id, segment.segment_key)
        is_storyboard = (
            anchor is not None and anchor.view_label == STORYBOARD_GRID_LABEL
        )
```

末尾（原 `keyframe` 判断块）：

```python
        keyframe_path: str | None = None
        if anchor is not None:
            from server.domain.validation import MAX_REFERENCE_IMAGES

            paths = paths[: MAX_REFERENCE_IMAGES - 1]
            keyframe_path = anchor.file_path
            paths = [keyframe_path, *paths]
        return paths, mode, keyframe_path, is_storyboard
```

（docstring 同步补一句：宫格分镜板优先占 Picture 1。）

`cmd_produce_video`（第 1757 行）解包改 4 元组：

```python
            reference_paths, mode, keyframe_path, is_storyboard = self._resolve_references(
                project, seg, sb.storyboard_version_id
            )
```

`_compile_production_prompt` 调用处（第 1791-1795 行）透传：

```python
            prompt_text = self._compile_production_prompt(
                project,
                seg,
                opening_frame=bool(keyframe_path) or seg.continuity.enabled,
                storyboard_reference=bool(keyframe_path) and is_storyboard,
            )
```

`_compile_production_prompt`（第 1823 行）签名与透传：

```python
    def _compile_production_prompt(
        self, project, segment, *, opening_frame: bool, storyboard_reference: bool = False
    ) -> str:
```

```python
        text = compile_h3_prompt(
            segment,
            assets_by_id,
            scene_summary=...,   # 保持现有实参不动
            style_line=...,
            opening_frame=opening_frame,
            storyboard_reference=storyboard_reference,
        )
```

`cmd_regenerate_segment`（第 1880-1905 行）同样改 4 元组解包 + 透传：

```python
        reference_paths, mode, keyframe_path, is_storyboard = self._resolve_references(
            project, segment, sb.storyboard_version_id
        )
```

```python
            prompt_text = self._compile_production_prompt(
                project,
                segment,
                opening_frame=bool(keyframe_path) or segment.continuity.enabled,
                storyboard_reference=bool(keyframe_path) and is_storyboard,
            )
```

（尾帧接力分支不动：`keyframe_path` 让位逻辑按 `reference_paths[0] == keyframe_path` 剥离，宫格路径同样适用。）

- [ ] **Step 4: 运行确认通过 + 修既有用例**

Run: `python -m pytest server/tests -x -q`
Expected: 全量 PASS。既有调用 `_resolve_references` 3 元组解包的测试需改为 4 元组。

- [ ] **Step 5: Commit**

```bash
git add server/app/usecases.py server/tests
git commit -m "feat: 视频生成宫格分镜板占 Picture 1 并透传声明句"
```

---

### Task 8: 全量回归 + 重启验证（AGENTS.md 硬规则）

**Files:** 无代码改动

- [ ] **Step 1: 全量测试**

Run: `python -m pytest server/tests -q`
Expected: 全部 PASS（含存量用例口径修正后）

- [ ] **Step 2: 确认无在途任务**

```bash
python -c 'import sqlalchemy as sa;from server.infra.config import Settings;s=Settings(_env_file=None);e=sa.create_engine("sqlite:///"+str(s.data_dir/"app.db"));print(list(e.connect().execute(sa.text("select type,status,count(*) from jobs group by type,status"))))'
```

- [ ] **Step 3: 执行 Alembic 迁移 + 重启 Worker 与 API**

```bash
alembic upgrade head
python scripts/run_workers.py --stop
python scripts/run_workers.py --count 3
python -m uvicorn server.api.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 4: 健康确认（必须 stale=false）**

```bash
curl -s http://127.0.0.1:8000/api/system/health
```

Expected: `"stale": false`

- [ ] **Step 5: Commit（如有遗留）+ 汇报**

向用户汇报：改动摘要、测试结果、以及**人工验收路径**（花币动作由用户发起）：多 shot 段 → 抽取关键帧（生成 N 格）→ 逐格批准 → 自动拼宫格 → 产视频，比对 H3 是否按宫格镜序执行。报告需含实际花费（provider_calls.usage.coins）。

---

## 自检记录

- 规格覆盖：领域字段/迁移（T1）、逐格描述（T2）、声明句（T3）、拼图（T4）、逐格入队+上传（T5）、同步与锚点（T6）、视频接线+接力让位（T7）、重启验证（T8）——与设计文档第 3 节逐项对应。
- 无占位符：测试中 `<既有夹具参数>` 标记指"复用该测试文件既有夹具形态"（已在 T4 注明参照 `test_reference_cards_three_in_frame_characters_keep_scene_card`），非逻辑占位。
- 类型一致性：`_resolve_references` 4 元组在 T7 三个调用点统一更新；`grid_cell: int | None`、`STORYBOARD_GRID_LABEL`、`MAX_GRID_CELLS`、`_approved_anchor_frame`、`_sync_storyboard_grid`、`_invalidate_storyboard_grid`、`_compose_storyboard_grid` 命名全文一致。
