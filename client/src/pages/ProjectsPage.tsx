import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, type Project } from '../api/client'
import {
  isProjectBusy,
  projectStatusLabel,
  useCreateProject,
  useHealth,
  useProjects,
  useSeriesList,
} from '../hooks'
import { seriesStatusLabel } from './SeriesPage'

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs ${
        ok ? 'border-ok/40 text-ok' : 'border-danger/40 text-danger'
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-ok' : 'bg-danger'}`} />
      {label}
    </span>
  )
}

function CreateProjectForm({ onCreated }: { onCreated: (pid: string) => void }) {
  const create = useCreateProject()
  const [title, setTitle] = useState('')
  const [idea, setIdea] = useState('')
  const [tone, setTone] = useState<'hook' | 'three_act'>('hook')
  const [duration, setDuration] = useState(60)
  const [sceneCount, setSceneCount] = useState(3)
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    setError(null)
    create.mutate(
      {
        title,
        idea,
        params: {
          dramatic_tone: tone,
          target_duration_sec: Math.max(15, duration),
          scene_count: Math.max(1, sceneCount),
        },
      },
      {
        onSuccess: (result) => onCreated(result.project.id),
        onError: (err) => setError(err instanceof ApiError ? err.message : String(err)),
      },
    )
  }

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <input
        className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-sm"
        placeholder="项目标题"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        required
      />
      <textarea
        className="h-20 w-full rounded border border-line bg-surface-2 px-3 py-2 text-sm"
        placeholder="一句话想法：例如「深夜末班公交，司机与最后一位乘客」"
        value={idea}
        onChange={(e) => setIdea(e.target.value)}
      />
      <div>
        <p className="mb-1 text-xs text-ink-dim">剧作基调</p>
        <div className="grid grid-cols-2 gap-2">
          {(
            [
              { value: 'hook', label: '短剧钩子驱动', hint: '快节奏·强反转·爽感' },
              { value: 'three_act', label: '微电影三幕式', hint: '治愈·留白·情感闭环' },
            ] as const
          ).map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setTone(opt.value)}
              className={`rounded border px-2 py-1.5 text-left text-xs ${
                tone === opt.value
                  ? 'border-primary bg-primary/10 text-ink'
                  : 'border-line bg-surface-2 text-ink-dim hover:border-ink-dim'
              }`}
            >
              <span className="block font-medium">{opt.label}</span>
              <span className="block text-[11px] opacity-70">{opt.hint}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-ink-dim">
          目标总时长（秒）
          <input
            type="number"
            min={15}
            max={600}
            className="mt-1 w-full rounded border border-line bg-surface-2 px-2 py-1.5 text-sm text-ink"
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
          />
        </label>
        <label className="text-xs text-ink-dim">
          场景数
          <input
            type="number"
            min={1}
            max={12}
            className="mt-1 w-full rounded border border-line bg-surface-2 px-2 py-1.5 text-sm text-ink"
            value={sceneCount}
            onChange={(e) => setSceneCount(Number(e.target.value))}
          />
        </label>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      <button
        type="submit"
        className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover disabled:opacity-40"
        disabled={create.isPending || !title.trim() || !idea.trim()}
      >
        {create.isPending ? '创建中…' : '创建项目'}
      </button>
    </form>
  )
}

