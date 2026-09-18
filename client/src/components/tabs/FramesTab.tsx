import { useEffect, useRef, useState } from 'react'
import {
  api,
  ApiError,
  type FrameImage,
  type FrameReferenceOption,
  type FrameSegmentContext,
  type Project,
  type Segment,
} from '../../api/client'
import { useFrames, useStoryboard } from '../../hooks'
import { dedupeShotBeats } from '../../lib/format'
import ImageLightbox from '../ImageLightbox'

type RunFn = (type: string, payload?: Record<string, unknown>) => void

const KIND_LABEL: Record<string, string> = {
  character: '角色',
  scene: '场景',
  prop: '道具',
}

function FrameCard({
  frame,
  onRun,
  onPreview,
  onUseAsBase,
}: {
  frame: FrameImage
  onRun: RunFn
  onPreview: (src: string, caption: string) => void
  onUseAsBase: (frame: FrameImage) => void
}) {
  const [showPrompt, setShowPrompt] = useState(false)
  return (
    <figure className="overflow-hidden rounded border border-line">
      {frame.url ? (
        // 框保持原来的 16:9 大小，整帧缩进去完整显示（object-contain）：关键帧是成片
        // 画幅（9:16 竖版或 16:9 横版），object-cover 会把竖版帧裁成中间一条。
        <img
          src={frame.url}
          alt={`${frame.segment_key} ${frame.view_label}`}
          className="aspect-video w-full cursor-zoom-in bg-surface-2 object-contain"
          onClick={() => frame.url && onPreview(frame.url, `${frame.segment_key} · v${frame.version_no}`)}
        />
      ) : (
        <div className="flex aspect-video items-center justify-center bg-surface-2 text-xs text-ink-dim">
          {frame.status === 'generating' ? '生成中…' : frame.status}
        </div>
      )}
      {frame.reference_urls.length > 0 && (
        <div className="flex items-center gap-1 border-t border-line bg-surface-2 px-1.5 py-1">
          <span className="text-[10px] text-ink-dim">参考</span>
          {frame.reference_urls.map((url, i) => (
            <img
              key={url}
              src={url}
              alt={`参考图 ${i + 1}`}
              className="h-6 w-6 cursor-zoom-in rounded bg-surface-2 object-contain"
              onClick={() => onPreview(url, `${frame.segment_key} · 参考图 ${i + 1}`)}
            />
          ))}
        </div>
      )}
      <figcaption className="flex items-center justify-between px-1.5 py-1 text-xs">
        <button
          type="button"
          className="min-w-0 truncate text-left text-ink-dim hover:text-primary"
          title="查看/隐藏这张图的生图提示词"
          onClick={() => setShowPrompt((v) => !v)}
        >
          v{frame.version_no} {showPrompt ? '▾' : '▸'} 提示词
        </button>
        <span className="flex shrink-0 items-center gap-1.5">
          <label className="flex cursor-pointer items-center gap-1">
            <input
              type="checkbox"
              checked={frame.approved}
              onChange={(e) =>
                onRun('approve_frame_image', {
                  frame_image_id: frame.id,
                  approved: e.target.checked,
                })
              }
            />
            批准
          </label>
          <button
            type="button"
            className="text-ink-dim hover:text-primary"
            title="把这张的提示词与参考图填进下方编辑区"
            onClick={() => onUseAsBase(frame)}
          >
            改
          </button>
          <button
            type="button"
            className="text-ink-dim hover:text-danger"
            title="删除这张关键帧（磁盘文件保留）"
            onClick={() => {
              if (window.confirm(`删除 ${frame.segment_key} v${frame.version_no} 关键帧？磁盘文件会保留。`)) {
                onRun('delete_frame_image', { frame_image_id: frame.id })
              }
            }}
          >
            删
          </button>
        </span>
      </figcaption>
      {showPrompt && (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words border-t border-line bg-surface-2 px-1.5 py-1 text-[10px] leading-relaxed text-ink-dim">
          {frame.prompt || '（无提示词记录）'}
        </pre>
      )}
    </figure>
  )
}

