import { describe, expect, it } from 'vitest';
import { densityBriefs, fullSizeTrials, hostKindOf, meanMidpoint, midpoint, parseTrialId, runResultCore, span, trialId, unionVerdicts } from '../../src/lib/derive';
import type { TrialSummary } from '../../src/lib/types';

function trial(density: number, number: number, passed: boolean): TrialSummary {
  return {
    id: trialId(density, number), density, number, role: number === 1 ? 'ladder' : 'boundary', counts: true, order: 0,
    passed, at_limit: false, failed: passed ? [] : ['x'], ready: { microvms: density, all_ready_ms: 1, limit_ms: 1, met: true },
    tasks: { ok: density, of: density }, checks: [], clean: true,
    marks: { all_ready_ms: 1, release_ms: 1, last_return_ms: 1, clean_ms: 1, end_ms: 1 },
    attribution: null, host: null, microvm_mem_peak_mib: null, cost_per_1000_tasks: null,
  };
}

describe('trial ids', () => {
  it('numbers counting trials within their density', () => {
    expect(trialId(8, 2)).toBe('d8-t2');
    expect(parseTrialId('d8-t2')).toEqual({ counts: true, density: 8, number: 2 });
  });
  it('labels trials that do not count by role, unnumbered', () => {
    expect(parseTrialId('warmup')).toEqual({ counts: false, role: 'warmup', nth: 1 });
    expect(parseTrialId('warmup-2')).toEqual({ counts: false, role: 'warmup', nth: 2 });
    expect(parseTrialId('warmup-1')).toBeNull();
    expect(parseTrialId('d0-t1')).toBeNull();
    expect(parseTrialId('t005-firecracker-n8-r1')).toBeNull();
  });
});

describe('rules 3 and 4', () => {
  it('reproduces cap-baseline-1: 8 tested successfully, 12 failed, 9 to 11 and 16 not tested', () => {
    const trials = [
      trial(1, 1, true), trial(2, 1, true), trial(4, 1, true), trial(8, 1, true), trial(12, 1, false),
      trial(8, 2, true), trial(8, 3, true), trial(12, 2, false), trial(12, 3, false),
    ];
    const briefs = densityBriefs([1, 2, 4, 8, 12, 16], trials);
    expect(briefs.map((b) => b.result)).toEqual(['passed', 'passed', 'passed', 'passed', 'failed', 'not_tested']);
    expect(briefs[3].trial_results).toEqual([true, true, true]);
    expect(runResultCore(briefs)).toEqual({ tested_successfully: 8, first_failed: 12, gap: [9, 11], not_tested: [16] });
  });

  it('walks down when a boundary trial fails at the last passing density', () => {
    const trials = [
      trial(1, 1, true), trial(2, 1, true), trial(3, 1, true), trial(4, 1, true), trial(6, 1, false),
      trial(4, 2, true), trial(4, 3, false), trial(6, 2, false), trial(6, 3, false), trial(3, 2, true), trial(3, 3, true),
    ];
    const briefs = densityBriefs([1, 2, 3, 4, 6, 8], trials);
    expect(briefs.find((b) => b.density === 4)).toEqual({ density: 4, result: 'failed', passed: 2, trials: 3, trial_results: [true, true, false] });
    expect(runResultCore(briefs)).toEqual({ tested_successfully: 3, first_failed: 4, gap: null, not_tested: [8] });
  });

  it('has no result when the lowest density fails, and no limit when every density passes', () => {
    expect(runResultCore(densityBriefs([1, 2], [trial(1, 1, false)]))).toMatchObject({ tested_successfully: null, first_failed: 1 });
    expect(runResultCore(densityBriefs([1, 2], [trial(1, 1, true), trial(2, 1, true)]))).toMatchObject({
      tested_successfully: 2,
      first_failed: null,
      gap: null,
    });
  });
});

describe('rule 10: full-size screenshots', () => {
  const labelled = (role: 'warmup' | 'illustration') => ({ id: role, role, counts: false, number: null, density: 1, passed: null });
  // Walked down: trial 1 at 4 passed and trial 3 failed, so the result is 3 and the first failure trial 3 at 4.
  const walkedDown = [
    trial(1, 1, true), trial(2, 1, true), trial(3, 1, true), trial(4, 1, true), trial(6, 1, false),
    trial(4, 2, true), trial(4, 3, false), trial(6, 2, false), trial(6, 3, false), trial(3, 2, true), trial(3, 3, true),
  ];

  it('gives them to trial 1 at the result, the first trial that failed at the first failure, and the illustration', () => {
    const trials = [labelled('warmup'), ...walkedDown, labelled('illustration')];
    expect([...fullSizeTrials(trials, 3, 4)].sort()).toEqual(['d3-t1', 'd4-t3', 'illustration']);
  });
  it('gives only the last pass when nothing failed, and only the first failure when nothing passed', () => {
    expect([...fullSizeTrials([trial(1, 1, true), trial(2, 1, true)], 2, null)]).toEqual(['d2-t1']);
    expect([...fullSizeTrials([labelled('warmup'), trial(1, 1, false)], null, 1)]).toEqual(['d1-t1']);
  });
});

describe('rule 6: midpoints (D60)', () => {
  it("spans a replica from its result to its first failure, per host vCPU", () => {
    expect(span(8, 12, 16)).toEqual([0.5, 0.75]);
    expect(midpoint(8, 12, 16)).toBe(0.625);
    expect(midpoint(4, 6, 8)).toBe(0.625);
    expect(midpoint(3, 4, 8)).toBe(0.4375);
  });
  it('starts the span at 0 when nothing passed, and has no midpoint without a failure', () => {
    expect(span(null, 1, 8)).toEqual([0, 0.125]);
    expect(midpoint(16, null, 16)).toBeNull();
  });
  it("averages a spec's replicas, and has none if any replica has none", () => {
    expect(meanMidpoint([0.625, 0.4375])).toBe(0.53125);
    expect(meanMidpoint([0.625, null])).toBeNull();
    expect(meanMidpoint([])).toBeNull();
  });
});

describe('derived values', () => {
  it('orders verdicts by severity', () => {
    expect(unionVerdicts([['none'], ['io', 'host_cpu']])).toEqual(['host_cpu', 'io']);
    expect(unionVerdicts([['none'], ['none']])).toEqual(['none']);
    expect(unionVerdicts([['none'], ['unknown']])).toEqual(['unknown']);
  });
  it('derives host kind from the instance type', () => {
    expect(hostKindOf('m8i.4xlarge')).toBe('nested');
    expect(hostKindOf('m8i.metal-48xl')).toBe('metal');
  });
});