export default function ProjectsPage() {
  const { data: health } = useHealth()
  const { data, isLoading } = useProjects()
  // 系列聚合（用户拍板：首页每部剧一张卡，单集不单列；点开进系列页看各集）
  const { data: seriesData } = useSeriesList()
  const seriesRows = seriesData?.series ?? []
  const navigate = (pid: string) => {
    window.location.href = `/project/${pid}`
  }
  const allProjects: Project[] = data?.projects ?? []
  const projects: Project[] = allProjects.filter((p) => !p.series_id)

  return (
    <main className="mx-auto max-w-5xl px-8 py-10">
      <header className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">short-dreamV4</h1>
          <p className="mt-1 text-sm text-ink-dim">AI 短视频生产台 · 想法 → 剧本 → 资产 → 分镜 → 关键帧 → 成片</p>
        </div>
        <div className="flex gap-4">
          <Link to="/series" className="text-sm text-ink-dim hover:text-ink">
            系列短剧
          </Link>
          <Link to="/settings" className="text-sm text-ink-dim hover:text-ink">
            设置
          </Link>
        </div>
      </header>

      {health && (
        <section className="mb-8 flex flex-wrap gap-2">
          <StatusChip ok={health.db === 'ok'} label={`数据库 ${health.db}`} />
          <StatusChip ok={health.ffmpeg.found} label={health.ffmpeg.found ? 'ffmpeg 就绪' : 'ffmpeg 缺失'} />
          <StatusChip ok={health.providers_configured.llm} label="LLM Key" />
          <StatusChip ok={health.providers_configured.runninghub} label="RunningHub Key" />
          {health.code && (
            <StatusChip
              ok={!health.code.stale}
              label={health.code.stale ? '后端代码已过期，需重启' : `代码 ${health.code.version}`}
            />
          )}
        </section>
      )}

      {health?.code?.stale && (
        <div className="mb-6 rounded border border-danger/50 bg-danger/10 p-3 text-sm">
          <p className="font-medium text-danger">后端进程还在跑旧代码</p>
          <p className="mt-1 text-ink-dim">
            源码在 {new Date(health.code.newest_source_ts * 1000).toLocaleString()} 被修改
            （{health.code.newest_source_path}），而进程启动于{' '}
            {new Date(health.code.process_started_ts * 1000).toLocaleString()}。
            为了让「生成」按最新规则执行，请先重启后端：停掉 API 与 Worker 后重新启动
            （<code>python scripts/run_workers.py --stop</code> 再 <code>--count 3</code>，
            API 用 <code>uvicorn server.api.main:app --port 8000</code>）。
            代码过期期间，会花钱的动作（抽取资产／生成图／生成关键帧／产视频）会被直接拒绝，
            不会产生费用。
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <section className="space-y-6">
          {seriesRows.length > 0 && (
            <div>
              <h2 className="mb-3 text-sm font-medium text-ink-dim">系列剧（每部一张卡，点开看各集）</h2>
              <ul className="space-y-3">
                {seriesRows.map((s) => (
                  <li key={s.id} className="rounded-lg border border-line bg-surface p-4">
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="min-w-0 truncate font-medium" title={s.idea}>
                        {s.outline?.title ?? s.title}
                      </h3>
                      <span className="shrink-0 whitespace-nowrap rounded-full border border-line px-2 py-0.5 text-xs text-ink-dim">
                        {seriesStatusLabel(s.status)}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-1 text-sm text-ink-dim">{s.idea}</p>
                    <p className="mt-1 text-xs text-ink-dim">
                      已建档 {s.episode_projects ?? 0}/{s.params.episode_count} 集 · 每集约{' '}
                      {s.params.per_episode_sec} 秒
                    </p>
                    <div className="mt-3">
                      <Link
                        to={`/series/${s.id}`}
                        className="inline-block rounded bg-primary px-3 py-1.5 text-sm text-white hover:bg-primary-hover"
                      >
                        查看分集
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <h2 className="mb-3 text-sm font-medium text-ink-dim">项目</h2>
            {isLoading && <p className="text-sm text-ink-dim">加载中…</p>}
            {!isLoading && projects.length === 0 && (
              <p className="rounded border border-line bg-surface p-6 text-center text-sm text-ink-dim">
                暂无独立项目（系列剧的单集在上方卡片里）→
              </p>
            )}
            <ul className="space-y-3">
              {projects.map((project) => (
                <li key={project.id} className="rounded-lg border border-line bg-surface p-4">
                  <div className="flex items-start justify-between gap-3">
                    <h3
                      className="min-w-0 truncate font-medium"
                      title={project.title}
                    >
                      {project.title}
                    </h3>
                    <span className="shrink-0 whitespace-nowrap rounded-full border border-line px-2 py-0.5 text-xs text-ink-dim">
                      {projectStatusLabel(project.status)}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-sm text-ink-dim">{project.idea}</p>
                {project.stage_stats && project.stage_stats.segments_total > 0 && (
                  <p className="mt-1 text-xs text-ink-dim">
                    段进度 {project.stage_stats.segments_done}/{project.stage_stats.segments_total}
                  </p>
                )}
                <div className="mt-3 flex items-center gap-3">
                  <a
                    className={`rounded px-3 py-1.5 text-sm ${
                      isProjectBusy(project.status)
                        ? 'border border-line text-ink hover:border-primary hover:text-primary'
                        : 'bg-primary text-white hover:bg-primary-hover'
                    }`}
                    href={`/project/${project.id}`}
                  >
                    {/* 进行中也可进入（TASK-035 修正）：生成期正是要看进度、
                        看关键帧、改提示词的时候；原先 pointer-events-none
                        把整个项目锁在门外，用户只能干等 */}
                    {isProjectBusy(project.status) ? '查看（进行中）' : '继续'}
                  </a>
                  <button
                    className="text-xs text-ink-dim hover:text-danger"
                    onClick={() => {
                      if (window.confirm('确认归档该项目？'))
                        void fetch(`/api/projects/${project.id}/commands`, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ type: 'archive_project' }),
                        })
                    }}
                  >
                    归档
                  </button>
                </div>
              </li>
              ))}
            </ul>
          </div>
        </section>

        <aside className="rounded-lg border border-line bg-surface p-4">
          <h2 className="mb-3 text-sm font-medium text-ink-dim">新建项目</h2>
          <CreateProjectForm onCreated={navigate} />
        </aside>
      </div>
    </main>
  )
}
