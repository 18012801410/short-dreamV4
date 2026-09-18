import { useState } from 'react'
import { ApiError, type PictureRef, type Project, type Segment } from '../../api/client'
import { useStoryboard } from '../../hooks'
import { dedupeShotBeats } from '../../lib/format'
import ImageLightbox from '../ImageLightbox'

type RunFn = (type: string, payload?: Record<string, unknown>) => void
type PreviewFn = (src: string, caption: string) => void

const H3_MAX = 7000

function PictureThumb({
  picture,
  onPreview,
}: {
  picture: PictureRef
  onPreview: PreviewFn
}) {
  const isTail = picture.kind === 'tail_frame'
  const name =
    isTail
      ? picture.label || '前段尾帧'
      : `${picture.asset_name ?? picture.asset_id}${picture.view_label ? ` · ${picture.view_label}` : ''}`
  return (
    <figure
      className="w-20 shrink-0"
      title={
        isTail
          ? `承接 ${picture.source_segment_key} 结尾，作为 <Picture ${picture.picture_no}>（0.00s 开场）`
          : `<Picture ${picture.picture_no}>：${name}${picture.usage_note ? `（${picture.usage_note}）` : ''}`
      }
    >
      <div className="relative">
        {picture.url && !picture.missing ? (
          <img
            src={picture.url}
            alt={`Picture ${picture.picture_no}: ${name}`}
            className="aspect-video w-full cursor-zoom-in rounded border border-line bg-surface-2 object-contain"
            onClick={() => picture.url && onPreview(picture.url, `P${picture.picture_no} ${name}`)}
          />
        ) : (
          <div
            className={`flex aspect-video w-full items-center justify-center rounded border text-center text-[10px] leading-tight ${
              picture.missing ? 'border-danger/50 text-danger' : 'border-line bg-surface-2 text-ink-dim'
            }`}
          >
            {picture.missing ? picture.reason || '缺失' : '生成中…'}
          </div>
        )}
        <span
          className={`absolute left-1 top-1 rounded px-1 text-[10px] font-mono ${
            isTail ? 'bg-primary text-white' : 'bg-black/60 text-white'
          }`}
        >
          P{picture.picture_no}
        </span>
        {isTail && (
          <span className="absolute bottom-1 left-1 rounded bg-primary/90 px-1 text-[10px] text-white">
            尾帧 0.00s
          </span>
        )}
      </div>
      <figcaption className="mt-0.5 truncate text-[10px] text-ink-dim">{name}</figcaption>
    </figure>
  )
}

function shotWindows(segment: Segment) {
  const bounds = [...segment.shots.map((s) => s.cutpoint_sec), segment.duration_sec]
  return segment.shots.map((shot, i) => ({
    shot,
    index: i,
    start: bounds[i],
    end: bounds[i + 1],
  }))
}

function MiniThumbs({
  pictures,
  onPreview,
}: {
  pictures: PictureRef[]
  onPreview: PreviewFn
}) {
  const shown = pictures.filter((p) => !p.missing).slice(0, 6)
  return (
    <span className="flex items-center gap-1">
      {shown.map((picture) => (
        <img
          key={`${picture.kind}-${picture.picture_no}-${picture.asset_id ?? ''}`}
          src={picture.url ?? undefined}
          alt={`P${picture.picture_no}`}
          title={`P${picture.picture_no}${picture.kind === 'tail_frame' ? ' 前段尾帧·0.00s' : ` ${picture.asset_name ?? ''}`}（点击放大）`}
          className={`h-8 w-12 cursor-zoom-in rounded border bg-surface-2 object-contain ${
            picture.kind === 'tail_frame' ? 'border-primary' : 'border-line'
          }`}
          onClick={(e) => {
            e.preventDefault() // 不触发 <details> 折叠
            e.stopPropagation()
            if (picture.url) onPreview(picture.url, `P${picture.picture_no} ${picture.asset_name ?? '尾帧'}`)
          }}
        />
      ))}
      {pictures.length > shown.length && (
        <span className="text-xs text-ink-dim">+{pictures.length - shown.length}</span>
      )}
    </span>
  )
}

