import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import ScriptTab from '../components/tabs/ScriptTab'
import AssetsTab from '../components/tabs/AssetsTab'
import StoryboardTab from '../components/tabs/StoryboardTab'
import FramesTab from '../components/tabs/FramesTab'
import VideoTab from '../components/tabs/VideoTab'
import FilmTab from '../components/tabs/FilmTab'
import StageNav from '../components/StageNav'
import TaskCenter from '../components/TaskCenter'
import { gateFor, nextAutoStep, regenerateFor } from '../lib/pipeline'
import { isProjectBusy, useCommand, useProject } from '../hooks'

const TABS = [
  { key: 'script', label: '剧本' },
  { key: 'assets', label: '资产' },
  { key: 'storyboard', label: '分镜' },
  { key: 'frame', label: '关键帧' },
  { key: 'video', label: '视频' },
  { key: 'film', label: '成片' },
] as const

type TabKey = (typeof TABS)[number]['key']

function TabButton({ tab, current, onClick }: { tab: string; current: TabKey; onClick: () => void }) {
  return (
    <button
      role="tab"
      aria-selected={current === tab}
      className={`px-4 py-2 text-sm ${
        current === tab ? 'border-b-2 border-primary text-primary' : 'text-ink-dim hover:text-ink'
      }`}
      onClick={onClick}
    >
      {tab}
    </button>
  )
}

export default function WorkbenchPage() {
  const { projectId = '' } = useParams()
  const { data: project, isLoading, error } = useProject(projectId)
  const command = useCommand(projectId)
  const [tab, setTab] = useState<TabKey>('script')
  const [actionError, setActionError] = useState<string | null>(null)

  if (isLoading) return <main className="p-10 text-ink-dim">加载中…</main>
  if (error || !project)
    return (
      <main className="p-10">
        <p className="text-danger">项目加载失败</p>
        <Link to="/" className="text-primary">← 返回项目列表</Link>
      </main>
    )

  const gate = gateFor(project.status)
  const regen = regenerateFor(project.status)
  const auto = nextAutoStep(project.status)
  const busy = isProjectBusy(project.status)

  const run = (type: string, payload?: Record<string, unknown>) => {
    setActionError(null)
    command.mutate(
      { type, payload },
      {
        onError: (err) =>
          setActionError(err instanceof ApiError ? `${err.code}: ${err.message}` : String(err)),
      },
    )
  }

  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center gap-4 border-b border-line bg-surface px-4 py-2">
        <Link to="/" className="text-sm text-ink-dim hover:text-ink">
          ← 项目列表
        </Link>
        <h1 className="text-base font-medium">{project.title}</h1>
        <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-dim">
          {project.status}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {auto && !busy && (
            <button
              className="rounded border border-line px-3 py-1 text-sm hover:border-primary hover:text-primary"
              onClick={() =>
                run(auto.command, auto.command === 'produce_video' ? { scope: 'all' } : undefined)
              }
            >
              {auto.label}
            </button>
          )}
          <Link to="/settings" className="text-sm text-ink-dim hover:text-ink">
            设置
          </Link>
        </div>
      </header>

      {actionError && (
        <p className="border-b border-danger/40 bg-danger/10 px-4 py-1.5 text-sm text-danger">
          {actionError}
        </p>
      )}

      <div className="flex min-h-0 flex-1">
        <aside className="w-44 shrink-0 border-r border-line bg-surface p-4">
          <StageNav status={project.status} />
          <div className="mt-6 space-y-2 border-t border-line pt-4">
            {gate && (
              <button
                className="w-full rounded bg-primary px-3 py-2 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-40"
                disabled={command.isPending}
                onClick={() => run(gate.command)}
              >
                {gate.label}
              </button>
            )}
            {regen && !busy && (
              <button
                className="w-full rounded border border-line px-3 py-2 text-sm text-ink-dim hover:border-primary hover:text-primary"
                onClick={() => run(regen.command)}
              >
                {regen.label}
              </button>
            )}
          </div>
        </aside>

        <section className="min-w-0 flex-1 overflow-y-auto">
          <div role="tablist" className="flex border-b border-line bg-surface px-4">
            {TABS.map(({ key, label }) => (
              <TabButton key={key} tab={label} current={tab} onClick={() => setTab(key)} />
            ))}
          </div>
          <div className="p-6">
            {tab === 'script' && <ScriptTab project={project} onRun={run} />}
            {tab === 'assets' && <AssetsTab project={project} onRun={run} />}
            {tab === 'storyboard' && <StoryboardTab project={project} onRun={run} />}
            {tab === 'frame' && <FramesTab project={project} onRun={run} />}
            {tab === 'video' && <VideoTab project={project} onRun={run} />}
            {tab === 'film' && <FilmTab project={project} onRun={run} />}
          </div>
        </section>

        <TaskCenter pid={projectId} />
      </div>
    </main>
  )
}
