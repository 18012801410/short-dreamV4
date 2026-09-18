import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, type SettingsView } from '../api/client'
import { api } from '../api/client'

type TestState = { ok: boolean; error?: string } | null

export default function SettingsPage() {
  const [form, setForm] = useState<SettingsView | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<Record<string, TestState> | null>(null)

  useEffect(() => {
    api.settings().then(setForm).catch((err) => setError(String(err)))
  }, [])

  const save = () => {
    if (!form) return
    setError(null)
    setMessage(null)
    api
      .saveSettings(form)
      .then((saved) => {
        setForm(saved)
        setMessage('已保存')
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
  }

  const test = () => {
    setTesting(true)
    setTestResult(null)
    api
      .testSettings()
      .then(setTestResult)
      .finally(() => setTesting(false))
  }

  if (!form) return <main className="p-10 text-ink-dim">{error ?? '加载中…'}</main>

  const field = (
    label: string,
    key: keyof SettingsView,
    options?: { password?: boolean; mono?: boolean },
  ) => (
    <label className="block space-y-1">
      <span className="text-xs text-ink-dim">{label}</span>
      <input
        className={`w-full rounded border border-line bg-surface-2 px-3 py-2 text-sm ${options?.mono ? 'font-mono' : ''}`}
        type={options?.password ? 'password' : 'text'}
        value={String(form[key] ?? '')}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
      />
    </label>
  )

  return (
    <main className="mx-auto max-w-2xl space-y-6 px-8 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">设置</h1>
        <Link to="/" className="text-sm text-ink-dim hover:text-ink">
          ← 返回
        </Link>
      </header>

      <section className="space-y-3 rounded-lg border border-line bg-surface p-5">
        <h2 className="text-sm font-medium text-ink-dim">LLM（剧本/资产/分镜）</h2>
        {field('Base URL', 'llm_base_url', { mono: true })}
        {field('模型', 'llm_model', { mono: true })}
        {field('API Key', 'llm_api_key', { password: true, mono: true })}
      </section>

      <section className="space-y-3 rounded-lg border border-line bg-surface p-5">
        <h2 className="text-sm font-medium text-ink-dim">RunningHub（生图 + H3 视频工作流）</h2>
        {field('API Key', 'runninghub_api_key', { password: true, mono: true })}
        <label className="block space-y-1">
          <span className="text-xs text-ink-dim">图片生成并发（默认 2）</span>
          <input
            type="number"
            min={1}
            max={12}
            className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-sm"
            value={form.image_concurrency}
            onChange={(e) =>
              setForm({ ...form, image_concurrency: Number(e.target.value) || 1 })
            }
          />
          <span className="block text-[11px] text-ink-dim">
            同时生成的图/关键帧数量。调到与 RunningHub 账号的并发额度一致可避免
            「队列满被拒 → 等 2 分钟重试」；改完点保存立即生效，无需重启。
          </span>
        </label>
        <p className="text-xs text-ink-dim">
          图像模型 {form.image_model} · 视频模型 {form.video_model}
        </p>
      </section>

      <section className="space-y-3 rounded-lg border border-line bg-surface p-5">
        <h2 className="text-sm font-medium text-ink-dim">本地环境</h2>
        {field('ffmpeg 路径', 'ffmpeg_path', { mono: true })}
      </section>

      {error && <p className="text-sm text-danger">{error}</p>}
      {message && <p className="text-sm text-ok">{message}</p>}

      <div className="flex items-center gap-3">
        <button
          className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover"
          onClick={save}
        >
          保存
        </button>
        <button
          className="rounded border border-line px-4 py-2 text-sm hover:border-primary hover:text-primary"
          disabled={testing}
          onClick={() => void test()}
        >
          {testing ? '测试中…' : '测试连接'}
        </button>
        {testResult && (
          <span className="text-xs">
            {(['llm', 'image', 'video'] as const).map((key) => (
              <span
                key={key}
                className={`mr-3 ${testResult[key]?.ok ? 'text-ok' : 'text-danger'}`}
                title={testResult[key]?.error}
              >
                {key.toUpperCase()} {testResult[key]?.ok ? '✓' : '✗'}
              </span>
            ))}
          </span>
        )}
      </div>
      <p className="text-xs text-ink-dim">
        Key 保存后仅本机可见，界面回显打码（***后四位）。视频生成按 RunningHub 实际用量计费。
      </p>
    </main>
  )
}
