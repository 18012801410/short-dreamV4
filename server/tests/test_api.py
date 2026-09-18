"""REST API 冒烟测试（TASK-008-012）：错误信封与命令端点。"""

import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from server.api.main import create_app
from server.infra.config import Settings
from server.infra.tables import metadata

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="需要 ffmpeg"
)


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'api.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    return TestClient(create_app(settings, engine))


def test_create_and_get_project(client: TestClient):
    resp = client.post("/api/projects", json={"title": "渡口夜行", "idea": "老船工"})
    assert resp.status_code == 201
    pid = resp.json()["project"]["id"]
    assert resp.json()["project"]["status"] == "CREATED"

    got = client.get(f"/api/projects/{pid}")
    assert got.status_code == 200
    assert got.json()["title"] == "渡口夜行"


def test_command_state_illegal_envelope(client: TestClient):
    pid = client.post("/api/projects", json={"title": "t", "idea": "i"}).json()["project"]["id"]
    resp = client.post(f"/api/projects/{pid}/commands", json={"type": "approve_script"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "STATE_ILLEGAL"


def test_generate_script_enqueues_job(client: TestClient):
    pid = client.post("/api/projects", json={"title": "t", "idea": "i"}).json()["project"]["id"]
    resp = client.post(f"/api/projects/{pid}/commands", json={"type": "generate_script"})
    assert resp.status_code == 200
    assert resp.json()["jobs"][0]["type"] == "script_gen"
    jobs = client.get(f"/api/projects/{pid}/jobs").json()["jobs"]
    assert jobs and jobs[0]["status"] == "pending"


def test_unknown_command_rejected(client: TestClient):
    pid = client.post("/api/projects", json={"title": "t", "idea": "i"}).json()["project"]["id"]
    resp = client.post(f"/api/projects/{pid}/commands", json={"type": "fly"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_generation_reentry_guard_and_failure_recovery(tmp_path: Path):
    """TASK-032 生成类动作重入契约（两层）：
    ①仍有未终结任务 → 拒绝重复派发（关键帧等阶段重入=重复烧图钱）；
    ②上次任务已终结（失败）→ 允许重入恢复，项目不再卡死在忙碌态。
    """
    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'reentry.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    client = TestClient(create_app(settings, engine))
    pid = client.post(
        "/api/projects", json={"title": "t", "idea": "i"}
    ).json()["project"]["id"]

    first = client.post(f"/api/projects/{pid}/commands", json={"type": "generate_script"})
    assert first.status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["status"] == "SCRIPT_DRAFTING"

    # ① 任务未终结：忙碌态重入被任务护栏拒绝（证据是任务表，不是状态机）
    blocked = client.post(f"/api/projects/{pid}/commands", json={"type": "generate_script"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "STATE_ILLEGAL"

    # ② 任务失败终结后：允许重入恢复
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE jobs SET status='failed' WHERE project_id=:p"), {"p": pid})
    recovered = client.post(f"/api/projects/{pid}/commands", json={"type": "generate_script"})
    assert recovered.status_code == 200
    assert recovered.json()["jobs"][0]["type"] == "script_gen"


def test_script_endpoint_empty(client: TestClient):
    pid = client.post("/api/projects", json={"title": "t", "idea": "i"}).json()["project"]["id"]
    body = client.get(f"/api/projects/{pid}/script").json()
    assert body == {"active": None, "draft": None, "versions": []}


def test_settings_roundtrip_and_mask(client: TestClient):
    body = client.get("/api/settings").json()
    assert "llm_api_key" in body
    resp = client.put("/api/settings", json={"llm_model": "deepseek-chat"})
    assert resp.status_code == 200
    assert client.get("/api/settings").json()["llm_model"] == "deepseek-chat"
