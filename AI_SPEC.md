# AI 规格（AI Specification）—— short-dreamV4

## AI 职责
- 由想法生成结构化剧本（场景/角色/台词/时长估算）
- 由剧本抽取视觉资产（角色/场景/道具 + 外观锚点）并产出每张设定图的生图提示词
- 由剧本+资产生成分镜（段/镜头/切点/运镜/资产引用）与每段的 H3 视频提示词
- 生成的内容一律以 draft 版本进入人工审阅，AI 不直接确认

## AI 非职责
- 不调用视频/图片供应商（那是 Worker 适配器的事）
- 不决定流水线推进（确认门永远由人操作）
- 不修改已批准（active）工件；重生成一律新建 draft

## 智能体

### 智能体：ScriptAgent
目标：把想法+参数变成可拍摄的结构化剧本（EP001 文学剧本口径：节拍流承载画面描述）。
输入：idea、genre、style、dramatic_tone、target_duration_sec、scene_count、prompt_lang。
输出：ScriptContent（JSON Schema，Pydantic 校验）：
```json
{ "logline": "…", "scenes": [ { "id": "S1",
    "title": "外 · 霓虹夜市街头 · 夜", "summary": "…",
    "beats": [ { "type": "action|dialogue|sfx|on_screen_text|transition",
                 "text": "…", "speaker": "陆峥", "tone": "声音很低" } ],
    "est_seconds": 30 } ],
  "characters": [ { "name": "林晚", "profile": "…" } ], "props": [ "…" ] }
```
节拍流硬规则（2026-09-15 修订）：
- `title` 是场景头三要素「内/外 · 地点 · 日/夜」；
- `beats` 按时间排序覆盖每一个视觉事件，画面不能只有说话：action（谁/在哪/做什么/怎么做，
  具体到道具姿态方位光，每条 20-60 字，每场 4-12 条）、dialogue（text 只装说出口的话，
  speaker/tone 配套）、sfx、on_screen_text、transition（每场至多一条、放结尾）；
- 动作与台词交替；关键道具必须出现在 action 节拍并登记进 props；
- `dialogues` 由 dialogue 节拍派生（代码确定性生成，LLM 不再输出）：台词中的括号舞台指示
  归位为 `tone`（不丢弃，取代旧版"剥离后扔掉"）；
- 降级兼容：LLM 未写 beats 时从 dialogues/summary 回填并附 warnings（旧剧本 beats=[] 照常加载）；
- **人物与道具唯一命名（2026-09-17 用户硬要求）**：characters[] 是全剧本唯一人物登记表，
  dialogue.speaker 逐字等于登记名（禁别名/简称/称呼混用），action/summary 提及人物用登记名，
  同一道具只登记一次并贯穿全篇；生成后 `server/domain/name_registry.py` **确定性归一**——
  系列模式先把剧本人物名对齐大纲登记名（别名等价：全名/括号注名/去括号基名），再合并
  剧本内重复合名（保留说话人最多者）、全文别名改写为规范名、props 去重；无法自动修复的
  （未登记说话人、大纲外人物、疑似别名道具）写 warnings 交人工。
工具：LlmProvider.chat（JSON mode）。
**剧作基调双轨法则（2026-09-15 TASK-030，豆包短剧方法论内化；2026-09-17 TASK-048 提质重写）**：按
`params.dramatic_tone`（`hook` 默认 / `three_act`）向 system 注入对应结构法则——
- `hook`（短剧钩子驱动）：黄金开局（第一场前 3 拍抛核心矛盾，套用可拍开场模板：
  直接打脸/身份反转/极端困境/倒叙悬念，出场 ≤3 人）、高频反转+情绪密度（铺垫 ≤20%）、
  压迫具体化+释放降维打击（第一个爽点 ≤ 全片 40% 处）、反派合理性动机 + 反派惩罚铁律
  （片内实质惩罚）、台词千人千面且口语化（单句目标 8-20 字、硬上限 25 字，禁解释腔）、
  结尾卡点（最后一拍落在情绪最高点/悬念定格，transition 写明）；
- `three_act`（微电影三幕式）：单一核心事件、三幕节拍配比约 25/50/25、允许留白
  （纯画面节拍）、对立面可意象化（物件承载，不必人格化反派）、台词克制禁总结陈词；
- 两轨通用：情绪外化（action 禁抽象情绪词，写身体细节）、动作客观可拍。
未知基调回退 hook。`update_params` 可改 dramatic_tone/style/target_duration_sec/scene_count
（TASK-048 开放时长与场景数，生效于后续生成）。
护栏：schema 校验失败自动携带错误重试 1 次，再失败任务失败；场景数≈scene_count；估算总时长与 target_duration 偏差 >30% 时在结果中附 `warnings`。

**资产卡画幅与道具卡细则（2026-09-15 二次修订）**：
- 资产卡尺寸跟项目画幅（9:16 竖屏项目出 1080×1920 竖版卡；size_for_asset_card(ratio)），
  提示词内画幅词随 ratio 生成，场景卡三面板竖版改为纵向排列（上/中/下）；
- 道具一卡一件：剧本里的不同小物件各自独立成卡，严禁成对/多件物品拼进同一张卡
  （复合卡在特写参考时稀释单件身份）；
