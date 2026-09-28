// The density axis the results charts share: one equal band per density tested (so densities 8, 9 and 10 get as
// much room as 1 and 4), then one band for every listed density above the highest tested, marked not tested. The
// latency chart and the every-trial grid use the same bands and margins, so their columns line up.
import { ticks } from 'd3-array';
import * as f from './format';

export interface Band {
  /** The density (or density per host vCPU) the band holds; null for the not-tested band. */
  x: number | null;
  label: string;
  untested: boolean;
}

const EPS = 1e-9;
const uniq = (xs: number[]) =>
  [...xs].sort((a, b) => a - b).filter((v, i, all) => i === 0 || Math.abs(v - all[i - 1]) > EPS);

/** A tick's text: the density, or density per host vCPU without trailing zeros (0.5, 0.56). */
export function tickText(v: number, perVcpu: boolean): string {
  return perVcpu ? (v === 0 ? '0' : f.num(v, 2).replace(/0$/, '').replace(/\.0$/, '')) : f.num(v);
}

export function makeBands(tested: number[], listed: number[], perVcpu: boolean): Band[] {
  const t = uniq(tested);
  const top = t.at(-1) ?? Infinity;
  const beyond = uniq(listed).filter((x) => x > top + EPS);
  const out: Band[] = t.map((x) => ({ x, label: tickText(x, perVcpu), untested: false }));
  if (beyond.length) {
    const a = tickText(beyond[0], perVcpu);
    const b = tickText(beyond[beyond.length - 1], perVcpu);
    out.push({ x: null, label: a === b ? a : `${a}–${b}`, untested: true });
  }
  return out;
}

/** A tick's width in px in the charts' 11 px tabular figures: 7 a digit or dash, 3.3 a point or comma. */
const tickWidth = (s: string) => [...s].reduce((w, c) => w + (c === '.' || c === ',' ? 3.3 : 7), 0);

/**
 * Which bands keep their tick at band width `bw`: all of them when the widest fits with room to spare; else every
 * k-th, counted down from the highest density tested so that one always keeps its tick. A phone comparing hosts
 * of different sizes has a dozen bands of 0.06-wide ticks in about 26 px each. The not-tested band keeps its own.
 */
export function tickShown(bands: Band[], bw: number): (i: number) => boolean {
  const tested = bands.filter((b) => !b.untested);
  const widest = Math.max(0, ...tested.map((b) => tickWidth(b.label)));
  const k = Math.max(1, Math.ceil((widest + 5) / bw));
  let last = bands.length - 1;
  while (last >= 0 && bands[last].untested) last--;
  return (i) => !!bands[i]?.untested || (i <= last && (last - i) % k === 0);
}

/** A tick on the latency chart's linear x axis: its value, its label (null for an unlabelled minor tick), and
 * whether it marks where a spec first failed. */
export interface AxisTick {
  v: number;
  label: string | null;
  failure: boolean;
}

/**
 * The latency chart's x ticks over a linear axis from 0 to `end`: per host vCPU, every 0.25 with a label every 0.5;
 * per host, d3's nice steps, all labelled. Then a labelled tick where each spec first failed (`failures`), kept only
 * when it sits at least 28 px (`toPx`) from every other label, so the labels never crowd.
 */
export function latencyTicks(end: number, toPx: (v: number) => number, perVcpu: boolean, failures: number[], count = 6): AxisTick[] {
  const out: AxisTick[] = [];
  if (perVcpu) {
    for (let k = 0; k * 0.25 <= end + EPS; k++) {
      const v = k * 0.25;
      out.push({ v, label: k % 2 === 0 ? tickText(v, true) : null, failure: false });
    }
  } else {
    for (const v of ticks(0, end, count)) out.push({ v, label: tickText(v, false), failure: false });
  }
  for (const f of uniq(failures)) {
    const room = out.filter((t) => t.label !== null).every((t) => Math.abs(toPx(t.v) - toPx(f)) >= 28);
    if (!room) continue;
    // A failure on a minor tick labels that tick; elsewhere it is a tick of its own.
    const on = out.find((t) => Math.abs(t.v - f) < EPS);
    if (on) Object.assign(on, { label: tickText(f, perVcpu), failure: true });
    else out.push({ v: f, label: tickText(f, perVcpu), failure: true });
  }
  return out.sort((a, b) => a.v - b.v);
}

export function bandIndex(bands: Band[], x: number): number {
  return bands.findIndex((b) => b.x !== null && Math.abs(b.x - x) < EPS);
}

/** Left margin: room for the y axis on the latency chart and the replica labels on the grid. */
export const chartLeft = (phone: boolean) => (phone ? 38 : 80);
export const CHART_RIGHT = 8;

export function bandLayout(n: number, width: number, phone: boolean) {
  const left = chartLeft(phone);
  const bw = Math.max(8, (width - left - CHART_RIGHT) / Math.max(1, n));
  return { left, right: width - CHART_RIGHT, bw, start: (i: number) => left + i * bw, center: (i: number) => left + (i + 0.5) * bw };
}
