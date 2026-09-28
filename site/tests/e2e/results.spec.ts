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
  expect(headers.filter((h) => /,\s/.test(h) || h.trim().split(/\s+/).length > 4)).toEqual([]);
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
  { name: 'results-cap', path: at({ name: 'results', campaign: CAP }), h1: 'baseline', ready: 'At the limit' },
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
    ready: 'Browsers per host',
  },
  { name: 'run-density', path: at({ name: 'run', campaign: CAP, run: 'baseline-r1', density: 12 }), h1: 'm8i.4xlarge', ready: 'Browsers per host' },
  {
    name: 'trial',
    path: at({ name: 'trial', campaign: CAP, run: 'baseline-r1', trial: 'd12-t1', microvm: 3 }),
    h1: 'Trial 1 at 12 browsers',
    ready: 'Final screens',
  },
  {
    name: 'trial-illustration',
    path: at({ name: 'trial', campaign: CAP, run: 'baseline-r1', trial: 'illustration', microvm: null }),
    h1: /^Illustration at 1 browser$/,
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
  // The campaign picker: a labelled select-style button naming the selected campaign, with its question on one line
  // and its answer as a figure (D102): the lead spec's steady-state cost, then that spec and its browsers per host.
  // The results below are what it controls.
  await expect(page.locator('#campaign-label')).toHaveText('Choose a campaign');
  const picker = page.getByRole('button', { name: /^Campaign/ });
  await expect(picker).toContainText('Synthetic host sizes');
  await expect(picker.locator('.fig .line').first()).toHaveText('≈ $0.199 per 1,000 tasks');
  await expect(picker.locator('.fig .sub')).toHaveText('m8i.4xlarge · 8 browsers per host');
  await expect(picker.locator('.fig .line').first()).toHaveAttribute('title', 'Steady state over 2 replicas: $0.193–0.211 per 1,000 tasks; one burst $0.206–0.212');
  await expect(picker).toHaveAttribute('aria-expanded', 'false');
  await expect(picker).toHaveAttribute('aria-controls', 'campaign-panel');
  // Opening it lists every campaign as a card, newest first, the selected one marked.
  await picker.click();
  const list = page.getByRole('listbox', { name: 'Campaign' });
  await expect(list.getByRole('option')).toHaveCount(3);
  await expect(list.getByRole('option').first()).toContainText('Synthetic hypervisors');
  await expect(list.getByRole('option', { selected: true })).toContainText('Synthetic host sizes');
  // The real campaign's card: its snake_case title (D93), its question, one dot on the strip named for its one spec,
  // and its cost; one spec, so no "best".
  const cap = list.getByRole('option', { name: /^baseline/ });
  await expect(cap.locator('.q')).toHaveText('How many microVMs fit on one m8i.4xlarge?');
  await expect(cap.locator('.strip .dot')).toHaveCount(1);
  await expect(cap.locator('.strip .name')).toHaveText('m8i.4xlarge');
  await expect(cap.locator('.fig')).toHaveText(/^≈ \$0\.141\s*per 1,000 tasks\s*$/);
  // A campaign of several specs: a dot per spec on one shared browsers-per-vCPU scale (its top named with its unit),
  // the clear winner larger and named under its dot, and its cost at the right with "best".
  const hv = list.getByRole('option', { name: /^Synthetic hypervisors/ });
  await expect(hv.locator('.strip .dot')).toHaveCount(3);
  await expect(hv.locator('.strip .dot.best')).toHaveCount(1);
  await expect(hv.locator('.strip .name')).toHaveText('Firecracker PCI + RNG');
  await expect(hv.locator('.strip .unit')).toHaveText(/^[\d.]+ browsers \/ vCPU$/);
  await expect(hv.locator('.fig .v')).toHaveText('≈ $0.190');
  await expect(hv.locator('.fig .v')).toHaveClass(/win/);
  await expect(hv.locator('.fig .w')).toHaveText('best: Firecracker PCI + RNG');
  const sizes = list.getByRole('option', { name: /^Synthetic host sizes/ });
  await expect(sizes.locator('.strip .dot')).toHaveCount(2);
  await expect(sizes.locator('.fig .w')).toHaveText('best: m8i.4xlarge');
  // Nothing on a card but those four lines: no bars, no metric names, no SLO sentence.
  await expect(list.locator('.bars, .metric, .slo-tag, .common')).toHaveCount(0);
  // Escape closes it and puts focus back on the button.
  await page.keyboard.press('Escape');
  await expect(list).toHaveCount(0);
  await expect(picker).toBeFocused();
  await expect(picker).toHaveAttribute('aria-expanded', 'false');
  // The page's header: the question the campaign answers, then its answer.
  await expect(page.locator('.top .question')).toHaveText(/^Does density per host vCPU stay the same when the worker host doubles/);
  await expect(page.locator('.top .lead')).toHaveText('m8i.4xlarge fits 0.50 browsers per vCPU, m8i.2xlarge 0.38–0.50. Host CPU ran out first in both.');
  // Choosing by keyboard: ArrowDown opens on the selected campaign, ArrowDown again moves to the next, Enter opens it.
  await page.keyboard.press('ArrowDown');
  await expect(list).toBeVisible();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#\/results\/cap-baseline-1$/);
  await expect(page.locator('h1')).toHaveText('baseline');
  await expect(page.getByRole('button', { name: /^Campaign/ })).toContainText('baseline');
  await expect(page.getByRole('button', { name: /^Campaign/ }).locator('.fig .sub')).toHaveText('m8i.4xlarge · 8 browsers per host');
  await expect(page.getByRole('button', { name: /^Campaign/ })).toBeFocused();
  // And by a click on a card.
  await page.getByRole('button', { name: /^Campaign/ }).click();
  await page.getByRole('option', { name: /^Synthetic hypervisors/ }).click();
  await expect(page).toHaveURL(/#\/results\/nested-hv-synthetic$/);
  await expect(page.locator('h1')).toHaveText('Synthetic hypervisors');
  await expect(page.getByRole('button', { name: /^Campaign/ }).locator('.fig .line').first()).toHaveText('≈ $0.190 per 1,000 tasks');
  await expect(page.getByRole('button', { name: /^Campaign/ }).locator('.fig .sub')).toHaveText('Firecracker PCI + RNG · 10 browsers per host');
  await expect(page.getByRole('listbox')).toHaveCount(0);
});

