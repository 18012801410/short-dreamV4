"""用例/命令编排（TASK-008-012）：REST 命令端点背后的全部领域操作。

规则：命令由应用层校验前置状态（经领域状态机）后执行；生成类命令入队 jobs；
确认门做前置检查（approve_assets 的关键资产图、produce_video 的 REF_MISSING）。
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from pydantic import ValidationError

from server.app.context import AppContext
from server.app.handlers import new_id
from server.app.media import (
    abs_media_path,
    reference_sheet_rel,
    segment_frame_rel,
    segment_video_rel,
    size_for_asset_card,
    storyboard_rel,
    uploaded_frame_rel,
    uploaded_image_rel,
)
from server.domain.entities import (
    Asset,
    AssetImage,
    Job,
    JobType,
    Project,
    ProjectParams,
    ScriptContent,
    ScriptVersion,
    Segment,
    SegmentFrameImage,
    Series,
    SeriesOutline,
    SeriesParams,
    STORYBOARD_GRID_LABEL,
    StoryboardContent,
    StoryboardVersion,
    utcnow,
)
from server.domain.enums import (
    AssetImageStatus,
    AssetKind,
    JobStatus,
    ProjectStatus,
    SeriesStatus,
    Stage,
    VideoMode,
    WorkStatus,
)
from server.domain.errors import (
    DomainError,
    ReferenceMissingError,
    StateIllegalError,
    ValidationFailedError,
)
from server.domain.outline_quality import hard_gate_issues, validate_series_outline
from server.domain.project import apply_action, apply_stage_invalidated
from server.domain.textnorm import negative_for_kind
from server.domain.validation import (
    MAX_GRID_CELLS,
    truncate_references,
    validate_asset_set,
    validate_segment,
)


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


def _random_seed() -> int:
    """图生图随机种子（TASK-048 用户反馈：不传种子时工作流用保存的固定种子，
    「重新生成」会出一模一样的图）。显式传 seed 仍可锁定效果复现。"""
    return random.randint(0, 2**31 - 1)


# 拼合设定表单张最多容纳的资产卡数（过多会被 VL 编码缩得看不清，超出舍弃）
REFERENCE_SHEET_MAX = 4


def _compose_reference_sheet(settings, project_id: str, rel_paths: list[str]) -> str:
    """把多张资产卡横向拼成一张「参考设定表」，落盘 media 目录并返回相对路径。

    Edit 工作流参考槽硬上限 3 张（`TextEncodeQwenImageEditPlus` 节点只收
    image1/2/3），关键帧在场资产超过 3 个时，把多余卡片按序横拼成一张设定表
    占用第 3 槽（Qwen-VL 对 side-by-side character sheet 的理解可用）。
    文件名取路径列表摘要：同组卡片复用同一张拼图，重复生成不重复落盘。
    """
    import hashlib

    from PIL import Image

    digest = hashlib.md5("\n".join(rel_paths).encode("utf-8")).hexdigest()[:16]
    rel = reference_sheet_rel(project_id, digest)
    dest = abs_media_path(settings, rel)
    if dest.exists():
        return rel
    images = []
    for p in rel_paths:
        with Image.open(abs_media_path(settings, p)) as im:
            images.append(im.convert("RGB"))
    # 等高缩放（取中位高度为基准，避免单张超高图把其他卡压得过小），白底左起拼接
    target_h = sorted(im.height for im in images)[len(images) // 2]
    scaled = [
        im.resize((max(1, round(im.width * target_h / im.height)), target_h))
        for im in images
    ]
    gap = 24
    width = sum(im.width for im in scaled) + gap * (len(scaled) - 1)
    canvas = Image.new("RGB", (width, target_h), (255, 255, 255))
    x = 0
    for im in scaled:
        canvas.paste(im, (x, 0))
        x += im.width + gap
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, quality=92)
    return rel


def _compose_storyboard_grid(
    settings, project_id: str, segment_key: str, cell_rel_paths: list[str]
) -> str:
    """把该段已批准的格子拼成多宫格分镜板，落盘 media 目录并返回相对路径（方案A）。

    布局：storyboard_grid_cols 列、按格号顺序排布、白底 12px 分隔、每行居中。
    格子由同尺寸生图产出（size_for_asset_card），天然对齐；防御性等比缩放。
    文件名取「列数+格路径」摘要：同组格子同配置复用同一张宫格；改列数后
    digest 变化，重拼新图而非复用旧列数的宫格。
    """
    import hashlib

    from PIL import Image

    if not cell_rel_paths:
        raise ValueError("storyboard grid 需要至少一张格子图")
    # 列数参与布局也参与摘要：先于 digest 与缩放定好，避免 getattr 重复读取
    cols = max(1, int(getattr(settings, "storyboard_grid_cols", 3)))
    digest = hashlib.md5(
        (f"cols={cols}\n" + "\n".join(cell_rel_paths)).encode("utf-8")
    ).hexdigest()[:16]
    rel = storyboard_rel(project_id, segment_key, digest)
    dest = abs_media_path(settings, rel)
    if dest.exists():
        return rel
    images = []
    for p in cell_rel_paths:
        with Image.open(abs_media_path(settings, p)) as im:
            images.append(im.convert("RGB"))
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


_PRIMARY_LABEL_HINTS = ("主设定", "空镜")


def _pick_primary_image(approved: list, kind: AssetKind):
    """从已批准图中选一张作该资产的 <Picture N> 身份锚。

    先按标签层（主设定/空镜优先），同层取**最新批准**的一张（approved 按
    created_at 升序输入，取末位）——用户重画并批准新图后，后续关键帧/视频
    自动改用新形象（TASK-048 用户反馈：旧实现"不取最新"，资产更新后
    关键帧仍引用旧图）。
    """
    for hint in _PRIMARY_LABEL_HINTS:
        matching = [image for image in approved if hint in image.view_label]
        if matching:
            return matching[-1]
    return approved[-1]


class CommandResult(dict):
    """{"jobs": [...]}（异步）或 {"ok": True, "project": {...}}（同步）。"""


class WorkbenchService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        # 中文→英文提示词翻译用的 LLM（懒加载缓存；测试可注入假 LLM）
        self._prompt_llm = None

    # ------------------------------------------------------------------
    # 项目
    # ------------------------------------------------------------------

    def create_project(self, title: str, idea: str, params: dict | None = None) -> Any:
        parsed = ProjectParams(**(params or {}))
        project = Project(
            project_id=new_id("p"), title=title, idea=idea, params=parsed
        )
        self.ctx.projects.create(project)
        return project

    def get_project(self, pid: str) -> Any:
        project = self.ctx.projects.get(pid)
        if project is None:
            raise DomainError("NOT_FOUND", f"项目不存在：{pid}")
        return project

    def list_projects(self, include_archived: bool = False) -> list:
        projects = self.ctx.projects.list_all()
        if include_archived:
            return projects
        return [p for p in projects if p.status is not ProjectStatus.ARCHIVED]

    def delete_project(self, pid: str) -> dict:
        """硬删除项目（用户显式指令）：DB 全部关联行级联清除 + 媒体文件归档回收。

        - 有 pending/running 任务时拒绝：在途任务写回已删项目会造成脏数据，
          且视频/图片任务的回调轮询会因项目缺失而错乱（先取消或等跑完再删）。
        - 媒体不物理删除：`data/media/{pid}` 整目录移入 `data/trash/{pid}_{时间戳}`
          （工作区规则：删用户数据前留可校验的备份），确认无需恢复后可手动清空。
        """
        project = self.get_project(pid)
        busy = [
            j for j in self.ctx.jobs.list_all()
            if j.project_id == pid
            and j.status in (JobStatus.PENDING, JobStatus.RUNNING)
        ]
        if busy:
            raise DomainError(
                "STATE_ILLEGAL",
                f"项目有 {len(busy)} 个进行中/排队任务，请先取消或等其结束后再删除",
                details={"job_ids": [j.job_id for j in busy]},
            )
        counts = self.ctx.projects.delete_project_cascade(pid)
        media_src = self.ctx.settings.media_dir / pid
        moved_to = ""
        if media_src.exists():
            import shutil
            import time

            trash_root = self.ctx.settings.data_dir / "trash"
            trash_root.mkdir(parents=True, exist_ok=True)
            moved_to = str(trash_root / f"{pid}_{time.strftime('%Y%m%d_%H%M%S')}")
            shutil.move(str(media_src), moved_to)
        return {
            "project_id": pid,
            "title": project.title,
            "deleted_rows": counts,
            "media_moved_to": moved_to,
        }

    def translate_prompt(
        self, pid: str, text: str, mode: str = "t2i", current_prompt: str = ""
    ) -> str:
        """中文修改要求 → 融入现有提示词的新提示词（TASK-048 用户反馈：不会写
        提示词，只想说「场景中有 2 个桌子」这种改动）。

        按 Qwen-Image 官方规则组织；给了 current_prompt 就走「智能修改」——
        只落实用户要求的改动，其余逐字保留；没给就按公式生成完整提示词。
        edit 模式（关键帧图生图）输出 Qwen-Image-Edit 指令式编辑。
        LLM 免费调用；不落库——结果由页面填进提示词框确认后使用。
        """
        project = self.get_project(pid)
        cleaned = text.strip()
        if not cleaned:
            raise ValidationFailedError("中文描述为空")
        if self._prompt_llm is None:
            from server.adapters.llm import build_llm_from_settings

            self._prompt_llm = build_llm_from_settings(self.ctx.settings)
        style = project.params.style.strip() or "cinematic, photorealistic"
        base_prompt = current_prompt.strip()
        if base_prompt:
            system = (
                "你是 Qwen-Image 提示词编辑助手。用户给出现有提示词和一条中文"
                "修改要求，输出融合修改后的完整新提示词。规则：\n"
                "1. 只落实修改要求提到的改动，现有提示词的其余内容逐字保留，"
                "不得增删其他细节；\n"
                "2. 改动要精确并消除冲突：如要求「2 个桌子」，就把桌子写成 two "
                "tables 并移除原文里 a single table 这类冲突描述；\n"
                "3. 画面里要「写出来」的文字保留中文原文绝不翻译：英文双引号包裹"
                '并注明 Chinese characters 与字体颜色位置，如 the title reads '
                '"离婚协议书" in bold black Chinese characters；\n'
                "4. 按 Qwen-Image 官方公式组织：主体（特征+动作）→场景→风格→"
                "镜头语言→氛围→画质词；\n"
                "5. 60-120 词，只输出新提示词，不要解释。\n"
            )
            user = f"现有提示词：{base_prompt}\n修改要求：{cleaned}"
        elif mode == "edit":
            system = (
                "你是 Qwen-Image-Edit（图生图编辑模型）的提示词工程师。"
                "把用户的中文修改意图改写成一条英文编辑指令，规则：\n"
                "1. 用明确的编辑动词开头（Change / Add / Remove / Replace / Make ...），"
                "只描述要改什么，一句话说完；\n"
                "2. 指令里出现的具体物件与人物要沿用参考图里已有的外观描述；\n"
                "3. 画面里要「写出来」的文字保留中文原文绝不翻译：用英文双引号包裹并"
                '注明 Chinese characters，如 the sign reads "寿宴" in gold Chinese characters；\n'
                "4. 指令末尾固定加一句：Keep everything else in the reference image "
                "exactly unchanged (composition, identity, clothing, lighting).\n"
                "5. 20-60 词，不要解释性文字。\n"
            )
            user = f"修改意图：{cleaned}\n画面风格：{style}"
        else:
            system = (
                "你是通义千问 Qwen-Image 的绘图提示词工程师，按官方公式改写用户描述：\n"
                "主体（外观特征+动作姿态）→ 场景（室内外/天气/光线）→ 风格 → "
                "镜头语言（景别 wide/medium/close-up shot + 视角 eye-level/low angle）"
                "→ 氛围 → 细节修饰（画质词如 highly detailed, 8k）。"
                "用自然通顺的英文短句、逗号分隔（Qwen 理解自然语言，不要 SD 式"
                "关键词堆砌）。规则：\n"
                "1. 主体优先，用户的细节设定（人物特征、服装、道具、场景元素）逐项"
                "保留，不得增删；\n"
                "2. 场景里要「写出来」的文字（文件标题、招牌、横幅、屏幕上的字等）"
                "保留中文原文绝不翻译：用英文双引号包裹并注明 Chinese characters 与"
                '字体/颜色/位置，如 the document title reads "离婚协议书" in bold '
                "black Chinese characters at the top；\n"
                "3. 60-120 词；不要解释性文字，不要用引号包裹整条提示词。\n"
            )
            user = (
                f"画面描述：{cleaned}\n"
                f"画面风格：{style}"
            )
        reply = str(self._prompt_llm.chat(system=system, user=user, temperature=0.3))
        prompt = reply.strip().strip('"').strip()
        if not prompt:
            raise ValidationFailedError("翻译结果为空")
        return prompt

    # ------------------------------------------------------------------
    # 命令分发
    # ------------------------------------------------------------------

    # 生成类动作 → 其任务类型：重入护栏用（TASK-032）。领域层已放开
    # drafting→drafting 迁移以支持失败恢复，真正的"是否忙碌"证据是任务表。
    _GENERATION_JOB_TYPES = {
        "generate_script": JobType.SCRIPT_GEN,
        "generate_assets": JobType.ASSET_EXTRACT,
        "generate_storyboard": JobType.STORYBOARD_GEN,
        "generate_keyframes": JobType.FRAME_GEN,
    }

    # 付费命令（TASK-044）：这些动作会真的下单扣币，且其提示词由**进程内的代码**
    # 现场撰写/编译（资产卡口径、关键帧编辑指令、H3 六段）。进程代码过期时按旧规则
    # 生成、钱照扣、页面无异常——只能靠重启解决，所以在派发前硬拦。
    _PAID_COMMANDS = {
        "generate_assets",       # AssetAgent 写资产卡提示词 + 12 张图
        "generate_asset_image",  # 资产图
        "generate_keyframes",    # 关键帧图（compile_keyframe_prompt 编译）
        "generate_frame_image",  # 单张关键帧重抽
        "produce_video",         # H3 视频（compile_h3_prompt 编译）
        "regenerate_segment",    # 单段重生成
    }

    def _guard_stale_code(self, command_type: str) -> None:
        """代码过期护栏（TASK-044）：付费命令在进程加载了旧代码时直接拒绝。

        进程启动后修改的源码不会生效（uvicorn/worker 都没开 --reload），
        实测代价是"按旧资产卡口径生成 6 张卡、白花约 138 币且页面无异常"。
        逃生口：确知改动与本动作无关时设 ALLOW_STALE_CODE=1。
        """
        if command_type not in self._PAID_COMMANDS:
            return
        from server.infra.buildinfo import freshness

        fresh = freshness()
        if fresh.stale:
            raise DomainError("STALE_CODE", fresh.describe())

    def _guard_reentrant_generation(self, project, command_type: str) -> None:
        """生成类动作重入护栏（TASK-032）。

        生成 Job 终态失败会把项目留在 drafting 忙碌态，领域层必须放开重入才
        能恢复（「超神奶爸」分镜 Job 连败 3 次后无法重新派发的实测卡死）；但
        重复派发不能放开——关键帧这类阶段重入就是重复烧图钱。因此这里以任务
        表为准：仍有本阶段 pending/running 任务时拒绝，上次已终结（完成/失败/
        取消）才放行。
        """
        job_type = self._GENERATION_JOB_TYPES.get(command_type)
        if job_type is None:
            return
        active = [
            job
            for status in (JobStatus.PENDING, JobStatus.RUNNING)
            for job in self.ctx.jobs.list_by_status(status)
            if job.project_id == project.project_id and job.type is job_type
        ]
        if active:
            raise StateIllegalError(
                f"上一批 {command_type} 任务仍在执行（{len(active)} 个未终结），"
                "请等待完成或先取消后再重新生成"
            )

    def dispatch(self, pid: str, command_type: str, payload: dict | None = None) -> CommandResult:
        payload = payload or {}
        project = self.get_project(pid)
        self._guard_stale_code(command_type)
        self._guard_reentrant_generation(project, command_type)
        method = {
            "generate_script": self.cmd_generate_script,
            "edit_script_draft": self.cmd_edit_script_draft,
            "approve_script": self.cmd_approve_script,
            "generate_assets": self.cmd_generate_assets,
            "create_asset": self.cmd_create_asset,
            "edit_asset": self.cmd_edit_asset,
            "update_params": self.cmd_update_params,
            "generate_asset_image": self.cmd_generate_asset_image,
            "delete_asset_image": self.cmd_delete_asset_image,
            "upload_asset_image": self.cmd_upload_asset_image,
            "approve_asset_image": self.cmd_approve_asset_image,
            "approve_assets": self.cmd_approve_assets,
            "generate_storyboard": self.cmd_generate_storyboard,
            "edit_segment": self.cmd_edit_segment,
            "approve_storyboard": self.cmd_approve_storyboard,
            "generate_keyframes": self.cmd_generate_keyframes,
            "generate_frame_image": self.cmd_generate_frame_image,
            "upload_frame_image": self.cmd_upload_frame_image,
            "delete_frame_image": self.cmd_delete_frame_image,
            "approve_frame_image": self.cmd_approve_frame_image,
            "approve_keyframes": self.cmd_approve_keyframes,
            "produce_video": self.cmd_produce_video,
            "regenerate_segment": self.cmd_regenerate_segment,
            "compose": self.cmd_compose,
            "run_to_next_gate": self.cmd_run_to_next_gate,
            "archive_project": self.cmd_archive_project,
        }.get(command_type)
        if method is None:
            raise DomainError("VALIDATION_ERROR", f"未知命令：{command_type}")
        return method(project, payload)

    # ------------------------------------------------------------------
    # 系列分集（TASK-047）：大纲驱动，每集一个普通 project，复用既有流水线
    # ------------------------------------------------------------------

    def create_series(self, title: str, idea: str, params: dict | None = None) -> Series:
        parsed = SeriesParams(**(params or {})) if params else SeriesParams()
        series_row = Series(
            series_id=new_id("sr"), title=title.strip() or "未命名系列", idea=idea, params=parsed
        )
        self.ctx.series.add(series_row)
        return series_row

    def get_series(self, sid: str) -> Series:
        series_row = self.ctx.series.get(sid)
        if series_row is None:
            raise DomainError("NOT_FOUND", f"系列不存在：{sid}")
        return series_row

    def list_series(self) -> list[Series]:
        return self.ctx.series.list_all()

    def series_episodes(self, sid: str) -> list[dict]:
        """分集视图：大纲梗概卡 × 集项目状态（系列页列表用）。"""
        series_row = self.get_series(sid)
        projects = {p.episode_no: p for p in self.ctx.projects.list_by_series(sid)}
        episodes: list[dict] = []
        if series_row.outline is not None:
            for ep in series_row.outline.episodes:
                project = projects.get(ep.episode_no)
                episodes.append(
                    {
                        "episode_no": ep.episode_no,
                        "title": ep.title,
                        "opening_hook": ep.opening_hook,
                        "synopsis": ep.synopsis,
                        "highlight": ep.highlight,
                        "ending_hook": ep.ending_hook,
                        # Project 实体（未建集为 None）：由路由层序列化
                        "project": project,
                    }
                )
        return episodes

    def dispatch_series(self, sid: str, command_type: str, payload: dict | None = None) -> CommandResult:
        payload = payload or {}
        series_row = self.get_series(sid)
        method = {
            "generate_series_outline": self.cmd_generate_series_outline,
            "edit_series_outline": self.cmd_edit_series_outline,
            "approve_series_outline": self.cmd_approve_series_outline,
            "generate_episode_scripts": self.cmd_generate_episode_scripts,
            "inherit_series_assets": self.cmd_inherit_series_assets,
        }.get(command_type)
        if method is None:
            raise DomainError("VALIDATION_ERROR", f"未知系列命令：{command_type}")
        return method(series_row, payload)

    def cmd_generate_series_outline(self, series_row: Series, payload: dict) -> CommandResult:
        active = [
            job
            for status in (JobStatus.PENDING, JobStatus.RUNNING)
            for job in self.ctx.jobs.list_by_status(status)
            if job.project_id == series_row.series_id
            and job.type is JobType.SERIES_OUTLINE_GEN
        ]
        if active:
            raise StateIllegalError(
                f"上一轮大纲生成仍在执行（{len(active)} 个未终结），请等待完成后再重新生成"
            )
        moved = series_row.model_copy(
            update={"status": SeriesStatus.OUTLINE_DRAFTING, "updated_at": utcnow()}
        )
        job = Job(
            job_id=new_id("job"),
            project_id=series_row.series_id,
            type=JobType.SERIES_OUTLINE_GEN,
            payload={"series_id": series_row.series_id},
        )
        self.ctx.jobs.insert(job)
        self.ctx.series.save(moved)
        return CommandResult(jobs=[job])

    def cmd_edit_series_outline(self, series_row: Series, payload: dict) -> CommandResult:
        raw = payload.get("outline") if isinstance(payload.get("outline"), dict) else payload
        try:
            outline = SeriesOutline.model_validate(raw)
        except ValidationError as exc:
            raise ValidationFailedError(
                "大纲 JSON 不符合 schema", details={"errors": exc.errors()[:8]}
            ) from exc
        # 每次编辑都重跑确定性质量门（软硬门结果都在 warnings 里展示）
        issues = validate_series_outline(
            outline, expected_episodes=series_row.params.episode_count
        )
        outline = outline.model_copy(update={"warnings": issues})
        update: dict = {"outline": outline, "updated_at": utcnow()}
        if series_row.status in {SeriesStatus.CREATED, SeriesStatus.OUTLINE_APPROVED}:
            # 手写首稿直接进入评审；已确认后再改 → 回到待确认重新过门
            update["status"] = SeriesStatus.OUTLINE_READY
        series_row = series_row.model_copy(update=update)
        self.ctx.series.save(series_row)
        return CommandResult(ok=True, series=series_row)

    def cmd_approve_series_outline(self, series_row: Series, payload: dict) -> CommandResult:
        if series_row.status is not SeriesStatus.OUTLINE_READY:
            raise StateIllegalError(
                f"当前状态 {series_row.status.value} 不可确认大纲（需先有可评审的大纲草稿）"
            )
        outline = series_row.outline
        if outline is None:
            raise DomainError("NOT_FOUND", "没有可确认的大纲")
        hard = hard_gate_issues(
            outline, expected_episodes=series_row.params.episode_count
        )
        if hard:
            raise ValidationFailedError(
                "大纲质量门未通过，先修订再确认（软门风险在 warnings 里提示，不阻断）",
                details={"issues": hard},
            )
        moved = series_row.model_copy(
            update={"status": SeriesStatus.OUTLINE_APPROVED, "updated_at": utcnow()}
        )
        self.ctx.series.save(moved)
        return CommandResult(ok=True, series=moved)

    def cmd_generate_episode_scripts(self, series_row: Series, payload: dict) -> CommandResult:
        """为缺项目的集批量建档并入队剧本生成（每集一个 job，独立重试）。"""
        if series_row.status is not SeriesStatus.OUTLINE_APPROVED or series_row.outline is None:
            raise DomainError(
                "STATE_ILLEGAL", "先确认系列大纲，再批量生成分集剧本"
            )
        outline = series_row.outline
        requested = payload.get("episode_nos")
        numbers = (
            [int(n) for n in requested]
            if isinstance(requested, list) and requested
            else [ep.episode_no for ep in outline.episodes]
        )
        existing = {
            p.episode_no: p for p in self.ctx.projects.list_by_series(series_row.series_id)
        }
        jobs: list[Job] = []
        for no in numbers:
            episode = next((e for e in outline.episodes if e.episode_no == no), None)
            if episode is None:
                raise DomainError("NOT_FOUND", f"大纲没有第 {no} 集")
            if no in existing:
                continue  # 已建集不重建；重写剧本走该项目自身的 generate_script
            params = ProjectParams(
                genre=series_row.params.genre,
                style=series_row.params.style,
                dramatic_tone=series_row.params.dramatic_tone,
                target_duration_sec=series_row.params.per_episode_sec,
                scene_count=series_row.params.scene_count,
                ratio=series_row.params.ratio,
                resolution=series_row.params.resolution,
                prompt_lang=series_row.params.prompt_lang,
            )
            project = Project(
                project_id=new_id("p"),
                title=f"{outline.title}·第{no:02d}集·{episode.title}",
                idea=series_row.idea,
                params=params,
                series_id=series_row.series_id,
                episode_no=no,
            )
            self.ctx.projects.create(project)
            moved = apply_action(project, "generate_script")
            job = Job(
                job_id=new_id("job"),
                project_id=project.project_id,
                type=JobType.SCRIPT_GEN,
                payload={
                    "project_id": project.project_id,
                    "series_id": series_row.series_id,
                    "episode_no": no,
                },
            )
            self.ctx.jobs.insert(job)
            self.ctx.projects.save(moved)
            jobs.append(job)
        if not jobs:
            raise DomainError(
                "STATE_ILLEGAL",
                "所选集数都已有项目；要重写某一集的剧本，请在该项目工作台里重新生成剧本",
            )
        return CommandResult(jobs=jobs)

    def cmd_inherit_series_assets(self, series_row: Series, payload: dict) -> CommandResult:
        """把来源集的资产卡（含图片行）整套复制到目标集。

        图片文件是同一份磁盘文件（file_path 原样复制），零生成成本；角色形象
        跨集一致。要求目标集已确认剧本（SCRIPT_APPROVED）——资产挂在剧本之后；
        复制后目标集直接落到 ASSET_READY，走正常「确认资产」门。
        """
        source_no = int(payload.get("from_episode_no") or 1)
        target_no = int(payload.get("to_episode_no") or 0)
        if not target_no or target_no == source_no:
            raise DomainError("VALIDATION_ERROR", "需要 to_episode_no，且不能与来源集相同")
        by_no = {p.episode_no: p for p in self.ctx.projects.list_by_series(series_row.series_id)}
        source = by_no.get(source_no)
        target = by_no.get(target_no)
        if source is None or target is None:
            missing = []
            if source is None:
                missing.append(f"来源第 {source_no} 集（不存在）")
            if target is None:
                missing.append(f"目标第 {target_no} 集（不存在）")
            raise DomainError("NOT_FOUND", "缺少集项目：" + "、".join(missing))
        if target.status is not ProjectStatus.SCRIPT_APPROVED:
            raise StateIllegalError(
                f"继承资产要求目标集已确认剧本（当前 {target.status.value}）"
            )
        assets = self.ctx.assets.list_assets(source.project_id)
        if not assets:
            raise DomainError(
                "NOT_FOUND", f"来源集（第 {source_no} 集）还没有资产，请先完成资产抽取与确认"
            )
        # 整体替换：清目标旧卡再复制（与重新抽取同口径，避免两套卡叠加）
        copied_assets, copied_images = self._copy_series_assets(source, target)
        # SCRIPT_APPROVED → ASSET_DRAFTING → ASSET_READY（与抽取完成同态）
        moved = apply_action(apply_action(target, "generate_assets"), "assets_generated")
        self.ctx.projects.save(moved)
        return CommandResult(
            ok=True,
            project=moved,
            copied_assets=copied_assets,
            copied_images=copied_images,
        )

    # ------------------------------------------------------------------
    # 剧本（TASK-008）
    # ------------------------------------------------------------------

    def cmd_generate_script(self, project, payload) -> CommandResult:
        moved = apply_action(project, "generate_script")
        job = Job(
            job_id=new_id("job"),
            project_id=project.project_id,
            type=JobType.SCRIPT_GEN,
            payload={"project_id": project.project_id},
        )
        self.ctx.jobs.insert(job)
        self.ctx.projects.save(moved)
        return CommandResult(jobs=[job])

    def cmd_edit_script_draft(self, project, payload) -> CommandResult:
        try:
            content = ScriptContent.model_validate(payload)
        except ValidationError as exc:
            raise ValidationFailedError(
                "剧本 JSON 不符合 schema", details={"errors": exc.errors()[:8]}
            ) from exc
        # 已确认后编辑 → FR-016 失效回退
        if project.status in {
            ProjectStatus.SCRIPT_APPROVED,
            ProjectStatus.ASSET_DRAFTING,
            ProjectStatus.ASSET_READY,
            ProjectStatus.ASSET_APPROVED,
            ProjectStatus.STORYBOARD_DRAFTING,
            ProjectStatus.STORYBOARD_READY,
            ProjectStatus.STORYBOARD_APPROVED,
            ProjectStatus.VIDEO_PRODUCING,
            ProjectStatus.VIDEO_READY,
            ProjectStatus.COMPOSING,
            ProjectStatus.COMPOSED,
        }:
            project = apply_stage_invalidated(project, Stage.SCRIPT)
            self.ctx.projects.save(project)
        elif project.status is ProjectStatus.CREATED:
            # 手写首稿：进入剧本阶段
            project = apply_action(project, "generate_script")
            self.ctx.projects.save(project)
        version_no = len(self.ctx.scripts.list_by_project(project.project_id)) + 1
        version = ScriptVersion(
            script_version_id=f"sv-{_uuid()}",
            project_id=project.project_id,
            version_no=version_no,
            content=content,
            source="manual_edit",
        )
        self.ctx.scripts.add(version)
        if project.status is ProjectStatus.SCRIPT_DRAFTING:
            # 手写首稿没有生成 Job 来触发 script_generated，这里直接推进到 READY
            project = apply_action(project, "script_generated")
            self.ctx.projects.save(project)
        return CommandResult(ok=True, project=self.get_project(project.project_id))

    def cmd_approve_script(self, project, payload) -> CommandResult:
        moved = apply_action(project, "approve_script")  # 前置状态不合法 → STATE_ILLEGAL
        draft = self.ctx.scripts.latest_draft(project.project_id)
        if draft is None:
            raise DomainError("NOT_FOUND", "没有可确认的剧本草稿")
        old_active = self.ctx.scripts.active(project.project_id)
        if old_active is not None:
            old_active.status = WorkStatus.SUPERSEDED
            self.ctx.scripts.save(old_active)
        draft.status = WorkStatus.ACTIVE
        self.ctx.scripts.save(draft)
        self.ctx.projects.save(moved)
        return CommandResult(ok=True, project=self.ctx.projects.get(moved.project_id))

    # ------------------------------------------------------------------
    # 资产（TASK-009）
    # ------------------------------------------------------------------

    def cmd_generate_assets(self, project, payload) -> CommandResult:
        # 系列分集（TASK-047 用户拍板：一致性优先）：同系列已有带批准图的兄弟集时，
        # 「抽取资产」自动改为**免费继承**那套基准卡——全系列同一套脸，杜绝每集
        # 重抽导致的形象漂移与重复扣币。确实要从本集剧本重抽时传
        # payload.force_extract=true（LLM 重写卡片 + 重新生图扣币）。
        if project.series_id and not payload.get("force_extract"):
            source = self._series_asset_source(project)
            if source is not None:
                moved = apply_action(project, "generate_assets")
                copied_assets, copied_images = self._copy_series_assets(source, moved)
                moved = apply_action(moved, "assets_generated")
                self.ctx.projects.save(moved)
                return CommandResult(
                    ok=True,
                    project=moved,
                    inherited_from={"project_id": source.project_id, "title": source.title},
                    copied_assets=copied_assets,
                    copied_images=copied_images,
                )
        moved = apply_action(project, "generate_assets")
        job = Job(
            job_id=new_id("job"),
            project_id=project.project_id,
            type=JobType.ASSET_EXTRACT,
            payload={"project_id": project.project_id},
        )
        self.ctx.jobs.insert(job)
        self.ctx.projects.save(moved)
        return CommandResult(jobs=[job])

    def _series_asset_source(self, project):
        """系列资产基准集：同系列中「有已批准且已落盘图」的其他集，集号最小者优先。"""
        if not project.series_id:
            return None
        candidates = []
        for p in self.ctx.projects.list_by_series(project.series_id):
            if p.project_id == project.project_id:
                continue
            if any(
                i.approved and i.file_path
                for i in self.ctx.assets.list_images(project_id=p.project_id)
            ):
                candidates.append(p)
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.episode_no or 999)
        return candidates[0]

    def _copy_series_assets(self, source, target) -> tuple[int, int]:
        """把来源集资产卡整套复制到目标集（整体替换语义，与重新抽取同口径）。

        图片文件是同一份磁盘文件（file_path 原样复制），零生成成本、跨集形象
        一致。先清目标现有资产再复制，避免新旧两套卡叠加。
        """
        self.ctx.assets.delete_assets_for_project(target.project_id)
        assets = self.ctx.assets.list_assets(source.project_id)
        images = self.ctx.assets.list_images(project_id=source.project_id)
        id_map: dict[str, str] = {}
        for asset in assets:
            new_asset_id = new_id("asset")
            id_map[asset.asset_id] = new_asset_id
            self.ctx.assets.add_asset(
                asset.model_copy(update={"asset_id": new_asset_id, "project_id": target.project_id})
            )
        copied_images = 0
        for image in images:
            new_asset_id = id_map.get(image.asset_id)
            if new_asset_id is None:
                continue
            self.ctx.assets.add_image(
                image.model_copy(
                    update={"asset_image_id": new_id("img"), "asset_id": new_asset_id}
                )
            )
            copied_images += 1
        return len(assets), copied_images

    def _ensure_assets_editable(self, project) -> Any:
        """资产人工增改的状态门：资产阶段内直接改；已过资产门 → FR-016 失效回退。"""
        if project.status in {ProjectStatus.ASSET_DRAFTING, ProjectStatus.ASSET_READY}:
            return project
        if project.status in {
            ProjectStatus.ASSET_APPROVED,
            ProjectStatus.STORYBOARD_DRAFTING,
            ProjectStatus.STORYBOARD_READY,
            ProjectStatus.STORYBOARD_APPROVED,
            # 关键帧阶段（TASK-031）：FRAME_READY/APPROVED 允许补漏资产（如 LLM
            # 漏抽取的角色），按 FR-016 回退 ASSET_READY 重走下游；FRAME_DRAFTING
            # 为忙碌态不放开（frame_gen 在途）
            ProjectStatus.FRAME_READY,
            ProjectStatus.FRAME_APPROVED,
            ProjectStatus.VIDEO_PRODUCING,
            ProjectStatus.VIDEO_READY,
            ProjectStatus.COMPOSING,
            ProjectStatus.COMPOSED,
        }:
            return apply_stage_invalidated(project, Stage.ASSETS)
        raise DomainError(
            "STATE_ILLEGAL",
            f"当前状态 {project.status.value} 不可增改资产（需先确认剧本并抽取资产）",
        )

    def _parse_image_plan(self, raw: Any) -> list[dict]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ValidationFailedError("image_plan 必须是 [{view_label, image_prompt}] 数组")
        plan: list[dict] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict) or not str(item.get("view_label", "")).strip():
                raise ValidationFailedError(f"image_plan[{index}] 缺少 view_label")
            if not str(item.get("image_prompt", "")).strip():
                raise ValidationFailedError(f"image_plan[{index}] 缺少 image_prompt")
            plan.append(item)
        return plan

    def cmd_create_asset(self, project, payload) -> CommandResult:
        """人工新增资产（LLM 漏拆时的补充入口）：同步命令，不触发抽取任务。"""
        project = self._ensure_assets_editable(project)
        kind_raw = str(payload.get("kind", "")).strip()
        if kind_raw not in {k.value for k in AssetKind}:
            raise ValidationFailedError("kind 必须是 character/scene/prop 之一")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValidationFailedError("资产名称不得为空")
        existing = [(a.kind.value, a.name) for a in self.ctx.assets.list_assets(project.project_id)]
        validate_asset_set(existing + [(kind_raw, name)])
        from server.domain.entities import ImagePlanItem

        asset = Asset(
            asset_id=f"asset-{_uuid()}",
            project_id=project.project_id,
            kind=AssetKind(kind_raw),
            name=name,
            description=str(payload.get("description", "")),
            visual_anchor=str(payload.get("visual_anchor", "")),
            image_plan=[
                ImagePlanItem.model_validate(item)
                for item in self._parse_image_plan(payload.get("image_plan"))
            ],
        )
        self.ctx.assets.add_asset(asset)
        if project.status is not ProjectStatus.ASSET_DRAFTING:
            self.ctx.projects.save(project)
        return CommandResult(ok=True, asset=asset)

    def cmd_edit_asset(self, project, payload) -> CommandResult:
        """人工修订资产设定（名称/描述/一致性锚点/生图计划）：同步命令。"""
        project = self._ensure_assets_editable(project)
        asset = self.ctx.assets.get_asset(str(payload.get("asset_id", "")))
        if asset is None or asset.project_id != project.project_id:
            raise DomainError("NOT_FOUND", f"资产不存在：{payload.get('asset_id')}")
        updates: dict = {}
        if "name" in payload:
            name = str(payload["name"]).strip()
            if not name:
                raise ValidationFailedError("资产名称不得为空")
            others = [
                (a.kind.value, a.name)
                for a in self.ctx.assets.list_assets(project.project_id)
                if a.asset_id != asset.asset_id
            ]
            validate_asset_set(others + [(asset.kind.value, name)])
            updates["name"] = name
        if "description" in payload:
            updates["description"] = str(payload["description"])
        if "visual_anchor" in payload:
            updates["visual_anchor"] = str(payload["visual_anchor"])
        if "image_plan" in payload:
            from server.domain.entities import ImagePlanItem

            updates["image_plan"] = [
                ImagePlanItem.model_validate(item)
                for item in self._parse_image_plan(payload.get("image_plan"))
            ]
        updated = asset.model_copy(update=updates)
        self.ctx.assets.save_asset(updated)
        if project.status is not ProjectStatus.ASSET_DRAFTING:
            self.ctx.projects.save(project)
        return CommandResult(ok=True, asset=updated)

    def cmd_update_params(self, project, payload) -> CommandResult:
        """项目参数人工修订（style 出图风格 / dramatic_tone 剧作基调 /
        target_duration_sec 目标时长 / scene_count 场景数，TASK-048）：
        同步命令，即时生效于后续生成。

        参数只影响之后的生成，不改已确认工件，除 ARCHIVED 外均可改。
        """
        if project.status is ProjectStatus.ARCHIVED:
            raise DomainError("STATE_ILLEGAL", "已归档项目不可修改参数")
        updates: dict = {}
        if "style" in payload:
            updates["style"] = str(payload["style"]).strip()
        if "dramatic_tone" in payload:
            from server.domain.enums import DramaticTone

            try:
                updates["dramatic_tone"] = DramaticTone(str(payload["dramatic_tone"]))
            except ValueError as exc:
                raise DomainError(
                    "VALIDATION_ERROR",
                    f"dramatic_tone 取值非法：{payload['dramatic_tone']}"
                    f"（可选：{[t.value for t in DramaticTone]}）",
                ) from exc
        if "target_duration_sec" in payload:
            try:
                seconds = int(payload["target_duration_sec"])
            except (TypeError, ValueError) as exc:
                raise DomainError(
                    "VALIDATION_ERROR", f"target_duration_sec 必须是正整数：{payload['target_duration_sec']}"
                ) from exc
            if seconds <= 0:
                raise DomainError("VALIDATION_ERROR", "target_duration_sec 必须是正整数")
            updates["target_duration_sec"] = seconds
        if "scene_count" in payload:
            try:
                scenes = int(payload["scene_count"])
            except (TypeError, ValueError) as exc:
                raise DomainError(
                    "VALIDATION_ERROR", f"scene_count 必须是正整数：{payload['scene_count']}"
                ) from exc
            if scenes < 1:
                raise DomainError("VALIDATION_ERROR", "scene_count 必须 ≥1")
            updates["scene_count"] = scenes
        if updates:
            params = project.params.model_copy(update=updates)
            project = project.model_copy(update={"params": params, "updated_at": utcnow()})
            self.ctx.projects.save(project)
        return CommandResult(ok=True, project=self.ctx.projects.get(project.project_id))

    def cmd_generate_asset_image(self, project, payload) -> CommandResult:
        asset = self.ctx.assets.get_asset(str(payload.get("asset_id", "")))
        if asset is None or asset.project_id != project.project_id:
            raise DomainError("NOT_FOUND", f"资产不存在：{payload.get('asset_id')}")
        view_label = str(payload.get("view_label") or "主设定")
        extra = str(payload.get("extra_prompt") or "")
        base_prompt = next(
            (
                item.image_prompt
                for item in asset.image_plan
                if item.view_label == view_label
            ),
            "",
        )
        prompt = extra or base_prompt
        # 出图风格（项目参数）：生成时拼入，未包含过才拼，改风格后无需重写提示词；
        # 风格为空时落默认风格线（TASK-046：空风格 = 每张卡各画各的，成片风格漂移）
        from server.app.h3_compiler import DEFAULT_STYLE_LINE

        style = project.params.style.strip() or DEFAULT_STYLE_LINE
        if style and style not in prompt:
            prompt = f"{prompt}, style: {style}"
        if asset.kind is AssetKind.SCENE:
            # 场景图必须无人：即使 LLM/手写提示词带人形词，出图前确定性剥掉
            from server.domain.textnorm import sanitize_scene_prompt

            prompt, _removed = sanitize_scene_prompt(prompt)
        elif asset.kind is AssetKind.PROP:
            # 道具图必须独占画面：剥掉手/人/场景词，确保 isolated 纯白底
            from server.domain.textnorm import sanitize_prop_prompt

            prompt, _removed = sanitize_prop_prompt(prompt)
        # seed 可选：固定 seed + 同提示词 = 可复现（排查/迭代稳定出图用）；
        # 未显式给种子时随机化——工作流里保存的是固定种子，重生成会一模一样
        seed_payload = payload.get("seed")
        seed = int(seed_payload) if seed_payload not in (None, "") else _random_seed()
        width, height = size_for_asset_card(project.params.ratio)
        image_row = AssetImage(
            asset_image_id=f"img-{_uuid()}",
            asset_id=asset.asset_id,
            version_no=len(self.ctx.assets.list_images(asset_id=asset.asset_id)) + 1,
            view_label=view_label,
            prompt=prompt,
            status=AssetImageStatus.GENERATING,
        )
        self.ctx.assets.add_image(image_row)
        from server.app.media import asset_image_rel

        rel = asset_image_rel(project.project_id, asset.asset_id, image_row.asset_image_id)
        negative = negative_for_kind(asset.kind)
        job = Job(
            job_id=new_id("job"),
            project_id=project.project_id,
            type=JobType.IMAGE_GEN,
            payload={
                "asset_image_id": image_row.asset_image_id,
                "prompt": prompt,
                "width": width,
                "height": height,
                "dest_rel": rel,
                "negative": negative,
                **({"seed": seed} if seed is not None else {}),
            },
        )
        self.ctx.jobs.insert(job)
        return CommandResult(jobs=[job])

    def cmd_upload_asset_image(self, project, payload) -> CommandResult:
        """multipart 上传：payload 携带已读字节与元数据（路由层组装）。"""
        asset = self.ctx.assets.get_asset(str(payload.get("asset_id", "")))
        if asset is None or asset.project_id != project.project_id:
            raise DomainError("NOT_FOUND", f"资产不存在：{payload.get('asset_id')}")
        content: bytes = payload["content"]
        ext = str(payload.get("ext") or ".png")
        image_id = _uuid()
        rel = uploaded_image_rel(project.project_id, image_id, ext)
        abs_media_path(self.ctx.settings, rel).parent.mkdir(parents=True, exist_ok=True)
        abs_media_path(self.ctx.settings, rel).write_bytes(content)
        image_row = AssetImage(
            asset_image_id=f"img-{image_id}",
            asset_id=asset.asset_id,
            version_no=len(self.ctx.assets.list_images(asset_id=asset.asset_id)) + 1,
            view_label=str(payload.get("view_label") or "上传"),
            prompt="",
            provider="upload",
            file_path=rel,
            status=AssetImageStatus.UPLOADED,
        )
        self.ctx.assets.add_image(image_row)
        return CommandResult(ok=True, asset_image=image_row)

    def cmd_delete_asset_image(self, project, payload) -> CommandResult:
        """删除资产图片（清理生成历史里的旧版本）：只删库行，磁盘文件保留可恢复。

        删光角色/场景的图后，「确认资产」门会按缺批准图正常拦截，无需额外限制。
        """
        image_row = self.ctx.assets.get_image(str(payload.get("asset_image_id", "")))
        if image_row is None:
            raise DomainError("NOT_FOUND", f"资产图片不存在：{payload.get('asset_image_id')}")
        asset = self.ctx.assets.get_asset(image_row.asset_id)
        if asset is None or asset.project_id != project.project_id:
            raise DomainError("NOT_FOUND", f"资产不存在：{image_row.asset_id}")
        self.ctx.assets.delete_image(image_row.asset_image_id)
        return CommandResult(ok=True)

    def cmd_approve_asset_image(self, project, payload) -> CommandResult:
        image_row = self.ctx.assets.get_image(str(payload.get("asset_image_id", "")))
        if image_row is None:
            raise DomainError("NOT_FOUND", f"资产图片不存在：{payload.get('asset_image_id')}")
        approved = bool(payload.get("approved", True))
        if approved and image_row.status not in {
            AssetImageStatus.READY,
            AssetImageStatus.UPLOADED,
        }:
            raise DomainError("STATE_ILLEGAL", "只有 ready/uploaded 图片可批准")
        image_row.approved = approved
        self.ctx.assets.save_image(image_row)
        return CommandResult(ok=True, asset_image=image_row)

    def cmd_approve_assets(self, project, payload) -> CommandResult:
        missing = self._assets_missing_approved_images(project.project_id)
        if missing:
            raise ReferenceMissingError(
                "关键资产缺少已批准图片：{}".format(", ".join(missing)),
                details={"assets": missing},
            )
        moved = apply_action(project, "approve_assets")
        self.ctx.projects.save(moved)
        return CommandResult(ok=True, project=self.ctx.projects.get(moved.project_id))

    def _assets_missing_approved_images(self, pid: str) -> list[str]:
        assets = self.ctx.assets.list_assets(pid)
        images = self.ctx.assets.list_images(project_id=pid)
        approved_by_asset = {
            i.asset_id for i in images if i.approved
        }
        return [
            a.name
            for a in assets
            if a.kind in {AssetKind.CHARACTER, AssetKind.SCENE}
            and a.asset_id not in approved_by_asset
        ]

    # ------------------------------------------------------------------
    # 分镜（TASK-010）
    # ------------------------------------------------------------------

    def shot_beats_zh(self, pid: str, segments: list) -> dict[str, list[list[str]]]:
        """逐段逐镜列出所认领的剧本节拍原文（中文），供页面用中文展示镜头内容。

        镜头动作（shots[].action）本身是英文——它要逐字进 H3 的英文正文；页面上
        给人看的应该是剧本节拍（写剧本时的中文画面描述），两者通过 shot.beat_refs
        对齐。节拍缺认领（旧分镜）时返回空列表，页面回退显示英文原文。
        """
        script = self.ctx.scripts.active(pid)
        if script is None:
            return {}
        beats_by_scene = {scene.id: list(scene.beats) for scene in script.content.scenes}
        labels = {"sfx": "音效", "on_screen_text": "画面文字", "transition": "转场"}
        out: dict[str, list[list[str]]] = {}
        for segment in segments:
            beats = beats_by_scene.get(segment.scene_id, [])
            rows: list[list[str]] = []
            for shot in segment.shots:
                items: list[str] = []
                for ref in shot.beat_refs:
                    if not (1 <= ref <= len(beats)):
                        continue
                    beat = beats[ref - 1]
                    label = labels.get(beat.type.value, "")
                    speaker = f"{beat.speaker}：" if beat.speaker else ""
                    tone = f"（{beat.tone}）" if beat.tone else ""
                    body = f"{speaker}{beat.text}{tone}"
                    items.append(f"{label}{body}" if label else body)
                rows.append(items)
            out[segment.segment_key] = rows
        return out

    def segment_reference_previews(self, project, sb_version) -> dict[str, dict]:
        """逐段解析「实际会送入 H3 的参考图」预览（与生产解析同一套规则）。

        返回 {segment_key: {pictures, expected_count, mentioned, numbering_ok,
        prompt_text, prompt_manual}}；
        pictures 顺序即上传顺序：连续段尾帧在前（<Picture 1>），资产图按 asset_refs 顺序。

        TASK-040：编号核对与页面展示都用**生产实际会送出的文本**——未被人工覆盖的段
        按当前数据实时重编译（含关键帧占 <Picture 1> 的情况），而不是分镜时的旧缓存。
        此前用缓存文本核对，会因为"关键帧已批准但缓存文本没有该槽位"误报「编号错位」。
        """
        from server.app.h3_compiler import compile_h3_prompt
        from server.domain.validation import LABEL_RE, has_continuity_tail

        assets_by_id = {
            a.asset_id: a for a in self.ctx.assets.list_assets(project.project_id)
        }
        images_by_asset: dict[str, list] = {}
        for image in self.ctx.assets.list_images(project_id=project.project_id):
            images_by_asset.setdefault(image.asset_id, []).append(image)
        tail_by_key: dict[str, str] = {}
        for clip in self.ctx.clips.list_by_project(project.project_id):
            if (
                clip.storyboard_version_id == sb_version.storyboard_version_id
                and clip.tail_frame_path
            ):
                tail_by_key[clip.segment_key] = clip.tail_frame_path

        previews: dict[str, dict] = {}
        for seg in sb_version.content.segments:
            pictures: list[dict] = []
            keyframe = self._approved_keyframe(project.project_id, seg.segment_key)
            if keyframe is not None:
                # 关键帧优先（TASK-031）：占 <Picture 1>，尾帧接力让位
                pictures.append({
                    "picture_no": 1,
                    "kind": "keyframe",
                    "label": "关键帧 · 0.00s 开场",
                    "url": f"/media/{keyframe.file_path}" if keyframe.file_path else None,
                    "missing": not keyframe.file_path,
                })
            elif has_continuity_tail(seg):
                tail_rel = tail_by_key.get(seg.continuity.with_prev_segment_key)
                pictures.append({
                    "picture_no": 1,
                    "kind": "tail_frame",
                    "source_segment_key": seg.continuity.with_prev_segment_key,
                    "label": "前段尾帧 · 0.00s 开场",
                    "url": f"/media/{tail_rel}" if tail_rel else None,
                    "missing": tail_rel is None,
                })
            for ref in seg.asset_refs:
                picture_no = len(pictures) + 1
                asset = assets_by_id.get(ref.asset_id)
                base = {
                    "picture_no": picture_no,
                    "kind": "asset",
                    "asset_id": ref.asset_id,
                    "usage_note": ref.usage_note,
                }
                if asset is None:
                    pictures.append({**base, "missing": True, "reason": "资产不存在"})
                    continue
                approved = [
                    i for i in images_by_asset.get(asset.asset_id, []) if i.approved
                ]
                if not approved:
                    pictures.append({
                        **base,
                        "asset_name": asset.name,
                        "asset_kind": asset.kind.value,
                        "missing": True,
                        "reason": "缺少已批准图片",
                    })
                    continue
                primary = _pick_primary_image(approved, asset.kind)
                pictures.append({
                    **base,
                    "asset_name": asset.name,
                    "asset_kind": asset.kind.value,
                    "view_label": primary.view_label,
                    "url": f"/media/{primary.file_path}" if primary.file_path else None,
                    "missing": False,
                })
            manual = bool(seg.h3_prompt.manual_override and seg.h3_prompt.text.strip())
            if manual:
                prompt_text = seg.h3_prompt.text
            else:
                prompt_text = compile_h3_prompt(
                    seg,
                    assets_by_id,
                    style_line=project.params.style,
                    opening_frame=keyframe is not None or seg.continuity.enabled,
                )
            mentioned = sorted(
                {
                    int(number)
                    for kind, number in LABEL_RE.findall(prompt_text)
                    if kind == "Picture"
                }
            )
            previews[seg.segment_key] = {
                "pictures": pictures,
                "expected_count": len(pictures),
                "mentioned": mentioned,
                "numbering_ok": mentioned == list(range(1, len(pictures) + 1)),
                "prompt_text": prompt_text,
                "prompt_manual": manual,
            }
        return previews

    def cmd_generate_storyboard(self, project, payload) -> CommandResult:
        # 分镜确认后（含关键帧/视频/成片阶段）允许重生成：FR-016 失效回退到
        # STORYBOARD_READY 再进入 DRAFTING；旧分镜版本与已生成成片保留，新版本
        # 需重新确认、重新过关键帧门与生产。忙碌态（VIDEO_PRODUCING/COMPOSING）
        # 不放开，避免与在途任务竞争。
        if project.status in {
            ProjectStatus.STORYBOARD_APPROVED,
            ProjectStatus.FRAME_READY,
            ProjectStatus.FRAME_APPROVED,
            ProjectStatus.VIDEO_READY,
            ProjectStatus.COMPOSED,
        }:
            project = apply_stage_invalidated(project, Stage.STORYBOARD)
            self.ctx.projects.save(project)
        moved = apply_action(project, "generate_storyboard")
        job = Job(
            job_id=new_id("job"),
            project_id=project.project_id,
            type=JobType.STORYBOARD_GEN,
            payload={"project_id": project.project_id},
        )
        self.ctx.jobs.insert(job)
        self.ctx.projects.save(moved)
        return CommandResult(jobs=[job])

    def cmd_edit_segment(self, project, payload) -> CommandResult:
        known_ids = {a.asset_id for a in self.ctx.assets.list_assets(project.project_id)}
        draft = self.ctx.storyboards.latest_draft(project.project_id)
        active = self.ctx.storyboards.active(project.project_id)
        base = draft or active
        if base is None:
            raise DomainError("NOT_FOUND", "没有可编辑的分镜")
        segments = [s.model_copy(deep=True) for s in base.content.segments]
        new_key = str(payload.get("segment_key", ""))
        replaced = False
        for index, seg in enumerate(segments):
            if seg.segment_key == new_key:
                merged = payload.get("segment", payload)
                if isinstance(merged, dict):
                    # UI 会把只读展示字段（references/resolution_status 等）一并回传，
                    # 而实体模型 extra="forbid"：先按 Segment 字段白名单过滤，再整段
                    # model_validate 做嵌套类型转换（旧实现只转 h3_prompt/shots，
                    # asset_refs 等留在 dict 状态，会在 validate_segment 里
                    # 报 'dict' object has no attribute 'asset_id'）
                    allowed = set(Segment.model_fields)
                    fields = seg.model_dump()
                    fields.update({k: v for k, v in merged.items() if k in allowed})
                    updated = Segment.model_validate(fields)
                else:
                    updated = None
                if updated is None:
                    raise DomainError("VALIDATION_ERROR", "segment 载荷无效")
                # 恢复自动编译（TASK-040）：服务端按当前结构化数据重编译正文，
                # 清掉人工覆盖——页面无需自己知道编译结果长什么样
                if payload.get("reset_prompt"):
                    from server.app.h3_compiler import compile_h3_prompt

                    assets_by_id = {
                        a.asset_id: a
                        for a in self.ctx.assets.list_assets(project.project_id)
                    }
                    compiled = compile_h3_prompt(
                        updated,
                        assets_by_id,
                        style_line=project.params.style,
                        opening_frame=updated.continuity.enabled,
                    )
                    updated = updated.model_copy(
                        update={
                            "h3_prompt": updated.h3_prompt.model_copy(
                                update={"text": compiled, "manual_override": False}
                            )
                        }
                    )
                # 人工覆盖判定（TASK-040）：正文与改前不同 → 按载荷声明是否人工覆盖
                # （默认 True，即"我手改的就是我要的"）；传回原文或显式
                # prompt_manual_override=false 则恢复"编译器为准"
                elif updated.h3_prompt.text.strip() != seg.h3_prompt.text.strip():
                    override = payload.get("prompt_manual_override")
                    updated = updated.model_copy(
                        update={
                            "h3_prompt": updated.h3_prompt.model_copy(
                                update={
                                    "manual_override": True
                                    if override is None
                                    else bool(override)
                                }
                            )
                        }
                    )
                validate_segment(updated, known_asset_ids=known_ids)
                segments[index] = updated
                replaced = True
        if not replaced:
            raise DomainError("NOT_FOUND", f"段不存在：{new_key}")
        content = StoryboardContent(segments=segments)
        version_no = len(self.ctx.storyboards.list_by_project(project.project_id)) + 1
        version = StoryboardVersion(
            storyboard_version_id=f"sbv-{_uuid()}",
            project_id=project.project_id,
            version_no=version_no,
            content=content,
        )
        if base.status is WorkStatus.ACTIVE:
            project = apply_stage_invalidated(project, Stage.STORYBOARD)
            base.status = WorkStatus.SUPERSEDED
            self.ctx.storyboards.save(base)
            self.ctx.projects.save(project)
        self.ctx.storyboards.add(version)
        return CommandResult(ok=True, storyboard_version=version)

    def cmd_approve_storyboard(self, project, payload) -> CommandResult:
        moved = apply_action(project, "approve_storyboard")
        draft = self.ctx.storyboards.latest_draft(project.project_id)
        if draft is None:
            raise DomainError("NOT_FOUND", "没有可确认的分镜草稿")
        active = self.ctx.storyboards.active(project.project_id)
        if active is not None:
            active.status = WorkStatus.SUPERSEDED
            self.ctx.storyboards.save(active)
        draft.status = WorkStatus.ACTIVE
        self.ctx.storyboards.save(draft)
        self.ctx.projects.save(moved)
        return CommandResult(ok=True, project=self.ctx.projects.get(moved.project_id))

    # ------------------------------------------------------------------
    # 关键帧（TASK-031）：分镜确认后、视频前，逐段生成开场锚点图
    # ------------------------------------------------------------------

    def _active_segments(self, pid: str):
        sb = self.ctx.storyboards.active(pid)
        if sb is None:
            raise DomainError("NOT_FOUND", "缺少 active 分镜")
        return sb, list(sb.content.segments)

    def _approved_keyframe(self, pid: str, segment_key: str) -> SegmentFrameImage | None:
        """该段已批准关键帧（确定性取最高批准版本，不随生成顺序漂移）。"""
        approved = [
            i for i in self.ctx.frames.list_by_segment(pid, segment_key) if i.approved
        ]
        if not approved:
            return None
        return max(approved, key=lambda i: i.version_no)

    def _segment_reference_cards(self, pid: str, segment) -> list[str]:
        """段开场帧的图生图参考图（Edit 通道，TASK-031/033）。

        组成（不设上限）：**画面内角色身份卡**（按序全部）+ **场景卡**。
        - 只传 in_frame 的角色：图生图模型"给谁的脸就摆谁"，送进不在构图里的卡会
          补出第二个人、两卡特征还会互串（实测：坐着的乘客挂上了司机的工牌）。
        - 道具卡不传：白底产品图对场景静帧没有身份价值，只带来白底/物件漂移。
        - **不做跨段链式锚定**（TASK-038 实测否决）：把上一段已批准关键帧放进参考图
          （无论放 [img0] 还是末位），本段构图都会被它带跑——后视镜特写被画成上一段的
          侧拍司机。场景一致性因此不靠关键帧继承，而由视频阶段（H3 拿场景卡 + 本段
          关键帧作 Picture 1）与连续段尾帧接力承担。

        槽位分派（Edit 工作流只有 3 个参考槽）在 `_resolve_reference_slots`：
        总数 ≤3 逐槽原样送入；>3 时前 2 张各占一槽，其余拼成设定表占第 3 槽
        （旧口径"≥3 角色丢场景卡"已废弃——拼表后场景卡不再需要让位）。
        """
        from server.app.h3_compiler import in_frame_by_name

        in_frame = in_frame_by_name(segment)
        characters: list[Asset] = []
        scene: Asset | None = None
        for ref in segment.asset_refs:
            asset = self.ctx.assets.get_asset(ref.asset_id)
            if asset is None:
                continue
            if asset.kind is AssetKind.CHARACTER:
                if in_frame.get(asset.name.strip(), True):
                    characters.append(asset)
            elif asset.kind is AssetKind.SCENE and scene is None:
                scene = asset
        picked: list[str] = []
        scene_cards = [scene] if scene is not None else []
        for asset in [*characters, *scene_cards]:
            approved = [
                i for i in self.ctx.assets.list_images(asset_id=asset.asset_id) if i.approved
            ]
            if not approved:
                continue
            primary = _pick_primary_image(approved, asset.kind)
            if primary.file_path and primary.file_path not in picked:
                picked.append(primary.file_path)
        return picked

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
        """编译该段关键帧生图提示词、登记 SegmentFrameImage 并入队 frame_gen。

        TASK-035 人工可控：prompt_override 直接采用（不再编译，页面上改完就按改的来）；
        reference_image_ids 指定参考图（资产图 id，按给定顺序），留空走自动选择
        （画面内角色卡 + 场景卡）。实际使用的参考图记进帧行，供页面摊开展示。
        TASK-036：有角色卡参考图时正文不写身份串（身份交给参考图，提示词专注画面）。
        seed 未显式给定时随机化（固定种子会让「重新生成」出一模一样的图）。
        多宫格方案A：cell_no 非 None 时该行是格子行，画面描述取对应 shot 的
        动作（action→description 回退），并在入队前作废旧宫格分镜板。
        """
        from server.app.h3_compiler import KEYFRAME_NEGATIVE, compile_keyframe_prompt

        if seed is None:
            seed = _random_seed()
        # 逐格口径：格子的画面描述来自对应 shot，不再固定读 keyframe_description
        cell_description = ""
        if cell_no is not None:
            shot = segment.shots[cell_no - 1]
            cell_description = shot.action.strip() or shot.description.strip()
        assets_by_id = {
            a.asset_id: a for a in self.ctx.assets.list_assets(project.project_id)
        }
        # 图生图参考通道（可选）：配置了 Edit 工作流时，把资产卡作为参考图随任务
        # 上传（锁脸）；留空 = 纯文生图路径
        if reference_image_ids is not None:
            raw_paths = self._reference_paths_by_image_ids(
                project.project_id, reference_image_ids
            )
        elif self.ctx.settings.runninghub_workflow_image_edit:
            raw_paths = self._segment_reference_cards(
                project.project_id, segment
            )
        else:
            raw_paths = []
        # 槽位分派：>3 个资产时多余卡片拼成设定表（Edit 工作流只有 3 个参考槽）
        reference_rel_paths, reference_bindings = self._resolve_reference_slots(
            project.project_id, raw_paths
        )
        if prompt_override.strip():
            prompt = prompt_override.strip()
        else:
            # I2I 编辑指令要逐张声明"哪张图管谁、保留什么"（豆包 frame.md 口径）：
            # 参考图路径 → 资产，按送入顺序成为 Picture 1/2/3（TASK-045）；
            # 风格为空时落默认风格线（TASK-046：无风格句则关键帧各自发挥）
            from server.app.h3_compiler import DEFAULT_STYLE_LINE

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
        width, height = size_for_asset_card(project.params.ratio)
        row_id = f"frm-{_uuid()}"
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
            # 重抽格 → 旧宫格分镜板作废（全部格重新批准后由 approve 流程重拼）
            self._invalidate_storyboard_grid(project.project_id, segment.segment_key)
        payload = {
            "frame_image_id": row_id,
            "prompt": prompt,
            "width": width,
            "height": height,
            "dest_rel": segment_frame_rel(project.project_id, segment.segment_key, row_id),
            "negative": KEYFRAME_NEGATIVE,
            "seed": seed,
        }
        if reference_rel_paths:
            payload["reference_rel_paths"] = reference_rel_paths
        job = Job(
            job_id=new_id("job"),
            project_id=project.project_id,
            type=JobType.FRAME_GEN,
            payload=payload,
        )
        self.ctx.jobs.insert(job)
        return job

    def _invalidate_storyboard_grid(self, pid: str, key: str) -> None:
        """作废该段宫格分镜板行（重抽格后旧拼图失效，由 approve 流程重拼）。"""
        for row in self.ctx.frames.list_by_segment(pid, key):
            if row.view_label == STORYBOARD_GRID_LABEL:
                self.ctx.frames.delete(row.frame_image_id)

    def auto_reference_cards(self, pid: str, segment) -> list[str]:
        """页面展示用：该段自动选中的参考图（相对路径，编图生图通道未启用时为空）。"""
        if not self.ctx.settings.runninghub_workflow_image_edit:
            return []
        return self._segment_reference_cards(pid, segment)

    def _resolve_reference_slots(self, pid: str, paths: list[str]) -> tuple[list[str], list]:
        """把选中的参考路径分派进 Edit 工作流的 3 个参考槽（>3 资产的拼表方案）。

        - 总数 ≤3：逐槽原样送入，Picture 1/2/3 与资产一一对应。
        - 总数 >3（`TextEncodeQwenImageEditPlus` 节点硬上限 image1/2/3）：前 2 张
          各占一槽，其余资产卡（含场景卡，超出 REFERENCE_SHEET_MAX 张舍弃）横向
          拼成一张「设定表」占第 3 槽；提示词 bindings 里以 ReferenceSheet 逐卡
          声明从左到右是谁。

        返回（槽位路径列表, 提示词 bindings：Asset | ReferenceSheet）。
        以"已批准主图路径"匹配资产：同一资产只有一张主图进参考，映射唯一。
        """
        from server.app.h3_compiler import ReferenceSheet

        by_path: dict[str, Asset] = {}
        for asset in self.ctx.assets.list_assets(pid):
            approved = [
                i for i in self.ctx.assets.list_images(asset_id=asset.asset_id) if i.approved
            ]
            if not approved:
                continue
            primary = _pick_primary_image(approved, asset.kind)
            if primary.file_path:
                by_path.setdefault(primary.file_path, asset)

        def bind(ps: list[str]) -> list[Asset]:
            return [by_path[p] for p in ps if p in by_path]

        if len(paths) <= 3:
            return list(paths), bind(paths)
        solo, extras = list(paths[:2]), list(paths[2:REFERENCE_SHEET_MAX + 2])
        sheet_rel = _compose_reference_sheet(self.ctx.settings, pid, extras)
        return [*solo, sheet_rel], [*bind(solo), ReferenceSheet(assets=bind(extras))]

    def _reference_paths_by_image_ids(
        self, pid: str, image_ids: list[str]
    ) -> list[str]:
        """把页面指定的资产图 id 解析成相对路径（按给定顺序，去重、跳过未落盘图）。

        只接受已批准且已落盘的图：与自动选择同一口径，避免手工指定把"生成中/
        被删掉的图"送进生图任务。
        """
        paths: list[str] = []
        for image_id in image_ids:
            image = self.ctx.assets.get_image(str(image_id))
            if image is None or not image.approved or not image.file_path:
                continue
            if image.file_path not in paths:
                paths.append(image.file_path)
        return paths

    def cmd_generate_keyframes(self, project, payload) -> CommandResult:
        """逐段入队关键帧生成；已批准/更晚阶段重生成走 FR-016 失效回退
        （忙碌态不放开），旧关键帧版本保留可回批。

        多宫格方案A：单镜段保持旧单帧口径（1 个任务、无格号）；多镜段逐格
        入队，每格画面描述取对应 shot，最多 MAX_GRID_CELLS 格（超出的镜头
        交给视频模型按提示词发挥）。
        """
        if project.status in {
            ProjectStatus.FRAME_APPROVED,
            ProjectStatus.VIDEO_READY,
            ProjectStatus.COMPOSED,
        }:
            project = apply_stage_invalidated(project, Stage.FRAME)
            self.ctx.projects.save(project)
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

    def cmd_generate_frame_image(self, project, payload) -> CommandResult:
        """单段重抽关键帧（TASK-035 人工可控）；版本递增，旧版本保留。

        可覆盖：prompt（整段生图提示词，页面改完即用）、reference_asset_image_ids
        （手工指定参考图，按给定顺序）、extra_prompt（在编译提示词后追加）、seed。
        多宫格方案A：payload 带 cell_no（1 起）时按对应 shot 重抽该格，并入队前
        作废旧宫格分镜板。
        """
        _, segments = self._active_segments(project.project_id)
        key = str(payload.get("segment_key", ""))
        segment = next((s for s in segments if s.segment_key == key), None)
        if segment is None:
            raise DomainError("NOT_FOUND", f"段不存在：{key}")
        extra = str(payload.get("extra_prompt") or "")
        prompt_override = str(payload.get("prompt") or "")
        seed_payload = payload.get("seed")
        seed = int(seed_payload) if seed_payload not in (None, "") else None
        raw_cell = payload.get("cell_no")
        cell_no = int(raw_cell) if raw_cell not in (None, "") else None
        raw_images = payload.get("reference_asset_image_ids")
        reference_image_ids = (
            [str(i) for i in raw_images] if isinstance(raw_images, list) else None
        )
        job = self._enqueue_frame_job(
            project,
            segment,
            extra_prompt=extra,
            seed=seed,
            prompt_override=prompt_override,
            reference_image_ids=reference_image_ids,
            cell_no=cell_no,
        )
        return CommandResult(jobs=[job])

    def cmd_upload_frame_image(self, project, payload) -> CommandResult:
        """multipart 上传关键帧：payload 携带已读字节与元数据（路由层组装）。

        多宫格方案A：payload 带 cell_no（1 起）时该行标记为对应格子的上传行。
        """
        _, segments = self._active_segments(project.project_id)
        key = str(payload.get("segment_key", ""))
        if not any(s.segment_key == key for s in segments):
            raise DomainError("NOT_FOUND", f"段不存在：{key}")
        content: bytes = payload["content"]
        ext = str(payload.get("ext") or ".png")
        raw_cell = payload.get("cell_no")
        cell_no = int(raw_cell) if raw_cell not in (None, "") else None
        image_id = _uuid()
        rel = uploaded_frame_rel(project.project_id, image_id, ext)
        abs_media_path(self.ctx.settings, rel).parent.mkdir(parents=True, exist_ok=True)
        abs_media_path(self.ctx.settings, rel).write_bytes(content)
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
        self.ctx.frames.add(row)
        return CommandResult(ok=True, frame_image=row)

    def cmd_delete_frame_image(self, project, payload) -> CommandResult:
        """删除关键帧（清理旧版本）：只删库行，磁盘文件保留可恢复。"""
        row = self.ctx.frames.get(str(payload.get("frame_image_id", "")))
        if row is None or row.project_id != project.project_id:
            raise DomainError("NOT_FOUND", f"关键帧不存在：{payload.get('frame_image_id')}")
        self.ctx.frames.delete(row.frame_image_id)
        return CommandResult(ok=True)

    def cmd_approve_frame_image(self, project, payload) -> CommandResult:
        row = self.ctx.frames.get(str(payload.get("frame_image_id", "")))
        if row is None or row.project_id != project.project_id:
            raise DomainError("NOT_FOUND", f"关键帧不存在：{payload.get('frame_image_id')}")
        approved = bool(payload.get("approved", True))
        if approved and row.status not in {
            AssetImageStatus.READY,
            AssetImageStatus.UPLOADED,
        }:
            raise DomainError("STATE_ILLEGAL", "只有 ready/uploaded 图片可批准")
        row.approved = approved
        self.ctx.frames.save(row)
        return CommandResult(ok=True, frame_image=row)

    def cmd_approve_keyframes(self, project, payload) -> CommandResult:
        """关键帧确认门：默认逐段 ≥1 张已批准关键帧；显式 allow_tail_fallback
        才允许缺帧段回退尾帧接力（不静默降级，AI_RULES 红线 3）。"""
        _, segments = self._active_segments(project.project_id)
        missing = [
            s.segment_key
            for s in segments
            if self._approved_keyframe(project.project_id, s.segment_key) is None
        ]
        if missing and not bool(payload.get("allow_tail_fallback")):
            raise ReferenceMissingError(
                "以下段缺少已批准关键帧：{}（可为缺帧段生成/上传关键帧，"
                "或显式勾选「缺帧段回退尾帧接力」后确认）".format(", ".join(missing)),
                details={"segments": missing},
            )
        moved = apply_action(project, "approve_keyframes")
        self.ctx.projects.save(moved)
        return CommandResult(ok=True, project=self.ctx.projects.get(moved.project_id))

    # ------------------------------------------------------------------
    # 视频生产（TASK-011）
    # ------------------------------------------------------------------

    def _resolve_references(
        self, project, segment, sb_version_id: str
    ) -> tuple[list[str], VideoMode, str | None]:
        """参考图解析（COMMAND-001）：关键帧优先 + 每资产一张确定性主图。

        段有已批准关键帧 → 它占 <Picture 1>（开场锚点，尾帧接力让位，TASK-031），
        资产图顺延；>9 截断时关键帧槽位保留、只截资产图。主图选择决定资产
        身份锚：角色优先「主设定」、场景优先「空镜」，都没有才取第一张已批准
        图——不取「最新」。顺序即上传顺序，任何重排都会破坏提示词编号。
        返回 (reference_paths, mode, keyframe_path|None)。
        """
        keyframe = self._approved_keyframe(project.project_id, segment.segment_key)
        resolved: list[tuple[str, AssetKind, str, str]] = []
        missing: list[str] = []
        for ref in segment.asset_refs:
            asset = self.ctx.assets.get_asset(ref.asset_id)
            if asset is None:
                missing.append(ref.asset_id)
                continue
            approved = [
                i for i in self.ctx.assets.list_images(asset_id=ref.asset_id) if i.approved
            ]
            if not approved:
                missing.append(asset.name)
                continue
            primary = _pick_primary_image(approved, asset.kind)
            resolved.append((primary.file_path, asset.kind, asset.asset_id, primary.asset_image_id))
        if missing:
            raise ReferenceMissingError(
                "缺少已批准参考图：{}".format(", ".join(missing)),
                details={"assets": missing},
            )
        mode = (
            VideoMode.R2VA
            if (resolved or keyframe is not None)
            else VideoMode.T2VA
        )
        from server.domain.entities import ResolvedReference

        refs = [
            ResolvedReference(
                asset_id=asset_id, kind=kind, asset_image_id=image_id, file_path=path
            )
            for path, kind, asset_id, image_id in resolved
        ]
        kept, _warnings = truncate_references(refs)
        paths = [r.file_path for r in kept]
        keyframe_path: str | None = None
        if keyframe is not None:
            from server.domain.validation import MAX_REFERENCE_IMAGES

            paths = paths[: MAX_REFERENCE_IMAGES - 1]
            keyframe_path = keyframe.file_path
            paths = [keyframe_path, *paths]
        return paths, mode, keyframe_path

    def _tail_frame_for(self, pid: str, prev_key: str, sb_version_id: str) -> str | None:

        for clip in reversed(self.ctx.clips.list_by_project(pid)):
            if (
                clip.segment_key == prev_key
                and clip.storyboard_version_id == sb_version_id
                and clip.tail_frame_path
            ):
                return clip.tail_frame_path
        return None

    def cmd_produce_video(self, project, payload) -> CommandResult:
        scope = str(payload.get("scope", "all"))
        sb = self.ctx.storyboards.active(project.project_id)
        if sb is None:
            raise DomainError("NOT_FOUND", "缺少 active 分镜")
        moved = apply_action(project, "produce_video")
        self.ctx.projects.save(moved)
        segments = sb.content.segments
        if scope == "first_only":
            targets = [segments[0]]
        elif scope == "all":
            targets = list(segments)
        else:
            targets = [s for s in segments if s.segment_key == scope]
            if not targets:
                raise DomainError("NOT_FOUND", f"段不存在：{scope}")
        keys = [s.segment_key for s in segments]
        jobs: list[Job] = []
        batch_job_ids: dict[str, str] = {}
        for seg in targets:
            index = keys.index(seg.segment_key)
            prev_key = keys[index - 1] if index > 0 else ""
            reference_paths, mode, keyframe_path = self._resolve_references(
                project, seg, sb.storyboard_version_id
            )
            # TASK-046 衔接修复：关键帧只作**场景首段**的 <Picture 1>（定开场构图）；
            # 同场景后继段改用**尾帧接力**续接前段的动作、光影与镜头位置——
            # 此前每段都从自己的静帧冷启动（TASK-031 关键帧优先），
            # 段间接缝全是跳切（用户实测"衔接不流畅"）。
            scene_head = index == 0 or segments[index - 1].scene_id != seg.scene_id
            use_relay = (not scene_head) and seg.continuity.enabled and bool(prev_key)
            if keyframe_path is not None and not use_relay:
                # 关键帧占 <Picture 1>，资产图顺延；不给"保持不变"锁，
                # 非连续段以 opening_frame=True 编译（TASK-018 编号错位同源）
                continuity_prev = None
                mode = VideoMode.R2VA
            else:
                # 尾帧接力：尾帧在【运行时】由 handler 从前段 Clip 解析（同批前段
                # 此刻还没成片），这里只记录前段 key 并建立依赖边，保证 handler
                # 执行时前段已成功。参考槽 0 的关键帧让位，尾帧补位 <Picture 1>。
                if keyframe_path and reference_paths and reference_paths[0] == keyframe_path:
                    reference_paths = reference_paths[1:]
                keyframe_path = None
                continuity_prev = prev_key if seg.continuity.enabled and prev_key else None
                if continuity_prev:
                    mode = VideoMode.R2VA
                    if prev_key not in batch_job_ids and self._tail_frame_for(
                        project.project_id, prev_key, sb.storyboard_version_id
                    ) is None:
                        raise DomainError(
                            "DEPENDENCY_BLOCKED",
                            f"连续性前段 {prev_key} 尚无成片，无法生成 {seg.segment_key}",
                        )
            # 提示词一律生产时实时重编译（TASK-032）：存储的 h3_prompt.text
            # 降级为分镜时的编译缓存，编译器升级（身份锚点唯一/锚点消毒/
            # 开场位置注入）对存量项目即时生效
            prompt_text = self._compile_production_prompt(
                project,
                seg,
                opening_frame=bool(keyframe_path) or seg.continuity.enabled,
            )
            version_no = sb.version_no
            job = Job(
                job_id=new_id("job"),
                project_id=project.project_id,
                type=JobType.VIDEO_GEN,
                depends_on=[batch_job_ids[prev_key]] if prev_key in batch_job_ids else [],
                input_snapshot={
                    "storyboard_version_id": sb.storyboard_version_id,
                    "segment_key": seg.segment_key,
                    "version_no": version_no,
                    "prompt": prompt_text,
                    "duration_sec": seg.duration_sec,
                    "mode": mode.value,
                    "ratio": project.params.ratio,
                    "reference_paths": reference_paths,
                    "continuity_prev_segment_key": continuity_prev,
                    "opening_frame_source": (
                        "keyframe" if keyframe_path else ("tail_frame" if continuity_prev else None)
                    ),
                    "dest_rel": segment_video_rel(project.project_id, seg.segment_key, version_no),
                },
            )
            self.ctx.jobs.insert(job)
            batch_job_ids[seg.segment_key] = job.job_id
            jobs.append(job)
        return CommandResult(jobs=jobs)

    def _compile_production_prompt(
        self, project, segment, *, opening_frame: bool
    ) -> str:
        """生产时实时重编译六段提示词（TASK-032）。

        存储的 h3_prompt.text 只是分镜生成时的编译缓存；编译器升级（身份
        锚点唯一、定妆卡/空镜短语消毒、开场位置注入）必须对存量分镜即时
        生效，而不是被缓存文本挡住。opening_frame 指明 <Picture 1> 槽位
        来源（关键帧或尾帧接力）。守住 7000 字符契约。

        TASK-040 人工覆盖：段落被人在页面上改过正文（h3_prompt.manual_override）
        时直接用存储文本，不再重编译——手改的意图优先于编译器升级；清除覆盖标记
        后恢复常态。
        """
        if segment.h3_prompt.manual_override and segment.h3_prompt.text.strip():
            text = segment.h3_prompt.text.strip()
            if len(text) > 7000:
                raise DomainError(
                    "VALIDATION_ERROR",
                    f"{segment.segment_key} 人工覆盖的提示词超 7000 字符，请精简",
                )
            return text

        from server.app.h3_compiler import compile_h3_prompt

        assets_by_id = {
            a.asset_id: a for a in self.ctx.assets.list_assets(project.project_id)
        }
        text = compile_h3_prompt(
            segment,
            assets_by_id,
            style_line=project.params.style,
            opening_frame=opening_frame,
        )
        if len(text) > 7000:
            raise DomainError(
                "VALIDATION_ERROR",
                f"{segment.segment_key} 生产版提示词超 7000 字符，请精简 action",
            )
        return text

    def cmd_regenerate_segment(self, project, payload) -> CommandResult:
        key = str(payload.get("segment_key", ""))
        sb = self.ctx.storyboards.active(project.project_id)
        if sb is None:
            raise DomainError("NOT_FOUND", "缺少 active 分镜")
        segment = next((s for s in sb.content.segments if s.segment_key == key), None)
        if segment is None:
            raise DomainError("NOT_FOUND", f"段不存在：{key}")
        if payload.get("prompt_overrides"):
            prompt_text = str(payload["prompt_overrides"])
            segment = segment.model_copy(
                update={"h3_prompt": segment.h3_prompt.model_copy(update={"text": prompt_text})}
            )
            validate_segment(segment, known_asset_ids={
                a.asset_id for a in self.ctx.assets.list_assets(project.project_id)
            })
        reference_paths, mode, keyframe_path = self._resolve_references(
            project, segment, sb.storyboard_version_id
        )
        keys = [s.segment_key for s in sb.content.segments]
        index = keys.index(key)
        prev_key = keys[index - 1] if index > 0 else ""
        # 关键帧优先（TASK-031）：有批准关键帧即占 Picture 1，尾帧接力让位
        if keyframe_path is not None:
            continuity_prev = None
            mode = VideoMode.R2VA
        else:
            continuity_prev = prev_key if segment.continuity.enabled and prev_key else None
            if continuity_prev and self._tail_frame_for(
                project.project_id, prev_key, sb.storyboard_version_id
            ) is None:
                raise DomainError("DEPENDENCY_BLOCKED", f"前段 {prev_key} 尾帧不可用")
            if continuity_prev:
                mode = VideoMode.R2VA
        if not payload.get("prompt_overrides"):
            # 无人工覆盖时生产时实时重编译（TASK-032，同 cmd_produce_video）：
            # 编译器升级对存量段即时生效；人工覆盖文本尊重用户原稿不重编译
            prompt_text = self._compile_production_prompt(
                project,
                segment,
                opening_frame=bool(keyframe_path) or segment.continuity.enabled,
            )
        version_no = len(self.ctx.clips.list_by_project(project.project_id)) + 1
        job = Job(
            job_id=new_id("job"),
            project_id=project.project_id,
            type=JobType.VIDEO_GEN,
            input_snapshot={
                "storyboard_version_id": sb.storyboard_version_id,
                "segment_key": key,
                "version_no": sb.version_no,
                "prompt": prompt_text,
                "duration_sec": segment.duration_sec,
                "mode": mode.value,
                "ratio": project.params.ratio,
                "reference_paths": reference_paths,
                "continuity_prev_segment_key": continuity_prev,
                "opening_frame_source": (
                    "keyframe" if keyframe_path else ("tail_frame" if continuity_prev else None)
                ),
                "dest_rel": segment_video_rel(project.project_id, key, version_no),
            },
        )
        self.ctx.jobs.insert(job)
        return CommandResult(jobs=[job])

    # ------------------------------------------------------------------
    # 合成（TASK-012）
    # ------------------------------------------------------------------

    def cmd_compose(self, project, payload) -> CommandResult:
        moved = apply_action(project, "compose")
        job = Job(
            job_id=new_id("job"),
            project_id=project.project_id,
            type=JobType.COMPOSE,
            payload={"project_id": project.project_id},
        )
        self.ctx.jobs.insert(job)
        self.ctx.projects.save(moved)
        return CommandResult(jobs=[job])

    # ------------------------------------------------------------------
    # 一键连跑 / 归档
    # ------------------------------------------------------------------

    def cmd_run_to_next_gate(self, project, payload) -> CommandResult:
        next_command = {
            ProjectStatus.CREATED: "generate_script",
            ProjectStatus.SCRIPT_APPROVED: "generate_assets",
            ProjectStatus.ASSET_APPROVED: "generate_storyboard",
            ProjectStatus.STORYBOARD_APPROVED: "generate_keyframes",
            ProjectStatus.FRAME_READY: "approve_keyframes",
            ProjectStatus.VIDEO_READY: "compose",
        }.get(project.status)
        if next_command is None:
            return CommandResult(ok=True, jobs=[], stopped=True)
        payload_extra: dict = {}
        if next_command == "produce_video":
            payload_extra = {"scope": "all"}
        return self.dispatch(project.project_id, next_command, payload_extra)

    def cmd_archive_project(self, project, payload) -> CommandResult:
        moved = apply_action(project, "archive")
        self.ctx.projects.save(moved)
        return CommandResult(ok=True, project=self.ctx.projects.get(moved.project_id))
