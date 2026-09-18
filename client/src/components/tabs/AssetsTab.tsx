import { useRef, useState } from 'react'
import { api, ApiError, type Asset, type Project } from '../../api/client'
import { useAssets } from '../../hooks'
import ImageLightbox from '../ImageLightbox'
import ZhPromptBox from '../ZhPromptBox'

type RunFn = (type: string, payload?: Record<string, unknown>) => void

const KIND_LABEL: Record<string, string> = { character: '角色', scene: '场景', prop: '道具' }

/** 出图风格预设：value 会拼进生图提示词，label 用于展示。 */
const STYLE_PRESETS: { label: string; value: string }[] = [
  {
    label: '3D 皮克斯风',
    value: '3D animation style, Pixar/Disney look, soft rounded shapes, exaggerated silhouettes, high-quality render',
  },
  {
    label: '写实电影感',
    value: 'cinematic realistic style, film grain, dramatic natural lighting, 35mm photography',
  },
  { label: '国漫风', value: 'Chinese animation style, guoman aesthetic, elegant linework, rich colors' },
  { label: '日漫动画风', value: 'Japanese anime style, cel shading, clean linework, vivid colors' },
  {
    label: '水墨国风',
    value: 'Chinese ink wash painting style, brush strokes, monochrome with subtle color accents',
  },
  {
    label: '赛博朋克',
    value: 'cyberpunk style, neon lights, high contrast, futuristic dystopian city mood',
  },
]

