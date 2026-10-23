import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests (M15). Playwright starts a throwaway backend (../backend/scripts/e2e_backend.sh: fresh
 * collectflow_e2e database, seeded, fake Gemini and Gmail) and a production build of this app.
 * Journeys share that database, so they run one at a time.
 */
const API_PORT = 8002;
const WEB_PORT = 3002;
const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: ci,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: ci ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "desktop",
      dependencies: ["setup"],
      testIgnore: /mobile\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1366, height: 900 } },
    },
    {
      name: "mobile",
      dependencies: ["desktop"],
      testMatch: /mobile\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, hasTouch: true },
    },
  ],
  webServer: [
    {
      command: "../backend/scripts/e2e_backend.sh",
      url: `http://127.0.0.1:${API_PORT}/api/health/`,
      env: { E2E_API_PORT: String(API_PORT) },
      timeout: 240_000,
      reuseExistingServer: false,
      stdout: "ignore",
    },
    {
      command: `npm run build && npx next start -p ${WEB_PORT} -H 127.0.0.1`,
      url: `http://127.0.0.1:${WEB_PORT}/login`,
      env: { API_BASE_URL: `http://127.0.0.1:${API_PORT}`, NEXT_PUBLIC_API_BASE_URL: `http://127.0.0.1:${API_PORT}` },
      timeout: 600_000,
      reuseExistingServer: !ci,
    },
  ],
});
