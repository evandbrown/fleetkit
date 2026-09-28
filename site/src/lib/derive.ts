// The rules in DATA.md ("Rules"), as functions. The dataset builder applies the same rules (site/build/rules.py);
// contract.ts uses these to check what it shipped, and the pages use them to compare specs across campaigns.
import {
  COUNTING_ROLES,
  VERDICTS,
  type DensityBrief,
  type Range,
  type TrialRole,
  type TrialSummary,
  type Verdict,
} from './types';

export const TRIAL_ID = /^(?:d([1-9]\d*)-t([1-9]\d*)|(warmup|illustration)(?:-([2-9]|[1-9]\d+))?)$/;

/** "d8-t2" for trial 2 at density 8. */
export function trialId(density: number, number: number): string {
  return `d${density}-t${number}`;
}

export type ParsedTrialId =
  | { counts: true; density: number; number: number }
  | { counts: false; role: 'warmup' | 'illustration'; nth: number };

export function parseTrialId(id: string): ParsedTrialId | null {
  const m = TRIAL_ID.exec(id);
  if (!m) return null;
  if (m[1]) return { counts: true, density: Number(m[1]), number: Number(m[2]) };
  return { counts: false, role: m[3] as 'warmup' | 'illustration', nth: m[4] ? Number(m[4]) : 1 };
}

export function countsTowardResult(role: TrialRole): boolean {
  return COUNTING_ROLES.includes(role);
}

/** Rule 1: a trial passes if it meets every criterion. */
export function meetsEveryCriterion(t: Pick<TrialSummary, 'ready' | 'tasks' | 'checks' | 'clean'>): boolean {
  return t.ready.met && t.tasks.ok === t.tasks.of && t.checks.every((c) => c.met) && t.clean;
}

/** Rule 3: each listed density's result, from the trials that count. */
export function densityBriefs(densities: number[], trials: TrialSummary[]): DensityBrief[] {
  return densities.map((density) => {
    const at = trials.filter((t) => t.counts && t.density === density).sort((a, b) => a.number! - b.number!);
    const passed = at.filter((t) => t.passed === true).length;
    const result: DensityBrief['result'] = at.length === 0 ? 'not_tested' : passed === at.length ? 'passed' : 'failed';
    return { density, result, passed, trials: at.length, trial_results: at.map((t) => t.passed === true) };
  });
}

export interface ResultCore {
  tested_successfully: number | null;
  first_failed: number | null;
  gap: Range | null;
  not_tested: number[];
}

/** Rule 4: a run's result from its per-density results (listed in ascending order). */
export function runResultCore(briefs: DensityBrief[]): ResultCore {
  let tested: number | null = null;
  for (const b of briefs) {
    if (b.result !== 'passed') break;
    tested = b.density;
  }
  const failed = briefs.filter((b) => b.result === 'failed').map((b) => b.density);
  const first_failed = failed.length ? Math.min(...failed) : null;
  const gap: Range | null =
    tested !== null && first_failed !== null && first_failed - tested > 1 ? [tested + 1, first_failed - 1] : null;
  const not_tested = briefs.filter((b) => b.result === 'not_tested').map((b) => b.density);
  return { tested_successfully: tested, first_failed, gap, not_tested };
}

/**
 * Rule 6 (D60): a replica's span runs from its result (0 if nothing passed) to its first failure, per host vCPU.
 * Null when nothing failed: the result is only "at least".
 */
export function span(tested: number | null, firstFailed: number | null, hostVcpus: number): Range | null {
  if (firstFailed === null) return null;
  return [(tested ?? 0) / hostVcpus, firstFailed / hostVcpus];
}

/** Rule 6: the middle of a replica's span, or null when it has none. */
export function midpoint(tested: number | null, firstFailed: number | null, hostVcpus: number): number | null {
  const s = span(tested, firstFailed, hostVcpus);
  return s ? (s[0] + s[1]) / 2 : null;
}

/** Rule 6: a spec's midpoint is the mean of its replicas'; null if any replica has none, or there are none. */
export function meanMidpoint(midpoints: (number | null)[]): number | null {
  if (!midpoints.length || midpoints.some((m) => m === null)) return null;
  return (midpoints as number[]).reduce((a, b) => a + b, 0) / midpoints.length;
}

/** The union of verdicts, most severe first; `none` only if nothing fired and nothing was unknown. */
export function unionVerdicts(lists: Verdict[][]): Verdict[] {
  const seen = new Set(lists.flat());
  const fired = VERDICTS.filter((v) => v !== 'none' && v !== 'unknown' && seen.has(v));
  if (fired.length) return fired;
  return seen.has('unknown') || seen.size === 0 ? ['unknown'] : ['none'];
}

/** [min, max] of the values, or null if there are none. */
export function rangeOf(values: (number | null | undefined)[]): Range | null {
  const v = values.filter((x): x is number => typeof x === 'number' && Number.isFinite(x));
  return v.length ? [Math.min(...v), Math.max(...v)] : null;
}

/** Rule 5: USD per 1,000 tasks for a window of `seconds` shared by `density` tasks. */
export function costPer1000(priceUsdPerHour: number, seconds: number, density: number): number {
  return (1000 * priceUsdPerHour * seconds) / 3600 / density;
}

/** Host kind follows from the instance type, never an input (D56). */
export function hostKindOf(instanceType: string): 'nested' | 'metal' {
  return /\.metal(-|$)/.test(instanceType) ? 'metal' : 'nested';
}