test('shows what we tested: a column per spec, inputs that match written once, inputs that differ per column', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: S }));
  const t = page.locator('table.tested');
  // One table under "What we tested": a column per spec, headed by its name, its why under it.
  await expect(t.locator('thead th[scope="col"]')).toHaveText(['m8i.4xlarge', 'm8i.2xlarge']);
  await expect(t.locator('thead .why')).toHaveText([/^The host the baseline ran on/, /^Half the vCPUs of the same family/]);
  await expect(t.locator('tbody th')).toHaveText(['Host', 'Hypervisor', 'MicroVM', 'Browsers per host', 'Replicas', 'SLOs']);
  // The host and the densities differ: a cell per spec, in the specs' order; a run of densities as a range. A number
  // and its unit are joined by a no-break space (\s here), so "16 vCPU" never breaks in a narrow column.
  await expect(t.locator('td.diff')).toHaveText([
    /^m8i\.4xlarge\s*16\svCPU · 64\sGiB · nested$/,
    /^m8i\.2xlarge\s*8\svCPU · 32\sGiB · nested$/,
    '1, 2, 4, 8, 12, 16',
    '1–4, 6, 8',
  ]);
  // The rest match: written once, across every spec column; the SLOs one target per line (checked below).
  await expect(t.locator('td.same')).toHaveText(['Firecracker', '2 vCPU · 2 GiB', '2 hosts per spec', /^Browser ready\s*≤ 180 s\s*Tasks succeed/]);
  await expect(t.locator('td.same').first()).toHaveAttribute('colspan', '2');
  // Specs are named by what sets them apart, never by their slugs; the devices are a row only where they differ.
  await page.goto(at({ name: 'results', campaign: HV }));
  await expect(t.locator('thead th[scope="col"]')).toHaveText(['Firecracker PCI + RNG', 'Cloud Hypervisor', 'Firecracker MMIO']);
  // Each spec's why, whole.
  await expect(t.locator('thead .why').first()).toHaveText(/so the two differ only in the hypervisor\.$/);
  await expect(t.locator('tbody th')).toHaveText(['Host', 'Hypervisor', 'Devices', 'MicroVM', 'Browsers per host', 'Replicas', 'SLOs']);
  await expect(t.locator('td.diff')).toHaveText(['Firecracker', 'Cloud Hypervisor', 'Firecracker', 'PCI, RNG device', 'PCI, RNG device', 'MMIO, no RNG device']);
  await expect(page.locator('main')).not.toContainText('firecracker-mmio');
  // One spec: two columns, label and value, no header.
  await page.goto(at({ name: 'results', campaign: CAP }));
  await expect(page.getByRole('heading', { name: 'What we tested' })).toBeVisible();
  await expect(t.locator('thead')).toHaveCount(0);
  await expect(t.locator('td.diff')).toHaveCount(0);
  await expect(t.locator('tbody tr').first()).toHaveText(/^Host\s*m8i\.4xlarge\s*16\svCPU · 64\sGiB · nested$/);
});

