// The rules in DATA.md ("Rules"), as functions. The dataset builder applies the same rules; contract.ts uses
// these to check that what it shipped is consistent.
import {
  COUNTING_ROLES,
  VERDICTS,
  type DensityBrief,
  type Range,
  type TrialRole,
  type TrialSummary,
  type Verdict,
} from './types';

export const TRIAL_ID = /^(?:d([1-9]\d*)-t([1-9]\d*)|(warmup|illustration|fault)(?:-([2-9]|[1-9]\d+))?)$/;

/** "d8-t2" for trial 2 at density 8. */
export function trialId(density: number, number: number): string {
  return `d${density}-t${number}`;
}

export type ParsedTrialId =
  | { counts: true; density: number; number: number }
  | { counts: false; role: 'warmup' | 'illustration' | 'fault'; nth: number };

export function parseTrialId(id: string): ParsedTrialId | null {
  const m = TRIAL_ID.exec(id);
  if (!m) return null;
  if (m[1]) return { counts: true, density: Number(m[1]), number: Number(m[2]) };
  return { counts: false, role: m[3] as 'warmup' | 'illustration' | 'fault', nth: m[4] ? Number(m[4]) : 1 };
}

export function countsTowardResult(role: TrialRole): boolean {
  return COUNTING_ROLES.includes(role);
}

/** Rule 1: a trial passes if it meets every criterion in its spec. */
export function meetsEveryCriterion(t: Pick<TrialSummary, 'ready' | 'tasks' | 'checks' | 'clean'>): boolean {
  return t.ready.met && t.tasks.ok === t.tasks.of && t.checks.every((c) => c.met) && t.clean;
}

/** Rule 3: each listed density's result, from the trials that count. */
export function densityBriefs(densities: number[], trials: TrialSummary[]): DensityBrief[] {
  return densities.map((density) => {
    const at = trials.filter((t) => t.counts && t.density === density);
    const passed = at.filter((t) => t.passed === true).length;
    const result: DensityBrief['result'] = at.length === 0 ? 'not_run' : passed === at.length ? 'passed' : 'failed';
    return { density, result, passed, trials: at.length };
  });
}

export interface ResultCore {
  tested_successfully: number | null;
  first_failed: number | null;
  not_tried: Range | null;
  not_run: number[];
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
  const not_tried: Range | null =
    tested !== null && first_failed !== null && first_failed - tested > 1 ? [tested + 1, first_failed - 1] : null;
  const not_run = briefs.filter((b) => b.result === 'not_run').map((b) => b.density);
  return { tested_successfully: tested, first_failed, not_tried, not_run };
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

/** Host kind is derived from the instance type, never an input (D56). */
export function hostKindOf(instanceType: string): 'nested' | 'metal' {
  return /\.metal(-|$)/.test(instanceType) ? 'metal' : 'nested';
}
