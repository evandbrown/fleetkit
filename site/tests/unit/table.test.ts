// The "What we tested" table's rows and texts (src/lib/table.ts), run over the fixtures.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { campaignRefs, type SpecRef } from '../../src/lib/shape';
import { STANDARD_CRITERIA } from '../../src/lib/spec';
import { densitiesText, devicesText, guestText, replicasText, slosText, tableRows, warmText, type TableRow } from '../../src/lib/table';
import type { CampaignDoc, Spec, SpecDoc } from '../../src/lib/types';

const DATA = join(__dirname, '../fixtures/data/campaigns');
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const syn = read<CampaignDoc>('nested-sizes-synthetic', 'campaign.json');
const hv = read<CampaignDoc>('nested-hv-synthetic', 'campaign.json');
const cap = read<CampaignDoc>('cap-baseline-1', 'campaign.json');

const STANDARD = 'ready ≤ 180 s · tasks 100% · step p50 ≤ 2 s · step p95 ≤ 3 s · task p95 ≤ 10 s';
/** The fixtures' SLOs: the targets every campaign before 28 September 2026 was judged by, each with the standard beside it. */
const EARLIER = 'ready ≤ 180 s · tasks 100% · step p50 ≤ 1 s (standard 2 s) · step p95 ≤ 2 s (standard 3 s) · task p95 ≤ 5 s (standard 10 s)';
/** A number and its unit are joined by a no-break space, so a narrow cell breaks only at the separators. */
const NB = ' ';
const LEAN = '--disable-features=PreloadTopChromeWebUI,WebUIOmniboxPopup,WebUIOmniboxAimPopup,WebUIOmniboxFullPopup';

/** A copy of a spec with some of its inputs changed. */
const withSpec = (s: SpecDoc, spec: Partial<Spec>, name = `${s.name}-x`): SpecDoc => ({ ...s, name, spec: { ...s.spec, ...spec } });
const ref = (c: CampaignDoc, s: SpecDoc): SpecRef => ({ campaign: c.id, campaignTitle: c.title, spec: s, groupLabel: s.name });
const rowsOf = (specs: SpecDoc[], replicas = specs.map(() => 1)) => tableRows(specs.map((s) => ref(cap, s)), replicas);
const find = (rows: TableRow[], key: TableRow['key']) => rows.find((r) => r.key === key)!;
const texts = (row: TableRow) => row.values.map((v) => v.main);
const shownKeys = (rows: TableRow[]) => rows.filter((r) => r.shown).map((r) => r.key);

