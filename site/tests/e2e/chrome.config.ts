// The end-to-end tests with the Google Chrome already installed on this machine, for when Playwright's own
// Chromium hasn't been downloaded:  npx playwright test -c tests/e2e/chrome.config.ts
// It builds into .vite/e2e (ignored by git) and serves on its own port, so it never touches dist/ or a preview
// server someone else is running. SCREENSHOTS=<dir> also saves a full-page screenshot of every route there.
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';

const SITE = fileURLToPath(new URL('../..', import.meta.url));
const PORT = Number(process.env.E2E_PORT ?? 4179);

export default defineConfig({
  testDir: '.',
  outputDir: `${SITE}/test-results`,
  fullyParallel: true,
  reporter: 'list',
  use: { baseURL: `http://localhost:${PORT}/fleetkit/`, channel: 'chrome' },
  webServer: {
    command:
      `npx vite build --outDir .vite/e2e --emptyOutDir -l warn && ` +
      `node scripts/serve.mjs --dir .vite/e2e --base /fleetkit/ --data tests/fixtures/data --port ${PORT}`,
    cwd: SITE,
    url: `http://localhost:${PORT}/fleetkit/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], channel: 'chrome', viewport: { width: 1280, height: 900 } } },
    { name: 'phone', use: { ...devices['Pixel 7'], channel: 'chrome', viewport: { width: 390, height: 844 } } },
  ],
});
