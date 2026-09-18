/** 流水线阶段推导（UI 状态的唯一来源，纯函数便于单测）。 */

export type StageKey = 'script' | 'assets' | 'storyboard' | 'frame' | 'video' | 'film'
export type StageState = 'pending' | 'working' | 'gate' | 'done'

export interface StageInfo {
  key: StageKey
  label: string
  state: StageState
}

const STAGES: { key: StageKey; label: string }[] = [
  { key: 'script', label: '① 剧本' },
  { key: 'assets', label: '② 资产' },
  { key: 'storyboard', label: '③ 分镜' },
  { key: 'frame', label: '④ 关键帧' },
  { key: 'video', label: '⑤ 视频' },
  { key: 'film', label: '⑥ 成片' },
]

/** 状态 → 各阶段（working/gate/done/pending）；gate 即当前确认门所在阶段。 */
export function deriveStages(status: string): StageInfo[] {
  const state: Record<StageKey, StageState> = {
    script: 'pending',
    assets: 'pending',
    storyboard: 'pending',
    frame: 'pending',
    video: 'pending',
    film: 'pending',
  }
  const mark = (done: StageKey[], gate: StageKey | null, working: StageKey | null) => {
    for (const key of done) state[key] = 'done'
    if (gate) state[gate] = 'gate'
    if (working) state[working] = 'working'
  }
  switch (status) {
    case 'SCRIPT_DRAFTING':
      mark([], null, 'script')
      break
    case 'SCRIPT_READY':
      mark([], 'script', null)
      break
    case 'SCRIPT_APPROVED':
      mark(['script'], null, null)
      break
    case 'ASSET_DRAFTING':
      mark(['script'], null, 'assets')
      break
    case 'ASSET_READY':
      mark(['script'], 'assets', null)
      break
    case 'ASSET_APPROVED':
      mark(['script', 'assets'], null, null)
      break
    case 'STORYBOARD_DRAFTING':
      mark(['script', 'assets'], null, 'storyboard')
      break
    case 'STORYBOARD_READY':
      mark(['script', 'assets'], 'storyboard', null)
      break
    case 'STORYBOARD_APPROVED':
      mark(['script', 'assets', 'storyboard'], null, null)
      break
    case 'FRAME_DRAFTING':
      mark(['script', 'assets', 'storyboard'], null, 'frame')
      break
    case 'FRAME_READY':
      mark(['script', 'assets', 'storyboard'], 'frame', null)
      break
    case 'FRAME_APPROVED':
      mark(['script', 'assets', 'storyboard', 'frame'], null, null)
      break
    case 'VIDEO_PRODUCING':
      mark(['script', 'assets', 'storyboard', 'frame'], null, 'video')
      break
    case 'VIDEO_READY':
      mark(['script', 'assets', 'storyboard', 'frame'], 'video', null)
      break
    case 'COMPOSING':
      mark(['script', 'assets', 'storyboard', 'frame', 'video'], null, 'film')
      break
    case 'COMPOSED':
      mark(['script', 'assets', 'storyboard', 'frame', 'video'], 'film', null)
      state.film = 'done'
      break
    case 'ARCHIVED':
      mark(['script', 'assets', 'storyboard', 'frame', 'video', 'film'], null, null)
      break
  }
  return STAGES.map(({ key, label }) => ({ key, label, state: state[key] }))
}

/** 确认门：返回 (按钮文案, 命令类型)；null 表示当前无门可确认。 */
export function gateFor(status: string): { label: string; command: string } | null {
  switch (status) {
    case 'SCRIPT_READY':
      return { label: '确认剧本', command: 'approve_script' }
    case 'ASSET_READY':
      return { label: '确认资产', command: 'approve_assets' }
    case 'STORYBOARD_READY':
      return { label: '确认分镜', command: 'approve_storyboard' }
    case 'FRAME_READY':
      // 缺帧段需在关键帧 Tab 勾选「回退尾帧接力」，否则服务端按缺批准帧拦截
      return { label: '确认关键帧', command: 'approve_keyframes' }
    case 'VIDEO_READY':
      return { label: '合成成片', command: 'compose' }
    case 'COMPOSED':
      // 段视频重生成后可再出一版成片（重新合成会按当前分镜台词烧录字幕）
      return { label: '重新合成成片', command: 'compose' }
    default:
      return null
  }
}

/** 重新生成（次要按钮）：当前阶段可用的重新生成命令。 */
export function regenerateFor(status: string): { label: string; command: string } | null {
  switch (status) {
    case 'SCRIPT_READY':
      return { label: '重新生成剧本', command: 'generate_script' }
    case 'ASSET_READY':
      return { label: '重新抽取资产', command: 'generate_assets' }
    case 'STORYBOARD_READY':
      return { label: '重新生成分镜', command: 'generate_storyboard' }
    case 'STORYBOARD_APPROVED':
    case 'FRAME_READY':
    case 'FRAME_APPROVED':
    case 'VIDEO_READY':
    case 'COMPOSED':
      // 分镜确认后仍可重生成（失效回退到分镜待确认，关键帧门需重新通过）
      return { label: '重新生成分镜', command: 'generate_storyboard' }
    default:
      return null
  }
}

/** 一键连跑可达的下一个自动步骤。 */
export function nextAutoStep(status: string): { label: string; command: string } | null {
  switch (status) {
    case 'CREATED':
      return { label: '生成剧本', command: 'generate_script' }
    case 'SCRIPT_APPROVED':
      return { label: '抽取资产', command: 'generate_assets' }
    case 'ASSET_APPROVED':
      return { label: '生成分镜', command: 'generate_storyboard' }
    case 'STORYBOARD_APPROVED':
      return { label: '生成关键帧', command: 'generate_keyframes' }
    case 'FRAME_APPROVED':
      return { label: '生产全部视频', command: 'produce_video' }
    case 'VIDEO_READY':
      return { label: '合成成片', command: 'compose' }
    default:
      return null
  }
}
