import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { findSynthetic } from '../../scripts/data-guard.mjs';

const SITE = join(__dirname, '../..');

describe('synthetic data guard', () => {
  it('finds the synthetic fixtures', () => {
    const found: string[] = findSynthetic(join(SITE, 'tests/fixtures/data'));
    expect(found).toContain('index.json');
    expect(found).toContain('campaigns/nested-sizes-synthetic/campaign.json');
    expect(found).toContain('campaigns/nested-hv-synthetic/campaign.json');
    expect(found.filter((f) => f !== 'index.json').every((f) => /^campaigns\/[a-z0-9-]+-synthetic\//.test(f))).toBe(true);
  });
  it('finds nothing synthetic in the published dataset', () => {
    expect(findSynthetic(join(SITE, 'public/data'))).toEqual([]);
  });
});
