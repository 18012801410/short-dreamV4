"""TASK-001 Spike：MiniMax 契约实测（用后即弃）。

用途：
  1) image-01 文生图：钉死 endpoint / 参数 / 响应结构 / URL 下载
  2) H3 多参考生视频（r2va）：Base64 参考图 + 轮询 + 下载 mp4
  3) 互斥规则实测：reference_image 与 first_frame 混用应报错
  4) ffmpeg 抽尾帧并校验尺寸

运行：python spike/minimax_spike.py   （需 .env 中配置 MINIMAX_API_KEY）
产物：spike/out/（每次运行先清空）+ spike/out/report.json
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "out"
POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = 600


def load_env() -> dict[str, str]:
    env_path = ROOT / ".env"
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    values.update({k: v for k, v in os.environ.items() if k.startswith("MINIMAX")})
    return values


def mask(key: str) -> str:
    return f"***{key[-4:]}" if key else "<missing>"


def record(step: str, data: dict[str, Any]) -> None:
    path = OUT / f"{step}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{step}] -> {path.name}")


def step1_text_to_image(client: httpx.Client, headers: dict) -> dict:
    """候选路径探测：官方文档口径 /v1/image_generation。"""
    payload = {
        "model": "image-01",
        "prompt": (
            "Cinematic night shot of an old ferryman standing on a wooden pier, "
            "cold gray-green palette, film grain, 35mm, realistic style"
        ),
        "aspect_ratio": "16:9",
        "response_format": "url",
    }
    attempts = []
    for path in ["/v1/image_generation", "/v1/image/generation"]:
        resp = client.post(BASE + path, json=payload, headers=headers, timeout=120)
        attempts.append({"path": path, "status": resp.status_code, "body": resp.json()})
        if resp.status_code == 200:
            record("1_t2i", {"endpoint": path, "request": payload, "response": resp.json()})
            image_urls = resp.json().get("data", {}).get("image_urls") or []
            if not image_urls:
                return {"ok": False, "error": "no image_urls in response"}
            img_bytes = client.get(image_urls[0], timeout=60).content
            (OUT / "ref_image.png").write_bytes(img_bytes)
            return {"ok": True, "endpoint": path, "bytes": len(img_bytes)}
    record("1_t2i", {"attempts": attempts})
    return {"ok": False, "error": "all candidate endpoints failed", "attempts": attempts}


def to_data_uri(png_path: Path) -> str:
    encoded = base64.b64encode(png_path.read_bytes()).decode()
    return f"data:image/png;base64,{encoded}"


def create_video_task(client: httpx.Client, headers: dict, payload: dict) -> dict:
    resp = client.post(BASE + "/v2/video_generation", json=payload, headers=headers, timeout=120)
    return {"status": resp.status_code, "body": resp.json()}


def poll_video_task(client: httpx.Client, headers: dict, task_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT_SEC
    last: dict = {}
    while time.time() < deadline:
        resp = client.get(
            BASE + f"/v2/query/video_generation/{task_id}", headers=headers, timeout=60
        )
        last = {"status": resp.status_code, "body": resp.json()}
        task = resp.json().get("task", {})
        state = task.get("status")
        print(f"  poll: {state}")
        if state in {"succeeded", "failed", "cancelled"}:
            return last
        time.sleep(POLL_INTERVAL_SEC)
    return {"status": "timeout", "body": last}


def step2_r2va_video(client: httpx.Client, headers: dict) -> dict:
    data_uri = to_data_uri(OUT / "ref_image.png")
    payload = {
        "model": "MiniMax-H3",
        "content": [
            {"type": "text", "text": (
                "For the target video, at 0.00 seconds into the target video, "
                "<Picture 1> (from [Shot 1]) is fully referenced. "
                "[Shot 1] Cinematic, live-action, cold gray-green palette. The old ferryman "
                "slowly pushes off from the pier, the camera does a gentle push in as the boat "
                "drifts away. He says in an off-screen voiceover \"the river remembers\", "
                "while his lips remain completely closed."
            )},
            {"type": "image_url", "image_url": {"url": data_uri}, "role": "reference_image"},
        ],
        "resolution": "768P",
        "duration": 5,
    }
    created = create_video_task(client, headers, payload)
    record("2_r2va_create", {"request_no_b64": {**payload, "content": [
        {"type": "text", "text": payload["content"][0]["text"]},
        {"type": "image_url", "image_url": {"url": "<base64 data uri>"}},
    ]}, "response": created})
    if created["status"] != 200:
        return {"ok": False, "error": "create failed", "detail": created}
    task_id = created["body"].get("task_id") or created["body"].get("data", {}).get("task_id")
    final = poll_video_task(client, headers, task_id)
    record("2_r2va_final", final)
    task = final.get("body", {}).get("task", {})
    if task.get("status") != "succeeded":
        return {"ok": False, "error": "task not succeeded", "detail": final}
    video_url = task["content"]["url"]
    mp4 = client.get(video_url, timeout=300).content
    (OUT / "clip_r2va.mp4").write_bytes(mp4)
    record("2_r2va_usage", {"usage": task.get("usage"), "bytes": len(mp4)})
    return {"ok": True, "task_id": task_id, "bytes": len(mp4)}


def step3_exclusion_test(client: httpx.Client, headers: dict) -> dict:
    """互斥实测：first_frame 与 reference_image 同请求，官方规定必须报错。"""
    data_uri = to_data_uri(OUT / "ref_image.png")
    payload = {
        "model": "MiniMax-H3",
        "content": [
            {"type": "text", "text": "A ferryman pushes off from the pier at night."},
            {"type": "image_url", "image_url": {"url": data_uri}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": data_uri}, "role": "reference_image"},
        ],
        "resolution": "768P",
        "duration": 5,
    }
    created = create_video_task(client, headers, payload)
    record("3_exclusion", created)
    excluded = created["status"] != 200
    return {
        "ok": excluded,
        "note": "ok=true 表示混用被拒绝（互斥规则成立）",
        "detail_status": created["status"],
    }


def step4_tail_frame() -> dict:
    clip = OUT / "clip_r2va.mp4"
    tail = OUT / "tail_frame.jpg"
    result = subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(clip), "-frames:v", "1", str(tail)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0 or not tail.exists():
        return {"ok": False, "error": result.stderr[-500:]}
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(tail)],
        capture_output=True, text=True, timeout=30,
    )
    streams = json.loads(probe.stdout).get("streams", [])
    width = streams[0]["width"] if streams else 0
    height = streams[0]["height"] if streams else 0
    return {"ok": width >= 256 and height >= 256, "width": width, "height": height}


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    env = load_env()
    api_key = env.get("MINIMAX_API_KEY", "")
    global BASE
    BASE = env.get("MINIMAX_BASE_URL", BASE).rstrip("/")
    if not api_key:
        print("MINIMAX_API_KEY 未配置：请在 .env 中填写后重跑本脚本。")
        return 2
    print(f"using MINIMAX_API_KEY {mask(api_key)} @ {BASE}")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    report: dict[str, Any] = {}
    with httpx.Client() as client:
        report["1_t2i"] = step1_text_to_image(client, headers)
        if report["1_t2i"].get("ok"):
            report["2_r2va"] = step2_r2va_video(client, headers)
            report["3_exclusion"] = step3_exclusion_test(client, headers)
            if report["2_r2va"].get("ok"):
                report["4_tail_frame"] = step4_tail_frame()
        else:
            report["1_t2i_skip_rest"] = "生图失败，后续步骤跳过"

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok = all(v.get("ok") for v in report.values() if isinstance(v, dict) and "ok" in v)
    print(f"\nSPIKE {'PASSED' if ok else 'FAILED'} — 详情见 spike/out/report.json")
    return 0 if ok else 1


BASE = "https://api.minimax.cn"

if __name__ == "__main__":
    sys.exit(main())
