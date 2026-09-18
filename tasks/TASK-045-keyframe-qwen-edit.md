# TASK-045：关键帧三人症候群诊断与修复（换脸 / 换装 / 多人）

日期：2026-09-17
来源：用户反馈「生成的关键帧图片各种问题……要么画面人物变了，要么衣服变了，要么多个人，
到底是提示词有问题还是什么」，并要求联网查文档。

## 诊断方法与证据（逝去的童年 p-72c1b958c9fe，16 段关键帧全量核对）

逐段比对「提示词声明人数 vs 有参考卡人数」，并目检成图与参考卡：

| 段 | 症状 | 证据 |
|---|---|---|
| S02G04 | 人物变了 + 多个人 | 声明 3 人、只传 2 张角色卡：小伙伴乙（黄裙马尾）**没有参考卡 → 直接消失**，右侧被画成**第二个小伙伴甲**（同款红条纹衣+同发型） |
| S02G05 | 同上 | 同结构（3 人 2 卡） |
| S02G02 | 双胞胎 + 少人 | 描述写"Two girls… A third girl…"（无名人物），只有小伙伴乙一人有卡；参考槽**复制乙的卡**补满 → 画出来两个一模一样的乙、第三个人消失 |
| S01G01 / S02G03 等 13 段 | 正常 | 单人 + 角色卡 + 场景卡：脸、服装（含开衫/手表）、场景地标全部贴合 |

结论：**问题不在"关键帧"这一个环节，而是三个结构性缺陷叠加，且都被多人段放大。**

## 三个根因

1. **第三人没有参考卡**：Edit 工作流只有 3 个参考槽，旧逻辑固定"角色卡 ≤2 + 场景卡"。
   3 人同框时第三人必然无卡——图生图模型只能拿参考里的人拼凑。
2. **补槽复制第一张（人物卡）**：参考不足 3 张时，适配器用 `file_names[0]` 填满槽位。
   同一张人物卡占据两个条件槽 → 模型把这个人"复印"进画面。
3. **提示词写给了错误的读者**：我们用 `[img0]（陈默）的脸部特征…` 这类占位符绑定 +
   大量元指令（"方向性物体必须方向自洽：写清人物朝向…"、"景别按文字来"）。
   联网调研（官方文档/源码/issue）证实：
   - ComfyUI `TextEncodeQwenImageEditPlus` **不解析 `[img0]`**——它把参考图按槽位编码为
     `Picture 1/2/3` 再原样拼接正文（comfy_extras/nodes_qwen.py），`[img0]` 只是 6 个无意义字符；
   - 官方示例是**短散文 + 名字 + 方位指称**，并显式声明总人数（社区共识：正向声明有效）；
   - **负向提示词基本无效**（官方示例只给一个空格；issue #120），我们依赖的
     KEYFRAME_NEGATIVE 拦不住"多个人"；
   - 多人身份融合是 2509 的已知弱项（官方一致性承诺只写在单图章节；issue #88、
     nunchaku#618；2511 版主打"角色一致性提升"侧面承认）。
   来源清单见文末。

## 修复

| 层 | 内容 |
|---|---|
| 参考卡选择（`usecases._segment_reference_cards`） | 在场角色 ≥3 → **3 槽全给角色卡、场景卡让位**（场景一致性由视频阶段承担：H3 带场景卡 + 本帧作 Picture 1 + 尾帧接力）；≤2 → 角色卡 + 场景卡（原行为） |
| 补槽策略（`adapters/image.py`） | 不足 3 张时**复制最后一张**（通常为场景卡），不再复制第一张人物卡 |
| 分镜规则 C8（`agents.py`） | keyframe_description 里可见人物只能是 in_frame=true 的**具名角色资产**，逐人写资产名、人数与 in_frame 一致；禁止 two girls / a third boy / a crowd 等无名人物；确需路人只写远处不具名背景 |
| 校验器（`validation.py`） | `_KEYFRAME_UNNAMED_PEOPLE_RE` 命中数词/序数词/冠词+人物词、集合人群名词 → VALIDATION_FAILED 进重试反馈（真实事故原句 "Two girls…"、"A third girl…"、"A chubby boy…" 全部命中；"two bicycles"、"her two hands" 不误伤） |
| **提示词编译器重写**（`h3_compiler.compile_keyframe_prompt`） | ①参考图指称全部改为 **Picture N**（与节点自身标签一致）；②人数**正向声明** `Exactly N people in the frame, no other people`；③身份锁定一句话 "Keep every character's face, hairstyle and clothing exactly the same as in their Picture reference."；④**删除全部元指令**（方向性物体六要素、"以…为准（…）"、"角色参考图只用于身份…"、"光影方向…"）——扩散模型不执行指令，只当噪声；⑤提示词从约 1300 字符压到约 700 |
| 文档 | AI_SPEC 关键帧阶段与参考卡裁剪小节同步改写 |

