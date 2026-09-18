# AGENTS.md —— short-dreamV4 工作约定

面向在本仓库工作的编码智能体（含 ZCode/Claude Code 等）。这里只放**必须照做、且违反会直接造成损失**
的硬约定；编码规范、验证清单、范围限制等见 [AI_RULES.md](AI_RULES.md)，规格见各 `*_SPEC.md`。

---

## 1. 改完代码必须重启 API 与 Worker（改完即重启，不许拖）

**规则**：任何对 `server/`、`scripts/` 下源码的修改——尤其是提示词模板、资产卡口径、
H3/关键帧编译器、Provider 参数、生成逻辑——**改完必须立刻重启 API 与 Worker**，
并在继续任何操作前确认新代码已生效。

```bash
# 1) 先确认没有在途任务（有 running/pending 就先等它跑完）
python -c 'import sqlalchemy as sa;from server.infra.config import Settings;s=Settings(_env_file=None);e=sa.create_engine("sqlite:///"+str(s.data_dir/"app.db"));print(list(e.connect().execute(sa.text("select type,status,count(*) from jobs group by type,status"))))'

# 2) 重启 Worker 池与 API
python scripts/run_workers.py --status          # 看现有几个
python scripts/run_workers.py --stop
python scripts/run_workers.py --count 3         # 池启动前会自动做一次遗留任务恢复
python -m uvicorn server.api.main:app --host 127.0.0.1 --port 8000   # 先停掉旧的 uvicorn

# 3) 确认生效：必须 stale=false
curl -s http://127.0.0.1:8000/api/system/health
```

**为什么是硬规则**：Python 进程只在**启动时**加载一次代码（uvicorn/worker 都没开 `--reload`），
改完不重启 = 跑的仍是旧规则。实测代价（2026-09-16）：改完「角色全身卡 + 单幅广角空镜」规则未重启，
用户点「抽取资产」时旧 Worker 按旧口径写提示词并照常扣币，**白花 89 币**，而页面完全看不出异常
（卡片、进度、状态都正常，只有肉眼比对"人是不是全身"才看得出）。

**配套机制（不要绕过）**：
- 进程加载旧代码时，付费动作（`generate_assets`／`generate_asset_image`／`generate_keyframes`／
  `generate_frame_image`／`produce_video`／`regenerate_segment`）一律返回 `STALE_CODE` 拒绝执行；
  `/api/system/health` 的 `code.stale=true`，首页红字提示「后端代码已过期，需重启」。
- `ALLOW_STALE_CODE=1` 是逃生口，仅在**确知本次改动与该动作无关**时使用，且必须在报告里写明理由。
- 两条路径别混：「点某张卡生成」读的是库里已存的提示词（**不受重启影响**，改提示词后即时生效）；
  「抽取资产」是进程现场撰写提示词（**必须重启才生效**）。改完提示词模板后，
  存量项目的提示词可用 `python -m scripts.normalize_asset_cards --kind=character,scene --prompts-only --apply`
  免费重写（dry-run 为默认，`--apply` 才落库）。

## 2. 花钱之前先确认自己不是旧代码，花钱之后如实报账

- 会下单扣币的动作：抽取资产（1 次 → N 张图）、生成资产图、生成/重抽关键帧、产视频、单段重生成。
  执行前先过一遍第 1 条。
- 报告里给出**实际花费**（`provider_calls.usage.coins` 实录），不要用估算值糊过去。
- 任何"重试"不得对已成功收费的调用重复发起；重启/回收前先确认没有在途任务，
  否则 `recover_stale_running` 会把在途任务判为中断重排，可能重复计费。

## 3. 删改用户数据前：先看目标、先备份

- 删除/覆盖前打开实物确认（内容与描述一致、确实是你以为的那个东西）。
- 批量清理前做**可校验**的备份，并逐项核对：
  SQLite 用在线备份 API（含 WAL 中已提交事务的单文件快照），
  `sqlite3.connect(src).backup(dst)`，然后逐表比对行数一致再动手。
- 破坏性操作要么有用户明确指令，要么先把备份路径和影响面报告给用户。
