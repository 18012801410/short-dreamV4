"""VideoProvider 适配器单测（TASK-007）：MockTransport + 注入探针。"""

import json
from pathlib import Path

import httpx
import pytest

from server.adapters.runninghub import RunningHubClient, RunningHubError
from server.adapters.video import (
    VIDEO_NODE_ID,
    RunningHubVideo,
    build_timeline,
    extract_subject_definitions,
    frames_for_duration,
)
from server.domain.providers import VideoProvider

WORKFLOW_ID = "wf-video"
PROMPT = (
    "subject_definitions: <Subject 1> is the old ferryman in <Picture 1>, with a "
    "weathered face.\n"
    "summary: [reference generation] He pushes the boat and speaks.\n"
    "retention_analysis: <Picture 1> (appears in [Shot 1]): fully_preserved - framing.\n"
    "detailed_description: [Shot 1] Night pier. [Shot 2] At 00:05.000, <Subject 1> "
    "(S1) says <d>[English] the river remembers</d>.\n"
    "overall_soundscape: Water lapping.\n"
    "non_diegetic_music: Sparse low strings."
)


def make_client(responses: list[httpx.Response], requests: list[dict]) -> RunningHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("content-type") == "application/json":
            requests.append(json.loads(request.content))
        else:
            requests.append({"url": str(request.url)})
        return responses.pop(0)

    return RunningHubClient(
        base_url="https://www.runninghub.cn",
        api_key="rh-key",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        poll_interval=0.0,
    )


def api(code: int, data, msg: str = "success") -> httpx.Response:
    return httpx.Response(200, json={"code": code, "msg": msg, "data": data})


def make_template() -> dict:
    return {
        "segments": [
            {
                "id": "seg0",
                "start": 0,
                "length": 243,
                "durationSec": 10,
                "prompt": "old",
                "continuityFromPrev": True,
                "refs": [{"index": 0, "imageFile": "stale.png"}],
            },
            {"id": "seg1", "start": 243, "length": 124, "durationSec": 5, "prompt": "old2"},
        ],
        "totalFrames": 367,
        "durationSec": 20,
        "video": {"sourceFrameCount": 367},
        "global": {"prompt": "old-global", "refs": [{"index": 0, "imageFile": "stale.png"}]},
        "output": {"audioMode": "source", "aspectRatio": "16:9 (宽屏)"},
        "batchWorkspaces": {
            "r2v": {
                "segments": [{"id": "seg0", "prompt": "old"}],
                "globalCommon": {"prompt": "", "refs": []},
            }
        },
        "runSelectEnabled": False,
        "runSelection": [],
    }


def make_provider(
    responses: list[httpx.Response], requests: list[dict], probed: dict
) -> RunningHubVideo:
    client = make_client(responses, requests)
    return RunningHubVideo(
        client=client,
        workflow_id=WORKFLOW_ID,
        timeline_template=make_template(),
        probe=lambda _path: probed,  # type: ignore[arg-type]
    )


def test_frames_for_duration_matches_measured_budget() -> None:
    assert frames_for_duration(10) == 243  # TASK-001 实测口径


def test_extract_subject_definitions_block() -> None:
    extracted = extract_subject_definitions(PROMPT)
    assert extracted.startswith("<Subject 1> is the old ferryman")
    assert "summary:" not in extracted and "retention_analysis" not in extracted
    assert extract_subject_definitions("no marker here") == ""


def test_build_timeline_single_segment_and_refs() -> None:
    timeline = json.loads(
        build_timeline(
            make_template(),
            prompt=PROMPT,
            duration_sec=10,
            ref_file_names=["openapi/a.png", "openapi/b.png"],
            total_frames=243,
        )
    )
    assert len(timeline["segments"]) == 1
    seg = timeline["segments"][0]
    assert seg["prompt"] == PROMPT
    assert seg["continuityFromPrev"] is False and seg["refs"] == []
    assert timeline["totalFrames"] == 243 and timeline["durationSec"] == 10
    assert timeline["global"]["refs"] == [
        {"index": 0, "imageFile": "openapi/a.png",
         "fileName": "", "type": "input", "subfolder": ""},
        {"index": 1, "imageFile": "openapi/b.png",
         "fileName": "", "type": "input", "subfolder": ""},
    ]
    assert timeline["global"]["prompt"].startswith("<Subject 1>")
    assert timeline["output"]["audioMode"] == "generate"
    assert timeline["runSelection"] == [0]
    r2v = timeline["batchWorkspaces"]["r2v"]
    assert r2v["segments"][0]["prompt"] == PROMPT and r2v["runSelection"] == [0]


def test_generate_full_chain_with_probe(tmp_path: Path) -> None:
    requests: list[dict] = []
    responses = [
        api(0, {"fileName": "openapi/ref.png", "type": "image"}),  # upload
        api(0, {"taskId": "t-9"}),  # create
        api(0, "RUNNING"),
        api(0, "SUCCESS"),
        api(0, [{"fileUrl": "https://rh.test/v_pre.mp4", "nodeId": "20", "consumeCoins": "51"},
                {"fileUrl": "https://rh.test/v_final.mp4", "nodeId": "7", "consumeCoins": "51",
                 "taskCostTime": "252"}]),
        httpx.Response(200, content=b"mp4-bytes"),  # download
    ]
    provider = make_provider(responses, requests, {"width": 1920, "height": 1088,
                                                   "duration_sec": 10.125, "has_audio": True})
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"img")
    dest = tmp_path / "media" / "p-1" / "segments" / "s01.mp4"

    result = provider.generate(
        prompt=PROMPT, duration_sec=10, reference_files=[ref], dest=dest
    )

    assert dest.read_bytes() == b"mp4-bytes"
    assert result.provider_task_id == "t-9"
    assert result.duration_sec == 10.125 and result.has_audio
    assert result.usage.coins == "51"
    # upload 是首个请求（multipart，仅 URL 记录），create 紧随其后
    assert requests[0]["url"].endswith("/openapi/v2/media/upload/binary")
    create_req = requests[1]
    assert create_req.get("apiKey") == "rh-key"
    assert create_req["workflowId"] == WORKFLOW_ID
    fields = {
        (n["nodeId"], n["fieldName"]): n["fieldValue"]
        for n in create_req["nodeInfoList"]
    }
    assert fields[(VIDEO_NODE_ID, "total_frames")] == 243
    assert fields[(VIDEO_NODE_ID, "global_prompt")].startswith("<Subject 1>")
    timeline = json.loads(fields[(VIDEO_NODE_ID, "timeline_data")])
    assert timeline["global"]["refs"][0]["imageFile"] == "openapi/ref.png"


def test_generate_without_references_unsupported(tmp_path: Path) -> None:
    provider = make_provider([], [], {})
    with pytest.raises(RunningHubError) as excinfo:
        provider.generate(
            prompt=PROMPT, duration_sec=5, reference_files=[], dest=tmp_path / "v.mp4"
        )
    assert excinfo.value.code == "UNSUPPORTED_MODE"


def test_video_provider_protocol_satisfied(tmp_path: Path) -> None:
    provider = make_provider([], [], {})
    assert isinstance(provider, VideoProvider)
