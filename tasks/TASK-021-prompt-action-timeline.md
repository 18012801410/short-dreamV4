# TASK-021 —— 视频提示词动作时间线（对齐官方 h3-prompt-writing）

日期：2026-09-13 ｜ 模式：提示词契约修订（AI_SPEC 红线区，用户提出） ｜ 状态：已完成

## 用户需求
「分镜的动作时间线，我要的是在视频提示词中增加时间线」，参照官方
https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing

## 官方口径（references/ref-en.txt §5.2 + 开头 Detail 要求）
- `detailed_description` 350-500 英文词（对白密集型优先保证台词时间线完整）。
- 每镜必须写全：当前构图、主体外观与位置、环境与光、**动作与状态变化**、
  运镜、当前音效、参考内容实际生效点；「避免缩写成剧情梗概」。
- 风格定调：[Shot 1] 之前 1-2 句英文（full-reference 模式与 T2VA 的差异点）。
- 时间窗由 `[Shot N] At MM:SS.mmm` 切点界定；镜内动作按时间写渐进过程。

## 差距（真实项目实测）
「超级神豪」active 分镜 S01G01（6s/2 镜）的 detailed_description 仅 **115 词**，
两镜都是单句状态快照：无动作渐进、无运镜幅度、无镜头内音效、风格句混在 Shot 1 里。

## 修复
`H3_RULES_DIGEST` 规则 3 重写（分镜 Agent 生成与 edit_segment 同闸）：
- detailed_description 350-500 词；开头 1-2 句风格定调在 [Shot 1] 之前；
- 每镜把时间窗（本镜切点→下一镜切点/段尾）写成显式渐进时间线：
  开始状态 → 中途事件 → 结束状态；
- 必含构图/人物位置、环境光、运镜（类型+幅度+速度）、镜头内音效；
- 禁止剧情梗概/单句快照；base 模式同规则作用于 integrated_multimodal_description。

不做 350 词硬校验（避免 brittle 失败），密度靠提示词约束 + 人工在分镜 Tab 审。
AI_SPEC「六段结构 4」与「时间线记法」节同步。

## 与 TASK-019 的关系
UI 动作时间线（比例时间条 + 逐镜起止秒）是给人看的时间轴；本任务是给 H3 看的
提示词内时间线。两者同源（shots 切点），互补不冲突。

## 验证
117 passed + ruff clean；存量分镜不变，重新生成分镜即按新密度产出。
