# 任务 TASK-001 — Spike：供应商契约实测（RunningHub 工作流通道）

## 目标
用真实 API 验证现行通道（RunningHub 工作流 API，AI_SPEC 附录 B）的两类契约：QWEN 文生图工作流端到端；MiniMax H3 导演台工作流端到端（上传参考图 → 单段 timeline → 轮询 → 下载 → ffmpeg 抽尾帧）。结论回写 AI_SPEC 附录 B。

## 背景
2026-09-12 用户拍板：视频/图片从 MiniMax 官方直连改为 RunningHub 工作流（原附录 A 方案作废为备选）。适配器实现（TASK-006/007）前先用最便宜方式钉死契约。

## 参考
- AI_SPEC.md 附录 B（RunningHub 契约，现行）/ 附录 A（官方直连，备选）
- workerflow/ 两个工作流 JSON（输入节点结构）
- PROJECT_STATE.md

## 范围
脚本 `spike/runninghub_spike.py`（--skip-image 可复用参考图重跑视频步）：
1. 文生图：工作流 2098715929763995649，nodeInfoList 改节点 6 提示词 + 节点 5 seed
2. 上传 `/openapi/v2/media/upload/binary` 换 fileName
3. 视频：工作流 2098713358475288577，节点 28 单段 timeline（refs[0]=上传图，7s/175 帧沿用模板口径）
4. ffmpeg 抽尾帧 + 校验尺寸/音轨

## 实测结论（2026-09-12，已回写 AI_SPEC 附录 B）
- ✅ 文生图端到端：create→RUNNING→SUCCESS→outputs→下载 7.26MB png；耗 21 RH 币、104s；产物节点 18（SaveImage）
- ✅ 轮询契约：`/task/openapi/status` 返回 QUEUED/RUNNING/FAILED/SUCCESS 字符串
- ✅ 上传鉴权：**只认 `Authorization: Bearer` 头**；form/query 传 apiKey 均报 "apiKey is required"（官方文档示例误导，已实测纠正）
- ✅ 上传成功：fileName 形如 `openapi/<sha>.png`，可直接填 ComfyUI 节点输入
- ❌ **视频工作流 2098713358475288577 不可用**：`create` 与 `getJsonApiFormat` 均报 810 `WORKFLOW_NOT_SAVED_OR_NOT_RUNNING`（同 key 下图片工作流正常，key 本身没问题）→ 该工作流在此账号下不存在/未保存/未运行，或 ID 有误。需用户在 RunningHub 确认：①工作流页面 URL 里的真实 ID；②工作流是否已保存到自己账号并至少运行/发布过一次
- 节点 28 timeline_data 驱动方式（单段 + refs[0] 替换）待工作流可用后验证

## 非目标
- 不写正式适配器代码（TASK-006/007）；不测官方 V2 直连（附录 A 备选）
- 不测 callback、参考视频/音频

## 验收标准
- [x] 文生图成功，图片落盘且可打开（ref_image.png 7.26MB，21 币/104s）
- [x] r2va 视频成功：mp4 可播放、10.125s（243 帧@24fps）、有音轨、1920×1088（2026-09-13，51 币/252s）
- [x] 尾帧 jpg 落盘且尺寸 ≥256px（1920×1088，画面与参考图身份一致，目检通过）
- [x] 实测参数与 AI_SPEC 附录 B 逐项对账（上传鉴权勘误、工作流 ID 勘误、时长=帧数/帧率 均已回写）
- [x] 脚本可重复执行（--skip-image 幂等复用）

**结论：TASK-001 完成（2026-09-13）。** 遗留移交：`create` 响应含 `netWssUrl`（WebSocket 进度通道），MVP 不用、继续轮询即可。

## 验证
- [x] `python -m ruff check spike` 全绿
- [x] 产物落盘 spike/out/（已 gitignore）
