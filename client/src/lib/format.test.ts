import { describe, expect, it } from 'vitest'
import { formatDuration } from './format'

describe('formatDuration', () => {
  it('formats seconds under a minute', () => {
    expect(formatDuration(45)).toBe('45s')
  })

  it('formats minutes with padded seconds', () => {
    expect(formatDuration(63)).toBe('1m03s')
    expect(formatDuration(83)).toBe('1m23s')
  })

  it('clamps negative input to zero', () => {
    expect(formatDuration(-5)).toBe('0s')
  })
})
