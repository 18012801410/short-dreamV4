import { useState } from 'react'
import { type Project } from '../../api/client'
import { formatDuration } from '../../lib/format'
import { useFilms } from '../../hooks'

type RunFn = (type: string, payload?: Record<string, unknown>) => void

export default function FilmTab({
  project,
  onRun,
}: {
  project: Project
  onRun: RunFn
}) {
  const { data, isLoading } = useFilms(project.id)
  const [preferSubtitled, setPreferSubtitled] = useState(true)
  if (isLoading) return <p className="text-sm text-ink-dim">加载中…</p>
  const films = data?.films ?? []
  const latest = films[0]
  const playUrl =
    latest && preferSubtitled && latest.subtitle_url ? latest.subtitle_url : latest?.url ?? null
  const hasBoth = Boolean(latest?.url && latest.subtitle_url)

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center gap-3">
        <button
          className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover"
          disabled={
            project.status !== 'VIDEO_READY' &&
            project.status !== 'COMPOSING' &&
            project.status !== 'COMPOSED'
          }
          onClick={() => onRun('compose')}
        >
          {project.status === 'COMPOSED' ? '重新合成成片' : '合成成片'}
        </button>
        <span className="text-xs text-ink-dim">合成时会自动按分镜台词烧录字幕</span>
        {project.status === 'COMPOSING' && <span className="text-sm text-warn">合成中…</span>}
        {project.status === 'COMPOSED' && <span className="text-sm text-ok">已完成</span>}
      </div>

      {!latest || (!latest.url && latest.status !== 'failed') ? (
        <div className="rounded border border-line bg-surface p-6 text-center text-sm text-ink-dim">
          尚无成片。视频全部就绪后点「合成成片」。
        </div>
      ) : (
        <section className="space-y-3">
          {playUrl && (
            <video key={playUrl} src={playUrl} controls className="w-full rounded-lg bg-black" />
          )}
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <p className="text-ink-dim">
              v{latest.version_no} · {formatDuration(latest.duration_sec)} ·{' '}
              {latest.segment_keys.length} 段
            </p>
            {hasBoth && (
              <label className="flex cursor-pointer items-center gap-1 text-ink-dim">
                <input
                  type="checkbox"
                  checked={preferSubtitled}
                  onChange={(e) => setPreferSubtitled(e.target.checked)}
                />
                显示字幕版
              </label>
            )}
            {latest.url && (
              <a className="text-primary" href={latest.url} download>
                下载原片
              </a>
            )}
            {latest.subtitle_url && (
              <a className="text-primary" href={latest.subtitle_url} download>
                下载字幕版
              </a>
            )}
          </div>
          {latest.status === 'failed' && <p className="text-sm text-danger">{latest.error}</p>}
          {films.length > 1 && (
            <details>
              <summary className="cursor-pointer text-xs text-ink-dim">
                历史版本（{films.length - 1}）
              </summary>
              <ul className="mt-1 space-y-1 text-xs">
                {films.slice(1).map((film) => (
                  <li key={film.id} className="flex items-center gap-2 text-ink-dim">
                    <span>v{film.version_no}</span>
                    <span>{formatDuration(film.duration_sec)}</span>
                    {film.subtitle_url && (
                      <a className="text-primary" href={film.subtitle_url} download>
                        字幕版
                      </a>
                    )}
                    {film.url && (
                      <a className="text-primary" href={film.url} download>
                        原片
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}
    </div>
  )
}
