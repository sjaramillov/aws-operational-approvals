import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PublicRuntimeConfig } from '../runtimeConfig'
import { completeCognitoLogin, getAccessToken, getValidAccessToken } from './pkce'

const settings: PublicRuntimeConfig = {
  apiBaseUrl: 'https://a1b2c3d4.execute-api.us-east-1.amazonaws.com',
  awsRegion: 'us-east-1',
  cognitoUserPool: 'us-east-1_AbCdEf123',
  cognitoClientId: 'public-client-example',
  cognitoDomain: 'https://approvals-sales-pilot.auth.us-east-1.amazoncognito.com',
  redirectUri: 'https://d111111abcdef8.cloudfront.net/auth/callback',
  logoutUri: 'https://d111111abcdef8.cloudfront.net/',
  oauthFlow: 'authorization_code_pkce',
  scopes: ['openid'],
  expiresAt: '2026-09-03T18:00:00Z',
  syntheticDataOnly: true,
  deploymentBinding: {
    sourceRevision: '1111111111111111111111111111111111111111',
    frontendReleaseSha256: '2222222222222222222222222222222222222222222222222222222222222222',
  },
}

afterEach(() => vi.unstubAllGlobals())

function jwt(exp: number): string {
  const payload = btoa(JSON.stringify({ exp })).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '')
  return `header.${payload}.signature`
}

describe('Cognito PKCE callback', () => {
  it('scrubs the authorization code before exchange and clears transient values on success', async () => {
    window.history.replaceState({}, '', '/auth/callback?code=secret-code&state=expected-state')
    sessionStorage.setItem('approvals-oauth-state', 'expected-state')
    sessionStorage.setItem('approvals-pkce-verifier', 'synthetic-verifier')
    const fetchMock = vi.fn().mockImplementation(async () => {
      expect(window.location.pathname).toBe('/auth/callback')
      expect(window.location.search).toBe('')
      return new Response(JSON.stringify({ access_token: 'opaque-access-token' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    await completeCognitoLogin('?code=secret-code&state=expected-state', settings)

    expect(getAccessToken()).toBe('opaque-access-token')
    expect(sessionStorage.getItem('approvals-oauth-state')).toBeNull()
    expect(sessionStorage.getItem('approvals-pkce-verifier')).toBeNull()
  })

  it('keeps the callback URL scrubbed and clears the transaction when token exchange fails', async () => {
    window.history.replaceState({}, '', '/auth/callback?code=failed-code&state=expected-state')
    sessionStorage.setItem('approvals-oauth-state', 'expected-state')
    sessionStorage.setItem('approvals-pkce-verifier', 'synthetic-verifier')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 400 })))

    await expect(completeCognitoLogin('?code=failed-code&state=expected-state', settings)).rejects.toThrow(/rechazó el intercambio/)
    expect(window.location.pathname).toBe('/auth/callback')
    expect(window.location.search).toBe('')
    expect(getAccessToken()).toBeNull()
    expect(sessionStorage.getItem('approvals-oauth-state')).toBeNull()
    expect(sessionStorage.getItem('approvals-pkce-verifier')).toBeNull()
  })

  it('refreshes before JWT expiry and preserves Cognito refresh-token rotation', async () => {
    sessionStorage.setItem('approvals-access-token', jwt(Math.floor(Date.now() / 1000) + 30))
    sessionStorage.setItem('approvals-refresh-token', 'initial-refresh-token')
    const renewed = jwt(Math.floor(Date.now() / 1000) + 900)
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ access_token: renewed, refresh_token: 'rotated-refresh-token' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getValidAccessToken(settings)).resolves.toBe(renewed)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    const body = init.body as URLSearchParams
    expect(body.get('grant_type')).toBe('refresh_token')
    expect(body.get('refresh_token')).toBe('initial-refresh-token')
    expect(body.has('client_secret')).toBe(false)
    expect(sessionStorage.getItem('approvals-refresh-token')).toBe('rotated-refresh-token')
  })

  it('clears the complete session when Cognito rejects refresh', async () => {
    sessionStorage.setItem('approvals-access-token', jwt(Math.floor(Date.now() / 1000) - 10))
    sessionStorage.setItem('approvals-refresh-token', 'rejected-refresh-token')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 400 })))

    await expect(getValidAccessToken(settings)).rejects.toThrow(/rechazó la renovación/)
    expect(sessionStorage.getItem('approvals-access-token')).toBeNull()
    expect(sessionStorage.getItem('approvals-refresh-token')).toBeNull()
  })
})
