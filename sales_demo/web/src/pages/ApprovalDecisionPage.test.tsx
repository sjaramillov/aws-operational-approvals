import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { Identity, SalesApi, SalesApplication } from '../domain'
import type { RuntimeContext } from '../runtime'
import { DecisionPollingTimeout } from '../api/pollDecision'
import { ApprovalDecisionPage } from './ApprovalDecisionPage'

const { pollDecisionMock } = vi.hoisted(() => ({ pollDecisionMock: vi.fn() }))

vi.mock('../api/pollDecision', async () => {
  const actual = await vi.importActual<typeof import('../api/pollDecision')>('../api/pollDecision')
  return { ...actual, pollDecisionUntilFinal: pollDecisionMock }
})

const application: SalesApplication = {
  applicationId: 'app_0123456789abcdef0123456789abcdef',
  tenantId: 'tenant-andino',
  vehicleCount: 51,
  status: 'PENDING_MANAGER',
  createdAt: '2026-08-26T15:00:00Z',
  updatedAt: '2026-08-26T15:00:00Z',
  auditTrail: [],
}

const manager: Identity = {
  subject: 'manager-a',
  tenantId: 'tenant-andino',
  tenantName: 'Tenant Andino',
  role: 'MANAGER',
  displayName: 'Manager sintético A',
  syntheticData: true,
}

describe('manager decision verification', () => {
  it('never posts a second decision after 202 plus a polling timeout', async () => {
    pollDecisionMock.mockRejectedValue(new DecisionPollingTimeout(7))
    const decideApplication = vi.fn().mockResolvedValue({
      applicationId: application.applicationId,
      accepted: true,
      status: 'PENDING_MANAGER',
    })
    const api: SalesApi = {
      getHealth: vi.fn(),
      getMe: vi.fn(),
      createApplication: vi.fn(),
      listApplications: vi.fn(),
      getApplication: vi.fn().mockResolvedValue(application),
      listApprovals: vi.fn(),
      decideApplication,
      getPlanPlus: vi.fn(),
    }
    const context: RuntimeContext = { identity: manager, api, health: null }
    const user = userEvent.setup()

    render(
      <MemoryRouter initialEntries={[`/aprobaciones/${application.applicationId}`]}>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route path="/aprobaciones/:applicationId" element={<ApprovalDecisionPage />} />
            <Route path="/solicitudes/:applicationId" element={<p>Detalle durable</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    await screen.findByRole('heading', { name: `Decidir ${application.applicationId}` })
    await user.click(screen.getByRole('radio', { name: /Aprobar/ }))
    await user.selectOptions(screen.getByLabelText('Código de razón'), 'CAPACITY_CONFIRMED')
    await user.click(screen.getByRole('button', { name: 'Confirmar decisión' }))

    await screen.findByText(/estado durable no convergió/i)
    expect(screen.getByRole('button', { name: 'Decisión ya aceptada' })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Ver/recargar solicitud' })).toBeVisible()
    expect(decideApplication).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Reintentar verificación' }))
    await waitFor(() => expect(pollDecisionMock).toHaveBeenCalledTimes(2))
    expect(decideApplication).toHaveBeenCalledTimes(1)
  })
})
