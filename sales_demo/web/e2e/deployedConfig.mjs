import { lstatSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { isAbsolute, relative, resolve } from 'node:path'

export const DEPLOYED_USER_SLOTS = Object.freeze({
  customerA: Object.freeze({ username: 'customer-a', tenantId: 'tenant-a', role: 'CUSTOMER' }),
  managerA: Object.freeze({ username: 'manager-a', tenantId: 'tenant-a', role: 'MANAGER' }),
  customerB: Object.freeze({ username: 'customer-b', tenantId: 'tenant-b', role: 'CUSTOMER' }),
  managerB: Object.freeze({ username: 'manager-b', tenantId: 'tenant-b', role: 'MANAGER' }),
})

const REQUIRED_ENV = Object.freeze([
  'APPROVALS_E2E_BASE_URL',
  'APPROVALS_E2E_CREDENTIALS_FILE',
  'APPROVALS_E2E_EXPECTED_SOURCE_REVISION',
  'APPROVALS_E2E_EXPECTED_RELEASE_SHA256',
  'APPROVALS_E2E_MODE',
])

function exactKeys(value, keys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} debe ser un objeto JSON.`)
  }
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} no coincide con el contrato exacto esperado.`)
  }
}

function requiredEnvironment(environment, required = REQUIRED_ENV) {
  const values = {}
  for (const name of required) {
    const value = environment[name]
    if (typeof value !== 'string' || value.trim() === '') {
      throw new Error(`Falta la variable obligatoria ${name}.`)
    }
    values[name] = value.trim()
  }
  return values
}

function validateBaseUrl(raw) {
  let url
  try {
    url = new URL(raw)
  } catch {
    throw new Error('APPROVALS_E2E_BASE_URL no es una URL válida.')
  }
  if (
    url.protocol !== 'https:'
    || url.username
    || url.password
    || url.port
    || url.pathname !== '/'
    || url.search
    || url.hash
    || !/^[a-z0-9]+\.cloudfront\.net$/i.test(url.hostname)
  ) {
    throw new Error('APPROVALS_E2E_BASE_URL debe ser la raíz HTTPS exacta de CloudFront.')
  }
  return url.origin
}

function isWithin(parent, child) {
  const pathFromParent = relative(parent, child)
  return pathFromParent === '' || (!pathFromParent.startsWith('..') && !isAbsolute(pathFromParent))
}

function readPrivateCredentials(rawPath, projectRoot) {
  if (!isAbsolute(rawPath)) throw new Error('APPROVALS_E2E_CREDENTIALS_FILE debe ser una ruta absoluta.')
  const requestedPath = resolve(rawPath)
  const requested = lstatSync(requestedPath)
  if (requested.isSymbolicLink() || !requested.isFile()) {
    throw new Error('El archivo privado de credenciales debe ser un archivo regular, no un enlace.')
  }
  const credentialsPath = realpathSync(requestedPath)
  if (isWithin(realpathSync(projectRoot), credentialsPath)) {
    throw new Error('El archivo privado de credenciales debe vivir fuera del repositorio.')
  }
  const metadata = statSync(credentialsPath)
  if ((metadata.mode & 0o077) !== 0) {
    throw new Error('El archivo privado de credenciales debe tener modo 0600.')
  }
  if (typeof process.getuid === 'function' && metadata.uid !== process.getuid()) {
    throw new Error('El archivo privado de credenciales debe pertenecer al usuario que ejecuta Playwright.')
  }
  if (metadata.size < 2 || metadata.size > 65_536) {
    throw new Error('El archivo privado de credenciales tiene un tamaño fuera del contrato.')
  }

  let parsed
  try {
    parsed = JSON.parse(readFileSync(credentialsPath, 'utf8'))
  } catch {
    throw new Error('El archivo privado de credenciales no contiene JSON válido.')
  }
  exactKeys(parsed, ['schemaVersion', 'syntheticOnly', 'users'], 'El archivo privado de credenciales')
  if (parsed.schemaVersion !== 1 || parsed.syntheticOnly !== true) {
    throw new Error('El archivo privado debe declarar schemaVersion=1 y syntheticOnly=true.')
  }
  exactKeys(parsed.users, Object.keys(DEPLOYED_USER_SLOTS), 'users')

  const users = {}
  for (const [slot, expected] of Object.entries(DEPLOYED_USER_SLOTS)) {
    const candidate = parsed.users[slot]
    exactKeys(candidate, ['password', 'username'], `users.${slot}`)
    if (candidate.username !== expected.username) {
      throw new Error(`users.${slot}.username no coincide con la identidad sintética allowlisted.`)
    }
    const password = candidate.password
    if (
      typeof password !== 'string'
      || password.length < 14
      || password.length > 256
      || !/[a-z]/.test(password)
      || !/[A-Z]/.test(password)
      || !/[0-9]/.test(password)
      || !/[^A-Za-z0-9]/.test(password)
      || password.toLowerCase().includes(expected.username)
    ) {
      throw new Error(`users.${slot}.password no satisface el contrato privado de Cognito.`)
    }
    users[slot] = Object.freeze({ ...expected, password })
  }
  return Object.freeze(users)
}

