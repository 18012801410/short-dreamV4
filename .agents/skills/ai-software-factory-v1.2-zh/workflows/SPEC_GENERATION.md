# 规格生成工作流（Specification Generation Workflow）

本工作流把一个自然语言的产品想法转换成可进入实现的规格说明包。

## 第 1 步 — 产品探索
除非人类已经批准过项目简报，先运行 `workflows/BRAINSTORM.md`。然后明确：
- 目标用户
- 问题
- 期望结果
- 核心流程
- MVP
- 约束
- 显式非目标

## 第 2 步 — 需求
生成：
- PROJECT_BRIEF.md
- REQUIREMENTS.md

当模糊点会实质改变范围时，不要自行发明业务需求。把它们标记为待定问题（Open Questions）。

## 第 3 步 — 架构
生成：
- ARCHITECTURE.md
- DOMAIN_MODEL.md
- TECH_SPEC.md

识别边界、实体、状态机、外部服务与不变量。

## 第 4 步 — 契约
生成：
- API_SPEC.md
- AI_SPEC.md

在实现之前定义结构化接口。

## 第 5 步 — UI
生成：
- UI_SPEC.md
- DESIGN_SYSTEM.md（UI 项目）

描述页面、布局骨架、交互与重要的 UI 状态。在实现之前定义设计 Token 与组件清单。

## 第 6 步 — 开发计划
更新：
- DEVELOPMENT_PLAN.md
- PROJECT_STATE.md

把工作拆成按依赖排序的阶段。

## 第 7 步 — 任务生成
生成最小的、可进入实现的 TASK 文件。

## 第 8 步 — 一致性检查
验证：
- 需求都被架构承载
- 领域实体支撑需求
- API 契约与领域行为一致
- UI 动作都映射到受支持的用例
- AI 命令都映射到合法的领域操作
- 开发任务覆盖已批准的范围

如果存在矛盾，停下并在编码之前报告。
