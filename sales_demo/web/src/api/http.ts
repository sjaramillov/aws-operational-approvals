import {
  ApiProblem,
  type ApprovalSummary,
  type Decision,
  type DecisionAccepted,
  type Health,
  type Identity,
  type ProblemDetails,
  type SalesApi,
  type SalesApplication,
} from '../domain'

export interface HttpAuthentication {
  getAccessToken(forceRefresh?: boolean): Promise<string>
  onAuthenticationFailure(): void
}

function authenticationExpired(): ApiProblem {
  return new ApiProblem({
    status: 401,
    code: 'AUTHENTICATION_EXPIRED',
    detail: 'La sesión expiró y no pudo renovarse. Ingresa nuevamente.',
  })
}

async function request<T>(apiBaseUrl: string, path: string, authentication: HttpAuthentication, init: RequestInit = {}): Promise<T> {
  const send = async (accessToken: string): Promise<Response> => {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    headers.set('Authorization', `Bearer ${accessToken}`)
    if (init.body) headers.set('Content-Type', 'application/json')
    return fetch(`${apiBaseUrl.replace(/\/$/, '')}${path}`, { ...init, headers })
  }

  const accessToken = async (forceRefresh: boolean): Promise<string> => {
    try {
      return await authentication.getAccessToken(forceRefresh)
    } catch {
      authentication.onAuthenticationFailure()
      throw authenticationExpired()
    }
  }

  let response = await send(await accessToken(false))
  if (response.status === 401) {
    response = await send(await accessToken(true))
    if (response.status === 401) {
      authentication.onAuthenticationFailure()
      throw authenticationExpired()
    }
  }

  if (!response.ok) {
    const fallback: ProblemDetails = {
      type: 'about:blank',
      title: 'No se pudo completar la operación',
      status: response.status,
      detail: 'El servicio no devolvió un detalle seguro.',
      code: 'HTTP_ERROR',
    }
    const problem = (await response.json().catch(() => fallback)) as ProblemDetails
    throw new ApiProblem({ status: problem.status ?? response.status, code: problem.code ?? 'HTTP_ERROR', detail: problem.detail })
  }
  return response.json() as Promise<T>
}

export function createHttpApi(authentication: HttpAuthentication, apiBaseUrl: string): SalesApi {
  return {
    getHealth: (signal) => request<Health>(apiBaseUrl, '/health', authentication, { signal }),
    getMe: (signal) => request<Identity>(apiBaseUrl, '/me', authentication, { signal }),
    async createApplication(vehicleCount, idempotencyKey) {
      return request<{ application: SalesApplication; replayed: boolean }>(apiBaseUrl, '/applications', authentication, {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify({ vehicleCount }),
      })
    },
    async listApplications(signal) {
      const response = await request<{ items: SalesApplication[] }>(apiBaseUrl, '/applications', authentication, { signal })
      return response.items
    },
    async getApplication(applicationId, signal) {
      const response = await request<{ application: SalesApplication }>(
        apiBaseUrl,
        `/applications/${encodeURIComponent(applicationId)}`,
        authentication,
        { signal },
      )
      return response.application
    },
    async listApprovals(signal) {
      const response = await request<{ items: ApprovalSummary[] }>(apiBaseUrl, '/approvals', authentication, { signal })
      return response.items
    },
    async decideApplication(applicationId, decision: Decision, reasonCode: string) {
      return request<DecisionAccepted>(
        apiBaseUrl,
        `/approvals/${encodeURIComponent(applicationId)}/decision`,
        authentication,
        { method: 'POST', body: JSON.stringify({ decision, reasonCode }) },
      )
    },
    async getPlanPlus(signal) {
      const response = await request<{ plan: Awaited<ReturnType<SalesApi['getPlanPlus']>> }>(apiBaseUrl, '/plan-plus', authentication, { signal })
      return response.plan
    },
  }
}
