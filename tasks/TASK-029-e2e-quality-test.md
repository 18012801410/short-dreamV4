# TASK-029 端到端质量测试与自我修复迭代（雨夜关东煮）

## 目标
从一句话想法真实跑通「剧本→资产→分镜→视频→成片」全链路，每阶段产物逐项人工级验收
（看图/看视频/抽帧），发现问题即修即重出，循环直到成片达标；修复对照官方文档口径。

## 测试载体
- 项目：`p-e6fd455a9702` 雨夜关东煮（E2E质量测试），双人对手戏，目标 40s
- 驱动：`scripts/test_pipeline.py`（in-process Worker，分阶段暂停供验收）+ `scripts/inspect_clip.py`（逐段抽帧验收）

## 最终成果
- **成片：`data/media/p-e6fd455a9702/films/film_v1_sub.mp4`（46.16s，1920×1088，含烧录字幕）**
- 9 段全部单段验收通过（含末秒崩坏抽检）；拼接时长误差 <0.1s；音轨满动态（峰值 0dB）
- 消耗：约 1721 RH 币（含全部返工与双 Worker 竞争浪费；正常单次全流程估约 900-1100 币）

---

## 各阶段发现与修复（共 12 项，全部闭环）

### 阶段 1 剧本（1 项）
| # | 问题 | 修复 |
|---|------|------|
| 1 | 老人 profile 混入英文「 mysteriously 淋透的老人」（LLM 文本污染） | edit_script_draft 手工修正 |

### 阶段 2 资产图（3 项）
| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 2 | 「雨夜街景」被分类为 prop，出图成白底微缩模型卡 | LLM 分类错误 + 提示词含 "miniature diorama look" | AssetAgent 加 kind 分类硬规则与微缩词禁令；textnorm 道具消毒正则加 diorama/miniature/scale model；数据侧 kind→scene + 提示词重写重出 |
| 3 | 关东煮汤锅缺分格/透明盖；"oden" 一词引入蓝色雨夜背景 | 提示词弱 + 词汇先验污染 | 换 "divided simmering pot / four compartments / clear glass lid" 后 v3 合格 |
| 4 | generate_asset_image 默认 view_label=主设定，场景卡（空镜标签）回退中文兜底提示词 | 调用方未传标签 | 按实际标签传参；兜底图质量高，采纳 |

### 阶段 3 分镜（6 项）
| # | 问题 | 修复（文件） |
|---|------|--------------|
| 5 | LLM 不遵守切段策略（v1 5段46s / v2 6段60s / 12s长段3切点） | agents.py：切段策略入 system prompt + 确定性校验（段数=round(target/5)±1、Σ时长≤±15%、单段≤8s、shots≤2），失败带错误重试 |
| 6 | 台词漏分配：v4 漏掉关键句「等得都快忘了自己为什么等了」 | agents.py：剧本台词↔dialogue_refs 逐字 Counter 对账（缺失/杜撰均报错） |
| 7 | LLM 长 JSON 被 DeepSeek 默认输出上限截断（EOF at 32426 chars） | 新增 `LLM_MAX_TOKENS` 配置（.env=8192）全链透传 max_tokens |
| 8 | cmd_edit_segment 两 bug：h3_prompt dict 不转换直接崩；StoryboardVersion 不存在 source 字段 | usecases.py：H3Prompt/Shot 显式 model_validate；移除非法字段 |
| 9 | validate_segment 只查 refs⊆`<d>` 不查反向：S01G01 提前写入了下一段台词（成片会读两遍） | validation.py：新增 `<d>` 块必须由 dialogue_ref 背书的反向对账；S01G01 提示词手工修正（v7） |
| 10 | 环境事故：遗留旧代码 Worker（anaconda python -m server.worker）与本测试抢队列，用旧校验保存了 v3、以 10s 默认时长覆盖新代码产物、每次任务产生重复 clip 行 | 终止两个遗留 worker 进程（24360/20448）；清理 9 条污染 clip 行后重产；教训：跑批前必须确认无并发 Worker，引擎无任务认领原子性 |

**v7 分镜定稿**：9 段×5s=45s（偏差 12.5%<15%），全段连续性链，10 句台词逐字分配，numbering_ok=9/9，
六段式结构/(S1)(S2) 说话人绑定/口型证据/尾帧承接句全部符合官方 h3-prompt-writing 口径。

