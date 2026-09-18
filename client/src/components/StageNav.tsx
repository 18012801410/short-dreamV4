import { deriveStages } from '../lib/pipeline'

const STATE_ICON: Record<string, string> = {
  done: '✓',
  working: '●',
  gate: '●',
  pending: '○',
}
const STATE_COLOR: Record<string, string> = {
  done: 'text-ok',
  working: 'text-warn',
  gate: 'text-primary',
  pending: 'text-ink-dim',
}

export default function StageNav({ status }: { status: string }) {
  const stages = deriveStages(status)
  return (
    <nav aria-label="流水线阶段" className="space-y-2 text-sm">
      {stages.map((stage) => (
        <div key={stage.key} className={`flex items-center gap-2 ${STATE_COLOR[stage.state]}`}>
          <span aria-hidden className="w-4 text-center font-mono">
            {STATE_ICON[stage.state]}
          </span>
          <span className={stage.state === 'gate' ? 'font-medium text-ink' : ''}>{stage.label}</span>
        </div>
      ))}
    </nav>
  )
}
