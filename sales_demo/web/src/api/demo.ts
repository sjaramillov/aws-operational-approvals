import {
  ApiProblem,
  type ApprovalSummary,
  type Decision,
  type Health,
  type Identity,
  type PlanPlus,
  type SalesApi,
  type SalesApplication,
} from '../domain'

const STORE_KEY = 'approvals-sales-demo-data:v2'
const PILOT_HOURS = 192
const DEMO_FINALIZATION_DELAY_MS = 450
const IDEMPOTENCY_KEY = /^[A-Za-z0-9._~:+/=\-]{16,128}$/
const REASON_CODE = /^[A-Z][A-Z0-9_]{2,63}$/
const DEMO_ACTOR_IDS = [
  'actor_a1b2c3d4e5f60718',
  'actor_b2c3d4e5f60718a1',
  'actor_c3d4e5f60718a1b2',
  'actor_d4e5f60718a1b2c3',
]

interface StoredDemo {
  startedAt: string
  expiresAt: string
  applications: SalesApplication[]
  idempotency: Record<string, string>
  pendingDecisions: Record<string, PendingDecision>
}

interface PendingDecision {
  decision: Decision
  reasonCode: string
  actorId: string
  readyAt: number
}

export const DEMO_IDENTITIES: Identity[] = [
  {
    subject: 'customer-a',
    tenantId: 'tenant-andino',
    tenantName: 'Tenant Andino',
    role: 'CUSTOMER',
    displayName: 'Customer sintético A',
    syntheticData: true,
  },
  {
    subject: 'manager-a',
    tenantId: 'tenant-andino',
    tenantName: 'Tenant Andino',
    role: 'MANAGER',
    displayName: 'Manager sintético A',
    syntheticData: true,
  },
  {
    subject: 'customer-b',
    tenantId: 'tenant-litoral',
    tenantName: 'Tenant Litoral',
    role: 'CUSTOMER',
    displayName: 'Customer sintético B',
    syntheticData: true,
  },
  {
    subject: 'manager-b',
    tenantId: 'tenant-litoral',
    tenantName: 'Tenant Litoral',
    role: 'MANAGER',
    displayName: 'Manager sintético B',
    syntheticData: true,
  },
]

function newStore(): StoredDemo {
  const started = new Date()
  return {
    startedAt: started.toISOString(),
    expiresAt: new Date(started.getTime() + PILOT_HOURS * 60 * 60 * 1000).toISOString(),
    applications: [],
    idempotency: {},
    pendingDecisions: {},
  }
}

function loadStore(): StoredDemo {
  const raw = localStorage.getItem(STORE_KEY)
  if (!raw) {
    const store = newStore()
    saveStore(store)
    return store
  }
  try {
    const parsed = JSON.parse(raw) as StoredDemo
    return { ...parsed, pendingDecisions: parsed.pendingDecisions ?? {} }
  } catch {
    const store = newStore()
    saveStore(store)
    return store
  }
}

function saveStore(store: StoredDemo): void {
  localStorage.setItem(STORE_KEY, JSON.stringify(store))
}

function materializeReadyDecisions(store: StoredDemo): void {
  let changed = false
  for (const [applicationId, pending] of Object.entries(store.pendingDecisions)) {
    if (Date.now() < pending.readyAt) continue
    const application = store.applications.find((item) => item.applicationId === applicationId)
    if (!application || application.status !== 'PENDING_MANAGER') {
      delete store.pendingDecisions[applicationId]
      changed = true
      continue
    }

    const now = new Date().toISOString()
    application.decision = pending.decision
    application.reasonCode = pending.reasonCode
    application.updatedAt = now
    application.status = pending.decision === 'APPROVE' ? 'CONTRACT_ACTIVE' : 'REJECTED'
    application.contractId = pending.decision === 'APPROVE' ? `ctr_${application.applicationId.slice(4, 28)}` : undefined
    application.planActivatedAt = pending.decision === 'APPROVE' ? now : undefined
    application.auditTrail.push({
      action: 'MANAGER_DECISION_RECORDED',
      actorId: pending.actorId,
      actorRole: 'MANAGER',
      occurredAt: now,
      decision: pending.decision,
      reasonCode: pending.reasonCode,
    })
    application.auditTrail.push({
      action: pending.decision === 'APPROVE' ? 'CONTRACT_ACTIVATED' : 'APPLICATION_REJECTED',
      actorId: pending.actorId,
      actorRole: 'MANAGER',
      occurredAt: now,
      decision: pending.decision,
      reasonCode: pending.reasonCode,
    })
    delete store.pendingDecisions[applicationId]
    changed = true
  }
  if (changed) saveStore(store)
}

