"""RunningHubClient 与 ImageProvider 适配器单测（TASK-006）：MockTransport。"""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel  # noqa: F401  (占位保持 import 结构一致)

from server.adapters.image import RunningHubImage, build_image_provider_from_settings
from server.adapters.runninghub import RunningHubClient, RunningHubError
from server.domain.providers import ImageProvider
from server.infra.config import Settings

WORKFLOW_ID = "wf-image"


def make_transport(responses: list[httpx.Response], requests: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        requests.append(json.loads(body) if body else {"url": str(request.url)})
        return responses.pop(0)

    return httpx.MockTransport(handler)


def make_client(responses: list[httpx.Response], requests: list[dict]) -> RunningHubClient:
    return RunningHubClient(
        base_url="https://www.runninghub.cn",
        api_key="rh-key",
        transport=make_transport(responses, requests),
        sleep=lambda _s: None,
        poll_interval=0.0,
    )


def api(code: int, data, msg: str = "success") -> httpx.Response:
    return httpx.Response(200, json={"code": code, "msg": msg, "data": data})


def status(state: str) -> httpx.Response:
    return api(0, state)


def outputs(*urls: str, **extra) -> httpx.Response:
    items = [
        {"fileUrl": url, "fileType": url.rsplit(".", 1)[-1], "nodeId": "18",
         "consumeCoins": "21", "taskCostTime": "104", **extra}
        for url in urls
    ]
    return api(0, items)


def test_generate_full_chain_lands_file(tmp_path: Path) -> None:
    requests: list[dict] = []
    responses = [
        api(0, {"taskId": "t-1", "taskStatus": "RUNNING"}),  # create
        status("RUNNING"),
        status("SUCCESS"),
        outputs("https://rh.test/out/img.png"),
        httpx.Response(200, content=b"png-bytes"),  # download
    ]
    client = make_client(responses, requests)
    provider = RunningHubImage(client=client, workflow_id=WORKFLOW_ID)
    dest = tmp_path / "media" / "p-1" / "assets" / "a.png"

    result = provider.generate(prompt="a ferryman", width=1080, height=1920, dest=dest)

    assert dest.read_bytes() == b"png-bytes"
    assert result.provider_task_id == "t-1"
    assert result.size_bytes == len(b"png-bytes")
    assert result.usage.coins == "21" and result.usage.task_seconds == "104"
    create_req = requests[0]
    assert create_req["workflowId"] == WORKFLOW_ID
    assert create_req["apiKey"] == "rh-key"
    fields = {(n["nodeId"], n["fieldName"]): n["fieldValue"] for n in create_req["nodeInfoList"]}
    assert fields[("6", "text")] == "a ferryman"
    assert fields[("8", "width")] == 1080
    assert fields[("8", "height")] == 1920
    assert ("5", "seed") in fields


def test_create_rejected_433(tmp_path: Path) -> None:
    requests: list[dict] = []
    client = make_client(
        [api(433, None, msg="提示词审核未通过")], requests
    )
    provider = RunningHubImage(client=client, workflow_id=WORKFLOW_ID)

    with pytest.raises(RunningHubError) as excinfo:
        provider.generate(prompt="x", width=1, height=1, dest=tmp_path / "a.png")
    assert excinfo.value.code == "REJECTED"
    assert "审核" in excinfo.value.message


def test_failed_task_surfaces_failed_reason(tmp_path: Path) -> None:
    requests: list[dict] = []
    failed_reason = {"nodeType": "KSampler", "exception_message": "OOM"}
    responses = [
        api(0, {"taskId": "t-2"}),  # create
        status("FAILED"),
        api(805, {"failedReason": failed_reason}, msg="APIKEY_TASK_STATUS_ERROR"),
    ]
    client = make_client(responses, requests)
    provider = RunningHubImage(client=client, workflow_id=WORKFLOW_ID)

    with pytest.raises(RunningHubError) as excinfo:
        provider.generate(prompt="x", width=1, height=1, dest=tmp_path / "a.png")
    assert excinfo.value.code == "TASK_FAILED"
    assert excinfo.value.details["failedReason"] == failed_reason


def test_poll_timeout_cancels_task(tmp_path: Path) -> None:
    requests: list[dict] = []
    responses = [
        api(0, {"taskId": "t-3"}),  # create
        status("RUNNING"),
        api(0, None),  # cancel
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        requests.append(json.loads(body) if body else {"url": str(request.url)})
        return responses.pop(0)

    client = RunningHubClient(
        base_url="https://www.runninghub.cn",
        api_key="rh-key",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        poll_interval=0.0,
        poll_timeout=0.0,  # 立即超时
    )
    provider = RunningHubImage(client=client, workflow_id=WORKFLOW_ID)

    with pytest.raises(RunningHubError) as excinfo:
        provider.generate(prompt="x", width=1, height=1, dest=tmp_path / "a.png")

    assert excinfo.value.code == "TIMEOUT"
    cancel_req = requests[-1]
    assert cancel_req.get("taskId") == "t-3"


def test_upload_uses_bearer_header_only(tmp_path: Path) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["body_head"] = request.content[:200]
        return httpx.Response(
            200,
            json={"code": 0, "data": {"fileName": "openapi/abc.png", "type": "image"}},
        )

    client = RunningHubClient(
        base_url="https://www.runninghub.cn",
        api_key="rh-key",
        transport=httpx.MockTransport(handler),
    )
    file = tmp_path / "ref.png"
    file.write_bytes(b"img")

    name = client.upload(file)

    assert name == "openapi/abc.png"
    assert captured["auth"] == "Bearer rh-key"
    assert b"rh-key" not in captured["body_head"]


def test_image_provider_protocol_satisfied(tmp_path: Path) -> None:
    client = make_client([], [])
    assert isinstance(RunningHubImage(client=client, workflow_id=WORKFLOW_ID), ImageProvider)


def test_factory_requires_api_key() -> None:
    settings = Settings(runninghub_api_key="", _env_file=None)
    with pytest.raises(RunningHubError) as excinfo:
        build_image_provider_from_settings(settings)
    assert excinfo.value.code == "AUTH"


# --------------------------------------------------------------------------
# Edit 参考版（TASK-031）：关键帧图生图，参考图上传 + 切工作流 + prompt 字段
# --------------------------------------------------------------------------


def test_generate_edit_branch_uploads_references(tmp_path: Path) -> None:
    ref = tmp_path / "card.png"
    ref.write_bytes(b"fake-card")
    dest = tmp_path / "out" / "kf.png"

    captured: dict = {}
    upload_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/openapi/v2/media/upload/binary":
            upload_count["n"] += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "success",
                    "data": {"fileName": f"openapi/ref{upload_count['n']}.png"},
                },
            )
        if path == "/task/openapi/create":
            captured["create"] = json.loads(request.content)
            return api(0, {"taskId": "t-edit", "taskStatus": "RUNNING"})
        if path == "/task/openapi/status":
            return status("SUCCESS")
        if path == "/task/openapi/outputs":
            return outputs("https://rh.test/out/kf_result.png")
        return httpx.Response(200, content=b"png-bytes")

    client = RunningHubClient(
        base_url="https://www.runninghub.cn",
        api_key="rh-key",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        poll_interval=0.0,
    )
    provider = RunningHubImage(
        client=client, workflow_id="wf-t2i", edit_workflow_id="wf-edit"
    )
    result = provider.generate(
        prompt="keep the same face, medium shot at the pier",
        width=1080,
        height=1920,
        dest=dest,
        seed=42,
        negative="text, watermark",
        reference_files=[ref],
    )

    assert result.provider_task_id == "t-edit"
    assert dest.read_bytes() == b"png-bytes"
    body = captured["create"]
    assert body["workflowId"] == "wf-edit"
    entries = {(n["nodeId"], n["fieldName"]): n["fieldValue"] for n in body["nodeInfoList"]}
    # 正向/负向走 prompt 字段（TextEncodeQwenImageEditPlus），不再是 text
    assert entries[("6", "prompt")] == "keep the same face, medium shot at the pier"
    assert entries[("7", "prompt")] == "text, watermark"
    assert entries[("5", "seed")] == 42
    assert entries[("8", "width")] == 1080 and entries[("8", "height")] == 1920
    # 参考图 3 槽位：1 张上传后复用填充（无场景时只能复制角色卡）
    assert entries[("10", "image")] == "openapi/ref1.png"
    assert entries[("11", "image")] == "openapi/ref1.png"
    assert entries[("12", "image")] == "openapi/ref1.png"