test('shows how it performed: one row per spec with its replicas, cost and what ran out, and every trial folded under it', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: S }));
  // The SLOs as the last row of what we tested, not a block of their own. The fixtures were judged by the targets in
  // force before 28 September 2026, so the standard is named beside each of the three that differ.
  const slos = page.locator('table.tested tbody tr', { has: page.locator('th', { hasText: /^SLOs$/ }) });
  await expect(slos.locator('.slo-list li')).toHaveText([
    /^Browser ready\s+≤ 180 s$/,
    /^Tasks succeed\s+100%$/,
    /^Each step p50\s+≤ 1 s\s+standard 2 s$/,
    /^Each step p95\s+≤ 2 s\s+standard 3 s$/,
    /^Whole task p95\s+≤ 5 s\s+standard 10 s$/,
  ]);
  // Every fixture campaign was judged by the same targets, so no card carries the "SLOs" word and nothing says so
  // above the list: About states the SLOs.
  await expect(page.locator('.campaign-picker .common')).toHaveCount(0);
  await page.getByRole('button', { name: /^Campaign/ }).click();
  await expect(page.getByRole('listbox').locator('.card .slo')).toHaveCount(0);
  await expect(page.getByRole('listbox').getByRole('option')).toHaveCount(3);
  await expect(page.getByRole('listbox').locator('.slo-tag')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Success criteria' })).toHaveCount(0);
  // Four sections of equal standing, each with one sentence under its heading.
  await expect(page.locator('main h2')).toHaveText(['What we tested', 'How it performed', 'Latency by browsers per host', 'At the limit']);
  await expect(page.locator('main p.sub')).toHaveCount(4);
  // One row per spec. Each reads out its replicas' last pass and first failure (agreeing replicas stacked), its
  // midpoint, one cost number (the median of the replicas' steady-state middles, D102) and what ran out.
  const specs = page.locator('.answer li.spec');
  await expect(specs).toHaveCount(2);
  // In the strip, one lane per replica, each naming itself and its figures on hover; no count labels.
  await expect(specs.nth(0).locator('.lane')).toHaveCount(2);
  await expect(specs.nth(0).locator('.lane').nth(0)).toHaveAttribute('title', 'Replica 1: last pass at 8 browsers, first failure at 12 browsers');
  await expect(specs.nth(0).locator('.lane').nth(1)).toHaveAttribute('title', 'Replica 2: last pass at 8 browsers, first failure at 12 browsers');
  await expect(specs.nth(1).locator('.lane').nth(0)).toHaveAttribute('title', 'Replica 1: last pass at 4 browsers, first failure at 6 browsers');
  await expect(specs.nth(1).locator('.lane').nth(1)).toHaveAttribute('title', 'Replica 2: last pass at 3 browsers, first failure at 4 browsers');
  await expect(specs.locator('.lane .p')).toHaveCount(4);
  await expect(specs.locator('.lane .x')).toHaveCount(4);
  await expect(specs.locator('.lane .link')).toHaveCount(4);
  await expect(specs.locator('.strip small')).toHaveCount(0);
  await expect(specs.nth(0)).toHaveAttribute(
    'aria-label',
    'm8i.4xlarge: last pass at 8 in 2 replicas, first failure at 12 in 2 replicas; midpoint 0.63 browsers per vCPU; ≈ $0.199 per 1,000 tasks; ran out: Host CPU at 12; the clear winner on cost',
  );
  // Its replicas ran out at 6 and at 4, so the note names both ends. One replica's host was never full, so the cost
  // comes from the other.
  await expect(specs.nth(1)).toHaveAttribute(
    'aria-label',
    'm8i.2xlarge: last pass at 3 in 1 replica, 4 in 1 replica, first failure at 4 in 1 replica, 6 in 1 replica; midpoint 0.53 browsers per vCPU; ≈ $0.202 per 1,000 tasks; ran out: Host CPU at 4–6',
  );
  await expect(specs.locator('.cost')).toHaveText([/^≈\s*\$0\.199\s*$/, /^≈\s*\$0\.202\s*$/]);
  for (const c of await specs.locator('.cost').all()) await expect(c).toHaveAttribute('title', /^Steady state/);
  await expect(specs.nth(0).locator('.cost')).toHaveAttribute('title', 'Steady state over 2 replicas: $0.193–0.211 per 1,000 tasks; one burst $0.206–0.212');
  await expect(specs.locator('.out')).toHaveText([/^Host CPU\s*at 12$/, /^Host CPU\s*at 4–6$/]);
  // Every replica of m8i.4xlarge is cheaper than every replica of m8i.2xlarge: a clear winner, highlighted.
  await expect(page.locator('.answer li.spec.best .name')).toHaveText(['m8i.4xlarge']);
  await expect(page.locator('.answer .key')).not.toContainText('no clear winner');
  // A tick at each spec's midpoint; the axis once, above the first row, its title at the left of the tick row (the
  // head's copy shows on a phone only); a three-entry key.
  await expect(specs.locator('.mid')).toHaveCount(2);
  await expect(page.locator('.answer .head')).toHaveText(/^Spec\s*Browsers per vCPU\s*\$ \/ 1k tasks\s*Ran out\s*$/);
  // The strip's title sits on the tick row; under 760 px, where the name column is hidden, it shows in the head instead.
  const phone = (page.viewportSize()?.width ?? 1280) < 760;
  await expect(page.locator('.answer .head .phone-title')).toBeVisible({ visible: phone });
  await expect(page.locator('.answer .axis .axis-title')).toHaveText('Browsers per vCPU');
  await expect(page.locator('.answer .axis .t')).toHaveText(['0', '0.2', '0.4', '0.6', '0.8']);
  await expect(page.locator('.answer .key')).toHaveText(/^last pass\s*first failure\s*midpoint\s*$/);
  // No replica links in the rows: they live in Every trial and replica, closed by default.
  await expect(page.locator('.answer a')).toHaveCount(0);
  const trials = page.locator('details.trials');
  await expect(trials).not.toHaveAttribute('open', '');
  await expect(trials.locator('summary')).toHaveText(/^Every trial and replica · 4 runs$/);
  await trials.locator('summary').click();

  const chart = page.locator('figure.chart > svg');
  // Nine counting trials in three of the runs, eleven in m8i-2xlarge-r2; nothing joins them.
  await expect(chart.locator('a[href*="/trials/"]')).toHaveCount(38);
  await expect(chart.locator('path:not(.m-fail), polyline')).toHaveCount(0);
  // Each mark sits at its trial's closest SLO; the dashed line is the limit.
  await expect(chart.locator('a[aria-label*="% of its limit"]')).toHaveCount(38);
  // The replica labels are links to the runs. "Click a mark to open its trial" is said once on the page, by the
  // latency chart, not again here.
  await expect(chart.locator('a[href$="/runs/m8i-2xlarge-r2"]')).toHaveCount(1);
  await expect(page.locator('figure.chart .hint')).toHaveText('');
  await expect(page.getByText(/^(Click|Tap) a mark to open its trial$/)).toHaveCount(1);

  // Latency by browsers per host, visible with no click: every counting trial in both panels, per host vCPU across sizes,
  // and per spec a line through the median at each count, on a linear axis labelled every 0.5 and where a spec first
  // failed (0.75 for m8i.4xlarge; m8i.2xlarge's 0.5 is a round step already). The listed counts beyond 0.75 are shaded.
  const lat = page.locator('.lat');
  await expect(lat.locator('svg a.mark')).toHaveCount(76);
  await expect(lat.locator('.pn-step path.line')).toHaveCount(2);
  await expect(lat.locator('.pn-task path.line')).toHaveCount(2);
  await expect(lat.locator('.pn-step text.tick.x')).toHaveText(['0', '0.5', '0.75', '1', '1.5', '2']);
  await expect(lat.locator('.pn-step text.tick.x.fail')).toHaveText(['0.75']);
  await expect(lat.locator('.pn-step text.untested-label')).toHaveText('not tested');
  await expect(lat.locator('text.axis-title')).toHaveText(['Browsers per vCPU', 'Browsers per vCPU']);
  // The key names the specs; the SLO and the failures are labelled on the chart itself.
  await expect(lat.getByRole('list', { name: 'Key' })).toHaveText(/^\s*m8i\.4xlarge\s*m8i\.2xlarge\s*not tested\s*$/);
  // At the limit: each spec's first failing trial, chosen from a dropdown labelled Spec. Its button names the spec
  // shown and where it stopped passing; open, it lists every spec the same way, the shown one selected.
  const limit = page.locator('.atlimit');
  await limit.scrollIntoViewIfNeeded();
  const spec = limit.getByRole('button', { name: /^Spec/ });
  await expect(spec).toHaveText(/^\s*m8i\.4xlarge\s*fails at 12 browsers\s*$/);
  await expect(spec).toHaveAttribute('aria-expanded', 'false');
  await spec.click();
  const list = page.getByRole('listbox', { name: 'Spec' });
  await expect(list.getByRole('option')).toHaveText([/^\s*m8i\.4xlarge\s*fails at 12 browsers\s*$/, /^\s*m8i\.2xlarge\s*fails at 4 browsers\s*$/]);
  await expect(list.getByRole('option', { selected: true })).toContainText('m8i.4xlarge');
  await list.getByRole('option', { name: /^m8i\.2xlarge/ }).click();
  await expect(list).toHaveCount(0);
  await expect(spec).toHaveText(/^\s*m8i\.2xlarge\s*fails at 4 browsers\s*$/);
  await expect(limit.locator('.title')).toHaveText(/^4 browsers\s*trial 3 · replica 2$/);
  await expect(limit.locator('.lanes svg a')).toHaveCount(4);
  // The rule that ran out, named; a switch sets the last density that passed beside the failure, in the same run.
  await expect(limit.locator('.result')).toContainText('Host CPU utilization ≥ 90%');
  await limit.getByRole('button', { name: /^Last pass/ }).click();
  await expect(limit.locator('.title')).toHaveText(/^3 browsers\s*trial 1 · replica 2$/);
  await expect(limit.getByRole('button', { name: /^Last pass/ })).toHaveAttribute('aria-pressed', 'true');
  await limit.getByRole('button', { name: /^First failure/ }).click();
  await expect(limit.locator('.title')).toHaveText(/^4 browsers\s*trial 3 · replica 2$/);

  await chart.locator('a[href$="/runs/m8i-2xlarge-r2/trials/d4-t3"]').click();
  await expect(page.locator('h1')).toHaveText('Trial 3 at 4 browsers');
});

