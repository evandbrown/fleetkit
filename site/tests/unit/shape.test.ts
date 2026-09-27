// The views' data shaping, run over the fixtures.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  capacityLine,
  coresStack,
  densityRows,
  lanes,
  limitPanels,
  mib,
  replicaGroups,
  reproduce,
  runAttribution,
  sameEverywhere,
  specCards,
  specRows,
} from '../../src/lib/shape';
import { leafPaths } from '../../src/lib/spec';
import type { CampaignDoc, RunDoc, TrialDoc } from '../../src/lib/types';

const DATA = join(__dirname, '../fixtures/data/campaigns');
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;

const syn = read<CampaignDoc>('nested-sizes-synthetic', 'campaign.json');
const synRun = (id: string) => read<RunDoc>('nested-sizes-synthetic', 'runs', `${id}.json`);
const cap = read<CampaignDoc>('cap-baseline-1', 'campaign.json');
const capRun = read<RunDoc>('cap-baseline-1', 'runs', 'baseline-r1.json');
const capTrial = (id: string) => read<TrialDoc>('cap-baseline-1', 'runs', 'baseline-r1', `${id}.json`);

describe('spec cards', () => {
  it('mark what the 8-vCPU spec changes from the base, with the base value beside it', () => {
    const [base, small] = specCards(syn);
    expect(base.isBase).toBe(true);
    expect(base.rows.filter((r) => r.changed)).toEqual([]);
    expect(small.isBase).toBe(false);
    const changed = small.rows.filter((r) => r.changed);
    expect(changed.map((r) => r.key)).toEqual(['host', 'densities']);
    expect(changed[0]).toMatchObject({ value: 'm8i.2xlarge', base: 'Instance type m8i.4xlarge', note: '8 vCPUs, nested' });
    expect(changed[1]).toMatchObject({ value: '1, 2, 3, 4, 6, 8', base: 'Densities to test 1, 2, 4, 8, 12, 16' });
  });

  it('show the same inputs in the same order on every card, so they line up', () => {
    const keys = specCards(syn).map((c) => c.rows.map((r) => r.key));
    expect(keys[0]).toEqual(keys[1]);
    expect(keys[0]).toEqual(['host', 'hypervisor', 'microvm', 'densities', 'criteria', 'price']);
  });

  it('give the price as assumed and each spec its own runs', () => {
    const [base, small] = specCards(syn);
    expect(base.rows.find((r) => r.key === 'price')).toMatchObject({ cls: 'assumed', value: '$0.84672 an hour' });
    expect(small.runs.map((r) => r.id)).toEqual(['m8i-2xlarge-r1', 'm8i-2xlarge-r2']);
  });

  it('list a campaign of one with nothing changed', () => {
    const [only] = specCards(cap);
    expect(only.isBase).toBe(true);
    expect(only.rows.find((r) => r.key === 'microvm')?.value).toBe('2 vCPUs, 2 GiB');
    expect(sameEverywhere(cap)).toEqual(leafPaths(cap.definition.base));
  });

  it('count the inputs that are the same in every spec', () => {
    const same = sameEverywhere(syn);
    expect(same).not.toContain('host.instance_type');
    expect(same).not.toContain('procedure.densities');
    expect(same.length).toBe(leafPaths(syn.definition.base).length - 2);
  });

  it('write memory in GiB when it is whole', () => {
    expect(mib(2048)).toBe('2 GiB');
    expect(mib(1536)).toBe('1,536 MiB');
  });
});

describe('replicas grouped by spec', () => {
  it('give the spread across replicas, never a mean', () => {
    const [big, small] = replicaGroups(syn);
    expect(big.tested).toEqual([8, 8]);
    expect(small.tested).toEqual([3, 4]);
    expect(small.perHostVcpu).toEqual([0.375, 0.5]);
    expect(small.costExecution).toEqual([0.0808, 0.0918]);
    expect(small.costObserved).toEqual([0.1998, 0.2467]);
    expect(small.limits).toEqual([{ verdict: 'host_cpu', runs: 2 }]);
    expect(small.runs.map((r) => r.replica)).toEqual([1, 2]);
  });
});