function SegmentFrameRow({
  project,
  segment,
  frames,
  description,
  context,
  referenceOptions,
  onRun,
  onPreview,
}: {
  project: Project
  segment: Segment
  frames: FrameImage[]
  description: string
  context?: FrameSegmentContext
  referenceOptions: FrameReferenceOption[]
  onRun: RunFn
  onPreview: (src: string, caption: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [seed, setSeed] = useState('')
  const [editing, setEditing] = useState(false)
  const [descDraft, setDescDraft] = useState(description)
  const [panelOpen, setPanelOpen] = useState(false)
  const [promptDraft, setPromptDraft] = useState('')
  const [selectedImageIds, setSelectedImageIds] = useState<string[]>([])
  const [baseVersion, setBaseVersion] = useState<number | null>(null)
  const [refsUnrecorded, setRefsUnrecorded] = useState(false)
  const [genZh, setGenZh] = useState('')
  const [genBusy, setGenBusy] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const hasApproved = frames.some((f) => f.approved)
  const latest = frames.length > 0 ? frames[frames.length - 1] : null

  const imagesByPath = new Map(referenceOptions.map((o) => [o.path, o]))
  const autoPaths = context?.auto_reference_paths ?? []

  // 资产图更新检测（TASK-048 用户反馈）：这一帧实际用的参考图与当前自动选择
  // 不一致 = 资产图换过/更新过，提示重抽（重抽会自动用最新批准的资产卡）
  const latestRefs = latest?.reference_paths ?? []
  const refsStale =
    latest != null &&
    latestRefs.length > 0 &&
    autoPaths.length > 0 &&
    [...latestRefs].sort().join() !== [...autoPaths].sort().join()

  /** 从一版帧（或自动选择）填编辑区：提示词 = 该版实际提示词，参考图 = 该版实际用的。 */
  const fillFrom = (frame: FrameImage | null) => {
    // 历史帧（"记录参考图"功能上线前生成）没有参考图记录：此时回退到本段
    // 自动选择，避免"打开面板直接重抽 → 参考图被清空变成纯文本生图"
    const recorded = frame?.reference_paths ?? []
    const paths = recorded.length > 0 ? recorded : autoPaths
    setPromptDraft(frame ? frame.prompt : '')
    setSelectedImageIds(
      paths.map((p) => imagesByPath.get(p)?.image_id).filter((v): v is string => Boolean(v)),
    )
    setBaseVersion(frame ? frame.version_no : null)
    setRefsUnrecorded(Boolean(frame) && recorded.length === 0 && autoPaths.length > 0)
    setPanelOpen(true)
  }

  useEffect(() => {
    if (panelOpen && promptDraft === '' && latest) fillFrom(latest)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelOpen, latest?.id])

  const upload = async (file: File) => {
    try {
      await api.uploadFrameImage(project.id, segment.segment_key, file)
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : '上传失败')
    }
  }

  const genExample = '换成雪夜，人物表情更紧张'

  /** 统一生成入口：框里有字 = 编辑指令智能融入当前提示词；留空 = 按最新资产图随机重生成。 */
  const generate = async () => {
    if (genBusy) return
    setGenBusy(true)
    setGenError(null)
    try {
      const request = genZh.trim()
      if (request) {
        const base = promptDraft || latest?.prompt || ''
        const result = await api.translatePrompt(project.id, request, 'edit', base)
        onRun('generate_frame_image', {
          segment_key: segment.segment_key,
          prompt: result.prompt,
        })
      } else {
        onRun('generate_frame_image', { segment_key: segment.segment_key })
      }
      setGenZh('')
    } catch (err) {
      setGenError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setGenBusy(false)
    }
  }

  const saveDescription = () => {
    onRun('edit_segment', {
      segment_key: segment.segment_key,
      segment: { ...segment, keyframe_description: descDraft.trim() },
    })
    setEditing(false)
  }

  const toggleImage = (imageId: string) => {
    setSelectedImageIds((prev) =>
      prev.includes(imageId) ? prev.filter((i) => i !== imageId) : [...prev, imageId],
    )
  }

  const regenerateWithEdits = () => {
    onRun('generate_frame_image', {
      segment_key: segment.segment_key,
      prompt: promptDraft.trim(),
      reference_asset_image_ids: selectedImageIds,
      ...(seed.trim() ? { seed: Number(seed) } : {}),
    })
    setSeed('')
  }

  return (
    <article className="rounded-lg border border-line bg-surface p-3">
      <header className="mb-2 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          {segment.segment_key}
          <span className="text-xs font-normal text-ink-dim">
            {context?.scene_id ?? segment.scene_id} · {context?.duration_sec ?? segment.duration_sec}s ·{' '}
            {segment.shots.length} 镜
          </span>
        </h3>
        <div className="flex items-center gap-2">
          <span className={`text-xs ${hasApproved ? 'text-ok' : 'text-warn'}`}>
            {hasApproved ? '已批准（作开场参考）' : '未批准（视频将回退尾帧接力）'}
          </span>
          <button
            className="text-xs text-ink-dim hover:text-primary"
            onClick={() => {
              setDescDraft(description)
              setEditing((v) => !v)
            }}
          >
            {editing ? '收起' : '改锚点描述'}
          </button>
        </div>
      </header>

      {/* 这一段戏是什么：中文剧本节拍（逐镜）+ 逐人开场位置（画面内/画面外） */}
      {(context?.shot_beats_zh?.length ||
        context?.shot_summaries?.length ||
        context?.placements?.length) && (
        <div className="mb-2 space-y-1 rounded border border-line bg-surface-2 p-2">
          {dedupeShotBeats(context?.shot_beats_zh, segment.shots.map((sh) => sh.camera)).map(
            (row, i) => (
            <div key={i} className="text-xs">
              <span className="text-ink-faint">镜{i + 1}·</span>
              {row.text ? (
                <span className="text-ink">{row.text}</span>
              ) : row.sameAsPrev ? (
                <span className="text-ink-faint">
                  （与上一镜同属一拍{row.camera ? `，景别：${row.camera}` : ''}）
                </span>
              ) : (
                <span className="text-ink-dim">（本镜未认领剧本节拍）</span>
              )}
              {context?.shot_summaries?.[i] && (
                <details className="mt-0.5">
                  <summary className="cursor-pointer text-[11px] text-ink-faint hover:text-primary">
                    英文原文（送视频提示词的动作段）
                  </summary>
                  <p className="mt-0.5 leading-relaxed text-ink-dim">
                    {context.shot_summaries[i]}
                  </p>
                </details>
              )}
            </div>
            ),
          )}
          {(context?.shot_beats_zh ?? []).length === 0 &&
            context?.shot_summaries?.map((summary, i) => (
              <p key={i} className="text-xs text-ink-dim">
                <span className="text-ink-faint">镜{i + 1}·</span>
                {summary}
              </p>
            ))}
          {context?.placements?.map((p) => (
            <p key={p.name} className="text-xs">
              <span className={p.in_frame ? 'text-ok' : 'text-ink-faint'}>
                {p.in_frame ? '画面内' : '画面外'}
              </span>{' '}
              <span className="font-medium">{p.name}</span>{' '}
              <span className="text-ink-dim">{p.placement}</span>
            </p>
          ))}
        </div>
      )}

      {editing ? (
        <div className="mb-2 space-y-1.5 rounded border border-line bg-surface-2 p-2">
          <textarea
            className="h-16 w-full rounded border border-line bg-surface px-2 py-1 text-xs"
            value={descDraft}
            placeholder="英文开场锚点描述：第 0 帧的构图/人物位置姿态/持物/光效"
            onChange={(e) => setDescDraft(e.target.value)}
          />
          <button
            className="rounded bg-primary px-3 py-1 text-xs text-white hover:bg-primary-hover"
            onClick={saveDescription}
          >
            保存描述（生成新版本时生效）
          </button>
        </div>
      ) : (
        <p className="mb-2 line-clamp-2 text-xs text-ink-dim">
          {description || '（无锚点描述——生图时回退首镜开场状态；可在分镜 Tab 或此处补充）'}
        </p>
      )}

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {frames.map((frame) => (
          <FrameCard
            key={frame.id}
            frame={frame}
            onRun={onRun}
            onPreview={onPreview}
            onUseAsBase={(f) => fillFrom(f)}
          />
        ))}
      </div>

      {refsStale && (
        <p className="mt-2 text-[11px] text-warn">
          ⚠ 资产图已更新：这一帧还是按旧资产图生成的，点下方「生成」即可用最新资产卡重做（约 40 币/张）
        </p>
      )}

      {/* 统一智能生成（TASK-048 用户反馈：像资产卡一样直接说改什么）：
          有字 = 按编辑指令智能融入当前提示词；留空 = 按最新资产图随机重生成 */}
      <div className="mt-2 rounded border border-line/60 p-2">
        <textarea
          rows={2}
          className="w-full rounded border border-line bg-surface-2 px-2 py-1 text-xs"
          placeholder={`直接说这一帧要改什么，如「${
            genExample
          }」；留空点生成 = 用最新资产图重新生成`}
          value={genZh}
          onChange={(e) => setGenZh(e.target.value)}
        />
        <div className="mt-1 flex items-center gap-2">
          <button
            className="rounded bg-primary px-3 py-1 text-xs text-white hover:bg-primary-hover disabled:opacity-40"
            disabled={genBusy}
            onClick={() => void generate()}
          >
            {genBusy ? '处理中…' : '生成'}
          </button>
          <button
            className="rounded border border-line px-2 py-0.5 text-xs hover:border-primary hover:text-primary"
            onClick={() => fileRef.current?.click()}
          >
            上传
          </button>
          <button
            className="rounded border border-line px-2 py-0.5 text-xs hover:border-primary hover:text-primary"
            onClick={() => {
              if (panelOpen) setPanelOpen(false)
              else fillFrom(latest)
            }}
          >
            {panelOpen ? '收起高级选项' : '高级：提示词/参考图/seed'}
          </button>
          <span className="text-[11px] text-ink-dim">
            中文直接说即可；留空 = 随机重生成
          </span>
          {genError && <span className="text-xs text-danger">{genError}</span>}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void upload(file)
            e.target.value = ''
          }}
        />
      </div>

      {panelOpen && (
        <div className="mt-2 space-y-2 rounded border border-primary/40 bg-surface-2 p-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium">
              生图提示词{baseVersion ? `（基于 v${baseVersion}）` : '（自动编译）'}
            </span>
            <button
              className="text-ink-dim hover:text-primary"
              onClick={() => fillFrom(null)}
              title="清空为自动编译提示词 + 自动选中的参考图"
            >
              恢复自动
            </button>
          </div>
          <textarea
            className="h-40 w-full rounded border border-line bg-surface px-2 py-1 font-mono text-[11px] leading-relaxed"
            value={promptDraft}
            placeholder="留空 = 按分镜数据自动编译（角色身份锁 + 画面人数 + 位置 + 场景锚点）"
            onChange={(e) => setPromptDraft(e.target.value)}
          />
          <p className="text-[11px] text-ink-dim">
            想用中文改画面，直接用上面的智能生成框即可；这里的手工提示词是进阶选项。
            参考图（勾选送入图生图通道；不勾 = 纯文本生图）。自动选择 = 画面内角色卡 + 场景卡。
            不在画面里的角色卡会把第二张脸也带进画面，建议不要勾。
            {refsUnrecorded && (
              <span className="text-warn">
                {' '}
                注意：这一版生成于"记录参考图"功能上线前，没有参考图记录，此处填的是本段**当前自动选择**的参考图。
              </span>
            )}
          </p>
          {(['character', 'scene', 'prop'] as const).map((kind) => {
            const options = referenceOptions.filter((o) => o.kind === kind)
            if (options.length === 0) return null
            return (
              <div key={kind} className="space-y-1">
                <p className="text-[11px] text-ink-faint">{KIND_LABEL[kind]}</p>
                <div className="flex flex-wrap gap-2">
                  {options.map((option) => {
                    const checked = selectedImageIds.includes(option.image_id)
                    const isAuto = autoPaths.includes(option.path)
                    return (
                      <label
                        key={option.image_id}
                        className={`flex w-24 cursor-pointer flex-col gap-1 rounded border p-1 text-[11px] ${
                          checked ? 'border-primary bg-primary/10' : 'border-line'
                        }`}
                      >
                        <span className="flex items-center gap-1">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleImage(option.image_id)}
                          />
                          <span className="min-w-0 truncate" title={`${option.name} · ${option.view_label}`}>
                            {option.name}
                          </span>
                        </span>
                        <img
                          src={option.url}
                          alt={option.name}
                          className="h-16 w-full cursor-zoom-in rounded bg-surface-2 object-contain"
                          onClick={(e) => {
                            e.preventDefault()
                            onPreview(option.url, `${option.name} · ${option.view_label}`)
                          }}
                        />
                        <span className="text-ink-faint">
                          {option.view_label}
                          {isAuto ? ' · 自动' : ''}
                        </span>
                      </label>
                    )
                  })}
                </div>
              </div>
            )
          })}
          <div className="flex flex-wrap items-center gap-2">
            <input
              className="w-28 rounded border border-line bg-surface px-2 py-1 text-xs"
              value={seed}
              placeholder="seed（可留空）"
              onChange={(e) => setSeed(e.target.value)}
            />
            <button
              className="rounded bg-primary px-3 py-1 text-xs text-white hover:bg-primary-hover"
              onClick={regenerateWithEdits}
            >
              用以上提示词与参考图重抽
            </button>
            <span className="text-[11px] text-ink-dim">
              已选参考图 {selectedImageIds.length} 张；生成新版本，旧版本保留可对比
            </span>
          </div>
        </div>
      )}
    </article>
  )
}

