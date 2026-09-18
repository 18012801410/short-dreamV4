# 任务 TASK-005 — LlmProvider：OpenAI 兼容 + JSON mode 结构化输出 + 重试

## 目标
实现 `server/adapters/llm/`：OpenAI 兼容 ChatCompletions 适配器（GLM/DeepSeek/MiniMax 等可配 base_url+model），支撑三个 Agent（剧本/资产/分镜）的 JSON mode 结构化输出与护栏重试。配套单测（MockTransport，不依赖真实 key）。

## 背景
阶段 3 适配器首个任务。AI_SPEC「结构化输出」：所有 LLM 输出走 JSON mode + Pydantic 校验，校验失败带错误重试 1 次；每次调用记录 tokens 用量。ARCHITECTURE：Provider 接口由 domain 定义、adapters 实现。

## 范围
- `server/domain/providers.py`：`LlmProvider` 协议（chat / chat_json）、`LlmResult`、`LlmUsage`（domain 拥有接口，零框架依赖——仅 Pydantic 泛型）。
- `server/adapters/llm/base.py`：`LlmError`（code：AUTH / TRANSPORT / BAD_RESPONSE / EMPTY_CONTENT / SCHEMA_VALIDATION）。
- `server/adapters/llm/openai_compatible.py`：
  - chat：POST /chat/completions，system+user 消息，透出 content 与 usage
  - chat_json：JSON mode（response_format=json_object）→ 剥 ```json 围栏 → Pydantic 校验；失败把校验错误摘要追加为纠错消息重试 1 次；再失败抛 SCHEMA_VALIDATION
  - 传输层：429/5xx 指数退避重试（≤2 次，sleep 可注入）；401/403 → AUTH；其他非 2xx → BAD_RESPONSE
- `server/adapters/llm/__init__.py`：`build_llm_from_settings()` 工厂（key 缺失抛 AUTH）。

## 非目标
- 三个 Agent 的提示词模板与 handler 接入（TASK-008/009/010）；ProviderCall 落库（handler 层用 LlmResult.usage 记）；真实 key 冒烟（等 LLM_API_KEY）。
- 流式输出、工具调用、多轮对话记忆（MVP 不用）。

## 需求
1. 同步 httpx.Client（Worker 引擎为同步 handler 协议；TECH_SPEC 的「httpx async」备注由本任务修订为同步，Worker 单进程逐 Job 串行，无并发需求）。
2. apikey 只进 Authorization 头，日志/异常消息不得包含 key。
3. 重试计费安全：schema 重试会重复消耗 tokens（LLM 按量计费，属预期），但最多 1 次。

## 验收标准
- [ ] chat 返回 content + usage
- [ ] chat_json 合法 JSON 直接过；带 ```json 围栏也能过
- [ ] 首次输出不合法 → 第二次请求包含纠错消息并通过，attempts=2
- [ ] 两次都不合法 → SCHEMA_VALIDATION，details 含两次原始输出摘要
- [ ] 401 → AUTH 立即失败；429 → 退避重试后成功；5xx 同 429
- [ ] key 为空时工厂抛 AUTH

## 验证
- [ ] `python -m ruff check server`
- [ ] `python -m pytest server/tests` 全绿
