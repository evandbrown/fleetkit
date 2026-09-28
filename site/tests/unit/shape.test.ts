// The views' data shaping, run over the fixtures.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  campaignHeadline,
  campaignRefs,
  chartRows,
  closestSlo,
  compareRows,
  comparisonLine,
  coresStack,
  densityRows,
  differingRows,
  headline,
  lanes,
  limitBars,
  limitPanels,
  runAttribution,
  runResult,
  sameCriteria,
  specCard,
  specLabels,
  specResults,
  specSections,
  trialSlos,
  type SpecRef,
} from '../../src/lib/shape';
import { differing, FIELDS, field, flatten, mib, show } from '../../src/lib/spec';
import type { CampaignDoc, Index, RunDoc, TrialDoc } from '../../src/lib/types';

const DATA = join(__dirname, '../fixtures/data/campaigns');
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;

const syn = read<CampaignDoc>('nested-sizes-synthetic', 'campaign.json');
const hv = read<CampaignDoc>('nested-hv-synthetic', 'campaign.json');
const synRun = (id: string) => read<RunDoc>('nested-sizes-synthetic', 'runs', `${id}.json`);
const cap = read<CampaignDoc>('cap-baseline-1', 'campaign.json');
const capRun = read<RunDoc>('cap-baseline-1', 'runs', 'baseline-r1.json');
const capTrial = (id: string) => read<TrialDoc>('cap-baseline-1', 'runs', 'baseline-r1', `${id}.json`);
const index = JSON.parse(readFileSync(join(DATA, '..', 'index.json'), 'utf8')) as Index;
const docs = new Map([syn, hv, cap].map((c) => [c.id, c]));
const refs = (c: CampaignDoc): SpecRef[] => campaignRefs(c);

describe('specs, from the input schema', () => {
  it('label every field in the schema order', () => {
    expect(FIELDS.map((f) => f.path).slice(0, 4)).toEqual([
      'worker_host.instance_type', 'hypervisor.name', 'hypervisor.virtio_transport', 'hypervisor.virtio_rng',
    ]);
    expect(field('microvm.memory_mib')).toMatchObject({ label: 'MicroVM memory', unit: 'MiB', tier: 'basic', group: 'MicroVM' });
    expect(field('densities')).toMatchObject({ label: 'Densities', group: 'Densities' });
  });

  it('write values plainly', () => {
    expect(show('hypervisor.name', 'cloud-hypervisor')).toBe('Cloud Hypervisor');
    expect(show('microvm.memory_mib', 2048)).toBe('2 GiB');
    expect(show('criteria.step_p50_target_ms', 1000)).toBe('1,000 ms');
    expect(show('densities', [1, 2, 4])).toBe('1, 2, 4');
    expect(mib(1536)).toBe('1,536 MiB');
  });

  it('group every input by section, marking what a spec changed', () => {
    const groups = specSections(syn.specs[1].spec, syn.specs[1].changes);
    expect(groups.map((g) => g.group)).toEqual(['Worker host', 'Hypervisor', 'MicroVM', 'Densities', 'Success criteria', 'Procedure', 'Support host']);
    expect(groups.find((g) => g.group === 'Success criteria')!.rows[0].label).toBe('Each step p50');
    const changed = groups.flatMap((g) => g.rows.filter((r) => r.base !== undefined));
    expect(changed.map((r) => [r.path, r.base, r.value])).toEqual([
      ['worker_host.instance_type', 'm8i.4xlarge', 'm8i.2xlarge'],
      ['densities', '1, 2, 4, 8, 12, 16', '1, 2, 3, 4, 6, 8'],
    ]);
    expect(groups.flatMap((g) => g.rows).length).toBe(Object.keys(flatten(syn.specs[1].spec)).length);
  });
});

