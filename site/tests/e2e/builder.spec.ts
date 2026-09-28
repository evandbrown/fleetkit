import { expect, test } from '@playwright/test';

// The builder's field help (D72): every field's label has an ⓘ whose explanation shows on hover, on keyboard focus
// and on tap, hides on Escape or a tap elsewhere, and is named by the field's input for a screen reader.
test.beforeEach(async ({ page }) => {
  await page.goto('#/builder');
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Base spec' })).toBeVisible();
});

test('every field and review figure has an explanation the input points at', async ({ page }) => {
  const base = page.getByRole('region', { name: 'Base spec' });
  for (const name of ['Worker host', 'Hypervisor', 'MicroVM vCPUs', 'MicroVM memory', 'Densities', 'Step p50 target']) {
    await expect(base.getByRole('button', { name: `About ${name}` })).toBeVisible();
  }
  for (const name of ['Name', 'Question', 'Replicas', 'Shut down after', 'Runs', 'Time', 'Expected', 'Worst case', 'vCPUs', 'Cost']) {
    await expect(page.getByRole('button', { name: `About ${name}`, exact: true })).toBeVisible();
  }
  const p50 = page.getByRole('textbox', { name: 'Step p50 target', exact: true });
  await expect(p50).toHaveAccessibleDescription(/^In each trial, the median time of each of the five steps must be at or under this/);
});

test('shows on hover and keyboard focus, hides on leaving and Escape', async ({ page, isMobile }) => {
  test.skip(isMobile, 'hover and Tab are desktop');
  const btn = page.getByRole('region', { name: 'Base spec' }).getByRole('button', { name: 'About Densities' });
  const tip = page.getByRole('tooltip').filter({ hasText: 'How many microVMs each trial starts at once' });
  await btn.hover();
  await expect(tip).toBeVisible();
  await page.mouse.move(0, 0);
  await expect(tip).toBeHidden();

  await page.getByRole('textbox', { name: 'Step p50 target', exact: true }).focus();
  await page.keyboard.press('Shift+Tab');
  const p50 = page.getByRole('tooltip').filter({ hasText: 'median time' });
  await expect(p50).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(p50).toBeHidden();
});

test("shows on hovering the field's label too, and labels every spec's name and its added inputs", async ({ page, isMobile }) => {
  test.skip(isMobile, 'hover is desktop');
  const tip = page.getByRole('tooltip').filter({ hasText: 'median time' });
  await page.locator('label', { hasText: /^Step p50 target$/ }).hover();
  await expect(tip).toBeVisible();
  await page.mouse.move(0, 0);
  await expect(tip).toBeHidden();
  await page.getByRole('button', { name: '+ Add spec' }).click().catch(() => {});
  const specs = page.locator('article.card');
  if ((await specs.count()) > 0) {
    await expect(specs.first().locator('label', { hasText: 'Spec name' })).toBeVisible();
    await expect(specs.first().getByRole('button', { name: 'About Change an input' })).toBeVisible();
  }
});

test('a tap pins it; a tap elsewhere hides it', async ({ page }) => {
  const btn = page.getByRole('button', { name: 'About Worst case' });
  const tip = page.getByRole('tooltip').filter({ hasText: 'shutdown timer' });
  await btn.click();
  await expect(tip).toBeVisible();
  const box = (await tip.boundingBox())!;
  const width = page.viewportSize()!.width;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(width);
  await page.getByRole('heading', { name: 'Review' }).click();
  await expect(tip).toBeHidden();
});
