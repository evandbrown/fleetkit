import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { href } from '../../src/lib/router';
import type { CampaignDoc, Index, RunDoc } from '../../src/lib/types';

// Every route over the published dataset (public/data): every campaign, run, density and trial it holds, plus the
// fixed pages. Stricter than the fixture smoke: no request may fail (the real dataset has every screenshot), every
// image must decode, and no page may show a value the site couldn't format.
const DATA = fileURLToPath(new URL('../../public/data', import.meta.url));
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const index = read<Index>('index.json');

type Route = { name: string; path: string; h1?: RegExp | string; ready?: string };
const featured = read<CampaignDoc>('campaigns', index.featured!, 'campaign.json');
const ROUTES: Route[] = [
  { name: 'home', path: './', h1: featured.title, ready: 'How it performed' },
  { name: 'results', path: `./${href({ name: 'results', campaign: null })}`, h1: featured.title, ready: 'What we tested' },
  { name: 'compare', path: `./${href({ name: 'compare', specs: null })}`, h1: 'Compare specs', ready: 'How it performed' },
];
for (const e of index.campaigns) {
  const c = read<CampaignDoc>('campaigns', e.id, 'campaign.json');
  ROUTES.push({ name: `results-${c.id}`, path: `./${href({ name: 'results', campaign: c.id })}`, h1: c.title, ready: 'Success criteria' });
  for (const entry of c.runs) {
    const r = read<RunDoc>('campaigns', c.id, 'runs', `${entry.id}.json`);
    const run = (density: number | null) => `./${href({ name: 'run', campaign: c.id, run: r.id, density })}`;
    const trial = (t: string, microvm: number | null = null) => `./${href({ name: 'trial', campaign: c.id, run: r.id, trial: t, microvm })}`;
    // A run page is titled by its spec (and replica), as the campaign labels it: checked below for cap-baseline-1.
    ROUTES.push({ name: `run-${c.id}-${r.id}`, path: run(null), ready: 'What limited it' });
    for (const d of r.by_density) {
      if (d.result === 'not_tested') continue;
      ROUTES.push({ name: `run-${c.id}-${r.id}-density-${d.density}`, path: run(d.density), ready: 'Densities' });
    }
    for (const t of r.trials) {
      ROUTES.push({ name: `trial-${c.id}-${r.id}-${t.id}`, path: trial(t.id), ready: 'Final screens' });
    }
    const last = r.trials.filter((t) => t.counts).at(-1);
    if (last) ROUTES.push({ name: `trial-${c.id}-${r.id}-${last.id}-microvm-3`, path: trial(last.id, 3), ready: 'Final screens' });
  }
}
ROUTES.push(
  { name: 'about', path: './#/about', h1: 'What we evaluate', ready: 'Architecture' },
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

for (const scheme of ['light'] as const) {
  test.describe(`published data, every route, ${scheme}`, () => {
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
  const CAP = 'cap-baseline-1';
  const RUN = 'baseline-r1';

  test('opens on the featured campaign, selected, with no synthetic banner', async ({ page }) => {
    await page.goto('./');
    await expect(page.locator('h1')).toHaveText(featured.title);
    const chooser = page.getByRole('navigation', { name: 'Results' });
    // The campaigns are always shown as cards to choose from, even when there is one.
    await expect(chooser.getByRole('link')).toHaveCount(index.campaigns.length);
    await expect(chooser.locator('[aria-current="page"]')).toContainText(featured.title);
    await expect(page.getByText('Synthetic data')).toHaveCount(0);
  });

  test('shows what was tested, the criteria and how it performed', async ({ page }) => {
    await page.goto(`./${href({ name: 'results', campaign: CAP })}`);
    await expect(page.locator('.single .in').first()).toContainText('m8i.4xlarge');
    await expect(page.locator('.single')).toContainText('16 vCPU · 64 GiB · nested');
    await expect(page.getByRole('list', { name: 'Success criteria' }).getByRole('listitem')).toHaveCount(5);
    await expect(page.locator('dl.stats .v')).toHaveText(['8', '0.50', '$0.068–0.083', 'Host CPU']);
    await expect(page.locator('figure.chart > svg a[href*="/trials/"]')).toHaveCount(9);
    await expect(page.locator('figure.chart')).toContainText('9–11 not tested');
  });

  test('gives the run result and its trials', async ({ page }) => {
    await page.goto(`./${href({ name: 'run', campaign: CAP, run: RUN, density: null })}`);
    await expect(page.locator('h1')).toHaveText('m8i.4xlarge · Firecracker');
    await expect(page.locator('p.crumbs')).toHaveText(/› m8i\.4xlarge · Firecracker$/);
    await expect(page.locator('table.densities tbody tr').nth(4)).toContainText('home p50 1,063–1,563 ms');
    await expect(page.locator('table.densities tbody tr').nth(5)).toContainText('not tested');
    await expect(page.getByRole('link', { name: /^trial \d at density (8|12), (passed|failed)$/ })).toHaveCount(6);
  });

  test('shows every final screen of a trial and the illustration filmstrip', async ({ page }) => {
    await page.goto(`./${href({ name: 'trial', campaign: CAP, run: RUN, trial: 'd8-t1', microvm: null })}`);
    await expect(page.getByRole('heading', { name: 'Final screens' })).toBeVisible();
    await scrollThrough(page);
    await expect(page.locator('.shots img')).toHaveCount(8);
    await page.goto(`./${href({ name: 'trial', campaign: CAP, run: RUN, trial: 'illustration', microvm: null })}`);
    await expect(page.getByRole('heading', { name: 'Filmstrip' })).toBeVisible();
    await scrollThrough(page);
    await expect(page.locator('.shots.film img')).toHaveCount(5);
  });
});
