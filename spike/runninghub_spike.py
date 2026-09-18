"""TASK-001 Spike：RunningHub 工作流契约实测（用后即弃）。

用途：
  1) QWEN 文生图工作流（2098715929763995649）：改节点 6 提示词 + 节点 5 seed，
     钉死 create/status/outputs 链路与产物下载
  2) 参考图上传 /openapi/v2/media/upload/binary 换取 fileName
  3) MiniMax H3 导演台工作流（2093160296864116737，节点 12）：单段 timeline 驱动
     （一次任务一个 Segment，refs[0]=上传图，六段式提示词），下载 mp4
  4) ffmpeg 抽尾帧并校验尺寸/音轨

运行：python spike/runninghub_spike.py [--skip-image]
  --skip-image 复用 spike/out/ref_image.png（换提示词重跑视频时不重复扣生图费）
产物：spike/out/（不清空，逐文件覆盖）+ spike/out/report.json
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # 独立脚本直跑时仍可导入 server 包

from server.infra.config import get_settings  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
VIDEO_WORKFLOW_JSON = ROOT / "workerflow" / (
    "MiniMaxH3Director_ref2va_2093160296864116737_api.json"
)
VIDEO_NODE_ID = "12"  # 该版本的 MiniMaxH3Director 节点
VIDEO_SAVE_NODE = "7"  # SaveVideo（另一个 20 是预览通道）

IMAGE_PROMPT = (
    "Cinematic night shot of an old Chinese ferryman standing on a wooden pier by a "
    "dark river, cold gray-green palette, film grain, 35mm, realistic style, medium "
    "shot, moody atmospheric lighting"
)
GLOBAL_PROMPT = (
    "subject_definitions:\n"
    "<Subject 1> is the old ferryman in <Picture 1>, with a weathered face, gray "
    "stubble and a dark oilskin coat; identity and clothing fully locked to the "
    "reference image."
)
VIDEO_PROMPT = (
    "subject_definitions: <Subject 1> is the old ferryman in <Picture 1>, with a "
    "weathered face, gray stubble and a dark oilskin coat; his identity stays fully "
    "locked to the reference. <Picture 1> is also the opening composition of this "
    "shot.\n"
    "summary: [reference generation] The ferryman pushes his boat off the night pier "
    "and speaks to the river.\n"
    "retention_analysis: <Picture 1> (appears in [Shot 1], [Shot 2]): fully_preserved "
    "- pier framing, the ferryman's face shape, stubble and oilskin coat stay "
    "identical across both shots.\n"
    "detailed_description: [Shot 1] Cold gray-green night, live-action, film grain. "
    "The scene opens exactly as <Picture 1>: the old ferryman stands at the wooden "
    "pier, dark river behind him. He unties the mooring rope and pushes the boat off "
    "the pier while the camera does a gentle push in. [Shot 2] At 00:05.000, on the "
    "drifting boat, <Subject 1> (S1) faces the river; his lips move as he says "
    "<d>[English] the river remembers</d>.\n"
    "overall_soundscape: Water lapping against wooden planks, rope creaking, distant "
    "night crickets.\n"
    "non_diegetic_music: Sparse low strings, slow tempo."
)

IMAGE_POLL_TIMEOUT = 600
VIDEO_POLL_TIMEOUT = 2400
POLL_INTERVAL = 10


class RunningHubClient:
    def __init__(self, base: str, api_key: str) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.http = httpx.Client(timeout=120)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        host = self.base.split("//", 1)[1]
        headers = {"Host": host, "Authorization": f"Bearer {self.api_key}"}
        resp = self.http.post(
            self.base + path, json={"apiKey": self.api_key, **payload}, headers=headers
        )
        body = resp.json()
        return {"http_status": resp.status_code, **body}

    def create_task(self, workflow_id: str, node_info_list: list[dict]) -> dict[str, Any]:
        return self._post(
            "/task/openapi/create",
            {"workflowId": workflow_id, "nodeInfoList": node_info_list},
        )

    def status(self, task_id: str) -> dict[str, Any]:
        return self._post("/task/openapi/status", {"taskId": task_id})

    def outputs(self, task_id: str) -> dict[str, Any]:
        return self._post("/task/openapi/outputs", {"taskId": task_id})

    def upload(self, file_path: Path) -> dict[str, Any]:
        # 实测：上传接口只认 Authorization: Bearer 头；form/query 传 apiKey 均报
        # "apiKey is required"（官方文档示例中的 form apiKey 字段是误导）
        host = self.base.split("//", 1)[1]
        headers = {"Host": host, "Authorization": f"Bearer {self.api_key}"}
        with file_path.open("rb") as fh:
            resp = self.http.post(
                self.base + "/openapi/v2/media/upload/binary",
                files={"file": (file_path.name, fh)},
                headers=headers,
            )
        return resp.json()

    def wait_for_success(self, task_id: str, timeout: int, label: str) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.status(task_id)
            state = st.get("data")
            print(f"  [{label}] status: {state}")
            if state == "SUCCESS":
                return self.outputs(task_id)
            if state == "FAILED":
                outs = self.outputs(task_id)
                return {"failed": True, "status_raw": st, "outputs_raw": outs}
            time.sleep(POLL_INTERVAL)
        return {"failed": True, "error": f"{label} poll timeout ({timeout}s)", "status_raw": st}


def record(step: str, data: dict[str, Any]) -> None:
    path = OUT / f"{step}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{step}] -> {path.name}")


def download(client: RunningHubClient, url: str, dest: Path) -> int:
    resp = client.http.get(url, timeout=600)
    dest.write_bytes(resp.content)
    return len(resp.content)


def pick_file(outputs_data: list, *suffixes: str, prefer_node: str | None = None) -> dict | None:
    candidates = [
        i
        for i in outputs_data or []
        if str(i.get("fileUrl", "")).lower().endswith(suffixes)
    ]
    if prefer_node:
        for item in candidates:
            if str(item.get("nodeId")) == prefer_node:
                return item
    return candidates[0] if candidates else None


def step1_text_to_image(client: RunningHubClient, workflow_id: str) -> dict[str, Any]:
    node_info_list = [
        {"nodeId": "6", "fieldName": "text", "fieldValue": IMAGE_PROMPT},
        {"nodeId": "5", "fieldName": "seed", "fieldValue": random.randint(1, 2**31)},
    ]
    created = client.create_task(workflow_id, node_info_list)
    record("1_t2i_create", {"request": node_info_list, "response": created})
    if created.get("code") != 0:
        return {"ok": False, "error": f"create failed: {created.get('msg')}"}
    task_id = created["data"]["taskId"]
    final = client.wait_for_success(task_id, IMAGE_POLL_TIMEOUT, "t2i")
    record("1_t2i_final", final)
    if final.get("failed"):
        return {"ok": False, "error": "task failed", "detail": final}
    file = pick_file(final.get("data", []), ".png", ".jpg", ".jpeg", ".webp")
    if not file:
        return {"ok": False, "error": "no image in outputs", "detail": final}
    size = download(client, file["fileUrl"], OUT / "ref_image.png")
    return {
        "ok": True,
        "task_id": task_id,
        "bytes": size,
        "cost": {k: file.get(k) for k in ("consumeCoins", "taskCostTime")},
    }


def upload_reference(client: RunningHubClient) -> dict[str, Any]:
    result = client.upload(OUT / "ref_image.png")
    record("2_upload", result)
    file_name = (result.get("data") or {}).get("fileName")
    if not file_name:
        msg = result.get("msg") or result.get("message")
        return {"ok": False, "error": f"upload failed: {msg}"}
    return {"ok": True, "file_name": file_name}


def build_timeline_data(ref_file_name: str, total_frames: int, duration_sec: int) -> str:
    """以远端工作流 timeline 为模板做最小修改：单段、换提示词与参考图。"""
    template = json.loads(VIDEO_WORKFLOW_JSON.read_text(encoding="utf-8"))
    timeline = json.loads(template[VIDEO_NODE_ID]["inputs"]["timeline_data"])
    seg = timeline["segments"][0]
    seg["prompt"] = VIDEO_PROMPT
    seg["continuityFromPrev"] = False
    seg["refs"] = []
    timeline["segments"] = [seg]
    timeline["totalFrames"] = total_frames
    timeline["durationSec"] = duration_sec
    timeline["video"]["sourceFrameCount"] = total_frames
    timeline["global"]["prompt"] = GLOBAL_PROMPT
    timeline["global"]["refs"] = [
        {
            "index": 0,
            "imageFile": ref_file_name,
            "fileName": "",
            "type": "input",
            "subfolder": "",
        }
    ]
    timeline["output"]["audioMode"] = "generate"  # 无源视频，成片音轨由 H3 生成
    if "r2v" in timeline.get("batchWorkspaces", {}):
        r2v = timeline["batchWorkspaces"]["r2v"]
        r2v["segments"] = [dict(seg)]
        r2v["runSelectEnabled"] = True
        r2v["runSelection"] = [0]
        r2v["selectedIndex"] = 0
        r2v["globalCommon"] = {
            "commonEnabled": True,
            "commonCollapsed": True,
            "prompt": GLOBAL_PROMPT,
            "refs": [dict(r) for r in timeline["global"]["refs"]],
            "refAudios": [],
            "refVideos": [],
        }
    timeline["runSelectEnabled"] = True
    timeline["runSelection"] = [0]
    return json.dumps(timeline, ensure_ascii=False)


def step3_video(client: RunningHubClient, workflow_id: str, ref_file_name: str) -> dict[str, Any]:
    # 沿用模板首段帧数口径（243 帧 ≈ 10s @24fps），风险最小
    duration_sec, total_frames = 10, 243
    timeline_data = build_timeline_data(ref_file_name, total_frames, duration_sec)
    node_info_list = [
        {"nodeId": VIDEO_NODE_ID, "fieldName": "timeline_data", "fieldValue": timeline_data},
        {"nodeId": VIDEO_NODE_ID, "fieldName": "total_frames", "fieldValue": total_frames},
        {"nodeId": VIDEO_NODE_ID, "fieldName": "global_prompt", "fieldValue": GLOBAL_PROMPT},
        {
            "nodeId": VIDEO_NODE_ID,
            "fieldName": "seed",
            "fieldValue": random.randint(1, 2**31),
        },
    ]
    payload_no_timeline = [
        {
            **n,
            "fieldValue": (
                str(n["fieldValue"])[:300] + "…<truncated>"
                if len(str(n["fieldValue"])) > 300
                else n["fieldValue"]
            ),
        }
        for n in node_info_list
    ]
    created = client.create_task(workflow_id, node_info_list)
    record("3_video_create", {"request_no_timeline": payload_no_timeline, "response": created})
    if created.get("code") != 0:
        return {
            "ok": False,
            "error": f"create failed: {created.get('msg')}",
            "promptTips": created.get("promptTips"),
        }
    task_id = created["data"]["taskId"]
    final = client.wait_for_success(task_id, VIDEO_POLL_TIMEOUT, "video")
    record("3_video_final", final)
    if final.get("failed"):
        return {"ok": False, "error": "task failed", "detail": final}
    file = pick_file(
        final.get("data", []), ".mp4", ".webm", ".mov", prefer_node=VIDEO_SAVE_NODE
    )
    if not file:
        return {"ok": False, "error": "no video in outputs", "detail": final}
    size = download(client, file["fileUrl"], OUT / "clip_r2va.mp4")
    return {
        "ok": True,
        "task_id": task_id,
        "bytes": size,
        "cost": {k: file.get(k) for k in ("consumeCoins", "taskCostTime")},
    }


def step4_tail_frame() -> dict[str, Any]:
    clip = OUT / "clip_r2va.mp4"
    tail = OUT / "tail_frame.jpg"
    result = subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(clip), "-frames:v", "1", str(tail)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0 or not tail.exists():
        return {"ok": False, "error": result.stderr[-500:]}
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(clip)],
        capture_output=True, text=True, timeout=60,
    )
    streams = json.loads(probe.stdout).get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    dims_ok = video.get("width", 0) >= 256 and video.get("height", 0) >= 256
    return {
        "ok": dims_ok and tail.exists(),
        "video": {k: video.get(k) for k in ("width", "height", "duration")},
        "has_audio": bool(audio),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-image", action="store_true", help="复用已有 ref_image.png")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    if not settings.runninghub_api_key:
        print("RUNNINGHUB_API_KEY 未配置：请在 .env 中填写后重跑本脚本。")
        return 2
    print(
        f"using RUNNINGHUB_API_KEY ***{settings.runninghub_api_key[-4:]} "
        f"@ {settings.runninghub_base_url}"
    )
    client = RunningHubClient(settings.runninghub_base_url, settings.runninghub_api_key)

    report: dict[str, Any] = {}
    if args.skip_image and (OUT / "ref_image.png").exists():
        report["1_t2i"] = {"ok": True, "skipped": True}
    else:
        report["1_t2i"] = step1_text_to_image(client, settings.runninghub_workflow_image)
    if report["1_t2i"].get("ok"):
        report["2_upload"] = upload_reference(client)
        if report["2_upload"].get("ok"):
            report["3_video"] = step3_video(
                client, settings.runninghub_workflow_video, report["2_upload"]["file_name"]
            )
            if report["3_video"].get("ok"):
                report["4_tail_frame"] = step4_tail_frame()
        else:
            report["3_video"] = {"ok": False, "error": "参考图上传失败，视频步骤跳过"}
    else:
        report["1_t2i_skip_rest"] = "生图失败，后续步骤跳过"

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok = all(v.get("ok") for v in report.values() if isinstance(v, dict) and "ok" in v)
    print(f"\nSPIKE {'PASSED' if ok else 'FAILED'} — 详情见 spike/out/report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
