import { describe, expect, it } from 'vitest';
import { densityBriefs, hostKindOf, parseTrialId, runResultCore, trialId, unionVerdicts } from '../../src/lib/derive';
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
  it('reproduces cap-baseline-1: 8 tested successfully, 12 failed, 9 to 11 not tried, 16 not run', () => {
    const trials = [
      trial(1, 1, true), trial(2, 1, true), trial(4, 1, true), trial(8, 1, true), trial(12, 1, false),
      trial(8, 2, true), trial(8, 3, true), trial(12, 2, false), trial(12, 3, false),
    ];
    const briefs = densityBriefs([1, 2, 4, 8, 12, 16], trials);
    expect(briefs.map((b) => b.result)).toEqual(['passed', 'passed', 'passed', 'passed', 'failed', 'not_run']);
    expect(runResultCore(briefs)).toEqual({ tested_successfully: 8, first_failed: 12, not_tried: [9, 11], not_run: [16] });
  });

  it('walks down when a boundary trial fails at the last passing density', () => {
    const trials = [
      trial(1, 1, true), trial(2, 1, true), trial(3, 1, true), trial(4, 1, true), trial(6, 1, false),
      trial(4, 2, true), trial(4, 3, false), trial(6, 2, false), trial(6, 3, false), trial(3, 2, true), trial(3, 3, true),
    ];
    const briefs = densityBriefs([1, 2, 3, 4, 6, 8], trials);
    expect(briefs.find((b) => b.density === 4)).toEqual({ density: 4, result: 'failed', passed: 2, trials: 3 });
    expect(runResultCore(briefs)).toEqual({ tested_successfully: 3, first_failed: 4, not_tried: null, not_run: [8] });
  });

  it('has no result when the lowest density fails, and no limit when every density passes', () => {
    expect(runResultCore(densityBriefs([1, 2], [trial(1, 1, false)]))).toMatchObject({ tested_successfully: null, first_failed: 1 });
    expect(runResultCore(densityBriefs([1, 2], [trial(1, 1, true), trial(2, 1, true)]))).toMatchObject({
      tested_successfully: 2,
      first_failed: null,
      not_tried: null,
    });
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
