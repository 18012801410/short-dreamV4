"""Provider 接口（TASK-005）：domain 拥有，adapters 实现（ARCHITECTURE §2）。

本模块只定义协议与结果载体，零框架依赖（Pydantic 泛型除外）；
实现见 server/adapters/llm/。精确签名由各适配器任务钉死。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass
class LlmUsage:
    """一次 LLM 调用的 tokens 用量（供 ProviderCall 记账，FR-015）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LlmResult[ModelT: BaseModel]:
    """chat_json 的返回：校验后的结构化数据 + 用量 + 实际尝试次数。"""

    data: ModelT
    usage: LlmUsage
    attempts: int = 1
    raw_content: str = ""


@runtime_checkable
class LlmProvider(Protocol):
    """OpenAI 兼容 ChatCompletions 的最小面。

    - chat：纯文本补全（temperature 由调用方按 Agent 设定）
    - chat_json：JSON mode + Pydantic 校验 + 带错误重试 1 次（AI_SPEC 护栏）
    """

    def chat(self, *, system: str, user: str, temperature: float = 0.7) -> str: ...

    def chat_json[ModelT: BaseModel](
        self,
        *,
        system: str,
        user: str,
        schema: type[ModelT],
        temperature: float = 0.7,
    ) -> LlmResult[ModelT]: ...


@dataclass
class MediaUsage:
    """一次媒体生成的用量（RunningHub：RH 币与任务秒数；供 ProviderCall 记账）。"""

    coins: str = "0"
    task_seconds: str = "0"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedImage:
    """一次生图结果：产物已由适配器下载落地到 dest（红线：URL 临时必须立即落地）。"""

    provider_task_id: str
    seed: int
    file_url: str
    dest: Path
    size_bytes: int
    usage: MediaUsage


@runtime_checkable
class ImageProvider(Protocol):
    """资产生图的最小面：宽高由调用方按项目画幅给定。

    seed 缺省随机，传入可复现；negative 仅在稳定版工作流（配置了负向节点）时生效。
    """

    def generate(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        dest: Path,
        seed: int | None = None,
        negative: str | None = None,
    ) -> GeneratedImage: ...


@dataclass
class GeneratedVideo:
    """一次段视频生成结果：产物已落盘，时长/宽高/音轨来自 ffprobe 实测。"""

    provider_task_id: str
    file_url: str
    dest: Path
    size_bytes: int
    duration_sec: float
    width: int
    height: int
    has_audio: bool
    usage: MediaUsage


@runtime_checkable
class VideoProvider(Protocol):
    """段视频生成（H3 导演台 reference 模式）。

    reference_files 顺序即提示词 <Picture N> 编号；连续性尾帧由调用方放在首位。
    """

    def generate(
        self,
        *,
        prompt: str,
        duration_sec: int,
        reference_files: list[Path],
        dest: Path,
        seed: int | None = None,
    ) -> GeneratedVideo: ...