describe('the texts', () => {
  it('write the densities in order, a run of three or more as a range', () => {
    expect(densitiesText([1, 4, 8, 16, 20, 22, 24, 25, 26, 27, 28, 29, 30, 31, 32])).toBe('1, 4, 8, 16, 20, 22, 24–32');
    expect(densitiesText([1, 2, 3, 4, 5, 6])).toBe('1–6');
    expect(densitiesText([1, 4, 8, 9, 10, 11, 12, 14, 16])).toBe('1, 4, 8–12, 14, 16');
    // A run of two stays two numbers.
    expect(densitiesText([1, 2, 4, 8, 12, 16])).toBe('1, 2, 4, 8, 12, 16');
    expect(densitiesText([1, 96, 120, 128, 132])).toBe('1, 96, 120, 128, 132');
    expect(densitiesText([8])).toBe('8');
    expect(densitiesText([])).toBe('');
  });

  it('name the guest console and memory pages, the defaults where a spec leaves them out', () => {
    expect(guestText({ vcpus: 1, memory_mib: 1024, console: 'quiet-i8042', memory_pages: 'thp' })).toBe('quiet console, no keyboard probe · huge pages');
    expect(guestText({ vcpus: 1, memory_mib: 1024, console: 'quiet' })).toBe('quiet console · 4 KiB pages');
    expect(guestText({ vcpus: 1, memory_mib: 1024, memory_pages: 'thp' })).toBe('default console · huge pages');
    expect(guestText({ vcpus: 2, memory_mib: 2048 })).toBe('default console · 4 KiB pages');
    expect(guestText({ vcpus: 2, memory_mib: 2048, console: 'verbose', memory_pages: '4k' })).toBe('default console · 4 KiB pages');
  });

  it('write the five SLOs in order, a value other than the standard followed by the standard', () => {
    expect(slosText(STANDARD_CRITERIA)).toBe(STANDARD);
    // The metal campaign's own targets (hv-host-2).
    const metal = { ...STANDARD_CRITERIA, task_p95_target_ms: 5000, ready_timeout_s: 900 };
    expect(slosText(metal)).toBe('ready ≤ 900 s (standard 180 s) · tasks 100% · step p50 ≤ 2 s · step p95 ≤ 3 s · task p95 ≤ 5 s (standard 10 s)');
    expect(slosText(cap.specs[0].spec.criteria)).toBe(EARLIER);
    // A time limit shows only where it differs from the standard.
    expect(slosText({ ...STANDARD_CRITERIA, step_timeout_ms: 20000 })).toBe(`${STANDARD} · step limit 20 s (standard 10 s)`);
  });

  it('name the devices, the warm start and the replicas', () => {
    expect(devicesText({ name: 'firecracker', virtio_transport: 'pci', virtio_rng: true })).toBe('PCI, RNG device');
    expect(devicesText({ name: 'firecracker', virtio_transport: 'mmio', virtio_rng: false })).toBe('MMIO, no RNG device');
    const procedure = { trials_per_density: 1, boundary_trials: 2, settle_s: 10 };
    expect(warmText({ ...procedure, release_after_ready_s: 5 })).toBe('5 s wait after ready');
    expect(warmText({ ...procedure, release_after_ready_s: 0 })).toBe('no wait');
    expect(warmText(procedure)).toBe('no wait');
    expect(replicasText(5)).toBe('5 hosts per spec');
    expect(replicasText(1)).toBe('1 host per spec');
  });
});

