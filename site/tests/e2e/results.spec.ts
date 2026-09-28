import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { href, type Route } from '../../src/lib/router';

// Results against the fixtures: the chooser and a campaign, compare, a run and a trial. Addresses come from the
// router, so these follow its routes.
const at = (r: Route) => `./${href(r)}`;

function watch(page: Page, baseURL: string) {
  const base = new URL(baseURL);
  const outside: string[] = [];
  const errors: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.protocol.startsWith('http') && !(u.host === base.host && u.pathname.startsWith(base.pathname))) outside.push(r.url());
  });
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    // Missing screenshots are expected: the fixtures have no image files.
    if (m.type() === 'error' && !/Failed to load resource.*404/.test(m.text())) errors.push(m.text());
  });
  return { outside, errors };
}

async function noHorizontalScroll(page: Page) {
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sw).toBeLessThanOrEqual(cw);
}

/** D70: distilled facts. No paragraph over two sentences, no term popovers, no UTC times, no clauses in headers. */
async function distilled(page: Page) {
  const long = await page.$$eval('main p', (ps) =>
    ps.map((p) => (p as HTMLElement).innerText).filter((t) => (t.match(/[.!?](\s|$)/g) ?? []).length > 2),
  );
  expect(long).toEqual([]);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  const headers = await page.$$eval('main th[scope="col"], main thead th', (ths) => ths.map((t) => (t as HTMLElement).innerText));
  expect(headers.filter((h) => /,\s/.test(h) || h.split(/\s+/).length > 4)).toEqual([]);
  expect(await page.locator('main').innerText()).not.toMatch(/\bUTC\b|campaign of one/i);
}

const SHOTS = process.env.SCREENSHOTS;

const S = 'nested-sizes-synthetic';
const HV = 'nested-hv-synthetic';
const CAP = 'cap-baseline-1';

const ROUTES: { name: string; path: string; h1: RegExp | string; ready: string }[] = [
  { name: 'results', path: at({ name: 'results', campaign: null }), h1: 'Synthetic host sizes', ready: 'What we tested' },
  { name: 'results-sizes', path: at({ name: 'results', campaign: S }), h1: 'Synthetic host sizes', ready: 'What we tested' },
  { name: 'results-hv', path: at({ name: 'results', campaign: HV }), h1: 'Synthetic hypervisors', ready: 'How it performed' },
  { name: 'results-cap', path: at({ name: 'results', campaign: CAP }), h1: 'Capacity baseline', ready: 'Every trial' },
  { name: 'compare', path: at({ name: 'compare', specs: null }), h1: 'Compare specs', ready: 'How it performed' },
  {
    name: 'compare-across',
    path: at({ name: 'compare', specs: [`${S}/m8i-2xlarge`, `${CAP}/baseline`] }),
    h1: 'Compare specs',
    ready: 'What we tested',
  },
  { name: 'run', path: at({ name: 'run', campaign: S, run: 'm8i-2xlarge-r2', density: null }), h1: 'm8i.2xlarge · replica 2', ready: 'What limited it' },
  {
    name: 'run-stopped-early',
    path: at({ name: 'run', campaign: HV, run: 'cloud-hypervisor-r2', density: null }),
    h1: 'Cloud Hypervisor · replica 2',
    ready: 'Densities',
  },
  { name: 'run-density', path: at({ name: 'run', campaign: CAP, run: 'baseline-r1', density: 12 }), h1: 'm8i.4xlarge · Firecracker', ready: 'Densities' },
  {
    name: 'trial',
    path: at({ name: 'trial', campaign: CAP, run: 'baseline-r1', trial: 'd12-t1', microvm: 3 }),
    h1: 'Trial 1 at density 12',
    ready: 'Final screens',
  },
  {
    name: 'trial-illustration',
    path: at({ name: 'trial', campaign: CAP, run: 'baseline-r1', trial: 'illustration', microvm: null }),
    h1: /^Illustration at density 1$/,
    ready: 'Filmstrip',
  },
];