function opaqueActor(identity: Identity): string {
  const index = DEMO_IDENTITIES.findIndex((candidate) => candidate.subject === identity.subject)
  return DEMO_ACTOR_IDS[index] ?? 'actor_e5f60718a1b2c3d4'
}

function ensureActive(store: StoredDemo): void {
  if (Date.now() >= Date.parse(store.expiresAt)) {
    throw new ApiProblem({ status: 410, code: 'PILOT_EXPIRED', detail: 'El piloto sintético expiró y ya no admite operaciones.' })
  }
}

function tenantApplication(store: StoredDemo, identity: Identity, applicationId: string): SalesApplication {
  const application = store.applications.find(
    (item) => item.applicationId === applicationId && item.tenantId === identity.tenantId,
  )
  if (!application) {
    throw new ApiProblem({ status: 404, code: 'NOT_FOUND', detail: 'No se encontró la solicitud.' })
  }
  return application
}

function requireRole(identity: Identity, role: Identity['role']): void {
  if (identity.role !== role) {
    throw new ApiProblem({ status: 403, code: 'ROLE_DENIED', detail: 'Tu rol no permite ejecutar esta acción.' })
  }
}

async function settle<T>(value: T, signal?: AbortSignal): Promise<T> {
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, 90)
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      },
      { once: true },
    )
  })
  return structuredClone(value)
}

