import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import type { CampaignDoc, Index, RunDoc } from '../../src/lib/types';

// Every route over the published dataset (public/data): every campaign, run, density and trial it holds, plus the
// fixed pages. Stricter than the fixture smoke: no request may fail (the real dataset has every screenshot), every
// image must decode, and no page may show a value the site couldn't format.
const DATA = fileURLToPath(new URL('../../public/data', import.meta.url));
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const index = read<Index>('index.json');

type Route = { name: string; path: string; h1?: RegExp | string; ready?: string };
const ROUTES: Route[] = [
  { name: 'home', path: './', ready: 'Latency against density' },
  { name: 'campaigns', path: './#/campaigns', h1: 'Campaigns' },
];
for (const e of index.campaigns) {
  const c = read<CampaignDoc>('campaigns', e.id, 'campaign.json');
  ROUTES.push({ name: `campaign-${c.id}`, path: `./#/campaigns/${c.id}`, h1: c.title, ready: 'Latency against density' });
  for (const entry of c.runs) {
    const r = read<RunDoc>('campaigns', c.id, 'runs', `${entry.id}.json`);
    const base = `./#/campaigns/${c.id}/runs/${r.id}`;
    ROUTES.push({ name: `run-${r.id}`, path: base, h1: `Run ${r.id}`, ready: 'What limited it' });
    for (const d of r.by_density) {
      if (d.result === 'not_run') continue;
      ROUTES.push({ name: `run-${r.id}-density-${d.density}`, path: `${base}?density=${d.density}`, ready: `Trials at density ${d.density}` });
    }
    for (const t of r.trials) {
      ROUTES.push({ name: `trial-${r.id}-${t.id}`, path: `${base}/trials/${t.id}`, ready: 'Final screens' });
    }
    const last = r.trials.filter((t) => t.counts).at(-1);
    if (last) ROUTES.push({ name: `trial-${r.id}-${last.id}-microvm-3`, path: `${base}/trials/${last.id}?microvm=3`, ready: 'Final screens' });
  }
}
ROUTES.push(
  { name: 'builder', path: './#/builder', h1: 'Experiment builder' },
  { name: 'method', path: './#/method' },
  { name: 'about', path: './#/about', h1: 'About' },
  { name: 'not-found', path: './#/nowhere', h1: 'Not found' },
);

function watch(page: Page, baseURL: string) {
  const base = new URL(baseURL);
  const outside: string[] = [];
  const failed: string[] = [];
  const errors: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.protocol.startsWith('http') && !(u.host === base.host && u.pathname.startsWith(base.pathname))) outside.push(r.url());
  });
  page.on('requestfailed', (r) => failed.push(`${r.url()} ${r.failure()?.errorText}`));
  page.on('response', (r) => {
    if (r.status() >= 400) failed.push(`${r.url()} ${r.status()}`);
  });
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  return { outside, failed, errors };
}

/** Scrolls through the page so lazy screenshots load, then back to the top. */
async function scrollThrough(page: Page) {
  await page.evaluate(async () => {
    const step = window.innerHeight;
    for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 60));
    }
    window.scrollTo(0, 0);
  });
  await page.waitForLoadState('networkidle');
}

const SHOTS = process.env.SCREENSHOTS;

for (const scheme of ['light', 'dark'] as const) {
  test.describe(`published data, every route, ${scheme}`, () => {
    test.use({ colorScheme: scheme });
    for (const r of ROUTES) {
      test(r.name, async ({ page, baseURL }) => {
        const w = watch(page, baseURL!);
        await page.goto(r.path);
        if (r.h1) await expect(page.locator('h1')).toHaveText(r.h1);
        else await expect(page.locator('h1')).not.toHaveText('');
        if (r.ready) await expect(page.getByRole('heading', { name: r.ready, exact: true })).toBeVisible();
        if (r.name !== 'not-found') await expect(page.getByRole('alert')).toHaveCount(0);
        await page.waitForLoadState('networkidle');
        await scrollThrough(page);

        // Every screenshot is in the dataset and decodes; every full-size link resolves.
        await expect(page.locator('.shot .missing')).toHaveCount(0);
        const broken = await page.$$eval('img', (imgs) =>
          imgs.filter((i) => !i.complete || i.naturalWidth === 0).map((i) => i.getAttribute('src')),
        );
        expect(broken).toEqual([]);
        const full = await page.$$eval('a[href$=".f.webp"]', (as) => [...new Set(as.map((a) => (a as HTMLAnchorElement).href))]);
        for (const u of full) expect((await page.request.get(u)).status(), u).toBe(200);

        // Nothing the site failed to format.
        const text = await page.locator('main').innerText();
        expect(text).not.toMatch(/\bNaN\b|\bundefined\b|\[object Object\]|\bInfinity\b/);
        expect(text).not.toMatch(/Synthetic/i);

        const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
        expect(sw).toBeLessThanOrEqual(cw);
        expect(w.outside).toEqual([]);
        expect(w.failed).toEqual([]);
        expect(w.errors).toEqual([]);
        if (SHOTS) {
          mkdirSync(SHOTS, { recursive: true });
          await page.screenshot({ path: join(SHOTS, `${test.info().project.name}-${r.name}-${scheme}.png`), fullPage: true });
        }
      });
    }
  });
}

test.describe('the published cap-baseline-1', () => {
  test('opens on the most recent campaign, with no synthetic banner', async ({ page }) => {
    await page.goto('./');
    await expect(page).toHaveURL(new RegExp(`#/campaigns/${index.latest}$`));
    await expect(page.getByText('Synthetic data')).toHaveCount(0);
    // (a glossary word's definition sits in the DOM beside it, so match the prose between the terms)
    await expect(page.locator('p.answer')).toContainText('on one m8i.4xlarge worker host (16 vCPUs = 8 cores × 2 threads, nested): 3 of 3 trials passed');
  });

  test('gives the run result and its trials', async ({ page }) => {
    await page.goto('./#/campaigns/cap-baseline-1/runs/baseline-r1');
    await expect(page.getByText('Density 8 tested successfully; density 12 failed; 9–11 not tried; 16 not run.')).toBeVisible();
    await expect(page.getByRole('link', { name: /^trial \d at density (8|12)$/ })).toHaveCount(6);
  });

  test('shows every final screen of a trial and the illustration filmstrip', async ({ page }) => {
    await page.goto('./#/campaigns/cap-baseline-1/runs/baseline-r1/trials/d8-t1');
    await expect(page.getByRole('heading', { name: 'Final screens' })).toBeVisible();
    await scrollThrough(page);
    await expect(page.locator('.shots img')).toHaveCount(8);
    await page.goto('./#/campaigns/cap-baseline-1/runs/baseline-r1/trials/illustration');
    await expect(page.getByRole('heading', { name: 'Filmstrip' })).toBeVisible();
    await scrollThrough(page);
    await expect(page.locator('.shots.film img')).toHaveCount(5);
  });
});