test('labels deliberately missing data: stopped early, not tested, no midpoint', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: HV }));
  const ch = page.locator('.answer li.spec').nth(1);
  // A replica that stopped early says so in the row, and the key gains the mark for it.
  await expect(ch).toHaveAttribute(
    'aria-label',
    'Cloud Hypervisor: last pass at 9 in 1 replica, stopped early after 9 in 1 replica, first failure at 10 in 1 replica; midpoint 9.5 browsers per host; ≈ $0.202 per 1,000 tasks; ran out: Host CPU at 10 · 1 of 2 replicas',
  );
  await expect(ch.locator('.out')).toHaveText(/^Host CPU\s*at 10 · 1 of 2 replicas$/);
  await expect(page.locator('.answer .key')).toContainText('stopped early');
  await page.locator('details.trials summary').click();
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
  // The picker groups specs under their campaign, each spec by its short name; two campaigns have a spec called
  // m8i.4xlarge, so the real campaign's is found inside its own group.
  const capGroup = page.locator('.picker .campaign', { has: page.locator('.cname', { hasText: /^baseline$/ }) });
  await capGroup.getByRole('checkbox', { name: 'm8i.4xlarge' }).check();
  await expect(page).toHaveURL(/specs=nested-sizes-synthetic\/m8i-2xlarge,cap-baseline-1\/baseline$/);
  // Grouped by campaign: each campaign's name once, over its specs; the host, densities and replicas differ.
  const t = page.locator('table.tested');
  await expect(t.locator('thead th[scope="colgroup"]')).toHaveText(['Synthetic host sizes', 'baseline']);
  await expect(t.locator('thead th[scope="col"]')).toHaveText(['m8i.2xlarge', 'm8i.4xlarge']);
  await expect(t.locator('td.diff')).toHaveCount(6);
  await expect(t.locator('tbody tr', { has: page.locator('th', { hasText: /^Replicas$/ }) }).locator('td')).toHaveText(['2 hosts per spec', '1 host per spec']);
  // The picker says what its rows and checkboxes are: a borderless table, a campaign's name beside its specs.
  await expect(page.locator('.picker legend')).toHaveText('Pick specs to compare');
  await expect(page.locator('.picker thead th')).toHaveText(['Campaign', 'Specs']);
  // How it performed groups the rows under their campaign, each with one cost number and what ran out.
  await expect(page.locator('.answer ol')).toHaveText(
    /^Synthetic host sizes\s*m8i\.2xlarge.*≈\s*\$0\.202\s*Host CPU\s*at 4–6\s*baseline\s*m8i\.4xlarge.*≈\s*\$0\.141\s*Host CPU\s*at 12$/s,
  );
  await expect(page.locator('.answer li.spec.best .name')).toHaveText(['m8i.4xlarge']);
  await expect(page.locator('details.trials summary')).toHaveText(/^Every trial and replica · \d+ runs$/);
  // Both judged by the same SLOs: no line saying they differ.
  await expect(page.locator('.slo-diff')).toHaveCount(0);
  await page.getByRole('checkbox', { name: 'm8i.2xlarge' }).uncheck();
  await capGroup.getByRole('checkbox', { name: 'm8i.4xlarge' }).uncheck();
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
  await expect(page.locator('.top .lead')).toHaveText('8 browsers met every SLO, 0.50 per vCPU. Host CPU ran out at 12.');
  // One spec, one replica: no stacking counts, and nothing to win against.
  await expect(page.locator('.answer li.spec')).toHaveAttribute(
    'aria-label',
    'm8i.4xlarge: last pass at 8, first failure at 12; midpoint 10 browsers per host; ≈ $0.141 per 1,000 tasks; ran out: Host CPU at 12',
  );
  await expect(page.locator('.answer li.spec.best')).toHaveCount(0);
  // One lane, unnumbered: there is only the one replica to name.
  await expect(page.locator('.answer .lane')).toHaveAttribute('title', 'last pass at 8 browsers, first failure at 12 browsers');
  // The run's replica label, under Every trial and replica, opens it.
  await page.locator('details.trials summary').click();
  await page.locator('figure.chart').getByRole('link', { name: 'Run baseline-r1' }).click();
  await expect(page).toHaveURL(/runs\/baseline-r1$/);
  await expect(page.locator('h1')).toHaveText('m8i.4xlarge');
  const rows = page.locator('table.densities tbody tr');
  await expect(rows).toHaveCount(6);
  await expect(rows.nth(3)).toContainText('passed');
  await expect(rows.nth(4)).toContainText('failed');
  await expect(rows.nth(4)).toContainText('home p50 1,063–1,563 ms');
  await expect(rows.nth(5)).toContainText('not tested');
  // What limited it: the rule that decided it, as bars at the last pass and the first failure; red only where it failed.
  await expect(page.locator('figure.bullet figcaption')).toHaveText([/^Host CPU pressure\s*limit ≥ 20%$/]);
  await expect(page.locator('figure.bullet .bv.fail')).toHaveCount(1);
  await page.getByRole('link', { name: 'trial 1 at 12 browsers, failed', exact: true }).click();
  await expect(page.locator('h1')).toHaveText('Trial 1 at 12 browsers');
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

