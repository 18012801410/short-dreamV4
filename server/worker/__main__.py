"""Worker 进程入口：python -m server.worker（TASK-012：真实 Provider 任务循环）。

并发 Worker 池（TASK-034）：多个进程可同时跑（任务认领是条件 UPDATE 抢占），
但**启动恢复只能由池启动时跑一次**——`recover_stale_running` 会把所有遗留
running 任务判为中断，多进程各自启动时会互相打断对方的在途任务（实测：新起
一个 Worker 就把在线跑图任务判成 INTERRUPTED 并重排，白费一张图）。

    python -m server.worker                 # 单进程（含启动恢复）
    python -m server.worker --no-recover    # 池成员：不做恢复
    python -m server.worker --recover-only  # 只跑一次恢复后退出（池启动前调用）
    池启动/停止统一走 scripts/run_workers.py
"""

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("worker")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Worker 进程")
    parser.add_argument(
        "--no-recover",
        action="store_true",
        help="跳过启动恢复（并发池成员必须加，否则会打断其他进程的在途任务）",
    )
    parser.add_argument(
        "--recover-only",
        action="store_true",
        help="只执行一次遗留任务恢复后退出（应在池启动前、无其他 Worker 时调用）",
    )
    args = parser.parse_args(argv)

    from server.adapters.image import build_image_provider_from_settings
    from server.adapters.runninghub import RunningHubError
    from server.adapters.video import build_video_provider_from_settings
    from server.app.agents import Agents
    from server.app.context import AppContext
    from server.app.handlers import build_handlers
    from server.app.lazy import LazyLlm
    from server.infra.config import get_settings
    from server.infra.db import get_engine
    from server.infra.settings_store import apply_overrides, load_overrides
    from server.worker.engine import EngineConfig, WorkerEngine

    settings = apply_overrides(get_settings(), load_overrides(get_engine()))
    ctx = AppContext.build(settings, get_engine())
    agents = Agents(LazyLlm(settings))

    # 启动即报代码新鲜度（TASK-044）：改完代码不重启，Worker 会继续按旧规则撰写
    # 资产提示词并照常扣币（实测白花约 138 币）。这里点名，抽取时还会硬拦。
    from server.infra.buildinfo import freshness

    fresh = freshness()
    if fresh.stale:
        log.warning("⚠ 本进程加载的是旧代码：%s", fresh.describe())
    else:
        log.info("代码版本 %s —— %s", fresh.version, fresh.describe())

    image = video = None
    if settings.runninghub_api_key:
        try:
            image = build_image_provider_from_settings(settings)
            video = build_video_provider_from_settings(settings)
        except RunningHubError as exc:
            log.warning("媒体 Provider 构建失败（相关任务将失败）：%s", exc)
    else:
        log.warning("RUNNINGHUB_API_KEY 未配置：图片/视频任务将失败")

    handlers = build_handlers(ctx, agents, image=image, video=video)

    # 图片并发上限（TASK-048，默认 2）：每次领取现场读 settings 表覆盖——
    # 设置页改完即生效，无需重启 Worker。RunningHub 账号并发额度不足时
    # 调低这里，避免任务被拒（TASK_QUEUE_MAXED）后的退避循环
    from server.domain.enums import JobType

    def image_concurrency_provider() -> int:
        raw = load_overrides(get_engine()).get("image_concurrency")
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return max(1, int(settings.image_concurrency))

    worker = WorkerEngine(
        ctx.jobs,
        handlers,
        EngineConfig(
            poll_interval_sec=settings.poll_interval_sec,
            retry_backoff_base_sec=settings.poll_interval_sec,
        ),
        image_concurrency_provider=image_concurrency_provider,
    )

    if args.recover_only:
        stats = worker.recover_stale_running()
        log.info("recover-only 完成：%s", stats)
        print(f"recovered: {stats}")
        return

    log.info(
        "worker started: poll_interval=%ss data_dir=%s recover=%s",
        settings.poll_interval_sec,
        settings.data_dir,
        not args.no_recover,
    )
    if not args.no_recover:
        stats = worker.recover_stale_running()
        if stats["resumed"] or stats["interrupted"]:
            log.info("recovered stale jobs: %s", stats)
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        log.info("worker stopped")


if __name__ == "__main__":
    main()
