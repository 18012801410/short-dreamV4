"""存量资产维护脚本（资产卡口径对齐：一资产一卡）。

- image_plan 多于 1 项 → 裁剪为 1 项（主设定/空镜优先，否则第一项）
- image_plan 提示词重写为当前卡模板（角色=**单幅正面全身定妆照**（TASK-043，
  此前是半身像）；场景=**单幅广角空镜全貌**（TASK-042，此前是多视图拼贴）；
  道具=白底卡），使「生成」按钮直接产出合规卡
- 同标签多张图时保留最新一张，删除其余（磁盘文件保留，可手工恢复）
- 若被删图中有 approved 而保留图未批准 → 保留图自动置为 approved（保持资产门一致）

`--kind=character|scene|prop`（可逗号分隔）只处理指定类型：改某类卡口径时
（如 TASK-042 场景多视图→单幅广角、TASK-043 角色半身→全身）用它精确重写存量
提示词，不动其他类型；`--scenes-only` 等价于 `--kind=scene`（旧写法保留）。
`--prompts-only` 只重写提示词、**不做同标签多图清理**（不删任何图片行、不动批准的图）。

默认 dry-run 只打印；确认无误后加 --apply 落库。
用法：python -m scripts.normalize_asset_cards [--apply] [--kind=scene] [--prompts-only]
"""

from __future__ import annotations

import sys

import sqlalchemy as sa

from server.app.context import AppContext
from server.domain.entities import ImagePlanItem
from server.infra.config import Settings
from server.infra.tables import metadata

PRIMARY_HINTS = ("主设定", "空镜")


def parse_kinds(argv: list[str]) -> set[str]:
    """解析 --kind=a,b（未给则空集 = 全部类型）；--scenes-only 是 --kind=scene 的旧写法。"""
    kinds: set[str] = set()
    for arg in argv:
        if arg.startswith("--kind="):
            kinds.update(k.strip() for k in arg.split("=", 1)[1].split(",") if k.strip())
    if "--scenes-only" in argv:
        kinds.add("scene")
    return kinds


def pick_primary_index(labels: list[str]) -> int:
    for index, label in enumerate(labels):
        if any(hint in label for hint in PRIMARY_HINTS):
            return index
    return 0


def card_prompt(kind: str, name: str, anchor: str, style: str, aspect: str = "16:9") -> str:
    """当前资产卡模板（确定性，不依赖 LLM）。

    角色 = 单幅正面全身定妆照（TASK-043）；场景 = 单幅广角空镜全貌（TASK-042）；
    道具 = 白底卡。
    """
    style_tail = f", style: {style}" if style.strip() else ""
    if kind == "character":
        core = (
            f"Character reference card of {name}, {anchor}, "
            "a single front-facing full-body portrait filling the frame, "
            "standing straight facing the camera, arms relaxed at the sides, "
            "head to toe completely in frame, no cropping of the head or the shoes, "
            "the whole outfit from top to shoes fully visible, "
            "symmetric facial features clearly readable, natural expression, "
            "looking at the camera, one person only, "
            "no collage, no multiple views, no panels, no turnaround sheet, "
            "clean light background, "
            f"{aspect}, high detail"
        )
    elif kind == "scene":
        anchor = f"{anchor}" if anchor else "an open space"
        core = (
            f"A completely deserted and empty place: wide establishing shot of {name}, {anchor}, "
            "one single unbroken photographic frame from one camera position, "
            "ultra-wide-angle lens view (about 16-24mm) from a corner or a slightly elevated "
            "vantage point that takes in the whole space at once - the far walls, both ends "
            "and all the main landmarks are visible together in one continuous shot, "
            "clear depth and front-to-back layering, the same landmark objects readable "
            "in position, count and size, every booth and cabin stands empty, "
            "key lighting and mood, important set props only, "
            f"single frame composition, {aspect}, high detail"
        )
    else:
        core = (
            f"Prop design card of {name}, {anchor}, "
            "a single isolated prop centered on a pure white background, "
            "studio product photography with soft even lighting, "
            "unworn and unused, standing upright by itself, "
            "high detail"
        )
    return core + style_tail


