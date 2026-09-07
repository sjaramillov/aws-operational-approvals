import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SalesApplication } from '../domain'
import { createHttpApi, type HttpAuthentication } from './http'

const application: SalesApplication = {
  applicationId: 'app_0123456789abcdef0123456789abcdef',
  tenantId: 'tenant-andino',
  vehicleCount: 51,
  status: 'PENDING_MANAGER',
  createdAt: '2026-08-26T15:00:00Z',
  updatedAt: '2026-08-26T15:00:00Z',
  auditTrail: [],
}

afterEach(() => vi.unstubAllGlobals())

function authentication(accessToken = 'synthetic-access-token'): HttpAuthentication {
  return {
    getAccessToken: vi.fn().mockResolvedValue(accessToken),
    onAuthenticationFailure: vi.fn(),
  }
}

describe('HTTP sales adapter', () => {
  it('sends the OpenAPI envelope and never sends tenantId from the browser', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ application, replayed: false }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await createHttpApi(authentication(), '/api').createApplication(51, 'stable-key-000000')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/applications')
    expect(JSON.parse(String(init.body))).toEqual({ vehicleCount: 51 })
    expect(String(init.body)).not.toContain('tenantId')
    const headers = init.headers as Headers
    expect(headers.get('Idempotency-Key')).toBe('stable-key-000000')
    expect(headers.get('Authorization')).toBe('Bearer synthetic-access-token')
  })

  it('maps application/problem+json without exposing response internals', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: 'https://example.invalid/problems/not-found',
            title: 'Not found',
            status: 404,
            detail: 'No se encontró la solicitud.',
            instance: '/applications/redacted',
            code: 'NOT_FOUND',
            requestId: 'request-redacted',
          }),
          { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    )

    await expect(createHttpApi(authentication(), '/api').getApplication('app_0123456789abcdef0123456789abcdef')).rejects.toMatchObject({
      status: 404,
      code: 'NOT_FOUND',
      message: 'No se encontró la solicitud.',
    })
  })

  it('preserves the exact 202 acceptance DTO without inventing a final state', async () => {
    const accepted = {
      applicationId: application.applicationId,
      accepted: true,
      status: 'PENDING_MANAGER',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(accepted), { status: 202, headers: { 'Content-Type': 'application/json' } }),
      ),
    )

    await expect(
      createHttpApi(authentication(), '/api').decideApplication(
        application.applicationId,
        'APPROVE',
        'MANAGER_APPROVED',
      ),
    ).resolves.toEqual(accepted)
  })

  it('retries one 401 with a forced refresh and uses the rotated access token', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ application }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const auth: HttpAuthentication = {
      getAccessToken: vi.fn().mockImplementation((forceRefresh = false) =>
        Promise.resolve(forceRefresh ? 'rotated-access-token' : 'initial-access-token')),
      onAuthenticationFailure: vi.fn(),
    }

    await expect(createHttpApi(auth, '/api').getApplication(application.applicationId)).resolves.toEqual(application)
    expect(auth.getAccessToken).toHaveBeenNthCalledWith(1, false)
    expect(auth.getAccessToken).toHaveBeenNthCalledWith(2, true)
    expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toHaveProperty('get')
    expect(((fetchMock.mock.calls[0][1] as RequestInit).headers as Headers).get('Authorization')).toBe('Bearer initial-access-token')
    expect(((fetchMock.mock.calls[1][1] as RequestInit).headers as Headers).get('Authorization')).toBe('Bearer rotated-access-token')
    expect(auth.onAuthenticationFailure).not.toHaveBeenCalled()
  })

  it('fails closed without a retry loop when refresh cannot produce a token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    const auth: HttpAuthentication = {
      getAccessToken: vi.fn().mockImplementation((forceRefresh = false) =>
        forceRefresh ? Promise.reject(new Error('refresh denied')) : Promise.resolve('expired-access-token')),
      onAuthenticationFailure: vi.fn(),
    }

    await expect(createHttpApi(auth, '/api').getApplication(application.applicationId)).rejects.toMatchObject({
      status: 401,
      code: 'AUTHENTICATION_EXPIRED',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(auth.getAccessToken).toHaveBeenCalledTimes(2)
    expect(auth.onAuthenticationFailure).toHaveBeenCalledTimes(1)
  })

  it('stops after one retry when the API rejects the refreshed token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    const auth: HttpAuthentication = {
      getAccessToken: vi.fn().mockResolvedValue('token-value'),
      onAuthenticationFailure: vi.fn(),
    }

    await expect(createHttpApi(auth, '/api').getApplication(application.applicationId)).rejects.toMatchObject({
      status: 401,
      code: 'AUTHENTICATION_EXPIRED',
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(auth.getAccessToken).toHaveBeenCalledTimes(2)
    expect(auth.onAuthenticationFailure).toHaveBeenCalledTimes(1)
  })
})
