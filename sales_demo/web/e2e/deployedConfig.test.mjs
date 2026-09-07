import assert from 'node:assert/strict'
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { loadDeployedE2EConfig } from './deployedConfig.mjs'

const NOT_A_SECRET = 'NotARealCredential!234'

function credentials() {
  return {
    schemaVersion: 1,
    syntheticOnly: true,
    users: {
      customerA: { username: 'customer-a', password: NOT_A_SECRET },
      managerA: { username: 'manager-a', password: NOT_A_SECRET },
      customerB: { username: 'customer-b', password: NOT_A_SECRET },
      managerB: { username: 'manager-b', password: NOT_A_SECRET },
    },
  }
}

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), 'approvals-deployed-e2e-'))
  const file = join(directory, 'credentials.json')
  writeFileSync(file, JSON.stringify(credentials()), { mode: 0o600 })
  const environment = {
    APPROVALS_E2E_BASE_URL: 'https://d111111abcdef8.cloudfront.net',
    APPROVALS_E2E_CREDENTIALS_FILE: file,
    APPROVALS_E2E_EXPECTED_SOURCE_REVISION: '1'.repeat(40),
    APPROVALS_E2E_EXPECTED_RELEASE_SHA256: '2'.repeat(64),
    APPROVALS_E2E_EXPECTED_EXPIRES_AT: '2099-08-26T23:00:00Z',
    APPROVALS_E2E_CONFIRM_ACTIVE_WRITES: 'YES',
    APPROVALS_E2E_MODE: 'ACTIVE_BUSINESS',
  }
  return { directory, environment, file }
}

test('acepta únicamente el contrato remoto exacto y privado', () => {
  const value = fixture()
  try {
    const parsed = loadDeployedE2EConfig(value.environment, { projectRoot: process.cwd(), now: 0 })
    assert.equal(parsed.baseUrl, 'https://d111111abcdef8.cloudfront.net')
    assert.equal(parsed.users.customerA.username, 'customer-a')
    assert.equal(parsed.users.managerB.role, 'MANAGER')
  } finally {
    rmSync(value.directory, { recursive: true, force: true })
  }
})

test('falla cerrado si no se reconoce explícitamente que habrá escrituras', () => {
  const value = fixture()
  try {
    value.environment.APPROVALS_E2E_CONFIRM_ACTIVE_WRITES = 'no'
    assert.throws(() => loadDeployedE2EConfig(value.environment), /exactamente YES/)
  } finally {
    rmSync(value.directory, { recursive: true, force: true })
  }
})

test('acepta readiness PREPARED sin expiración ni autorización de escrituras', () => {
  const value = fixture()
  try {
    value.environment.APPROVALS_E2E_MODE = 'PREPARED_READINESS'
    value.environment.APPROVALS_E2E_CONFIRM_PREPARED_READS = 'YES'
    delete value.environment.APPROVALS_E2E_EXPECTED_EXPIRES_AT
    delete value.environment.APPROVALS_E2E_CONFIRM_ACTIVE_WRITES
    const parsed = loadDeployedE2EConfig(value.environment, { projectRoot: process.cwd() })
    assert.equal(parsed.mode, 'PREPARED_READINESS')
    assert.equal(parsed.expiresAt, null)
  } finally {
    rmSync(value.directory, { recursive: true, force: true })
  }
})

test('rechaza credenciales legibles por grupo u otros usuarios', () => {
  const value = fixture()
  try {
    chmodSync(value.file, 0o640)
    assert.throws(() => loadDeployedE2EConfig(value.environment), /modo 0600/)
  } finally {
    rmSync(value.directory, { recursive: true, force: true })
  }
})

test('rechaza identidades distintas de las cuatro allowlisted sin filtrar contraseñas', () => {
  const value = fixture()
  try {
    const payload = credentials()
    payload.users.customerA.username = 'unexpected-user'
    writeFileSync(value.file, JSON.stringify(payload), { mode: 0o600 })
    assert.throws(
      () => loadDeployedE2EConfig(value.environment),
      (error) => error instanceof Error && !error.message.includes(NOT_A_SECRET) && /allowlisted/.test(error.message),
    )
  } finally {
    rmSync(value.directory, { recursive: true, force: true })
  }
})

test('el carril desplegado permanece separado del demo y sin artefactos sensibles', () => {
  const spec = readFileSync(new URL('./deployed.spec.mjs', import.meta.url), 'utf8')
  const config = readFileSync(new URL('../playwright.deployed.config.mjs', import.meta.url), 'utf8')
  assert.doesNotMatch(spec, /localStorage|Entrar a la demostración|Cambiar persona/)
  for (const contract of [
    'Continuar con Cognito',
    'code_challenge_method',
    'CONTRACT_ACTIVE',
    'PENDING_MANAGER',
    'REJECTED',
    'NOT_FOUND',
    'replayed',
    'Rastro de auditoría',
    'Servicio sintético activado',
  ]) {
    assert.match(spec, new RegExp(contract))
  }
  assert.doesNotMatch(config, /webServer/)
  assert.match(config, /retries:\s*0/)
  assert.match(config, /workers:\s*1/)
  assert.match(config, /trace:\s*'off'/)
  assert.match(config, /screenshot:\s*'off'/)
  assert.match(config, /video:\s*'off'/)
})
