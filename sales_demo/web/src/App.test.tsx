import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { App } from './App'

describe('entry experience', () => {
  it('labels the local synthetic mode and offers no public registration', async () => {
    window.history.replaceState({}, '', '/ingreso')
    render(<App runtimeConfig={null} />)

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Aprobaciones operativas' })).toBeVisible())
    expect(screen.getByText('Demostración · datos sintéticos · sin PII real')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Elige una persona sintética' })).toBeVisible()
    expect(screen.queryByText(/registrarse|crear cuenta/i)).not.toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(4)
  })
})
