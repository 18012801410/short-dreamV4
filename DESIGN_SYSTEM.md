# 设计系统（Design System）—— short-dreamV4

## 设计原则
- 生产工具：高信息密度、低装饰、状态一眼可辨
- 深色影棚主题（媒体预览为主的工作台，深底利于看片）
- 状态色语义全局唯一：进行中/成功/失败/等待 不得混用

## 组件库选型
| 决策 | 选择 | 原因 |
|---|---|---|
| 样式 | Tailwind CSS + 少量自定义组件 | 轻量、免重依赖；组件量不大 |
| 基础组件 | 自建（Button/Card/Dialog/Table/Tabs/Badge/Progress/Toast） | 避免引入大库；保持深色主题一致性 |

## 设计 Token（Tailwind theme extend）

### 颜色
| Token | 值 | 用途 |
|---|---|---|
| color/bg | #0f1115 | 页面底 |
| color/surface | #171a21 | 卡片/面板 |
| color/surface-2 | #1f2430 | 悬浮/次面板 |
| color/border | #2a3040 | 描边 |
| color/text | #e6e9ef | 主文本 |
| color/text-dim | #9aa3b2 | 次文本 |
| color/primary | #5b8cff | 主操作（确认/生成） |
| color/primary-hover | #7aa2ff | 悬浮 |
| color/success | #3fb96f | 成功/已批准 |
| color/warning | #e6a23c | 进行中/警示 |
| color/danger | #e5534b | 失败/阻断 |
| color/stale | #8b7355 | 失效（stale）工件 |

### 字体
| Token | 值 | 用途 |
|---|---|---|
| font/sans | system-ui, "Segoe UI", "Microsoft YaHei" | 界面 |
| font/mono | "Cascadia Code", Consolas | 提示词/日志/JSON |

size-scale：12/13/14/16/18/22/28；weight-scale：400/500/600。

### 间距
基准单位：8px；刻度：4/8/12/16/24/32。

### 圆角/描边/阴影
圆角：8px（卡片）/6px（控件）/999（徽标）；描边 1px color/border；阴影仅 Dialog 与抽屉（0 8px 24px rgb(0 0 0/.4)）。

## 主题
- 仅暗色（自用工具，不做亮色切换）
- 可定制点：无（保持克制）

## 组件清单

### 组件：Button
变体：primary / secondary / ghost / danger。状态：默认/悬浮/禁用（40% 透明+原因 tooltip）/加载（内联 spinner）。
Token：color/primary、surface、text。备注：确认门主按钮用 primary，宽度自适应文案。

### 组件：Badge（状态徽标）
变体：queued（dim）/ running（warning，呼吸动画）/ succeeded（success）/ failed（danger）/ stale（stale）/ cancelled（dim）。
备注：流水线导航与任务中心复用同一语义。

### 组件：Progress
变体：线性（任务）/分段（项目流水线：5 段点亮）。状态：进行中流动条、失败红色停在百分比。

### 组件：Dialog / Drawer / Toast / Tooltip
标准深色面板；Toast 顶部居中，错误类常驻可手动关。

## 无障碍规则
- 对比度：text/bg ≥ 7:1；text-dim ≥ 4.5:1
- 焦点：2px color/primary 外框
- 键盘：Tab 顺序=视觉顺序；Dialog 支持 Esc

## 推荐 / 禁止
- 推荐：状态用图标+文字双编码；数字等宽对齐；媒体区黑底
- 禁止：纯颜色表达状态；装饰性动画（running 呼吸除外）；亮色块大面积使用
