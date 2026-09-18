"""LlmProvider 适配器（TASK-005）：OpenAI 兼容实现 + 工厂。"""

from __future__ import annotations

from server.adapters.llm.base import LlmError
from server.adapters.llm.openai_compatible import OpenAICompatibleLlm
from server.infra.config import Settings


def build_llm_from_settings(settings: Settings) -> OpenAICompatibleLlm:
    if not settings.llm_api_key:
        raise LlmError("AUTH", "LLM_API_KEY 未配置（.env 或环境变量）")
    return OpenAICompatibleLlm(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        timeout=float(settings.llm_timeout_sec),
    )


__all__ = ["LlmError", "OpenAICompatibleLlm", "build_llm_from_settings"]