### 阶段 4 视频生产（2 项系统性）
| # | 问题 | 修复 |
|---|------|------|
| 11 | **任何时长都出 10 秒**：工作流模板 segment 自带 10s/243 帧默认值，适配器只改了顶层 totalFrames/durationSec，工作流以 segment 字段为准 | video.py `build_timeline`：同步 segment 的 start/length/frameCount/durationSec；修复后 5s 段精确出 5.125s（5×24+3 帧） |
| 12 | 产物下载损坏（S02G06 大量 Invalid NAL unit，ffprobe 只读 header 不解码所以漏检） | ffmpeg_svc 新增 `decode_check`（全流解码）；video 适配器落盘后校验，失败自动重下一次，仍坏显式报错 |

**逐段验收结论**（均匀抽帧+末秒 3 帧，验收工具 `scripts/inspect_clip.py`）：
- S01G01 进店 ✓ / S01G02 停售对话 ✓ / S01G03 转身取碗 ✓ / S02G04 端出关东煮 ✓
- S02G05 老人动容用餐 ✓ / S02G06 关键句 ✓ / S03G07 掏表抵账 ✓ / S03G08 追出空街 ✓
- S03G09 首次生成出现**两个小满克隆**（多余人物缺陷）→ 重出一次后合格；新末帧表盘月牙刻纹与道具卡呼应
- 已知小瑕疵（记录不改）：跨段制服有漂移（围裙时有时无、领口样式变化）；S02G05 纸碗变瓷碗；
  角色卡名牌与部分店内海报文字乱码。均为文生视频/文生图当前能力边界。

### 阶段 5 合成
- compose 一次通过：concat + SRT 烧录；成片 46.157s vs Σ段 46.125s，误差 0.03s
- 字幕逐句与画面/说话时刻对位（抽 9 帧核对）；音轨 mean -17.1dB / max 0.0dB 无静音

---

## 沉淀的方法论（验收工具链）
1. **逐段门禁**：连续性链式生成下，坏段会污染下游尾帧参考 → 必须逐段验收后再产下一段
2. **末秒加密抽帧**：H3 生成漂移集中在结尾（S01G02 三人克隆出现在最后一秒，4 帧均匀抽样漏检）
3. **独立性**：跑测试前清点 python 进程，防止遗留 Worker 用旧代码抢队列（本次浪费约 400 币+3 轮排查）
4. **双层防线**：LLM 行为约束（提示词）必须配套确定性校验（schema/结构/对账），重试机制才能兜底

---

## 追加：拼接感诊断与 A 组修复（2026-09-14 下午）

### 拼接感/换脸根因（用户实测反馈）
1. **机制层**：尾帧以 reference_image 语义锚送入（H3 官方 first_frame 与 reference_* 互斥），
   每段是"重新想象"而非字面续接 → 切点两侧机位/站位/服装/脸全被重解读（抽帧对比证实）
2. **实现层缺口**：工作流模板有 `referenceVideo` 槽位与 `continuityFromPrev`+22 帧重叠能力，
   AI_SPEC 也要求连续段发上一段实际视频，但实现 `refVideos` 恒为空
3. **合成层**：concat -c copy 硬拼、无过渡、无调色统一、每段音轨独立生成硬接
4. **提示词层**：subject_definitions 服装描述泛化，未逐字锁围裙/领型/名牌

### A 组修复（已完成，全部回归绿）
| 修复 | 内容 | 文件 |
|------|------|------|
| 电影化合成 | 统一调色(eq contrast/saturation) + 切点 0.25s xfade 交叉淡化 + 段音轨 acrossfade + 连续粉噪声雨床遮盖环境音跳变 + alimiter 防削波 | ffmpeg_svc.py `compose_film` |
| 字幕对齐 | build_film_srt 支持段起点数组（淡化缩短总时长后按新时间轴对齐） | subtitles.py / handlers.py |
| compose 重试状态机 | 上次失败回退 video_ready 后重试，成功收尾 apply(composed) 会 STATE_ILLEGAL——handler 起始时拨回 composing | handlers.py |
| 服装逐字锁 | H3 规则 2：角色 Subject 定义必须逐项复述 visual_anchor 身份锁并写 "wearing exactly the same outfit..., identical in every shot" | agents.py |

### 成效
- film_v2（44.34s）：切点软过渡、全片一个色调、雨声连续（mean -14.7dB / max 0dB）、字幕按新时间轴对位
- 修饰性小瑕疵：淡化窗口内相邻两句字幕会短暂同屏（0.25s），后续可在 SRT 生成时按 fade 收缩窗口

