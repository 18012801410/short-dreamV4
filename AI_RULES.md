# AI 规则（AI Rules）—— short-dreamV4

## 编码之前

1. 阅读 PROJECT_STATE.md 了解当前阶段与任务。
2. 阅读与本任务相关的规格：ARCHITECTURE.md、DOMAIN_MODEL.md、API_SPEC.md、AI_SPEC.md、TECH_SPEC.md。
3. 检查现有代码模式（仓储/适配器/命令处理器的既有写法）。
4. 陈述实现计划（改哪些模块、新增什么）。
5. 识别风险（是否触碰领域层/状态机/契约）。

## 范围

只修改任务所需文件。
禁止：
- 悄悄重新设计架构或状态机
- 未经批准更换/新增依赖库
- 重写无关模块、顺手清理无关代码
- 为绕过报错削弱类型或删除测试
- 在 UI 层写业务规则、在领域层 import 框架
- 绕过 Provider 适配器直接调用外部 API
- 把 API Key 或完整 Authorization 写进日志
- 让 succeeded 的任务/active 的工件可变（重试=新 Job，重生成=新 draft）

## 架构冲突

需求与规格冲突时，停下并报告：冲突、原因、受影响模块、备选方案、建议、迁移成本。等待批准后再动工。

## 验证

每个任务完成前运行：
- 后端：`ruff check .`、`mypy`（若配置）、`pytest server/tests`
- 前端：`npm run build`（含 tsc）、`npx vitest run`
- 手动：任务指定的验证路径（如用 FakeProvider 跑通一个项目全流程）

## 报告

报告：改动内容、涉及文件、验证结果（原样贴关键输出）、遗留问题、架构影响（有/无）。

## 完成的定义

验收标准逐条通过 + 验证命令全绿 + 无关回归为零 + PROJECT_STATE.md 已更新，任务才算完成。

## 本项目特有红线

1. H3 请求组装必须符合 AI_SPEC 附录 A 契约（互斥规则、≤9 参考图、≤7000 字符 prompt、duration 4-15 整数）。
2. Provider 临时 URL 素材必须立即下载落地本地，DB 只存相对路径。
3. 缺已批准参考图必须阻断（REF_MISSING），不允许静默降级。
4. 金额敏感：任何"重试"不得对已成功收费调用重复发起。
