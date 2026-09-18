# 任务 TASK-007 — VideoProvider：RunningHub H3 导演台 + FFmpegService 扩展

## 目标
实现 `server/adapters/video.py`：经 RunningHub 工作流 2093160296864116737（MiniMax H3 导演台 ref2va，节点 12）逐段生成视频——单段 timeline 组装（reference 六段模式）、上传参考图、提交/轮询/下载、ffprobe 测真实时长；扩展 `FFmpegService`（probe / 尾帧 / concat）。配套单测 + 一次真实冒烟。

## 背景
阶段 3 收尾。契约 = AI_SPEC 附录 B 实测记录（10s→243 帧→实际 10.125s、audioMode=generate、输出节点 7）。提示词本身已由领域校验器（validation.py）按官方 dialect 把关，本任务只负责运输与组装。

## 范围
- `server/domain/providers.py`：`VideoProvider` 协议、`GeneratedVideo`（含 probe 得到的真实时长/宽高/音轨）。
- `server/adapters/video.py`：
  - `build_timeline()`：以工作流模板 timeline 为底，单段化、写提示词、refs=上传 fileName（顺序即 <Picture N> 编号）、audioMode=generate、runSelection=[0]；`total_frames = duration_sec*24 + 3`（TASK-001 观测口径，真实时长以 ffprobe 为准）
  - `global_prompt` = 从完整六段提示词中提取 `subject_definitions` 段（身份锁），段 prompt = 完整提示词
  - `RunningHubVideo.generate(prompt, duration_sec, reference_files, dest, seed=None)`：上传 refs → create → 轮询 → 选节点 7 mp4 → 下载 → probe
  - base（无参考图）模式：本工作流模板无 t2v task_type 字符串，未实测——显式抛 UNSUPPORTED_MODE（记入开放问题）
- `server/adapters/ffmpeg_svc.py`：`FFmpegService`（probe / extract_tail_frame / concat -c copy）+ 保留 detect_ffmpeg。

## 非目标
- resolve_references 与模式矩阵的用例编排（TASK-011，尾帧作为 references[0] 的语义由该层实现）；<Picture N> 编号与 refs 顺序一致性由调用方保证（提示词校验已管）。

## 需求
1. mp4 URL 临时：立即下载落盘（红线）。
2. Clip 记录的真实时长来自 ffprobe，不信任请求参数。

## 验收标准
- [ ] timeline 组装：单段、refs 按 uploads 顺序、global_prompt=subject_definitions 提取、audioMode=generate、runSelection=[0]
- [ ] 全链：create→SUCCESS→outputs（优先节点 7）→下载→probe 得真实时长
- [ ] 无参考图 → UNSUPPORTED_MODE
- [ ] FFmpegService：probe/尾帧/concat 对真实生成的测试视频工作（ffmpeg 缺失则跳过）
- [ ] 真实冒烟：10s 段生成成功、时长/音轨经 ffprobe 验证（≈51 币）

## 验证
- [ ] `python -m ruff check server spike`
- [ ] `python -m pytest server/tests` 全绿