test.describe('every results page', () => {
  for (const r of ROUTES) {
    test(r.name, async ({ page, baseURL }) => {
      const w = watch(page, baseURL!);
      await page.goto(r.path);
      await expect(page.locator('h1')).toHaveText(r.h1);
      await expect(page.getByRole('heading', { name: r.ready, exact: true })).toBeVisible();
      await page.waitForLoadState('networkidle');
      await noHorizontalScroll(page);
      expect(w.outside).toEqual([]);
      expect(w.errors).toEqual([]);
      expect(await page.locator('main').innerText()).not.toMatch(/\bNaN\b|\bundefined\b|\[object Object\]|\bInfinity\b|\bnull\b/);
      await distilled(page);
      if (SHOTS) {
        mkdirSync(SHOTS, { recursive: true });
        await page.screenshot({ path: join(SHOTS, `${test.info().project.name}-${r.name}.png`), fullPage: true });
      }
    });
  }
});

test('opens on the featured campaign, selected among the results', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: null }));
  await expect(page.locator('h1')).toHaveText('Synthetic host sizes');
  await expect(page.getByRole('note')).toContainText('Synthetic data');
  // The campaigns as labelled tabs, counted; the selected one controls the results below.
  await expect(page.locator('#campaigns-label')).toHaveText(/^Campaigns\s*3$/);
  const chooser = page.getByRole('tablist', { name: /^Campaigns/ });
  await expect(chooser.getByRole('tab')).toHaveCount(3);
  await expect(chooser.getByRole('tab', { selected: true })).toContainText('Synthetic host sizes');
  await expect(page.locator('#campaign-panel')).toHaveAttribute('aria-labelledby', 'campaign-tab-nested-sizes-synthetic');
  await expect(chooser.getByRole('tab', { name: /^Capacity baseline/ })).toContainText('8 microVMs · 0.50 per vCPU');
  // Each card carries the question its campaign answers.
  await expect(chooser.getByRole('tab', { name: /^Capacity baseline/ })).toContainText('How many 2 vCPU / 2 GiB Firecracker microVMs');
  // A campaign of several specs shows a bar per spec, of what it compares.
  await expect(chooser.getByRole('tab', { name: /^Synthetic hypervisors/ }).locator('.bv')).toHaveText(['10', '9', '8–9']);
  await expect(chooser.getByRole('tab', { name: /^Synthetic host sizes/ })).toContainText('Per vCPU');
  // The page leads with the answer, not the question again.
  await expect(page.locator('.top .lead')).toHaveText('m8i.4xlarge fits 0.50 microVMs per host vCPU, m8i.2xlarge 0.38–0.50. Host CPU ran out first in both.');
  await chooser.getByRole('tab', { name: /^Capacity baseline/ }).click();
  await expect(page).toHaveURL(/#\/results\/cap-baseline-1$/);
  await expect(page.locator('h1')).toHaveText('Capacity baseline');
  await expect(chooser.getByRole('tab', { selected: true })).toContainText('Capacity baseline');
  await expect(chooser.getByRole('tab', { selected: true })).toBeFocused();
});

test('shows what we tested: a column per spec, inputs that match written once, inputs that differ highlighted', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: S }));
  const m = page.locator('.matrix');
  await expect(m.locator('.head')).toHaveText([/^m8i\.4xlarge/, /^m8i\.2xlarge\s*Half the vCPUs of the same family/]);
  // A column named for its host doesn't name it again in the host row.
  await expect(m.locator('.cell.diff')).toHaveText([/^16 vCPU/, /^8 vCPU/, /^1\s*2\s*4\s*8\s*12\s*16$/, /^1\s*2\s*3\s*4\s*6\s*8$/]);
  await expect(m.locator('.cell.same')).toHaveText([/^Firecracker$/, /^2 vCPU · 2 GiB\s*$/]);
  await expect(page.getByRole('list', { name: 'Success criteria' }).getByRole('listitem')).toHaveCount(5);
  // Specs are named by what sets them apart, never by their slugs.
  await page.goto(at({ name: 'results', campaign: HV }));
  await expect(page.locator('.matrix .head strong')).toHaveText(['Firecracker PCI + RNG', 'Cloud Hypervisor', 'Firecracker MMIO']);
  // Each spec's why, whole.
  await expect(page.locator('.matrix .head .why').first()).toHaveText(/so the two differ only in the hypervisor\.$/);
  await expect(page.locator('main')).not.toContainText('firecracker-mmio');
});

