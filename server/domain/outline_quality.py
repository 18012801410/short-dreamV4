"""系列大纲确定性质量门（TASK-047）：短剧方法论里可代码化的硬检查。

方法论来源（内化自 dj-short-drama / dj-novel-outline + 行业共识）：
- 每集必有开场钩子与集尾卡点（除大结局）；钩子是留存的生命线
- 爽点间隔 ≤3 集：连续 3 集无爽点 = 观众弃剧真空区
- 大爆点不压最后一集：高潮后要留收束空间，虎头蛇尾是大忌
- 人物表收敛：AI 生成与观众记忆都容不下超过 10 个具名角色
- 第 1 集必须带爽点/强冲突：免费区间的第一集决定整部剧的完播

质量门全部确定性检查（零 LLM 零成本），返回违规清单写入
SeriesOutline.warnings；确认大纲时「硬门」未过则拒绝（见 usecases），
「软门」仅提示不阻断。
"""

from __future__ import annotations

from server.domain.entities import SeriesOutline

# 爽点最大真空间隔（集）：连续无爽点的集数上限
MAX_HIGHLIGHT_GAP = 3
# 全剧具名人物上限
MAX_CHARACTERS = 10
# 分集梗概最小长度（字）：低于此值不足以支撑逐集剧本生成
MIN_SYNOPSIS_CHARS = 40


def validate_series_outline(
    outline: SeriesOutline, *, expected_episodes: int = 0
) -> list[str]:
    """返回违规/风险清单（空 = 全过）。只读，不修改 outline。"""
    issues: list[str] = []
    episodes = outline.episodes
    numbers = [ep.episode_no for ep in episodes]

    # 硬门 1：集号从 1 连续编号
    if numbers != list(range(1, len(episodes) + 1)):
        issues.append("分集编号必须从 1 起连续（当前：" + "、".join(map(str, numbers[:20])) + "）")

    # 硬门 2：集数与请求一致
    if expected_episodes and len(episodes) != expected_episodes:
        issues.append(f"分集数 {len(episodes)} 与请求的 {expected_episodes} 集不一致")

    # 硬门 3：每集开场钩子必填；集尾卡点除最后一集外必填
    for ep in episodes:
        if not ep.opening_hook.strip():
            issues.append(f"第 {ep.episode_no} 集缺少开场钩子（前 3 秒抓人的冲突/悬念）")
        is_last = ep.episode_no == max(numbers) if numbers else False
        if not is_last and not ep.ending_hook.strip():
            issues.append(f"第 {ep.episode_no} 集缺少集尾卡点（观众追下一集的理由）")

    # 硬门 4：第 1 集必须有爽点/强冲突（免费区间第一集定完播）
    first = episodes[0] if episodes else None
    if first is not None and not first.highlight.strip():
        issues.append("第 1 集缺少爽点/强冲突（highlight），完播率会显著受损")

    # 大爆点不压最后一集（≥6 集为硬门：高潮后要留收束空间；短篇降为软门——
    # 3-5 集的微剧里「最后一集爆点+同集收束」是常态）
    last_no = max(numbers) if numbers else 0
    if outline.climax_episode and outline.climax_episode >= last_no and last_no > 1:
        if last_no >= 6:
            issues.append(
                f"大爆点落在第 {outline.climax_episode} 集（最后一集），"
                "高潮后没有收束空间；应前移 1-3 集"
            )
        else:
            issues.append(
                f"大爆点落在第 {outline.climax_episode} 集（最后一集；"
                "少于 6 集的短篇可接受，长篇需前移留收束空间）"
            )

    # 软门 6：爽点真空区（连续 >3 集无 highlight）
    gap = 0
    gap_start = 0
    for ep in episodes:
        if ep.highlight.strip():
            gap = 0
            continue
        if gap == 0:
            gap_start = ep.episode_no
        gap += 1
        if gap > MAX_HIGHLIGHT_GAP:
            issues.append(
                f"第 {gap_start}-{ep.episode_no} 集连续 {gap} 集无爽点，超过 {MAX_HIGHLIGHT_GAP}"
                " 集真空上限，观众会在这里流失"
            )
            gap = 0  # 记录一次后重新计数，避免同一真空区刷屏

    # 软门 7：人物表收敛
    if len(outline.characters) > MAX_CHARACTERS:
        issues.append(
            f"全剧具名人物 {len(outline.characters)} 个，超过 {MAX_CHARACTERS} 个上限——"
            "合并次要人物或降为无名配角"
        )

    # 软门 8：梗概厚度
    for ep in episodes:
        if 0 < len(ep.synopsis.strip()) < MIN_SYNOPSIS_CHARS:
            issues.append(
                f"第 {ep.episode_no} 集梗概仅 {len(ep.synopsis.strip())} 字"
                f"（建议 ≥{MIN_SYNOPSIS_CHARS}），撑不起一集的冲突展开"
            )

    # 软门 9：连续同文卡点（偷懒复制）
    for i in range(2, len(episodes)):
        a, b, c = (
            episodes[i - 2].ending_hook.strip(),
            episodes[i - 1].ending_hook.strip(),
            episodes[i].ending_hook.strip(),
        )
        if a and a == b == c:
            issues.append(f"第 {i - 1}-{i + 1} 集集尾卡点完全相同，钩子类型需轮换（悬念/反转/情绪/信息/危机）")
            break

    return issues


def hard_gate_issues(outline: SeriesOutline, *, expected_episodes: int = 0) -> list[str]:
    """确认大纲时必须为空的硬门子集（软门只提示不阻断）。"""
    soft_markers = ("真空上限", "具名人物", "梗概仅", "完全相同", "短篇可接受")
    return [
        issue
        for issue in validate_series_outline(outline, expected_episodes=expected_episodes)
        if not any(marker in issue for marker in soft_markers)
    ]