describe('the rows', () => {
  it('come in order, shown where a spec sets them, once where they match and per spec where they differ', () => {
    const rows = tableRows(campaignRefs(syn), [2, 2]);
    expect(rows.map((r) => [r.key, r.shown, r.differs])).toEqual([
      ['host', true, true],
      ['hypervisor', true, false],
      ['devices', false, false],
      ['microvm', true, false],
      ['guest', false, false],
      ['chromium', false, false],
      ['warm', false, false],
      ['densities', true, true],
      ['replicas', true, false],
      ['slos', true, false],
      ['support', false, false],
    ]);
    expect(rows.filter((r) => r.shown).map((r) => r.label)).toEqual(['Host', 'Hypervisor', 'MicroVM', 'Densities', 'Replicas', 'SLOs']);
    expect(find(rows, 'host').values).toEqual([
      { main: 'm8i.4xlarge', sub: `16${NB}vCPU · 64${NB}GiB · nested` },
      { main: 'm8i.2xlarge', sub: `8${NB}vCPU · 32${NB}GiB · nested` },
    ]);
    expect(texts(find(rows, 'hypervisor'))).toEqual(['Firecracker', 'Firecracker']);
    expect(texts(find(rows, 'microvm'))).toEqual([`2${NB}vCPU · 2${NB}GiB`, `2${NB}vCPU · 2${NB}GiB`]);
    expect(texts(find(rows, 'densities'))).toEqual(['1, 2, 4, 8, 12, 16', '1–4, 6, 8']);
    expect(texts(find(rows, 'replicas'))).toEqual(['2 hosts per spec', '2 hosts per spec']);
    expect(texts(find(rows, 'slos'))).toEqual([EARLIER, EARLIER]);
    // A cell's sub is only where there is one.
    expect(find(rows, 'densities').values[0]).toEqual({ main: '1, 2, 4, 8, 12, 16' });
  });

  it('name the devices only where they differ between the specs', () => {
    const rows = tableRows(campaignRefs(hv), [2, 2, 2]);
    expect(shownKeys(rows)).toEqual(['host', 'hypervisor', 'devices', 'microvm', 'densities', 'replicas', 'slos']);
    expect(find(rows, 'hypervisor')).toMatchObject({ differs: true });
    expect(texts(find(rows, 'hypervisor'))).toEqual(['Firecracker', 'Cloud Hypervisor', 'Firecracker']);
    expect(find(rows, 'devices')).toMatchObject({ shown: true, differs: true });
    expect(texts(find(rows, 'devices'))).toEqual(['PCI, RNG device', 'PCI, RNG device', 'MMIO, no RNG device']);
    expect(find(rows, 'host')).toMatchObject({ differs: false });
    // One spec: nothing differs, and the devices are no row.
    const one = rowsOf([cap.specs[0]]);
    expect(one.every((r) => !r.differs)).toBe(true);
    expect(shownKeys(one)).toEqual(['host', 'hypervisor', 'microvm', 'densities', 'replicas', 'slos']);
  });

  it('add the support host as a last row only where asked (About shows the whole spec)', () => {
    const rows = tableRows([ref(cap, cap.specs[0])], [1], true);
    expect(shownKeys(rows)).toEqual(['host', 'hypervisor', 'microvm', 'densities', 'replicas', 'slos', 'support']);
    expect(rows.at(-1)).toMatchObject({ key: 'support', label: 'Support host', differs: false });
    expect(texts(rows.at(-1)!)).toEqual([cap.specs[0].spec.support_host.instance_type]);
  });

  it('add the guest, the Chromium flags and the warm start only where some spec sets them', () => {
    const base = cap.specs[0];
    // Writing the defaults is the same as leaving them out.
    const explicit = withSpec(base, { microvm: { ...base.spec.microvm, console: 'verbose', memory_pages: '4k' }, workload: { chromium_extra_flags: [] } }, 'explicit');
    expect(shownKeys(rowsOf([base, explicit]))).toEqual(['host', 'hypervisor', 'microvm', 'densities', 'replicas', 'slos']);
    expect(rowsOf([base, explicit]).some((r) => r.differs)).toBe(false);

    const quiet = withSpec(base, { microvm: { ...base.spec.microvm, console: 'quiet-i8042', memory_pages: 'thp' } }, 'quiet');
    const guest = find(rowsOf([base, quiet]), 'guest');
    expect(guest).toMatchObject({ label: 'Guest', shown: true, differs: true });
    expect(texts(guest)).toEqual(['default console · 4 KiB pages', 'quiet console, no keyboard probe · huge pages']);

    const lean = withSpec(base, { workload: { chromium_extra_flags: [LEAN] } }, 'lean');
    const flags = find(rowsOf([base, lean]), 'chromium');
    expect(flags).toMatchObject({ label: 'Chromium flags', shown: true, differs: true });
    expect(texts(flags)).toEqual(['none', LEAN]);
    // Both lean: the row stays, written once.
    expect(find(rowsOf([lean, withSpec(lean, {}, 'lean-2')]), 'chromium')).toMatchObject({ shown: true, differs: false });

    const warm = withSpec(base, { procedure: { ...base.spec.procedure, release_after_ready_s: 5 } }, 'warm');
    expect(find(rowsOf([base]), 'warm').shown).toBe(false);
    const w = find(rowsOf([base, warm]), 'warm');
    expect(w).toMatchObject({ label: 'Warm start', shown: true, differs: true });
    expect(texts(w)).toEqual(['no wait', '5 s wait after ready']);
  });

  it('give each campaign its own replicas on Compare, and each spec its own SLOs where they differ', () => {
    const refs = [...campaignRefs(syn).slice(1), ...campaignRefs(cap)];
    const rows = tableRows(refs, [syn.definition.replicas, cap.definition.replicas]);
    expect(find(rows, 'replicas')).toMatchObject({ differs: true });
    expect(texts(find(rows, 'replicas'))).toEqual(['2 hosts per spec', '1 host per spec']);
    expect(rows.filter((r) => r.differs).map((r) => r.key)).toEqual(['host', 'densities', 'replicas']);

    const standard = withSpec(cap.specs[0], { criteria: STANDARD_CRITERIA }, 'standard');
    const metal = withSpec(cap.specs[0], { criteria: { ...STANDARD_CRITERIA, ready_timeout_s: 900 } }, 'metal');
    const slos = find(rowsOf([standard, metal]), 'slos');
    expect(slos).toMatchObject({ differs: true });
    expect(texts(slos)).toEqual([STANDARD, 'ready ≤ 900 s (standard 180 s) · tasks 100% · step p50 ≤ 2 s · step p95 ≤ 3 s · task p95 ≤ 10 s']);
  });
});
