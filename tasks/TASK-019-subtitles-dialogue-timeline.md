# TASK-019 —— 成片字幕 + 台词舞台指示污染治理 + 分镜动作时间线 + 参考图显示修复

日期：2026-09-13 ｜ 模式：缺陷修复 + 有界新功能 ｜ 状态：已完成

## 用户反馈

1. 生成的视频补一套带字幕。
2. 形容词/动作描述被当成台词读出来（如「噗通一下，跪下」）。
3. 分镜没有动作时间线（0 秒在干嘛、0-4 秒在干嘛）。
4. 分镜依然没有显示所引用的参考图。

## 根因与修复

### ④ 参考图没显示 = 后端旧进程
TASK-018 的实现本身没有问题（用真实 DB 直调路由逻辑验证通过）：API 服务以
`uvicorn ... --port 8000`（无 --reload）常驻，改动后未重启。重启 API + worker 后
`GET /storyboard` 即返回 `references`。本次改进：折叠行也直接内联缩略图条（不用展开）。

### ② 台词污染：括号舞台指示被 H3 读出声
真实数据：剧本台词 `王强：（扑通跪下）林总，我错了…` —— 分镜 Agent 逐字搬进 `<d>` 块，
H3 把「扑通跪下」读出声。三层修复：
- 生成端：ScriptAgent 提示词明令 line 只装说出口的话，动作写 summary；
  StoryboardAgent 输入前确定性清洗（存量脏剧本同样被治理）。
- 确定性兜底：新增 `server/domain/textnorm.py` `strip_stage_directions`
  （全/半角括号内容=舞台指示），run_script 入库前剥离并写 warnings；纯指示台词移除。
- 校验端：`validate_h3_prompt` 新增 `<d>` 块含括号 → 报错（分镜重试与 edit_segment 同闸）。

### ① 成片字幕
- `server/app/subtitles.py`：按分镜 shots 切点 + Clip 实测时长生成 SRT
  （段起点=前段实测时长累加；台词窗口=所在镜头 [切点, 下一镜切点)；同镜多句均分）。
- `FFmpegService.burn_subtitles`：subtitles 滤镜 + libx264 重编码（音频直拷）；
  以视频目录为 cwd、SRT 用相对文件名，绕开 Windows 盘符转义。
- compose handler：成片后自动烧字幕 → `film_v{n}_sub.mp4` + `film_v{n}.srt`；
  无台词或烧录失败留空不阻断成片。Film 实体/表加 `subtitle_path`（迁移 0003）。
- 状态机补「COMPOSED → compose」重新合成（换段/重生成后可再出一版，纯本地 ffmpeg 零币）。
- FilmTab：默认播放字幕版，可切换原片；分别下载。字幕文本同样经舞台指示清洗。

### ③ 分镜动作时间线
StoryboardTab 每段新增：比例时间条（每镜宽度=时长占比）+ 逐镜「起–止s 镜号 运镜 描述」，
台词挂在所属镜头下——0 秒在干嘛一目了然。

## 真实端到端验证
- 「渡口夜行」重新合成：job 30s 完成 → `film_v2.mp4`(70.9s) + `film_v2.srt`(6 句) +
  `film_v2_sub.mp4`(1920×1088 有音轨)；抽帧确认字幕烧录清晰。
- 本机 ffmpeg（D:\ffmpeg-master-latest-win64-gpl）libass 可用，中文字体走 Microsoft YaHei。

## 验收
- 后端 116 passed + ruff clean（新增 textnorm/SRT/`<d>` 指示拦截/烧字幕集成测试）。
- 前端 tsc + vitest 14 passed。
- 迁移 0003 已应用到 data/app.db；API/worker 已重启至最新代码。
