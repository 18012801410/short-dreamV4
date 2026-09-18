# TASK-032：提示词管线根治——身份唯一性 + 空间状态结构化

日期：2026-09-16
来源：用户反馈——"生成的视频怎么那么差？每个视频经常出现重影，一个人镜头会出现两个一样的人；
人物在道具中的位置也很奇怪，比如应该是人物在开车，居然坐在了后排乘客位"。
要求：**不针对某个项目做调整，彻底解决根源**（"这个项目可能是座位，下个项目可能是其他的问题"）。

## 诊断（实证定位到行号）

### 重影（一人变多人）
1. `h3_compiler.compile_h3_prompt` 把同一角色的外貌锚点全文写了两遍：`<Picture N> is 角色,
   with <锚点>` 与 `<Subject N> is 角色 (Sx) in <Picture N>, with <同一锚点>`——执行端把
   带锚点的两处读成两个人（官方 production-prompt-grammar：部分/重复绑定 = 分裂）。
2. 锚点里带着"直视镜头"这类**定妆卡摆拍短语**，泄漏进剧情镜头（姿势属于参考卡，
   retention 已声明 pose follows this shot）。
3. `AssetAgent` 空镜策略把"空无一人"写进场景 `visual_anchor`，编译器原样嵌进**必须有人**
   的视频/关键帧提示词——图文矛盾时模型自行消解（复制人物补人数或清空场面）。
4. 只有说话人才建 `<Subject N>`，不开口的在场角色成了官方明令禁止的"没名字的那个人"
   （minimax-h3.md 反模式 #5）。
- 实证帧：`data/media/p-0a7e731af817/frames/S03G03_frm-*.png`（三个陈默并排）、
  `p-5dba0072e423/frames/S01G02_frm-*.png`（车外两个风衣男，剧本只有一人）。

### 位置错误（开车坐到副驾/后排）
1. `compile_keyframe_prompt` 的 locks 只有"外貌一致"，**没有任何空间绑定**；座位/站位全靠
   LLM 的 20-50 词自由散文，丢了就错——S02G02 关键帧阶段就互换了两人的座位与动作。
2. 视频阶段接力锚点要求"frame 0 必须与关键帧完全一致"，**关键帧错则视频被强制继承错误
   座位**；文字写 driver seat 与图冲突时模型听图的（dj-novel h3-prompt.md：图与文对不上，
   模型听图的，动作就乱）。
3. 场景参考图是驾驶位空置的空镜，进一步把"空置座位"的空间布局带给模型。
4. `cmd_produce_video` 直接使用分镜时存储的提示词文本——编译器升级对存量项目不生效。

## 修复（通用机制，不限定载具/座位）

| 层 | 文件 | 内容 |
|---|---|---|
| 领域 | `domain/entities.py` | 新增 `SubjectPlacement{name, placement}`；`Segment.subject_placements`（默认空列表，存量兼容） |
| 校验 | `domain/validation.py` | `_check_subject_placements`：分镜生成闭环（传 `character_assets`）时在场角色逐人必填、名字须对应在场角色、placement 必须英文、禁重复；`keyframe_description` 纯静态（禁 `<d>`/says/speaks/lips move）。失败进"反馈→重试"闭环 |
| 编译器 | `app/h3_compiler.py` | ①角色锚点全文只在 `<Subject N>` 出现一次，`<Picture N>` 只声明参考图角色（identity card，非"场景里还有一个人"）；②在场角色（含不开口者）全建 Subject，说话人追加 (Sx)，编号独立成序；③`sanitize_character_anchor` 剥卡片摆拍短语、`sanitize_scene_anchor` 剥空镜状态短语、`sanitize_prop_anchor` 剥白底/棚拍取景词但保留道具身份细节（存量兜底）；④`opening placement: …` 注入 Subject；⑤关键帧提示词逐人渲染 `角色 (锚点) is <placement>` + 单实例声明；⑥回退描述 `_strip_dynamic_noise` 剥台词/口型；负向词补 duplicate person/clone |
| 生产 | `app/usecases.py` | `_compile_production_prompt` 统一实时重编译（produce / regenerate 无人工覆盖时），存储文本降级为缓存；7000 字符契约保留 |
| Agent | `app/agents.py` | 分镜 schema 增 `subject_placements`；`STORYBOARD_CRAFT_RULES` 增 C6（位置状态显式化：逐人座位/站位+朝向+控制关系，机位短语不得与座位短语混写，三处一致）/C7（关键帧纯静态）；`_validate_content` 透传 `character_assets`；AssetAgent 增 anchor 卫生规则（visual_anchor 只写恒定事实，禁摆拍/空镜状态短语） |
| 状态机 | `domain/project.py` + `app/usecases.py` | 生成类动作（script/assets/storyboard/keyframes）允许从自己的 drafting 忙碌态重入——生成 Job 终态失败会把项目永久卡死（实测「超神奶爸」分镜 Job 连败 3 次后无法重派）；重复派发改由 `dispatch._guard_reentrant_generation` 以任务表判定（仍有 pending/running 任务则拒绝），关键帧这类会烧钱的阶段重入保护不降级 |

