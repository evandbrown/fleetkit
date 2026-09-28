import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

// Every request stays under /fleetkit/ on the server under test: no CDN, no fonts, no third parties.
function watchRequests(page: Page, baseURL: string): string[] {
  const base = new URL(baseURL);
  const outside: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.protocol.startsWith('http') && !(u.host === base.host && u.pathname.startsWith(base.pathname))) outside.push(r.url());
  });
  return outside;
}

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    // Missing screenshots are expected: the fixtures have no image files.
    if (m.type() === 'error' && !/Failed to load resource.*404/.test(m.text())) errors.push(m.text());
  });
  return errors;
}

async function noHorizontalScroll(page: Page) {
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sw).toBeLessThanOrEqual(cw);
}

const SHOTS = process.env.SCREENSHOTS;
async function shoot(page: Page, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: join(SHOTS, `${test.info().project.name}-${name}.png`), fullPage: true });
}

// The smoke test: every route loads against the fixtures (light is the only theme), with nothing leaving the site,
// no script errors and no sideways scrolling. The results pages have their own smoke test (results.spec.ts).
const ROUTES: { name: string; path: string; h1: RegExp | string; ready?: string }[] = [
  { name: 'home', path: './', h1: 'What is Fleetkit?', ready: 'Architecture' },
  { name: 'about-from-about', path: './#/about', h1: 'What is Fleetkit?', ready: 'Architecture' },
  { name: 'about-from-method', path: './#/method', h1: 'What is Fleetkit?', ready: 'Architecture' },
  { name: 'not-found', path: './#/nowhere', h1: 'Not found' },
];

for (const scheme of ['light'] as const) {
  test.describe(`every route, ${scheme}`, () => {
    for (const r of ROUTES) {
      test(r.name, async ({ page, baseURL }) => {
        const outside = watchRequests(page, baseURL!);
        const errors = watchErrors(page);
        await page.goto(r.path);
        await expect(page.locator('h1')).toHaveText(r.h1);
        if (r.ready) await expect(page.getByRole('heading', { name: r.ready, exact: true })).toBeVisible();
        await page.waitForLoadState('networkidle');
        await noHorizontalScroll(page);
        expect(outside).toEqual([]);
        expect(errors).toEqual([]);
        const text = await page.locator('main').innerText();
        expect(text).not.toMatch(/\bNaN\b|\bundefined\b|\[object Object\]|\bInfinity\b|\bnull\b/);
        await shoot(page, `${r.name}-${scheme}`);
      });
    }
  });
}

test('shows terms as plain words, with no popover', async ({ page }) => {
  await page.goto('./#/results/nested-sizes-synthetic');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await expect(page.locator('[aria-describedby]')).toHaveCount(0);
});

test('has three places in the navigation, About first, no theme switch and no footer', async ({ page }) => {
  await page.goto('./');
  const nav = page.getByRole('navigation', { name: 'Main' });
  await expect(nav.getByRole('link')).toHaveText(['About', 'Results', 'Builder']);
  await expect(nav.getByRole('link', { name: 'About' })).toHaveAttribute('aria-current', 'page');
  await expect(nav.getByRole('link', { name: 'About' })).toHaveAttribute('href', '#/');
  await expect(page.getByRole('link', { name: 'Fleetkit' })).toHaveAttribute('href', '#/');
  await expect(page.getByRole('button', { name: /theme/i })).toHaveCount(0);
  await expect(page.locator('body > #app > footer, footer.page')).toHaveCount(0);
  await page.goto('./#/results/cap-baseline-1/runs/baseline-r1');
  await expect(nav.getByRole('link', { name: 'Results' })).toHaveAttribute('aria-current', 'page');
});