test('links what is public on GitHub from campaign, run and trial pages, and nothing for synthetic data (D73)', async ({ page }) => {
  const GH = 'https://github.com/evandbrown/fleetkit';
  const COMMIT = '652f26d88c'; // as the dataset carries it: 10 characters, which GitHub resolves
  const DATA = `${GH}/blob/main/site/public/data/campaigns/${CAP}`;
  const source = page.getByRole('navigation', { name: 'Source' });
  const expectLinks = async (want: [string, string][]) => {
    await expect(source.getByRole('link')).toHaveText(want.map(([name]) => `${name}↗`));
    for (const [name, url] of want) {
      const a = source.getByRole('link', { name, exact: true });
      await expect(a).toHaveAttribute('href', url);
      await expect(a).toHaveAttribute('target', '_blank');
      await expect(a).toHaveAttribute('rel', 'noopener');
    }
  };
  const definition: [string, string] = ['Definition', `${GH}/blob/main/docs/capacity-experiment.md`];
  const harness: [string, string] = ['Harness @ 652f26d', `${GH}/tree/${COMMIT}`];

  await page.goto(at({ name: 'results', campaign: CAP }));
  await expectLinks([definition, ['Data', `${DATA}/campaign.json`], harness]);
  await page.goto(at({ name: 'run', campaign: CAP, run: 'baseline-r1', density: null }));
  await expectLinks([definition, ['Data', `${DATA}/runs/baseline-r1.json`], harness]);
  await page.goto(at({ name: 'trial', campaign: CAP, run: 'baseline-r1', trial: 'd12-t1', microvm: null }));
  await expect(page.getByRole('heading', { name: 'Final screens' })).toBeVisible();
  await expectLinks([definition, ['Data', `${DATA}/runs/baseline-r1/d12-t1.json`], harness]);
  await noHorizontalScroll(page);

  await page.goto(at({ name: 'results', campaign: S }));
  await expect(page.getByRole('heading', { name: 'What we tested' })).toBeVisible();
  await expect(source).toHaveCount(0);
  await page.goto(at({ name: 'run', campaign: S, run: 'm8i-2xlarge-r2', density: null }));
  await expect(page.getByRole('heading', { name: 'What limited it' })).toBeVisible();
  await expect(source).toHaveCount(0);
});

