import { describe, expect, it } from 'vitest';
import { area, href, parse, type Route } from '../../src/lib/router';

const ROUTES: Route[] = [
  { name: 'home' },
  { name: 'campaigns' },
  { name: 'campaign', campaign: 'nested-sizes-1' },
  { name: 'run', campaign: 'nested-sizes-1', run: 'm8i-2xlarge-r2', density: null },
  { name: 'run', campaign: 'nested-sizes-1', run: 'm8i-2xlarge-r2', density: 8 },
  { name: 'trial', campaign: 'cap-baseline-1', run: 'baseline-r1', trial: 'd8-t2', microvm: null },
  { name: 'trial', campaign: 'cap-baseline-1', run: 'baseline-r1', trial: 'warmup', microvm: 3 },
  { name: 'builder' },
  { name: 'method', anchor: null },
  { name: 'method', anchor: 'glossary' },
  { name: 'about' },
];

describe('router', () => {
  it('round-trips every route through its href', () => {
    for (const r of ROUTES) expect(parse(href(r))).toEqual(r);
  });

  it('treats an empty hash as home', () => {
    expect(parse('')).toEqual({ name: 'home' });
    expect(parse('#')).toEqual({ name: 'home' });
  });

  it('ignores bad query values', () => {
    expect(parse('#/campaigns/c/runs/r?density=0')).toMatchObject({ density: null });
    expect(parse('#/campaigns/c/runs/r?density=x')).toMatchObject({ density: null });
  });

  it('refuses ids outside [a-z0-9-] instead of fetching them', () => {
    expect(parse('#/campaigns/../secret').name).toBe('not_found');
    expect(parse('#/campaigns/Upper').name).toBe('not_found');
  });

  it('puts every data route under Campaigns', () => {
    expect(area(parse('#/campaigns/c/runs/r/trials/d1-t1'))).toBe('campaigns');
    expect(area(parse('#/method/glossary'))).toBe('method');
  });
});
