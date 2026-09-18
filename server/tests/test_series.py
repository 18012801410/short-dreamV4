"""系列分集（TASK-047）测试：大纲确定性质量门 + 大纲/逐集剧本生成流 + 资产继承。

大纲与分集剧本都是 LLM 免费动作，用 OneShot 假 LLM 驱动全链；付费阶段不在
本文件范围（继承资产只复制库行，零成本）。
"""

import sqlalchemy as sa
import pytest

from server.app.agents import Agents
from server.app.context import AppContext
from server.app.handlers import Handlers
from server.app.usecases import WorkbenchService
from server.domain.entities import (
    Asset,
    AssetImage,
    EpisodeOutline,
    ScriptContent,
    SeriesOutline,
)
from server.domain.enums import (
    AssetImageStatus,
    AssetKind,
    ProjectStatus,
    SeriesStatus,
)
from server.domain.errors import DomainError
from server.domain.outline_quality import hard_gate_issues, validate_series_outline
from server.domain.job import mark_running
from server.infra.config import Settings
from server.infra.tables import metadata
from server.tests.test_beat_flow import OneShotLlm, make_beat_script


def run_job(handlers, job):
    """模拟 Worker 引擎领取（pending → running）后执行 handler。"""
    return handlers.handle_script_gen(mark_running(job, {})) \
        if job.type.value == "script_gen" else handlers.handle_series_outline_gen(mark_running(job, {}))

# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def _long_synopsis(tag: str) -> str:
    return (
        f"{tag}：他重回都市，当年的仇人正坐在高台上敬酒，全场都在看他笑话；"
        "他不动声色地坐下，口袋里那枚能让全场起立的黑金令牌硌着掌心，"
        "他等着他们把傲慢说完，再一击致命。"
    )


def make_outline(episodes: int = 4, **overrides) -> SeriesOutline:
    eps = []
    for no in range(1, episodes + 1):
        is_last = no == episodes
        eps.append(
            EpisodeOutline(
                episode_no=no,
                title=f"第{no}集·回归",
                synopsis=_long_synopsis(f"第{no}集"),
                opening_hook=f"开场钩子{no}",
                ending_hook="" if is_last else f"集尾卡点{no}",
                highlight=f"身份碾压·亮出令牌（第{no}次）" if no % 2 == 1 else "",
                new_characters=[],
            )
        )
    fields = {
        "title": "赘婿令牌",
        "logline": "人人踩一脚的上门女婿，是集团失散的继承人",
        "genre_tags": ["赘婿逆袭", "身份碾压"],
        "characters": [{"name": "陈平", "profile": "上门女婿，真实身份集团继承人，口头禅：不必"}],
        "episodes": eps,
        "climax_episode": max(1, episodes - 1),
    }
    fields.update(overrides)
    return SeriesOutline(**fields)


