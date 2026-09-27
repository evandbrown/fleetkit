import { describe, expect, it } from 'vitest';
import { areaPath, linear, linePath, nearest, niceDomain } from '../../src/lib/scale';

describe('scales', () => {
  it('map a domain onto a range and back', () => {
    const x = linear([0, 16], [40, 200]);
    expect(x(0)).toBe(40);
    expect(x(8)).toBe(120);
    expect(x.invert(120)).toBe(8);
    expect(x.ticks(4)).toEqual([0, 5, 10, 15]);
  });
  it('put a zero-width domain in the middle', () => {
    expect(linear([3, 3], [0, 100])(3)).toBe(50);
  });
  it('round domains out from zero to a clean number', () => {
    expect(niceDomain([1279, 1563, 1150])).toEqual([0, 2000]);
    expect(niceDomain([0.375, 0.5])).toEqual([0, 0.5]);
    expect(niceDomain([])).toEqual([0, 1]);
    expect(niceDomain([5, 5], { zero: false })).toEqual([5, 6]);
  });
  it('find the nearest value', () => {
    expect(nearest([1, 2, 4, 8, 12], 9.9)).toBe(3);
    expect(nearest([], 1)).toBe(-1);
  });
  it('leave gaps in a line instead of bridging them', () => {
    expect(linePath([{ x: 0, y: 1 }, { x: 1, y: null }, { x: 2, y: 3 }, { x: 3, y: 4 }])).toBe('M0,1M2,3L3,4');
    expect(areaPath([0, 1], [0, 0], [2, 3])).toBe('M0,2L1,3L1,0L0,0Z');
  });
});