describe('latency against density', () => {
  const spec = cap.specs[0];
  const line = capacityLine(capRun, spec, 0);

  it('has a point at each density the run reached, not at 16', () => {
    expect(line.points.map((p) => p.density)).toEqual([1, 2, 4, 8, 12]);
    expect(line.points.map((p) => p.result)).toEqual(['passed', 'passed', 'passed', 'passed', 'failed']);
    expect(line.points.find((p) => p.density === 8)?.perHostVcpu).toBe(0.5);
  });

  it('takes the slowest trial at each density, which decides it', () => {
    const d12 = line.points.find((p) => p.density === 12)!;
    expect(d12.trials.map((t) => t.id)).toEqual(['d12-t1', 'd12-t2', 'd12-t3']);
    expect(d12.trials.map((t) => Math.round(t.stepP50!))).toEqual([1279, 1063, 1563]);
    expect(d12.trials.every((t) => t.step === 'home')).toBe(true);
    expect(Math.round(d12.stepP50!)).toBe(1563);
    expect(d12.step).toBe('home');
  });

  it('agrees with the verdicts: passed densities sit at or under both targets', () => {
    for (const run of syn.runs.map((r) => synRun(r.id)).concat(capRun)) {
      const s = (run.campaign === syn.id ? syn : cap).specs.find((x) => x.name === run.spec)!;
      for (const p of capacityLine(run, s, 0).points.filter((q) => q.result === 'passed')) {
        expect(p.stepP50!).toBeLessThanOrEqual(s.criteria.step_p50_ms);
        expect(p.taskP95!).toBeLessThanOrEqual(s.criteria.task_p95_ms);
      }
    }
  });

  it('keeps each spec its own targets and host size', () => {
    const small = capacityLine(synRun('m8i-2xlarge-r2'), syn.specs[1], 1);
    expect(small).toMatchObject({ specIndex: 1, replica: 2, hostVcpus: 8, targets: { task_p95_ms: 5000, step_p50_ms: 1000 } });
    expect(small.points.find((p) => p.density === 4)).toMatchObject({ result: 'failed', perHostVcpu: 0.5 });
  });
});

describe('densities of a run', () => {
  it('say which criterion failed at 12, and in how many trials', () => {
    const rows = densityRows(capRun);
    expect(rows.map((r) => r.density)).toEqual([1, 2, 4, 8, 12, 16]);
    const d12 = rows.find((r) => r.density === 12)!;
    expect(d12.misses).toEqual([{ text: 'home p50 1,063–1,563 ms against 1,000 ms', missed: 3, of: 3 }]);
    expect(d12.trials.map((t) => t.id)).toEqual(['d12-t1', 'd12-t2', 'd12-t3']);
    expect(rows.find((r) => r.density === 8)!.misses).toEqual([]);
    expect(rows.find((r) => r.density === 16)).toMatchObject({ result: 'not_run', trials: [] });
  });

  it('show a failed boundary trial at the walked-down density', () => {
    const d4 = densityRows(synRun('m8i-2xlarge-r2')).find((r) => r.density === 4)!;
    expect(d4).toMatchObject({ result: 'failed', passed: 2 });
    expect(d4.misses[0]).toMatchObject({ missed: 1, of: 3 });
  });
});

describe('what limited a run', () => {
  it('sums host CPU by process over the trials at the density that failed', () => {
    const a = runAttribution(capRun)!;
    expect(a).toMatchObject({ density: 12, atFailure: true, trials: 3, verdicts: ['host_cpu'] });
    expect(a.host.map((h) => h.key)).toEqual(['microvm_vcpus', 'hypervisor', 'hostd', 'driver', 'unattributed']);
    expect(a.host.reduce((t, h) => t + h.share, 0)).toBeCloseTo(1, 6);
    expect(a.host[0].share).toBeGreaterThan(0.9);
    expect(a.guest!.reduce((t, g) => t + g.share, 0)).toBeCloseTo(1, 3);
    expect(a.guest!.find((g) => g.key === 'renderer')!.share).toBeGreaterThan(0.5);
    const pressure = a.rules.find((r) => r.key === 'host_cpu_pressure_pct')!;
    expect(pressure.fired).toBe(3);
    expect(pressure.values!.map((v) => Math.round(v * 10) / 10)).toEqual([34.5, 46.5]);
  });
});

