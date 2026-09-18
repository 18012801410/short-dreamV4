"""OpenAI 兼容 ChatCompletions 适配器（TASK-005）：GLM/DeepSeek/MiniMax 等通用。

护栏契约（AI_SPEC「结构化输出」）：
- 所有 LLM 输出走 JSON mode + Pydantic 校验；校验失败带错误重试 1 次，再失败任务失败
- usage 透出给调用方记录 ProviderCall（FR-015）
密钥纪律：api_key 只进 Authorization 头，任何异常消息不得包含它。
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from server.adapters.llm.base import LlmError
from server.domain.providers import LlmResult, LlmUsage

_SCHEMA_RETRY_USER_MSG = (
    "你上一次的输出未通过 JSON Schema 校验。错误摘要：\n{errors}\n"
    "请重新输出，只输出一个符合要求的 JSON 对象，不要包含任何解释文字或代码围栏。"
)
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\s*|\s*```$", re.MULTILINE)


def _strip_json_fences(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped)
    if stripped.endswith("```"):
        # 首字符是换行/正文时上面的正则不会剥开头围栏，这里兜底剥尾部围栏
        stripped = re.sub(r"```\s*$", "", stripped)
    return stripped.strip()


class OpenAICompatibleLlm:
    """同步实现（Worker handler 为同步协议；单进程逐 Job 串行，无并发需求）。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,  # 测试注入，避免真实退避等待
        max_transport_retries: int = 2,
        max_tokens: int = 0,  # 0=不传（供应商默认）；长 JSON 结构化输出需显式放大
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )
        self._api_key = api_key
        self._model = model
        self._sleep = sleep
        self._max_transport_retries = max_transport_retries
        self._max_tokens = max_tokens

    # -- 公开接口 -----------------------------------------------------------

    def chat(self, *, system: str, user: str, temperature: float = 0.7) -> str:
        content, _ = self._complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            json_mode=False,
        )
        return content

    def chat_json[ModelT: BaseModel](
        self,
        *,
        system: str,
        user: str,
        schema: type[ModelT],
        temperature: float = 0.7,
    ) -> LlmResult[ModelT]:
        base_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        messages = base_messages
        raw_first = ""
        usage = LlmUsage()
        for attempt in (1, 2):
            content, usage = self._complete(
                messages, temperature=temperature, json_mode=True
            )
            cleaned = _strip_json_fences(content)
            try:
                data = schema.model_validate_json(cleaned)
            except ValidationError as exc:
                if attempt == 1:
                    raw_first = content
                    summary = "; ".join(
                        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                        for e in exc.errors()[:8]
                    )
                    messages = [
                        *base_messages,
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": _SCHEMA_RETRY_USER_MSG.format(errors=summary),
                        },
                    ]
                    if attempt == 1:
                        raw_first = content
                    import sys

                    print(
                        "schema 校验失败：\n错误: "
                        + "; ".join(
                            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                            for e in exc.errors()[:8]
                        )
                        + f"\n输出长度: {len(content)} 字符，头部: {content[:300]!r}"
                        + f"\n尾部: {content[-300:]!r}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                raise LlmError(
                    "SCHEMA_VALIDATION",
                    "LLM 输出两次未通过 schema 校验",
                    details={
                        "errors": [
                            {
                                "loc": [str(p) for p in e["loc"]],
                                "msg": e["msg"],
                            }
                            for e in exc.errors()[:8]
                        ],
                        "first_output": raw_first[:2000],
                        "second_output": content[:2000],
                    },
                ) from exc
            return LlmResult(
                data=data, usage=usage, attempts=attempt, raw_content=content
            )
        raise LlmError("SCHEMA_VALIDATION", "unreachable")  # pragma: no cover

    # -- 内部 ---------------------------------------------------------------

    def _complete(
        self, messages: list[dict[str, str]], *, temperature: float, json_mode: bool
    ) -> tuple[str, LlmUsage]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode and "reasoner" not in self._model:
            # DeepSeek reasoner 不支持 response_format: json_object（会返回空内容），
            # 靠系统提示词约束 JSON + 围栏剥离兜底
            payload["response_format"] = {"type": "json_object"}
        if self._max_tokens > 0:
            payload["max_tokens"] = self._max_tokens
        headers = {"Authorization": f"Bearer {self._api_key}"}

        last_error: LlmError | None = None
        for attempt in range(self._max_transport_retries + 1):
            try:
                resp = self._client.post(
                    "/chat/completions", json=payload, headers=headers
                )
            except httpx.HTTPError as exc:
                last_error = LlmError("TRANSPORT", f"LLM 请求失败：{type(exc).__name__}")
            else:
                if resp.status_code in {401, 403}:
                    raise LlmError("AUTH", "LLM 鉴权失败（检查 LLM_API_KEY）")
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = LlmError(
                        "TRANSPORT",
                        f"LLM 服务暂时不可用（HTTP {resp.status_code}）",
                    )
                elif resp.status_code != 200:
                    import sys

                    print(
                        f"LLM HTTP {resp.status_code}: {resp.text[:300]}",
                        file=sys.stderr,
                        flush=True,
                    )
                    raise LlmError(
                        "BAD_RESPONSE",
                        f"LLM 返回 HTTP {resp.status_code}",
                        details={"body": resp.text[:500]},
                    )
                else:
                    return self._parse_success(resp.json())

            if attempt < self._max_transport_retries:
                self._sleep(2**attempt)  # 1s, 2s
        assert last_error is not None
        raise last_error

    def _parse_success(self, body: dict[str, Any]) -> tuple[str, LlmUsage]:
        choices = body.get("choices") or []
        if not choices:
            raise LlmError(
                "BAD_RESPONSE", "LLM 响应缺少 choices", details={"body": str(body)[:500]}
            )
        content = (choices[0].get("message") or {}).get("content") or ""
        if not content.strip():
            raise LlmError("EMPTY_CONTENT", "LLM 返回了空内容")
        usage_raw = body.get("usage") or {}
        usage = LlmUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            total_tokens=int(usage_raw.get("total_tokens") or 0),
            raw=usage_raw,
        )
        return content, usage
