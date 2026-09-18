import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, type Series, type SeriesOutline } from '../api/client'
import {
  useCreateSeries,
  useSeries,
  useSeriesCommand,
  useSeriesList,
} from '../hooks'

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

export function seriesStatusLabel(status: string): string {
  const map: Record<string, string> = {
    created: '已创建',
    outline_drafting: '大纲生成中',
    outline_ready: '大纲待确认',
    outline_approved: '大纲已确认',
  }
  return map[status] ?? status
}

function CreateSeriesForm({ onCreated }: { onCreated: (sid: string) => void }) {
  const create = useCreateSeries()
  const [idea, setIdea] = useState('')
  const [genre, setGenre] = useState('')
  const [tone, setTone] = useState<'hook' | 'three_act'>('hook')
  const [episodeCount, setEpisodeCount] = useState(12)
  const [perEpisodeSec, setPerEpisodeSec] = useState(90)
  const [sceneCount, setSceneCount] = useState(4)
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    setError(null)
    create.mutate(
      {
        title: idea.trim().slice(0, 20) || '未命名系列',
        idea,
        params: {
          genre,
          dramatic_tone: tone,
          episode_count: Math.max(1, episodeCount),
          per_episode_sec: Math.max(30, perEpisodeSec),
          scene_count: Math.max(1, sceneCount),
        },
      },
      {
        onSuccess: (result) => onCreated(result.series.id),
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
      <textarea
        className="h-24 w-full rounded border border-line bg-surface-2 px-3 py-2 text-sm"
        placeholder="一句话想法：例如「被丈母娘赶出门的赘婿，其实是集团失散多年的继承人」"
        value={idea}
        onChange={(e) => setIdea(e.target.value)}
        required
      />
      <input
        className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-sm"
        placeholder="题材（可空：按大众口味自选，如 战神归来/甜宠/复仇打脸）"
        value={genre}
        onChange={(e) => setGenre(e.target.value)}
      />
      <div>
        <p className="mb-1 text-xs text-ink-dim">剧作基调</p>
        <div className="grid grid-cols-2 gap-2">
          {(
            [
              { value: 'hook', label: '短剧钩子驱动', hint: '爽剧·快节奏·强反转' },
              { value: 'three_act', label: '微电影三幕式', hint: '治愈·情感·慢节奏' },
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
      <div className="grid grid-cols-3 gap-2">
        <label className="text-xs text-ink-dim">
          集数
          <input
            type="number"
            min={1}
            max={100}
            className="mt-1 w-full rounded border border-line bg-surface-2 px-2 py-1.5 text-sm text-ink"
            value={episodeCount}
            onChange={(e) => setEpisodeCount(Number(e.target.value))}
          />
        </label>
        <label className="text-xs text-ink-dim">
          每集秒数
          <input
            type="number"
            min={30}
            max={300}
            className="mt-1 w-full rounded border border-line bg-surface-2 px-2 py-1.5 text-sm text-ink"
            value={perEpisodeSec}
            onChange={(e) => setPerEpisodeSec(Number(e.target.value))}
          />
        </label>
        <label className="text-xs text-ink-dim">
          单集场景数
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
      <p className="text-[11px] text-ink-dim">
        大纲与分集剧本生成都走 LLM、不花钱；后续每集出片才消耗 RunningHub 币。
      </p>
      {error && <p className="text-xs text-danger">{error}</p>}
      <button
        type="submit"
        className="w-full rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover disabled:opacity-40"
        disabled={create.isPending || !idea.trim()}
      >
        {create.isPending ? '创建中…' : '创建系列'}
      </button>
    </form>
  )
}

function EpisodeCard({
  episode,
  onChange,
}: {
  episode: SeriesOutline['episodes'][number]
  onChange: (patch: Partial<SeriesOutline['episodes'][number]>) => void
}) {
  const field = (
    label: string,
    key: 'title' | 'opening_hook' | 'synopsis' | 'highlight' | 'ending_hook',
    rows = 1,
  ) => (
    <label className="block text-xs text-ink-dim">
      {label}
      {rows === 1 ? (
        <input
          className="mt-0.5 w-full rounded border border-line bg-surface-2 px-2 py-1 text-sm text-ink"
          value={episode[key]}
          onChange={(e) => onChange({ [key]: e.target.value })}
        />
      ) : (
        <textarea
          rows={rows}
          className="mt-0.5 w-full rounded border border-line bg-surface-2 px-2 py-1 text-sm text-ink"
          value={episode[key]}
          onChange={(e) => onChange({ [key]: e.target.value })}
        />
      )}
    </label>
  )
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <p className="mb-2 text-xs font-medium text-primary">第 {episode.episode_no} 集</p>
      <div className="space-y-2">
        {field('集标题', 'title')}
        {field('开场钩子（前 3 秒）', 'opening_hook')}
        {field('本集梗概', 'synopsis', 3)}
        {field('本集爽点', 'highlight')}
        {field('集尾卡点', 'ending_hook')}
      </div>
    </div>
  )
}

function SeriesDetail({ series }: { series: Series }) {
  const command = useSeriesCommand(series.id)
  const { data: fetched } = useSeries(series.id)
  const current = fetched?.series ?? series
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<SeriesOutline | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // 服务端数据刷新后同步编辑草稿（避免输入中被轮询覆盖：仅在未编辑时同步）
  useEffect(() => {
    if (!editing && current.outline) {
      setDraft(JSON.parse(JSON.stringify(current.outline)) as SeriesOutline)
    }
  }, [current.outline, editing])

  const run = async (type: string, payload?: Record<string, unknown>, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return
    setError(null)
    setNotice(null)
    try {
      const result = await command.mutateAsync({ type, payload })
      if (type === 'inherit_series_assets' && result.copied_assets !== undefined) {
        setNotice(`已复制 ${result.copied_assets} 张资产卡（含 ${result.copied_images} 张图），零成本`)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }

  const status = current.status
  const outline = current.outline
  const episodes = current.episodes ?? []
  const missing = episodes.filter((e) => !e.project)

  return (
    <section className="space-y-4">
      <header className="rounded-lg border border-line bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold">{outline?.title ?? current.title}</h2>
            <p className="mt-0.5 text-sm text-ink-dim">{current.idea}</p>
          </div>
          <div className="flex items-center gap-2">
            <StatusChip ok={status === 'outline_approved'} label={seriesStatusLabel(status)} />
            {status === 'outline_drafting' && (
              <span className="text-xs text-ink-dim">生成中，页面会自动刷新…</span>
            )}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {(status === 'created' || status === 'outline_ready') && (
            <button
              className="rounded bg-primary px-3 py-1.5 text-sm text-white hover:bg-primary-hover disabled:opacity-40"
              disabled={command.isPending}
              onClick={() =>
                void run(
                  'generate_series_outline',
                  {},
                  outline ? '重新生成会覆盖当前大纲（含已编辑内容），继续？' : undefined,
                )
              }
            >
              {outline ? '重新生成大纲' : '生成大纲'}
            </button>
          )}
          {status === 'outline_ready' && outline && (
            <>
              <button
                className="rounded bg-primary px-3 py-1.5 text-sm text-white hover:bg-primary-hover disabled:opacity-40"
                disabled={command.isPending}
                onClick={() => void run('approve_series_outline')}
              >
                确认大纲
              </button>
              <button
                className="rounded border border-line px-3 py-1.5 text-sm text-ink hover:border-primary hover:text-primary"
                onClick={() => setEditing((v) => !v)}
              >
                {editing ? '退出编辑' : '编辑大纲'}
              </button>
            </>
          )}
          {status === 'outline_approved' && (
            <button
              className="rounded bg-primary px-3 py-1.5 text-sm text-white hover:bg-primary-hover disabled:opacity-40"
              disabled={command.isPending || missing.length === 0}
              onClick={() => void run('generate_episode_scripts')}
            >
              {missing.length === 0
                ? '全部分集已建档'
                : `生成分集剧本（${missing.length} 集待生成）`}
            </button>
          )}
        </div>
        {outline && outline.warnings.length > 0 && (
          <div className="mt-3 rounded border border-danger/40 bg-danger/10 p-2.5 text-xs">
            <p className="font-medium text-danger">质量门提示（确认前请先修订硬性问题）</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-ink-dim">
              {outline.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
        {error && <p className="mt-2 text-xs text-danger">{error}</p>}
        {notice && <p className="mt-2 text-xs text-ok">{notice}</p>}
      </header>

      {outline && (
        <section className="rounded-lg border border-line bg-surface p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-sm font-medium">
              一句话主线：{outline.logline}
            </h3>
            <p className="text-xs text-ink-dim">
              题材：{outline.genre_tags.join('、') || '—'} · 大爆点：第{' '}
              {outline.climax_episode || '—'} 集
            </p>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {outline.characters.map((c) => (
              <span
                key={c.name}
                title={c.profile}
                className="rounded-full border border-line px-2.5 py-0.5 text-xs text-ink-dim"
              >
                {c.name}
              </span>
            ))}
          </div>
        </section>
      )}

      {editing && draft ? (
        <section className="space-y-3">
          <div className="flex justify-end">
            <button
              className="rounded bg-primary px-3 py-1.5 text-sm text-white hover:bg-primary-hover disabled:opacity-40"
              disabled={command.isPending}
              onClick={async () => {
                await run('edit_series_outline', { outline: draft })
                setEditing(false)
              }}
            >
              保存大纲（自动重跑质量门）
            </button>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {draft.episodes.map((ep, i) => (
              <EpisodeCard
                key={ep.episode_no}
                episode={ep}
                onChange={(patch) =>
                  setDraft((d) => {
                    if (!d) return d
                    const eps = d.episodes.map((e, idx) =>
                      idx === i ? { ...e, ...patch } : e,
                    )
                    return { ...d, episodes: eps }
                  })
                }
              />
            ))}
          </div>
        </section>
      ) : (
        outline && (
          <section className="space-y-2">
            {episodes.map((row) => (
              <article key={row.episode_no} className="rounded-lg border border-line bg-surface p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="text-sm font-medium">
                    第 {row.episode_no} 集 · {row.title}
                  </h4>
                  <div className="flex items-center gap-2">
                    {row.project ? (
                      <>
                        <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-dim">
                          {row.project.status}
                        </span>
                        <Link
                          className="rounded bg-primary px-2.5 py-1 text-xs text-white hover:bg-primary-hover"
                          to={`/project/${row.project.id}`}
                        >
                          打开工作台
                        </Link>
                        {row.episode_no > 1 && row.project.status === 'SCRIPT_APPROVED' && (
                          <button
                            className="rounded border border-line px-2.5 py-1 text-xs text-ink hover:border-primary hover:text-primary"
                            disabled={command.isPending}
                            onClick={() =>
                              void run('inherit_series_assets', {
                                from_episode_no: 1,
                                to_episode_no: row.episode_no,
                              })
                            }
                          >
                            继承第1集资产
                          </button>
                        )}
                      </>
                    ) : (
                      <span className="text-xs text-ink-dim">未建档</span>
                    )}
                  </div>
                </div>
                <p className="mt-1.5 text-xs text-primary">🎣 开场钩子：{row.opening_hook}</p>
                <p className="mt-1 text-sm text-ink-dim">{row.synopsis}</p>
                {row.highlight && (
                  <p className="mt-1 text-xs text-ok">⚡ 爽点：{row.highlight}</p>
                )}
                {row.ending_hook && (
                  <p className="mt-1 text-xs text-primary">🚪 集尾卡点：{row.ending_hook}</p>
                )}
              </article>
            ))}
          </section>
        )
      )}
    </section>
  )
}

export default function SeriesPage() {
  // 支持 /series/:sid 直达某部剧（首页系列卡「查看分集」落点）
  const { sid: paramSid } = useParams<{ sid?: string }>()
  const { data } = useSeriesList()
  const [selectedId, setSelectedId] = useState<string | null>(paramSid ?? null)
  const rows: Series[] = data?.series ?? []
  const selected = rows.find((s) => s.id === selectedId) ?? null

  return (
    <main className="mx-auto max-w-6xl px-8 py-10">
      <header className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">系列短剧</h1>
          <p className="mt-1 text-sm text-ink-dim">
            一个想法 → 全剧大纲（钩子/爽点/卡点）→ 一集一集出片；每集都是独立项目，走完整制作流水线
          </p>
        </div>
        <Link to="/" className="text-sm text-ink-dim hover:text-ink">
          返回项目
        </Link>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_2fr]">
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-ink-dim">我的系列</h2>
          {rows.length === 0 && (
            <p className="rounded border border-line bg-surface p-4 text-sm text-ink-dim">
              还没有系列，用下方表单创建第一部 →
            </p>
          )}
          <ul className="space-y-2">
            {rows.map((s) => (
              <li key={s.id}>
                <button
                  onClick={() => setSelectedId(s.id)}
                  className={`w-full rounded-lg border p-3 text-left ${
                    selectedId === s.id
                      ? 'border-primary bg-primary/5'
                      : 'border-line bg-surface hover:border-ink-dim'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{s.outline?.title ?? s.title}</span>
                    <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-dim">
                      {seriesStatusLabel(s.status)}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-1 text-xs text-ink-dim">{s.idea}</p>
                  <p className="mt-1 text-[11px] text-ink-dim">
                    {s.params.episode_count} 集 × {s.params.per_episode_sec}s · 已建档{' '}
                    {s.episode_projects ?? 0} 集
                  </p>
                </button>
              </li>
            ))}
          </ul>
          <aside className="rounded-lg border border-line bg-surface p-4">
            <h2 className="mb-3 text-sm font-medium text-ink-dim">新建系列</h2>
            <CreateSeriesForm onCreated={setSelectedId} />
          </aside>
        </section>

        {selected ? (
          <SeriesDetail series={selected} />
        ) : (
          <section className="rounded-lg border border-dashed border-line p-10 text-center text-sm text-ink-dim">
            ← 选择一个系列查看大纲与分集进度
          </section>
        )}
      </div>
    </main>
  )
}
