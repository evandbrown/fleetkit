import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { findSynthetic } from '../../scripts/data-guard.mjs';
import { renderMethod, siteHref } from '../../scripts/method-doc.mjs';

const SITE = join(__dirname, '../..');

describe('synthetic data guard', () => {
  it('finds the synthetic fixtures', () => {
    const found: string[] = findSynthetic(join(SITE, 'tests/fixtures/data'));
    expect(found).toContain('index.json');
    expect(found).toContain('campaigns/nested-sizes-synthetic/campaign.json');
    expect(found.filter((f) => f !== 'index.json').every((f) => f.startsWith('campaigns/nested-sizes-synthetic/'))).toBe(true);
  });
  it('finds nothing synthetic in the published dataset', () => {
    expect(findSynthetic(join(SITE, 'public/data'))).toEqual([]);
  });
});

describe('method page', () => {
  it('points links at the site or the repository', () => {
    expect(siteHref('#glossary')).toBe('#/method/glossary');
    expect(siteHref('method.md#the-procedure')).toBe('#/method/the-procedure');
    expect(siteHref('capacity-experiment.md')).toBe('https://github.com/evandbrown/fleetkit/blob/main/docs/capacity-experiment.md');
    expect(siteHref('../harness/README.md')).toBe('https://github.com/evandbrown/fleetkit/blob/main/harness/README.md');
    expect(siteHref('https://example.com/x')).toBe('https://example.com/x');
  });
  it('gives headings ids and wraps tables so they scroll on a phone', () => {
    const { html, headings } = renderMethod('# Title\n\n## The glossary\n\n| a | b |\n|---|---|\n| 1 | 2 |\n');
    expect(headings.map((h: { id: string }) => h.id)).toEqual(['title', 'the-glossary']);
    expect(html).toContain('<h2 id="the-glossary">');
    expect(html).toContain('<div class="table-wrap"><table>');
  });
});
