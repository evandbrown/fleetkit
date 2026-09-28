import { describe, expect, it } from 'vitest';
import { area, href, parse, resolve, type Route } from '../../src/lib/router';

const ROUTES: Route[] = [
  { name: 'results', campaign: null },
  { name: 'results', campaign: 'nested-sizes-1' },
  { name: 'run', campaign: 'nested-sizes-1', run: 'm8i-2xlarge-r2', density: null },
  { name: 'run', campaign: 'nested-sizes-1', run: 'm8i-2xlarge-r2', density: 8 },
  { name: 'trial', campaign: 'cap-baseline-1', run: 'baseline-r1', trial: 'd8-t2', microvm: null },
  { name: 'trial', campaign: 'cap-baseline-1', run: 'baseline-r1', trial: 'warmup', microvm: 3 },
  { name: 'compare', specs: null },
  { name: 'compare', specs: [] },
  { name: 'compare', specs: ['nested-sizes-1/m8i-2xlarge', 'cap-baseline-1/baseline'] },
  { name: 'builder', from: null },
  { name: 'builder', from: 'campaign:nested-sizes-1' },
  { name: 'builder', from: 'run:nested-sizes-1/m8i-2xlarge-r2' },
  { name: 'about' },
];

describe('router', () => {
  it('round-trips every route through its href, with no redirect', () => {
    for (const r of ROUTES) expect(resolve(href(r))).toEqual({ route: r, redirect: null });
  });

  it('opens Results on the featured campaign from an empty hash, #/ and #/results', () => {
    for (const h of ['', '#', '#/', '#/results', '#/results/']) {
      expect(resolve(h)).toEqual({ route: { name: 'results', campaign: null }, redirect: null });
    }
  });

  it('writes the new addresses', () => {
    expect(href({ name: 'results', campaign: 'cap-baseline-1' })).toBe('#/results/cap-baseline-1');
    expect(href({ name: 'run', campaign: 'c', run: 'r', density: 8 })).toBe('#/results/c/runs/r?density=8');
    expect(href({ name: 'trial', campaign: 'c', run: 'r', trial: 'd8-t2', microvm: 3 })).toBe(
      '#/results/c/runs/r/trials/d8-t2?microvm=3',
    );
    expect(href({ name: 'compare', specs: ['a/b', 'c/d'] })).toBe('#/results/compare?specs=a/b,c/d');
    expect(href({ name: 'about' })).toBe('#/about');
  });

  it('reads #/results/compare as the compare page, never as a campaign', () => {
    expect(parse('#/results/compare')).toEqual({ name: 'compare', specs: null });
    expect(parse('#/results/compare?specs=a/b')).toEqual({ name: 'compare', specs: ['a/b'] });
  });

  it('redirects every older address to its new one', () => {
    const cases: [string, string][] = [
      ['#/campaigns', '#/results'],
      ['#/campaigns/', '#/results'],
      ['#/campaigns/cap-baseline-1', '#/results/cap-baseline-1'],
      ['#/campaigns/c/runs/r', '#/results/c/runs/r'],
      ['#/campaigns/c/runs/r?density=12', '#/results/c/runs/r?density=12'],
      ['#/campaigns/c/runs/r/trials/d12-t1', '#/results/c/runs/r/trials/d12-t1'],
      ['#/campaigns/c/runs/r/trials/d12-t1?microvm=3', '#/results/c/runs/r/trials/d12-t1?microvm=3'],
      ['#/compare', '#/results/compare'],
      ['#/compare?specs=a/b,c/d', '#/results/compare?specs=a/b,c/d'],
      ['#/compare?specs=', '#/results/compare?specs='],
      ['#/method', '#/about'],
      ['#/method/glossary', '#/about'],
    ];
    for (const [from, to] of cases) {
      const { route, redirect } = resolve(from);
      expect(redirect, from).toBe(to);
      // The page opens at once, before the address is rewritten.
      expect(route, from).toEqual(parse(to));
    }
  });

  it('ignores bad query values', () => {
    expect(parse('#/results/c/runs/r?density=0')).toMatchObject({ density: null });
    expect(parse('#/results/c/runs/r?density=x')).toMatchObject({ density: null });
    expect(parse('#/campaigns/c/runs/r?density=x')).toMatchObject({ density: null });
  });

  it('refuses ids outside [a-z0-9-] instead of fetching them', () => {
    for (const h of ['#/results/../secret', '#/results/Upper', '#/campaigns/../secret', '#/campaigns/Upper']) {
      expect(resolve(h)).toEqual({ route: { name: 'not_found', path: h.slice(1) }, redirect: null });
    }
  });

  it('keeps only well-formed spec references and builder sources', () => {
    expect(parse('#/results/compare?specs=a/b,../x,a/b,Up/per,c/d')).toEqual({ name: 'compare', specs: ['a/b', 'c/d'] });
    expect(parse('#/builder?from=campaign:../x')).toEqual({ name: 'builder', from: null });
    expect(parse('#/builder?from=spec:x')).toEqual({ name: 'builder', from: null });
  });

  it('puts every data page under Results in the navigation', () => {
    expect(area(parse('#/'))).toBe('results');
    expect(area(parse('#/results/c/runs/r/trials/d1-t1'))).toBe('results');
    expect(area(parse('#/results/compare?specs=a/b'))).toBe('results');
    expect(area(parse('#/builder'))).toBe('builder');
    expect(area(parse('#/method/glossary'))).toBe('about');
    expect(area(parse('#/nowhere'))).toBeNull();
  });
});