describe('what we tested', () => {
  it('shows each spec as its host, hypervisor, microVM and densities', () => {
    expect(specCard(cap.specs[0])).toEqual({
      host: { value: 'm8i.4xlarge', sub: '16 vCPU · 64 GiB · nested' },
      hypervisor: { value: 'Firecracker', sub: null },
      microvm: '2 vCPU · 2 GiB',
      densities: [1, 2, 4, 8, 12, 16],
    });
    // The devices are named only where they differ between the specs shown.
    expect(specCard(hv.specs[1], true).hypervisor).toEqual({ value: 'Cloud Hypervisor', sub: 'PCI + RNG devices' });
  });

  it('names each spec by what sets it apart, never by its slug', () => {
    expect(specLabels(hv.specs)).toEqual(['Firecracker PCI + RNG', 'Cloud Hypervisor', 'Firecracker MMIO']);
    expect(specLabels(syn.specs)).toEqual(['m8i.4xlarge', 'm8i.2xlarge']);
    expect(specLabels(cap.specs)).toEqual(['m8i.4xlarge · Firecracker']);
    expect(campaignRefs(hv).map((r) => [r.campaignTitle, r.groupLabel])[1]).toEqual(['Synthetic hypervisors', 'Cloud Hypervisor']);
  });

  it('highlights the inputs that differ between specs, in any campaigns', () => {
    expect(differing(syn.specs.map((s) => s.spec))).toEqual(['worker_host.instance_type', 'densities']);
    expect([...differingRows(syn.specs)]).toEqual(['host', 'densities']);
    expect([...differingRows(hv.specs)]).toEqual(['hypervisor']);
    expect([...differingRows(cap.specs)]).toEqual([]);
    // Across campaigns: cap-baseline-1's spec is the synthetic 16-vCPU spec; against firecracker-mmio only the densities differ.
    expect([...differingRows([cap.specs[0], syn.specs[0]])]).toEqual([]);
    expect([...differingRows([cap.specs[0], hv.specs[2]])]).toEqual(['densities']);
  });

  it('knows when every spec is judged by the same criteria', () => {
    expect(sameCriteria([...syn.specs, ...cap.specs])).toBe(true);
    const stricter = { ...cap.specs[0], spec: { ...cap.specs[0].spec, criteria: { ...cap.specs[0].spec.criteria, task_p95_target_ms: 4000 } } };
    expect(sameCriteria([cap.specs[0], stricter])).toBe(false);
  });
});

describe('how it performed', () => {
  it('gives each spec four figures: density, per vCPU, cost and what ran out', () => {
    const [big, small] = specResults(refs(syn), docs).map(headline);
    expect(big).toEqual({ density: '8', densityNote: null, perVcpu: '0.50', cost: '$0.081–0.086', ranOut: 'Host CPU', ranOutNote: 'at 12', ranOutDensity: 12 });
    expect(small).toMatchObject({ density: '3–4', perVcpu: '0.38–0.50', ranOut: 'Host CPU', ranOutNote: 'at 4' });
    expect(headline(specResults(refs(cap), docs)[0])).toEqual({
      density: '8', densityNote: null, perVcpu: '0.50', cost: '$0.068–0.083', ranOut: 'Host CPU', ranOutNote: 'at 12', ranOutDensity: 12,
    });
  });

  it('labels deliberately missing data: a replica stopped early, a failure in one replica only', () => {
    const [, ch, mmio] = specResults(refs(hv), docs).map(headline);
    expect(ch).toMatchObject({ density: '9', densityNote: '1 of 2 replicas stopped early', ranOutNote: 'at 10 · 1 of 2 replicas' });
    expect(mmio).toMatchObject({ density: '8–9', ranOut: 'Host CPU', ranOutNote: 'at 9 · 1 of 2 replicas' });
  });

  it('keeps every replica, and averages only the midpoints (D60)', () => {
    const [big, small] = specResults(refs(syn), docs);
    expect(big.replicas.map((r) => r.tested_successfully)).toEqual([8, 8]);
    expect(small.replicas.map((r) => r.tested_successfully)).toEqual([4, 3]);
    expect(small.midpoint).toBeCloseTo(0.53125, 4);
    expect(small.limits).toEqual([{ verdict: 'host_cpu', runs: 2, density: 4 }]);
    expect(small.cost![0]).toBeLessThanOrEqual(small.cost![1]);
  });

  it('compares specs in one table: density, per vCPU, midpoint and its distance from the highest (D60, D62)', () => {
    const r4 = (v: number | null) => (v === null ? null : Math.round(v * 1e4) / 1e4);
    const sizes = compareRows(specResults(refs(syn), docs));
    expect(sizes.map((e) => [e.label, e.density, e.perVcpu, r4(e.midpoint), r4(e.delta), e.partial, e.missing])).toEqual([
      ['m8i.4xlarge', '8', '0.50', 0.625, null, null, null],
      ['m8i.2xlarge', '3–4', '0.38–0.50', 0.5313, -0.0937, null, null],
    ]);
    // A replica that stopped early has no interval: the midpoint comes from the replicas that have one, and says so.
    const hvRows = compareRows(specResults(refs(hv), docs));
    expect(hvRows.map((e) => [e.label, r4(e.midpoint), e.partial, e.missing, e.ranOutAt])).toEqual([
      ['Firecracker PCI + RNG', 0.6563, null, null, 'at 11'],
      ['Cloud Hypervisor', 0.5938, '1 replica stopped early', null, 'at 10 · 1 of 2 replicas'],
      ['Firecracker MMIO', 0.5313, '1 replica stopped early', null, 'at 9 · 1 of 2 replicas'],
    ]);
    // The one-line comparison: highest first.
    expect(comparisonLine(hvRows).map((e) => [e.label, r4(e.delta)])).toEqual([
      ['Firecracker PCI + RNG', null],
      ['Cloud Hypervisor', -0.0625],
      ['Firecracker MMIO', -0.125],
    ]);
  });

  it('labels a spec with no interval in any replica', () => {
    const early = specResults(refs(hv), docs)[1];
    const none = { ...early, midpoint: null, replicas: early.replicas.map((x) => ({ ...x, midpoint_per_host_vcpu: null, first_failed: null, stopped_early: true })) };
    expect(compareRows([none])[0]).toMatchObject({ midpoint: null, partial: null, missing: 'stopped early' });
  });

  it("gives a run's own headline", () => {
    const r = runResult(syn, 'm8i-2xlarge-r2')!;
    expect(r.replicas.map((x) => x.run)).toEqual(['m8i-2xlarge-r2']);
    expect(headline(r)).toMatchObject({ density: '3', perVcpu: '0.38', ranOut: 'Host CPU', ranOutNote: 'at 4' });
    expect(runResult(syn, 'no-such-run')).toBeNull();
  });

  it('chooses a campaign by its best spec', () => {
    const e = (id: string) => index.campaigns.find((c) => c.id === id)!;
    expect(campaignHeadline(e('cap-baseline-1'))).toEqual({ density: '8', label: 'microVMs', perVcpu: '0.50' });
    expect(campaignHeadline(e('nested-sizes-synthetic'))).toEqual({ density: '8', label: 'microVMs', perVcpu: '0.50' });
    expect(campaignHeadline(e('nested-hv-synthetic'))).toMatchObject({ density: '10', perVcpu: '0.63' });
  });
});