## 验证

- 单测：参考卡 3 人让位场景卡、补槽用最后一张、无名人物校验（命中+不误伤）、
  新提示词格式（Picture 指称/人数声明/锁定句/无 [imgX]/无元指令/顺序/结尾）。
  **后端 220 项 / 前端 16 项全绿**，ruff + tsc 通过。
- 编译实测：同一开车段新提示词 717 字符（旧 1298），人数声明、Picture 指称、锁定句齐备。

## 已知边界与后续可选

- `cfg=4`、20 步 euler 的稳定版工作流下负向词有微弱作用，但**不要依赖它做内容控制**；
  若未来切 Lightning 4 步（cfg=1），负向词 100% 无效。
- 模型本身的多图人物一致性有限（官方沉默、社区证实偏弱）。若修复后多人段仍不稳，
  升级路径：①Qwen-Image-Edit-2511（官方宣称角色一致性显著提升）；②两段法
  （先出构图场景，再用输出图+人物卡做一次修脸小编辑，issue #88 高赞方案）；
  ③提示词改写前置节点（官方 demo 的隐藏技巧）。
- 存量项目里旧提示词生成的关键帧不会自动变好：单人段可只重出不满意的；
  多人段（声明人数 > 有卡人数）建议在分镜页先把 keyframe_description 改成逐人具名
  （keyframe_description 在编辑白名单里），再重出关键帧。

## 新旧对比实测（2026-09-17，逝去的童年 16 段全量重出 v2）

前置：S02G02/04/05 的 keyframe_description 先改为逐人具名（走 `edit_segment`，触发
FR-016 分镜失效 → 重新 approve_storyboard → generate_keyframes 重出全部 16 段 v2）。

| 段 | v1（旧提示词） | v2（新提示词） |
|---|---|---|
| S02G05（3 人全景，收尾定格） | 只画 2 人；乙长着马尾却**穿着甲的红条纹衣**（特征互串实锤） | **3 人齐且各自正确**：陈末白T帆船+蓝短裤+红绳、甲红条纹衣平头、乙黄裙马尾；自行车灰墙到位；无克隆 |
| S02G02（跳皮筋） | 两个一模一样的乙（双胞胎）+ 第三个消失 | **干净的单人画面**：乙跳皮筋，服装/发饰与卡一致，无双胞胎（描述改为乙单独跳、皮筋拉出画外） |
| S02G04（三人翻花绳） | 乙消失，右侧是第二个甲 | 乙正确出现（黄衣红绳马尾），克隆消失；**但 3 人只画了 2 个，甲缺席**——多人计数仍不保证 |
| S01G01 等 13 段单人 | 本来就好 | 无退化（陈末门口开衫/手表/场景地标齐） |

**结论**：①克隆人/换装/人被替换三类问题在 v2 全部消失；②无名单人段零退化；
③**"声明的 N 个人是否全部出现"仍是该模型的软肋**（S02G04 3 人只出 2 个，即便
提示词显式写 Exactly 3 people）——多人段的产出需逐张目检，不行就重摇一次。

**花费**：v2 批次 16 张 Edit 关键帧，已记录 14 次 = 599 币（约 43 币/张），全程约 685 币。

## 来源（调研摘要）

- ComfyUI `TextEncodeQwenImageEditPlus` 源码（Picture 1/2/3 拼接、384² 视觉 token、
  image1 可选接 latent）：github.com/comfyanonymous/ComfyUI/blob/master/comfy_extras/nodes_qwen.py
- 官方模型卡/文档（多图示例散文式、一致性承诺仅单图章节、ControlNet 才是官方改构图工具）：
  huggingface.co/Qwen/Qwen-Image-Edit-2509 ；github.com/QwenLM/Qwen-Image/blob/main/Qwen-Image-Edit-2509.md
- issue #88（改衣服背景连脸都漂）、#120（负向词无效）、#169（image 1 指称不可靠）、
  #100（多图组合非专项训练）：github.com/QwenLM/Qwen-Image/issues
- 阿里云百炼 API 文档（顺序有语义、仅中英、多图最佳 1-3 张）：
  help.aliyun.com/zh/model-studio/qwen-image-edit-api
- 社区实操（正向人数声明、名字指称、camera zoom 句式、拆两段编辑、prompt rewriter）：
  reddit.com/r/StableDiffusion 与 r/comfyui 多帖、
  github.com/lihaoyun6/ComfyUI-QwenPromptRewriter、learn.thinkdiffusion.com
