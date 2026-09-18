"""进程代码新鲜度检测（TASK-044）——防止"旧代码静默烧钱"。

**要解决的问题（实测教训，2026-09-16）**：Python 进程在启动时把模块加载进内存，
之后改代码**不会**生效（uvicorn/worker 都没开 --reload）。用户在页面上点「抽取资产」
时，跑在旧 Worker 里的是**旧版资产卡规则**（当时是"半身定妆像 / 多视图空镜"），
于是：
1. 生成的提示词是旧口径 → 页面看不出任何异常；
2. 12 张图照常生成、照常扣币（约 276 币），等发现时钱已经花掉。

这类浪费的共性是「进程代码过期 + 付费动作照跑」。本模块提供确定性判定：
**源码文件修改时间晚于进程启动时间 = 进程跑的是旧代码**（导入本模块的时刻即进程
启动时刻的近似，误差在秒级，配 1 秒宽限）。

判定结果用于两处，都是"在花钱之前拦住"：
- `WorkbenchService.dispatch`：付费命令（抽取资产/生成图/生成关键帧/产视频）先查，
  过期直接抛 `STALE_CODE`，页面收到的是可读的中文提示，而不是账单；
- Worker 的 asset_extract handler：抽取阶段是在 Worker 里跑的（提示词的作者），
  Worker 代码过期同样拒绝，不产生任何 API 调用。

逃生口：确知改动与本动作无关时，设环境变量 `ALLOW_STALE_CODE=1` 可放行（会记日志）。

判定失败（拿不到 mtime 等）一律**按新鲜处理**——这是"防止浪费"的辅助网，
不该因为读不到文件而把正常工作全堵死。
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

# 进程启动时刻的近似值：本模块在 API/Worker 启动早期被导入
PROCESS_START_TS: float = time.time()

# 仓库根（server/infra/buildinfo.py → 上溯两层）
REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_DIRS = ("server", "scripts")

# 同一秒内改文件 + 启动进程的误报宽限
_GRACE_SEC = 1.0

_ALLOW_ENV = "ALLOW_STALE_CODE"


@dataclass(frozen=True)
class CodeFreshness:
    """当前进程的代码新鲜度快照。"""

    stale: bool
    process_start_ts: float
    newest_source_ts: float
    newest_source_path: str
    version: str

    def describe(self) -> str:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.process_start_ts))
        changed = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.newest_source_ts))
        if not self.stale:
            return f"代码已是最新（{self.version}，进程启动 {started}）"
        return (
            f"当前进程加载的是旧代码：源码在 {changed} 被修改"
            f"（{self.newest_source_path}），而进程启动于 {started}。"
            f"请重启 API 与 Worker 后再继续——否则付费动作会按旧规则生成，白花币。"
        )


def _iter_sources() -> list[Path]:
    files: list[Path] = []
    for rel in _SOURCE_DIRS:
        base = REPO_ROOT / rel
        if base.is_dir():
            files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _fingerprint(files: list[tuple[Path, float, int]]) -> str:
    h = hashlib.sha256()
    for path, mtime, size in sorted(files, key=lambda f: str(f[0])):
        h.update(f"{path.name}:{int(mtime)}:{size};".encode())
    return h.hexdigest()[:12]


def freshness(now_ts: float | None = None) -> CodeFreshness:
    """判定当前进程是否跑着旧代码（失败时按"新鲜"处理，不阻断工作）。"""
    started = PROCESS_START_TS if now_ts is None else now_ts
    entries: list[tuple[Path, float, int]] = []
    newest_ts = 0.0
    newest_path = ""
    try:
        for path in _iter_sources():
            stat = path.stat()
            entries.append((path, stat.st_mtime, stat.st_size))
            if stat.st_mtime > newest_ts:
                newest_ts, newest_path = stat.st_mtime, str(path.relative_to(REPO_ROOT))
    except OSError:
        return CodeFreshness(False, started, 0.0, "", "")
    if not entries:
        return CodeFreshness(False, started, 0.0, "", "")
    allow = os.environ.get(_ALLOW_ENV, "").strip() not in ("", "0", "false", "False")
    stale = newest_ts > started + _GRACE_SEC and not allow
    return CodeFreshness(
        stale=stale,
        process_start_ts=started,
        newest_source_ts=newest_ts,
        newest_source_path=newest_path,
        version=_fingerprint(entries),
    )


def is_stale() -> bool:
    return freshness().stale