export function createDemoApi(identity: Identity): SalesApi {
  return {
    async getHealth(signal) {
      const store = loadStore()
      const health: Health = {
        status: Date.now() < Date.parse(store.expiresAt) ? 'ACTIVE' : 'EXPIRED',
        expiresAt: store.expiresAt,
        syntheticData: true,
        pii: false,
      }
      return settle(health, signal)
    },

    async getMe(signal) {
      ensureActive(loadStore())
      return settle(identity, signal)
    },

    async createApplication(vehicleCount, idempotencyKey) {
      requireRole(identity, 'CUSTOMER')
      const store = loadStore()
      ensureActive(store)
      if (!Number.isInteger(vehicleCount) || vehicleCount < 1 || vehicleCount > 10_000) {
        throw new ApiProblem({ status: 400, code: 'INVALID_VEHICLE_COUNT', detail: 'La cantidad debe ser un entero entre 1 y 10.000.' })
      }
      if (!IDEMPOTENCY_KEY.test(idempotencyKey)) {
        throw new ApiProblem({ status: 400, code: 'INVALID_IDEMPOTENCY_KEY', detail: 'La clave de idempotencia no cumple el contrato.' })
      }
      const scopedKey = `${identity.tenantId}:${idempotencyKey}`
      const replayId = store.idempotency[scopedKey]
      if (replayId) {
        return { application: structuredClone(tenantApplication(store, identity, replayId)), replayed: true }
      }

      const now = new Date().toISOString()
      const tenantApplicationsToday = store.applications.filter(
        (item) => item.tenantId === identity.tenantId && item.createdAt.slice(0, 10) === now.slice(0, 10),
      ).length
      if (tenantApplicationsToday >= 10) {
        throw new ApiProblem({ status: 429, code: 'DAILY_QUOTA_EXCEEDED', detail: 'El tenant alcanzó el límite de diez solicitudes para el día UTC.' })
      }
      const applicationId = `app_${crypto.randomUUID().replaceAll('-', '')}`
      const automatic = vehicleCount <= 50
      const application: SalesApplication = {
        applicationId,
        tenantId: identity.tenantId,
        vehicleCount,
        status: automatic ? 'CONTRACT_ACTIVE' : 'PENDING_MANAGER',
        createdAt: now,
        updatedAt: now,
        decision: automatic ? 'APPROVE' : undefined,
        reasonCode: automatic ? 'AUTO_APPROVED' : undefined,
        contractId: automatic ? `ctr_${applicationId.slice(4, 28)}` : undefined,
        planActivatedAt: automatic ? now : undefined,
        auditTrail: [
          {
            action: 'APPLICATION_SUBMITTED',
            actorId: opaqueActor(identity),
            actorRole: 'CUSTOMER',
            occurredAt: now,
          },
          automatic
            ? {
                action: 'CONTRACT_ACTIVATED',
                actorId: opaqueActor(identity),
                actorRole: 'CUSTOMER',
                occurredAt: now,
                decision: 'APPROVE',
                reasonCode: 'AUTO_APPROVED',
              }
            : {
                action: 'MANAGER_APPROVAL_REQUESTED',
                actorId: 'actor_0f0e0d0c0b0a0908',
                actorRole: 'MANAGER',
                occurredAt: now,
              },
        ],
      }
      store.applications.push(application)
      store.idempotency[scopedKey] = applicationId
      saveStore(store)
      return { application: structuredClone(application), replayed: false }
    },

    async listApplications(signal) {
      requireRole(identity, 'CUSTOMER')
      const store = loadStore()
      ensureActive(store)
      materializeReadyDecisions(store)
      const items = store.applications
        .filter((item) => item.tenantId === identity.tenantId)
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      return settle(items, signal)
    },

    async getApplication(applicationId, signal) {
      const store = loadStore()
      ensureActive(store)
      materializeReadyDecisions(store)
      return settle(tenantApplication(store, identity, applicationId), signal)
    },

    async listApprovals(signal) {
      requireRole(identity, 'MANAGER')
      const store = loadStore()
      ensureActive(store)
      materializeReadyDecisions(store)
      const items: ApprovalSummary[] = store.applications
        .filter((item) => item.tenantId === identity.tenantId && item.status === 'PENDING_MANAGER')
        .map(({ applicationId, vehicleCount, status, createdAt }) => ({ applicationId, vehicleCount, status, createdAt }))
      return settle(items, signal)
    },

    async decideApplication(applicationId, decision: Decision, reasonCode: string) {
      requireRole(identity, 'MANAGER')
      const store = loadStore()
      ensureActive(store)
      if (!REASON_CODE.test(reasonCode)) {
        throw new ApiProblem({ status: 400, code: 'INVALID_REASON_CODE', detail: 'El código de razón no cumple el contrato.' })
      }
      const application = tenantApplication(store, identity, applicationId)
      if (application.status !== 'PENDING_MANAGER') {
        throw new ApiProblem({ status: 409, code: 'ALREADY_DECIDED', detail: 'La solicitud ya tiene una decisión durable.' })
      }
      const existing = store.pendingDecisions[applicationId]
      if (existing && (existing.decision !== decision || existing.reasonCode !== reasonCode)) {
        throw new ApiProblem({ status: 409, code: 'DECISION_IN_PROGRESS', detail: 'Otra decisión del Manager ya está en procesamiento.' })
      }
      store.pendingDecisions[applicationId] = existing ?? {
        decision,
        reasonCode,
        actorId: opaqueActor(identity),
        readyAt: Date.now() + DEMO_FINALIZATION_DELAY_MS,
      }
      saveStore(store)
      return settle({ applicationId, accepted: true as const, status: 'PENDING_MANAGER' as const })
    },

    async getPlanPlus(signal) {
      requireRole(identity, 'CUSTOMER')
      const store = loadStore()
      ensureActive(store)
      materializeReadyDecisions(store)
      const active = store.applications
        .filter((item) => item.tenantId === identity.tenantId && item.status === 'CONTRACT_ACTIVE')
        .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0]
      const plan: PlanPlus = active
        ? {
            status: 'ACTIVE',
            applicationId: active.applicationId,
            contractId: active.contractId,
            activatedAt: active.updatedAt,
          }
        : { status: 'INACTIVE' }
      return settle(plan, signal)
    },
  }
}

export function resetDemoData(): void {
  localStorage.removeItem(STORE_KEY)
}
