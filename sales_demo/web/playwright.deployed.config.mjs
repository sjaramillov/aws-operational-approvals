import { defineConfig, devices } from '@playwright/test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadDeployedE2EConfig } from './e2e/deployedConfig.mjs'

const deployed = loadDeployedE2EConfig()

export default defineConfig({
  testDir: './e2e',
  testMatch: /deployed\.spec\.mjs/,
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  maxFailures: 1,
  timeout: 300_000,
  expect: { timeout: 30_000 },
  reporter: [['list']],
  outputDir: join(tmpdir(), `approvals-sales-deployed-e2e-${process.pid}`),
  preserveOutput: 'never',
  use: {
    baseURL: deployed.baseUrl,
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    acceptDownloads: false,
  },
  projects: [{ name: 'deployed-chromium', use: { ...devices['Desktop Chrome'] } }],
})
