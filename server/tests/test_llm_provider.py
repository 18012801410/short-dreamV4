"""LlmProvider 适配器单测（TASK-005）：MockTransport，不依赖真实 key。"""

import json

import httpx
import pytest
from pydantic import BaseModel

from server.adapters.llm import LlmError, OpenAICompatibleLlm, build_llm_from_settings
from server.domain.providers import LlmProvider
from server.infra.config import Settings


class OutSchema(BaseModel):
    title: str
    scenes: list[str]


def make_llm(responses: list[httpx.Response], requests: list[dict]) -> OpenAICompatibleLlm:
    """按序弹响应的 MockTransport；请求体记录进 requests。"""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return responses.pop(0)

    return OpenAICompatibleLlm(
        base_url="https://llm.test/api/v4",
        api_key="secret-key",
        model="glm-4.6",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def test_chat_returns_content_and_usage() -> None:
    requests: list[dict] = []
    llm = make_llm([completion("你好")], requests)

    text = llm.chat(system="sys", user="hi", temperature=0.8)

    assert text == "你好"
    assert requests[0]["model"] == "glm-4.6"
    assert requests[0]["temperature"] == 0.8
    assert "response_format" not in requests[0]


def test_chat_json_validates_directly() -> None:
    llm = make_llm([completion('{"title": "渡口", "scenes": ["S1"]}')], [])

    result = llm.chat_json(system="s", user="u", schema=OutSchema, temperature=0.4)

    assert result.data.title == "渡口"
    assert result.data.scenes == ["S1"]
    assert result.attempts == 1
    assert result.usage.total_tokens == 15


def test_chat_json_strips_code_fences() -> None:
    fenced = '```json\n{"title": "渡口", "scenes": ["S1"]}\n```'
    llm = make_llm([completion(fenced)], [])

    result = llm.chat_json(system="s", user="u", schema=OutSchema)

    assert result.data.scenes == ["S1"]


def test_chat_json_retries_with_error_feedback() -> None:
    requests: list[dict] = []
    bad = completion('{"title": "渡口"}')  # 缺 scenes
    good = completion('{"title": "渡口", "scenes": ["S1", "S2"]}')
    llm = make_llm([bad, good], requests)

    result = llm.chat_json(system="s", user="u", schema=OutSchema)

    assert result.attempts == 2
    assert len(requests) == 2
    retry_messages = requests[1]["messages"]
    assert retry_messages[-2]["role"] == "assistant"  # 上次原始输出
    assert "scenes" in retry_messages[-1]["content"]  # 纠错摘要指出缺失字段


def test_chat_json_both_invalid_raises_schema_validation() -> None:
    llm = make_llm([completion("not json"), completion('{"wrong": 1}')], [])

    with pytest.raises(LlmError) as excinfo:
        llm.chat_json(system="s", user="u", schema=OutSchema)
    assert excinfo.value.code == "SCHEMA_VALIDATION"
    assert "first_output" in excinfo.value.details
    assert "second_output" in excinfo.value.details


def test_auth_failure_raises_immediately() -> None:
    llm = make_llm([httpx.Response(401, json={"error": "bad key"})], [])

    with pytest.raises(LlmError) as excinfo:
        llm.chat(system="s", user="u")
    assert excinfo.value.code == "AUTH"


def test_rate_limit_retries_then_succeeds() -> None:
    sleeps: list[float] = []
    llm = make_llm(
        [httpx.Response(429, json={}), completion("ok")], []
    )
    llm._sleep = sleeps.append

    assert llm.chat(system="s", user="u") == "ok"
    assert sleeps == [1]


def test_server_error_retries_twice_then_raises_transport() -> None:
    sleeps: list[float] = []
    llm = make_llm(
        [
            httpx.Response(500, json={}),
            httpx.Response(503, json={}),
            httpx.Response(500, json={}),
        ],
        [],
    )
    llm._sleep = sleeps.append

    with pytest.raises(LlmError) as excinfo:
        llm.chat(system="s", user="u")
    assert excinfo.value.code == "TRANSPORT"
    assert sleeps == [1, 2]


def test_empty_content_raises() -> None:
    llm = make_llm([completion("")], [])

    with pytest.raises(LlmError) as excinfo:
        llm.chat(system="s", user="u")
    assert excinfo.value.code == "EMPTY_CONTENT"


def test_api_key_only_in_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.content.decode()
        return completion("x")

    llm = OpenAICompatibleLlm(
        base_url="https://llm.test",
        api_key="secret-key",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    llm.chat(system="s", user="u")
    assert captured["auth"] == "Bearer secret-key"
    assert "secret-key" not in captured["body"]


def test_factory_requires_api_key() -> None:
    settings = Settings(llm_api_key="", _env_file=None)
    with pytest.raises(LlmError) as excinfo:
        build_llm_from_settings(settings)
    assert excinfo.value.code == "AUTH"


def test_provider_protocol_satisfied() -> None:
    llm = make_llm([], [])
    assert isinstance(llm, LlmProvider)
