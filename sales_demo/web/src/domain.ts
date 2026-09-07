export type UserRole = 'CUSTOMER' | 'MANAGER'
export type PilotStatus = 'PREPARED' | 'ACTIVE' | 'EXPIRED'
export type ApplicationStatus =
  | 'PENDING_MANAGER'
  | 'APPROVED'
  | 'REJECTED'
  | 'CONTRACT_ACTIVE'
  | 'EXPIRED'
export type Decision = 'APPROVE' | 'REJECT'

export interface Identity {
  subject: string
  tenantId: string
  tenantName?: string
  role: UserRole
  displayName: string
  syntheticData: true
}

export interface Health {
  status: PilotStatus
  expiresAt: string | null
  syntheticData: true
  pii: false
}

export interface AuditEvent {
  action: string
  actorId: string
  actorRole: UserRole
  occurredAt: string
  decision?: Decision
  reasonCode?: string
}

export interface SalesApplication {
  applicationId: string
  tenantId: string
  vehicleCount: number
  status: ApplicationStatus
  createdAt: string
  updatedAt: string
  decision?: Decision
  reasonCode?: string
  contractId?: string
  planActivatedAt?: string
  auditTrail: AuditEvent[]
}

export interface ApprovalSummary {
  applicationId: string
  vehicleCount: number
  status: ApplicationStatus
  createdAt: string
}

export interface DecisionAccepted {
  applicationId: string
  accepted: true
  status: 'PENDING_MANAGER'
}

export interface PlanPlus {
  status: 'ACTIVE' | 'INACTIVE'
  applicationId?: string
  contractId?: string
  activatedAt?: string
}

export interface ProblemDetails {
  type: string
  title: string
  status: number
  detail: string
  instance?: string
  code: string
  requestId?: string
}

export interface SalesApi {
  getHealth(signal?: AbortSignal): Promise<Health>
  getMe(signal?: AbortSignal): Promise<Identity>
  createApplication(vehicleCount: number, idempotencyKey: string): Promise<{ application: SalesApplication; replayed: boolean }>
  listApplications(signal?: AbortSignal): Promise<SalesApplication[]>
  getApplication(applicationId: string, signal?: AbortSignal): Promise<SalesApplication>
  listApprovals(signal?: AbortSignal): Promise<ApprovalSummary[]>
  decideApplication(applicationId: string, decision: Decision, reasonCode: string): Promise<DecisionAccepted>
  getPlanPlus(signal?: AbortSignal): Promise<PlanPlus>
}

export class ApiProblem extends Error {
  readonly status: number
  readonly code: string

  constructor(problem: Pick<ProblemDetails, 'detail' | 'status' | 'code'>) {
    super(problem.detail)
    this.name = 'ApiProblem'
    this.status = problem.status
    this.code = problem.code
  }
}

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  PENDING_MANAGER: 'Requiere aprobación del Manager',
  APPROVED: 'Aprobada',
  REJECTED: 'Rechazada',
  CONTRACT_ACTIVE: 'Contrato y Servicio activos',
  EXPIRED: 'Expirada',
}

export const REASON_LABELS: Record<string, string> = {
  CAPACITY_CONFIRMED: 'Capacidad operativa confirmada',
  POLICY_EXCEPTION_APPROVED: 'Excepción comercial aprobada',
  CAPACITY_NOT_AVAILABLE: 'Capacidad operativa no disponible',
  INCOMPLETE_COMMERCIAL_CASE: 'Caso comercial incompleto',
  AUTO_THRESHOLD: 'Dentro del límite de aprobación automática',
  AUTO_APPROVED: 'Dentro del límite de aprobación automática',
  MANAGER_APPROVED: 'Capacidad operativa confirmada por el Manager',
  CAPACITY_NOT_APPROVED: 'Capacidad operativa no aprobada',
  RISK_NOT_ACCEPTED: 'Riesgo comercial no aceptado',
}