def test_generate_edit_branch_pads_with_last_reference(tmp_path: Path) -> None:
    """TASK-045：参考不足 3 张时用**最后一张**补槽，不再复制第一张。

    参考顺序是 [角色卡, …, 场景卡]：复制人物卡会把这个人"复印"进画面
    （实测 S02G02：乙的卡被复制后，画里出现两个一模一样的乙）；
    复制场景卡只是让背景条件重复，不会造人。
    """
    ref_a = tmp_path / "char_card.png"
    ref_a.write_bytes(b"char-card")
    ref_b = tmp_path / "scene_card.png"
    ref_b.write_bytes(b"scene-card")
    dest = tmp_path / "out" / "kf.png"

    captured: dict = {}
    upload_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/openapi/v2/media/upload/binary":
            upload_count["n"] += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "success",
                    "data": {"fileName": f"openapi/ref{upload_count['n']}.png"},
                },
            )
        if path == "/task/openapi/create":
            captured["create"] = json.loads(request.content)
            return api(0, {"taskId": "t-edit", "taskStatus": "RUNNING"})
        if path == "/task/openapi/status":
            return status("SUCCESS")
        if path == "/task/openapi/outputs":
            return outputs("https://rh.test/out/kf_result.png")
        return httpx.Response(200, content=b"png-bytes")

    client = RunningHubClient(
        base_url="https://www.runninghub.cn",
        api_key="rh-key",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        poll_interval=0.0,
    )
    provider = RunningHubImage(
        client=client, workflow_id="wf-t2i", edit_workflow_id="wf-edit"
    )
    provider.generate(
        prompt="keep the same face",
        width=1080,
        height=1920,
        dest=dest,
        negative="text",
        reference_files=[ref_a, ref_b],
    )

    entries = {
        (n["nodeId"], n["fieldName"]): n["fieldValue"]
        for n in captured["create"]["nodeInfoList"]
    }
    # [角色卡, 场景卡] → 第三槽补场景卡的复制，而不是再复制一份角色卡
    assert entries[("10", "image")] == "openapi/ref1.png"
    assert entries[("11", "image")] == "openapi/ref2.png"
    assert entries[("12", "image")] == "openapi/ref2.png"


def test_generate_without_references_keeps_t2i_path(tmp_path: Path) -> None:
    """未传参考图 = 纯文生图路径（即使配置了 edit 工作流也不切换）。"""
    dest = tmp_path / "out" / "kf.png"
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/task/openapi/create":
            captured["create"] = json.loads(request.content)
            return api(0, {"taskId": "t-t2i", "taskStatus": "RUNNING"})
        if path == "/task/openapi/status":
            return status("SUCCESS")
        if path == "/task/openapi/outputs":
            return outputs("https://rh.test/out/kf2.png")
        return httpx.Response(200, content=b"png-bytes")

    client = RunningHubClient(
        base_url="https://www.runninghub.cn",
        api_key="rh-key",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        poll_interval=0.0,
    )
    provider = RunningHubImage(
        client=client, workflow_id="wf-t2i", edit_workflow_id="wf-edit"
    )
    provider.generate(
        prompt="a pier at night", width=1920, height=1080, dest=dest, seed=7
    )
    body = captured["create"]
    assert body["workflowId"] == "wf-t2i"
    entries = {(n["nodeId"], n["fieldName"]): n["fieldValue"] for n in body["nodeInfoList"]}
    assert entries[("6", "text")] == "a pier at night"