describe('the result chart', () => {
  it('has one row per run, replicas together under their spec, a mark per counting trial', () => {
    const rows = chartRows(refs(syn), docs);
    expect(rows.map((r) => [r.key, r.first, r.replica, r.groupLabel])).toEqual([
      ['nested-sizes-synthetic/m8i-4xlarge-r1', true, 1, 'm8i.4xlarge'],
      ['nested-sizes-synthetic/m8i-4xlarge-r2', false, 2, 'm8i.4xlarge'],
      ['nested-sizes-synthetic/m8i-2xlarge-r1', true, 1, 'm8i.2xlarge'],
      ['nested-sizes-synthetic/m8i-2xlarge-r2', false, 2, 'm8i.2xlarge'],
    ]);
    const r2 = rows[3];
    const at4 = r2.marks.filter((m) => m.density === 4);
    expect(at4.map((m) => [m.k, m.passed, m.x, m.ratio])).toEqual([[0, true, 0.5, null], [1, true, 0.5, null], [2, false, 0.5, null]]);
    expect(at4[2].href).toBe('#/results/nested-sizes-synthetic/runs/m8i-2xlarge-r2/trials/d4-t3');
    expect(r2.runHref).toBe('#/results/nested-sizes-synthetic/runs/m8i-2xlarge-r2');
    expect(r2.notTested.map((n) => n.density)).toEqual([8]);
    // D60's interval: from the last density that passed to the first that failed, per host vCPU.
    expect(r2.band).toEqual({ from: 3 / 8, to: 4 / 8, gap: null });
    expect(r2.note).toBeNull();
  });

  it("places each mark at its trial's closest SLO when the run is loaded", () => {
    const rows = chartRows(refs(cap), docs, new Map([['cap-baseline-1/baseline-r1', capRun]]));
    const [row] = rows;
    expect(row.band).toEqual({ from: 8 / 16, to: 12 / 16, gap: [9, 11] });
    const at = (d: number) => row.marks.filter((m) => m.density === d).map((m) => m.ratio!);
    expect(at(8).every((r) => r > 0.5 && r <= 1)).toBe(true);
    expect(at(12).every((r) => r > 1)).toBe(true);
    expect(row.marks.find((m) => m.density === 12)!.title).toMatch(/^trial 1 at density 12: failed, home p50 at 128% of its limit$/);
  });

  it('marks densities not tested and labels every run that stopped without a failure', () => {
    const rows = chartRows(refs(hv), docs);
    const early = rows.find((r) => r.key.endsWith('cloud-hypervisor-r2'))!;
    expect(early.band).toBeNull();
    expect(early.note).toBe('stopped early');
    const mmio = rows.find((r) => r.key.endsWith('firecracker-mmio-r2'))!;
    expect(mmio.notTested.map((n) => n.density)).toEqual([10, 11, 12, 14, 16]);
    expect(mmio.note).toBe('stopped early');
    expect(rows.find((r) => r.key.endsWith('firecracker-r1'))!.note).toBeNull();
  });

  it('keeps a row, labelled, for a run not published yet', () => {
    const partial: CampaignDoc = { ...syn, runs: syn.runs.filter((r) => r.id !== 'm8i-2xlarge-r2') };
    const rows = chartRows(refs(partial), new Map([[syn.id, partial]]));
    expect(rows.at(-1)).toMatchObject({ runId: 'm8i-2xlarge-r2', runHref: null, marks: [], note: 'not run yet' });
  });
});

