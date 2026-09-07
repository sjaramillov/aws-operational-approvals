import type { ApplicationStatus, SalesApi, SalesApplication } from '../domain'

const FINAL_STATUSES = new Set<ApplicationStatus>(['CONTRACT_ACTIVE', 'REJECTED'])

export interface DecisionPollProgress {
  attempt: number
  maxAttempts: number
  lastStatus: ApplicationStatus
  nextDelayMs: number
}

export interface DecisionPollOptions {
  signal?: AbortSignal
  maxAttempts?: number
  initialDelayMs?: number
  maxDelayMs?: number
  onProgress?: (progress: DecisionPollProgress) => void
  sleep?: (delayMs: number, signal?: AbortSignal) => Promise<void>
}

export class DecisionPollingTimeout extends Error {
  readonly attempts: number

  constructor(attempts: number) {
    super('La decisión fue aceptada, pero el estado durable no convergió dentro de la ventana de verificación. Recarga la solicitud antes de intentar otra acción.')
    this.name = 'DecisionPollingTimeout'
    this.attempts = attempts
  }
}

function abortableSleep(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'))
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, delayMs)
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      },
      { once: true },
    )
  })
}

export async function pollDecisionUntilFinal(
  api: SalesApi,
  applicationId: string,
  options: DecisionPollOptions = {},
): Promise<SalesApplication> {
  const maxAttempts = options.maxAttempts ?? 7
  const maxDelayMs = options.maxDelayMs ?? 2_500
  const sleep = options.sleep ?? abortableSleep
  let delayMs = options.initialDelayMs ?? 250

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const application = await api.getApplication(applicationId, options.signal)
    if (FINAL_STATUSES.has(application.status)) return application
    if (attempt === maxAttempts) break

    options.onProgress?.({
      attempt,
      maxAttempts,
      lastStatus: application.status,
      nextDelayMs: delayMs,
    })
    await sleep(delayMs, options.signal)
    delayMs = Math.min(delayMs * 2, maxDelayMs)
  }

  throw new DecisionPollingTimeout(maxAttempts)
}