test('shows how it performed: a bar per run beside each spec\'s figures, and every trial on one chart', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: S }));
  // The SLOs as one row of chips under the answer, not a block of their own; the standard ones, so none is marked.
  await expect(page.getByRole('list', { name: 'Success criteria' }).getByRole('listitem')).toHaveCount(5);
  await expect(page.locator('dl.strip .slos li.on')).toHaveCount(0);
  await expect(page.locator('.chooser .slo-tag')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Success criteria' })).toHaveCount(0);
  const specs = page.locator('.answer li.spec');
  await expect(specs).toHaveCount(2);
  await expect(specs.nth(0).locator('.figs')).toHaveText(/^Max density\s*8\s*Per vCPU\s*0\.50\s*\$ \/ 1k tasks\s*\$0\.081–0\.086\s*Ran out\s*Host CPU\s*at 12$/);
  await expect(specs.nth(1).locator('.figs')).toHaveText(/^Max density\s*3–4\s*Per vCPU\s*0\.38–0\.50\s*\$ \/ 1k tasks\s*\$0\.085–0\.090\s*Ran out\s*Host CPU\s*at 4$/);
  // Each run a bar that opens it, labelled with its replica; a tick at the spec's midpoint.
  await expect(specs.nth(1).getByRole('link')).toHaveText([/^replica 1\s*4\s*$/, /^replica 2\s*3\s*$/]);
  await expect(specs.nth(1).locator('.pass b')).toHaveText(['4', '3']);
  await expect(specs.nth(1).getByRole('link').nth(1)).toHaveAttribute('href', '#/results/nested-sizes-synthetic/runs/m8i-2xlarge-r2');
  await expect(specs.locator('.mid')).toHaveCount(2);

  const chart = page.locator('figure.chart > svg');
  // Nine counting trials in three of the runs, eleven in m8i-2xlarge-r2; nothing joins them.
  await expect(chart.locator('a[href*="/trials/"]')).toHaveCount(38);
  await expect(chart.locator('path:not(.m-fail), polyline')).toHaveCount(0);
  // Each mark sits at its trial's closest SLO; the dashed line is the limit.
  await expect(chart.locator('a[aria-label*="% of its limit"]')).toHaveCount(38);
  // A caption, not a control; the replica labels are links to the runs.
  await expect(page.locator('figure.chart .hint')).toHaveText(/^(Click|Tap) a mark to open its trial$/);
  await expect(chart.locator('a[href$="/runs/m8i-2xlarge-r2"]')).toHaveCount(1);

  // Latency by density, visible with no click: every counting trial in both panels, per host vCPU across sizes.
  const lat = page.locator('.lat');
  await expect(lat.locator('svg a.mark')).toHaveCount(76);
  await expect(lat.locator('text.axis-title')).toHaveText(['Density per host vCPU', 'Density per host vCPU']);
  // At the limit: each spec's first failing trial, chosen by labelled tabs.
  const limit = page.locator('.atlimit');
  await limit.scrollIntoViewIfNeeded();
  const tabs = limit.getByRole('tablist', { name: 'Spec' });
  await expect(tabs.getByRole('tab')).toHaveText([/^\s*m8i\.4xlarge\s*fails at 12$/, /^\s*m8i\.2xlarge\s*fails at 4$/]);
  await tabs.getByRole('tab', { name: /^m8i\.2xlarge/ }).click();
  await expect(limit.locator('.title')).toHaveText(/^Density 4\s*trial 3 · replica 2$/);
  await expect(limit.locator('.lanes svg a')).toHaveCount(4);
  // The rule that ran out, named; a switch sets the last density that passed beside the failure, in the same run.
  await expect(limit.locator('.result')).toContainText('Host CPU utilization ≥ 90%');
  await limit.getByRole('button', { name: /^Last pass/ }).click();
  await expect(limit.locator('.title')).toHaveText(/^Density 3\s*trial 1 · replica 2$/);
  await expect(limit.getByRole('button', { name: /^Last pass/ })).toHaveAttribute('aria-pressed', 'true');
  await limit.getByRole('button', { name: /^First failure/ }).click();
  await expect(limit.locator('.title')).toHaveText(/^Density 4\s*trial 3 · replica 2$/);

  await chart.locator('a[href$="/runs/m8i-2xlarge-r2/trials/d4-t3"]').click();
  await expect(page.locator('h1')).toHaveText('Trial 3 at density 4');
});

