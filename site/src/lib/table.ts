// The "What we tested" table (SpecTable.svelte): one row per input, one column per spec, from the specs' documents.
// Pure functions over SpecRefs, so the tests can run them over the fixtures. A row's value is the text the reader
// sees: a row whose text is the same for every spec is written once, and a row no spec sets is left out. Nothing
// here recomputes a result.
import * as f from './format';
import { showsChromium, sloChips, type SpecRef } from './shape';
import { chromiumFlags, hypervisorName, mib } from './spec';
import type { Spec, SpecDoc } from './types';

export type RowKey = 'host' | 'hypervisor' | 'devices' | 'microvm' | 'guest' | 'chromium' | 'warm' | 'densities' | 'replicas' | 'slos' | 'support';

/** A number and its unit never break apart ("16 vCPU"), so a narrow column breaks only at the " · " separators. */
const nb = (s: string) => s.replaceAll(' ', ' ');

/** One spec's value in a row: the text, and a smaller line under it. */
export interface Cell {
  main: string;
  sub?: string;
}

export interface TableRow {
  key: RowKey;
  label: string;
  /** One cell per spec, in the specs' order. */
  values: Cell[];
  /** Whether the value differs between the specs: a cell per column when it does, one across the row when not. */
  differs: boolean;
  /** Whether the row is shown: a row no spec sets (no extra flags, no warm start) is left out. */
  shown: boolean;
}

/** "1, 4, 8, 16, 20, 22, 24–32": the densities in order, a run of three or more consecutive ones as a range. */
export function densitiesText(ds: number[]): string {
  const parts: string[] = [];
  for (let i = 0; i < ds.length; ) {
    let j = i;
    while (j + 1 < ds.length && ds[j + 1] === ds[j] + 1) j++;
    if (j - i >= 2) parts.push(`${f.num(ds[i])}–${f.num(ds[j])}`);
    else for (let k = i; k <= j; k++) parts.push(f.num(ds[k]));
    i = j + 1;
  }
  return parts.join(', ');
}

const CONSOLE: Record<NonNullable<Spec['microvm']['console']>, string> = {
  verbose: 'default console',
  quiet: 'quiet console',
  'quiet-i8042': 'quiet console, no keyboard probe',
};

/** "quiet console, no keyboard probe · huge pages"; a spec that leaves both out runs "default console · 4 KiB pages". */
export function guestText(m: Spec['microvm']): string {
  return `${CONSOLE[m.console ?? 'verbose']} · ${m.memory_pages === 'thp' ? 'huge pages' : '4 KiB pages'}`;
}

/** Whether a spec sets its guest to other than the defaults; writing the defaults is the same as leaving them out. */
const guestSet = (m: Spec['microvm']) => (m.console ?? 'verbose') !== 'verbose' || (m.memory_pages ?? '4k') !== '4k';

/**
 * "ready ≤ 180 s · tasks 100% · step p50 ≤ 1 s · step p95 ≤ 2 s · task p95 ≤ 5 s": the five SLOs in the order every
 * page shows them; a value other than the standard is followed by it, "step p50 ≤ 2 s (standard 1 s)". A time limit
 * (step, task) shows only where it differs from the standard, as sloChips gives it.
 */
export function slosText(c: Spec['criteria']): string {
  return sloChips(c, true)
    .map((x) => `${x.short.toLowerCase()} ${x.value}${x.standard ? ` (standard ${x.standard.replace(/^≤ /, '')})` : ''}`)
    .join(' · ');
}

/** "PCI, RNG device" or "MMIO, no RNG device": the virtio transport, and whether the microVM has a random-number device. */
export function devicesText(h: Spec['hypervisor']): string {
  return `${h.virtio_transport.toUpperCase()}, ${h.virtio_rng ? 'RNG device' : 'no RNG device'}`;
}

/** "5 s wait after ready" (release_after_ready_s); a spec without one starts the task at once: "no wait". */
export function warmText(p: Spec['procedure']): string {
  const s = p.release_after_ready_s ?? 0;
  return s > 0 ? `${f.num(s)} s wait after ready` : 'no wait';
}

/** "5 hosts per spec": the campaign's replicas, each run on its own host. */
export function replicasText(n: number): string {
  return `${f.num(n)} ${n === 1 ? 'host' : 'hosts'} per spec`;
}

/** The host: its instance type, and under it its size and kind, "16 vCPU · 32 GiB · nested". */
function hostCell(s: SpecDoc): Cell {
  return { main: s.host.instance_type, sub: `${nb(`${s.host.vcpus} vCPU`)} · ${nb(`${f.num(s.host.memory_gib)} GiB`)} · ${s.host.host_kind}` };
}

/**
 * The rows in the order the table shows them, each with a cell per spec and whether it is shown. `replicas[i]` is
 * the number of runs per spec in refs[i]'s campaign: the same for every spec of one campaign; on Compare, each
 * campaign's own. The devices are a row only where they differ; the guest, the Chromium flags and the warm start
 * only where some spec sets them; the support host only when asked (About, where the table is the whole spec).
 */
export function tableRows(refs: SpecRef[], replicas: number[], support = false): TableRow[] {
  const specs = refs.map((r) => r.spec);
  const row = (key: RowKey, label: string, values: Cell[], shown = true): TableRow => ({
    key,
    label,
    values,
    differs: new Set(values.map((v) => `${v.main}\n${v.sub ?? ''}`)).size > 1,
    shown,
  });
  const devices = row('devices', 'Devices', specs.map((s) => ({ main: devicesText(s.spec.hypervisor) })));
  return [
    row('host', 'Host', specs.map(hostCell)),
    row('hypervisor', 'Hypervisor', specs.map((s) => ({ main: hypervisorName(s.spec.hypervisor.name) }))),
    { ...devices, shown: devices.differs },
    row('microvm', 'MicroVM', specs.map((s) => ({ main: `${nb(`${s.spec.microvm.vcpus} vCPU`)} · ${nb(mib(s.spec.microvm.memory_mib))}` }))),
    row('guest', 'Guest', specs.map((s) => ({ main: guestText(s.spec.microvm) })), specs.some((s) => guestSet(s.spec.microvm))),
    row('chromium', 'Chromium flags', specs.map((s) => ({ main: chromiumFlags(s.spec).join(' ') || 'none' })), showsChromium(specs)),
    row(
      'warm',
      'Warm start',
      specs.map((s) => ({ main: warmText(s.spec.procedure) })),
      specs.some((s) => (s.spec.procedure.release_after_ready_s ?? 0) > 0),
    ),
    row('densities', 'Densities', specs.map((s) => ({ main: densitiesText(s.spec.densities) }))),
    row('replicas', 'Replicas', replicas.map((n) => ({ main: replicasText(n) }))),
    row('slos', 'SLOs', specs.map((s) => ({ main: slosText(s.spec.criteria) }))),
    row('support', 'Support host', specs.map((s) => ({ main: s.spec.support_host.instance_type })), support),
  ];
}
