# TASK-044：进程旧代码静默烧钱——代码新鲜度护栏

日期：2026-09-16
来源：用户反馈「不是改成全身照了吗，为什么生成的还是半身照呢」「怎么老有这种问题，浪费我钱」。

## 事故复盘（真实花了钱）

时间线（本地时间）：

| 时刻 | 事件 |
|---|---|
| 19:54 | 用户启动 3 个 Worker（`python -m server.worker --no-recover`） |
| 22:36 | 用户启动 API（uvicorn，无 `--reload`） |
| 22:39–22:52 | 我改成单幅广角空镜（TASK-042）、正面全身定妆照（TASK-043），改的都是 `server/` 下的源码 |
| 22:57–23:05 | 用户在页面点「抽取资产」→ **跑在旧 Worker 里的旧 AssetAgent** 按旧口径写提示词：4 张「半身定妆像」+ 2 张「三面板空镜」；随后 12 张图照常生成、照常扣币 |

**直接损失（provider_calls 实录）**：本项目本轮 12 张图共 **107 币**，其中与本次改动相关的错误口径占 **89 币**
——4 张半身卡 63 币 + 2 张三面板空镜卡 26 币；6 张道具卡 18 币（道具口径未变，属正常花费）。

**为什么没被发现**：提示词是 Worker 进程**现场撰写**的，写进 DB 后页面显示一切正常
（卡片、view_label、生成进度都没有异常），只有肉眼比对"人是不是全身"才看得出。等发现时钱已经花掉。

**根因**：Python 进程启动时把模块加载进内存，之后改代码**不生效**（uvicorn/worker 都没开 `--reload`）。
这不是本项目独有的坑——任何"长驻进程 + 代码里写提示词/规则"的组合都会这样。

## 修复

| 层 | 内容 |
|---|---|
| 新模块 `server/infra/buildinfo.py` | 确定性判定"进程是否在跑旧代码"：源码（`server/`、`scripts/` 下 `*.py`）最后修改时间晚于进程启动时间 = 旧代码（1 秒宽限；导入本模块的时刻近似进程启动）。给出 `version`（源码指纹）、新旧时间戳、涉及文件与**可直接照做**的中文处置文案。读不到文件时按"新鲜"处理——辅助网不该把正常工作全堵死 |
| 付费命令硬拦（API 侧） | `WorkbenchService.dispatch` 在派发前查：`generate_assets / generate_asset_image / generate_keyframes / generate_frame_image / produce_video / regenerate_segment` 在旧代码下一律抛 `STALE_CODE`，消息里写清"请重启 API 与 Worker"。免费动作（改参数、确认门、编辑、合成）不受影响 |
| 付费动作硬拦（Worker 侧） | `handle_asset_extract` 在**任何 Agent 调用之前**拒绝——资产提示词的作者就是 Worker 进程本身，这是本项目最贵的一步（1 次抽取 → 12 张图） |
| 可见性 | `/api/system/health` 返回 `code: {version, stale, process_started_ts, newest_source_ts, newest_source_path}`；Worker 启动打横幅（旧代码用 WARNING 加 ⚠）；项目首页状态条加「代码 xxx」/红字「后端代码已过期，需重启」并给出重启命令；过期时页面顶部显示红框说明 |
| 逃生口 | `ALLOW_STALE_CODE=1` 放行（确知改动与本动作无关时），默认不放行 |

## 规则化（写进 AGENTS.md）

仓库根新建 **AGENTS.md**（本项目此前没有该文件），第 1 节就是这条硬约定，含：
照抄可用的三组命令（查在途任务 → `scripts/run_workers.py --stop` / `--count 3` + 重启 uvicorn →
`curl /api/system/health` 确认 `code.stale=false`）、为什么是硬规则（含 89 币事故证据）、
配套机制与逃生口、以及两条容易混淆的路径（「点某张卡生成」读库里已存提示词、不受重启影响；
「抽取资产」由进程现场撰写、必须重启）+ 存量提示词免费重写命令。
第 2 节「花钱前自查、花钱后如实报账」，第 3 节「删改用户数据前先看目标先备份」。
AI_RULES.md 保持原样（不重复同一条规则，避免两处漂移）；PROJECT_STATE 交接备注改为先读 AGENTS.md。

## 验证

- 单测：`test_buildinfo.py`（新鲜/过期/逃生口）、`test_app_flow.py`（付费命令 `STALE_CODE`、
  免费命令放行、Worker handler 拒绝）。**后端 216 项 / 前端 16 项全绿**，`ruff check`、`tsc -b` 通过。
- 线上：重启 API + 3 Worker 后，health 返回 `stale: false, version: da25b30c8f22`；
  Worker 日志出现「代码版本 da25b30c8f22 —— 代码已是最新」。

## 遗留与代价

- 那 6 张旧口径卡（89 币）仍在库里可用（它们就是今天之前管线的默认口径，后续流程不受影响）；
  若要换成新口径，提示词已按新规则写好，页面上逐个点「生成」即可（估算 6 张约 80–90 币）。
- 我（助手）在处置过程中有一次操作失误：读到的任务状态是"运行中"，执行取消时该任务其实已经成功，
  导致一条正常记录被误标为 cancelled/failed，**已立即恢复**（job=succeeded、图片行=ready）；未影响产物。
- 重启 Worker 时回收把 2 个刚被认领的道具任务判为中断并重排（此时 RunningHub 侧可能已计费，
  但 `provider_calls` 无记录）——潜在未入账花费约 18 币，如账号账单对不上，原因在此。