test('opens older addresses at their new ones', async ({ page }) => {
  const cases: [string, RegExp][] = [
    ['./#/campaigns', /#\/results$/],
    ['./#/campaigns/cap-baseline-1', /#\/results\/cap-baseline-1$/],
    ['./#/campaigns/cap-baseline-1/runs/baseline-r1?density=8', /#\/results\/cap-baseline-1\/runs\/baseline-r1\?density=8$/],
    ['./#/campaigns/cap-baseline-1/runs/baseline-r1/trials/d12-t1?microvm=3', /#\/results\/cap-baseline-1\/runs\/baseline-r1\/trials\/d12-t1\?microvm=3$/],
    ['./#/compare?specs=cap-baseline-1/baseline', /#\/results\/compare\?specs=cap-baseline-1\/baseline$/],
    ['./#/method/glossary', /#\/$/],
    ['./#/about', /#\/$/],
  ];
  for (const [from, to] of cases) {
    await page.goto(from);
    await expect(page).toHaveURL(to);
    await expect(page.locator('h1')).toBeVisible();
  }
});

test('opens on About: what Fleetkit is, how it works, and the way into the results', async ({ page }) => {
  await page.goto('./');
  await expect(page.locator('h1')).toHaveText('What is Fleetkit?');
  // The intro says what drives the browsers: a test harness, not an agent (D92).
  await expect(page.locator('.hero .lead')).toContainText('benchmark framework for the browser fleet');
  await expect(page.locator('.hero .lead')).toContainText('driven by a test harness');
  await expect(page.locator('.hero')).not.toContainText(/AI agent/);
  await expect(page.getByRole('list', { name: 'How Fleetkit works' }).getByRole('listitem')).toHaveCount(4);
  const featured = page.getByRole('complementary', { name: 'Featured result' });
  await expect(featured).toContainText('Synthetic host sizes');
  // The figure names the host it ran on.
  await expect(featured).toContainText(/\d+\s*microVMs? on one m8i\.4xlarge/);
  await expect(page.getByRole('link', { name: /Define your own in the Builder/ })).toHaveAttribute('href', '#/builder');
  await expect(featured).toContainText('per host vCPU');
  // No question on the card (the answer carries its context), and a check before "SLOs" that reads as "every SLO met".
  await expect(featured.locator('.q')).toHaveCount(0);
  await expect(featured.locator('.slos')).toHaveText(/^\s*every SLO met\s*SLOs\s*$/);
  await expect(page.getByRole('link', { name: 'See the results' })).toHaveCount(1);
  await page.getByRole('link', { name: 'See the results' }).click();
  await expect(page).toHaveURL(/#\/results$/);
  await expect(page.locator('h1')).toHaveText('Synthetic host sizes');
  await page.getByRole('link', { name: 'Fleetkit' }).click();
  await expect(page.locator('h1')).toHaveText('What is Fleetkit?');
  await noHorizontalScroll(page);
});

test('explains the setup on About, with the featured spec, the standard SLOs and the five steps', async ({ page }) => {
  await page.goto('./');
  await expect(page.getByRole('img', { name: /^AWS us-east-1/ })).toBeVisible();
  // The method's standard SLOs (the input schema's defaults), not the featured campaign's.
  await expect(page.getByRole('list', { name: 'Success criteria' }).getByRole('listitem')).toHaveText([
    /^≤ 180 s\s*Browser ready$/,
    /^100%\s*Tasks succeed$/,
    /^≤ 1 s\s*Each step p50$/,
    /^≤ 2 s\s*Each step p95$/,
    /^≤ 5 s\s*Whole task p95$/,
  ]);
  // The featured campaign uses them, so its result carries no tag.
  await expect(page.getByRole('complementary', { name: 'Featured result' }).locator('.slo-tag')).toHaveCount(0);
  await expect(page.locator('ol.film > li')).toHaveCount(5);
  await expect(page.locator('ol.film')).toContainText('Verify cart');
  // The spec is the same table Results shows, for the featured campaign's first spec, with the support host last.
  await expect(page.locator('.how table.tested tbody th')).toHaveText(['Host', 'Hypervisor', 'MicroVM', 'Densities', 'Replicas', 'SLOs', 'Support host']);
  await expect(page.locator('.how table.tested thead')).toHaveCount(0);
  await expect(page.locator('ol.parts > li')).toHaveCount(5);
  await expect(page.locator('dl.measures dt')).toHaveText(['Latency', 'Time to ready', 'Host pressure', 'Cost', 'Max density', 'Midpoint']);
  // Nothing after What we measure: the one call to action is at the top, and there are no footer links.
  await expect(page.locator('section').last().getByRole('link')).toHaveCount(0);
  await expect(page.locator('main')).not.toContainText(/Words we don't use|glossary/i);
  await noHorizontalScroll(page);
});