- 角色卡兜底模板不再追加写死的 realistic style（与项目风格冲突），画幅词同步 ratio。

### 智能体：SeriesAgent（TASK-047，2026-09-17：系列分集）
目标：把一个想法变成**多集短剧**——先全剧大纲、后逐集剧本（行业标准打法：先大纲后剧本，
卡点节奏全季可控）。大纲与逐集剧本均走 LLM（零币），集内制作复用既有流水线
（每集 = 一个挂 series_id/episode_no 的普通 Project）。

**run_series_outline（大纲生成，温度 0.8）**
- 输入：series.idea、SeriesParams（genre/style/dramatic_tone/episode_count=12/per_episode_sec=90/scene_count=4/ratio…）。
- 输出 SeriesOutline（JSON Schema）：`{title 剧名, logline, genre_tags, characters[]（身份/动机/口头禅/爽点功能）, episodes[]（episode_no/title/synopsis 80-200字/opening_hook/ending_hook/highlight/new_characters）, climax_episode, warnings}`。
- system 注入爆款方法论 `_OUTLINE_LAWS`：大众题材库（战神/赘婿/甜宠/复仇…可叠加）、
  五大爽点矩阵（身份碾压/打脸复仇/逆袭翻盘/情感爆发/悬念揭秘，同类型不连续、强度递增）、
  五类集尾钩子轮换（悬念/反转/情绪/信息/危机，连续 3 集不同型）、黄金 3 秒开场模板、
  全剧节奏四阶段（起势/攀升/风暴/决战）、反派四层体系、人物表 ≤10/主角组 ≤5。
- 集数 >12 分批生成（每批 ≤12 集，携带既定剧名/人物表/上集梗概续写）；
  落库前确定性重排集号并截断到请求数。
- 确定性质量门 `server/domain/outline_quality.py`（零 LLM）：硬门=集号连续/集数一致/
  每集开场钩子必填/集尾卡点除大结局外必填/第 1 集必有爽点/大爆点不压最后一集——
  **确认大纲时硬拦**；软门=爽点真空 ≤3 集/人物收敛/梗概厚度/卡点不同文——只提示。
  生成与每次编辑都会重跑质量门，结果写进 `outline.warnings`。

**run_episode_script（逐集剧本，温度 0.8）**
- 输入：SeriesOutline + 本集 EpisodeOutline + 上一集 ending_hook + SeriesParams。
- 输出：ScriptContent（与 ScriptAgent 同 schema，下游资产/分镜/视频零改动消费）。
- system = 剧本公共骨架 + 基调法则 + `_SERIES_LAWS`（连载模式）：X1 承接上集
  （第一节拍回应上集卡点）、X2 落实大纲（首拍=开场钩子、末拍=集尾卡点、爽点可感知释放）、
  X3 跨集悬置许可（大反派可跨集，但每集必须有一次本集内小对抗释放——本条优先于
  单片 H4 的「禁止跨集悬置」）、X4 人物一致性（只用全剧人物表角色）、X5 信息配给
  （上集悬念给新进展，结尾必带新问题）。
- user 携带全剧人物表/本集四件套（钩子/梗概/爽点/卡点）/下一集走向/单集时长与场景数。
- 护栏与 ScriptAgent 一致（时长偏差 >30% 附 warnings；台词口播预算 ≤ 时长×3 字）。

**系列资产一致性（2026-09-17 用户拍板：一致性优先，杜绝每集重抽）**：资产基准集 =
同系列中「有已批准且已落盘图」的集（集号最小者优先）。此后其他任何一集点「抽取资产」
**自动变为免费继承**：整体替换目标集现有资产（与重新抽取同口径）+ 复制来源集卡片
与图片行（同一份磁盘文件），目标集直接落 ASSET_READY——全系列同一套脸、零币。
确要从本集剧本重写卡片并重新生图时，`generate_assets` 传 `payload.force_extract=true`。

