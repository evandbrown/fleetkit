// Number formatting. Values are never rounded across a threshold (vs), and ranges use an en dash.
import type { Range } from './types';

const nf = (digits: number) =>
  new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function num(v: number, digits = 0): string {
  return nf(digits).format(v);
}

/** Formats `value` with the fewest decimals (at least `digits`) that keep it on the same side of `threshold`. */
export function vs(value: number, threshold: number, digits = 0): string {
  const side = Math.sign(value - threshold);
  for (let d = digits; d <= 6; d++) {
    const r = Number(value.toFixed(d));
    if (Math.sign(r - threshold) === side) return num(r, d);
  }
  return String(value);
}

/** "1 microVM", "12 microVMs". */
export function count(n: number, one: string, many = `${one}s`): string {
  return `${num(n)} ${n === 1 ? one : many}`;
}

export function ms(v: number): string {
  return `${num(v)} ms`;
}

export function seconds(msValue: number, digits = 2): string {
  return `${num(msValue / 1000, digits)} s`;
}

export function pct(v: number, digits = 1): string {
  return `${num(v, digits)}%`;
}

/** USD with enough decimals for small per-task costs: $0.075, $0.211, $1.20. */
export function usd(v: number): string {
  return `$${num(v, v >= 1 ? 2 : 3)}`;
}

/** A ratio such as density per host vCPU: 0.50, 0.375. */
export function ratio(v: number): string {
  return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 3 }).format(v);
}

/** "1,063–1,563" or a single value when min = max. `f` formats each end. */
export function range(r: Range, f: (v: number) => string = (v) => num(v)): string {
  const [a, b] = [f(r[0]), f(r[1])];
  return a === b ? a : `${a}–${b}`;
}

/** A range of USD values: "$0.074–0.077". */
export function usdRange(r: Range): string {
  const [a, b] = [usd(r[0]), usd(r[1])];
  return a === b ? a : `${a}–${b.slice(1)}`;
}

/** [min, max] of a replica list, ignoring nulls. */
export function spread(values: (number | null)[]): Range | null {
  const v = values.filter((x): x is number => x !== null);
  return v.length ? [Math.min(...v), Math.max(...v)] : null;
}

/** "2026-09-27 16:49 UTC" from "2026-09-27T16:49Z". */
export function when(iso: string): string {
  return iso.replace('T', ' ').replace('Z', ' UTC');
}

export function duration(s: number): string {
  if (s < 90) return `${num(s)} s`;
  const m = Math.round(s / 60);
  return m < 90 ? `${m} min` : `${num(s / 3600, 1)} h`;
}