test('labels deliberately missing data: stopped early, not tested, no midpoint', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: HV }));
  const ch = page.locator('.answer li.spec').nth(1);
  await expect(ch.locator('.figs')).toHaveText(/^Max density\s*9\s*Per vCPU\s*0\.56\s*\$ \/ 1k tasks\s*\$0\.079–0\.082\s*Ran out\s*Host CPU\s*at 10 · 1 of 2 replicas$/);
  // A replica that stopped early says so at its bar.
  await expect(ch.getByRole('link').nth(1)).toHaveAccessibleName('Cloud Hypervisor, replica 2: passed up to 9, stopped early. Open the run');
  await expect(page.locator('.answer .key')).toContainText('stopped early');
  await expect(page.locator('figure.chart text.note')).toHaveText(['stopped early', 'stopped early']);
  await page.goto(at({ name: 'run', campaign: HV, run: 'firecracker-mmio-r2', density: 10 }));
  // Neighbouring densities not tested are one row.
  await expect(page.getByRole('row', { name: /^10–16 not tested$/ })).toBeVisible();
  await page.goto(at({ name: 'trial', campaign: CAP, run: 'baseline-r1', trial: 'warmup', microvm: null }));
  await expect(page.locator('p.verdict')).toHaveText('Warm-up, not judged.');
  // Not judged: the measured values, with no pass or fail marks.
  await expect(page.getByRole('list', { name: 'Success criteria' }).locator('svg.mark')).toHaveCount(0);
});

test('compares specs from different campaigns, in the URL', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: S }));
  await page.getByRole('link', { name: 'Compare specs' }).click();
  await expect(page.locator('h1')).toHaveText('Compare specs');
  await page.goto(at({ name: 'compare', specs: [`${S}/m8i-2xlarge`] }));
  await page.getByRole('checkbox', { name: 'm8i.4xlarge · Firecracker' }).check();
  await expect(page).toHaveURL(/specs=nested-sizes-synthetic\/m8i-2xlarge,cap-baseline-1\/baseline$/);
  // Grouped by campaign: each campaign's name once, over its specs.
  await expect(page.locator('.matrix .campaign')).toHaveText(['Synthetic host sizes', 'Capacity baseline']);
  await expect(page.locator('.matrix .head')).toHaveCount(2);
  await expect(page.locator('.matrix .cell.diff')).toHaveCount(4);
  // The picker says what its rows and chips are.
  await expect(page.locator('.picker legend')).toHaveText('Pick specs to compare');
  await expect(page.locator('.answer ol')).toHaveText(
    /^Synthetic host sizes\s*m8i\.2xlarge.*Max density\s*3–4\s*Per vCPU\s*0\.38–0\.50.*Capacity baseline\s*m8i\.4xlarge · Firecracker.*Max density\s*8\s*Per vCPU\s*0\.50/s,
  );
  // Both judged by the standard SLOs: no line saying they differ.
  await expect(page.locator('.slo-diff')).toHaveCount(0);
  await page.getByRole('checkbox', { name: 'm8i.2xlarge' }).uncheck();
  await page.getByRole('checkbox', { name: 'm8i.4xlarge · Firecracker' }).uncheck();
  await expect(page.getByText('Pick one or more specs.')).toBeVisible();
});