## 验证

- 新增 `server/tests/test_h3_compiler.py` 15 项：锚点唯一性（`count(锚点片段)==1`）、
  Picture 定义角色、卡片姿势词剥离、静默角色建 Subject、场景空镜短语剥离、placement 注入、
  无 placement 的存量兼容、关键帧位置绑定与单实例声明、回退描述剥台词、
  placement 必填/孤儿/CJK 校验、存量路径跳过、关键帧动态词校验、
  道具锚点保身份剥取景词。
- 新增状态机/护栏测试：四个生成动作的忙碌态重入（参数化）、忙碌态仍挡确认门、
  API 层重入护栏契约（任务未终结→409 STATE_ILLEGAL；任务失败终结→允许重入恢复）。
- 存量测试契约更新（新行为）：`test_compile_binds_speaker_to_subject_and_picture`、
  `test_compile_speaker_without_character_asset_keeps_name`、两个分镜工厂补 placements。
- **顺带修掉一个随机闪失败**：`test_app_flow.FakeLlm` 用 `sorted(ids)[0]` 随机挑资产，
  挑中场景资产时 `asset_refs` 指向场景、placement 角色对不上（旧校验只查 id 存在性，
  从未暴露）——改为只挑 character 资产。
- 后端 `pytest server/tests` 188 项全绿（连续多轮跑通）；`ruff check server` 全过。
- **存量数据干跑抽查**（6 个项目 69 段，只重编译不写库、不生成媒体）：37 段去掉重复角色锚点、
  18 段剥离"直视镜头"、25 段剥离"空无一人/空置"、22 段剥离道具"白底/崭新未使用"取景词
  （关键帧提示词侧 23 段）、61 段补全在场角色的 `<Subject N>`（原先只有说话人有）、
  0 段超 7000 字符契约。抽 S02G02 开车段对照：旧文本里陆峥完全无 Subject
  （没名字的人）、锚点写两遍、场景带"空无一人"；新文本锚点各出现一次、场景锚点只剩布局、
  道具身份细节保留而"白底/崭新未使用"剥离。
- **线上重启 + 实战验证（2026-09-16 18:33）**：重启 Worker/API 后重新派发被卡死的
  「超神奶爸」分镜任务（旧进程混用新旧代码时该任务连败 3 次，错误
  `'Segment' object has no attribute 'subject_placements'`）——**首次尝试即成功**（47s、
  3 次 deepseek 调用），产出 v2 分镜 **12 段全部带 `subject_placements`**，内容精度符合设计
  （如"standing at the left corner table, both arms raised holding the crayon drawing
  overhead, facing the hall"——站位/朝向/持物控制关系齐备）；编译产物锚点各出现一次、
  Picture 只声明参考图角色、opening placement 逐人注入、道具保留身份细节。

## 影响与后续

- 已生成的视频不变；**任何项目的下一次**关键帧/视频生成自动走新编译与硬校验——
  位置错误在便宜的关键帧阶段暴露（重抽一次关键帧），不再流到昂贵的视频阶段。
- 新分镜由校验闭环保证 `subject_placements` 完整；存量分镜无该字段时编译器跳过位置行，
  身份唯一性/消毒修复照常生效，不阻塞编辑与重出。
- 存量项目要完整拿到位置绑定，需重新生成分镜（新规则产出 placements）；此步不自动执行。
- 待用户择机重出问题段（如 S02G02 关键帧+视频）做真实效果验证。
