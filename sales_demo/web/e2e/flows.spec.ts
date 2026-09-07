import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

async function freshLogin(page: Page, identity: string) {
  await page.goto('/ingreso')
  await page.evaluate(() => {
    localStorage.clear()
    sessionStorage.clear()
  })
  await page.reload()
  await page.getByLabel(new RegExp(identity)).check()
  await page.getByRole('button', { name: 'Entrar a la demostración' }).click()
}

async function switchPersona(page: Page, identity: string) {
  await page.getByRole('button', { name: 'Cambiar persona' }).click()
  await expect(page.getByRole('heading', { name: 'Elige una persona sintética' })).toBeVisible()
  await page.getByLabel(new RegExp(identity)).check()
  await page.getByRole('button', { name: 'Entrar a la demostración' }).click()
}

async function createApplication(page: Page, count: number): Promise<string> {
  await page.getByRole('navigation', { name: 'Navegación principal' }).getByRole('link', { name: 'Nueva solicitud' }).click()
  await page.getByLabel('Cantidad de vehículos').fill(String(count))
  await page.getByRole('button', { name: 'Registrar solicitud' }).click()
  await expect(page.getByText(`${count} vehículos`, { exact: true })).toBeVisible()
  const match = page.url().match(/solicitudes\/(app_[0-9a-f]{32})/)
  expect(match, 'la URL debe contener el identificador opaco de solicitud').not.toBeNull()
  return match![1]
}

test.beforeEach(async ({ page }) => {
  await freshLogin(page, 'Customer sintético A')
})

test('50 vehículos se aprueban automáticamente y activan Servicio', async ({ page }) => {
  const applicationId = await createApplication(page, 50)

  await expect(page.getByText('Contrato y Servicio activos', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Contrato sintético activado')).toBeVisible()
  await page.getByRole('link', { name: 'Ver Servicio' }).click()
  await expect(page.getByRole('heading', { name: 'Servicio sintético activado' })).toBeVisible()
  await expect(page.getByText(applicationId)).toBeVisible()
  await expect(page.getByText('Demostración · datos sintéticos · sin PII real')).toBeVisible()

  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})

test('51 vehículos requieren y conservan aprobación del Manager', async ({ page }) => {
  const applicationId = await createApplication(page, 51)
  await expect(page.locator('[data-status="PENDING_MANAGER"]')).toContainText('Requiere aprobación del Manager')

  await switchPersona(page, 'Manager sintético A')
  await page.getByRole('link', { name: new RegExp(applicationId) }).click()
  await page.getByLabel('Aprobar').check()
  await page.getByLabel('Código de razón').selectOption('CAPACITY_CONFIRMED')
  await page.getByRole('button', { name: 'Confirmar decisión' }).click()

  await expect(page.locator('[data-decision-accepted="true"]')).toContainText('Decisión aceptada')
  await expect(page.locator('[data-decision-accepted="true"]')).toContainText(/Consulta [1-7] de 7/)
  await expect(page.getByText('Decisión aprobada y estado durable confirmado.')).toBeVisible()
  await expect(page.getByText('Decisión del Manager registrada')).toBeVisible()
  await expect(page.getByRole('list', { name: 'Rastro de auditoría' }).getByText('Capacidad operativa confirmada')).toHaveCount(2)

  await switchPersona(page, 'Customer sintético A')
  await page.getByRole('link', { name: 'Servicio' }).click()
  await expect(page.getByText(applicationId)).toBeVisible()
})

test('52 vehículos pueden rechazarse con razón auditable', async ({ page }) => {
  const applicationId = await createApplication(page, 52)

  await switchPersona(page, 'Manager sintético A')
  await page.getByRole('link', { name: new RegExp(applicationId) }).click()
  await page.getByLabel('Rechazar').check()
  await page.getByLabel('Código de razón').selectOption('CAPACITY_NOT_AVAILABLE')
  await page.getByRole('button', { name: 'Confirmar decisión' }).click()

  await expect(page.locator('[data-decision-accepted="true"]')).toContainText('Decisión aceptada')
  await expect(page.getByText('Rechazo registrado y estado durable confirmado.')).toBeVisible()
  await expect(page.locator('[data-status="REJECTED"]')).toContainText('Rechazada')
  await expect(page.getByText('Solicitud rechazada')).toBeVisible()
  await expect(page.getByRole('list', { name: 'Rastro de auditoría' }).getByText('Capacidad operativa no disponible')).toHaveCount(2)
  await expect(page.getByText(/contrato y Servicio activos/i)).toHaveCount(0)
})

test('un tenant ajeno recibe 404 sin revelar existencia', async ({ page }) => {
  const applicationId = await createApplication(page, 51)

  await switchPersona(page, 'Customer sintético B')
  await page.goto(`/solicitudes/${applicationId}`)
  await expect(page.getByRole('heading', { name: 'No se encontró la solicitud' })).toBeVisible()
  await expect(page.getByText('El recurso no existe o no pertenece a tu tenant. La respuesta no revela cuál de las dos condiciones aplica.')).toBeVisible()
})

test('teclado, foco y landmarks son operables', async ({ page }) => {
  await page.goto('/solicitudes')
  await expect(page.getByRole('heading', { name: 'Solicitudes de flota' })).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Saltar al contenido principal' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('#contenido-principal')).toBeFocused()

  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})

test('el ingreso sintético es explícito, accesible y no ofrece registro público', async ({ page }) => {
  await page.getByRole('button', { name: 'Cambiar persona' }).click()
  await expect(page.getByText('Demostración · datos sintéticos · sin PII real')).toBeVisible()
  await expect(page.getByText('Modo demo local')).toBeVisible()
  await expect(page.getByRole('radio')).toHaveCount(4)
  await expect(page.getByText(/registrarse|crear cuenta/i)).toHaveCount(0)

  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', '/manifest.webmanifest')
  const pwa = await page.evaluate(async () => {
    const manifest = (await fetch('/manifest.webmanifest').then((response) => response.json())) as { display?: string }
    const registration = await navigator.serviceWorker.ready
    return { display: manifest.display, scope: registration.scope }
  })
  expect(pwa.display).toBe('standalone')
  expect(new URL(pwa.scope).pathname).toBe('/')

  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})

test('el service worker nunca guarda el código OAuth en Cache Storage', async ({ page }) => {
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready
  })
  await page.reload()
  await page.goto('/auth/callback?code=oauth-code-must-not-be-cached&state=synthetic-state')
  await page.waitForLoadState('networkidle')

  const cachedUrls = await page.evaluate(async () => {
    const urls: string[] = []
    for (const cacheName of await caches.keys()) {
      const cache = await caches.open(cacheName)
      urls.push(...(await cache.keys()).map((request) => request.url))
    }
    return urls
  })
  expect(cachedUrls.join('\n')).not.toContain('code=')
  expect(cachedUrls.join('\n')).not.toContain('/auth/callback')
})
