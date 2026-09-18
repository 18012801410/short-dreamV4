# 任务 TASK-006 — ImageProvider：RunningHub QWEN 文生图工作流适配器

## 目标
实现 `server/adapters/image.py`：经 RunningHub 工作流 2098715929763995649（QWEN 文生图 4 步）生成资产设定图，产物立即下载落地本地，usage 透出。配套共享平台客户端与单测 + 一次真实冒烟。

## 背景
阶段 3 适配器第二个任务。契约已由 TASK-001 实测钉死（AI_SPEC 附录 B + 实测记录）：create→status→outputs 三段式；节点 6=提示词、节点 8=宽高、节点 5=seed；产物 fileUrl 临时必须立即落地。

## 范围
- `server/adapters/runninghub.py`：`RunningHubClient`（create_task / status / outputs / cancel / upload / download / wait_for_success）+ `RunningHubError`（AUTH/REJECTED/TASK_FAILED/TIMEOUT/UPLOAD_FAILED/BAD_RESPONSE/TRANSPORT）。spike 验证逻辑产品化，TASK-007 VideoProvider 复用。
  - upload 只认 `Authorization: Bearer` 头（TASK-001 实测勘误）
  - wait_for_success 超时 → 尽力 cancel（省币）→ 抛 TIMEOUT
- `server/domain/providers.py` 增补：`ImageProvider` 协议、`GeneratedImage`、`MediaUsage`。
- `server/adapters/image.py`：`RunningHubImage.generate(prompt, width, height, dest)`——随机 seed、三节点 nodeInfoList、轮询至终态、按后缀选产物、下载写盘（自动建父目录）；`build_image_provider_from_settings()` 工厂。

## 非目标
- ratio→像素的映射与资产落库编排（用例层 TASK-009）；参考图合规缩放（在参考解析 TASK-011 做，生成侧画幅本身合规）；ProviderCall 落库（handler 层用 GeneratedImage.usage 记）。

## 需求
1. fileUrl 临时链接必须立即下载落地（红线），DB 只存相对路径（仓储层职责）。
2. apiKey 只进请求体 `apiKey` 字段与 Bearer 头（RunningHub 契约），不进日志。

## 验收标准
- [ ] generate 全链：create→RUNNING→SUCCESS→outputs→下载写盘，返回 task_id/seed/url/usage
- [ ] create code=433（提示词审核）→ REJECTED，带 msg
- [ ] status FAILED → TASK_FAILED，details 带 failedReason
- [ ] 轮询超时 → cancel 被调用且抛 TIMEOUT
- [ ] upload 仅用 Bearer 头
- [ ] 真实冒烟：生成 1 张图落盘可打开（≈21 币）

## 验证
- [ ] `python -m ruff check server spike`
- [ ] `python -m pytest server/tests` 全绿