def make_ctx(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'series.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    return settings, AppContext.build(settings, engine)


class RecordingLlm(OneShotLlm):
    """记录 system/user 的假 LLM：断言注入内容用。"""

    def __init__(self, content) -> None:
        super().__init__(content)
        self.systems: list[str] = []
        self.users: list[str] = []

    def chat_json(self, *, system, user, schema, temperature):
        self.systems.append(system)
        self.users.append(user)
        return super().chat_json(system=system, user=user, schema=schema, temperature=temperature)


# --------------------------------------------------------------------------
# 确定性质量门
# --------------------------------------------------------------------------


def test_outline_quality_all_pass() -> None:
    assert validate_series_outline(make_outline(), expected_episodes=4) == []
    assert hard_gate_issues(make_outline(), expected_episodes=4) == []


def test_outline_quality_missing_hooks() -> None:
    outline = make_outline()
    outline.episodes[1].ending_hook = ""  # 第 2 集非最后一集，卡点必填
    outline.episodes[2].opening_hook = "  "  # 空白钩子
    issues = validate_series_outline(outline)
    assert any("第 2 集" in i and "集尾卡点" in i for i in issues)
    assert any("第 3 集" in i and "开场钩子" in i for i in issues)


def test_outline_quality_highlight_gap() -> None:
    # 只有第 1 集有爽点：2-6 集连续 5 集真空（上限 3）
    outline = make_outline(episodes=6)
    for ep in outline.episodes:
        ep.highlight = "身份碾压·第 1 集限定" if ep.episode_no == 1 else ""
    issues = validate_series_outline(outline)
    assert any("集无爽点" in i for i in issues)


def test_outline_quality_climax_not_on_last() -> None:
    outline = make_outline(episodes=4, climax_episode=4)
    assert any("大爆点" in i for i in validate_series_outline(outline))
    # 短篇（<6 集）：软门，不拦确认
    assert not any("大爆点" in i for i in hard_gate_issues(outline))
    # 长篇（≥6 集）：硬门，拦确认
    long_outline = make_outline(episodes=6, climax_episode=6)
    assert any("大爆点" in i for i in hard_gate_issues(long_outline))
    assert not any(
        "大爆点" in i
        for i in hard_gate_issues(make_outline(episodes=6, climax_episode=5))
    )


def test_outline_quality_episode_count_mismatch() -> None:
    issues = validate_series_outline(make_outline(episodes=4), expected_episodes=6)
    assert any("不一致" in i for i in issues)


def test_hard_gate_excludes_soft_markers() -> None:
    outline = make_outline()
    # 软门：人物超编 + 梗概过薄
    outline.characters = [{"name": f"角色{i}", "profile": "路人"} for i in range(12)]
    outline.episodes[0].synopsis = "太短"
    hard = hard_gate_issues(outline, expected_episodes=4)
    assert hard == []
    soft = validate_series_outline(outline, expected_episodes=4)
    assert any("具名人物" in i for i in soft)
    assert any("梗概仅" in i for i in soft)


def test_outline_renumbered_and_truncated_deterministically(tmp_path) -> None:
    """LLM 跳号/超量时按顺序重排并截断到请求数。"""
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    series_row = svc.create_series("赘婿令牌", "想法", {"episode_count": 3})
    outline = make_outline(episodes=5)
    for index, ep in enumerate(outline.episodes):
        ep.episode_no = (index + 1) * 10  # 10/20/30/40/50 跳号
    llm = RecordingLlm(outline)
    job = svc.dispatch_series(series_row.series_id, "generate_series_outline")["jobs"][0]
    run_job(Handlers(ctx, Agents(llm)), job)
    saved = svc.get_series(series_row.series_id)
    assert [e.episode_no for e in saved.outline.episodes] == [1, 2, 3]
    # 截断后集数与请求一致（不再有「不一致」警告）；climax=4 指向被截掉的集，
    # 质量门会提示大爆点位置异常
    assert all("不一致" not in w for w in saved.outline.warnings)
    assert any("大爆点" in w for w in saved.outline.warnings)


# --------------------------------------------------------------------------
# 大纲生成 → 评审 → 确认流
# --------------------------------------------------------------------------


def test_outline_generation_flow(tmp_path) -> None:
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    series_row = svc.create_series("赘婿令牌", "落魄女婿是集团太子", {"episode_count": 4})
    assert series_row.status is SeriesStatus.CREATED

    result = svc.dispatch_series(series_row.series_id, "generate_series_outline")
    job = result["jobs"][0]
    assert job.type.value == "series_outline_gen"
    assert svc.get_series(series_row.series_id).status is SeriesStatus.OUTLINE_DRAFTING

    # 重入护栏：任务未终结时再派发被拒
    with pytest.raises(DomainError):
        svc.dispatch_series(series_row.series_id, "generate_series_outline")

    llm = RecordingLlm(make_outline())
    outcome = run_job(Handlers(ctx, Agents(llm)), job)
    assert outcome.status.value == "succeeded"
    saved = svc.get_series(series_row.series_id)
    assert saved.status is SeriesStatus.OUTLINE_READY
    assert saved.outline is not None and len(saved.outline.episodes) == 4
    assert saved.outline.warnings == []
    # 方法论注入大纲 system；user 携带集数与单集时长
    assert "爆款方法论" in llm.systems[0]
    assert "总集数=4" in llm.users[0]
    assert "单集目标时长=90" in llm.users[0]
    # LLM 调用记入 provider_calls（零币）
    calls = ctx.calls.list_by_job(job.job_id)
    assert calls and calls[0].kind == "llm"


def test_outline_approve_gate(tmp_path) -> None:
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    series_row = svc.create_series("剧", "想法", {"episode_count": 4})
    bad = make_outline()
    bad.episodes[0].opening_hook = ""  # 硬门：第 1 集缺开场钩子
    svc.dispatch_series(
        series_row.series_id, "edit_series_outline", {"outline": bad.model_dump(mode="json")}
    )
    assert svc.get_series(series_row.series_id).status is SeriesStatus.OUTLINE_READY
    with pytest.raises(DomainError) as exc_info:
        svc.dispatch_series(series_row.series_id, "approve_series_outline")
    assert exc_info.value.code == "VALIDATION_FAILED"
    assert any("开场钩子" in i for i in exc_info.value.details["issues"])

    # 修复后可确认；确认后编辑 → 回到 OUTLINE_READY
    good = make_outline()
    svc.dispatch_series(
        series_row.series_id, "edit_series_outline", {"outline": good.model_dump(mode="json")}
    )
    svc.dispatch_series(series_row.series_id, "approve_series_outline")
    assert svc.get_series(series_row.series_id).status is SeriesStatus.OUTLINE_APPROVED
    svc.dispatch_series(
        series_row.series_id, "edit_series_outline", {"outline": good.model_dump(mode="json")}
    )
    assert svc.get_series(series_row.series_id).status is SeriesStatus.OUTLINE_READY


def test_outline_approve_requires_ready(tmp_path) -> None:
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    series_row = svc.create_series("剧", "想法", {})
    with pytest.raises(DomainError) as exc_info:
        svc.dispatch_series(series_row.series_id, "approve_series_outline")
    assert exc_info.value.code == "STATE_ILLEGAL"


# --------------------------------------------------------------------------
# 批量生成分集剧本 + 连载法则注入
# --------------------------------------------------------------------------


def _approve_outline(svc, series_row) -> None:
    svc.dispatch_series(
        series_row.series_id,
        "edit_series_outline",
        {"outline": make_outline().model_dump(mode="json")},
    )
    svc.dispatch_series(series_row.series_id, "approve_series_outline")


def test_generate_episode_scripts_creates_projects(tmp_path) -> None:
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    series_row = svc.create_series("赘婿令牌", "想法", {"episode_count": 4, "per_episode_sec": 90})
    _approve_outline(svc, series_row)

    result = svc.dispatch_series(series_row.series_id, "generate_episode_scripts")
    assert len(result["jobs"]) == 4
    projects = {p.episode_no: p for p in ctx.projects.list_by_series(series_row.series_id)}
    assert set(projects) == {1, 2, 3, 4}
    for no, project in projects.items():
        assert project.series_id == series_row.series_id
        assert project.status is ProjectStatus.SCRIPT_DRAFTING
        assert project.params.target_duration_sec == 90
        assert f"第{no:02d}集" in project.title
    payload = result["jobs"][0].payload
    assert payload["series_id"] == series_row.series_id and payload["episode_no"] in {1, 2, 3, 4}

    # 全部已建集后再派发 → 拒绝（重roll 走项目自身的 generate_script）
    with pytest.raises(DomainError):
        svc.dispatch_series(series_row.series_id, "generate_episode_scripts")

    # 未确认大纲就派发 → 拒绝
    other = svc.create_series("另一部", "想法", {})
    with pytest.raises(DomainError) as exc_info:
        svc.dispatch_series(other.series_id, "generate_episode_scripts")
    assert exc_info.value.code == "STATE_ILLEGAL"


def test_episode_script_injects_series_laws(tmp_path) -> None:
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    series_row = svc.create_series("赘婿令牌", "想法", {"episode_count": 4})
    _approve_outline(svc, series_row)
    jobs = svc.dispatch_series(series_row.series_id, "generate_episode_scripts")["jobs"]
    job_ep2 = next(j for j in jobs if j.payload["episode_no"] == 2)

    llm = RecordingLlm(make_beat_script())
    handlers = Handlers(ctx, Agents(llm))
    outcome = run_job(handlers, job_ep2)
    assert outcome.status.value == "succeeded"
    # 连载法则 + 上集卡点衔接都注入；本集任务带了开场钩子/集尾卡点
    assert "连载模式追加法则" in llm.systems[0]
    assert "跨集悬置许可" in llm.systems[0]
    assert "上一集（第 1 集）结尾卡点" in llm.users[0]
    assert "开场钩子2" in llm.users[0]
    assert "集尾卡点2" in llm.users[0]

    project = ctx.projects.get(job_ep2.project_id)
    assert project.status is ProjectStatus.SCRIPT_READY
    version = ctx.scripts.latest_draft(project.project_id)
    assert version is not None and version.content.logline


def test_single_project_script_does_not_get_series_laws(tmp_path) -> None:
    """旧单项目流程不受连载法则影响（提示词升级对两轨一致）。"""
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    project = svc.create_project("渡口", "想法", {})
    result = svc.dispatch(project.project_id, "generate_script")
    job = result["jobs"][0]
    llm = RecordingLlm(make_beat_script())
    run_job(Handlers(ctx, Agents(llm)), job)
    assert "连载模式追加法则" not in llm.systems[0]
    assert "黄金开局" in llm.systems[0]
    # TASK-048 提质：口语化台词上限进入法则
    assert "硬上限 25 字" in llm.systems[0]


# --------------------------------------------------------------------------
# API 路由冒烟（/api/series）
# --------------------------------------------------------------------------


def test_series_api_routes(tmp_path) -> None:
    import shutil as _shutil
    from fastapi.testclient import TestClient

    from server.api.main import create_app

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'api.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    client = TestClient(create_app(settings, engine))

    resp = client.post(
        "/api/series",
        json={"title": "赘婿令牌", "idea": "想法", "params": {"episode_count": 4}},
    )
    assert resp.status_code == 201
    sid = resp.json()["series"]["id"]
    assert resp.json()["series"]["status"] == "created"

    listed = client.get("/api/series").json()["series"]
    assert any(s["id"] == sid for s in listed)

    detail = client.get(f"/api/series/{sid}").json()["series"]
    assert detail["episodes"] == []

    # 未知系列命令 → 错误信封
    bad = client.post(f"/api/series/{sid}/commands", json={"type": "nope"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # 手写大纲：edit 让 CREATED → OUTLINE_READY（LLM 生成路径已在上游覆盖）
    outline = make_outline(episodes=4).model_dump(mode="json")
    client.post(
        f"/api/series/{sid}/commands",
        json={"type": "edit_series_outline", "payload": {"outline": outline}},
    )
    assert client.get(f"/api/series/{sid}").json()["series"]["status"] == "outline_ready"
    client.post(f"/api/series/{sid}/commands", json={"type": "approve_series_outline"})
    result = client.post(
        f"/api/series/{sid}/commands", json={"type": "generate_episode_scripts"}
    )
    assert len(result.json()["jobs"]) == 4
    detail = client.get(f"/api/series/{sid}").json()["series"]
    assert all(row["project"] is not None for row in detail["episodes"])
    assert detail["episodes"][0]["project"]["title"].startswith("赘婿令牌·第01集")


def test_generate_assets_auto_inherits_in_series(tmp_path) -> None:
    """同系列存在带批准图的兄弟集时，「抽取资产」自动变为免费继承（一致性优先）。"""
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    series_row = svc.create_series("赘婿令牌", "想法", {"episode_count": 2})
    svc.dispatch_series(
        series_row.series_id,
        "edit_series_outline",
        {"outline": make_outline(episodes=2).model_dump(mode="json")},
    )
    svc.dispatch_series(series_row.series_id, "approve_series_outline")
    jobs = svc.dispatch_series(series_row.series_id, "generate_episode_scripts")["jobs"]
    llm = RecordingLlm(make_beat_script())
    handlers = Handlers(ctx, Agents(llm))
    for job in jobs:
        run_job(handlers, job)
    projects = {p.episode_no: p for p in ctx.projects.list_by_series(series_row.series_id)}
    ep1, ep2 = projects[1], projects[2]
    for p in (ep1, ep2):
        svc.dispatch(p.project_id, "approve_script")

    asset = Asset(
        asset_id="asset-src-1",
        project_id=ep1.project_id,
        kind=AssetKind.CHARACTER,
        name="苏母（王秀兰）",
        visual_anchor="qipao dress",
    )
    ctx.assets.add_asset(asset)
    ctx.assets.add_image(
        AssetImage(
            asset_image_id="img-src-1",
            asset_id=asset.asset_id,
            version_no=1,
            view_label="主设定",
            file_path="media/ep1/mother.png",
            status=AssetImageStatus.READY,
            approved=True,
        )
    )
    # 模拟第1集「抽取完成」态（ASSET_DRAFTING→ASSET_READY）后过确认门
    # （用最新实体推进：ep1 快照是脚本确认前的状态）
    from server.domain.project import apply_action as _apply

    fresh_ep1 = ctx.projects.get(ep1.project_id)
    ctx.projects.save(_apply(_apply(fresh_ep1, "generate_assets"), "assets_generated"))
    svc.dispatch(ep1.project_id, "approve_assets")

    # ep2 点「抽取资产」→ 自动继承 ep1 的基准卡：零任务、零成本、同一份图
    result = svc.dispatch(ep2.project_id, "generate_assets")
    assert result["ok"] is True
    assert result["inherited_from"]["project_id"] == ep1.project_id
    assert result["copied_assets"] == 1
    assert result["project"].status is ProjectStatus.ASSET_READY
    ep2_assets = ctx.assets.list_assets(ep2.project_id)
    assert [a.name for a in ep2_assets] == ["苏母（王秀兰）"]
    ep2_images = ctx.assets.list_images(project_id=ep2.project_id)
    assert ep2_images[0].file_path == "media/ep1/mother.png"
    # 没有产生任何 image_gen / asset_extract 任务（零扣币）
    assert not [
        j
        for j in ctx.jobs.list_all()
        if j.project_id == ep2.project_id and j.type.value in ("image_gen", "asset_extract")
    ]

    # 独立项目（无系列）不受影响：照常走 LLM 抽取入队
    lone = svc.create_project("渡口", "想法", {})
    run_job(handlers, svc.dispatch(lone.project_id, "generate_script")["jobs"][0])
    svc.dispatch(lone.project_id, "approve_script")
    result = svc.dispatch(lone.project_id, "generate_assets")
    assert result["jobs"][0].type.value == "asset_extract"

    # force_extract=true：系列集也走真抽取（重写卡片 + 重新生图）
    result = svc.dispatch(ep2.project_id, "generate_assets", {"force_extract": True})
    assert result["jobs"][0].type.value == "asset_extract"


# --------------------------------------------------------------------------
# 资产继承（跨集零成本复制）
# --------------------------------------------------------------------------


def test_inherit_series_assets(tmp_path) -> None:
    _, ctx = make_ctx(tmp_path)
    svc = WorkbenchService(ctx)
    series_row = svc.create_series("赘婿令牌", "想法", {"episode_count": 2})
    svc.dispatch_series(
        series_row.series_id,
        "edit_series_outline",
        {"outline": make_outline(episodes=2).model_dump(mode="json")},
    )
    svc.dispatch_series(series_row.series_id, "approve_series_outline")
    jobs = svc.dispatch_series(series_row.series_id, "generate_episode_scripts")["jobs"]

    llm = RecordingLlm(make_beat_script())
    handlers = Handlers(ctx, Agents(llm))
    for job in jobs:
        run_job(handlers, job)

    # 第 1 集走完剧本确认 + 资产（直接造已批准的资产行，模拟抽取完成态）
    projects = {p.episode_no: p for p in ctx.projects.list_by_series(series_row.series_id)}
    ep1, ep2 = projects[1], projects[2]
    svc.dispatch(ep1.project_id, "approve_script")
    asset = Asset(
        asset_id="asset-src-1",
        project_id=ep1.project_id,
        kind=AssetKind.CHARACTER,
        name="陈平",
        visual_anchor="black suit",
    )
    ctx.assets.add_asset(asset)
    ctx.assets.add_image(
        AssetImage(
            asset_image_id="img-src-1",
            asset_id=asset.asset_id,
            version_no=1,
            view_label="主设定",
            file_path="media/p1/asset.png",
            status=AssetImageStatus.READY,
            approved=True,
        )
    )

    # 目标集未确认剧本 → 拒绝
    with pytest.raises(DomainError) as exc_info:
        svc.dispatch_series(
            series_row.series_id,
            "inherit_series_assets",
            {"from_episode_no": 1, "to_episode_no": 2},
        )
    assert exc_info.value.code == "STATE_ILLEGAL"

    svc.dispatch(ep2.project_id, "approve_script")
    result = svc.dispatch_series(
        series_row.series_id,
        "inherit_series_assets",
        {"from_episode_no": 1, "to_episode_no": 2},
    )
    assert result["copied_assets"] == 1 and result["copied_images"] == 1
    assert result["project"].status is ProjectStatus.ASSET_READY

    target_assets = ctx.assets.list_assets(ep2.project_id)
    assert len(target_assets) == 1
    assert target_assets[0].asset_id != asset.asset_id  # 新 id
    assert target_assets[0].name == "陈平"
    target_images = ctx.assets.list_images(project_id=ep2.project_id)
    assert target_images[0].file_path == "media/p1/asset.png"  # 同一份磁盘文件，零成本
    assert target_images[0].approved is True


def test_pick_primary_image_prefers_latest_approved() -> None:
    """同标签层取最新批准（用户重画批准新图后，关键帧/视频自动用新形象）。"""
    from server.app.usecases import _pick_primary_image
    from server.domain.entities import AssetImage

    old_main = AssetImage(
        asset_image_id="old", asset_id="a", version_no=1, view_label="主设定"
    )
    new_main = AssetImage(
        asset_image_id="new", asset_id="a", version_no=2, view_label="主设定"
    )
    assert _pick_primary_image([old_main, new_main], AssetKind.CHARACTER) is new_main

    # 标签层优先于新旧：「主设定」胜过更新的非主设定图
    other = AssetImage(
        asset_image_id="other", asset_id="a", version_no=3, view_label="半身像"
    )
    assert _pick_primary_image([old_main, other], AssetKind.CHARACTER) is old_main
