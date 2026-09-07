import { useEffect } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { Identity, SalesApi } from '../domain'
import { AppShell } from './AppShell'

const { useAuthMock } = vi.hoisted(() => ({ useAuthMock: vi.fn() }))

vi.mock('../auth/AuthContext', () => ({ useAuth: useAuthMock }))

const customer: Identity = {
  subject: 'customer-a',
  tenantId: 'tenant-andino',
  tenantName: 'Tenant Andino',
  role: 'CUSTOMER',
  displayName: 'Customer sintético A',
  syntheticData: true,
}

function BusinessProbe({ api }: { api: SalesApi }) {
  useEffect(() => {
    void api.listApplications()
  }, [api])
  return <button type="button">Crear operación de negocio</button>
}

describe('pilot lifecycle gate', () => {
  it('keeps navigation and business calls disabled while health is PREPARED', async () => {
    const getHealth = vi.fn().mockResolvedValue({
      status: 'PREPARED',
      expiresAt: null,
      syntheticData: true,
      pii: false,
    })
    const listApplications = vi.fn().mockResolvedValue([])
    const api: SalesApi = {
      getHealth,
      getMe: vi.fn(),
      createApplication: vi.fn(),
      listApplications,
      getApplication: vi.fn(),
      listApprovals: vi.fn(),
      decideApplication: vi.fn(),
      getPlanPlus: vi.fn(),
    }
    useAuthMock.mockReturnValue({
      identity: customer,
      api,
      loading: false,
      logout: vi.fn(),
      isDemo: false,
    })

    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<BusinessProbe api={api} />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'El piloto aún no está habilitado' })).toBeVisible()
    expect(screen.getByText(/T0 no ha comenzado/)).toBeVisible()
    expect(screen.queryByRole('navigation', { name: 'Navegación principal' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Crear operación de negocio' })).not.toBeInTheDocument()
    expect(listApplications).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Verificar activación' }))
    expect(await screen.findByText('Piloto en preparación')).toBeVisible()
    expect(getHealth).toHaveBeenCalledTimes(2)
    expect(listApplications).not.toHaveBeenCalled()
  })
})