### 智能体：AssetAgent
目标：剧本 → 资产清单 + 每资产的图片提示词。
输入：active ScriptContent。
输出：
```json
{ "assets": [ { "kind": "character|scene|prop", "name": "…",
    "description": "…", "visual_anchor": "年龄段/身材/发型/服饰色/签名道具等身份锁描述",
    "image_plan": [ { "view_label": "主设定|空镜", "image_prompt": "英文生图提示词" } ] } ] }
```
工具：LlmProvider.chat（JSON mode）。
护栏：kind 枚举校验；image_prompt 必须英文且含风格词（取 Project.params.style）；主角必须有主设定图。
**资产卡规范（2026-09-16 修订；道具见 TASK-020，场景见 TASK-042，角色见 TASK-043）**：
每个资产只出**一张设定卡**（image_plan 恰好 1 项），所有细节进同一张图——
- 角色卡（view_label=主设定）：**单幅正面平视全身定妆照**（TASK-043，替换此前的半身定妆像）——人物站直、正对镜头、双臂自然垂放，头到脚完整入画（不裁头顶与鞋），整个人物占满画面高度并居中；脸五官清晰对称、表情自然、直视镜头；**服装从头到脚完整可见**（上衣/下装/鞋与配饰都要看清）；干净浅背景；**只画人物本身，不画道具/箱子/载具**，但角色随身佩戴的配饰（手表/挂坠/工牌）照常画在身上（道具另有独立白底卡；外卖员等身份的语义联想仍可能带出随身物，可用 negative 通道或改写 anchor 压制）；严禁拼贴/多视图/三视图/分格，不再拆分面部特写/半身像/侧面像。理由（EP001 同 seed A/B 实测，PROJECT_STATE 2026-09-16）：全身版裤子/外套下摆/鞋的证据完整、脸部仍清晰，半身版下半身无据可依 → 用户拍板改全身；取景词（全身/立绘/full-body）只属卡片本身，写进 visual_anchor 会把剧情镜头锁成全身站姿，`sanitize_character_anchor` 确定性剥离；
- 场景卡（view_label=空镜）：**单幅广角空镜全貌**（TASK-042，替换此前的多视图拼贴卡）——一台相机、一个机位、一条连续画面；广角/超广角镜头（约 16-24mm）从能看见空间全貌的机位拍摄（室内取角落或略高于视线，街道取一端或对面临街位置），让空间绝大部分一次性入画：四面墙、两端与主要地标同框，纵深与前后层次清楚；同一组连续性地标在这幅画面内位置/数量/大小清晰可辨（防物体漂移），含关键光态与重要道具；**不分格/不拼贴/不多机位/平面图**。理由（实测）：多面板空镜卡作为关键帧图生图参考时几何自相矛盾（[imgN] 到底以哪一面板为准），模型只能在多个机位间摇摆，还与 KEYFRAME_NEGATIVE 的 split screen/collage 自相打架；9:16 项目里竖排三面板会被画成细条加黑边（EP001/末班车实测）。单幅广角卡同时给关键帧与 H3 视频一个可摆放人物的稳定空间；关键帧提示词因此显式声明"该参考图是同一空间的广角全貌：地标位置、朝向与相互距离以它为准；本帧的景别与构图按画面描述来"，避免参考图把特写拽成远景（TASK-038 链式锚定实测的同一机制）；
  场景卡措辞必须纯正向（a deserted and empty place / every booth stands empty），**不写 people/silhouette/mannequin 等人形词**（文生图模型会反向引入人形，实测教训），`sanitize_scene_prompt` 出图前确定性剥人形词；负向通道（稳定版 cfg>1，IMAGE_NEGATIVE_NODE=7）另拦 people 与 split screen/collage/multiple panels/contact sheet/black bars，确保卡片是单幅；
  场景 visual_anchor 只写空间事实（地标/布局/材质/光态），**不写「广角/机位/视角/构图/全景」等取景词**——取景属于卡片本身，写进锚点会把后续每个剧情镜头的景别锁成广角远景（`sanitize_scene_anchor` 编译期兜底剥离）；
- 道具卡（view_label=主设定）：单个道具居中立于纯白背景，棚拍产品图风格、崭新未使用，道具独占画面；正/负向词均严禁手/人/场景概念（正向写 isolated/unworn，负向用 IMAGE_NEGATIVE_PROP：hands/person/background 等）；sanitize_prop_prompt 双入口兜底。
LLM 违规拆分时应用层确定性裁剪（优先保留 主设定/空镜）；出图尺寸固定 1920×1080（size_for_asset_card，参考图不决定视频画幅，H3 只取身份与风格）。video_prompts 阶段在提示词中重复 visual_anchor 身份锁，同官方「提示词中重复的身份锁」。

