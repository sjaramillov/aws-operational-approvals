import { beforeEach, describe, expect, it } from 'vitest'
import { createDemoApi, DEMO_IDENTITIES, resetDemoData } from './demo'

const customerA = DEMO_IDENTITIES[0]
const managerA = DEMO_IDENTITIES[1]
const customerB = DEMO_IDENTITIES[2]

describe('demo sales adapter', () => {
  beforeEach(resetDemoData)

  it('auto-approves 50 vehicles and activates a synthetic contract', async () => {
    const api = createDemoApi(customerA)
    const result = await api.createApplication(50, 'key-50-0000000000')

    expect(result.replayed).toBe(false)
    expect(result.application.status).toBe('CONTRACT_ACTIVE')
    expect(result.application.contractId).toMatch(/^ctr_[0-9a-f]{24}$/)
    await expect(api.getPlanPlus()).resolves.toMatchObject({ status: 'ACTIVE', applicationId: result.application.applicationId })
  })

  it('replays the same tenant idempotency key without creating a duplicate', async () => {
    const api = createDemoApi(customerA)
    const first = await api.createApplication(51, 'stable-key-000000')
    const second = await api.createApplication(51, 'stable-key-000000')

    expect(second.replayed).toBe(true)
    expect(second.application.applicationId).toBe(first.application.applicationId)
    await expect(api.listApplications()).resolves.toHaveLength(1)
  })

  it('records approval and rejection with durable audit evidence', async () => {
    const customerApi = createDemoApi(customerA)
    const managerApi = createDemoApi(managerA)
    const approveTarget = await customerApi.createApplication(51, 'approve-key-00000')
    const rejectTarget = await customerApi.createApplication(52, 'reject-key-000000')

    const approvedAcceptance = await managerApi.decideApplication(
      approveTarget.application.applicationId,
      'APPROVE',
      'CAPACITY_CONFIRMED',
    )
    const rejectedAcceptance = await managerApi.decideApplication(
      rejectTarget.application.applicationId,
      'REJECT',
      'CAPACITY_NOT_AVAILABLE',
    )

    expect(approvedAcceptance).toEqual({
      applicationId: approveTarget.application.applicationId,
      accepted: true,
      status: 'PENDING_MANAGER',
    })
    expect(rejectedAcceptance).toEqual({
      applicationId: rejectTarget.application.applicationId,
      accepted: true,
      status: 'PENDING_MANAGER',
    })
    await expect(customerApi.getApplication(approveTarget.application.applicationId)).resolves.toMatchObject({ status: 'PENDING_MANAGER' })
    await expect(customerApi.getApplication(rejectTarget.application.applicationId)).resolves.toMatchObject({ status: 'PENDING_MANAGER' })

    await new Promise((resolve) => window.setTimeout(resolve, 400))
    const approved = await customerApi.getApplication(approveTarget.application.applicationId)
    const rejected = await customerApi.getApplication(rejectTarget.application.applicationId)

    expect(approved).toMatchObject({ status: 'CONTRACT_ACTIVE', decision: 'APPROVE' })
    expect(approved.auditTrail.at(-1)).toMatchObject({ actorRole: 'MANAGER', reasonCode: 'CAPACITY_CONFIRMED' })
    expect(rejected).toMatchObject({ status: 'REJECTED', decision: 'REJECT' })
    expect(rejected.auditTrail.at(-1)).toMatchObject({ actorRole: 'MANAGER', reasonCode: 'CAPACITY_NOT_AVAILABLE' })
  })

  it('returns the same 404 shape for a cross-tenant read', async () => {
    const created = await createDemoApi(customerA).createApplication(51, 'tenant-key-000000')

    await expect(createDemoApi(customerB).getApplication(created.application.applicationId)).rejects.toMatchObject({
      status: 404,
      code: 'NOT_FOUND',
    })
  })

  it('enforces the hard daily quota independently for each tenant', async () => {
    const api = createDemoApi(customerA)
    for (let index = 0; index < 10; index += 1) {
      await api.createApplication(50, `quota-key-${String(index).padStart(6, '0')}`)
    }

    await expect(api.createApplication(50, 'quota-key-999999')).rejects.toMatchObject({
      status: 429,
      code: 'DAILY_QUOTA_EXCEEDED',
    })
    await expect(createDemoApi(customerB).createApplication(50, 'quota-key-999999')).resolves.toMatchObject({ replayed: false })
  })
})