describe("a run's spec and running it again", () => {
  it('marks the inputs a spec changes from the base', () => {
    const groups = specRows(syn, syn.specs[1]);
    const changed = groups.flatMap((g) => g.rows.filter((r) => r.changed));
    expect(changed.map((r) => [r.path, r.base, r.value])).toEqual([
      ['host.instance_type', 'm8i.4xlarge', 'm8i.2xlarge'],
      ['procedure.densities', [1, 2, 4, 8, 12, 16], [1, 2, 3, 4, 6, 8]],
    ]);
    expect(groups.flatMap((g) => g.rows).length).toBe(leafPaths(syn.specs[1].spec).length);
  });

  it('gives the resolved spec, and a campaign definition of one replica built on it', () => {
    const r = reproduce(syn, syn.specs[1]);
    expect(JSON.parse(r.spec)).toEqual(syn.specs[1].spec);
    const def = JSON.parse(r.definition);
    expect(def).toMatchObject({ format: syn.definition.format, id: 'm8i-2xlarge-again', replicas: 1 });
    expect(def.base).toEqual(syn.specs[1].spec);
    expect(def.specs).toEqual([{ name: 'm8i-2xlarge', label: 'm8i.2xlarge (8 vCPUs)', changes: {} }]);
  });
});

describe('a trial', () => {
  const doc = capTrial('d12-t1');
  const summary = capRun.trials.find((t) => t.id === 'd12-t1')!;

  it('lays each microVM out as boot phases, then five steps, then destruction', () => {
    const L = lanes(doc);
    expect(L.lanes.map((l) => l.index)).toEqual(Array.from({ length: 12 }, (_, i) => i + 1));
    const first = L.lanes[0];
    expect(first.segs.map((s) => s.kind)).toEqual(['boot', 'boot', 'boot', 'boot', 'step', 'step', 'step', 'step', 'step', 'destroy']);
    expect(first.segs.filter((s) => s.kind === 'step').map((s) => s.step)).toEqual(['home', 'search', 'open_product', 'add_to_cart', 'verify_cart']);
    for (const lane of L.lanes) {
      for (let i = 1; i < lane.segs.length; i++) expect(lane.segs[i].start).toBeGreaterThanOrEqual(lane.segs[i - 1].start);
    }
    expect(L.domain[0]).toBe(0);
    expect(L.domain[1]).toBeGreaterThanOrEqual(doc.marks.clean_ms!);
  });

  it('plots the host CPU rules behind a host CPU verdict, with their thresholds', () => {
    const panels = limitPanels(doc, summary, cap.specs[0]);
    expect(panels.map((p) => p.key)).toEqual(['host_cpu_pressure_pct', 'host_cpu_util_pct']);
    expect(panels[0].threshold).toEqual({ value: 20, op: '>=', fired: true });
    expect(panels[0].series[0].values.length).toBe(doc.series.host.t_ms.length);
  });

  it('plots each microVM when its own CPU allowance was the verdict', () => {
    const throttled = { ...summary, attribution: { ...summary.attribution!, verdicts: ['microvm_cpu_allowance' as const] } };
    const [p] = limitPanels(doc, throttled, cap.specs[0]);
    expect(p).toMatchObject({ key: 'microvm_throttled_fraction', many: true });
    expect(p.series.length).toBe(12);
  });

  it('stacks host CPU by process in a fixed order', () => {
    expect(coresStack(doc)!.layers.map((l) => l.key)).toEqual(['microvm_vcpus', 'hypervisor', 'hostd', 'driver', 'unattributed']);
  });

  it('draws a microVM without boot phases as one starting segment', () => {
    const noBoot: TrialDoc = { ...doc, microvms: doc.microvms.map((m) => ({ ...m, boot: undefined })) };
    const first = lanes(noBoot).lanes[0];
    expect(first.segs[0]).toMatchObject({ kind: 'boot', start: 0, end: doc.microvms[0].ready_ms });
  });
});
