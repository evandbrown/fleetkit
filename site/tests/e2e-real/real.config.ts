// The end-to-end tests over the published dataset (public/data), with no fixtures served. Run it after the
// dataset builder, with the Google Chrome already installed on this machine:
//   npx playwright test -c tests/e2e-real/real.config.ts
// It builds into .vite/e2e-real (ignored by git) and serves on its own port, so it never touches dist/.
// SCREENSHOTS=<dir> also saves a full-page screenshot of every route there.
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';

const SITE = fileURLToPath(new URL('../..', import.meta.url));
const PORT = Number(process.env.E2E_PORT ?? 4181);

export default defineConfig({
  testDir: '.',
  outputDir: `${SITE}/test-results/real`,
  fullyParallel: true,
  reporter: 'list',
  use: { baseURL: `http://localhost:${PORT}/fleetkit/`, channel: 'chrome' },
  webServer: {
    command:
      `npx vite build --outDir .vite/e2e-real --emptyOutDir -l warn && ` +
      `node scripts/serve.mjs --dir .vite/e2e-real --base /fleetkit/ --port ${PORT}`,
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
