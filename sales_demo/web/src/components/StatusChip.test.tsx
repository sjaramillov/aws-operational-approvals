import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusChip } from './StatusChip'

describe('StatusChip', () => {
  it('expresses durable state with text, not color alone', () => {
    render(<StatusChip status="PENDING_MANAGER" />)
    expect(screen.getByText('Requiere aprobación del Manager')).toBeVisible()
    expect(screen.getByText('Requiere aprobación del Manager').closest('[data-status]')).toHaveAttribute(
      'data-status',
      'PENDING_MANAGER',
    )
  })

  it('labels PREPARED as a non-active pilot state', () => {
    render(<StatusChip status="PREPARED" />)
    expect(screen.getByText('Piloto en preparación')).toBeVisible()
    expect(screen.getByText('Piloto en preparación').closest('[data-status]')).toHaveAttribute('data-status', 'PREPARED')
  })
})
