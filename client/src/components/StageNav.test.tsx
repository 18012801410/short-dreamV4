import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import StageNav from './StageNav'

describe('StageNav', () => {
  it('renders all six stages as pending on a fresh project', () => {
    render(<StageNav status="CREATED" />)
    expect(screen.getByText('① 剧本')).toBeInTheDocument()
    expect(screen.getByText('④ 关键帧')).toBeInTheDocument()
    expect(screen.getByText('⑥ 成片')).toBeInTheDocument()
    expect(document.body.textContent).toContain('○')
  })

  it('marks the gated stage with a filled dot at SCRIPT_READY', () => {
    render(<StageNav status="SCRIPT_READY" />)
    const script = screen.getByText('① 剧本')
    expect(script.className).toContain('text-ink')
  })

  it('marks everything done when composed', () => {
    render(<StageNav status="COMPOSED" />)
    expect(document.body.textContent).not.toContain('○')
  })
})
