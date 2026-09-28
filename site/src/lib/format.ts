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

/** A ratio such as density per host vCPU, always to two decimals: 0.50, 0.38. */
export function ratio(v: number): string {
  return num(v, 2);
}

/**
 * `value` to `digits` decimals, rounded toward its own side of `threshold` when plain rounding would reach or cross
 * it: 89.98 against 90 is "89.9", 20.04 against 20 is "20.1". One precision, never the wrong side of a limit.
 */
export function side(value: number, threshold: number, digits = 1): string {
  const k = 10 ** digits;
  let r = Math.round(value * k) / k;
  if (value < threshold && r >= threshold) r = Math.floor(value * k) / k;
  if (value > threshold && r <= threshold) r = Math.ceil(value * k) / k;
  return num(r, digits);
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

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "27 Sep 2026" from "2026-09-27T16:49Z". */
export function day(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split('-');
  return `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`;
}

export function duration(s: number): string {
  if (s < 90) return `${num(s)} s`;
  const m = Math.round(s / 60);
  return m < 90 ? `${m} min` : `${num(s / 3600, 1)} h`;
}
