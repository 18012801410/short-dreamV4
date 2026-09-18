"""领域错误类型（TASK-003）：统一错误码，应用层/API 负责映射为 HTTP 响应。"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """领域层错误基类：code 供上层映射，details 携带结构化上下文。

    子类以类属性声明 code；直接实例化基类时允许 DomainError(code, message)
    双参形式（应用层自定义错误码，如 NOT_FOUND / VALIDATION_ERROR）。
    """

    code = "DOMAIN_ERROR"

    def __init__(
        self,
        code_or_message: str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if message is None:
            self.message = code_or_message
        else:
            self.message = message
            self.code = code_or_message
        super().__init__(self.message)
        self.details = details or {}

    def __str__(self) -> str:
        return self.message


class StateIllegalError(DomainError):
    """非法状态迁移（DOMAIN_MODEL：命令前置状态不满足时返回 STATE_ILLEGAL）。"""

    code = "STATE_ILLEGAL"


class ValidationFailedError(DomainError):
    """结构化校验失败（分镜护栏、H3 提示词结构、LLM schema 等）。"""

    code = "VALIDATION_FAILED"


class ReferenceMissingError(DomainError):
    """缺已批准参考图（红线：阻断提交，不静默降级）。"""

    code = "REF_MISSING"


class DependencyBlockedError(DomainError):
    """depends_on 未全部 succeeded，Job 不得进入 running。"""

    code = "DEPENDENCY_BLOCKED"


class JobNotRetryableError(DomainError):
    """重试不合法：Job 非 failed 态、succeeded 不可变或 attempts 已用尽。"""

    code = "JOB_NOT_RETRYABLE"


class ImmutableArtifactError(DomainError):
    """active/approved 工件不可变；修改必须新建 draft 版本。"""

    code = "ARTIFACT_IMMUTABLE"
