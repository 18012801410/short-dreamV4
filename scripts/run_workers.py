"""并发 Worker 启动器：一条命令起 N 个 Worker 进程（TASK-034）。

为什么可以并行：任务认领是条件 UPDATE 抢占（repositories.claim_next_runnable），
多进程只有一个能把 pending 置为 running；依赖顺序由 depends_on 保证。
瓶颈在云端：每张图/每段视频平台侧要 200s~数分钟，单进程串行时整批时间
= N × 单件时间；起 3 个进程后整批≈N/3 × 单件时间。

额度保护：RunningHub 账号并发额度占满时任务创建被拒（TASK_QUEUE_MAXED），
引擎按固定长退避重试且不消耗重试预算（engine._schedule_retry），因此进程数
略超额度只会表现为偶发重排，不会失败。

用法：
    python scripts/run_workers.py --count 3        # 起 3 个
    python scripts/run_workers.py --count 3 --stop  # 停掉本脚本起的
    python scripts/run_workers.py --status
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data" / "logs"
PID_FILE = LOG_DIR / "workers_pool.json"


def _python() -> str:
    return sys.executable


def _workers_running() -> list[int]:
    """当前在跑的 Worker 进程（按命令行匹配，跨 Windows/POSIX）。"""
    if os.name == "nt":
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -match 'server.worker' } | "
            "Select-Object -ExpandProperty ProcessId",
        ]
    else:
        cmd = ["bash", "-lc", "pgrep -f 'server.worker' || true"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    return [int(line) for line in out.split() if line.strip().isdigit()]


def _start_one(index: int) -> int:
    log = LOG_DIR / f"worker_pool{index}.log"
    err = LOG_DIR / f"worker_pool{index}.err.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out, err.open("ab") as errf:
        proc = subprocess.Popen(
            # --no-recover：池成员绝不做启动恢复——恢复会把"所有遗留 running
            # 任务"判为中断，多进程各自启动时会互相打断在途任务（实测丢过图）
            [_python(), "-m", "server.worker", "--no-recover"],
            cwd=str(ROOT),
            stdout=out,
            stderr=errf,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    return proc.pid


def _recover_once() -> str:
    """池启动前的单次遗留任务恢复（必须在没有 Worker 在跑时调用）。"""
    proc = subprocess.run(
        [_python(), "-m", "server.worker", "--recover-only"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    return (proc.stdout or "").strip() or (proc.stderr or "").strip()[-300:]


def _stop_pool() -> int:
    killed = 0
    for pid in _workers_running():
        try:
            if os.name == "nt":
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
                    capture_output=True, timeout=30,
                )
            else:
                os.kill(pid, 15)
            killed += 1
        except Exception:
            pass
    return killed


def main() -> int:
    parser = argparse.ArgumentParser(description="并发 Worker 启动器")
    parser.add_argument("--count", type=int, default=3, help="目标 Worker 进程数")
    parser.add_argument("--stop", action="store_true", help="停掉所有 Worker")
    parser.add_argument("--status", action="store_true", help="只报告现状")
    args = parser.parse_args()

    running = _workers_running()
    if args.status:
        print(f"Worker 进程：{len(running)} 个 {running}")
        return 0
    if args.stop:
        print(f"已停止 {_stop_pool()} 个 Worker")
        return 0

    target = max(1, args.count)
    if running:
        # 已有 Worker 在跑：只补足差额，绝不跑恢复（会把在途任务判成中断）
        print(f"已有 {len(running)} 个 Worker 在跑 {running}，只补足到 {target} 个")
    else:
        print("池启动前恢复遗留任务：", _recover_once())
    started: list[int] = []
    for i in range(target - len(running)):
        started.append(_start_one(len(running) + i + 1))
        time.sleep(1.5)  # 错开启动，避免同时抢占同一任务
    time.sleep(3)
    now = _workers_running()
    PID_FILE.write_text(json.dumps({"workers": now}, ensure_ascii=False), encoding="utf-8")
    print(f"目标 {target} 个，现有 {len(now)} 个：{now}（本次新起 {len(started)} 个）")
    print(f"日志：{LOG_DIR}/worker_pool*.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
