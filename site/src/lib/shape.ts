// Shapes the dataset for the views. Pure functions over the documents in DATA.md, so the tests can run them over
// the fixtures. Nothing here recomputes a result: pass, fail and "tested successfully" come from the builder, and
// these functions only group, pick and label.
import * as f from './format';
import { SUBJECT_LABEL } from './glossary';
import { differingPaths, fieldFor, getPath, grouped, show, SPEC_PATHS, type TypedField } from './spec';
import type {
  CampaignDefinition,
  CampaignDoc,
  Cls,
  GuestGroup,
  HostConsumer,
  Range,
  RunDoc,
  RunEntry,
  RuleKey,
  SpecDoc,
  SpecOutcome,
  StepName,
  Subject,
  TrialDoc,
  TrialSummary,
  Verdict,
} from './types';
import { GUEST_GROUPS, HOST_CONSUMERS } from './types';

// ---- campaign: the specs as cards ---------------------------------------------------------------

export interface CardRow {
  key: string;
  label: string;
  value: string;
  /** This spec changes one of the row's inputs from the base. */
  changed: boolean;
  /** The base's values for the inputs this spec changes, labelled: "Instance type m8i.4xlarge". */
  base?: string;
  note?: string;
  cls?: Cls;
}

export interface SpecCard {
  name: string;
  label: string;
  /** No changes: this spec is the base. */
  isBase: boolean;
  rows: CardRow[];
  runs: RunEntry[];
}

const HYPERVISOR_NAME: Record<SpecDoc['hypervisor'], string> = {
  firecracker: 'Firecracker',
  'cloud-hypervisor': 'Cloud Hypervisor',
};

/** "2 GiB" for whole GiB, else "1,536 MiB". */
export function mib(v: number): string {
  return v % 1024 === 0 ? `${f.num(v / 1024)} GiB` : `${f.num(v)} MiB`;
}

/** The inputs a reader compares first, from the typed copies, then any other input a spec changes. */
export function specCards(c: CampaignDoc): SpecCard[] {
  const typed: { key: string; label: string; fields: TypedField[]; value: (s: SpecDoc, runs: RunEntry[]) => string; note?: (s: SpecDoc, runs: RunEntry[]) => string | undefined }[] = [
    {
      key: 'host',
      label: 'Worker host',
      fields: ['instance_type'],
      value: (s) => s.instance_type,
      note: (s, runs) => {
        const vcpus = [...new Set(runs.map((r) => r.host.vcpus))];
        return `${vcpus.length ? `${vcpus.join(' or ')} vCPUs, ` : ''}${s.host_kind}`;
      },
    },
    { key: 'hypervisor', label: 'Hypervisor', fields: ['hypervisor'], value: (s) => HYPERVISOR_NAME[s.hypervisor] },
    {
      key: 'microvm',
      label: 'Each microVM',
      fields: ['microvm.vcpus', 'microvm.mem_mib', 'microvm.mem_overhead_mib'],
      value: (s) => `${s.microvm.vcpus} vCPUs, ${mib(s.microvm.mem_mib)}`,
    },
    { key: 'densities', label: 'Densities to test', fields: ['densities'], value: (s) => s.densities.join(', ') },
    {
      key: 'criteria',
      label: 'Pass criteria',
      fields: ['criteria.step_p50_ms', 'criteria.step_p95_ms', 'criteria.task_p95_ms', 'criteria.ready_limit_s'],
      value: (s) =>
        `each step p50 ≤ ${f.num(s.criteria.step_p50_ms)} ms and p95 ≤ ${f.num(s.criteria.step_p95_ms)} ms; ` +
        `whole task p95 ≤ ${f.num(s.criteria.task_p95_ms)} ms`,
    },
  ];
  const covered = new Set(typed.flatMap((t) => t.fields.map((x) => SPEC_PATHS[x] as string)));

  const baseText = (paths: string[]) =>
    paths
      .map((p) => {
        const field = fieldFor(c.spec_fields, p);
        return `${field.label} ${show(getPath(c.definition.base, p), field.unit)}`;
      })
      .join('; ');

  return c.specs.map((s) => {
    const runs = c.runs.filter((r) => r.spec === s.name);
    const changedPaths = new Set(s.changes.map((x) => x.path));
    const rows: CardRow[] = typed.map((t) => {
      const paths = t.fields.map((x) => SPEC_PATHS[x] as string).filter((p) => changedPaths.has(p));
      return {
        key: t.key,
        label: t.label,
        value: t.value(s, runs),
        changed: paths.length > 0,
        base: paths.length ? baseText(paths) : undefined,
        note: t.note?.(s, runs),
      };
    });
    for (const ch of s.changes) {
      if (covered.has(ch.path)) continue;
      const field = fieldFor(c.spec_fields, ch.path);
      rows.push({
        key: ch.path,
        label: field.label,
        value: show(ch.value, field.unit),
        changed: true,
        base: baseText([ch.path]),
        note: field.group,
      });
    }
    rows.push({
      key: 'price',
      label: 'Price, assumed',
      value: `$${f.num(s.price_usd_per_hour, 5).replace(/0+$/, '').replace(/\.$/, '')} an hour`,
      changed: false,
      note: 'public on-demand price for the instance type',
      cls: 'assumed',
    });
    return { name: s.name, label: s.label, isBase: s.changes.length === 0, rows, runs };
  });
}

