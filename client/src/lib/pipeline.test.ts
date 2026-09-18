import { describe, expect, it } from 'vitest'
import { deriveStages, gateFor, nextAutoStep, regenerateFor } from './pipeline'

describe('deriveStages', () => {
  it('fresh project: script pending, everything else pending', () => {
    const stages = deriveStages('CREATED')
    expect(stages.find((s) => s.key === 'script')?.state).toBe('pending')
    expect(stages.find((s) => s.key === 'film')?.state).toBe('pending')
  })

  it('SCRIPT_READY puts the gate on script and marks nothing after done', () => {
    const stages = deriveStages('SCRIPT_READY')
    expect(stages.find((s) => s.key === 'script')?.state).toBe('gate')
    expect(stages.find((s) => s.key === 'assets')?.state).toBe('pending')
  })

  it('SCRIPT_APPROVED marks script done', () => {
    expect(deriveStages('SCRIPT_APPROVED').find((s) => s.key === 'script')?.state).toBe('done')
  })

  it('STORYBOARD_APPROVED puts the next step on frame stage (pending, not done)', () => {
    const stages = deriveStages('STORYBOARD_APPROVED')
    expect(stages.find((s) => s.key === 'storyboard')?.state).toBe('done')
    expect(stages.find((s) => s.key === 'frame')?.state).toBe('pending')
  })

  it('FRAME_DRAFTING / FRAME_READY / FRAME_APPROVED drive the frame stage', () => {
    expect(deriveStages('FRAME_DRAFTING').find((s) => s.key === 'frame')?.state).toBe('working')
    expect(deriveStages('FRAME_READY').find((s) => s.key === 'frame')?.state).toBe('gate')
    expect(deriveStages('FRAME_APPROVED').find((s) => s.key === 'frame')?.state).toBe('done')
    expect(deriveStages('FRAME_APPROVED').find((s) => s.key === 'video')?.state).toBe('pending')
  })

  it('VIDEO_PRODUCING marks video working', () => {
    expect(deriveStages('VIDEO_PRODUCING').find((s) => s.key === 'video')?.state).toBe('working')
  })

  it('COMPOSED marks everything done', () => {
    const stages = deriveStages('COMPOSED')
    expect(stages.every((s) => s.state === 'done')).toBe(true)
  })
})

describe('gateFor', () => {
  it('maps ready states to approve commands', () => {
    expect(gateFor('SCRIPT_READY')?.command).toBe('approve_script')
    expect(gateFor('ASSET_READY')?.command).toBe('approve_assets')
    expect(gateFor('STORYBOARD_READY')?.command).toBe('approve_storyboard')
    expect(gateFor('FRAME_READY')?.command).toBe('approve_keyframes')
    expect(gateFor('VIDEO_READY')?.command).toBe('compose')
    expect(gateFor('CREATED')).toBeNull()
  })
})

describe('nextAutoStep / regenerateFor', () => {
  it('one-click run starts from generate_script on CREATED', () => {
    expect(nextAutoStep('CREATED')?.command).toBe('generate_script')
    expect(nextAutoStep('STORYBOARD_APPROVED')).toEqual({
      label: '生成关键帧',
      command: 'generate_keyframes',
    })
    expect(nextAutoStep('FRAME_APPROVED')).toEqual({
      label: '生产全部视频',
      command: 'produce_video',
    })
    expect(nextAutoStep('SCRIPT_READY')).toBeNull() // 门前进度暂停
  })

  it('regenerate offered only at gate states', () => {
    expect(regenerateFor('SCRIPT_READY')?.command).toBe('generate_script')
    expect(regenerateFor('SCRIPT_APPROVED')).toBeNull()
    // 分镜确认后仍可重生成（编号错位迁移路径）；关键帧门随之重过
    expect(regenerateFor('STORYBOARD_APPROVED')?.command).toBe('generate_storyboard')
    expect(regenerateFor('FRAME_READY')?.command).toBe('generate_storyboard')
    expect(regenerateFor('FRAME_APPROVED')?.command).toBe('generate_storyboard')
    expect(regenerateFor('VIDEO_READY')?.command).toBe('generate_storyboard')
    expect(regenerateFor('COMPOSED')?.command).toBe('generate_storyboard')
    expect(regenerateFor('VIDEO_PRODUCING')).toBeNull() // 忙碌态不放开
  })
})
