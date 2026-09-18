"""TASK-030 双轨剧作法与分镜细节规则：提示词注入 + 基调参数校验。"""

import pytest
from pydantic import ValidationError

from server.app.agents import (
    STORYBOARD_CRAFT_RULES,
    Agents,
)
from server.domain.entities import ProjectParams
from server.domain.enums import DramaticTone
from server.tests.test_beat_flow import OneShotLlm, make_beat_script

# --------------------------------------------------------------------------
# ScriptAgent：剧作基调 → 提示词注入
# --------------------------------------------------------------------------


class _CapturingLlm(OneShotLlm):
    """记录 system 提示词，供断言注入内容。"""

    def __init__(self, data) -> None:
        super().__init__(data)
        self.systems: list[str] = []

    def chat_json(self, *, system, user, schema, temperature):  # noqa: D102
        self.systems.append(system)
        return super().chat_json(
            system=system, user=user, schema=schema, temperature=temperature
        )


def _run_script(params: dict):
    llm = _CapturingLlm(make_beat_script())
    content = Agents(llm).run_script("想法", params)
    return content, llm.systems[-1]


def test_run_script_defaults_to_hook_tone() -> None:
    """不传基调时默认走 hook 法则（黄金开局/反派惩罚铁律）。"""
    _, system = _run_script({})
    assert "短剧钩子驱动" in system
    assert "黄金开局" in system
    assert "反派惩罚铁律" in system
    assert "微电影三幕式" not in system
    # 两轨通用纪律恒在
    assert "情绪靠动作外化" in system


def test_run_script_hook_tone_explicit() -> None:
    _, system = _run_script({"dramatic_tone": "hook"})
    assert "高频反转" in system
    assert "台词千人千面" in system


def test_run_script_three_act_tone() -> None:
    _, system = _run_script({"dramatic_tone": "three_act"})
    assert "微电影三幕式" in system
    assert "单一核心事件" in system
    assert "三幕配比" in system
    assert "允许留白" in system
    assert "对立面可意象化" in system
    # hook 专属法则不得出现
    assert "黄金开局" not in system
    assert "反派惩罚铁律" not in system


def test_run_script_unknown_tone_falls_back_to_hook() -> None:
    _, system = _run_script({"dramatic_tone": "whatever"})
    assert "短剧钩子驱动" in system


# --------------------------------------------------------------------------
# StoryboardAgent：镜头写作细节规则常量
# --------------------------------------------------------------------------


def test_storyboard_craft_rules_cover_doubao_mechanisms() -> None:
    """POV 归属 / 禁群体量词 / 方向性物体六要素 / 情绪外化 / 光影意图 全在列。"""
    assert "POV 归属" in STORYBOARD_CRAFT_RULES
    assert "不得出现该角色自己的完整正脸" in STORYBOARD_CRAFT_RULES
    assert "禁群体量词" in STORYBOARD_CRAFT_RULES
    assert "逐个点名" in STORYBOARD_CRAFT_RULES
    assert "方向性物体六要素" in STORYBOARD_CRAFT_RULES
    for element in ("人物朝向", "视线方向", "物体朝向", "手部", "哪一面", "距离"):
        assert element in STORYBOARD_CRAFT_RULES
    assert "情绪外化" in STORYBOARD_CRAFT_RULES
    assert "光影意图" in STORYBOARD_CRAFT_RULES


def test_run_storyboard_system_includes_craft_rules() -> None:
    """run_storyboard 的 system 提示词拼接了细节规则（借捕获 LLM 验证拼接点）。"""
    from server.tests.factories import make_asset
    from server.tests.test_app_flow import make_script_content

    script = make_script_content()
    assets = [make_asset("a-1", "character", "老船工")]

    class _SbCaptureLlm:
        def __init__(self) -> None:
            self.systems: list[str] = []

        def chat_json(self, *, system, user, schema, temperature):
            self.systems.append(system)
            # 只验证 system 内容，拿到即抛，避免走进完整校验链
            raise AssertionError(system)

        def chat(self, **kwargs):  # pragma: no cover
            return "pong"

    llm = _SbCaptureLlm()
    agents = Agents(llm)
    with pytest.raises(AssertionError, match="POV 归属"):
        agents.run_storyboard(
            script, assets, {"target_duration_sec": 12, "style": "cinematic"}
        )
    assert llm.systems, "run_storyboard 应至少调用一次 LLM"
    assert "方向性物体六要素" in llm.systems[0]


# --------------------------------------------------------------------------
# ProjectParams / update_params：基调参数
# --------------------------------------------------------------------------


def test_project_params_tone_default_and_coercion() -> None:
    params = ProjectParams()
    assert params.dramatic_tone is DramaticTone.HOOK
    assert ProjectParams(dramatic_tone="three_act").dramatic_tone is DramaticTone.THREE_ACT
    with pytest.raises(ValidationError):
        ProjectParams(dramatic_tone="invalid")


def test_update_params_dramatic_tone(tmp_path) -> None:
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.errors import DomainError
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'tone.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    project = svc.create_project("渡口", "想法", {})

    result = svc.dispatch(project.project_id, "update_params", {"dramatic_tone": "three_act"})
    assert result["project"].params.dramatic_tone is DramaticTone.THREE_ACT

    with pytest.raises(DomainError) as exc_info:
        svc.dispatch(project.project_id, "update_params", {"dramatic_tone": "nope"})
    assert exc_info.value.code == "VALIDATION_ERROR"