function SegmentRow({
  segment,
  onRun,
  onPreview,
}: {
  segment: Segment
  onRun: RunFn
  onPreview: PreviewFn
}) {
  const [text, setText] = useState(segment.h3_prompt.text)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const refMissing = segment.resolution_status?.status === 'ref_missing'
  const references = segment.references
  const pictures = references?.pictures ?? []
  const numberingBad = references ? !references.numbering_ok : false
  const hasAnyMissing = pictures.some((p) => p.missing)

  const save = () => {
    setError(null)
    try {
      onRun('edit_segment', {
        segment_key: segment.segment_key,
        segment: {
          ...segment,
          h3_prompt: { ...segment.h3_prompt, text },
        },
      })
      setDirty(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }

  const resetPrompt = () => {
    setError(null)
    // 恢复自动编译（TASK-040）：服务端按结构化数据重编译正文并清掉人工覆盖标记
    onRun('edit_segment', {
      segment_key: segment.segment_key,
      reset_prompt: true,
      segment: { ...segment, h3_prompt: { ...segment.h3_prompt, manual_override: false } },
    })
    setDirty(false)
  }

  return (
    <details className="rounded border border-line bg-surface">
      <summary className="flex cursor-pointer flex-wrap items-center gap-3 px-3 py-2 text-sm">
        <span className="font-mono">{segment.segment_key}</span>
        <span className="text-ink-dim">{segment.duration_sec}s</span>
        <span className="text-ink-dim">{segment.shots.length} 镜</span>
        {pictures.length > 0 && <MiniThumbs pictures={pictures} onPreview={onPreview} />}
        <span className="text-ink-dim">
          参考图 {references ? `${pictures.length} 张` : '—'}
        </span>
        {segment.continuity.enabled && (
          <span className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-ink-dim">
            续 {segment.continuity.with_prev_segment_key || '前段'}
          </span>
        )}
        {(refMissing || hasAnyMissing) && (
          <span className="rounded border border-danger/40 px-1.5 py-0.5 text-xs text-danger">
            缺参考图
          </span>
        )}
        {numberingBad && (
          <span
            className="rounded border border-warn/50 px-1.5 py-0.5 text-xs text-warn"
            title={`提示词里的 <Picture> 编号（${references?.mentioned.join(', ') || '无'}）与实际参考图张数 ${references?.expected_count} 不一致——人物会绑错参考图导致变脸，请重新生成分镜或改写提示词编号`}
          >
            ⚠ 编号错位
          </span>
        )}
        {segment.h3_prompt.text.length > H3_MAX && (
          <span className="text-xs text-danger">提示词超长</span>
        )}
      </summary>
      <div className="space-y-2 border-t border-line px-3 py-2">
        {pictures.length > 0 && (
          <div>
            <p className="mb-1 text-xs text-ink-dim">
              实际参考图（按送入 H3 的顺序 = 提示词 &lt;Picture N&gt; 编号）：
            </p>
            <div className="flex flex-wrap gap-2">
              {pictures.map((picture) => (
                <PictureThumb
                  key={`${picture.kind}-${picture.picture_no}-${picture.asset_id ?? ''}`}
                  picture={picture}
                  onPreview={onPreview}
                />
              ))}
            </div>
          </div>
        )}
        <div>
          <p className="mb-1 text-xs text-ink-dim">动作时间线（每镜起止 = 分镜切点，末镜到段尾）：</p>
          <div className="mb-2 flex h-6 w-full overflow-hidden rounded border border-line">
            {shotWindows(segment).map(({ shot, start, end }) => (
              <div
                key={shot.shot_no}
                title={`#${shot.shot_no} ${start.toFixed(1)}–${end.toFixed(1)}s：${shot.description}`}
                className="flex items-center justify-center border-r border-line bg-surface-2 text-[10px] text-ink-dim last:border-r-0"
                style={{ width: `${((end - start) / segment.duration_sec) * 100}%` }}
              >
                #{shot.shot_no} {end - start >= 2 ? `${start.toFixed(1)}-${end.toFixed(1)}s` : ''}
              </div>
            ))}
          </div>
          {shotWindows(segment).map(({ shot, start, end, index: shotIndex }) => (
            <div key={shot.shot_no} className="mb-1 text-xs text-ink-dim">
              <span className="font-mono text-ink">
                {start.toFixed(1)}–{end.toFixed(1)}s
              </span>{' '}
              <span className="font-mono">#{shot.shot_no}</span> {shot.camera || '—'}
              {(() => {
                const rows = dedupeShotBeats(
                  segment.shot_beats_zh,
                  segment.shots.map((sh) => sh.camera),
                )
                const row = rows[shotIndex]
                if (!row) {
                  return <span className="block pl-6 text-ink">{shot.description}</span>
                }
                if (row.text) {
                  return <span className="block pl-6 leading-relaxed text-ink">{row.text}</span>
                }
                if (row.sameAsPrev) {
                  return (
                    <span className="block pl-6 leading-relaxed text-ink-faint">
                      （与上一镜同属一拍{row.camera ? `，景别：${row.camera}` : ''}）
                    </span>
                  )
                }
                return <span className="block pl-6 text-ink">{shot.description}</span>
              })()}
              {shot.action && (
                <details className="pl-6">
                  <summary className="cursor-pointer text-[11px] text-ink-faint hover:text-primary">
                    英文原文（送视频提示词的动作段）
                  </summary>
                  <span className="block leading-relaxed text-ink-dim">{shot.action}</span>
                </details>
              )}
              {shot.dialogue_refs.map((d, i) => (
                <span key={i} className="block pl-6 text-ink">
                  {d.speaker}：{d.line}
                  {d.tone && <span className="text-ink-dim">（{d.tone}）</span>}
                </span>
              ))}
            </div>
          ))}
        </div>
        <div>
          <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="flex items-center gap-2 text-ink-dim">
              H3 提示词（六段结构）
              {segment.prompt_view === 'compiled' && !segment.h3_prompt.manual_override && (
                <span
                  className="rounded bg-surface-2 px-1.5 py-0.5 text-ink-faint"
                  title="显示的是按当前分镜数据实时编译的正文——与产视频时送给 H3 的完全一致。直接编辑即转为人工覆盖。"
                >
                  实时编译
                </span>
              )}
              {segment.h3_prompt.manual_override && (
                <span
                  className="rounded border border-warn/50 px-1.5 py-0.5 text-warn"
                  title="这段正文已被人工改过：产视频时直接用它，不再被编译器重编译覆盖。点右侧「恢复自动编译」可交回编译器。"
                >
                  人工覆盖（生成时不再重编译）
                </span>
              )}
            </span>
            <span className="flex items-center gap-2">
              {segment.h3_prompt.manual_override && (
                <button
                  className="rounded border border-line px-2 py-0.5 hover:border-primary hover:text-primary"
                  title="按当前分镜数据重新编译这段正文，并清除人工覆盖标记"
                  onClick={resetPrompt}
                >
                  恢复自动编译
                </button>
              )}
              <span className={text.length > H3_MAX ? 'text-danger' : 'text-ink-dim'}>
                {text.length}/{H3_MAX}
              </span>
            </span>
          </div>
          <textarea
            className="h-56 w-full rounded border border-line bg-surface-2 p-2 font-mono text-xs"
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              setDirty(true)
            }}
          />
          {error && <p className="mt-1 text-xs text-danger">{error}</p>}
          <div className="mt-1 flex items-center gap-2">
            <button
              className="rounded border border-line px-3 py-1 text-xs hover:border-primary hover:text-primary disabled:opacity-40"
              disabled={!dirty}
              onClick={save}
            >
              保存段修改
            </button>
            <span className="text-[11px] text-ink-dim">
              直接改这段正文即视为人工覆盖（保存后标记为人工覆盖，生成时以你的版本为准）
            </span>
          </div>
        </div>
      </div>
    </details>
  )
}