function StyleBar({ project, onRun }: { project: Project; onRun: RunFn }) {
  const current = project.params.style ?? ''
  const activePreset = STYLE_PRESETS.find((p) => p.value === current)
  const [custom, setCustom] = useState('')

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">出图风格</span>
        {current ? (
          <span className="max-w-[28rem] truncate rounded bg-surface-2 px-2 py-0.5 text-xs text-ink-dim">
            {activePreset ? activePreset.label : current}
          </span>
        ) : (
          <span className="text-xs text-warn">未设置——建议先选风格再出图（会拼进所有生图提示词）</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {STYLE_PRESETS.map((preset) => (
          <button
            key={preset.label}
            className={`rounded-full border px-3 py-1 text-xs ${
              current === preset.value
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-line text-ink-dim hover:border-primary hover:text-primary'
            }`}
            onClick={() => onRun('update_params', { style: preset.value })}
          >
            {preset.label}
          </button>
        ))}
        <input
          className="w-56 rounded border border-line bg-surface-2 px-2 py-1 text-xs"
          placeholder="自定义风格描述（中英文均可）"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
        />
        <button
          className="rounded border border-line px-2.5 py-1 text-xs hover:border-primary hover:text-primary disabled:opacity-40"
          disabled={!custom.trim()}
          onClick={() => {
            onRun('update_params', { style: custom.trim() })
            setCustom('')
          }}
        >
          应用
        </button>
        {current && (
          <button
            className="rounded px-2 py-1 text-xs text-ink-dim hover:text-danger"
            onClick={() => onRun('update_params', { style: '' })}
          >
            清除
          </button>
        )}
      </div>
    </div>
  )
}

function ImagePlanEditor({
  projectId,
  plan,
  onChange,
}: {
  projectId: string
  plan: { view_label: string; image_prompt: string }[]
  onChange: (plan: { view_label: string; image_prompt: string }[]) => void
}) {
  return (
    <div className="space-y-1.5">
      {plan.map((item, i) => (
        <div key={i} className="rounded border border-line/60 p-1.5">
          <div className="flex gap-1.5">
            <input
              className="w-28 rounded border border-line bg-surface-2 px-2 py-1 text-xs"
              value={item.view_label}
              placeholder="视图名"
              onChange={(e) => {
                const next = [...plan]
                next[i] = { ...item, view_label: e.target.value }
                onChange(next)
              }}
            />
            <input
              className="min-w-0 flex-1 rounded border border-line bg-surface-2 px-2 py-1 text-xs"
              value={item.image_prompt}
              placeholder="生图提示词（中英文均可）"
              onChange={(e) => {
                const next = [...plan]
                next[i] = { ...item, image_prompt: e.target.value }
                onChange(next)
              }}
            />
            <button
              type="button"
              className="rounded border border-line px-2 text-xs text-ink-dim hover:border-danger hover:text-danger"
              onClick={() => onChange(plan.filter((_, j) => j !== i))}
            >
              删
            </button>
          </div>
          {/* 中文改提示词（TASK-048）：智能融入这一条的英文提示词 */}
          <ZhPromptBox
            projectId={projectId}
            currentPrompt={item.image_prompt}
            hint="直接写要改的内容（如「场景中有2张桌子」「改成夜晚」），智能融入这条提示词"
            onResult={(prompt) => {
              const next = [...plan]
              next[i] = { ...item, image_prompt: prompt }
              onChange(next)
            }}
          />
        </div>
      ))}
      <button
        type="button"
        className="rounded border border-dashed border-line px-2 py-1 text-xs text-ink-dim hover:border-primary hover:text-primary"
        onClick={() => onChange([...plan, { view_label: '', image_prompt: '' }])}
      >
        + 加一张设定图
      </button>
    </div>
  )
}

function AssetCard({
  project,
  asset,
  onRun,
  onPreview,
}: {
  project: Project
  asset: Asset
  onRun: RunFn
  onPreview: (src: string, caption: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const keyRequired = asset.kind === 'character' || asset.kind === 'scene'
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({
    name: asset.name,
    description: asset.description,
    visual_anchor: asset.visual_anchor,
    image_plan: asset.image_plan.map((p) => ({ ...p })),
  })
  const [genPrompt, setGenPrompt] = useState('')
  const [genBusy, setGenBusy] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const primaryPlan = asset.image_plan[0]

  const upload = async (file: File) => {
    try {
      await api.uploadAssetImage(project.id, asset.id, file)
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : '上传失败')
    }
  }

  /** 统一生成入口：框里有字 = 智能融入基础提示词；留空 = 按当前设定随机重生成。 */
  const generate = async () => {
    if (genBusy) return
    setGenBusy(true)
    setGenError(null)
    try {
      const request = genPrompt.trim()
      let extra = ''
      if (request) {
        const result = await api.translatePrompt(
          project.id,
          request,
          't2i',
          primaryPlan?.image_prompt ?? '',
        )
        extra = result.prompt
      }
      onRun('generate_asset_image', {
        asset_id: asset.id,
        view_label: primaryPlan?.view_label ?? '主设定',
        ...(extra ? { extra_prompt: extra } : {}),
      })
      setGenPrompt('')
    } catch (err) {
      setGenError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setGenBusy(false)
    }
  }

  const saveEdit = () => {
    onRun('edit_asset', {
      asset_id: asset.id,
      name: form.name,
      description: form.description,
      visual_anchor: form.visual_anchor,
      image_plan: form.image_plan.filter((p) => p.view_label && p.image_prompt),
    })
    setEditing(false)
  }

  return (
    <article className="rounded-lg border border-line bg-surface p-3">
      <header className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-medium">
          <span className="mr-1.5 rounded bg-surface-2 px-1.5 py-0.5 text-xs text-ink-dim">
            {KIND_LABEL[asset.kind] ?? asset.kind}
          </span>
          {asset.name}
        </h3>
        <div className="flex items-center gap-2">
          {keyRequired && (
            <span className={`text-xs ${asset.images.some((i) => i.approved) ? 'text-ok' : 'text-warn'}`}>
              {asset.images.some((i) => i.approved) ? '已批准' : '缺少批准图'}
            </span>
          )}
          <button
            className="text-xs text-ink-dim hover:text-primary"
            onClick={() => {
              setForm({
                name: asset.name,
                description: asset.description,
                visual_anchor: asset.visual_anchor,
                image_plan: asset.image_plan.map((p) => ({ ...p })),
              })
              setEditing((v) => !v)
            }}
          >
            {editing ? '收起' : '编辑设定'}
          </button>
        </div>
      </header>

      {editing ? (
        <div className="mb-2 space-y-2 rounded border border-line bg-surface-2 p-2">
          <input
            className="w-full rounded border border-line bg-surface px-2 py-1 text-xs"
            value={form.name}
            placeholder="名称"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <input
            className="w-full rounded border border-line bg-surface px-2 py-1 text-xs"
            value={form.visual_anchor}
            placeholder="一致性锚点（发型/服装/配色/材质）"
            onChange={(e) => setForm({ ...form, visual_anchor: e.target.value })}
          />
          <textarea
            className="h-14 w-full rounded border border-line bg-surface px-2 py-1 text-xs"
            value={form.description}
            placeholder="描述"
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
          <ImagePlanEditor
            projectId={project.id}
            plan={form.image_plan}
            onChange={(image_plan) => setForm({ ...form, image_plan })}
          />
          <button
            className="rounded bg-primary px-3 py-1 text-xs text-white hover:bg-primary-hover"
            onClick={saveEdit}
          >
            保存设定
          </button>
        </div>
      ) : (
        <p className="mb-2 text-xs text-ink-dim">{asset.visual_anchor}</p>
      )}

      <div className="grid grid-cols-2 gap-2">
        {asset.images.map((image) => (
          <figure key={image.id} className="overflow-hidden rounded border border-line">
            {image.url ? (
              // 框保持原来的 16:9 大小，图缩进去完整显示（object-contain）：
              // 全身定妆照与空镜都是竖长图，object-cover 会把人物裁成只剩躯干。
              <img
                src={image.url}
                alt={`${asset.name} ${image.view_label}`}
                className="aspect-video w-full cursor-zoom-in bg-surface-2 object-contain"
                onClick={() => image.url && onPreview(image.url, `${asset.name} · ${image.view_label}`)}
              />
            ) : (
              <div className="flex aspect-video items-center justify-center bg-surface-2 text-xs text-ink-dim">
                {image.status === 'generating' ? '生成中…' : image.status}
              </div>
            )}
            <figcaption className="flex items-center justify-between px-1.5 py-1 text-xs">
              <span className="min-w-0 truncate text-ink-dim">{image.view_label}</span>
              <span className="flex shrink-0 items-center gap-1.5">
                <label className="flex cursor-pointer items-center gap-1">
                  <input
                    type="checkbox"
                    checked={image.approved}
                    onChange={(e) =>
                      onRun('approve_asset_image', {
                        asset_image_id: image.id,
                        approved: e.target.checked,
                      })
                    }
                  />
                  批准
                </label>
                <button
                  type="button"
                  className="text-ink-dim hover:text-danger"
                  title="删除这张图（磁盘文件保留）"
                  onClick={() => {
                    if (
                      window.confirm(
                        `删除「${asset.name} · ${image.view_label}」这张图？磁盘文件会保留，可放心删。`,
                      )
                    ) {
                      onRun('delete_asset_image', { asset_image_id: image.id })
                    }
                  }}
                >
                  删
                </button>
              </span>
            </figcaption>
          </figure>
        ))}
      </div>

      {/* 统一智能生成（TASK-048 用户反馈：输入框太多）：一个框一条路——
          直接说想改什么（中/英均可）→ 智能融入这张卡的基础提示词；
          留空 = 按当前设定重新生成（随机种子出新图） */}
      <div className="mt-2 rounded border border-line/60 p-2">
        <textarea
          rows={2}
          className="w-full rounded border border-line bg-surface-2 px-2 py-1 text-xs"
          placeholder={`想改什么直接说，如「${
            asset.kind === 'scene' ? '场景中有2张桌子' : '换成雨夜街头，他撑着黑伞'
          }」；留空 = 按当前设定重新生成`}
          value={genPrompt}
          onChange={(e) => setGenPrompt(e.target.value)}
        />
        <div className="mt-1 flex items-center gap-2">
          <button
            className="rounded bg-primary px-3 py-1 text-xs text-white hover:bg-primary-hover disabled:opacity-40"
            disabled={genBusy}
            onClick={() => void generate()}
          >
            {genBusy ? '处理中…' : '生成'}
          </button>
          <span className="text-[11px] text-ink-dim">
            中文直接说即可；写了内容 = 智能融入基础提示词，留空 = 随机重生成
          </span>
          {genError && <span className="text-xs text-danger">{genError}</span>}
        </div>
      </div>

      <div className="mt-2">
        <button
          className="rounded border border-line px-2 py-0.5 text-xs hover:border-primary hover:text-primary"
          onClick={() => fileRef.current?.click()}
        >
          上传图片
        </button>
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
    </article>
  )
}

function NewAssetForm({ onRun }: { onRun: RunFn }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({
    kind: 'character',
    name: '',
    description: '',
    visual_anchor: '',
  })

  if (!open) {
    return (
      <button
        className="rounded border border-dashed border-line px-3 py-2 text-sm text-ink-dim hover:border-primary hover:text-primary"
        onClick={() => setOpen(true)}
      >
        + 新增资产（LLM 没拆到的角色 / 场景 / 道具）
      </button>
    )
  }
  return (
    <div className="space-y-2 rounded-lg border border-line bg-surface p-3">
      <div className="flex gap-2">
        <select
          className="rounded border border-line bg-surface-2 px-2 py-1 text-xs"
          value={form.kind}
          onChange={(e) => setForm({ ...form, kind: e.target.value })}
        >
          <option value="character">角色</option>
          <option value="scene">场景</option>
          <option value="prop">道具</option>
        </select>
        <input
          className="min-w-0 flex-1 rounded border border-line bg-surface-2 px-2 py-1 text-xs"
          value={form.name}
          placeholder="名称"
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
      </div>
      <input
        className="w-full rounded border border-line bg-surface-2 px-2 py-1 text-xs"
        value={form.visual_anchor}
        placeholder="一致性锚点：发型 / 服装 / 配色 / 材质（越具体越不换脸）"
        onChange={(e) => setForm({ ...form, visual_anchor: e.target.value })}
      />
      <textarea
        className="h-14 w-full rounded border border-line bg-surface-2 px-2 py-1 text-xs"
        value={form.description}
        placeholder="描述（可选）"
        onChange={(e) => setForm({ ...form, description: e.target.value })}
      />
      <div className="flex gap-2">
        <button
          className="rounded bg-primary px-3 py-1 text-xs text-white hover:bg-primary-hover disabled:opacity-40"
          disabled={!form.name.trim()}
          onClick={() => {
            onRun('create_asset', { ...form, name: form.name.trim() })
            setForm({ kind: 'character', name: '', description: '', visual_anchor: '' })
            setOpen(false)
          }}
        >
          创建
        </button>
        <button
          className="rounded border border-line px-3 py-1 text-xs text-ink-dim hover:border-line"
          onClick={() => setOpen(false)}
        >
          取消
        </button>
      </div>
    </div>
  )
}

export default function AssetsTab({
  project,
  onRun,
}: {
  project: Project
  onRun: RunFn
}) {
  const { data, isLoading } = useAssets(project.id)
  const [preview, setPreview] = useState<{ src: string; caption: string } | null>(null)
  if (isLoading) return <p className="text-sm text-ink-dim">加载中…</p>
  const assets = data?.assets ?? []
  return (
    <div className="space-y-3">
      <StyleBar project={project} onRun={onRun} />
      <p className="text-xs text-ink-dim">
        每个资产只出一张设定卡：角色卡=正面全身定妆照（站直对镜头、头到脚完整，服装从鞋到配饰都看得清）；
        场景卡=空镜单幅广角全貌（无人，一眼看尽整个空间的地标与布局）；道具卡=白底居中。
        视频与关键帧按这些卡锁定人物长相与空间；
        也可以用自定义提示词补图，或直接上传参考图。角色与场景各需 ≥1 张已批准图才能过「确认资产」门。
      </p>
      <NewAssetForm onRun={onRun} />
      {assets.length === 0 ? (
        <div className="rounded border border-line bg-surface p-6 text-center">
          <p className="text-sm text-ink-dim">还没有资产。先确认剧本，然后抽取资产。</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {assets.map((asset) => (
            <AssetCard
              key={asset.id}
              project={project}
              asset={asset}
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
