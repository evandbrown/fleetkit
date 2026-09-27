// Linear scales and axis ticks for the charts. Small on purpose: the charts need nothing else, and d3-scale
// would pull colour interpolation into the bundle.
import { nice, ticks } from 'd3-array';

export interface Linear {
  (v: number): number;
  domain: [number, number];
  range: [number, number];
  ticks: (count?: number) => number[];
  invert: (px: number) => number;
}

/** Maps [d0, d1] onto [r0, r1]. A zero-width domain maps everything to the middle of the range. */
export function linear(domain: [number, number], range: [number, number]): Linear {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  const f = ((v: number) => (span === 0 ? (r0 + r1) / 2 : r0 + ((v - d0) / span) * (r1 - r0))) as Linear;
  f.domain = domain;
  f.range = range;
  f.ticks = (count = 5) => ticks(d0, d1, count);
  f.invert = (px: number) => (r1 === r0 ? d0 : d0 + ((px - r0) / (r1 - r0)) * span);
  return f;
}

/** A domain from 0 (or the data's minimum, if `zero` is false) to a round number at or above every value. */
export function niceDomain(values: number[], opts: { zero?: boolean; count?: number; min?: number } = {}): [number, number] {
  const { zero = true, count = 5 } = opts;
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return [0, 1];
  let lo = zero ? Math.min(0, ...finite) : Math.min(...finite);
  let hi = Math.max(...finite);
  if (opts.min !== undefined) lo = Math.min(lo, opts.min);
  if (hi === lo) hi = lo + 1;
  const [a, b] = nice(lo, hi, count);
  return [a, b];
}

/** The index of the value in `xs` nearest to `x`. `xs` needn't be sorted. */
export function nearest(xs: number[], x: number): number {
  let best = -1;
  let dist = Infinity;
  xs.forEach((v, i) => {
    const d = Math.abs(v - x);
    if (d < dist) {
      dist = d;
      best = i;
    }
  });
  return best;
}

/** An SVG path through the points, skipping gaps (null y) rather than bridging them. */
export function linePath(points: { x: number; y: number | null }[]): string {
  let d = '';
  let pen = false;
  for (const p of points) {
    if (p.y === null || !Number.isFinite(p.y)) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${round(p.x)},${round(p.y)}`;
    pen = true;
  }
  return d;
}

/** A closed area between two lines sharing x positions (for stacked areas). */
export function areaPath(xs: number[], lower: number[], upper: number[]): string {
  if (!xs.length) return '';
  let d = `M${round(xs[0])},${round(upper[0])}`;
  for (let i = 1; i < xs.length; i++) d += `L${round(xs[i])},${round(upper[i])}`;
  for (let i = xs.length - 1; i >= 0; i--) d += `L${round(xs[i])},${round(lower[i])}`;
  return d + 'Z';
}

const round = (v: number) => Math.round(v * 10) / 10;
