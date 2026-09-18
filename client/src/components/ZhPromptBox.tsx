import { useState } from 'react'
import { ApiError, api } from '../api/client'

/** 中文改动要求 → 智能融入现有提示词（按 Qwen-Image 官方规则；不会写提示词直接说想改什么即可）。 */
export default function ZhPromptBox({
  projectId,
  mode = 't2i',
  currentPrompt = '',
  onResult,
  hint = '不会写提示词没关系：直接说想改什么（如「场景中有2个桌子」），智能融入现有提示词',
}: {
  projectId: string
  /** t2i = 文生图公式（默认）；edit = Qwen-Image-Edit 编辑指令（关键帧用） */
  mode?: 't2i' | 'edit'
  /** 现有提示词：提供后走「智能修改」（只改要求的部分，其余保留） */
  currentPrompt?: string
  onResult: (englishPrompt: string) => void
  hint?: string
}) {
  const [zh, setZh] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const hasBase = currentPrompt.trim().length > 0

  const translate = async () => {
    if (!zh.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.translatePrompt(projectId, zh.trim(), mode, currentPrompt)
      onResult(result.prompt)
      setZh('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-1 space-y-1">
      <textarea
        rows={2}
        className="w-full rounded border border-line bg-surface-2 px-2 py-1 text-xs"
        placeholder={hint}
        value={zh}
        onChange={(e) => setZh(e.target.value)}
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded border border-primary/50 px-2 py-0.5 text-xs text-primary hover:bg-primary/10 disabled:opacity-40"
          disabled={busy || !zh.trim()}
          onClick={() => void translate()}
        >
          {busy ? '处理中…' : hasBase ? '把我的修改融入提示词' : '生成英文提示词'}
        </button>
        <span className="text-[11px] text-ink-dim">
          {hasBase ? '只改你说的地方，其余原样保留' : '也可以直接在上方输入框用中文'}
        </span>
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>
    </div>
  )
}
