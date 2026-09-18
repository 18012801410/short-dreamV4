import { useQuery } from '@tanstack/react-query'
import { api, type Job } from '../api/client'
import { useCancelJob, useRetryJob } from '../hooks'

const TYPE_LABEL: Record<string, string> = {
  script_gen: '剧本生成',
  asset_extract: '资产抽取',
  image_gen: '设定图',
  storyboard_gen: '分镜生成',
  frame_gen: '关键帧',
  video_gen: '视频生成',
  tail_extract: '尾帧提取',
  compose: '成片合成',
}

function providerHint(error: { code: string; message: string; provider_code: string } | null): string | null {
  if (!error) return null
  if (error.provider_code === 'AUTH' || error.code === 'PROVIDER_ERROR' && error.message.includes('KEY'))
    return '请到设置页检查 API Key'
  if (error.provider_code === 'TIMEOUT') return '生成超时，可重试'
  if (error.message.includes('余额') || error.message.includes('coins')) return '余额不足，请充值'
  return null
}

function JobRow({ job, pid }: { job: Job; pid: string }) {
  const retry = useRetryJob(pid)
  const cancel = useCancelJob(pid)
  const busy = job.status === 'pending' || job.status === 'running'
  const hint = providerHint(job.error)
  return (
    <li className="rounded border border-line bg-surface-2 px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">
          {TYPE_LABEL[job.type] ?? job.type}
          <span className="ml-2 text-ink-dim">#{job.id.slice(-6)}</span>
        </span>
        <span
          className={
            job.status === 'succeeded'
              ? 'text-ok'
              : job.status === 'failed'
                ? 'text-danger'
                : job.status === 'cancelled'
                  ? 'text-ink-dim'
                  : 'text-warn'
          }
        >
          {job.status === 'running' ? `进行中 ${job.progress}%` : job.status}
        </span>
      </div>
      {job.error && (
        <div className="mt-1 text-danger">
          <p>
            {job.error.message}
            {job.error.provider_code ? ` (${job.error.provider_code})` : ''}
          </p>
          {hint && <p className="text-warn">提示：{hint}</p>}
        </div>
      )}
      <div className="mt-1 flex gap-2">
        {job.status === 'failed' && (
          <button
            className="rounded border border-line px-2 py-0.5 hover:border-primary hover:text-primary disabled:opacity-40"
            disabled={job.attempts >= job.max_attempts}
            title={job.attempts >= job.max_attempts ? '重试次数已用尽，请用重新生成' : ''}
            onClick={() => retry.mutate(job.id)}
          >
            重试
          </button>
        )}
        {busy && (
          <button
            className="rounded border border-line px-2 py-0.5 hover:border-danger hover:text-danger"
            onClick={() => cancel.mutate(job.id)}
          >
            取消
          </button>
        )}
      </div>
      {job.log.length > 0 && (
        <details className="mt-1 text-ink-dim">
          <summary className="cursor-pointer">日志</summary>
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono">
            {job.log.map((e) => `${e.at.slice(11, 19)} ${e.event} ${e.detail}`).join('\n')}
          </pre>
        </details>
      )}
    </li>
  )
}

export default function TaskCenter({ pid }: { pid: string }) {
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 8000,
  })
  const { data, isLoading } = useQuery({
    queryKey: ['jobs', pid],
    queryFn: () => api.jobs(pid),
    refetchInterval: 2000,
  })
  const jobs = data?.jobs ?? []
  const groups: { label: string; jobs: Job[] }[] = [
    { label: '进行中', jobs: jobs.filter((j) => j.status === 'pending' || j.status === 'running') },
    { label: '失败', jobs: jobs.filter((j) => j.status === 'failed') },
    { label: '已完成', jobs: jobs.filter((j) => j.status === 'succeeded').slice(0, 5) },
  ]

  return (
    <aside className="w-72 shrink-0 overflow-y-auto border-l border-line bg-surface p-3">
      <h2 className="mb-2 text-sm font-medium text-ink-dim">任务中心</h2>
      {health && !health.ffmpeg.found && (
        <p className="mb-2 rounded border border-danger/40 px-2 py-1 text-xs text-danger">
          ffmpeg 缺失，视频功能不可用（winget install Gyan.FFmpeg）
        </p>
      )}
      {isLoading && <p className="text-xs text-ink-dim">加载中…</p>}
      {!isLoading && jobs.length === 0 && <p className="text-xs text-ink-dim">暂无任务</p>}
      {groups.map(
        (group) =>
          group.jobs.length > 0 && (
            <section key={group.label} className="mb-3">
              <h3 className="mb-1 text-xs text-ink-dim">── {group.label}</h3>
              <ul className="space-y-2">
                {group.jobs.map((job) => (
                  <JobRow key={job.id} job={job} pid={pid} />
                ))}
              </ul>
            </section>
          ),
      )}
    </aside>
  )
}
