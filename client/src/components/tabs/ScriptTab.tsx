import { useState } from 'react'
import { type Beat, type Project, type ScriptContent } from '../../api/client'
import { useScript } from '../../hooks'

type RunFn = (type: string, payload?: Record<string, unknown>) => void

const EMPTY_SCRIPT: ScriptContent = {
  logline: '',
  scenes: [{ id: 'S1', title: '', summary: '', dialogues: [], est_seconds: 8 }],
  characters: [],
  props: [],
  warnings: [],
}

const BEAT_LABEL: Record<string, string> = {
  action: '动作',
  dialogue: '台词',
  sfx: '音效',
  on_screen_text: '画面文字',
  transition: '转场',
}

function BeatItem({ beat }: { beat: Beat }) {
  if (beat.type === 'dialogue') {
    return (
      <li>
        <span className="text-primary">{beat.speaker || '？'}：</span>
        {beat.text}
        {beat.tone && <span className="text-ink-dim">（{beat.tone}）</span>}
      </li>
    )
  }
  if (beat.type === 'action') {
    return <li className="text-ink">{beat.text}</li>
  }
  return (
    <li className="text-ink-dim">
      <span className="mr-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
        {BEAT_LABEL[beat.type] ?? beat.type}
      </span>
      {beat.text}
    </li>
  )
}

function ScriptView({ content }: { content: ScriptContent }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-dim">{content.logline}</p>
      {content.warnings.length > 0 && (
        <ul className="rounded border border-warn/40 px-3 py-2 text-xs text-warn">
          {content.warnings.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
      )}
      {content.scenes.map((scene) => (
        <details key={scene.id} className="rounded border border-line bg-surface p-3" open>
          <summary className="cursor-pointer text-sm font-medium">
            {scene.id} · {scene.title}
            <span className="ml-2 text-xs text-ink-dim">≈{scene.est_seconds}s</span>
          </summary>
          <p className="mt-2 text-sm text-ink-dim">{scene.summary}</p>
          {scene.beats && scene.beats.length > 0 ? (
            <ul className="mt-2 space-y-1.5 text-sm">
              {scene.beats.map((beat, i) => (
                <BeatItem key={i} beat={beat} />
              ))}
            </ul>
          ) : (
            <ul className="mt-2 space-y-1 text-sm">
              {scene.dialogues.map((d, i) => (
                <li key={i}>
                  <span className="text-primary">{d.speaker}：</span>
                  {d.line}
                </li>
              ))}
            </ul>
          )}
        </details>
      ))}
      {content.characters.length > 0 && (
        <p className="text-xs text-ink-dim">
          角色：{content.characters.map((c) => c.name).join('、')}
          {content.props.length > 0 ? `｜道具：${content.props.join('、')}` : ''}
        </p>
      )}
    </div>
  )
}

function ScriptEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial: ScriptContent
  onSave: (content: ScriptContent) => void
  onCancel: () => void
}) {
  const [content, setContent] = useState<ScriptContent>(() => structuredClone(initial))
  const [jsonMode, setJsonMode] = useState(false)
  const [jsonText, setJsonText] = useState(() => JSON.stringify(initial, null, 2))
  const [error, setError] = useState<string | null>(null)

  const update = (patch: Partial<ScriptContent>) => setContent((c) => ({ ...c!, ...patch }))

  const addScene = () => {
    const numbers = content.scenes.map((s) => parseInt(s.id.slice(1), 10) || 0)
    const next = (numbers.length ? Math.max(...numbers) : 0) + 1
    update({
      scenes: [
        ...content.scenes,
        { id: `S${next}`, title: '新场景', summary: '', dialogues: [], est_seconds: 8 },
      ],
    })
  }

  const save = () => {
    setError(null)
    if (jsonMode) {
      try {
        onSave(JSON.parse(jsonText) as ScriptContent)
      } catch {
        setError('JSON 解析失败，请检查格式')
      }
      return
    }
    onSave(content)
  }

  return (
    <div className="space-y-3 rounded-lg border border-primary/40 bg-surface p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-primary">编辑剧本</h3>
        <label className="flex cursor-pointer items-center gap-1 text-xs text-ink-dim">
          <input type="checkbox" checked={jsonMode} onChange={(e) => setJsonMode(e.target.checked)} />
          JSON 模式
        </label>
      </div>

      {jsonMode ? (
        <textarea
          className="h-96 w-full rounded border border-line bg-surface-2 p-2 font-mono text-xs"
          value={jsonText}
          onChange={(e) => setJsonText(e.target.value)}
        />
      ) : (
        <div className="space-y-3">
          <label className="block">
            <span className="text-xs text-ink-dim">一句话故事（logline）</span>
            <input
              className="mt-1 w-full rounded border border-line bg-surface-2 px-2 py-1 text-sm"
              value={content.logline}
              onChange={(e) => update({ logline: e.target.value })}
            />
          </label>

          {content.scenes.map((scene, si) => (
            <div key={scene.id} className="rounded border border-line bg-surface-2 p-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-ink-dim">{scene.id}</span>
                <input
                  className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 text-xs"
                  value={scene.title}
                  placeholder="场景标题"
                  onChange={(e) => {
                    const scenes = [...content.scenes]
                    scenes[si] = { ...scene, title: e.target.value }
                    update({ scenes })
                  }}
                />
                <input
                  type="number"
                  min={1}
                  className="w-16 rounded border border-line bg-surface px-2 py-1 text-xs"
                  value={scene.est_seconds}
                  title="预计秒数"
                  onChange={(e) => {
                    const scenes = [...content.scenes]
                    scenes[si] = { ...scene, est_seconds: Number(e.target.value) || 0 }
                    update({ scenes })
                  }}
                />
                <button
                  type="button"
                  className="text-xs text-ink-dim hover:text-danger"
                  onClick={() => update({ scenes: content.scenes.filter((_, i) => i !== si) })}
                >
                  删除
                </button>
              </div>
              <textarea
                className="mt-1.5 h-12 w-full rounded border border-line bg-surface px-2 py-1 text-xs"
                value={scene.summary}
                placeholder="场景概要"
                onChange={(e) => {
                  const scenes = [...content.scenes]
                  scenes[si] = { ...scene, summary: e.target.value }
                  update({ scenes })
                }}
              />
              <div className="mt-1.5 space-y-1">
                {scene.dialogues.map((d, di) => (
                  <div key={di} className="flex gap-1.5">
                    <input
                      className="w-24 rounded border border-line bg-surface px-2 py-1 text-xs"
                      value={d.speaker}
                      placeholder="说话人"
                      onChange={(e) => {
                        const scenes = [...content.scenes]
                        const dialogues = [...scene.dialogues]
                        dialogues[di] = { ...d, speaker: e.target.value }
                        scenes[si] = { ...scene, dialogues }
                        update({ scenes })
                      }}
                    />
                    <input
                      className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 text-xs"
                      value={d.line}
                      placeholder="台词"
                      onChange={(e) => {
                        const scenes = [...content.scenes]
                        const dialogues = [...scene.dialogues]
                        dialogues[di] = { ...d, line: e.target.value }
                        scenes[si] = { ...scene, dialogues }
                        update({ scenes })
                      }}
                    />
                    <button
                      type="button"
                      className="rounded border border-line px-2 text-xs text-ink-dim hover:border-danger hover:text-danger"
                      onClick={() => {
                        const scenes = [...content.scenes]
                        scenes[si] = {
                          ...scene,
                          dialogues: scene.dialogues.filter((_, i) => i !== di),
                        }
                        update({ scenes })
                      }}
                    >
                      删
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className="rounded border border-dashed border-line px-2 py-1 text-xs text-ink-dim hover:border-primary hover:text-primary"
                  onClick={() => {
                    const scenes = [...content.scenes]
                    scenes[si] = {
                      ...scene,
                      dialogues: [...scene.dialogues, { speaker: '', line: '' }],
                    }
                    update({ scenes })
                  }}
                >
                  + 加一句台词
                </button>
              </div>
            </div>
          ))}
          <button
            type="button"
            className="rounded border border-dashed border-line px-3 py-1.5 text-xs text-ink-dim hover:border-primary hover:text-primary"
            onClick={addScene}
          >
            + 加一个场景
          </button>

          <div className="flex flex-wrap gap-1.5">
            {content.characters.map((c, ci) => (
              <span key={ci} className="flex items-center gap-1 rounded bg-surface-2 px-2 py-0.5 text-xs">
                {c.name}
                <button
                  type="button"
                  className="text-ink-dim hover:text-danger"
                  onClick={() =>
                    update({ characters: content.characters.filter((_, i) => i !== ci) })
                  }
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {error && <p className="text-xs text-danger">{error}</p>}
      <div className="flex gap-2">
        <button
          className="rounded bg-primary px-4 py-1.5 text-sm text-white hover:bg-primary-hover"
          onClick={save}
        >
          保存为新草稿
        </button>
        <button
          className="rounded border border-line px-4 py-1.5 text-sm text-ink-dim hover:border-line"
          onClick={onCancel}
        >
          取消
        </button>
      </div>
      <p className="text-xs text-ink-dim">
        保存会生成新草稿版本；若剧本已确认，项目会回退到「剧本待确认」，下游分镜/视频需要重新生成。
      </p>
    </div>
  )
}

export default function ScriptTab({
  project,
  onRun,
}: {
  project: Project
  onRun: RunFn
}) {
  const { data, isLoading, error } = useScript(project.id)
  const [editingFrom, setEditingFrom] = useState<'draft' | 'active' | null>(null)

  if (isLoading) return <p className="text-sm text-ink-dim">加载中…</p>
  if (error) return <p className="text-sm text-danger">剧本加载失败</p>

  const draft = data?.draft ?? null
  const active = data?.active ?? null
  const editingSource =
    editingFrom === 'draft' ? draft?.content : editingFrom === 'active' ? active?.content : null
  const editingContent = editingFrom ? (editingSource ?? EMPTY_SCRIPT) : null

  const saveEdit = (content: ScriptContent) => {
    onRun('edit_script_draft', content as unknown as Record<string, unknown>)
    setEditingFrom(null)
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-ink-dim">剧本</h2>
      </header>

      {editingContent && (
        <ScriptEditor
          initial={editingContent}
          onSave={saveEdit}
          onCancel={() => setEditingFrom(null)}
        />
      )}

      {!draft && !active && !editingContent && (
        <div className="rounded border border-line bg-surface p-6 text-center">
          <p className="text-sm text-ink-dim">还没有剧本。</p>
          <div className="mt-3 flex justify-center gap-2">
            <button
              className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover"
              onClick={() => onRun('generate_script')}
            >
              生成剧本
            </button>
            <button
              className="rounded border border-line px-4 py-2 text-sm hover:border-primary hover:text-primary"
              onClick={() => setEditingFrom('active')}
            >
              手写剧本
            </button>
          </div>
        </div>
      )}

      {draft && (
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs text-warn">草稿 v{draft.version_no}（待确认，可继续编辑）</h3>
            {!editingContent && (
              <button
                className="rounded border border-line px-3 py-1 text-xs hover:border-primary hover:text-primary"
                onClick={() => setEditingFrom('draft')}
              >
                编辑
              </button>
            )}
          </div>
          <ScriptView content={draft.content} />
        </section>
      )}

      {active && (
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs text-ok">已确认 v{active.version_no}</h3>
            {!editingContent && (
              <button
                className="rounded border border-line px-3 py-1 text-xs hover:border-primary hover:text-primary"
                onClick={() => setEditingFrom('active')}
                title="编辑会生成新草稿，项目回退到「剧本待确认」，下游需重新生成"
              >
                编辑（重新进入草稿）
              </button>
            )}
          </div>
          <ScriptView content={active.content} />
        </section>
      )}
    </div>
  )
}
