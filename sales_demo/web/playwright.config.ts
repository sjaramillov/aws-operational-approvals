import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', testMatch: /flows\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-320', testMatch: /responsive\.spec\.ts/, use: { viewport: { width: 320, height: 740 } } },
  ],
  webServer: {
    command: 'npm run build:demo && npm run preview -- --port 4173',
    port: 4173,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
