import { expect, test } from '@playwright/test'
import { loadDeployedE2EConfig } from './deployedConfig.mjs'

const deployed = loadDeployedE2EConfig()
const SYNTHETIC_BANNER = 'Demostración · datos sintéticos · sin PII real'
const APPLICATION_ID = /^app_[0-9a-f]{32}$/

function exactKeys(value, expected, label) {
  expect(value, `${label} debe ser un objeto`).not.toBeNull()
  expect(Array.isArray(value), `${label} no puede ser un arreglo`).toBe(false)
  expect(Object.keys(value).sort(), `${label} debe conservar el contrato exacto`).toEqual([...expected].sort())
}

async function preflight(request) {
  const runtimeResponse = await request.get(`${deployed.baseUrl}/runtime-config.json`, {
    headers: { Accept: 'application/json' },
  })
  expect(runtimeResponse.status(), 'runtime-config.json debe estar disponible').toBe(200)
  expect(runtimeResponse.headers()['content-type'] ?? '').toContain('application/json')
  expect(runtimeResponse.headers()['cache-control'] ?? '').toContain('no-store')
  const runtime = await runtimeResponse.json()
  exactKeys(runtime, [
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
  ], 'runtime-config.json')
  exactKeys(runtime.deploymentBinding, ['sourceRevision', 'frontendReleaseSha256'], 'deploymentBinding')
  expect(runtime.syntheticDataOnly).toBe(true)
  expect(runtime.oauthFlow).toBe('authorization_code_pkce')
  expect(runtime.scopes).toEqual(['openid'])
  expect(runtime.expiresAt).toBe(deployed.expiresAt)
  expect(runtime.deploymentBinding.sourceRevision).toBe(deployed.sourceRevision)
  expect(runtime.deploymentBinding.frontendReleaseSha256).toBe(deployed.frontendReleaseSha256)
  expect(new URL(runtime.redirectUri).origin).toBe(deployed.baseUrl)
  expect(new URL(runtime.redirectUri).pathname).toBe('/auth/callback')
  expect(new URL(runtime.logoutUri).toString()).toBe(`${deployed.baseUrl}/`)
  expect(new URL(runtime.cognitoDomain).protocol).toBe('https:')
  expect(new URL(runtime.apiBaseUrl).protocol).toBe('https:')

  const healthResponse = await request.get(`${runtime.apiBaseUrl}/health`, {
    headers: { Accept: 'application/json' },
  })
  expect(healthResponse.status(), 'el piloto debe exponer health antes de autenticar usuarios').toBe(200)
  const health = await healthResponse.json()
  exactKeys(health, ['status', 'expiresAt', 'syntheticData', 'pii'], 'GET /health')
  const expectedStatus = deployed.mode === 'PREPARED_READINESS' ? 'PREPARED' : 'ACTIVE'
  expect(health).toEqual({
    status: expectedStatus,
    expiresAt: deployed.expiresAt,
    syntheticData: true,
    pii: false,
  })
  if (deployed.mode === 'ACTIVE_BUSINESS') {
    expect(Date.parse(health.expiresAt)).toBeGreaterThan(Date.now())
  }
  return runtime
}

