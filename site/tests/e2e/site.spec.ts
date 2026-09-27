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
    // Missing screenshots are expected: the cap-baseline-1 fixture has no image files.
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

// The smoke test: every route loads against the fixtures, in light and dark, with nothing leaving the site,
// no script errors and no sideways scrolling.
const ROUTES: { name: string; path: string; h1: RegExp | string; ready?: string }[] = [
  { name: 'home', path: './', h1: 'Synthetic: two host sizes', ready: 'Latency against density' },
  { name: 'campaigns', path: './#/campaigns', h1: 'Campaigns' },
  { name: 'campaign-synthetic', path: './#/campaigns/nested-sizes-synthetic', h1: 'Synthetic: two host sizes', ready: 'Latency against density' },
  { name: 'campaign-cap-baseline-1', path: './#/campaigns/cap-baseline-1', h1: 'Capacity baseline', ready: 'Latency against density' },
  { name: 'run', path: './#/campaigns/nested-sizes-synthetic/runs/m8i-2xlarge-r2', h1: 'Run m8i-2xlarge-r2', ready: 'What limited it' },
  { name: 'run-density', path: './#/campaigns/cap-baseline-1/runs/baseline-r1?density=12', h1: 'Run baseline-r1', ready: 'Trials at density 12' },
  { name: 'trial', path: './#/campaigns/cap-baseline-1/runs/baseline-r1/trials/d12-t1?microvm=3', h1: 'Trial 1 at density 12', ready: 'Final screens' },
  { name: 'trial-illustration', path: './#/campaigns/cap-baseline-1/runs/baseline-r1/trials/illustration', h1: /^Illustration at density 1$/, ready: 'Filmstrip' },
  { name: 'builder', path: './#/builder', h1: 'Experiment builder' },
  { name: 'method', path: './#/method', h1: /./ },
  { name: 'about', path: './#/about', h1: 'About' },
  { name: 'not-found', path: './#/nowhere', h1: 'Not found' },
];

for (const scheme of ['light', 'dark'] as const) {
  test.describe(`every route, ${scheme}`, () => {
    test.use({ colorScheme: scheme });
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
        await shoot(page, `${r.name}-${scheme}`);
      });
    }
  });
}

test('opens on the most recent campaign, marked synthetic', async ({ page }) => {
  await page.goto('./');
  await expect(page).toHaveURL(/#\/campaigns\/nested-sizes-synthetic$/);
  await expect(page.getByRole('note')).toContainText('Synthetic data');
  await expect(page.getByRole('heading', { name: 'What differs between the specs' })).toBeVisible();
  await expect(page.locator('.card .row.changed')).toHaveCount(2);
});

test('drills from a campaign to a run, a trial and a microVM', async ({ page }) => {
  await page.goto('./#/campaigns/cap-baseline-1');
  await expect(page.getByText('shown as a campaign of one')).toBeVisible();
  await page.getByRole('link', { name: 'baseline-r1', exact: true }).first().click();
  await expect(page.getByText('Density 8 tested successfully; density 12 failed; 9–11 not tried; 16 not run.')).toBeVisible();
  await page.getByRole('link', { name: 'trial 1 at density 12', exact: true }).click();
  await expect(page.locator('h1')).toHaveText('Trial 1 at density 12');
  await expect(page.getByRole('heading', { name: 'MicroVMs' })).toBeVisible();
  await page.getByRole('link', { name: 'microVM 3', exact: true }).click();
  await expect(page).toHaveURL(/trials\/d12-t1\?microvm=3$/);
  await expect(page.getByRole('heading', { name: /^MicroVM 3/ })).toBeVisible();
  await noHorizontalScroll(page);
  await page.goBack();
  await page.goBack();
  await expect(page).toHaveURL(/runs\/baseline-r1$/);
});

test('filters a run to one density', async ({ page }) => {
  await page.goto('./#/campaigns/cap-baseline-1/runs/baseline-r1?density=8');
  await expect(page.getByRole('heading', { name: 'Trials at density 8' })).toBeVisible();
  await expect(page.getByRole('link', { name: /^trial \d at density 8$/ })).toHaveCount(3);
});

test('reads the capacity chart from the keyboard', async ({ page }) => {
  await page.goto('./#/campaigns/cap-baseline-1');
  const slider = page.getByRole('slider', { name: /^Whole task p95 against density/ });
  await slider.focus();
  await page.keyboard.press('End');
  await expect(slider).toHaveAttribute('aria-valuetext', /^Density 12\. .*failed\.$/);
});

test('defines terms on focus', async ({ page }) => {
  await page.goto('./#/campaigns/cap-baseline-1');
  const term = page.locator('.term', { hasText: 'tested successfully' }).first();
  await term.focus();
  await expect(page.getByRole('tooltip').filter({ hasText: 'never a maximum' }).first()).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('tooltip').filter({ hasText: 'never a maximum' }).first()).toBeHidden();
});

test('has the method, the builder placeholder and about', async ({ page }) => {
  await page.goto('./#/method');
  await expect(page.locator('h1')).toBeVisible();
  await page.getByRole('link', { name: 'Builder' }).click();
  await expect(page.getByText('Coming next.')).toBeVisible();
  await page.getByRole('link', { name: 'About' }).click();
  await expect(page.locator('h1')).toHaveText('About');
});

test('says so when something is not in the dataset', async ({ page }) => {
  await page.goto('./#/campaigns/no-such-campaign');
  await expect(page.getByRole('alert')).toContainText("isn't in this dataset");
});