### 智能体：StoryboardAgent
目标：剧本+资产 → 分镜（段→镜头）+ 每段 H3 提示词。
输入：active ScriptContent、资产集（含 visual_anchor）、Project.params（ratio/resolution/prompt_lang/style）。
输出（每段）：
```json
{ "segment_key": "S01G01", "scene_id": "S1", "index": 1, "duration_sec": 10,
  "shots": [ { "shot_no": 1, "cutpoint_sec": 0.0, "camera": "push in",
               "beat_refs": [1, 2], "description": "英文短语 ≤25 词",
               "action": "英文动作时间线段落（本镜窗口 开始→中途→结束，≤5s 镜 60-120 词，6-8s 镜 100-160 词）",
               "dialogue_refs": [ { "speaker": "林晚", "line": "…", "tone": "in a low voice" } ] } ],
  "asset_refs": [ { "asset_id": "…", "usage_note": "主角全身参考" } ],
  "continuity": { "enabled": true, "with_prev_segment_key": "S01G00" },
  "soundscape": "英文环境声（场景底噪 + 剧本 sfx 节拍）",
  "music": "英文配乐意图（无配乐写 N/A）",
  "keyframe_description": "英文开场锚点静态描述（第 0 帧构图/人物位置姿态/持物/光效，20-50 词；纯静态，禁台词/口型）",
  "subject_placements": [ { "name": "在场角色资产名（逐人一条）",
                           "placement": "英文开场空间状态：座位/站位+朝向+控制关系（in the driver's seat, hands on the steering wheel）",
                           "in_frame": "布尔：本段第 0 帧画面里是否可见（true/false，缺省 true）" } ],
  "h3_prompt": { "text": "", "lang": "en" } }
```
节拍认领（2026-09-15 修订）：每个镜用 `beat_refs` 认领它负责呈现的场景节拍序号（1 起）；
场景中所有 action / on_screen_text 节拍必须被至少一个镜头认领（确定性校验，丢拍即重试）；
dialogue 节拍由 dialogue_refs 逐字承载；`h3_prompt.text` 恒写空串——六段提示词由
`h3_compiler.compile_h3_prompt` 从结构化数据确定性编译：画面密度取 `Shot.action`
（空则回退 description），语气取 `ShotDialogueRef.tone`，声音/配乐取
`Segment.soundscape/music`（空则中性兜底，不再硬编码雨声），风格行取 Project.params.style
（空则默认电影感，不再恒定夜景）。
说话人绑定（2026-09-15 二次修订，对齐官方方言）：有角色资产背书的说话人在
subject_definitions 生成官方句式 `<Subject N> is … (SN) in <Picture M>, with …`（人与其
参考图挂钩），detailed_description 里台词由 `<Subject N> (SN) says …` 引出（ID 绑脸，
防止台词配给画面里唯一可见的嘴；旁白等无资产背书者保留原名，不造未定义标签）；
retention_analysis 按参考图类型分化——角色卡只锁身份（姿态表情归本镜）、场景卡只锁
空间地理（光态天气归本镜）、道具卡锁形状颜色标记、尾帧只锁开场构图姿态。
视频输出画幅：input_snapshot.ratio（produce/regenerate 快照携带）→ generate →
`dims_for_ratio` 换算（模板 864×480 横版为像素预算，方向不同则交换），写入 timeline
顶层与 output 节点；9:16 竖屏项目不再产出横版成片。
**身份唯一性与空间状态（2026-09-16 TASK-032，重影与位置错误根治）**：
- **锚点唯一**：角色外貌锚点全文只在 `<Subject N>` 定义出现一次。`<Picture N>` 只声明
  参考图角色（角色卡写 "character identity reference card of X (identity only, not an
  additional on-scene person)"；场景卡写 "location reference of X: <布局锚点>"）。
  同一锚点在 Picture/Subject 两处出现会被执行端读成两个人（实测 S03G03 三克隆、
  S01G02 双风衣男）。
- **在场角色全建 Subject**：含不开口者（官方"不留没名字的那个人"），说话人追加 (Sx)；
  Subject 编号按 asset_refs/参考图顺序独立编号，与 (Sx) 台词 ID 各自成序。
- **锚点消毒（编译期，存量数据兜底）**：`sanitize_character_anchor` 剥离"直视镜头/白底/
  定妆/半身/全身/立绘/full-body"等卡片摆拍与取景短语（姿势与景别属参考卡，TASK-043 角色卡
  是全身定妆照，取景词留在锚点会把剧情镜头锁成全身站姿）；`sanitize_scene_anchor` 剥两类——"空无一人/
  空置/deserted"等空镜状态短语（与有人镜头矛盾时模型会复制或清空人物）与"广角/机位/
  视角/构图/全景"等取景短语（TASK-042 场景卡是单幅广角全貌，取景词留在锚点会把后续
  每个镜头的景别锁成广角远景）；`sanitize_prop_anchor`
  剥"白底/棚拍/崭新未使用"取景词但保留道具身份细节（道具无 Subject，锚点是唯一文本载体）。
  新数据的锚点卫生由 AssetAgent 规则从源头保证（visual_anchor 只写恒定事实）。
- **空间状态结构化**：`Segment.subject_placements` 逐人给出开场位置状态，校验器强制
  （分镜生成闭环传 `character_assets` 时必填，名字须对应在场角色资产、placement 必须英文）；
  编译器把 `opening placement: …` 注入 `<Subject N>`，关键帧提示词逐人渲染
  "`角色 (锚点) is <placement>`"，与 `keyframe_description`、首镜 action 三处一致。
- **生产时实时重编译**：`cmd_produce_video` / `cmd_regenerate_segment`（无人工覆盖时）一律
  调 `compile_h3_prompt` 重编译，存储的 `h3_prompt.text` 降级为分镜时的编译缓存——
  编译器升级对存量项目即时生效，不再被缓存文本挡住。
工具：LlmProvider.chat（JSON mode）。
**镜头写作细节规则（2026-09-15 TASK-030，豆包分镜方法论内化，`STORYBOARD_CRAFT_RULES` 常量拼接进 system）**：
POV 归属（POV 必须标注所属角色、镜内不得出现该角色完整正脸、与第三视角切换保持服装/持物/光影连续）、
禁群体量词（同镜多角色逐个点名，禁 the crowd 类概括）、
方向性物体六要素（手机/镜子/书本/车门/杯把等 → 人物朝向+视线方向+物体朝向+手部接触+可见面+距离）、
情绪外化（action 禁抽象情绪词）、光影意图（情绪转折/时间变化段写明光效）。
护栏（保存前确定性校验，任一失败即重试 1 次再失败任务失败）：
- duration_sec ∈ [4,15] 整数；Σ段时长与 target_duration 偏差 >30% 附 warnings
- cutpoint 单调递增且 < duration_sec
- asset_refs 的 asset_id 必须存在
- beat_refs 不越界；action/on_screen_text 节拍 100% 被认领
- h3_prompt.text ≤7000 字符；结构完整（编译器构造即合法，见下方「H3 提示词生成规范」）

