// Runs the site's contract checks (src/lib/contract.ts) over a built dataset. The dataset builder calls this
// with FLEETKIT_DATA set to its staging directory: cd site && npx vitest run --config build/vitest.config.ts
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  root: fileURLToPath(new URL('..', import.meta.url)),
  test: {
    include: ['build/contract.test.ts'],
    environment: 'node',
  },
});