async function loginWithCognito(browser, runtime, user) {
  const context = await browser.newContext({
    baseURL: deployed.baseUrl,
    acceptDownloads: false,
  })
  const page = await context.newPage()
  try {
    await page.goto('/ingreso')
    await expect(page.getByRole('heading', { name: 'Ingresa con Cognito' })).toBeVisible()
    await expect(page.getByText('Modo demo local')).toHaveCount(0)
    await page.getByRole('button', { name: 'Continuar con Cognito' }).click()
    await page.waitForURL((url) => url.origin === new URL(runtime.cognitoDomain).origin, { waitUntil: 'domcontentloaded' })

    const authorizeUrl = new URL(page.url())
    expect(authorizeUrl.searchParams.get('response_type')).toBe('code')
    expect(authorizeUrl.searchParams.get('client_id')).toBe(runtime.cognitoClientId)
    expect(authorizeUrl.searchParams.get('redirect_uri')).toBe(runtime.redirectUri)
    expect(authorizeUrl.searchParams.get('scope')).toBe('openid')
    expect(authorizeUrl.searchParams.get('code_challenge_method')).toBe('S256')
    expect(authorizeUrl.searchParams.get('code_challenge') ?? '').toMatch(/^[A-Za-z0-9_-]{43,128}$/)
    expect(authorizeUrl.searchParams.get('state') ?? '').toMatch(/^[A-Za-z0-9_-]{20,}$/)
    expect(authorizeUrl.searchParams.has('client_secret')).toBe(false)

    const username = page.locator('#signInFormUsername, input[name="username"]').first()
    const password = page.locator('#signInFormPassword, input[name="password"]').first()
    const submit = page.locator('#signInSubmitButton, input[name="signInSubmitButton"], button[type="submit"]').first()
    await expect(username, 'Cognito Hosted UI debe exponer username').toBeVisible()
    await expect(password, 'Cognito Hosted UI debe exponer password').toBeVisible()
    await username.fill(user.username)
    await password.fill(user.password)
    await submit.click()

    const homePath = user.role === 'CUSTOMER' ? '/solicitudes' : '/aprobaciones'
    try {
      await page.waitForURL((url) => url.origin === deployed.baseUrl && url.pathname === homePath, {
        timeout: 45_000,
        waitUntil: 'domcontentloaded',
      })
    } catch {
      throw new Error(`Cognito no completó el retorno seguro para la identidad sintética ${user.username}.`)
    }
    expect(new URL(page.url()).searchParams.has('code')).toBe(false)
    expect(new URL(page.url()).searchParams.has('state')).toBe(false)
    const sessionContract = await page.evaluate(() => ({
      accessToken: Boolean(sessionStorage.getItem('approvals-access-token')),
      refreshToken: Boolean(sessionStorage.getItem('approvals-refresh-token')),
      pkceVerifierCleared: sessionStorage.getItem('approvals-pkce-verifier') === null,
      oauthStateCleared: sessionStorage.getItem('approvals-oauth-state') === null,
      demoIdentityAbsent: sessionStorage.getItem('approvals-demo-identity') === null,
    }))
    expect(sessionContract).toEqual({
      accessToken: true,
      refreshToken: true,
      pkceVerifierCleared: true,
      oauthStateCleared: true,
      demoIdentityAbsent: true,
    })
    await expect(page.getByText(SYNTHETIC_BANNER)).toBeVisible()
    await expect(page.getByText('Sesión Cognito con PKCE')).toBeVisible()
    const sessionFacts = page.getByLabel('Contexto de sesión')
    await expect(sessionFacts).toContainText(user.tenantId)
    await expect(sessionFacts).toContainText(user.role === 'CUSTOMER' ? 'Customer' : 'Manager')
    return { context, page }
  } catch (error) {
    await context.close()
    throw error
  }
}

async function createApplication(page, vehicleCount) {
  await page.goto('/solicitudes/nueva')
  await page.getByLabel('Cantidad de vehículos').fill(String(vehicleCount))
  const createRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return request.method() === 'POST' && url.pathname.endsWith('/applications')
  })
  await page.getByRole('button', { name: 'Registrar solicitud' }).click()
  const request = await createRequest
  const idempotencyKey = request.headers()['idempotency-key']
  expect(idempotencyKey).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
  await page.waitForURL((url) => /\/solicitudes\/app_[0-9a-f]{32}$/.test(url.pathname))
  const applicationId = new URL(page.url()).pathname.split('/').at(-1)
  expect(applicationId).toMatch(APPLICATION_ID)
  await expect(page.getByText(`${vehicleCount} vehículos`, { exact: true })).toBeVisible()
  return { applicationId, idempotencyKey }
}