def main(
    apply: bool, kinds: set[str] | None = None, prompts_only: bool = False
) -> None:
    kinds = kinds or set()
    settings = Settings(_env_file=None)
    engine = sa.create_engine(
        f"sqlite:///{settings.data_dir / 'app.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)

    topped_up_plans = 0
    trimmed_plans = 0
    rewritten_prompts = 0
    pruned_images = 0
    approved_transfers = 0
    for project in ctx.projects.list_all():
        style = project.params.style or ""
        aspect = (project.params.ratio or "16:9").strip() or "16:9"
        for asset in ctx.assets.list_assets(project.project_id):
            if kinds and asset.kind.value not in kinds:
                continue
            plan_changed = False
            # 0) 空 image_plan 兜底：没有计划项的资产点「生成」会退化成裸锚点
            #    （不带动画幅/卡片取景），这里补一条当前卡模板
            if not asset.image_plan:
                label = "空镜" if asset.kind.value == "scene" else "主设定"
                print(f"[{project.title}] {asset.name}: image_plan 为空 → 补 [{label}] 卡模板")
                topped_up_plans += 1
                if apply:
                    asset.image_plan = [
                        ImagePlanItem(
                            view_label=label,
                            image_prompt=card_prompt(
                                asset.kind.value, asset.name, asset.visual_anchor, style, aspect
                            ),
                        )
                    ]
                    plan_changed = True
            # 1) 裁剪 image_plan
            if len(asset.image_plan) > 1:
                labels = [p.view_label for p in asset.image_plan]
                keep = asset.image_plan[pick_primary_index(labels)]
                print(
                    f"[{project.title}] {asset.name}: image_plan {labels} -> [{keep.view_label}]"
                )
                trimmed_plans += 1
                if apply:
                    asset.image_plan = [keep]
                    plan_changed = True
            # 2) 提示词重写为当前卡模板
            for item in asset.image_plan:
                new_prompt = card_prompt(
                    asset.kind.value, asset.name, asset.visual_anchor, style, aspect
                )
                if item.image_prompt.strip() != new_prompt:
                    print(
                        f"[{project.title}] {asset.name}: 提示词重写"
                        f"「{item.image_prompt[:40]}…」→ 当前卡模板"
                    )
                    rewritten_prompts += 1
                    item.image_prompt = new_prompt
                    plan_changed = True
            if apply and plan_changed:
                ctx.assets.save_asset(asset)
            if prompts_only:
                continue
            # 3) 同标签多图保留最新一张
            images = ctx.assets.list_images(asset_id=asset.asset_id)
            if not images:
                continue
            labels = [i.view_label for i in images]
            primary_label = labels[pick_primary_index(labels)]
            # 主标签内保留最新一张【已落盘】的图（生成中/失败的行不顶掉可用的老图）；
            # 若全部还在生成中，则保留最新的生成中行（任务完成时会落盘）
            same_label = [i for i in images if i.view_label == primary_label]
            landed = [i for i in same_label if i.status.value in ("ready", "uploaded")]
            primary = (landed or same_label)[-1]
            extras = [i for i in images if i.asset_image_id != primary.asset_image_id]
            if not extras:
                continue
            print(
                f"[{project.title}] {asset.name}: 删图 "
                f"{[i.view_label for i in extras]}（保留最新：{primary.view_label}）"
            )
            pruned_images += len(extras)
            if apply:
                for extra in extras:
                    ctx.assets.delete_image(extra.asset_image_id)
                if any(i.approved for i in extras) and not primary.approved:
                    primary.approved = True
                    ctx.assets.save_image(primary)
                    approved_transfers += 1
                    print(f"    保留图 {primary.view_label} 自动置为 approved（保持资产门）")

    mode = "已落库" if apply else "dry-run（加 --apply 落库）"
    scope = f"仅 {','.join(sorted(kinds))} 资产" if kinds else "全部资产"
    prune_note = "（--prompts-only：不删图）" if prompts_only else ""
    print(
        f"\n{mode}（{scope}）{prune_note}: 补卡模板 {topped_up_plans} 个，"
        f"裁剪 image_plan {trimmed_plans} 个，"
        f"重写提示词 {rewritten_prompts} 条，"
        f"删图 {pruned_images} 张，补批准 {approved_transfers} 次"
    )


if __name__ == "__main__":
    main(
        apply="--apply" in sys.argv,
        kinds=parse_kinds(sys.argv),
        prompts_only="--prompts-only" in sys.argv,
    )