## 工具/命令

### COMMAND-001 resolve_references
名称：参考图解析（确定性代码，非 LLM）。
用途：把 segment.asset_refs 解析成 H3 请求参考图列表。
输入：segment、active 资产集及其 approved 图片。
校验：每个被引用资产 ≥1 approved 图，否则 `REF_MISSING`；解析后总数 >9 按 角色>场景>道具 截断（记录 warning）。
副作用：写 VideoJob.input_snapshot。
回滚：无（快照只读）。

### COMMAND-002 build_video_request
名称：H3 请求组装（确定性代码）。
用途：按模式矩阵生成 V2 请求体。
规则（官方契约，见附录 A）：
| 资产参考图 | continuity | 模式 | content 组装 |
|---|---|---|---|
| ≥1 | 开 | r2va | text(prompt) + reference_image[尾帧] + reference_image[资产图…] |
| ≥1 | 关 | r2va | text + reference_image[资产图…] |
| 0 | 开 | i2va | text + image_url{role:first_frame}=尾帧 |
| 0 | 关 | t2va | text（ratio 必填非 adaptive） |
副作用：无（纯函数）。
回滚：无。

## 关键帧阶段（TASK-031，2026-09-15：豆包 doubao-creative-drama 流程结构层落地）

**语义**：分镜确认后、视频前，为每个 Segment 生成一张「开场锚点关键帧」（默认每段 1 张）。
段有已批准关键帧 → 产视频时它占 `<Picture 1>` 开场参考（`continuity_prev` 清空、尾帧接力让位、
DEPENDENCY_BLOCKED 检查跳过）；无 → 回退现有尾帧接力。非连续段（如首段）补关键帧时，
提示词以 `compile_h3_prompt(opening_frame=True)` 重编译出 Picture 1 槽位，避免编号错位
（TASK-018 同源问题）；重编译后守住 7000 字符契约。锚定句/retention 措辞已中性化
（"the exact opening frame of this video"），对尾帧/关键帧两来源通用。

- **关键帧生图提示词**：`compile_keyframe_prompt` 确定性编译（对齐 V3 哲学，不经 LLM）=
  风格行 + `Segment.keyframe_description`（分镜 Agent 产出；空则回退首镜 action/description，
  并经 `_strip_dynamic_noise` 剥台词块与口型句）+ 画面内角色的逐人身份锁。
  TASK-032 起角色锁并入开场位置（`角色 (锚点) is <placement>`，来自 `subject_placements`）
  并**锁死画面人数**（"the frame contains exactly N people, each appearing exactly once"）；
  TASK-033 起只列 `in_frame=true` 的角色，本段在场但画面外的角色改写成正向状态句
  （"X stays outside the frame, and the rest of the visible space is empty of people"），
  全员画面外时直接声明画面无人。场景锚点经空镜消毒后进 setting 行，并声明
  "以 [imgN] 为准（该参考图是同一空间的广角全貌：地标位置、朝向与相互距离以它为准；
  全员画面外时直接声明画面无人。场景锚点经空镜消毒后进 setting 行。**参考图指称必须用
  `Picture N`**（TASK-045：`TextEncodeQwenImageEditPlus` 按槽位把参考图编码为
  "Picture 1/2/3" 再拼接正文，`[imgN]` 占位符原生不被解析、等于噪声）；每张参考图一句
  "Picture N is X's character reference card / a wide-angle overview of the same location"；
  人数用**正向声明** `Exactly N people in the frame, no other people`（该模型负向通道
  基本无效，issue #120 实测）；身份锁定一句话
  "Keep every character's face, hairstyle and clothing exactly the same as in their
  Picture reference."；**元指令一律不进图像提示词**（"写清…""方向性物体六要素""景别按
  文字来"是对分镜作者说的话，扩散模型只当噪声）。负向词 `KEYFRAME_NEGATIVE`
  仍走工作流负向通道，但不作为内容控制的主要手段（cfg>1 时才有微弱作用）。
- **图生图参考卡裁剪（TASK-033/045）**：`_segment_reference_cards` 只传 `in_frame=true`
  角色的身份卡 + 场景卡，**道具卡不传**。原因（实测）：图生图模型"给谁的脸就把谁摆进
  画面"，把不在构图里的角色卡送进去会在画面里补出第二个人、并让两张卡的特征互相串
  （坐着乘客挂上了司机的道具）。
  槽位分配（Edit 工作流共 3 槽）：在场角色 ≤2 → 角色卡(≤2)+场景卡；**在场角色 ≥3 →
  3 槽全给角色卡、场景卡让位**（TASK-045 实测：第三人没有卡会被画成"参考里某个人的
  复制品"——逝去的童年 S02G04 小伙伴乙没卡 → 直接消失，右侧出现第二个小伙伴甲）。
  参考不足 3 张时补槽**复制最后一张**（通常是场景卡）；复制人物卡会把人"复印"进画面
  （S02G02 双胞胎实测）。
