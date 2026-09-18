"""剧本人物/道具唯一命名归一（用户硬要求：同一人物/道具不得出现多个命名）。

第 3 集实测事故的根治：重摇剧本把同一批人写成 李桂芬/王秀兰/陈国栋/老管家，
资产抽取照着剧本抽 → 出来一堆"陌生人"。本模块在剧本生成后**确定性**归一
（零 LLM，不靠模型自觉），四层防线：

1. 系列对齐（cast 给定时）：剧本人物与大纲人物表按别名等价（全名/括号注名/
   去括号基名）对上的，一律改写成大纲登记名；对不上的写 warning；
2. 剧本内合并：characters[] 里别名等价的多个条目合并为一个规范名
   （取台词说话人出现最多者，并列取先登记），profile 合并；
3. 全文归一：dialogue.speaker / beats.text / scenes.summary / logline 里的
   别名一律单遍改写为规范名（长别名优先，一次匹配不重复套娃）；
4. props 去重（去括号基名相同视为同一道具），疑似别名（互为子串）给 warning。

无法自动修复的（未登记说话人、大纲外人物）只写 warnings，由人工决策——
warnings 已在页面展示。
"""

from __future__ import annotations

import re

from server.domain.entities import ScriptContent
from server.domain.enums import BeatType

_PAREN_RE = re.compile(r"[（(]([^（）()]+)[）)]")


def _name_and_aliases(name: str) -> tuple[str, set[str]]:
    """(规范名, 等价别名集合)。别名 = 括号注名 + 去括号基名（不含规范名本身）。"""
    name = name.strip()
    aliases: set[str] = {name}
    for match in _PAREN_RE.finditer(name):
        inner = match.group(1).strip()
        if inner:
            aliases.add(inner)
    base = _PAREN_RE.sub("", name).strip()
    if base:
        aliases.add(base)
    return name, aliases


def _group_key(name: str) -> str:
    """归并键：去括号基名（「苏母（王秀兰）」与「王秀兰」同键）。"""
    base = _PAREN_RE.sub("", name.strip()).strip()
    return base or name.strip()


def names_match(a: str, b: str) -> bool:
    """两个名字是否别名等价（全名/括号注名/去括号基名 任一相交即视为同一人）。"""
    return bool(_name_and_aliases(a)[1] & _name_and_aliases(b)[1])


