import type { PublicRuntimeConfig } from '../runtimeConfig'

const VERIFIER_KEY = 'approvals-pkce-verifier'
const STATE_KEY = 'approvals-oauth-state'
const ACCESS_TOKEN_KEY = 'approvals-access-token'
const REFRESH_TOKEN_KEY = 'approvals-refresh-token'
const TOKEN_EXPIRY_SKEW_SECONDS = 60
let refreshInFlight: Promise<string> | null = null
let sessionGeneration = 0

function clearPkceTransaction(): void {
  sessionStorage.removeItem(VERIFIER_KEY)
  sessionStorage.removeItem(STATE_KEY)
}

function scrubCallbackUrl(): void {
  window.history.replaceState(window.history.state, '', '/auth/callback')
}

function randomUrlSafe(bytes: number): string {
  const data = crypto.getRandomValues(new Uint8Array(bytes))
  return btoa(String.fromCharCode(...data)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '')
}

function encodeUrlSafe(data: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(data))).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '')
}

export async function beginCognitoLogin(settings: PublicRuntimeConfig): Promise<void> {
  const verifier = randomUrlSafe(64)
  const challenge = encodeUrlSafe(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
  const state = randomUrlSafe(24)
  sessionStorage.setItem(VERIFIER_KEY, verifier)
  sessionStorage.setItem(STATE_KEY, state)

  const url = new URL('/oauth2/authorize', settings.cognitoDomain)
  url.search = new URLSearchParams({
    response_type: 'code',
    client_id: settings.cognitoClientId,
    redirect_uri: settings.redirectUri,
    scope: settings.scopes.join(' '),
    state,
    code_challenge_method: 'S256',
    code_challenge: challenge,
  }).toString()
  window.location.assign(url)
}

export async function completeCognitoLogin(search: string, settings: PublicRuntimeConfig): Promise<void> {
  const params = new URLSearchParams(search)
  const code = params.get('code')
  const state = params.get('state')
  const expectedState = sessionStorage.getItem(STATE_KEY)
  const verifier = sessionStorage.getItem(VERIFIER_KEY)
  if (!code || !state || !expectedState || state !== expectedState || !verifier) {
    scrubCallbackUrl()
    clearPkceTransaction()
    clearAccessToken()
    throw new Error('La respuesta de autenticación no superó la validación de estado PKCE.')
  }

  scrubCallbackUrl()
  clearAccessToken()
  try {
    const response = await fetch(new URL('/oauth2/token', settings.cognitoDomain), {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        client_id: settings.cognitoClientId,
        redirect_uri: settings.redirectUri,
        code,
        code_verifier: verifier,
      }),
    })
    if (!response.ok) throw new Error('Cognito rechazó el intercambio del código de autorización.')
    const tokens = (await response.json()) as { access_token?: string; refresh_token?: string }
    if (!tokens.access_token) throw new Error('Cognito no devolvió un access token.')
    sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
    if (tokens.refresh_token) sessionStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token)
  } finally {
    clearPkceTransaction()
  }
}

export function getAccessToken(): string | null {
  return sessionStorage.getItem(ACCESS_TOKEN_KEY)
}

export function clearAccessToken(): void {
  sessionGeneration += 1
  sessionStorage.removeItem(ACCESS_TOKEN_KEY)
  sessionStorage.removeItem(REFRESH_TOKEN_KEY)
}

function jwtExpiresAt(token: string): number | null {
  const payload = token.split('.')[1]
  if (!payload) return null
  try {
    const normalized = payload.replaceAll('-', '+').replaceAll('_', '/')
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')
    const parsed = JSON.parse(atob(padded)) as { exp?: unknown }
    return typeof parsed.exp === 'number' && Number.isFinite(parsed.exp) ? parsed.exp : null
  } catch {
    return null
  }
}

async function refreshCognitoTokens(settings: PublicRuntimeConfig): Promise<string> {
  const refreshToken = sessionStorage.getItem(REFRESH_TOKEN_KEY)
  if (!refreshToken) throw new Error('La sesión no dispone de un refresh token válido.')
  const generation = sessionGeneration

  const response = await fetch(new URL('/oauth2/token', settings.cognitoDomain), {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: settings.cognitoClientId,
      refresh_token: refreshToken,
    }),
  })
  if (!response.ok) throw new Error('Cognito rechazó la renovación de la sesión.')
  const tokens = (await response.json()) as { access_token?: string; refresh_token?: string }
  if (!tokens.access_token) throw new Error('Cognito no devolvió un access token renovado.')
  if (generation !== sessionGeneration || sessionStorage.getItem(REFRESH_TOKEN_KEY) !== refreshToken) {
    throw new Error('La sesión cambió durante la renovación y el resultado fue descartado.')
  }
  sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
  if (tokens.refresh_token) sessionStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token)
  return tokens.access_token
}

export async function getValidAccessToken(settings: PublicRuntimeConfig, forceRefresh = false): Promise<string> {
  const accessToken = sessionStorage.getItem(ACCESS_TOKEN_KEY)
  const expiresAt = accessToken ? jwtExpiresAt(accessToken) : null
  const mustRefresh = forceRefresh || !accessToken || expiresAt === null || expiresAt <= Date.now() / 1000 + TOKEN_EXPIRY_SKEW_SECONDS
  if (!mustRefresh && accessToken) return accessToken

  if (!refreshInFlight) {
    refreshInFlight = refreshCognitoTokens(settings)
      .catch((caught) => {
        clearAccessToken()
        throw caught
      })
      .finally(() => {
        refreshInFlight = null
      })
  }
  return refreshInFlight
}

export function cognitoLogoutUrl(settings: PublicRuntimeConfig): string {
  const url = new URL('/logout', settings.cognitoDomain)
  url.search = new URLSearchParams({ client_id: settings.cognitoClientId, logout_uri: settings.logoutUri }).toString()
  return url.toString()
}