- **图生图参考通道（可选，2026-09-16）**：配置 `RUNNINGHUB_WORKFLOW_IMAGE_EDIT`
  （Qwen-Image-Edit-2509 参考版，`workerflow/QWEN图生图（Edit参考版单段）_api.json`）
  后，关键帧任务自动携带在场资产卡已批准主图（角色优先、其次场景，≤3 张，
  `_segment_reference_cards`），适配器上传参考图填节点 10/11/12 并切 Edit 工作流
  （正向/负向写节点 6/7 的 `prompt` 字段；不足 3 张复用第一张填充）——
  关键帧 = 卡的脸 + 新构图，根治纯文生图关键帧的换脸/证据冲突问题。
  留空 = 关键帧走文生图工作流，行为不变。详见 workerflow 使用说明。
- **边界**：QWEN 工作流为纯文生图（无图输入），关键帧一致性靠锚点文字锁；
  人物身份一致性在视频阶段仍由资产卡参考图承担，关键帧负责开场构图/状态/风格锚定
  与尾帧模糊痛点。
- **确认门**：`approve_keyframes` 默认要求逐段 ≥1 已批准关键帧；显式
  `allow_tail_fallback=true` 才允许缺帧段回退尾帧接力（不静默降级，AI_RULES 红线 3）。
- **命令**：generate_keyframes（逐段入队 frame_gen）/ generate_frame_image（单段重抽，
  extra_prompt/seed）/ upload_frame_image / delete_frame_image / approve_frame_image /
  approve_keyframes。全部段各有 ready/uploaded 图 → `keyframes_generated` 推进 FRAME_READY。
- **失效回退**：重新生成分镜（FRAME_READY/FRAME_APPROVED/VIDEO_READY/COMPOSED 发起）
  回退 STORYBOARD_READY，关键帧门需重新通过；旧关键帧版本保留可回批。
  FRAME_DRAFTING 为忙碌态不放开分镜重生成（frame_gen 在途）。

## 结构化输出

- 所有 LLM 输出走 JSON mode + Pydantic 模型校验；校验失败带错误重试 1 次
- LLM 温度：剧本 0.8、资产 0.6、分镜 0.4（结构优先）
- 每次调用记录 ProviderCall（tokens 用量）

## 对话流程

平台无对话式交互；"对话"发生在提示词模板内。用户反馈回路 = 编辑 draft + 重新生成。

## H3 提示词生成规范（对齐官方 dialect：MiniMax-AI/MiniMax-H3 `skills/h3-prompt-writing`；本地权威内化 short-drama-video-prompts/references/minimax-h3*.md）

**模式矩阵**（由该段绑定的输入参考图决定，不是由"想不想多模态"决定）：

| 该段绑定的图 | H3 模式 | 正文结构 |
|---|---|---|
| 无图（且创作者明确选文生视频） | base 文生 | 三段 |
| 一张「起始帧」 | first_frame | 三段 + 逐字对齐句 |
| 「起始帧」+「结束帧」 | first_last_frame | 三段 + 逐字对齐句 |
| 其他任何组合（起始帧与人物/地点/道具图并存，或仅资产图，或含连续性尾帧） | reference（full-reference） | **六段** |

H3 首帧/尾帧输入与参考输入**互斥**：「起始帧 + 角色板 + 场景板」不能拆成 first_frame + reference_image，整组统一走 reference，起始帧也以 reference_image 送入。连续段：上一段实际视频作 `reference_video`（`<Video 1>`），其尾帧作 `reference_image`（`<Picture 1>`），不标 first_frame。

**三段结构**（base/首帧/首尾帧；字段恒英文、按序、顶格）：
1. `integrated_multimodal_description:` 每镜头一行 `[Shot k]`；`[Shot 1]` 无时间戳；首帧模式从输入帧可见姿态开始
2. `overall_soundscape:` 1-4 句环境声/动作声/非语言人声；不复述台词；是动作指令
3. `non_diegetic_music:` 1-3 句配器与速度；没有写 `N/A`，不留空

**六段结构**（reference；一条提示词整体提交）：
1. `subject_definitions:` 每个在场主体一句 `<Subject N> is ... in <Picture M>, with ...`；有对白时绑定说话人 ID（`<Subject 1> is Lu Zheng (S1), ...`）；`<Picture 1>` 为开场构图说明
2. `summary:` 一两句概括，以方括号任务前缀开头（`[reference generation]`）
3. `retention_analysis:` 逐素材写保留强度与出现镜次——视觉用 `fully_preserved / partially_preserved / attribute_transfer / weak_reference`，音频用 `fully_copy / partially_copy / reference / weak_reference`；「起始帧」只锚构图姿态（fully_preserved），「身份」只锚脸型体态，各管一摊
4. `detailed_description:` 六段主体，官方口径 350-500 英文词：开头 1-2 句英文定风格（写在 `[Shot 1]` 之前），`[Shot 1]` 无时间戳开场，`[Shot N] At MM:SS.mmm` 切点严格递增、落在时长内、与分镜切点一致；每个镜头块必须把本镜时间窗（本镜切点→下一镜切点/段尾）的动作写成显式渐进时间线（开始状态→中途事件→结束状态），并含构图与人物位置、环境光、运镜（类型+幅度+速度）、镜头内音效；禁止剧情梗概式缩写（2026-09-13 对齐官方 h3-prompt-writing ref-en）
5. `overall_soundscape:` 同三段
6. `non_diegetic_music:` 同三段