export default function StoryboardTab({
  project,
  onRun,
}: {
  project: Project
  onRun: RunFn
}) {
  const { data, isLoading } = useStoryboard(project.id)
  const [preview, setPreview] = useState<{ src: string; caption: string } | null>(null)
  if (isLoading) return <p className="text-sm text-ink-dim">加载中…</p>
  const version = data?.draft ?? data?.active ?? null
  if (!version) {
    return (
      <div className="rounded border border-line bg-surface p-6 text-center text-sm text-ink-dim">
        还没有分镜。确认资产后生成分镜。
      </div>
    )
  }
  return (
    <div className="space-y-3">
      <p className="text-xs text-ink-dim">
        {version.status === 'draft' ? '草稿' : '已确认'} v{version.version_no} · {version.segments.length} 段 ·
        点击段展开参考图、镜头与提示词编辑。连续段的前段尾帧固定是 &lt;Picture 1&gt;（0.00s 开场），
        资产图从 &lt;Picture 2&gt; 起编号。
      </p>
      <div className="space-y-2">
        {version.segments.map((segment) => (
          <SegmentRow
            key={segment.segment_key}
            segment={segment}
            onRun={onRun}
            onPreview={(src, caption) => setPreview({ src, caption })}
          />
        ))}
      </div>
      {preview && (
        <ImageLightbox src={preview.src} caption={preview.caption} onClose={() => setPreview(null)} />
      )}
    </div>
  )
}
