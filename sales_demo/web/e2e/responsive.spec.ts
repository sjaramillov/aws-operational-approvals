import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

test('el flujo conserva controles y no desborda a 320 px', async ({ page }) => {
  await page.goto('/ingreso')
  await page.evaluate(() => {
    localStorage.clear()
    sessionStorage.clear()
  })
  await page.reload()
  await page.getByLabel(/Customer sintético A/).check()
  await page.getByRole('button', { name: 'Entrar a la demostración' }).click()
  await page.getByRole('navigation', { name: 'Navegación principal' }).getByRole('link', { name: 'Nueva solicitud' }).click()

  await expect(page.getByLabel('Cantidad de vehículos')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Registrar solicitud' })).toBeVisible()
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }))
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.width)

  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})