def enforce_name_registry(
    content: ScriptContent, *, cast: list[str] | None = None
) -> list[str]:
    """归一 content 的人物/道具命名，返回 issue 清单（调用方并入 warnings）。

    cast：系列大纲人物表登记名（逐集剧本传入）；单项目剧本传 None。
    就地修改 content；已与规范名一致的输入原样返回空 issues。
    """
    issues: list[str] = []

    # ---- 1) 系列对齐：剧本人物名 → 大纲登记名（别名等价即改写） ----
    cast_canonical: dict[str, str] = {}  # 别名/原名 → 大纲登记名
    if cast:
        for cast_name in cast:
            _, cast_aliases = _name_and_aliases(cast_name)
            for alias in cast_aliases:
                cast_canonical.setdefault(alias, cast_name)
    if cast_canonical:
        for character in content.characters:
            canonical = cast_canonical.get(character.name.strip())
            if canonical and canonical != character.name:
                issues.append(
                    f"人物「{character.name}」已对齐大纲登记名「{canonical}」"
                )
                character.name = canonical
            elif canonical is None and not any(
                character.name.strip() in _name_and_aliases(c)[1] for c in cast
            ):
                issues.append(
                    f"人物「{character.name}」不在大纲人物表（也不在本集应登记的新角色中），"
                    "请人工确认是否为同一人的另一种叫法"
                )

    # ---- 2) 剧本内合并：别名等价的条目归并为一个规范名 ----
    # 分组：两两别名集合有交集 → 同一人
    groups: list[list[int]] = []
    for index, character in enumerate(content.characters):
        _, aliases = _name_and_aliases(character.name)
        target = None
        for group in groups:
            group_aliases: set[str] = set()
            for other in group:
                group_aliases |= _name_and_aliases(content.characters[other].name)[1]
            if aliases & group_aliases:
                target = group
                break
        if target is None:
            groups.append([index])
        else:
            target.append(index)

    rename_map: dict[str, str] = {}  # 旧条目名 → 规范名（含等价别名）
    for group in groups:
        if len(group) == 1:
            continue
        # 规范名 = 台词说话人出现最多的登记名；并列取先登记
        speaker_counts: dict[str, int] = {}
        for scene in content.scenes:
            for beat in scene.beats:
                if beat.type is BeatType.DIALOGUE and beat.speaker.strip():
                    name = beat.speaker.strip()
                    speaker_counts[name] = speaker_counts.get(name, 0) + 1
        ordered = sorted(
            group,
            key=lambda i: (
                -speaker_counts.get(content.characters[i].name.strip(), 0),
                i,
            ),
        )
        canonical_index = ordered[0]
        canonical = content.characters[canonical_index].name.strip()
        names = [content.characters[i].name.strip() for i in group]
        issues.append(f"人物「{'」「'.join(names)}」为同一人的多个命名，已统一为「{canonical}」")
        # profile 合并进规范条目
        profiles: list[str] = []
        for i in group:
            profile = content.characters[i].profile.strip()
            if profile and profile not in profiles:
                profiles.append(profile)
        content.characters[canonical_index].profile = "；".join(profiles)
        for i in reversed(group):
            if i != canonical_index:
                content.characters.pop(i)
        for name in names:
            if name != canonical:
                rename_map[name] = canonical

    # 全体登记名 → 别名映射（含单条目人物的别名，供说话人/正文归一）
    alias_to_canonical: dict[str, str] = {}
    for character in content.characters:
        name, aliases = _name_and_aliases(character.name)
        for alias in aliases:
            owner = alias_to_canonical.get(alias)
            if owner is not None and owner != name:
                issues.append(
                    f"别名「{alias}」同时命中「{owner}」与「{name}」，"
                    "命名有歧义，请人工区分"
                )
                continue
            alias_to_canonical[alias] = name
    # 合并组里的旧名也要改写到规范名（旧条目已删，别名表需补充）
    for old, canonical in rename_map.items():
        _, old_aliases = _name_and_aliases(old)
        for alias in old_aliases:
            alias_to_canonical.setdefault(alias, canonical)

    # ---- 3) 全文归一：说话人 + 正文提及（单遍、长别名优先） ----
    body_aliases = sorted(
        (a for a, c in alias_to_canonical.items() if a != c),
        key=len,
        reverse=True,
    )

    def rewrite(text: str) -> str:
        if not body_aliases:
            return text
        pattern = re.compile("|".join(re.escape(a) for a in body_aliases))
        return pattern.sub(lambda m: alias_to_canonical[m.group(0)], text)

    for scene in content.scenes:
        for beat in scene.beats:
            if beat.speaker and beat.speaker.strip() in alias_to_canonical:
                beat.speaker = alias_to_canonical[beat.speaker.strip()]
            beat.text = rewrite(beat.text)
        for dialogue in scene.dialogues:
            if dialogue.speaker and dialogue.speaker.strip() in alias_to_canonical:
                dialogue.speaker = alias_to_canonical[dialogue.speaker.strip()]
            dialogue.line = rewrite(dialogue.line)
        scene.summary = rewrite(scene.summary)
    content.logline = rewrite(content.logline)

    # 未登记说话人 → warning（人工处理；可能真是漏登记的人物）
    registered = {c.name.strip() for c in content.characters}
    registered |= set(alias_to_canonical)
    for scene in content.scenes:
        for beat in scene.beats:
            speaker = beat.speaker.strip()
            if beat.type is BeatType.DIALOGUE and speaker and speaker not in registered:
                issues.append(f"说话人「{speaker}」未在 characters 登记，请补充或改名")

    # ---- 4) props 去重：基名相同视为同一道具；互为子串 → 疑似别名 ----
    deduped: list[str] = []
    base_seen: dict[str, str] = {}
    for prop in content.props:
        key = prop.strip()
        if not key:
            continue
        base = _group_key(key)
        if base in base_seen:
            issues.append(f"道具「{key}」与「{base_seen[base]}」为同一道具的多个命名，已合并")
            continue
        base_seen[base] = key
        deduped.append(key)
    for i, prop in enumerate(deduped):
        for other in deduped[i + 1:]:
            if prop != other and (prop in other or other in prop):
                issues.append(
                    f"道具「{prop}」与「{other}」疑似同一道具的两种命名，请人工合并"
                )
    content.props = deduped

    return issues