### 追加：V2/V3 四件套移植 + 全量重出（2026-09-14 晚）
用户反馈"每段 5 秒单镜头硬切感严重"后研究 V2/V3 实现（short-dreamV3 h3_prompt.py/keyframes.py），
确认 V3 靠四件套让一镜一段的短段落也无硬切感，全部移植到 V4：

| 移植项 | 实现 | 文件 |
|--------|------|------|
| 强锚定句 | 连续段 Picture 1 定义升级为"frame 0 must match this image exactly"（LLM 不写时由 `_ensure_relay_anchor` 确定性注入——实测 LLM 仍倾向写旧弱句，代码兜底必要） | agents.py |
| 景别交替/位置感知 | 规则 8：相邻段首镜景别错开、开场段建立空间、收尾段推向高潮、固定陈设不得增删 | agents.py |
| 状态衔接 | 段尾 end state 显式化，下段 detailed_description 开头呼应 | agents.py |
| 口播预算校验 | 台词字数 > 段时长×5 字/秒 → 确定性报错带反馈重试 | agents.py |

配套修正：提示词密度按时长分级（≤5s 段 150-250 词，防止 9 段 JSON 超 DeepSeek 8192 输出上限被截断）；
chat_json 失败明细落 stderr（排障）。

**重出结果（v9 分镜 + 9 段全部重产）**：服装一致性显著改善（围裙/工牌全片在位，服装锁生效）；
段间衔接从"重新画一遍"变为"从上一段尾帧状态出发"；S03G09 首次生成整段画风坍塌成动漫
（特写手部+怀表把模型带偏），重抽一次回到真人电影质感。成片 **film_v3_sub.mp4（44.37s）**：
淡化过渡+统一调色+连续雨床+字幕按新时间轴对位。本轮视频重出约 470 币。

### 追加：一致性根治轮（2026-09-14 深夜，代码就绪待充值续跑）
用户反馈"衣服变来变去、男人头发也变"。逐帧蒙太奇分析（每段 12 帧）确认：段内一致性很好，
**跨段漂移集中于脸/制服版型/老人发态**——根因=四视图拼贴卡是弱身份锚（模型每次从拼贴取脸
位置不同）+ 角色卡(棚拍干爽)与尾帧(剧情湿透)两套外貌证据冲突 + 尾帧接力链累积漂移。

**已落地的代码修复（125 测试全绿）：**
1. 角色卡改单视图正面定妆照（AssetAgent 模板 + 兜底提示词）——消灭拼贴取脸随机性；
   小满/老人两张新定妆照已生成并批准（旧四视图卡已删）
2. 切段策略改 **8 秒双镜头**（段数 9→6，切点减半；段内第 2 镜同一次生成身份天然连续）+
   段数公式 round(target/8)、夹具更新
3. segment_key 确定性规范化 `_normalize_segment_keys`（index 重排场景内序号、key=S{scene}G{index}、
   continuity 引用同步）——LLM 的场景内/全局/漏零三种命名全部自动纠正
4. entities.py 解析期 key 一致性强校验移除（拦住规范化），语义下沉 validation.validate_segment
5. SEGMENT_KEY_RE 放宽允许 1-2 位场景号（LLM 偶尔漏补零写 S1G01）
6. JobRepo.claim_next_runnable 原子认领（条件 UPDATE 抢占）——多 Worker 并发不再双跑
7. shots[].description 改英文短语 ≤25 词（中文描述撑爆 DeepSeek 8192 输出上限的主因）

**运维发现：scripts/watchdog.sh 三层守护**（03:31 启动的 3 个 bash 实例互为备份，30s 轮询
拉起 worker/API）——曾导致杀掉的旧代码 Worker 反复复活、以旧代码处理任务污染生成。
已与用户侧对齐：代码已稳定，watchdog 保活的 Worker 加载的是最新代码，可安全保留；
**开发改码期间应先停 watchdog（bash scripts/watchdog.sh 的进程），改完再启动。**

**阻塞**：DeepSeek 账户余额不足（HTTP 402 Payment Required），分镜重生成暂停于 job-6fb86e19d736。
充值后续跑：①重置该任务为 pending → ②`python scripts/test_pipeline.py wait p-e6fd455a9702`
→ ③验收 v12 分镜（8s 双镜+锚定句+服装锁）→ approve → ④逐段 `video <key>` + inspect 验收
→ ⑤compose 出 film_v4。

