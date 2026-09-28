import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { href } from '../../src/lib/router';
import type { CampaignDoc, Index, RunDoc, TrialDoc } from '../../src/lib/types';

// Every route over the published dataset (public/data): every campaign, run, density and trial it holds, plus the
// fixed pages. Stricter than the fixture smoke: no request may fail (the real dataset has every screenshot), every
// image must decode, and no page may show a value the site couldn't format.
const DATA = fileURLToPath(new URL('../../public/data', import.meta.url));
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const index = read<Index>('index.json');

// full: a trial page, and whether its screenshots link to full size (DATA.md, rule 10).
type Route = { name: string; path: string; h1?: RegExp | string; ready?: string; full?: boolean };
const featured = read<CampaignDoc>('campaigns', index.featured!, 'campaign.json');
const ROUTES: Route[] = [
  { name: 'home', path: './', h1: 'What is Fleetkit?', ready: 'Architecture' },
  { name: 'results', path: `./${href({ name: 'results', campaign: null })}`, h1: featured.title, ready: 'What we tested' },
  { name: 'compare', path: `./${href({ name: 'compare', specs: null })}`, h1: 'Compare specs', ready: 'How it performed' },
];
for (const e of index.campaigns) {
  const c = read<CampaignDoc>('campaigns', e.id, 'campaign.json');
  ROUTES.push({ name: `results-${c.id}`, path: `./${href({ name: 'results', campaign: c.id })}`, h1: c.title, ready: 'Every trial' });
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
    const full = (t: string) => read<TrialDoc>('campaigns', c.id, 'runs', r.id, `${t}.json`).full_size_screenshots;
    for (const t of r.trials) {
      ROUTES.push({ name: `trial-${c.id}-${r.id}-${t.id}`, path: trial(t.id), ready: 'Final screens', full: full(t.id) });
    }
    const last = r.trials.filter((t) => t.counts).at(-1);
    if (last) ROUTES.push({ name: `trial-${c.id}-${r.id}-${last.id}-microvm-3`, path: trial(last.id, 3), ready: 'Final screens', full: full(last.id) });
  }
}
ROUTES.push(
  { name: 'about', path: './#/about', h1: 'What is Fleetkit?', ready: 'Architecture' },
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
        // Rule 10: a trial's screenshots link to full size exactly when the dataset has it; otherwise they say so.
        if (r.full !== undefined) {
          expect(full.length > 0, 'links to full-size screenshots').toBe(r.full);
          await expect(page.getByText('Thumbnails only', { exact: true })).toHaveCount(r.full ? 0 : 1);
        }

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

  test('opens Results on the featured campaign, selected, with no synthetic banner', async ({ page }) => {
    await page.goto(`./${href({ name: 'results', campaign: null })}`);
    await expect(page.locator('h1')).toHaveText(featured.title);
    // The campaigns, labelled and counted, as tabs; the selected one controls the results below.
    await expect(page.locator('#campaigns-label')).toHaveText(new RegExp(`^Campaigns\\s*${index.campaigns.length}$`));
    const chooser = page.getByRole('tablist', { name: /^Campaigns/ });
    await expect(chooser.getByRole('tab')).toHaveCount(index.campaigns.length);
    const selected = chooser.getByRole('tab', { selected: true });
    await expect(selected).toHaveCount(1);
    await expect(selected).toContainText(featured.title);
    await expect(page.locator('#campaign-panel')).toHaveAttribute('role', 'tabpanel');
    await expect(page.locator('#campaign-panel')).toHaveAttribute('aria-labelledby', `campaign-tab-${featured.id}`);
    await expect(page.getByText('Synthetic data')).toHaveCount(0);
  });

  test('shows what was tested, the criteria and how it performed', async ({ page }) => {
    await page.goto(`./${href({ name: 'results', campaign: CAP })}`);
    await expect(page.locator('.single .in').first()).toContainText('m8i.4xlarge');
    await expect(page.locator('.single')).toContainText('16 vCPU · 64 GiB · nested');
    await expect(page.getByRole('list', { name: 'Success criteria' }).getByRole('listitem')).toHaveCount(5);
    // The standard SLOs: no chip is marked as differing from them.
    await expect(page.locator('dl.strip .slos li.on')).toHaveCount(0);
    await expect(page.locator('.top .lead')).toHaveText('8 microVMs met every SLO, 0.50 per host vCPU. Host CPU ran out at 12.');
    await expect(page.locator('.answer .figs')).toHaveText(/^Max density\s*8\s*Per vCPU\s*0\.50\s*\$ \/ 1k tasks\s*\$0\.068–0\.083\s*Ran out\s*Host CPU\s*at 12$/);
    // Latency by density, with no click: a mark per counting trial in each panel, against its SLO.
    const lat = page.locator('.lat');
    await expect(lat.locator('figcaption')).toHaveText(['Slowest step p50 (ms)', 'Task p95 (ms)']);
    await expect(lat.locator('svg a.mark')).toHaveCount(18);
    await expect(lat.locator('text.slo-label')).toHaveText(['SLO ≤ 1,000 ms', 'SLO ≤ 5,000 ms']);
    await expect(lat.getByRole('link', { name: 'trial 1 at density 12, failed: slowest step p50 1,279 ms' })).toBeVisible();
    // At the limit: the first failing trial, compact, and a way into it.
    const limit = page.locator('.atlimit');
    await limit.scrollIntoViewIfNeeded();
    await expect(limit.getByRole('tablist')).toHaveCount(0);
    await expect(limit.locator('.title')).toHaveText(/^Density 12\s*trial 1$/);
    await expect(limit.locator('.result')).toContainText('home p50 1,279 ms > 1,000 ms');
    await expect(limit.getByRole('link', { name: 'Open this trial →' })).toHaveAttribute('href', `#/results/${CAP}/runs/${RUN}/trials/d12-t1`);
    await expect(limit.locator('.lanes svg a')).toHaveCount(12);
    await expect(limit.locator('figure.tc figcaption')).toContainText(['Host CPU by process (vCPUs)', 'Host CPU pressure, the rule that ran out']);
    await expect(limit.getByRole('list', { name: 'Host CPU key' })).toContainText("microVMs' vCPUs");
    // Every trial: its marks open their trials, and say so.
    await expect(page.locator('figure.chart .hint')).toHaveText(/^(Click|Tap) a mark to open its trial$/);
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
    // Trial 1 at 8 is the run's last pass (rule 10): each final screen opens at full size.
    await page.goto(`./${href({ name: 'trial', campaign: CAP, run: RUN, trial: 'd8-t1', microvm: null })}`);
    await expect(page.getByRole('heading', { name: 'Final screens' })).toBeVisible();
    await scrollThrough(page);
    await expect(page.locator('.shots img')).toHaveCount(8);
    await expect(page.locator('.shots a[href$=".f.webp"] img')).toHaveCount(8);
    await page.goto(`./${href({ name: 'trial', campaign: CAP, run: RUN, trial: 'illustration', microvm: null })}`);
    await expect(page.getByRole('heading', { name: 'Filmstrip' })).toBeVisible();
    await scrollThrough(page);
    await expect(page.locator('.shots.film img')).toHaveCount(5);
    await expect(page.locator('.shots.film a[href$=".f.webp"] img')).toHaveCount(5);
  });

  test('shows thumbnails only, labelled, for a trial between the ends (rule 10)', async ({ page }) => {
    await page.goto(`./${href({ name: 'trial', campaign: CAP, run: RUN, trial: 'd8-t2', microvm: 3 })}`);
    await expect(page.getByRole('heading', { name: 'Final screens' })).toBeVisible();
    await scrollThrough(page);
    await expect(page.getByText('Thumbnails only', { exact: true })).toBeVisible();
    await expect(page.locator('.shots img')).toHaveCount(8);
    await expect(page.locator('a[href$=".f.webp"]')).toHaveCount(0);
    await expect(page.locator('figcaption', { hasText: /^Final screen, thumbnail only$/ })).toHaveCount(1);
  });

  test('opens each frame of the home page filmstrip at full size', async ({ page }) => {
    await page.goto('./');
    const film = page.locator('ol.film');
    await film.scrollIntoViewIfNeeded();
    await expect(film.locator('a[href$=".f.webp"] img')).toHaveCount(5);
    for (const u of await film.locator('a').evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).href))) {
      expect((await page.request.get(u)).status(), u).toBe(200);
    }
  });
});