**时间线记法**：`[Shot N] At MM:SS.mmm`；无时间戳的 `[Shot k]` 只是行文回指，不构成切点；改了分镜秒数忘改提示词，validate 当场拦。**动作时间线纪律（官方 5.2）**：镜头窗口由 At 切点界定，每镜内部的动作必须按时间写成渐进过程（开场状态→发展→落点），不是单帧快照；风格定调句在 `[Shot 1]` 之前，不占镜头。

**参考图编号硬校验（2026-09-13 修订，变脸问题根因修复）**：正文 `<Picture N>` 编号集合必须恰为 `{1..实际参考图数}`（= asset_refs 数 + 连续段尾帧 1）。连续段 `<Picture 1>` 固定为前段尾帧（0.00s 开场画面），资产图从 `<Picture 2>` 起按 asset_refs 顺序编号；非连续段从 `<Picture 1>` 起。该规则由 validate_segment 确定性校验（分镜 Agent 生成重试与 edit_segment 同闸），此前实现漏了这条校验、Agent 又不知道尾帧占位约定，导致连续段整体错位一号——人物绑错参考图，直接变脸/场景漂移。生产解析时参考图严格保持 asset_refs 顺序；>9 保序截断不重排；每资产取一张确定性主图（角色「主设定」、场景「空镜」优先，不取「最新」）。

**台词与说话人（四条硬线，实测缺一即「A 的台词从 B 嘴里出来」）**：
1. 全片一份说话人映射 `说话人 ID：(S1)=名字；…`（导出件文件头，与视觉设定条目名一致），正文用到的 ID 都在映射里
2. 每句台词 `<d>[语言] 逐字台词</d>`（一个标点不动）写在画面段时间线上，同一分句由说话人 ID 引出，且 ID 挂在人物指称上（名字 / `<Subject N>`），不挂在服装道具短语上
3. 说话瞬间给口型证据：正对镜头 `his lips move as he says`；背对 `with his back to the camera`；画外音 `says in an off-screen voiceover` + 闭唇说明；画面里的听者写闭唇静默
4. 六段模式下每个在场人物都有自己的 `<Subject N>` 并带 ID——说话人 ID 只有绑到参考图定义出的脸上，口型与声线才跟人对齐

**标签纪律**：`<Picture N>/<Video N>/<Audio N>` 按同类素材顺序编号；正文引用的每个标签必须能在 `subject_definitions` 找到定义；每张图只管一件事（起始帧→构图、身份→长相、地理→空间、造型状态→服装），不越界。

**语言分工**：结构字段与镜头标记恒为英文（官方口径）；`promptLang=zh` 只切正文语言（备选，实测不稳回英文）；对白、歌词、画内可见文字保留原语言；画内文字用英文双引号原样引用；禁角色名入英文正文（用通用身份描述）。

**其他约定**：运镜用官方词表（static shot/push in/tracking shot…20 种，可加幅度速度）；遵守「常见动作原则」（不写精确物理交互与微表情）；人物此刻位置状态必须与参考图一致——图文对不上，模型听图的。

**数量与时长上限**：一次生成 ≤1 首帧 + ≤1 尾帧 + ≤9 参考图 + ≤3 参考视频 + ≤3 参考音频；时长 4-15s 整数（H3-Max 5-15s）；超限在分镜阶段按重要性取舍并写明放弃项，不在正文里假装仍然生效。

## 人类审批点

- 剧本确认、资产批准、分镜确认、关键帧确认（四大门）；资产图与关键帧图逐图批准
- 视频生成前可再选 `produce_video first_only` 试跑

## 失败处理

- LLM 输出不合规：自动重试 1 次 → 任务 failed 附校验错误
- 敏感内容（H3 422/1026）：任务 failed，错误文案提示"改写提示词/台词后重试"
- 余额不足（402/1008）：任务 failed，全局提示充值
- 限流（429）：指数退避自动重试（≤3 次），超限 failed

## 评估

- 准确性：分镜/剧本 schema 校验通过率（目标 ≥95%，重试后 100% 落地）
- 安全性：无密钥泄漏；提示词不含真实姓名（en 模式）
- 工具正确性：H3 请求组装 100% 契约合规（单测覆盖模式矩阵全分支）
- 回归测试：参考解析、模式矩阵、提示词结构校验为必测项

## 附录 A（备选，未采用）：MiniMax V2 视频生成官方直连契约（2026-09-12 核实，官方文档）

> 2026-09-12 用户拍板：视频/图片改走 RunningHub 工作流 API（附录 B，现行）；本附录保留作为未来直连官方 API 的备选参考。H3 提示词方法论不受通道影响，继续有效。