export default function FramesTab({
  project,
  onRun,
}: {
  project: Project
  onRun: RunFn
}) {
  const { data, isLoading } = useFrames(project.id)
  const { data: sbData } = useStoryboard(project.id)
  const [preview, setPreview] = useState<{ src: string; caption: string } | null>(null)
  const [allowFallback, setAllowFallback] = useState(false)

  if (isLoading) return <p className="text-sm text-ink-dim">加载中…</p>
  const frames = data?.frames ?? []
  const descriptions = data?.keyframe_descriptions ?? {}
  const context = data?.segment_context ?? {}
  const referenceOptions = data?.reference_options ?? []
  const active = sbData?.active ?? null
  const segments: Segment[] = active?.segments ?? []
  const byKey = new Map<string, FrameImage[]>()
  for (const frame of frames) {
    const list = byKey.get(frame.segment_key) ?? []
    list.push(frame)
    byKey.set(frame.segment_key, list)
  }
  const missingKeys = segments
    .filter((s) => !byKey.get(s.segment_key)?.some((f) => f.approved))
    .map((s) => s.segment_key)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-ink-dim">
          关键帧 = 每段视频的开场锚点图（第 0 帧）：生成 → 逐段批准 → 确认。已批准关键帧的视频段用它锁定开场构图、
          人物位置与风格（替代前段尾帧接力，避免尾帧模糊传染）；未批准的段在确认时需勾选「缺帧段回退尾帧接力」。
        </p>
        {segments.length > 0 && (
          <button
            className="shrink-0 rounded border border-primary/50 px-3 py-1 text-xs text-primary hover:bg-primary/10 disabled:opacity-40"
            onClick={() => {
              const cost = segments.length * 40
              if (
                window.confirm(
                  `按最新批准的资产图与当前规则，重新生成全部 ${segments.length} 段关键帧？\n` +
                    `约 ${segments.length} × 40 = ${cost} 币（以实际账单为准）；旧版本保留可对比。`,
                )
              )
                onRun('generate_keyframes')
            }}
          >
            重新生成全部关键帧
          </button>
        )}
      </div>
      {project.status === 'FRAME_READY' && missingKeys.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-warn/40 bg-warn/10 p-2 text-xs">
          <span>
            缺批准关键帧：{missingKeys.join('、')}
          </span>
          <label className="flex cursor-pointer items-center gap-1">
            <input
              type="checkbox"
              checked={allowFallback}
              onChange={(e) => setAllowFallback(e.target.checked)}
            />
            缺帧段回退尾帧接力（显式确认，不静默降级）
          </label>
          <button
            className="rounded bg-primary px-3 py-1 text-xs text-white hover:bg-primary-hover"
            onClick={() => onRun('approve_keyframes', { allow_tail_fallback: allowFallback })}
          >
            确认关键帧
          </button>
        </div>
      )}
      {segments.length === 0 ? (
        <div className="rounded border border-line bg-surface p-6 text-center">
          <p className="text-sm text-ink-dim">还没有 active 分镜。先确认分镜，再生成关键帧。</p>
        </div>
      ) : (
        <div className="space-y-3">
          {segments.map((segment) => (
            <SegmentFrameRow
              key={segment.segment_key}
              project={project}
              segment={segment}
              frames={byKey.get(segment.segment_key) ?? []}
              description={descriptions[segment.segment_key] ?? segment.keyframe_description ?? ''}
              context={context[segment.segment_key]}
              referenceOptions={referenceOptions}
              onRun={onRun}
              onPreview={(src, caption) => setPreview({ src, caption })}
            />
          ))}
        </div>
      )}
      {preview && (
        <ImageLightbox src={preview.src} caption={preview.caption} onClose={() => setPreview(null)} />
      )}
    </div>
  )
}