test.describe('the published nested-hv-1', () => {
  const HV = 'nested-hv-1';
  const hv = read<CampaignDoc>('campaigns', HV, 'campaign.json');
  const counting = hv.runs
    .map((e) => read<RunDoc>('campaigns', HV, 'runs', `${e.id}.json`))
    .reduce((n, r) => n + r.trials.filter((t) => t.counts).length, 0);

  test('chooses campaigns from labelled tabs, by pointer or keyboard', async ({ page }) => {
    await page.goto(`./${href({ name: 'results', campaign: HV })}`);
    const tabs = page.getByRole('tablist', { name: /^Campaigns/ });
    const selected = tabs.getByRole('tab', { selected: true });
    await expect(selected).toContainText(hv.title);
    await expect(selected).toContainText(hv.question);
    // One tab stop: the selected campaign. The arrow keys move, Enter opens, and focus stays on the chooser.
    await expect(tabs.getByRole('tab').and(page.locator('[tabindex="0"]'))).toHaveCount(1);
    await selected.focus();
    await page.keyboard.press('ArrowRight');
    const next = index.campaigns[(index.campaigns.findIndex((c) => c.id === HV) + 1) % index.campaigns.length];
    await expect(tabs.getByRole('tab', { name: new RegExp(`^${next.title}`) })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(new RegExp(`#/results/${next.id}$`));
    await expect(page.locator('h1')).toHaveText(next.title);
    await expect(tabs.getByRole('tab', { selected: true })).toBeFocused();
    await tabs.getByRole('tab', { name: new RegExp(`^${hv.title}`) }).click();
    await expect(page.locator('h1')).toHaveText(hv.title);
  });

  test('shows latency by density for every spec and replica without a click', async ({ page }) => {
    await page.goto(`./${href({ name: 'results', campaign: HV })}`);
    const lat = page.locator('.lat');
    await expect(lat.locator('svg a.mark')).toHaveCount(counting * 2);
    await expect(lat.getByRole('list', { name: 'Key' })).toHaveText(
      /Firecracker PCI \+ RNG\s*Cloud Hypervisor\s*Firecracker MMIO\s*replica 1\s*replica 2\s*failed\s*over the SLO/,
    );
    // Pointing at (or tabbing to) a trial reads it out.
    await lat.getByRole('link', { name: /^Cloud Hypervisor · replica 2 · trial 3 at density 10, failed: slowest step p50/ }).focus();
    await expect(lat.locator('.read')).toContainText('Cloud Hypervisor · replica 2 · trial 3 at density 10');
  });

  test('loads one trial at the limit, only when it comes into view, and switches spec with tabs', async ({ page }) => {
    const trials: string[] = [];
    page.on('request', (r) => {
      const m = new URL(r.url()).pathname.match(/\/runs\/[^/]+\/([^/]+)\.json$/);
      if (m) trials.push(r.url());
    });
    await page.goto(`./${href({ name: 'results', campaign: HV })}`);
    await expect(page.locator('.lat svg a.mark').first()).toBeVisible();
    await page.waitForLoadState('networkidle');
    expect(trials).toEqual([]);

    const limit = page.locator('.atlimit');
    await limit.scrollIntoViewIfNeeded();
    const tabs = limit.getByRole('tablist', { name: 'Spec' });
    await expect(tabs.getByRole('tab')).toHaveText([/^\s*Firecracker PCI \+ RNG\s*fails at 10$/, /^\s*Cloud Hypervisor\s*fails at 10$/, /^\s*Firecracker MMIO\s*fails at 9$/]);
    const open = limit.getByRole('link', { name: 'Open this trial →' });
    await expect(open).toHaveAttribute('href', `#/results/${HV}/runs/firecracker-r1/trials/d10-t1`);
    await expect(limit.locator('.lanes svg a')).toHaveCount(10);
    expect(trials).toHaveLength(1);

    await tabs.getByRole('tab', { name: /^Firecracker MMIO/ }).click();
    await expect(open).toHaveAttribute('href', `#/results/${HV}/runs/firecracker-mmio-r1/trials/d9-t3`);
    await expect(limit.locator('.title')).toHaveText(/^Density 9\s*trial 3 · replica 1$/);
    await expect(limit.locator('.lanes svg a')).toHaveCount(9);
    expect(trials).toHaveLength(2);
    await tabs.getByRole('tab', { selected: true }).press('ArrowLeft');
    await expect(tabs.getByRole('tab', { name: /^Cloud Hypervisor/ })).toHaveAttribute('aria-selected', 'true');
    await expect(open).toHaveAttribute('href', `#/results/${HV}/runs/cloud-hypervisor-r1/trials/d10-t1`);
    await open.click();
    await expect(page.locator('h1')).toHaveText('Trial 1 at density 10');
  });
});

test.describe('the published hv-host-2: a metal host, judged by SLOs of its own (D74)', () => {
  const METAL = 'hv-host-2';
  const RUN = 'firecracker-metal-r1';
  const LOOSER = 'Looser SLOs: step p50 ≤ 2 s, step p95 ≤ 3 s, ready ≤ 900 s';
  /** The method's standard SLOs, the input schema's defaults. */
  const STANDARD = { ready_timeout_s: 180, step_p50_target_ms: 1000, step_p95_target_ms: 2000, task_p95_target_ms: 5000, step_timeout_ms: 10000, task_timeout_ms: 45000 };
  const standard = (c: CampaignDoc) =>
    c.specs.every((s) => Object.entries(STANDARD).every(([k, v]) => s.spec.criteria[k as keyof typeof STANDARD] === v));
  const campaigns = index.campaigns.map((e) => read<CampaignDoc>('campaigns', e.id, 'campaign.json'));

  test('About states the standard SLOs, whichever campaign is featured, and the featured result names its own', async ({ page }) => {
    await page.goto('./');
    await expect(page.getByRole('list', { name: 'Success criteria' }).getByRole('listitem')).toHaveText([
      /^≤ 180 s\s*Browser ready$/,
      /^100%\s*Tasks succeed$/,
      /^≤ 1 s\s*Each step p50$/,
      /^≤ 2 s\s*Each step p95$/,
      /^≤ 5 s\s*Whole task p95$/,
    ]);
    const tag = page.getByRole('complementary', { name: 'Featured result' }).locator('.slo-tag');
    if (featured.id === METAL) await expect(tag).toHaveText(LOOSER);
    else if (standard(featured)) await expect(tag).toHaveCount(0);
  });

  test('tags each campaign card whose SLOs are not the standard ones, and only those', async ({ page }) => {
    await page.goto(`./${href({ name: 'results', campaign: METAL })}`);
    const tabs = page.getByRole('tablist', { name: /^Campaigns/ });
    await expect(tabs.getByRole('tab', { name: /^Metal host/ }).locator('.slo-tag')).toHaveText(LOOSER);
    await expect(tabs.locator('.slo-tag')).toHaveCount(campaigns.filter((c) => !standard(c)).length);
    for (const c of campaigns.filter(standard)) await expect(page.locator(`#campaign-tab-${c.id} .slo-tag`)).toHaveCount(0);
  });

  test('highlights the SLOs that differ from the standard on its page, with the standard beside each', async ({ page }) => {
    await page.goto(`./${href({ name: 'results', campaign: METAL })}`);
    const slos = page.locator('dl.strip').getByRole('list', { name: 'Success criteria' });
    await expect(slos.getByRole('listitem')).toHaveCount(5);
    await expect(slos.locator('li.on')).toHaveText([/^Ready\s*≤ 900 s\s*standard ≤ 180 s$/, /^Step p50\s*≤ 2 s\s*standard ≤ 1 s$/, /^Step p95\s*≤ 3 s\s*standard ≤ 2 s$/]);
    await expect(slos.locator('li:not(.on)')).toHaveText([/^Tasks\s*100%$/, /^Task p95\s*≤ 5 s$/]);
  });

  test('says the SLOs differ above the charts when a metal spec is compared with nested ones', async ({ page }) => {
    await page.goto(`./${href({ name: 'compare', specs: [`${METAL}/firecracker-metal`, 'nested-sizes-1/m8i-4xlarge'] })}`);
    const note = page.getByRole('note').filter({ hasText: 'SLOs differ' });
    await expect(note).toHaveText('SLOs differ: Metal host uses step p50 ≤ 2 s, step p95 ≤ 3 s and ready ≤ 900 s; the rest use the standard.');
    const [noteBox, chartBox] = [await note.boundingBox(), await page.locator('.answer').boundingBox()];
    expect(noteBox!.y).toBeLessThan(chartBox!.y);
    // The latency chart names whose each step limit is; the task limit is shared, so it names no one.
    await expect(page.locator('.lat .pn-step text.slo-label')).toHaveText(['SLO ≤ 1,000 ms · standard', 'SLO ≤ 2,000 ms · Metal host']);
    await expect(page.locator('.lat .pn-task text.slo-label')).toHaveText(['SLO ≤ 5,000 ms']);
    // Each host says its kind when metal sits beside nested.
    await expect(page.locator('dl.strip .item').first()).toContainText(/m8i\.metal-48xl\s*192 vCPU · 768 GiB · metal/);
    await expect(page.locator('dl.strip .item').first()).toContainText(/m8i\.4xlarge\s*16 vCPU · 64 GiB · nested/);
    // Specs judged alike: no line.
    await page.goto(`./${href({ name: 'compare', specs: ['nested-sizes-1/m8i-4xlarge', 'nested-sizes-1/m8i-2xlarge'] })}`);
    await expect(page.getByRole('heading', { name: 'How it performed' })).toBeVisible();
    await expect(page.locator('.slo-diff')).toHaveCount(0);
  });

  /** The host CPU chart's y-axis top tick, its host-vCPU line and the highest point any layer reaches (SVG y grows down). */
  async function cpuChart(page: Page, title: string) {
    const svg = page.locator('figure.tc').filter({ has: page.locator('figcaption', { hasText: title }) }).locator('svg');
    await expect(svg).toBeVisible();
    return svg.evaluate((el) => {
      const ceiling = Number(el.querySelector('line.ref')!.getAttribute('y1'));
      const ys = [...el.querySelectorAll('path')].flatMap((p) => [...(p.getAttribute('d') ?? '').matchAll(/[ML][\d.-]+,([\d.-]+)/g)].map((m) => Number(m[1])));
      const ticks = [...el.querySelectorAll('text.tick')].map((t) => t.textContent ?? '').filter((t) => !t.endsWith(' s')).map((t) => Number(t.replace(/,/g, '')));
      return { ceiling, highest: Math.min(...ys), top: Math.max(...ticks) };
    });
  }

  test('never draws more host CPU than the host has, on the trial page and at the limit', async ({ page }) => {
    const r = read<RunDoc>('campaigns', METAL, 'runs', `${RUN}.json`);
    expect(r.host.vcpus).toBe(192);
    // d192-t2 recorded a boot sample of about 1,150 vCPUs: a sampling artifact of cumulative counters.
    await page.goto(`./${href({ name: 'trial', campaign: METAL, run: RUN, trial: 'd192-t2', microvm: null })}`);
    const trial = await cpuChart(page, 'Host CPU (vCPUs)');
    expect(trial.highest).toBeGreaterThanOrEqual(trial.ceiling - 0.2);
    expect(trial.top).toBeLessThanOrEqual(192 * 1.1);
    await page.goto(`./${href({ name: 'results', campaign: METAL })}`);
    await page.locator('.atlimit').scrollIntoViewIfNeeded();
    const limit = await cpuChart(page, 'Host CPU by process (vCPUs)');
    expect(limit.highest).toBeGreaterThanOrEqual(limit.ceiling - 0.2);
    expect(limit.top).toBeLessThanOrEqual(192 * 1.1);
  });

  test('packs 192 microVM lanes into about 480 px, numbered every 10th, and keeps a selected one in view', async ({ page }) => {
    const every10 = ['1', ...Array.from({ length: 19 }, (_, i) => String((i + 1) * 10))];
    await page.goto(`./${href({ name: 'trial', campaign: METAL, run: RUN, trial: 'd192-t2', microvm: null })}`);
    const lanes = page.locator('.lanes svg').first();
    expect(Number(await lanes.getAttribute('height'))).toBeLessThanOrEqual(480);
    await expect(lanes.locator('text.lane-label')).toHaveText(every10);
    // Selected, microVM 3 gets its number and a ring; the 1 beside it makes room.
    await page.goto(`./${href({ name: 'trial', campaign: METAL, run: RUN, trial: 'd192-t2', microvm: 3 })}`);
    await expect(page.locator('.lanes svg').first().locator('text.lane-label')).toHaveText(['3', ...every10.slice(1)]);
    await expect(page.locator('.lanes svg rect.sel-ring')).toHaveCount(1);
    // At the limit, compact, the same.
    await page.goto(`./${href({ name: 'results', campaign: METAL })}`);
    const limit = page.locator('.atlimit');
    await limit.scrollIntoViewIfNeeded();
    await expect(limit.locator('.lanes svg text.lane-label')).toHaveText(every10);
    expect(Number(await limit.locator('.lanes svg').getAttribute('height'))).toBeLessThanOrEqual(480);
  });
});

test.describe('links what is public on GitHub (D73)', () => {
  const GH = 'https://github.com/evandbrown/fleetkit';
  const DATA = `${GH}/blob/main/site/public/data`;
  const CASES = [
    { id: 'nested-sizes-1', definition: 'experiments/campaigns/nested-sizes-1.json' },   // a nested campaign
    { id: 'hv-host-2', definition: 'experiments/campaigns/hv-host-2.json' },             // the metal campaign
    { id: 'cap-baseline-1', definition: 'docs/capacity-experiment.md' },                 // reconstructed: its pre-registration
  ];

  async function expectSource(page: Page, want: [string, string][]) {
    const source = page.getByRole('navigation', { name: 'Source' });
    await expect(source.getByRole('link')).toHaveCount(want.length);
    for (const [i, [name, url]] of want.entries()) {
      const a = source.getByRole('link').nth(i);
      await expect(a).toHaveAccessibleName(name);
      await expect(a).toHaveAttribute('href', url);
      await expect(a).toHaveAttribute('target', '_blank');
      await expect(a).toHaveAttribute('rel', 'noopener');
    }
  }

  for (const k of CASES) {
    test(`${k.id}: its campaign and each run link the definition, their data and the harness commit`, async ({ page }) => {
      const c = read<CampaignDoc>('campaigns', k.id, 'campaign.json');
      const commits = [...new Set(c.runs.map((r) => r.harness_commit))];
      for (const x of commits) expect(x).toMatch(/^[0-9a-f]{40}$/);
      if (k.id === 'cap-baseline-1') expect(commits).toEqual(['652f26d88cda86a453e31e29f9def2e771dd4971']);
      const definition: [string, string] = ['Definition', `${GH}/blob/main/${k.definition}`];
      const harness = (x: string): [string, string] => [`Harness @ ${x.slice(0, 7)}`, `${GH}/tree/${x}`];

      await page.goto(`./${href({ name: 'results', campaign: k.id })}`);
      await expect(page.getByRole('heading', { name: 'What we tested' })).toBeVisible();
      await expectSource(page, [definition, ['Data', `${DATA}/campaigns/${k.id}/campaign.json`], ...commits.map((x) => harness(x!))]);

      for (const r of c.runs) {
        await page.goto(`./${href({ name: 'run', campaign: k.id, run: r.id, density: null })}`);
        await expect(page.getByRole('heading', { name: 'What limited it' })).toBeVisible();
        await expectSource(page, [definition, ['Data', `${DATA}/campaigns/${k.id}/runs/${r.id}.json`], harness(r.harness_commit!)]);
      }
      const r = read<RunDoc>('campaigns', k.id, 'runs', `${c.runs[0].id}.json`);
      const t = r.trials.find((x) => x.counts)!;
      await page.goto(`./${href({ name: 'trial', campaign: k.id, run: r.id, trial: t.id, microvm: null })}`);
      await expect(page.getByRole('heading', { name: 'Final screens' })).toBeVisible();
      await expectSource(page, [definition, ['Data', `${DATA}/campaigns/${k.id}/runs/${r.id}/${t.id}.json`], harness(r.harness_commit!)]);
    });
  }

  test('a campaign whose runs used two harness commits links each once, in run order', async ({ page }) => {
    const c = read<CampaignDoc>('campaigns', 'nested-hv-1', 'campaign.json');
    const commits = [...new Set(c.runs.map((r) => r.harness_commit!))];
    expect(commits).toHaveLength(2);
    await page.goto(`./${href({ name: 'results', campaign: c.id })}`);
    await expect(page.getByRole('heading', { name: 'What we tested' })).toBeVisible();
    await expectSource(page, [
      ['Definition', `${GH}/blob/main/experiments/campaigns/nested-hv-1.json`],
      ['Data', `${DATA}/campaigns/nested-hv-1/campaign.json`],
      ...commits.map((x): [string, string] => [`Harness @ ${x.slice(0, 7)}`, `${GH}/tree/${x}`]),
    ]);
  });
});
