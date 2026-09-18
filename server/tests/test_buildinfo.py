"""代码新鲜度护栏单测（TASK-044）。

背景（实测教训）：改完资产卡规则后没重启进程，旧 Worker 仍按旧口径撰写提示词，
照常生成 6 张卡、白花约 138 币，而且页面完全看不出异常——因为提示词是代码现场写的。
本模块把"进程加载旧代码"变成确定性判定，并在**付费动作之前**硬拦。
"""

from __future__ import annotations

import time

from server.infra.buildinfo import freshness


def test_freshness_clean_for_long_running_process_started_after_edits() -> None:
    """进程启动时刻晚于源码最后修改 → 新鲜（正常重启后的状态）。"""
    f = freshness(now_ts=time.time() + 3600)
    assert f.stale is False
    assert f.version


def test_freshness_flags_process_started_before_edits() -> None:
    """进程启动时刻早于源码最后修改 → 旧代码（就是白花钱的那次）。"""
    f = freshness(now_ts=1.0)  # 1970 年启动，任何源码都比它新
    assert f.stale is True
    assert f.newest_source_path.endswith(".py")
    assert "重启" in f.describe()


def test_allow_stale_code_env_is_escape_hatch(monkeypatch) -> None:
    """确知改动与本动作无关时，可用 ALLOW_STALE_CODE=1 放行（默认不放行）。"""
    monkeypatch.setenv("ALLOW_STALE_CODE", "1")
    assert freshness(now_ts=1.0).stale is False