describe('closest SLO', () => {
  it("is a trial's worst criterion as a share of its limit, capped for a failed task", () => {
    const d12 = capRun.trials.find((t) => t.id === 'd12-t1')!;
    const c = closestSlo(d12);
    expect(c.what).toBe('home p50');
    expect(c.ratio).toBeCloseTo(1.279, 2);
    const failedTask = { ...d12, tasks: { ok: 11, of: 12 } };
    expect(closestSlo(failedTask)).toEqual({ ratio: 2, what: 'a failed task' });
  });
});

describe('densities of a run', () => {
  it('say which criterion failed at 12, and in how many trials', () => {
    const rows = densityRows(capRun);
    expect(rows.map((r) => r.density)).toEqual([1, 2, 4, 8, 12, 16]);
    const d12 = rows.find((r) => r.density === 12)!;
    expect(d12.misses).toEqual([{ text: 'home p50', value: '1,063–1,563 ms', missed: 3, of: 3 }]);
    expect(d12.trials.map((t) => t.id)).toEqual(['d12-t1', 'd12-t2', 'd12-t3']);
    expect(rows.find((r) => r.density === 8)!.misses).toEqual([]);
    expect(rows.find((r) => r.density === 16)).toMatchObject({ result: 'not_tested', trials: [] });
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

describe('what limited a run, as bars', () => {
  it('shows each rule that fired at the failure, at the last pass and the failure, and the rest as others', () => {
    const b = limitBars(capRun, cap.rules)!;
    expect(b.fired.map((x) => x.key)).toEqual(['host_cpu_pressure_pct', 'host_cpu_util_pct']);
    const p = b.fired[0];
    expect(p).toMatchObject({ op: '>=', threshold: 20 });
    expect(p.at.map((a) => a.density)).toEqual([8, 12]);
    expect(p.at[1].values.map((v) => Math.round(v * 10) / 10)).toEqual([34.5, 46.5]);
    expect(b.others.length).toBe(cap.rules.length - 2);
    expect(limitBars(read<RunDoc>('nested-hv-synthetic', 'runs', 'cloud-hypervisor-r2.json'), hv.rules)).toBeNull();
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

  it('plots the host CPU rule that decided a host CPU verdict, with its threshold', () => {
    const panels = limitPanels(doc, summary, cap.rules);
    expect(panels.map((p) => p.key)).toEqual(['host_cpu_pressure_pct']);
    expect(panels[0].threshold).toEqual({ value: 20, op: '>=', fired: true });
    expect(panels[0].series[0].values.length).toBe(doc.series.host.t_ms.length);
  });

  it('plots each microVM when its own CPU allowance was the verdict', () => {
    const throttled = { ...summary, attribution: { ...summary.attribution!, verdicts: ['microvm_cpu_allowance' as const] } };
    const [p] = limitPanels(doc, throttled, cap.rules);
    expect(p).toMatchObject({ key: 'microvm_throttled_fraction', many: true });
    expect(p.series.length).toBe(12);
  });

  it('reads against the five SLOs, in the badges order', () => {
    expect(trialSlos(summary)).toEqual([
      { value: '4.9 s', met: true },
      { value: '12 of 12', met: true },
      { value: '1,279 ms', met: false, note: 'home' },
      { value: '1,735 ms', met: true, note: 'home' },
      { value: '3,515 ms', met: true },
    ]);
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