test('hands a campaign and a run to the builder through the URL', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: S }));
  await expect(page.getByRole('link', { name: 'New campaign from this' })).toHaveAttribute('href', '#/builder?from=campaign:nested-sizes-synthetic');
  await page.goto(at({ name: 'run', campaign: S, run: 'm8i-2xlarge-r2', density: null }));
  await expect(page.getByRole('link', { name: 'New campaign from this' })).toHaveAttribute('href', '#/builder?from=run:nested-sizes-synthetic/m8i-2xlarge-r2');
  await page.getByRole('link', { name: 'New campaign from this' }).click();
  await expect(page).toHaveURL(/#\/builder\?from=run:nested-sizes-synthetic\/m8i-2xlarge-r2$/);
  await expect(page.locator('h1')).toBeVisible();
});

test('drills from results to a run, a trial and a microVM', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: CAP }));
  await expect(page.locator('.top .lead')).toHaveText('8 microVMs met every SLO, 0.50 per host vCPU. Host CPU ran out at 12.');
  await expect(page.locator('.answer .figs')).toHaveText(/^Max density\s*8\s*Per vCPU\s*0\.50\s*\$ \/ 1k tasks\s*\$0\.068–0\.083\s*Ran out\s*Host CPU\s*at 12$/);
  // The run's bar opens it.
  await page.locator('.answer').getByRole('link', { name: /failed at 12\. Open the run$/ }).click();
  await expect(page).toHaveURL(/runs\/baseline-r1$/);
  await expect(page.locator('h1')).toHaveText('m8i.4xlarge · Firecracker');
  const rows = page.locator('table.densities tbody tr');
  await expect(rows).toHaveCount(6);
  await expect(rows.nth(3)).toContainText('passed');
  await expect(rows.nth(4)).toContainText('failed');
  await expect(rows.nth(4)).toContainText('home p50 1,063–1,563 ms');
  await expect(rows.nth(5)).toContainText('not tested');
  // What limited it: the rule that decided it, as bars at the last pass and the first failure; red only where it failed.
  await expect(page.locator('figure.bullet figcaption')).toHaveText([/^Host CPU pressure\s*limit ≥ 20%$/]);
  await expect(page.locator('figure.bullet .bv.fail')).toHaveCount(1);
  await page.getByRole('link', { name: 'trial 1 at density 12, failed', exact: true }).click();
  await expect(page.locator('h1')).toHaveText('Trial 1 at density 12');
  await expect(page.locator('p.verdict')).toHaveText(/^\s*Failed: home p50 1,279 ms > 1,000 ms$/);
  await expect(page.getByRole('list', { name: 'Success criteria' }).getByRole('listitem').nth(2)).toContainText('1,279 ms');
  await expect(page.getByRole('heading', { name: 'MicroVMs' })).toBeVisible();
  await page.getByRole('link', { name: 'microVM 3', exact: true }).click();
  await expect(page).toHaveURL(/trials\/d12-t1\?microvm=3$/);
  await expect(page.getByRole('heading', { name: /^MicroVM 3/ })).toBeVisible();
  await noHorizontalScroll(page);
  await page.goBack();
  await page.goBack();
  await expect(page).toHaveURL(/runs\/baseline-r1$/);
});

test('highlights one density of a run', async ({ page }) => {
  await page.goto(at({ name: 'run', campaign: CAP, run: 'baseline-r1', density: 8 }));
  await expect(page.locator('table.densities tr.current')).toHaveCount(1);
  await expect(page.locator('table.densities tr.current td').first()).toHaveText('8');
  await expect(page.locator('table.densities tr.current').getByRole('link', { name: /^trial \d at density 8, passed$/ })).toHaveCount(3);
});

test('says so when something is not in the dataset', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: 'no-such-campaign' }));
  await expect(page.getByRole('alert')).toContainText("isn't in this dataset");
  await expect(page.getByRole('alert').getByRole('link', { name: 'Back to results' })).toHaveAttribute('href', '#/results');
});
