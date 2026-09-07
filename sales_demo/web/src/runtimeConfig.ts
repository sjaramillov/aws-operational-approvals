export const IS_DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true'

export interface PublicRuntimeConfig {
  apiBaseUrl: string
  awsRegion: string
  cognitoUserPool: string
  cognitoClientId: string
  cognitoDomain: string
  redirectUri: string
  logoutUri: string
  oauthFlow: 'authorization_code_pkce'
  scopes: ['openid']
  expiresAt: string | null
  syntheticDataOnly: true
  deploymentBinding: {
    sourceRevision: string
    frontendReleaseSha256: string
  }
}

const EXPECTED_KEYS = new Set<keyof PublicRuntimeConfig>([
  'apiBaseUrl',
  'awsRegion',
  'cognitoUserPool',
  'cognitoClientId',
  'cognitoDomain',
  'redirectUri',
  'logoutUri',
  'oauthFlow',
  'scopes',
  'expiresAt',
  'syntheticDataOnly',
  'deploymentBinding',
])

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`La configuración pública no contiene ${field}.`)
  return value
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function httpsUrl(value: unknown, field: string): URL {
  const raw = requiredString(value, field)
  const url = new URL(raw)
  if (url.protocol !== 'https:') throw new Error(`${field} debe usar HTTPS en producción.`)
  if (url.username || url.password) throw new Error(`${field} no puede contener credenciales.`)
  return url
}

export function parseRuntimeConfig(value: unknown): PublicRuntimeConfig {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('La configuración pública no es un objeto JSON válido.')
  }
  const record = value as Record<string, unknown>
  const keys = Object.keys(record)
  if (keys.some((key) => !EXPECTED_KEYS.has(key as keyof PublicRuntimeConfig)) || keys.length !== EXPECTED_KEYS.size) {
    throw new Error('La configuración pública no coincide con el contrato exacto esperado.')
  }
  if (record.oauthFlow !== 'authorization_code_pkce') throw new Error('El flujo OAuth público debe ser Authorization Code + PKCE.')
  if (!Array.isArray(record.scopes) || record.scopes.length !== 1 || record.scopes[0] !== 'openid') {
    throw new Error('El único scope permitido para Cognito es openid.')
  }
  if (record.syntheticDataOnly !== true) throw new Error('La configuración debe declarar datos exclusivamente sintéticos.')
  if (!record.deploymentBinding || typeof record.deploymentBinding !== 'object' || Array.isArray(record.deploymentBinding)) {
    throw new Error('deploymentBinding debe ser un objeto público verificable.')
  }
  const binding = record.deploymentBinding as Record<string, unknown>
  if (
    Object.keys(binding).length !== 2
    || !Object.hasOwn(binding, 'sourceRevision')
    || !Object.hasOwn(binding, 'frontendReleaseSha256')
    || typeof binding.sourceRevision !== 'string'
    || !/^[0-9a-f]{40}$/.test(binding.sourceRevision)
    || typeof binding.frontendReleaseSha256 !== 'string'
    || !/^[0-9a-f]{64}$/.test(binding.frontendReleaseSha256)
  ) {
    throw new Error('deploymentBinding no coincide con los hashes exactos del candidato.')
  }
  const expiresAt = record.expiresAt === null ? null : requiredString(record.expiresAt, 'expiresAt')
  if (expiresAt !== null && !Number.isFinite(Date.parse(expiresAt))) throw new Error('expiresAt no es una fecha válida.')

  const awsRegion = requiredString(record.awsRegion, 'awsRegion')
  if (!/^[a-z]{2}(?:-gov)?-[a-z]+-\d$/.test(awsRegion)) {
    throw new Error('awsRegion no coincide con una región AWS válida.')
  }

  const apiBaseUrl = httpsUrl(record.apiBaseUrl, 'apiBaseUrl')
  const apiHost = new RegExp(`^[a-z0-9-]+\\.execute-api\\.${escapeRegExp(awsRegion)}\\.amazonaws\\.com$`, 'i')
  if (!apiHost.test(apiBaseUrl.hostname) || apiBaseUrl.search || apiBaseUrl.hash) {
    throw new Error('apiBaseUrl debe apuntar al host regional de API Gateway sin query ni fragmento.')
  }

  const cognitoDomain = httpsUrl(record.cognitoDomain, 'cognitoDomain')
  const cognitoHost = new RegExp(`^[a-z0-9-]+\\.auth\\.${escapeRegExp(awsRegion)}\\.amazoncognito\\.com$`, 'i')
  if (!cognitoHost.test(cognitoDomain.hostname) || cognitoDomain.pathname !== '/' || cognitoDomain.search || cognitoDomain.hash) {
    throw new Error('cognitoDomain debe apuntar al dominio regional de Cognito sin rutas adicionales.')
  }

  const cognitoUserPool = requiredString(record.cognitoUserPool, 'cognitoUserPool')
  const userPoolPattern = new RegExp(`^${escapeRegExp(awsRegion)}_[A-Za-z0-9]+$`)
  if (!userPoolPattern.test(cognitoUserPool)) {
    throw new Error('cognitoUserPool debe estar prefijado por la región configurada.')
  }

  const redirectUri = httpsUrl(record.redirectUri, 'redirectUri')
  const logoutUri = httpsUrl(record.logoutUri, 'logoutUri')
  const cloudFrontHost = /^[a-z0-9]+\.cloudfront\.net$/i
  if (!cloudFrontHost.test(redirectUri.hostname) || !cloudFrontHost.test(logoutUri.hostname)) {
    throw new Error('redirectUri y logoutUri deben apuntar al origen CloudFront del piloto.')
  }
  if (redirectUri.origin !== logoutUri.origin) {
    throw new Error('redirectUri y logoutUri deben compartir el mismo origen CloudFront.')
  }
  if (redirectUri.pathname !== '/auth/callback' || redirectUri.search || redirectUri.hash) {
    throw new Error('redirectUri debe terminar exactamente en /auth/callback.')
  }
  if (logoutUri.pathname !== '/' || logoutUri.search || logoutUri.hash) {
    throw new Error('logoutUri debe ser la raíz exacta del origen CloudFront.')
  }

  return Object.freeze({
    apiBaseUrl: apiBaseUrl.toString().replace(/\/$/, ''),
    awsRegion,
    cognitoUserPool,
    cognitoClientId: requiredString(record.cognitoClientId, 'cognitoClientId'),
    cognitoDomain: cognitoDomain.origin,
    redirectUri: redirectUri.toString(),
    logoutUri: logoutUri.toString(),
    oauthFlow: 'authorization_code_pkce',
    scopes: ['openid'] as ['openid'],
    expiresAt,
    syntheticDataOnly: true,
    deploymentBinding: Object.freeze({
      sourceRevision: binding.sourceRevision,
      frontendReleaseSha256: binding.frontendReleaseSha256,
    }),
  })
}

export async function loadRuntimeConfig(): Promise<PublicRuntimeConfig> {
  const response = await fetch('/runtime-config.json', {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error('No se pudo cargar /runtime-config.json después del despliegue de infraestructura.')
  const contentType = response.headers.get('Content-Type')?.toLowerCase() ?? ''
  if (!contentType.includes('application/json')) {
    throw new Error('/runtime-config.json no devolvió JSON; el fallback SPA no es una configuración válida.')
  }
  try {
    return parseRuntimeConfig(await response.json())
  } catch (caught) {
    if (caught instanceof SyntaxError) throw new Error('/runtime-config.json contiene JSON inválido.')
    throw caught
  }
}