async function listApplicationIdsFromSession(page, apiBaseUrl) {
  return page.evaluate(async ({ endpoint }) => {
    const accessToken = sessionStorage.getItem('approvals-access-token')
    if (!accessToken) return { status: 0, applicationIds: [] }
    const response = await fetch(`${endpoint}/applications`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${accessToken}` },
    })
    if (!response.ok) return { status: response.status, applicationIds: [] }
    const payload = await response.json()
    return {
      status: response.status,
      applicationIds: Array.isArray(payload.items)
        ? payload.items.map((item) => item.applicationId).filter((value) => typeof value === 'string')
        : [],
    }
  }, { endpoint: apiBaseUrl })
}

async function replayApplicationFromSession(page, apiBaseUrl, vehicleCount, idempotencyKey) {
  return page.evaluate(async ({ endpoint, count, key }) => {
    const accessToken = sessionStorage.getItem('approvals-access-token')
    if (!accessToken) return { status: 0, replayed: false, applicationId: null, vehicleCount: null }
    const response = await fetch(`${endpoint}/applications`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
        'Idempotency-Key': key,
      },
      body: JSON.stringify({ vehicleCount: count }),
    })
    const payload = await response.json().catch(() => null)
    return {
      status: response.status,
      replayed: payload?.replayed === true,
      applicationId: payload?.application?.applicationId ?? null,
      vehicleCount: payload?.application?.vehicleCount ?? null,
    }
  }, { endpoint: apiBaseUrl, count: vehicleCount, key: idempotencyKey })
}

async function waitForDurableStatus(page, applicationId, status) {
  await expect.poll(async () => {
    await page.goto(`/solicitudes/${applicationId}`, { waitUntil: 'domcontentloaded' })
    const chip = page.locator('[data-status]').first()
    try {
      await chip.waitFor({ state: 'visible', timeout: 5_000 })
      return await chip.getAttribute('data-status')
    } catch {
      return 'UNAVAILABLE'
    }
  }, {
    message: `${applicationId} debe converger a ${status}`,
    timeout: 60_000,
    intervals: [500, 1_000, 2_000, 3_000],
  }).toBe(status)
}

async function decideApplication(page, applicationId, decision) {
  await page.goto('/aprobaciones')
  await page.getByRole('link', { name: new RegExp(applicationId) }).click()
  await expect(page.locator('[data-status="PENDING_MANAGER"]')).toBeVisible()

  let decisionPosts = 0
  const countDecisionPost = (request) => {
    const path = new URL(request.url()).pathname
    if (request.method() === 'POST' && path.endsWith(`/approvals/${applicationId}/decision`)) decisionPosts += 1
  }
  page.on('request', countDecisionPost)
  try {
    await page.getByLabel(decision === 'APPROVE' ? 'Aprobar' : 'Rechazar').check()
    await page.getByLabel('Código de razón').selectOption(
      decision === 'APPROVE' ? 'CAPACITY_CONFIRMED' : 'CAPACITY_NOT_AVAILABLE',
    )
    await page.getByRole('button', { name: 'Confirmar decisión' }).click()
    await expect(page.locator('[data-decision-accepted="true"]')).toContainText('Decisión aceptada')

    const completionText = decision === 'APPROVE'
      ? 'Decisión aprobada y estado durable confirmado.'
      : 'Rechazo registrado y estado durable confirmado.'
    const completion = page.getByText(completionText, { exact: true })
    const retry = page.getByRole('button', { name: 'Reintentar verificación' })
    const firstOutcome = await Promise.race([
      completion.waitFor({ state: 'visible', timeout: 30_000 }).then(() => 'complete'),
      retry.waitFor({ state: 'visible', timeout: 30_000 }).then(() => 'retry'),
    ])
    if (firstOutcome === 'retry') {
      await retry.click()
      await completion.waitFor({ state: 'visible', timeout: 30_000 })
    }
    expect(decisionPosts, 'un 202 aceptado nunca debe provocar un segundo POST').toBe(1)
  } finally {
    page.off('request', countDecisionPost)
  }
}

async function assertManagerAudit(page, decision, reasonLabel) {
  const event = page
    .getByRole('list', { name: 'Rastro de auditoría' })
    .getByRole('listitem')
    .filter({ has: page.getByRole('heading', { name: 'Decisión del Manager registrada' }) })
  await expect(event).toHaveCount(1)
  await expect(event.getByText(/^Manager · actor_[0-9a-f]{16}$/)).toBeVisible()
  await expect(event.getByText(decision === 'APPROVE' ? 'Aprobar' : 'Rechazar', { exact: true })).toBeVisible()
  await expect(event.getByText(reasonLabel, { exact: true })).toBeVisible()
  const occurredAt = await event.locator('time').getAttribute('datetime')
  expect(occurredAt).not.toBeNull()
  expect(Number.isFinite(Date.parse(occurredAt))).toBe(true)
  expect(Date.parse(occurredAt)).toBeLessThanOrEqual(Date.now())
}

test('piloto desplegado: Cognito PKCE, 50/51/52, Servicio y aislamiento tenant', async ({ browser, request }) => {
  const sessions = []
  try {
    const runtime = await test.step('preflight público exacto ACTIVE y release binding', () => preflight(request))

    const customerA = await test.step('autenticar customer-a por Cognito Authorization Code + PKCE', async () => {
      const session = await loginWithCognito(browser, runtime, deployed.users.customerA)
      sessions.push(session)
      return session.page
    })
    const managerA = await test.step('autenticar manager-a por Cognito Authorization Code + PKCE', async () => {
      const session = await loginWithCognito(browser, runtime, deployed.users.managerA)
      sessions.push(session)
      return session.page
    })
    const customerB = await test.step('autenticar customer-b por Cognito Authorization Code + PKCE', async () => {
      const session = await loginWithCognito(browser, runtime, deployed.users.customerB)
      sessions.push(session)
      return session.page
    })
    await test.step('autenticar manager-b sin producir efectos de negocio', async () => {
      const session = await loginWithCognito(browser, runtime, deployed.users.managerB)
      sessions.push(session)
    })

    if (deployed.mode === 'PREPARED_READINESS') {
      await test.step('confirmar PREPARED sin ejecutar efectos de negocio', async () => {
        for (const { page } of sessions) {
          await expect(page.getByRole('heading', { name: 'El piloto aún no está habilitado' })).toBeVisible()
        }
      })
      return
    }

    await test.step('50 vehículos convergen a contrato y Servicio activos', async () => {
      const before = await listApplicationIdsFromSession(customerA, runtime.apiBaseUrl)
      expect(before.status).toBe(200)
      const { applicationId, idempotencyKey } = await createApplication(customerA, 50)
      const replay = await replayApplicationFromSession(customerA, runtime.apiBaseUrl, 50, idempotencyKey)
      expect(replay).toEqual({ status: 200, replayed: true, applicationId, vehicleCount: 50 })
      const after = await listApplicationIdsFromSession(customerA, runtime.apiBaseUrl)
      expect(after.status).toBe(200)
      expect(after.applicationIds).toHaveLength(before.applicationIds.length + 1)
      expect(after.applicationIds.filter((candidate) => candidate === applicationId)).toHaveLength(1)
      await waitForDurableStatus(customerA, applicationId, 'CONTRACT_ACTIVE')
      await expect(customerA.getByText('Contrato y Servicio activos', { exact: true }).first()).toBeVisible()
      await customerA.getByRole('link', { name: 'Ver Servicio' }).click()
      await expect(customerA.getByRole('heading', { name: 'Servicio sintético activado' })).toBeVisible()
      await expect(customerA.getByText(applicationId)).toBeVisible()
      await expect(customerA.getByText(SYNTHETIC_BANNER)).toBeVisible()
    })

    const application51 = await test.step('51 vehículos requieren aprobación real del Manager', async () => {
      const { applicationId } = await createApplication(customerA, 51)
      await expect(customerA.locator('[data-status="PENDING_MANAGER"]')).toContainText('Requiere aprobación del Manager')
      await decideApplication(managerA, applicationId, 'APPROVE')
      await expect(managerA.locator('[data-status="CONTRACT_ACTIVE"]')).toContainText('Contrato y Servicio activos')
      await expect(managerA.getByText('Capacidad operativa confirmada').first()).toBeVisible()
      await assertManagerAudit(managerA, 'APPROVE', 'Capacidad operativa confirmada')
      await customerA.goto(`/solicitudes/${applicationId}`)
      await expect(customerA.locator('[data-status="CONTRACT_ACTIVE"]')).toBeVisible()
      await customerA.goto('/plan-plus')
      await expect(customerA.getByText(applicationId)).toBeVisible()
      return applicationId
    })

    await test.step('un Customer del tenant B recibe 404 indistinguible para un recurso del tenant A', async () => {
      const apiResponse = customerB.waitForResponse((response) => {
        const url = new URL(response.url())
        return response.request().method() === 'GET' && url.pathname.endsWith(`/applications/${application51}`)
      })
      await customerB.goto(`/solicitudes/${application51}`)
      const response = await apiResponse
      expect(response.status()).toBe(404)
      const problem = await response.json()
      expect(problem.code).toBe('NOT_FOUND')
      expect(problem.detail).toBe('The requested resource was not found.')
      await expect(customerB.getByRole('heading', { name: 'No se encontró la solicitud' })).toBeVisible()
      await expect(customerB.getByText('El recurso no existe o no pertenece a tu tenant. La respuesta no revela cuál de las dos condiciones aplica.')).toBeVisible()
    })

    await test.step('52 vehículos pueden rechazarse con razón durable y sin contrato', async () => {
      const { applicationId } = await createApplication(customerA, 52)
      await decideApplication(managerA, applicationId, 'REJECT')
      await expect(managerA.locator('[data-status="REJECTED"]')).toContainText('Rechazada')
      await expect(managerA.getByText('Capacidad operativa no disponible').first()).toBeVisible()
      await assertManagerAudit(managerA, 'REJECT', 'Capacidad operativa no disponible')
      await expect(managerA.getByText(/contrato y Servicio activos/i)).toHaveCount(0)
      await customerA.goto(`/solicitudes/${applicationId}`)
      await expect(customerA.locator('[data-status="REJECTED"]')).toContainText('Rechazada')
      await expect(customerA.getByText('Solicitud rechazada')).toBeVisible()
    })
  } finally {
    await Promise.allSettled(sessions.map(({ context }) => context.close()))
  }
})
