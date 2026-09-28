// The views' data shaping, run over the fixtures.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  answerRows,
  answerText,
  campaignFigure,
  campaignHeadline,
  campaignRefs,
  chartRows,
  closestSlo,
  compareRows,
  coresStack,
  firedRule,
  densityRows,
  headline,
  lanes,
  latency,
  limitBars,
  limitPanels,
  limitPick,
  runAttribution,
  runResult,
  sameCriteria,
  sharedBands,
  showsChromium,
  specLabels,
  specResults,
  specSections,
  trialSlos,
  type SpecRef,
} from '../../src/lib/shape';
import { latencyTicks, makeBands, tickShown } from '../../src/lib/bands';
import { laneLayout, LANES_MAX_PX } from '../../src/lib/scale';
import { differing, FIELDS, field, flagsShort, flagsWords, flatten, mib, show } from '../../src/lib/spec';
import type { CampaignDoc, Index, RunDoc, SpecDoc, TrialDoc } from '../../src/lib/types';

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
    expect(groups.map((g) => g.group)).toEqual(['Worker host', 'Hypervisor', 'MicroVM', 'Browsers per host', 'Success criteria', 'Procedure', 'Support host']);
    expect(groups.find((g) => g.group === 'Success criteria')!.rows[0].label).toBe('Step p50 target');
    const changed = groups.flatMap((g) => g.rows.filter((r) => r.base !== undefined));
    expect(changed.map((r) => [r.path, r.base, r.value])).toEqual([
      ['worker_host.instance_type', 'm8i.4xlarge', 'm8i.2xlarge'],
      ['densities', '1, 2, 4, 8, 12, 16', '1, 2, 3, 4, 6, 8'],
    ]);
    expect(groups.flatMap((g) => g.rows).length).toBe(Object.keys(flatten(syn.specs[1].spec)).length);
  });

  it('mark a list a spec changed from the default the base leaves out, and write it whole', () => {
    const flags = ['--disable-features=PreloadTopChromeWebUI,WebUIOmniboxPopup', '--no-zygote'];
    const spec = { ...syn.specs[1].spec, workload: { chromium_extra_flags: flags } };
    const changes = [...syn.specs[1].changes, { path: 'workload.chromium_extra_flags', base: [], value: flags }];
    const workload = specSections(spec, changes).find((g) => g.group === 'Workload')!;
    expect(workload.rows).toEqual([
      { path: 'workload.chromium_extra_flags', label: 'Extra Chromium flags', value: flags.join(' '), base: 'none' },
    ]);
    // A spec that writes the default list shows it as none, unmarked.
    const none = specSections({ ...syn.specs[0].spec, workload: { chromium_extra_flags: [] } }, syn.specs[0].changes);
    expect(none.find((g) => g.group === 'Workload')!.rows).toEqual([
      { path: 'workload.chromium_extra_flags', label: 'Extra Chromium flags', value: 'none', base: undefined },
    ]);
  });
});

