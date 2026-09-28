/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fixturesData, refuseSyntheticData } from './scripts/data-guard.mjs';

// Served under evan.mx/fleetkit/, so every URL is relative: base './' and hash routes.
export default defineConfig(({ mode }) => ({
  base: './',
  plugins: [
    svelte(),
    refuseSyntheticData(),
    // `npm run dev:fixtures`: the fixtures (including synthetic campaigns) at ./data/, dev server only.
    mode === 'fixtures' ? fixturesData('tests/fixtures/data') : null,
  ],
  build: {
    target: 'es2022',
    sourcemap: false,
  },
  // The site bundles the input schema (experiments/schema) for spec labels, so the dev server may read it.
  server: { port: 5173, fs: { allow: ['.', '../experiments/schema'] } },
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'node',
  },
}));
