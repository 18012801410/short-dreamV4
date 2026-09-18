# AI 软件工厂（AI Software Factory）v1.2 中文版

一套与 AI 编码智能体协作构建软件的可复用操作系统。

## 理念

不要让 AI"把整个项目做出来"。

采用：

产品 → 头脑风暴 → 架构 → 领域 → 契约 → 计划 → 小任务 → 实现 → 验证 → 评审 → 状态

## 项目章程

每个有分量的项目都应具备：

- PROJECT_BRIEF.md（项目简报）
- ARCHITECTURE.md（架构）
- DOMAIN_MODEL.md（领域模型）
- AI_RULES.md（AI 规则）
- DEVELOPMENT_PLAN.md（开发计划）
- PROJECT_STATE.md（项目状态）

## 智能体循环

阅读 → 检查 → 计划 → 实现 → 验证 → 报告 → 更新状态

## 它为什么存在

目的在于：让人类始终掌控产品方向与架构，同时用 AI 换来高速实现。

## 典型的启动指令

阅读 SKILL.md 和所有项目章程文档。先不要写代码。总结当前架构、项目状态、待定问题，以及最小的下一个任务。

## v1.1 — 规格生成

v1.1 增加了正式的规格说明包：

- REQUIREMENTS.md
- TECH_SPEC.md
- API_SPEC.md
- UI_SPEC.md
- AI_SPEC.md

在大量实现之前使用 `workflows/SPEC_GENERATION.md`。

预期生命周期为：

想法 → 规格说明包 → 一致性检查 → 开发计划 → 任务 → 编码 → 验证

## v1.2 — 头脑风暴

v1.2 增加了 `workflows/BRAINSTORM.md`（改编自 obra/superpowers 的 brainstorming 技能）：

- 对每个请求先分类（技术验证 / 有界改动 / 架构级），据此缩放流程繁简——但绝不跳过批准闸门
- 一次一个问题地向用户提问，优先给选择题
- 提出 2-3 个候选方案，附权衡与推荐
- 分节呈现设计，逐节获得确认
- 以用户批准的 PROJECT_BRIEF.md 收尾，之后才允许进入 SPEC_GENERATION

现在的完整生命周期是：

想法 → 头脑风暴 → 批准的简报 → 规格说明包 → 一致性检查 → 开发计划 → 任务 → 编码 → 验证

v1.2 还包括：

- SKILL.md 新增"文件地图"，指明每个工作流、检查清单和模板
- 流程规模说明：单文件修复只需要 TASK.md；完整规格说明包只用于新项目和新子系统
- PROJECT_STATE.md 模板新增"待定问题"一节；PROJECT_BRIEF.md 模板新增"决策"表
- UI_SPEC.md 的 Layout 一节扩成结构化布局规格：页面骨架线框、栅格与断点、间距与密度
- 新增 DESIGN_SYSTEM.md 模板：设计 Token、组件清单、主题、无障碍规则
- BRAINSTORM 新增 UI 专项提问（风格参照、设备优先、信息密度）和视觉问题的 HTML mockup 路径
- 本目录是完整中文翻译；英文原版在 `../ai-software-factory-v1.2/`