### 追加：GLM 切换 + v14 全量重出完成（2026-09-15 凌晨）
用户充值后提供 GLM key，经三轮对比确定 **GLM-4.6 + 分场景调用** 组合成功：
- glm-5.3-flash：输出偷懒（只写 soundscape+music 骨架），弃用
- deepseek-reasoner：response_format 不支持（空内容），适配器已加绕行；且 CoT 吃满 max_tokens，需 32K 预算
- glm-4.6：结构完整 ✓，配合分场景调用（单次输出缩至 1/3）不再截断 ✓

**最终成果：film_v4_sub.mp4（54.29s，1920×1088，含字幕）**
- v14 分镜：9 段（6+8+5+6+8+5+6+6+5）、8 个连续段全部带强锚定句、服装锁全片在位
- 电影化合成：统一调色 + 0.25s xfade + 音轨 acrossfade + 连续雨声床
- 终帧：小满工牌特写 + 月牙怀表 + 雨夜便利店暖光，定妆卡级一致性
- 音轨 mean -15.1dB / max 0dB，无静音无削波

**新增基础设施（后续项目直接受益）：**
1. `server/app/h3_compiler.py`——六段提示词确定性编译器（LLM 只产结构化数据，
   提示词由代码编译，"构造即合法"，V3 哲学落地 V4）
2. `_normalize_sections`——章节乱序/挤行/重复的确定性重排
3. `_upgrade_base_to_reference`——base(三段) → reference(六段) 确定性升级
4. `_autorepair_scene`——台词模糊还原逐字原文、口播超限自动加长段时长（≤8s）、
   [Shot 1] 时间戳误加剥离、subject_definitions 缺失注入
5. `LLM_TIMEOUT_SEC` 配置化（推理模型/长输出可调）
6. Job 原子认领 + 驱动 failed 复查（防瞬时失败误判退出）

### 追加：referenceVideo 视频续接实验（2026-09-15）
接入导演台 referenceVideo 槽位（上传前段成片 mp4 → 段级 referenceVideo + continuityFromPrev=true），
S01G02 A/B 实测结论：**该槽位当前语义偏"内容/风格参考"而非"帧 0 续接"**——生成从便利店外景
重新开始（参考视频的起点而非终点），月牙怀表提前出现，小满道歉台词整段丢失，偏离分镜。
处理：该段回退分镜忠实的 v14 渲染；代码路径保留但默认关闭（settings.h3_reference_video=false），
待官方文档确认槽位语义或拿到 first_frame 类 task type 字符串后再开启。
同轮确认 DeepSeek 400 根因之一为 .env 三件套错位（GLM key/base + deepseek 模型名），
已统一。**最终交付：film_v5_sub.mp4（54.29s）**——S01G02 用 v14 渲染（分镜忠实、双角色
对话完整），其余段同 film_v4。

### 追加：口播截断根治（2026-09-15，用户反馈"说话没结束被截断"）
根因：口播预算按 5 字/秒校验过松，且把预算降级为警告后接受超速——H3 实际中文语速
约 3.5-4 字/秒，超速段尾句被吞。修复：
1. 校验阈值 5 → 4 字/秒（ spoken > duration×4 即报错）
2. 自动加长上限 8s → 12s（H3 支持 4-15s），加长后同步均布重算切点
3. 删除"按比例缩放总时长"的兜底（缩放正是掐断台词的元凶——缩时长必超预算）
4. Σ总时长从硬门降为 warning（场景 est_seconds 是粗估；台词完整 > 时长精准）
配套发现并修复：分镜版本迭代时 clips 的 storyboard_version_id 未随迁（尾帧接力
"前段尾帧不可用"的根因），迁移后旧渲染可直接复用。

**最终交付：film_v6_sub.mp4（58.32s）**——台词段全部 4 字/秒以内（台词完整不掐断），
全片双镜头、强锚定、服装锁、电影化合成齐备。

### 遗留建议（未在本次实施）
- WorkerEngine 任务认领无原子性（PENDING 即可被执行），多进程部署会双跑：建议加 DB 行锁或租约
- 引擎对 TASK_QUEUE_MAXED 类供应商限流的退避应加长（分钟级），当前 5/10/20s 会快速耗尽重试
- compose 的 clip 选择取 clips[-1]，clip 表会随重生成无限增长：建议按 (segment, storyboard_version) 唯一化
-角色卡名牌乱码为通病，可在角色模板中弱化「名牌文字」描述或后期贴字
