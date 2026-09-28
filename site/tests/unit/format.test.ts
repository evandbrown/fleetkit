import { describe, expect, it } from 'vitest';
import { count, day, range, ratio, side, usdRange, vs } from '../../src/lib/format';

describe('format', () => {
  it('never rounds a value across its threshold', () => {
    expect(vs(89.98, 90)).toBe('89.98');
    expect(vs(90.004, 90)).toBe('90.004');
    expect(vs(1279.246, 1000)).toBe('1,279');
    expect(vs(999.6, 1000)).toBe('999.6');
    expect(vs(90, 90)).toBe('90');
  });
  it('keeps one precision without crossing a threshold', () => {
    expect(side(89.98, 90)).toBe('89.9');
    expect(side(20.04, 20)).toBe('20.1');
    expect(side(34.52, 20)).toBe('34.5');
    expect(side(0.0999, 0.1, 2)).toBe('0.09');
    expect(ratio(0.625)).toBe('0.63');
    expect(ratio(0.5)).toBe('0.50');
  });
  it('writes ranges with an en dash', () => {
    expect(range([1063, 1563])).toBe('1,063–1,563');
    expect(range([8, 8])).toBe('8');
    expect(usdRange([0.0681, 0.0827])).toBe('$0.068–0.083');
    expect(usdRange([0.2107, 0.214])).toBe('$0.211–0.214');
    expect(range([0.5154, 0.5239], (v) => `${Math.round(v * 100)}%`)).toBe('52%');
  });
  it('writes a day plainly', () => {
    expect(day('2026-09-27T16:49Z')).toBe('27 Sep 2026');
  });
  it('counts in the singular for one', () => {
    expect(count(1, 'microVM')).toBe('1 microVM');
    expect(count(12, 'microVM')).toBe('12 microVMs');
  });
});