/** How many inputs are the same in every spec (every leaf of the base that no spec changes). */
export function sameEverywhere(c: CampaignDoc): string[] {
  const changed = new Set(differingPaths(c));
  return grouped(c, c.definition.base)
    .flatMap((g) => g.rows.map((r) => r.field.path))
    .filter((p) => !changed.has(p));
}

// ---- campaign: runs side by side, replicas grouped ----------------------------------------------

export interface ReplicaGroup {
  spec: SpecDoc;
  outcome: SpecOutcome;
  runs: RunEntry[];
  /** [min, max] across replicas; null when no replica has a value. */
  tested: Range | null;
  perHostVcpu: Range | null;
  costExecution: Range | null;
  costObserved: Range | null;
  /** The distinct first verdicts at each run's limit, most common first. */
  limits: { verdict: Verdict; runs: number }[];
  complete: number;
}

export function replicaGroups(c: CampaignDoc): ReplicaGroup[] {
  return c.specs.map((spec) => {
    const outcome = c.outcomes.find((o) => o.spec === spec.name)!;
    const runs = outcome.runs.map((id) => c.runs.find((r) => r.id === id)!).filter(Boolean);
    const costs = outcome.cost_per_1000_tasks.filter((x): x is { execution: Range; observed: Range } => x !== null);
    const joinRanges = (rs: Range[]): Range | null =>
      rs.length ? [Math.min(...rs.map((r) => r[0])), Math.max(...rs.map((r) => r[1]))] : null;
    const counts = new Map<Verdict, number>();
    for (const r of runs) {
      const v = r.result?.limit?.verdicts[0];
      if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    return {
      spec,
      outcome,
      runs,
      tested: f.spread(outcome.tested_successfully),
      perHostVcpu: f.spread(outcome.per_host_vcpu),
      costExecution: joinRanges(costs.map((x) => x.execution)),
      costObserved: joinRanges(costs.map((x) => x.observed)),
      limits: [...counts].map(([verdict, n]) => ({ verdict, runs: n })).sort((a, b) => b.runs - a.runs),
      complete: runs.filter((r) => r.status === 'complete').length,
    };
  });
}

// ---- campaign: latency against density ----------------------------------------------------------

export interface CapacityTrial {
  id: string;
  number: number;
  passed: boolean;
  taskP95: number | null;
  /** The slowest step's median in this trial, and which step it was. */
  stepP50: number | null;
  step: StepName | null;
}

export interface CapacityPoint {
  density: number;
  perHostVcpu: number;
  result: 'passed' | 'failed';
  trials: CapacityTrial[];
  /** The slowest trial at this density: a density passes only if every trial does, so the slowest one decides. */
  taskP95: number | null;
  stepP50: number | null;
  step: StepName | null;
}

export interface CapacityLine {
  run: string;
  spec: string;
  replica: number;
  /** The spec's position in the campaign, from 0: it picks the line's colour, so a run keeps its colour. */
  specIndex: number;
  hostVcpus: number;
  targets: { task_p95_ms: number; step_p50_ms: number };
  points: CapacityPoint[];
}

const value = (t: TrialSummary, subject: Subject, stat: 'p50' | 'p95') =>
  t.checks.find((c) => c.subject === subject && c.stat === stat)?.value_ms ?? null;

function maxOf<T>(items: T[], get: (x: T) => number | null): T | null {
  let best: T | null = null;
  let top = -Infinity;
  for (const x of items) {
    const v = get(x);
    if (v !== null && v > top) {
      top = v;
      best = x;
    }
  }
  return best;
}

/** One run's line: at each density it reached, the slowest trial's task p95 and slowest step median. */
export function capacityLine(run: RunDoc, spec: SpecDoc, specIndex: number): CapacityLine {
  const points: CapacityPoint[] = [];
  for (const d of run.by_density) {
    if (d.result === 'not_run') continue;
    const trials: CapacityTrial[] = run.trials
      .filter((t) => t.counts && t.density === d.density)
      .sort((a, b) => a.number! - b.number!)
      .map((t) => {
        const steps = t.checks.filter((c) => c.subject !== 'task' && c.stat === 'p50');
        const slow = maxOf(steps, (c) => c.value_ms);
        return {
          id: t.id,
          number: t.number!,
          passed: t.passed === true,
          taskP95: value(t, 'task', 'p95'),
          stepP50: slow?.value_ms ?? null,
          step: (slow?.subject as StepName | undefined) ?? null,
        };
      });
    const worstTask = maxOf(trials, (t) => t.taskP95);
    const worstStep = maxOf(trials, (t) => t.stepP50);
    points.push({
      density: d.density,
      perHostVcpu: d.density / run.host.vcpus,
      result: d.result,
      trials,
      taskP95: worstTask?.taskP95 ?? null,
      stepP50: worstStep?.stepP50 ?? null,
      step: worstStep?.step ?? null,
    });
  }
  return {
    run: run.id,
    spec: run.spec,
    replica: run.replica,
    specIndex,
    hostVcpus: run.host.vcpus,
    targets: { task_p95_ms: spec.criteria.task_p95_ms, step_p50_ms: spec.criteria.step_p50_ms },
    points,
  };
}

// ---- run: densities, what limited it, its spec, reproducing it -----------------------------------

export interface Miss {
  text: string;
  /** How many of the density's trials missed it. */
  missed: number;
  of: number;
}

export interface DensityRow {
  density: number;
  result: 'passed' | 'failed' | 'not_run';
  passed: number;
  trials: TrialSummary[];
  /** Every criterion some trial at this density missed, worded for the table. */
  misses: Miss[];
  verdicts: Verdict[];
  cpuPressure: Range | null;
  busyCores: Range | null;
  memUsed: Range | null;
  memAllocated: number;
  costExecution: Range | null;
}

export function densityRows(run: RunDoc): DensityRow[] {
  return run.by_density.map((d) => {
    const trials = run.trials.filter((t) => t.counts && t.density === d.density).sort((a, b) => a.number! - b.number!);
    const n = trials.length;
    const misses: Miss[] = [];
    const notReady = trials.filter((t) => !t.ready.met).length;
    if (notReady) misses.push({ text: 'not every microVM was ready in time', missed: notReady, of: n });
    const failedTasks = trials.filter((t) => t.tasks.ok < t.tasks.of).length;
    if (failedTasks) misses.push({ text: 'a task failed', missed: failedTasks, of: n });
    for (const ch of d.checks) {
      if (ch.met === n) continue;
      misses.push({
        text: `${SUBJECT_LABEL[ch.subject]} ${ch.stat} ${f.range(ch.range, (v) => f.vs(v, ch.target_ms))} ms against ${f.num(ch.target_ms)} ms`,
        missed: n - ch.met,
        of: n,
      });
    }
    const dirty = trials.filter((t) => !t.clean).length;
    if (dirty) misses.push({ text: 'something was left on the host', missed: dirty, of: n });
    return {
      density: d.density,
      result: d.result,
      passed: d.passed,
      trials,
      misses,
      verdicts: d.verdicts,
      cpuPressure: d.host?.cpu_pressure_pct ?? null,
      busyCores: d.host?.busy_cores ?? null,
      memUsed: d.host?.mem_used_gib ?? null,
      memAllocated: d.mem_allocated_gib,
      costExecution: d.cost_per_1000_tasks?.execution ?? null,
    };
  });
}

export interface RunAttribution {
  density: number;
  /** true: the density that failed; false: nothing failed, so this is the highest density tested. */
  atFailure: boolean;
  trials: number;
  verdicts: Verdict[];
  /** Host CPU-seconds by process, summed over the trials at this density. */
  host: { key: HostConsumer; seconds: number; share: number }[];
  hostBusy: number;
  /** Shares of busy guest CPU, averaged over the trials. */
  guest: { key: GuestGroup; share: number }[] | null;
  rules: { key: RuleKey; values: Range | null; fired: number }[];
}

/** What used the CPU at the density that limited the run (or, if none failed, the highest it reached). */
export function runAttribution(run: RunDoc): RunAttribution | null {
  const density = run.result?.first_failed ?? run.result?.tested_successfully ?? null;
  if (density === null) return null;
  const trials = run.trials.filter((t) => t.counts && t.density === density && t.attribution);
  if (!trials.length) return null;
  const sum = (k: HostConsumer) => trials.reduce((a, t) => a + t.attribution!.host_cpu_s[k], 0);
  const busy = trials.reduce((a, t) => a + t.attribution!.host_cpu_s.busy, 0);
  const total = HOST_CONSUMERS.reduce((a, k) => a + sum(k), 0);
  const withGuest = trials.filter((t) => t.attribution!.guest_share);
  const ruleKeys = [...new Set(trials.flatMap((t) => t.attribution!.rules.map((r) => r.key)))];
  return {
    density,
    atFailure: run.result?.first_failed === density,
    trials: trials.length,
    verdicts: run.by_density.find((d) => d.density === density)?.verdicts ?? [],
    host: HOST_CONSUMERS.map((key) => ({ key, seconds: sum(key), share: total ? sum(key) / total : 0 })),
    hostBusy: busy,
    guest: withGuest.length
      ? GUEST_GROUPS.map((key) => ({
          key,
          share: withGuest.reduce((a, t) => a + t.attribution!.guest_share![key], 0) / withGuest.length,
        }))
      : null,
    rules: ruleKeys.map((key) => {
      const rs = trials.map((t) => t.attribution!.rules.find((r) => r.key === key)!).filter(Boolean);
      return { key, values: f.spread(rs.map((r) => r.value)), fired: rs.filter((r) => r.fired).length };
    }),
  };
}

export interface SpecRow {
  path: string;
  label: string;
  unit?: string;
  note?: string;
  value: unknown;
  /** Set when this spec changes the input from the campaign's base. */
  base?: unknown;
  changed: boolean;
}

/** Every input of a spec, grouped for display, with the ones it changes from the base marked. */
export function specRows(c: CampaignDoc, s: SpecDoc): { group: string; rows: SpecRow[] }[] {
  const changes = new Map(s.changes.map((x) => [x.path, x]));
  return grouped(c, s.spec).map((g) => ({
    group: g.group,
    rows: g.rows.map(({ field, value }) => {
      const ch = changes.get(field.path);
      return {
        path: field.path,
        label: field.label,
        unit: field.unit,
        note: field.note,
        value,
        base: ch?.base,
        changed: Boolean(ch),
      };
    }),
  }));
}

/** What to paste into chat to run a spec again: the spec alone, or a campaign definition of one. */
export function reproduce(c: CampaignDoc, s: SpecDoc, replicas = 1): { spec: string; definition: string } {
  const definition: CampaignDefinition = {
    format: c.definition.format,
    id: `${s.name}-again`,
    question: `Does ${s.label} give the same result on another worker host? (First run in ${c.id}.)`,
    base: s.spec,
    specs: [{ name: s.name, label: s.label, changes: {} }],
    replicas,
  };
  return { spec: JSON.stringify(s.spec, null, 2), definition: JSON.stringify(definition, null, 2) };
}

// ---- trial: microVM lanes, the limiting resource over time --------------------------------------

export type LaneSegKind = 'boot' | 'step' | 'destroy';

export interface LaneSeg {
  kind: LaneSegKind;
  /** Boot: 1-4 in order. Step: its name. */
  phase?: number;
  step?: StepName;
  label: string;
  start: number;
  end: number;
  ok: boolean;
}

export interface Lane {
  index: number;
  product: string;
  outcome: TrialDoc['microvms'][number]['outcome'];
  ok: boolean | null;
  ready: number | null;
  segs: LaneSeg[];
  img: string | null;
}

export const BOOT_PHASES = [
  'hypervisor starting the microVM',
  'guest kernel booting',
  'guest daemon starting',
  'Chromium starting',
] as const;

/** Each microVM as a lane of segments on the trial's clock (ms from the moment all N were requested). */
export function lanes(doc: TrialDoc): { domain: Range; lanes: Lane[] } {
  const out = doc.microvms.map((m): Lane => {
    const segs: LaneSeg[] = [];
    if (m.boot) {
      const b = m.boot;
      const cuts = [b.process_started_ms, b.kernel_start_ms, b.guestd_start_ms, b.chromium_launch_ms, b.chromium_ready_ms];
      for (let i = 0; i < 4; i++) {
        if (cuts[i + 1] > cuts[i]) {
          segs.push({ kind: 'boot', phase: i + 1, label: BOOT_PHASES[i], start: cuts[i], end: cuts[i + 1], ok: true });
        }
      }
    } else if (m.ready_ms !== null) {
      segs.push({ kind: 'boot', phase: 2, label: 'starting (phases not recorded)', start: 0, end: m.ready_ms, ok: true });
    }
    for (const st of m.steps) {
      segs.push({ kind: 'step', step: st.name, label: SUBJECT_LABEL[st.name], start: st.start_ms, end: st.end_ms, ok: st.ok });
    }
    if (m.destroy) {
      segs.push({ kind: 'destroy', label: 'destroyed', start: m.destroy.start_ms, end: m.destroy.end_ms, ok: true });
    }
    return {
      index: m.index,
      product: m.product,
      outcome: m.outcome,
      ok: m.task ? m.task.ok : null,
      ready: m.ready_ms,
      segs,
      img: m.img ?? null,
    };
  });
  const ends = out.flatMap((l) => l.segs.map((s) => s.end));
  const end = Math.max(doc.marks.clean_ms ?? 0, doc.marks.last_return_ms ?? 0, ...ends, 1);
  return { domain: [0, end], lanes: out };
}

export interface SeriesPanel {
  key: string;
  title: string;
  unit: '%' | '' | 'GiB';
  t: number[][];
  series: { label: string; values: number[] }[];
  /** Several lines of one kind (one per microVM), drawn in one colour. */
  many: boolean;
  threshold?: { value: number; op: '>=' | '<'; fired: boolean | null };
}

const HOST_SERIES: Partial<Record<RuleKey, keyof TrialDoc['series']['host']>> = {
  host_cpu_pressure_pct: 'cpu_pressure_pct',
  host_cpu_util_pct: 'cpu_util_pct',
  host_mem_pressure_pct: 'mem_pressure_pct',
  host_io_pressure_pct: 'io_pressure_pct',
  host_steal_pct: 'steal_pct',
};

const RULE_TITLE: Record<RuleKey, string> = {
  host_cpu_pressure_pct: 'Host CPU pressure',
  host_cpu_util_pct: 'Host CPU utilisation',
  microvm_throttled_fraction: 'Throttled fraction, each microVM',
  host_mem_available_fraction: 'Host memory used',
  host_mem_pressure_pct: 'Host memory pressure',
  host_io_pressure_pct: 'Host IO pressure',
  host_steal_pct: 'Steal',
};

/**
 * The series behind the trial's verdict, each with its rule's threshold. When no rule fired (or a signal wasn't
 * recorded), the host CPU series, which limited every run so far.
 */
export function limitPanels(doc: TrialDoc, summary: TrialSummary, spec: SpecDoc): SeriesPanel[] {
  const verdict = summary.attribution?.verdicts[0] ?? doc.limit.verdicts[0] ?? 'unknown';
  const lookFor: Verdict = verdict === 'none' || verdict === 'unknown' ? 'host_cpu' : verdict;
  const rules = spec.rules.filter((r) => r.verdict === lookFor);
  const fired = (k: RuleKey) => summary.attribution?.rules.find((r) => r.key === k)?.fired ?? null;
  const panels: SeriesPanel[] = [];
  const h = doc.series.host;
  for (const rule of rules) {
    const threshold = { value: rule.threshold, op: rule.op, fired: fired(rule.key) };
    const hostKey = HOST_SERIES[rule.key];
    if (hostKey) {
      const values = h[hostKey] as number[] | undefined;
      if (!values) continue;
      panels.push({ key: rule.key, title: RULE_TITLE[rule.key], unit: '%', t: [h.t_ms], series: [{ label: RULE_TITLE[rule.key], values }], many: false, threshold });
    } else if (rule.key === 'microvm_throttled_fraction' && doc.series.microvms?.length) {
      const ms = doc.series.microvms;
      panels.push({
        key: rule.key,
        title: RULE_TITLE[rule.key],
        unit: '',
        t: ms.map((m) => m.t_ms),
        series: ms.map((m) => ({ label: `microVM ${m.index}`, values: m.throttled_fraction })),
        many: true,
        threshold,
      });
    } else if (rule.key === 'host_mem_available_fraction') {
      panels.push({ key: rule.key, title: RULE_TITLE[rule.key], unit: 'GiB', t: [h.t_ms], series: [{ label: 'used', values: h.mem_used_gib }], many: false });
    }
  }
  return panels;
}

/** Host CPU by process as stacked cores, if the run recorded per-process counters. */
export function coresStack(doc: TrialDoc): { t: number[]; layers: { key: HostConsumer; values: number[] }[] } | null {
  const cores = doc.series.host.cores;
  if (!cores) return null;
  return { t: doc.series.host.t_ms, layers: HOST_CONSUMERS.map((key) => ({ key, values: cores[key] })) };
}
