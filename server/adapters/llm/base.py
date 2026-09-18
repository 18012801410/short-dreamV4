"""LlmAdapter 错误类型（TASK-005）：结构化 code 供 Worker 映射到 JobError。"""

from __future__ import annotations

from typing import Any

# 错误码：AUTH=key 无效/缺失；TRANSPORT=超时/网络/限流重试耗尽；
# BAD_RESPONSE=非 2xx 或响应结构不符；EMPTY_CONTENT=补全为空；
# SCHEMA_VALIDATION=JSON mode 输出两次均未通过 Pydantic 校验。


class LlmError(Exception):
    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"