test('labels thumbnails as such where the dataset has no full-size screenshots (rule 10)', async ({ page }) => {
  // Trial 1 at 12 is the run's first failure, so its screenshots are published at full size; trial 1 at 4 isn't.
  await page.goto(at({ name: 'trial', campaign: CAP, run: 'baseline-r1', trial: 'd12-t1', microvm: 3 }));
  await expect(page.getByRole('heading', { name: 'Final screens' })).toBeVisible();
  await expect(page.getByText('Thumbnails only')).toHaveCount(0);
  await expect(page.locator('figcaption', { hasText: /^Final screen$/ })).toHaveCount(1);
  await page.goto(at({ name: 'trial', campaign: CAP, run: 'baseline-r1', trial: 'd4-t1', microvm: 2 }));
  await expect(page.getByRole('heading', { name: 'Final screens' })).toBeVisible();
  await expect(page.getByText('Thumbnails only', { exact: true })).toBeVisible();
  await expect(page.locator('figcaption', { hasText: /^Final screen, thumbnail only$/ })).toHaveCount(1);
  await distilled(page);
});

test('highlights one browser count of a run', async ({ page }) => {
  await page.goto(at({ name: 'run', campaign: CAP, run: 'baseline-r1', density: 8 }));
  await expect(page.locator('table.densities tr.current')).toHaveCount(1);
  await expect(page.locator('table.densities tr.current td').first()).toHaveText('8');
  await expect(page.locator('table.densities tr.current').getByRole('link', { name: /^trial \d at 8 browsers, passed$/ })).toHaveCount(3);
});

test('says so when something is not in the dataset', async ({ page }) => {
  await page.goto(at({ name: 'results', campaign: 'no-such-campaign' }));
  await expect(page.getByRole('alert')).toContainText("isn't in this dataset");
  await expect(page.getByRole('alert').getByRole('link', { name: 'Back to results' })).toHaveAttribute('href', '#/results');
});