describe('what we tested', () => {
  it('sees a guest console or memory pages only where they are not the defaults', () => {
    const vm = (microvm: object) => ({ ...cap.specs[0], spec: { ...cap.specs[0].spec, microvm: { vcpus: 2, memory_mib: 2048, ...microvm } } });
    // Left out is the default: a spec that writes the default doesn't differ from one that leaves it out.
    const explicit = vm({ console: 'verbose', memory_pages: '4k' });
    expect(differing([cap.specs[0].spec, explicit.spec])).toEqual([]);
    expect(differing([cap.specs[0].spec, vm({ console: 'quiet' }).spec])).toEqual(['microvm.console']);
    expect(differing([cap.specs[0].spec, vm({ memory_pages: 'thp' }).spec])).toEqual(['microvm.memory_pages']);
  });

  it('shows extra Chromium flags, a list, as its own row only where some spec adds them', () => {
    const LEAN = ['--disable-features=PreloadTopChromeWebUI,WebUIOmniboxPopup,WebUIOmniboxAimPopup,WebUIOmniboxFullPopup'];
    const flagged = (s: SpecDoc, flags: string[] | undefined): SpecDoc => ({
      ...s,
      name: `${s.name}-f${flags?.length ?? 'x'}`,
      spec: { ...s.spec, ...(flags ? { workload: { chromium_extra_flags: flags } } : {}) },
    });
    const [big, small] = syn.specs;
    const four = [big, flagged(big, LEAN), small, flagged(small, LEAN)];
    expect(differing(four.map((s) => s.spec))).toContain('workload.chromium_extra_flags');
    // One set of flags: labels say which specs add it; the table says what it is.
    expect(specLabels(four)).toEqual([
      'm8i.4xlarge · no extra flags',
      'm8i.4xlarge · extra flags',
      'm8i.2xlarge · no extra flags',
      'm8i.2xlarge · extra flags',
    ]);
    // Several: the flags, a long one cut to its name, unless cut two different lists would read the same.
    const one = ['--renderer-process-limit=1'];
    expect(specLabels([big, flagged(big, LEAN), { ...flagged(big, one), name: 'one' }])).toEqual([
      'no extra flags', '--disable-features=…', '--renderer-process-limit=1',
    ]);
    const other = ['--disable-features=PreloadTopChromeWebUI,WebUIOmniboxPopup'];
    expect(specLabels([flagged(big, LEAN), flagged(big, other)])).toEqual([LEAN[0], other[0]]);
    expect(flagsWords([big.spec, flagged(big, ['--renderer-process-limit=1']).spec])).toEqual(['none', '--renderer-process-limit=1']);
    expect(flagsShort(LEAN)).toBe('--disable-features=…');
    // A list equal to the default, written or left out, is no difference and no row.
    const written = flagged(big, []);
    expect(differing([big.spec, written.spec])).toEqual([]);
    expect(showsChromium([big, written])).toBe(false);
    expect(showsChromium(four)).toBe(true);
    expect(specLabels([big, written])).toEqual(specLabels([big, { ...big, name: 'again' }]));
  });

  it('names each spec by what sets it apart, never by its slug', () => {
    expect(specLabels(hv.specs)).toEqual(['Firecracker PCI + RNG', 'Cloud Hypervisor', 'Firecracker MMIO']);
    expect(specLabels(syn.specs)).toEqual(['m8i.4xlarge', 'm8i.2xlarge']);
    expect(specLabels(cap.specs)).toEqual(['m8i.4xlarge · Firecracker']);
    expect(campaignRefs(hv).map((r) => [r.campaignTitle, r.groupLabel])[1]).toEqual(['Synthetic hypervisors', 'Cloud Hypervisor']);
    // The catalog's short name, where a spec has one, is what the pages call it (D93).
    const named: CampaignDoc = { ...hv, specs: hv.specs.map((s, i) => (i === 1 ? { ...s, short: 'CH' } : s)) };
    expect(campaignRefs(named).map((r) => r.groupLabel)).toEqual(['Firecracker PCI + RNG', 'CH', 'Firecracker MMIO']);
  });

  it('knows which inputs differ between specs', () => {
    expect(differing(syn.specs.map((s) => s.spec))).toEqual(['worker_host.instance_type', 'densities']);
    expect(differing(cap.specs.map((s) => s.spec))).toEqual([]);
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
    // The cost is one number (D102): the median of the replicas' steady-state range middles (D81), 0.20165 and
    // 0.19655 here; the ranges and the burst go in the note. The count is the one most replicas held.
    expect(big).toEqual({
      density: '8', densityNote: null, count: '8', perVcpu: '0.50', cost: '$0.199', costValue: expect.closeTo(0.1991, 6), burst: '$0.208',
      costNote: 'Steady state over 2 replicas: $0.193–0.211 per 1,000 tasks; one burst $0.206–0.212',
      ranOut: 'Host CPU', ranOutNote: 'at 12', ranOutDensity: 12,
    });
    // Its replicas ran out at 6 and at 4: the note gives both ends, never only the lowest. One replica's host was
    // never full, so the cost comes from the other, and the note says so.
    expect(small).toMatchObject({
      density: '3–4', count: '3', perVcpu: '0.38–0.50', cost: '$0.202', burst: '$0.226',
      costNote: 'Steady state (1 of 2 never filled the host): $0.196–0.208 per 1,000 tasks; one burst $0.204–0.251',
      ranOut: 'Host CPU', ranOutNote: 'at 4–6', ranOutDensity: 4,
    });
    expect(headline(specResults(refs(cap), docs)[0])).toEqual({
      density: '8', densityNote: null, count: '8', perVcpu: '0.50', cost: '$0.141', costValue: expect.closeTo(0.1413, 6), burst: '$0.193',
      costNote: 'Steady state: $0.141–0.142 per 1,000 tasks; one burst $0.184–0.201',
      ranOut: 'Host CPU', ranOutNote: 'at 12', ranOutDensity: 12,
    });
    // No steady-state figure anywhere: the burst stands alone, and the note says why.
    const [, , mmio] = specResults(refs(hv), docs);
    const noFleet = { ...mmio, replicas: mmio.replicas.map((x) => ({ ...x, cost_per_1000_tasks: { ...x.cost_per_1000_tasks!, steady_state: null } })) };
    expect(headline(noFleet)).toMatchObject({
      cost: null, costValue: null, burst: '$0.201',
      costNote: 'One burst over 2 replicas: $0.193–0.214 per 1,000 tasks. The host was never full, so there is no steady-state figure.',
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
    expect(small.limits).toEqual([{ verdict: 'host_cpu', runs: 2, density: 4, densityMax: 6 }]);
    expect(big.limits).toEqual([{ verdict: 'host_cpu', runs: 2, density: 12, densityMax: 12 }]);
    expect(small.cost!.burst[0]).toBeLessThanOrEqual(small.cost!.burst[1]);
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
  });

  it('answers in a line: what each spec fit, grouped, and what ran out (D72)', () => {
    expect(answerText(specResults(refs(cap), docs))).toBe('8 browsers met every SLO, 0.50 per vCPU. Host CPU ran out at 12.');
    // Across host sizes the figure is per host vCPU.
    expect(answerText(specResults(refs(syn), docs))).toBe(
      'm8i.4xlarge fits 0.50 browsers per vCPU, m8i.2xlarge 0.38–0.50. Host CPU ran out first in both.',
    );
    expect(answerText(specResults(refs(hv), docs))).toBe(
      'Firecracker PCI + RNG fits 10 browsers, Cloud Hypervisor 9, Firecracker MMIO 8–9. Host CPU ran out first in all three.',
    );
    // With more than three results the line names the best and gives the range of the rest.
    const three = specResults(refs(hv), docs);
    const six = [
      ...three,
      ...three.map((r) => ({
        ...r,
        ref: { ...r.ref, groupLabel: `${r.ref.groupLabel} B` },
        replicas: r.replicas.map((x) => ({ ...x, tested_successfully: (x.tested_successfully ?? 0) - 3 })),
      })),
    ];
    expect(answerText(six)).toBe('Firecracker PCI + RNG fits the most, 10 browsers; the other 5 specs 5–9. Host CPU ran out first in all 6.');
  });

  it('gives each run of each spec its last pass and first failure, with a link to the run', () => {
    const rows = answerRows(specResults(refs(hv), docs), docs);
    expect(rows.map((r) => [r.label, r.series, r.runs.map((x) => [x.replica, x.passed, x.failed, x.stoppedEarly])])).toEqual([
      ['Firecracker PCI + RNG', 0, [[1, 10, 11, false], [2, 10, 11, false]]],
      ['Cloud Hypervisor', 1, [[1, 9, 10, false], [2, 9, null, true]]],
      ['Firecracker MMIO', 2, [[1, 8, 9, false], [2, 9, null, true]]],
    ]);
    expect(rows[1].runs[1].href).toBe('#/results/nested-hv-synthetic/runs/cloud-hypervisor-r2');
    const partial: CampaignDoc = { ...syn, runs: syn.runs.filter((r) => r.id !== 'm8i-2xlarge-r2') };
    const p = answerRows(specResults(refs(partial), new Map([[syn.id, partial]])), new Map([[syn.id, partial]]));
    expect(p[1].runs[1]).toMatchObject({ replica: 2, href: null, passed: null });
  });

  it('crowns the clear winner on cost, and scales each cost to the dearest (D102)', () => {
    // Firecracker's replica middles are both under every other spec's: a clear winner, and the dearest sets the scale.
    const hvRows = answerRows(specResults(refs(hv), docs), docs);
    expect(hvRows.map((r) => [r.label, r.cost, r.best, Math.round(r.share! * 100)])).toEqual([
      ['Firecracker PCI + RNG', '$0.190', true, 94],
      ['Cloud Hypervisor', '$0.202', false, 100],
      ['Firecracker MMIO', '$0.201', false, 100],
    ]);
    // The catalog's featured spec leads instead when it is named, and is no clear winner when it isn't the cheapest.
    const featured = answerRows(specResults(refs(hv), docs), docs, 'cloud-hypervisor');
    expect(featured.map((r) => r.best)).toEqual([false, false, false]);
    // One costed spec: nothing to win against, so nothing is crowned.
    expect(answerRows(specResults(refs(cap), docs), docs).map((r) => [r.best, r.share])).toEqual([[false, 1]]);
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
    expect(campaignHeadline(e('cap-baseline-1'))).toEqual({ density: '8', label: 'browsers', perVcpu: '0.50', spec: 'baseline' });
    expect(campaignHeadline(e('nested-sizes-synthetic'))).toEqual({ density: '8', label: 'browsers', perVcpu: '0.50', spec: 'm8i-4xlarge' });
    expect(campaignHeadline(e('nested-hv-synthetic'))).toMatchObject({ density: '10', perVcpu: '0.63', spec: 'firecracker' });
  });

  it("gives each campaign's card its answer: one spec's figures, or a bar per spec (D72)", () => {
    const e = (id: string) => index.campaigns.find((c) => c.id === id)!;
    expect(campaignFigure(e('cap-baseline-1')).single).toMatchObject({ density: '8', perVcpu: '0.50' });
    const sizes = campaignFigure(e('nested-sizes-synthetic'), ['m8i.4xlarge', 'm8i.2xlarge']);
    expect(sizes.metric).toBe('Per vCPU');
    expect(sizes.bars.map((b) => [b.label, b.text])).toEqual([['m8i.4xlarge', '0.50'], ['m8i.2xlarge', '0.38–0.50']]);
    const hvs = campaignFigure(e('nested-hv-synthetic'));
    expect(hvs.metric).toBe('Most browsers');
    expect(hvs.bars.map((b) => [b.text, b.lo, b.hi])).toEqual([['10', 10, 10], ['9', 9, 9], ['8–9', 8, 9]]);
    expect(hvs.max).toBe(10);
    // A spec none of whose replicas reached a failure is a floor, as the headline writes it.
    const capped = { ...e('nested-hv-synthetic'), outcomes: e('nested-hv-synthetic').outcomes.map((o, i) => (i === 0 ? { ...o, replicas: o.replicas.map((r) => ({ ...r, first_failed: null })) } : o)) };
    expect(campaignFigure(capped).bars[0].text).toBe('≥ 10');
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
    expect(row.marks.find((m) => m.density === 12)!.title).toMatch(/^trial 1 at 12 browsers: failed, home p50 at 128% of its limit$/);
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

describe('the shared density bands', () => {
  it('give each density tested a band, and the listed densities above them one not-tested band', () => {
    const capRuns = new Map([['cap-baseline-1/baseline-r1', capRun]]);
    const bands = sharedBands(latency(refs(cap), docs, capRuns), chartRows(refs(cap), docs, capRuns));
    expect(bands.map((b) => [b.label, b.untested])).toEqual([
      ['1', false],
      ['2', false],
      ['4', false],
      ['8', false],
      ['12', false],
      ['16', true],
    ]);
  });
});

describe('the density ticks', () => {
  it('all show when they fit, else every other one, counted down from the highest density tested', () => {
    const perVcpu = makeBands([1, 4, 8, 9, 10, 11].map((d) => d / 16).concat([1, 24, 48, 96, 108, 120, 132, 144, 160, 176, 192].map((d) => d / 192)), [], true);
    expect(perVcpu.map((b) => b.label)).toEqual(['0.01', '0.06', '0.13', '0.25', '0.5', '0.56', '0.63', '0.69', '0.75', '0.83', '0.92', '1']);
    const all = tickShown(perVcpu, 77);
    expect(perVcpu.every((_, i) => all(i))).toBe(true);
    // A phone: about 26 px a band, for ticks 24 px wide.
    const phone = tickShown(perVcpu, 26);
    expect(perVcpu.filter((_, i) => phone(i)).map((b) => b.label)).toEqual(['0.06', '0.25', '0.56', '0.69', '0.83', '1']);
    // The not-tested band keeps its tick.
    const withUntested = makeBands([1, 24, 48, 96, 108, 120, 132, 144, 160, 176, 184], [192], false);
    const t = tickShown(withUntested, 20);
    expect(withUntested.filter((_, i) => t(i)).map((b) => b.label)).toEqual(['1', '48', '108', '132', '160', '184', '192']);
  });
});

describe('latency by density', () => {
  const synRuns = new Map(syn.runs.map((r) => [`${syn.id}/${r.id}`, synRun(r.id)]));

  it("gives every counting trial its slowest step p50 and its task p95, per spec, per host vCPU across sizes", () => {
    const lat = latency(refs(syn), docs, synRuns);
    expect(lat.perVcpu).toBe(true);
    expect(lat.replicas).toBe(2);
    expect(lat.stepTargets).toEqual([1000]);
    expect(lat.taskTargets).toEqual([5000]);
    expect(lat.series.map((s) => [s.label, s.points.length])).toEqual([['m8i.4xlarge', 18], ['m8i.2xlarge', 20]]);
    const p = lat.series[1].points.find((x) => x.key === 'nested-sizes-synthetic/m8i-2xlarge-r2/d4-t3')!;
    expect(p).toMatchObject({
      series: 1,
      replica: 2,
      density: 4,
      x: 0.5,
      step: { ms: 1246.9796, target: 1000, name: 'home' },
      task: { ms: 2809.2428, target: 5000 },
      passed: false,
      title: 'm8i.2xlarge · replica 2 · trial 3 at 4 browsers',
      href: '#/results/nested-sizes-synthetic/runs/m8i-2xlarge-r2/trials/d4-t3',
    });
    // Warm-ups and illustrations aren't judged, so they aren't drawn; points run low density to high.
    const xs = lat.series.flatMap((s) => s.points.map((q) => q.x));
    expect(lat.series.every((s) => s.points.every((q, i, a) => i === 0 || a[i - 1].x <= q.x))).toBe(true);
    expect(xs.length).toBe(38);
  });

  it('uses the density itself on one host size, and names nothing a single run needs no name for', () => {
    const lat = latency(refs(cap), docs, new Map([['cap-baseline-1/baseline-r1', capRun]]));
    expect(lat.perVcpu).toBe(false);
    expect(lat.replicas).toBe(1);
    const at12 = lat.series[0].points.filter((p) => p.density === 12);
    expect(at12.map((p) => [p.x, p.title, p.passed])).toEqual([
      [12, 'trial 1 at 12 browsers', false],
      [12, 'trial 2 at 12 browsers', false],
      [12, 'trial 3 at 12 browsers', false],
    ]);
    expect(at12[0].step).toEqual({ ms: 1279.246, target: 1000, name: 'home' });
  });

  it('draws nothing for runs whose documents are not loaded', () => {
    expect(latency(refs(syn), docs, new Map()).series.every((s) => s.points.length === 0)).toBe(true);
  });

  it('gives each spec the median of its trials at every count it tested, replicas pooled, failed trials included', () => {
    const lat = latency(refs(syn), docs, synRuns);
    const small = lat.series[1];
    expect(small.medians.map((m) => [m.x, m.n])).toEqual([[0.125, 2], [0.25, 2], [0.375, 4], [0.5, 6], [0.75, 6]]);
    const at4 = small.medians.find((m) => m.x === 0.5)!;
    // Six trials at 4 browsers on the 8-vCPU host across both replicas: the even count averages the middle two.
    expect(at4.step).toBeCloseTo(985, 3);
    expect(at4.task).toBeCloseTo(2583.855, 2);
    expect(small.medians.at(-1)!.step).toBeCloseTo(1354.449, 2);
    expect(lat.series[0].medians.map((m) => m.x)).toEqual([0.0625, 0.125, 0.25, 0.5, 0.75]);
    const one = latency(refs(cap), docs, new Map([['cap-baseline-1/baseline-r1', capRun]])).series[0];
    expect(one.medians.map((m) => [m.x, m.n, m.step])).toEqual([[1, 1, 438.476], [2, 1, 507.96], [4, 1, 522.9515], [8, 3, 806.926], [12, 3, 1279.246]]);
  });

  it('ticks a linear x axis every 0.25 per vCPU, labelled every 0.5, with a failure label only where it has room', () => {
    const px = (v: number) => v * 400; // 400 px per browser per vCPU
    const t = latencyTicks(2.1, px, true, [0.75, 0.5, 1.6875]);
    expect(t.map((k) => k.v)).toEqual([0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.6875, 1.75, 2]);
    expect(t.filter((k) => k.label !== null).map((k) => [k.label, k.failure])).toEqual([
      ['0', false], ['0.5', false], ['0.75', true], ['1', false], ['1.5', false], ['1.69', true], ['2', false],
    ]);
    // Narrower: 1.69 is 19 px from 1.75, so only 0.75 (100 px from its neighbours) keeps its label.
    const narrow = latencyTicks(2.1, (v) => v * 100, true, [0.75, 0.5, 1.6875]);
    expect(narrow.filter((k) => k.failure).map((k) => k.label)).toEqual([]);
    expect(latencyTicks(2.1, (v) => v * 150, true, [0.75]).filter((k) => k.failure).map((k) => k.label)).toEqual(['0.75']);
    // Per host: round steps, all labelled; a failure at 152 sits 70 px from 140 at 5.8 px a browser, one at 144 does not.
    const host = latencyTicks(159.6, (v) => v * 5.8, false, [152], 6);
    expect(host.map((k) => k.label)).toEqual(['0', '20', '40', '60', '80', '100', '120', '140', '152']);
    expect(host.find((k) => k.failure)?.v).toBe(152);
    expect(latencyTicks(159.6, (v) => v * 5.8, false, [144], 6).some((k) => k.failure)).toBe(false);
  });
});

describe('at the limit', () => {
  const runsOf = (c: CampaignDoc) =>
    new Map(c.runs.map((r) => [`${c.id}/${r.id}`, read<RunDoc>(c.id, 'runs', `${r.id}.json`)]));

  it("picks the replica that failed lowest, and there the first trial that failed", () => {
    const [big, small] = refs(syn);
    const s = limitPick(small, syn, runsOf(syn))!;
    expect([s.run.id, s.density, s.trial.id, s.failed, s.ranOut]).toEqual(['m8i-2xlarge-r2', 4, 'd4-t3', true, 'host_cpu']);
    // Both replicas fail at 12: the first replica.
    const b = limitPick(big, syn, runsOf(syn))!;
    expect([b.run.id, b.density, b.trial.id]).toEqual(['m8i-4xlarge-r1', 12, 'd12-t1']);
    const mmio = limitPick(refs(hv).find((r) => r.spec.name === 'firecracker-mmio')!, hv, runsOf(hv))!;
    expect([mmio.run.id, mmio.density, mmio.trial.id]).toEqual(['firecracker-mmio-r1', 9, 'd9-t1']);
    // Beside it, a passing trial at the last density that passed, in the same run.
    expect([s.pass?.density, s.pass?.trial.passed]).toEqual([3, true]);
    // The rule that ran out at the failure.
    const at4 = s.run.trials.filter((t) => t.counts && t.density === 4);
    expect(firedRule(at4, syn.rules, s.ranOut)?.key).toBe('host_cpu_util_pct');
    expect(firedRule(at4, syn.rules, null)).toBeNull();
  });

  it('falls back to the highest density that passed when nothing failed, and to nothing with no runs loaded', () => {
    const early: CampaignDoc = { ...hv, runs: hv.runs.filter((r) => r.id === 'cloud-hypervisor-r2') };
    const ch = refs(early).find((r) => r.spec.name === 'cloud-hypervisor')!;
    const p = limitPick(ch, early, runsOf(early))!;
    expect([p.run.id, p.density, p.trial.id, p.failed, p.ranOut]).toEqual(['cloud-hypervisor-r2', 9, 'd9-t1', false, null]);
    expect(limitPick(ch, early, new Map())).toBeNull();
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

  it("never stacks host CPU past the host's vCPUs, keeping each process's share, and leaves the data alone", () => {
    // A boot spike, as cumulative counters give on a 192-vCPU host: 1,150 vCPUs in one short interval.
    const cores = doc.series.host.cores!;
    const spiked: TrialDoc = {
      ...doc,
      series: { ...doc.series, host: { ...doc.series.host, cores: { ...cores, microvm_vcpus: cores.microvm_vcpus.map((v, i) => (i === 3 ? 1000 : v)), hypervisor: cores.hypervisor.map((v, i) => (i === 3 ? 150 : v)) } } },
    };
    const total = (s: ReturnType<typeof coresStack>, i: number) => s!.layers.reduce((a, l) => a + l.values[i], 0);
    const raw = coresStack(spiked)!;
    const clipped = coresStack(spiked, 16)!;
    expect(total(raw, 3)).toBeGreaterThan(1150);
    expect(total(clipped, 3)).toBeCloseTo(16, 9);
    for (let i = 0; i < clipped.t.length; i++) expect(total(clipped, i)).toBeLessThanOrEqual(16 + 1e-9);
    // Each process keeps its share of the sample.
    const share = (s: ReturnType<typeof coresStack>, k: string) => s!.layers.find((l) => l.key === k)!.values[3] / total(s, 3);
    expect(share(clipped, 'hypervisor')).toBeCloseTo(share(raw, 'hypervisor'), 9);
    // A sample within the host is drawn as recorded; the document itself is unchanged.
    const within = raw.t.findIndex((_, i) => total(raw, i) <= 16 && total(raw, i) > 1);
    expect(within).toBeGreaterThanOrEqual(0);
    expect(clipped.layers.map((l) => l.values[within])).toEqual(raw.layers.map((l) => l.values[within]));
    expect(spiked.series.host.cores!.microvm_vcpus[3]).toBe(1000);
  });

  it('keeps a small trial\'s lanes as they were, and packs hundreds into about 480 px', () => {
    const height = (n: number, compact = false) => n * laneLayout(n, compact).rowH;
    // Small trials: rows by size, every lane (or every fifth) numbered.
    expect([12, 32, 64].map((n) => laneLayout(n).rowH)).toEqual([18, 12, 7]);
    expect([12, 32, 64].map((n) => laneLayout(n, true).rowH)).toEqual([13, 9, 6]);
    expect(laneLayout(12).labelled(7)).toBe(true);
    expect([0, 1, 5, 10].map((r) => laneLayout(48).labelled(r))).toEqual([true, false, true, true]);
    expect(laneLayout(64).dense).toBe(false);
    // 192 lanes (a metal host's full density): thin rows, the whole chart under 480 px with its axis, numbered 1, 10, 20...
    for (const compact of [false, true]) {
      const L = laneLayout(192, compact);
      expect(L.dense).toBe(true);
      expect(height(192, compact)).toBeLessThanOrEqual(LANES_MAX_PX);
      expect(6 + height(192, compact) + 26).toBeLessThanOrEqual(480);
      expect(Array.from({ length: 192 }, (_, r) => r).filter(L.labelled).map((r) => r + 1)).toEqual([1, ...Array.from({ length: 19 }, (_, i) => (i + 1) * 10)]);
    }
    // Thinner still: every 20th.
    expect(Array.from({ length: 400 }, (_, r) => r).filter(laneLayout(400).labelled).slice(0, 3).map((r) => r + 1)).toEqual([1, 20, 40]);
  });

  it('draws a microVM without boot phases as one starting segment', () => {
    const noBoot: TrialDoc = { ...doc, microvms: doc.microvms.map((m) => ({ ...m, boot: undefined })) };
    const first = lanes(noBoot).lanes[0];
    expect(first.segs[0]).toMatchObject({ kind: 'boot', start: 0, end: doc.microvms[0].ready_ms });
  });
});