export function loadDeployedE2EConfig(environment = process.env, options = {}) {
  const values = requiredEnvironment(environment)
  const mode = values.APPROVALS_E2E_MODE
  if (!['PREPARED_READINESS', 'ACTIVE_BUSINESS'].includes(mode)) {
    throw new Error('APPROVALS_E2E_MODE debe ser PREPARED_READINESS o ACTIVE_BUSINESS.')
  }
  if (
    mode === 'PREPARED_READINESS'
    && environment.APPROVALS_E2E_CONFIRM_PREPARED_READS !== 'YES'
  ) {
    throw new Error('APPROVALS_E2E_CONFIRM_PREPARED_READS debe ser exactamente YES; el recorrido autentica cuatro identidades sin escrituras de negocio.')
  }
  if (
    mode === 'ACTIVE_BUSINESS'
    && environment.APPROVALS_E2E_CONFIRM_ACTIVE_WRITES !== 'YES'
  ) {
    throw new Error('APPROVALS_E2E_CONFIRM_ACTIVE_WRITES debe ser exactamente YES; el recorrido crea tres solicitudes sintéticas.')
  }
  const sourceRevision = values.APPROVALS_E2E_EXPECTED_SOURCE_REVISION
  const frontendReleaseSha256 = values.APPROVALS_E2E_EXPECTED_RELEASE_SHA256
  if (!/^[0-9a-f]{40}$/.test(sourceRevision)) {
    throw new Error('APPROVALS_E2E_EXPECTED_SOURCE_REVISION debe ser un SHA Git completo en minúsculas.')
  }
  if (!/^[0-9a-f]{64}$/.test(frontendReleaseSha256)) {
    throw new Error('APPROVALS_E2E_EXPECTED_RELEASE_SHA256 debe ser un SHA-256 completo en minúsculas.')
  }
  let expiresAt = null
  if (mode === 'ACTIVE_BUSINESS') {
    const active = requiredEnvironment(environment, ['APPROVALS_E2E_EXPECTED_EXPIRES_AT'])
    expiresAt = active.APPROVALS_E2E_EXPECTED_EXPIRES_AT
    const expiresAtEpoch = Date.parse(expiresAt)
    const now = options.now ?? Date.now()
    if (!Number.isFinite(expiresAtEpoch) || expiresAtEpoch <= now) {
      throw new Error('APPROVALS_E2E_EXPECTED_EXPIRES_AT debe ser un RFC3339 futuro.')
    }
  }
  const projectRoot = resolve(options.projectRoot ?? process.cwd())
  return Object.freeze({
    baseUrl: validateBaseUrl(values.APPROVALS_E2E_BASE_URL),
    mode,
    sourceRevision,
    frontendReleaseSha256,
    expiresAt,
    users: readPrivateCredentials(values.APPROVALS_E2E_CREDENTIALS_FILE, projectRoot),
  })
}