- `POST https://api.minimax.cn/v2/video_generation`，Bearer 鉴权；`GET /v2/query/video_generation/{task_id}` 轮询
- model：`MiniMax-H3`（t2v/图生视频/多模态参考，768P|2K，4~15s 整数）；`MiniMax-H3-Max`（极速，不支持多模态参考与 2K，480P|768P，5~15s）
- content[]：`{type: text|image_url|video_url|audio_url, role?}`；text 必填且 ≤7000 字符；role：`first_frame|last_frame|reference_image|reference_video|reference_audio`
- **互斥**：`first_frame/last_frame` 与 `reference_*` 不可同时出现
- 数量限制：参考图 ≤9、参考视频 ≤3、参考音频 ≤3；图片单张 ≤30MB、[256,5760]px、宽高比 [0.4,2.5]
- 请求体 ≤64MB；URL 支持 公网 URL / `mm_file://{file_id}` / Base64 data URI（+33% 体积）
- resolution 必填（480P/768P/2K 按模型）；duration 必填（整数秒）；ratio 默认 adaptive（t2va 必填非 adaptive；i2va 恒 adaptive；r2va 可显式指定）
- 创建响应 `{task_id}`；查询 task.status：queued/running/succeeded/failed/cancelled；成功返回 content.url（临时，须立即下载）与 usage{total_seconds,…}
- 错误：400/2013 参数、401/1004 鉴权、402/1008 余额、422/1026 敏感内容、429/1002 限流、500/1000 服务端
- callback_url 存在但平台不用（轮询）

## 附录 B：RunningHub 工作流契约（现行通道，2026-09-12 用户拍板 + 官方文档核实）

- Base：`https://www.runninghub.cn`；鉴权：body 带 `apiKey`（请求示例同时带 `Host` 头与 `Authorization: Bearer <apiKey>`）
- 发起任务：`POST /task/openapi/create`，body `{apiKey, workflowId, nodeInfoList: [{nodeId, fieldName, fieldValue}]}` → `{code: 0, msg, data: {taskId, taskStatus: QUEUED|RUNNING|FAILED}, promptTips}`；code=433 表示提示词审核未过（msg 为原因文案），不创建任务
- 轮询状态：`POST /task/openapi/status`，body `{apiKey, taskId}` → `data` 为字符串：`QUEUED | RUNNING | FAILED | SUCCESS`
- 拉取产物：`POST /task/openapi/outputs`，body 同上 → 成功 `data: [{fileUrl, fileType, nodeId, taskCostTime, consumeCoins, …}]`（**fileUrl 临时，必须立即下载落地**）；`804`=运行中 / `813`=排队中 / `805`=失败（`data.failedReason` 含 nodeType/nodeName/exceptionMessage/traceback）
- 取消任务：`POST /task/openapi/cancel`
- 文件上传：`POST /openapi/v2/media/upload/binary`（multipart 字段 `file`；**鉴权只认 `Authorization: Bearer <apiKey>` 头——实测 form/query 传 apiKey 均报 "apiKey is required"，官方文档示例的 form apiKey 字段是误导**）→ `{code:0, data: {fileName, download_url, size, type}}`；返回的 `fileName`（形如 `openapi/xxx.png`）填入 ComfyUI 节点输入以引用该图
- 工作流（workflowId 存 .env，可换）：
  - 视频 `2093160296864116737` =「MiniMax H3 导演台 ref2va」（模板存 `workerflow/MiniMaxH3Director_ref2va_2093160296864116737_api.json`）：核心节点 **`12`**（MiniMaxH3Director），输入 `global_prompt` + `timeline_data`（JSON：`segments[]{prompt, length, durationSec, continuityFromPrev}`、`global.refs[]{imageFile}`、`output{aspectRatio,width,height,audioMode}`、`totalFrames`、`frameRate:24`），另有 seed/width/height/ref_max_size/total_frames/steps/cfg/sampler 直填字段；输出节点 **`7`**（SaveVideo，`20` 为预览通道）出 mp4，**自带音轨**。该版本默认 global_prompt 即六段式口径
  - 图片 `2098715929763995649` =「QWEN 文生图（4步）极速」：节点 `6`（CLIPTextEncode）`text`=提示词；节点 `8`（EmptyLatentImage）`width/height`=画幅；节点 `5`（KSampler）`seed`；输出节点 `18`（SaveImage）出 png
- **实测记录（2026-09-13 端到端通过）**：10s 段（durationSec=10 → totalFrames=243 → 实际成片 10.125s，**真实时长=totalFrames/帧率**）；6 段式英文提示词 + 单张上传参考图（refs[0]）驱动成功；成片 1920×1088（节点 38 refine 精修 2MP）、有音轨（output.audioMode="generate"）；消耗 51 RH 币、任务耗时 252s；生图参考图 21 币/104s
- **平台对接策略（保持领域模型不变）**：一次 RunningHub 任务只承载一个 Segment（timeline 单段、`continuityFromPrev=false`），段间连续性沿用平台既有语义——上段尾帧经上传 API 变成 `refs[0]` 并在提示词中对齐 0.00s；不使用工作流内置的跨段连续性（那会把多段绑进一次任务，破坏段级重生成）
- 模式 ↔ 工作流映射：reference 六段 ↔ 导演台 `task_type=r2v`（refs 填上传 fileName）；base 三段 ↔ `task_type=t2v`；首帧/首尾帧三段待平台建模「起始帧」槽位后接 `i2v`（当前 Segment 无该槽位）
- H3 契约红线中仍适用本通道的部分：参考图 ≤9、单图 [256,5760]px、宽高比 [0.4,2.5]、提示词方法论四段结构；`duration 4-15` 映射为 timeline 的 `durationSec` 与 `length=durationSec*帧率` 帧数
- 节点 ID 与 timeline_data 字段行为以 TASK-001 spike 实测为准，出入回写本附录
