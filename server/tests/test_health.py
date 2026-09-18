"""健康端点冒烟测试（TASK-002）。"""

from fastapi.testclient import TestClient

from server.api.main import create_app


def test_health_reports_core_dependencies() -> None:
    client = TestClient(create_app())
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["db"] in {"ok", "error"}
    assert set(body["providers_configured"]) == {"minimax", "llm", "runninghub"}
    assert set(body["ffmpeg"]) == {"found", "path", "version"}
