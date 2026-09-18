"""LLM 懒加载代理：服务/Worker 启动不要求 key 已配置，首次调用才构建。"""

from __future__ import annotations

from typing import Any

from server.infra.config import Settings


class LazyLlm:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._inner: Any = None

    def _get(self) -> Any:
        if self._inner is None:
            from server.adapters.llm import build_llm_from_settings

            self._inner = build_llm_from_settings(self._settings)
        return self._inner

    def chat(self, **kwargs: Any) -> str:
        return self._get().chat(**kwargs)

    def chat_json(self, **kwargs: Any) -> Any:
        return self._get().chat_json(**kwargs)
