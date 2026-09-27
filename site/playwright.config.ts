import { defineConfig, devices } from '@playwright/test';

// End-to-end tests run against the built site, served under /fleetkit/ as in production, with the fixtures
// (including the synthetic campaign) served at ./data/ by the preview server, never copied into dist/.
export default defineConfig({
  testDir: 'tests/e2e',
  fullyParallel: true,
  reporter: 'list',
  use: { baseURL: 'http://localhost:4173/fleetkit/' },
  webServer: {
    command: 'npm run build && node scripts/serve.mjs --dir dist --base /fleetkit/ --data tests/fixtures/data --port 4173',
    url: 'http://localhost:4173/fleetkit/',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } } },
    { name: 'phone', use: { ...devices['Pixel 7'], viewport: { width: 390, height: 844 } } },
  ],
});
