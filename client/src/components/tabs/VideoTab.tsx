import { type Project } from '../../api/client'
import { useSegments } from '../../hooks'

type RunFn = (type: string, payload?: Record<string, unknown>) => void

function depLabel(row: { job: { depends_on: string[] } | null }): string | null {
  const deps = row.job?.depends_on ?? []
  return deps.length > 0 ? `等待 ${deps.length} 个前置任务` : null
}

export default function VideoTab({
  project,
  onRun,
}: {
  project: Project
  onRun: RunFn
}) {
  const { data, isLoading } = useSegments(project.id)
  if (isLoading) return <p className="text-sm text-ink-dim">加载中…</p>
  const segments = data?.segments ?? []
  if (segments.length === 0) {
    return (
      <div className="rounded border border-line bg-surface p-6 text-center text-sm text-ink-dim">
        还没有分镜段。先确认分镜。
      </div>
    )
  }
  const anyProducing = segments.some((s) => s.job && (s.job.status === 'pending' || s.job.status === 'running'))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button
          className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover disabled:opacity-40"
          disabled={anyProducing}
          onClick={() => onRun('produce_video', { scope: 'all' })}
        >
          生成全部段
        </button>
        <button
          className="rounded border border-line px-4 py-2 text-sm hover:border-primary hover:text-primary disabled:opacity-40"
          disabled={anyProducing}
          onClick={() => onRun('produce_video', { scope: 'first_only' })}
          title="先只生成第一段，验证风格后再批量"
        >
          首段试跑
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {segments.map((row) => {
          const waiting = depLabel(row)
          return (
            <article key={row.segment_key} className="rounded-lg border border-line bg-surface p-3">
              <header className="mb-2 flex items-center justify-between">
                <h3 className="font-mono text-sm">{row.segment_key}</h3>
                <span
                  className={
                    row.status === 'done'
                      ? 'text-xs text-ok'
                      : row.job?.status === 'failed'
                        ? 'text-xs text-danger'
                        : 'text-xs text-warn'
                  }
                >
                  {row.status === 'done'
                    ? `完成 ${row.clip?.duration_sec?.toFixed(1)}s`
                    : row.job
                      ? row.job.status === 'running'
                        ? `生成中 ${row.job.progress}%`
                        : row.job.status === 'failed'
                          ? '失败'
                          : waiting ?? row.job.status
                      : '未开始'}
                </span>
              </header>
              {row.clip ? (
                <video
                  src={row.clip.url}
                  controls
                  preload="metadata"
                  className="aspect-video w-full rounded bg-black"
                />
              ) : (
                <div className="flex aspect-video items-center justify-center rounded bg-surface-2 text-xs text-ink-dim">
                  {row.job ? '生成中…' : '无视频'}
                </div>
              )}
              <footer className="mt-2 flex gap-2">
                <button
                  className="rounded border border-line px-2 py-0.5 text-xs hover:border-primary hover:text-primary"
                  onClick={() => onRun('regenerate_segment', { segment_key: row.segment_key })}
                >
                  重新生成
                </button>
                {row.clip?.tail_frame_url && (
                  <img
                    src={row.clip.tail_frame_url}
                    alt={`${row.segment_key} 尾帧`}
                    className="ml-auto h-8 rounded border border-line"
                    title="尾帧（下一段连续性参考）"
                  />
                )}
              </footer>
            </article>
          )
        })}
      </div>
    </div>
  )
}
