import { describe, expect, it, vi } from 'vitest'
import type { SalesApi, SalesApplication } from '../domain'
import { pollDecisionUntilFinal } from './pollDecision'

function application(status: SalesApplication['status']): SalesApplication {
  return {
    applicationId: 'app_0123456789abcdef0123456789abcdef',
    tenantId: 'tenant-andino',
    vehicleCount: 51,
    status,
    createdAt: '2026-08-26T15:00:00Z',
    updatedAt: '2026-08-26T15:00:00Z',
    auditTrail: [],
  }
}

describe('bounded decision polling', () => {
  it('backs off while pending and returns only a durable final state', async () => {
    const getApplication = vi
      .fn()
      .mockResolvedValueOnce(application('PENDING_MANAGER'))
      .mockResolvedValueOnce(application('PENDING_MANAGER'))
      .mockResolvedValueOnce(application('CONTRACT_ACTIVE'))
    const delays: number[] = []
    const api = { getApplication } as unknown as SalesApi

    const result = await pollDecisionUntilFinal(api, application('PENDING_MANAGER').applicationId, {
      initialDelayMs: 100,
      maxAttempts: 4,
      sleep: async (delay) => { delays.push(delay) },
    })

    expect(result.status).toBe('CONTRACT_ACTIVE')
    expect(delays).toEqual([100, 200])
    expect(getApplication).toHaveBeenCalledTimes(3)
  })

  it('fails closed with an explicit timeout after the bounded attempts', async () => {
    const api = { getApplication: vi.fn().mockResolvedValue(application('PENDING_MANAGER')) } as unknown as SalesApi

    await expect(
      pollDecisionUntilFinal(api, application('PENDING_MANAGER').applicationId, {
        maxAttempts: 3,
        sleep: async () => undefined,
      }),
    ).rejects.toMatchObject({ name: 'DecisionPollingTimeout', attempts: 3 })
  })
})
