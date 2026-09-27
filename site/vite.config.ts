/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { methodDoc } from './scripts/method-doc.mjs';
import { fixturesData, refuseSyntheticData } from './scripts/data-guard.mjs';

// Served under evan.mx/fleetkit/, so every URL is relative: base './' and hash routes.
export default defineConfig(({ mode }) => ({
  base: './',
  plugins: [
    svelte(),
    methodDoc('../docs/method.md'),
    refuseSyntheticData(),
    // `npm run dev:fixtures`: the fixtures (including synthetic campaigns) at ./data/, dev server only.
    mode === 'fixtures' ? fixturesData('tests/fixtures/data') : null,
  ],
  build: {
    target: 'es2022',
    sourcemap: false,
  },
  server: { port: 5173 },
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'node',
  },
}));
