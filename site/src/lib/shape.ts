// Shapes the dataset for the views. Pure functions over the documents in DATA.md, so the tests can run them over
// the fixtures. Nothing here recomputes a result: pass, fail and the result come from the builder; these only
// group, pick and label (and compute D60 midpoints for specs from different campaigns, by the same rule).
import * as f from './format';
import { meanMidpoint } from './derive';
import { STOPPED_EARLY, SUBJECT_LABEL, VERDICT_SHORT } from './glossary';
import { href } from './router';
import { differing, flatten, field, hypervisorName, mib, show } from './spec';
import type {
  CampaignDoc,
  CampaignEntry,
  GuestGroup,
  HostConsumer,
  Range,
  ReplicaResult,
  RuleDef,
  RuleKey,
  RunDoc,
  RunEntry,
  Spec,
  SpecDoc,
  StepName,
  TrialDoc,
  TrialSummary,
  Verdict,
} from './types';
import { GUEST_GROUPS, HOST_CONSUMERS } from './types';

export interface SpecRef {
  campaign: string;
  campaignTitle: string;
  spec: SpecDoc;
  /** What the pages call the spec: the input that sets it apart in its campaign ("Cloud Hypervisor", "m8i.2xlarge"). */
  groupLabel: string;
}

/** Refs for a campaign's own specs, each labelled by what sets it apart. */
export function campaignRefs(c: CampaignDoc): SpecRef[] {
  const labels = specLabels(c.specs);
  return c.specs.map((spec, i) => ({ campaign: c.id, campaignTitle: c.title, spec, groupLabel: labels[i] }));
}

const devices = (h: Spec['hypervisor']) => `${h.virtio_transport.toUpperCase()}${h.virtio_rng ? ' + RNG' : ''}`;

/**
 * Display names for specs, from the inputs that differ between them: the instance type, the hypervisor, the microVM
 * size, then the devices to break a tie ("Firecracker MMIO"). One spec, or specs that differ in nothing named here,
 * get "m8i.4xlarge · Firecracker"; the builder's label is the last resort. Slugs stay in URLs.
 */
export function specLabels(specs: SpecDoc[]): string[] {
  const host = (s: SpecDoc) => s.host.instance_type;
  const hv = (s: SpecDoc) => hypervisorName(s.spec.hypervisor.name);
  const vm = (s: SpecDoc) => `${s.spec.microvm.vcpus} vCPU · ${mib(s.spec.microvm.memory_mib)} microVM`;
  const parts = [host, hv, vm].filter((p) => new Set(specs.map(p)).size > 1);
  const first = specs.map((s) => parts.map((p) => p(s)).join(' · '));
  const tied = first.map((l, i) => {
    const same = specs.filter((_, j) => first[j] === l);
    if (same.length < 2 || new Set(same.map((s) => devices(s.spec.hypervisor))).size < 2) return l;
    const d = devices(specs[i].spec.hypervisor);
    return `${l || hv(specs[i])} ${d}`;
  });
  const named = tied.map((l, i) => l || `${host(specs[i])} · ${hv(specs[i])}`);
  return named.map((l, i) => (named.filter((x) => x === l).length > 1 ? specs[i].label || specs[i].name : l));
}

// ---- what we tested: one card per spec ------------------------------------------------------------------

export type CardRow = 'host' | 'hypervisor' | 'microvm' | 'densities';

export interface SpecCard {
  host: { value: string; sub: string };
  /** The devices are named only when they differ between the specs shown. */
  hypervisor: { value: string; sub: string | null };
  microvm: string;
  densities: number[];
}

/** A spec's inputs as the card shows them: the host, the hypervisor, the microVM, the densities. */
export function specCard(s: SpecDoc, showDevices = false): SpecCard {
  const h = s.spec.hypervisor;
  return {
    host: { value: s.host.instance_type, sub: `${s.host.vcpus} vCPU · ${f.num(s.host.memory_gib)} GiB · ${s.host.host_kind}` },
    hypervisor: { value: hypervisorName(h.name), sub: showDevices ? `${devices(h)} devices` : null },
    microvm: `${s.spec.microvm.vcpus} vCPU · ${mib(s.spec.microvm.memory_mib)}`,
    densities: s.spec.densities,
  };
}

const ROW_OF: [string, CardRow][] = [
  ['worker_host.', 'host'],
  ['hypervisor.', 'hypervisor'],
  ['microvm.', 'microvm'],
  ['densities', 'densities'],
];

/** The card rows whose inputs differ between the specs: what the cards highlight. */
export function differingRows(specs: SpecDoc[]): Set<CardRow> {
  const out = new Set<CardRow>();
  if (specs.length < 2) return out;
  for (const path of differing(specs.map((s) => s.spec))) {
    const row = ROW_OF.find(([prefix]) => path === prefix || path.startsWith(prefix));
    if (row) out.add(row[1]);
  }
  return out;
}

// ---- success criteria ------------------------------------------------------------------------------------

/** Whether every spec is judged by the same criteria. */
export function sameCriteria(specs: SpecDoc[]): boolean {
  return specs.every((s) => JSON.stringify(s.spec.criteria) === JSON.stringify(specs[0].spec.criteria));
}

// ---- how it performed: a headline per spec ---------------------------------------------------------------

export interface SpecResult {
  ref: SpecRef;
  runs: RunEntry[];
  replicas: ReplicaResult[];
  midpoint: number | null;
  /** Execution cost per 1,000 tasks at each replica's result, joined: [min, max]. */
  cost: Range | null;
  /** The distinct verdicts at each replica's first failure, most common first. */
  limits: { verdict: Verdict; runs: number; density: number }[];
}

export function specResults(refs: SpecRef[], docs: Map<string, CampaignDoc>): SpecResult[] {
  return refs.map((ref) => {
    const c = docs.get(ref.campaign)!;
    const replicas = c.outcomes.find((o) => o.spec === ref.spec.name)?.replicas ?? [];
    const runs = c.runs.filter((r) => r.spec === ref.spec.name);
    const costs = replicas.flatMap((r) => (r.cost_per_1000_tasks ? [r.cost_per_1000_tasks.execution] : []));
    const counts = new Map<Verdict, { runs: number; density: number }>();
    for (const run of runs) {
      const l = run.result.limit;
      const v = l?.verdicts[0];
      if (!l || !v) continue;
      const had = counts.get(v);
      counts.set(v, { runs: (had?.runs ?? 0) + 1, density: Math.min(had?.density ?? Infinity, l.density) });
    }
    return {
      ref,
      runs,
      replicas,
      midpoint: meanMidpoint(replicas.map((r) => r.midpoint_per_host_vcpu)),
      cost: costs.length ? [Math.min(...costs.map((x) => x[0])), Math.max(...costs.map((x) => x[1]))] : null,
      limits: [...counts].map(([verdict, x]) => ({ verdict, ...x })).sort((a, b) => b.runs - a.runs),
    };
  });
}

/** One run's result shaped as a spec's with a single replica, for the run page's headline. */
export function runResult(c: CampaignDoc, runId: string): SpecResult | null {
  const run = c.runs.find((r) => r.id === runId);
  const spec = run && c.specs.find((s) => s.name === run.spec);
  if (!run || !spec) return null;
  const one: CampaignDoc = {
    ...c,
    runs: [run],
    outcomes: c.outcomes.map((o) => ({ ...o, replicas: o.replicas.filter((x) => x.run === runId) })),
  };
  return specResults([{ campaign: c.id, campaignTitle: c.title, spec, groupLabel: spec.name }], new Map([[c.id, one]]))[0];
}

/** "8", "3–4", or null when no replica passed a density. */
function spread(values: (number | null)[], fmt: (v: number) => string): string | null {
  const v = values.filter((x): x is number => x !== null);
  if (!v.length) return null;
  return f.range([Math.min(...v), Math.max(...v)], fmt);
}

export interface Headline {
  /** The highest density that met every criterion: "8", "3–4" across replicas, "≥ 9" with no failure. */
  density: string;
  /** Under the density: "stopped early", "none passed", "not run yet". */
  densityNote: string | null;
  perVcpu: string | null;
  cost: string | null;
  /** What ran out: "Host CPU", or a label ("not reached"). */
  ranOut: string;
  ranOutNote: string | null;
  /** The density where it ran out, for a link to it. */
  ranOutDensity: number | null;
}

/** A spec's result in four figures (D52): density, per host vCPU, cost per 1,000 tasks, what ran out. */
export function headline(r: SpecResult): Headline {
  const reps = r.replicas;
  if (!reps.length) {
    return { density: '–', densityNote: 'not run yet', perVcpu: null, cost: null, ranOut: '–', ranOutNote: null, ranOutDensity: null };
  }
  const passed = spread(reps.map((x) => x.tested_successfully), (v) => f.num(v));
  const noFailure = reps.every((x) => x.first_failed === null);
  const early = reps.filter((x) => x.stopped_early).length;
  const top = r.limits[0];
  return {
    density: passed === null ? '0' : noFailure ? `≥ ${passed}` : passed,
    densityNote:
      passed === null
        ? 'none passed'
        : early
          ? reps.length > 1
            ? `${early} of ${reps.length} replicas ${STOPPED_EARLY}`
            : STOPPED_EARLY
          : null,
    perVcpu: spread(reps.map((x) => x.per_host_vcpu), f.ratio),
    cost: r.cost ? f.usdRange(r.cost) : null,
    ranOut: top ? VERDICT_SHORT[top.verdict] : noFailure ? 'not reached' : VERDICT_SHORT.unknown,
    ranOutNote: top ? `at ${top.density}${reps.length > 1 && top.runs < reps.length ? ` · ${top.runs} of ${reps.length} replicas` : ''}` : null,
    ranOutDensity: top ? top.density : null,
  };
}

/** The headline a campaign is chosen by: its best spec's density and density per host vCPU. */
export function campaignHeadline(e: CampaignEntry): {
  /** "8", "3–4"; null with a label instead. */
  density: string | null;
  label: string;
  perVcpu: string | null;
} {
  const score = (o: CampaignEntry['outcomes'][number]) => {
    const per = o.replicas.map((r) => r.per_host_vcpu).filter((x): x is number => x !== null);
    return o.midpoint_per_host_vcpu ?? (per.length ? Math.min(...per) : -1);
  };
  const best = [...e.outcomes].sort((a, b) => score(b) - score(a))[0];
  if (!best || !best.replicas.length) return { density: null, label: 'not run yet', perVcpu: null };
  const passed = spread(best.replicas.map((r) => r.tested_successfully), (v) => f.num(v));
  return {
    density: passed,
    label: passed === null ? 'none passed' : passed === '1' ? 'microVM' : 'microVMs',
    perVcpu: spread(best.replicas.map((r) => r.per_host_vcpu), f.ratio),
  };
}

export interface CompareRow {
  key: string;
  label: string;
  campaign: string;
  campaignTitle: string;
  href: string | null;
  /** The highest density that met every SLO: "10", "8–9" across replicas, "≥ 9" with no failure. */
  density: string;
  /** That density per host vCPU, across replicas: "0.50", "0.38–0.50". */
  perVcpu: string | null;
  /**
   * D60: the mean of the replicas' midpoints per host vCPU. When some replicas have no interval (stopped early, no
   * failure yet) it is the mean of those that do, and `partial` says how many are missing.
   */
  midpoint: number | null;
  partial: string | null;
  /** The distance from the highest midpoint shown; null for the highest. */
  delta: number | null;
  /** Why there is no midpoint at all, where it would be (D62). */
  missing: string | null;
  cost: string | null;
  ranOut: string;
  ranOutAt: string | null;
}

/** The comparison across specs (D60) in one table: each spec's density, per vCPU, cost and what ran out. */
export function compareRows(results: SpecResult[]): CompareRow[] {
  const mids = results.map((r) => {
    if (r.midpoint !== null) return { midpoint: r.midpoint, partial: null };
    const have = r.replicas.filter((x) => x.midpoint_per_host_vcpu !== null);
    if (!have.length) return { midpoint: null, partial: null };
    const without = r.replicas.filter((x) => x.midpoint_per_host_vcpu === null);
    const why = without.every((x) => x.stopped_early) ? STOPPED_EARLY : 'no failure yet';
    return {
      midpoint: meanMidpoint(have.map((x) => x.midpoint_per_host_vcpu)),
      partial: `${without.length} ${without.length === 1 ? 'replica' : 'replicas'} ${why}`,
    };
  });
  const top = Math.max(-Infinity, ...mids.map((m) => m.midpoint ?? -Infinity));
  return results.map((r, i) => {
    const h = headline(r);
    const { midpoint, partial } = mids[i];
    const noFailure = r.replicas.filter((x) => x.first_failed === null);
    return {
      key: `${r.ref.campaign}/${r.ref.spec.name}`,
      label: r.ref.groupLabel,
      campaign: r.ref.campaign,
      campaignTitle: r.ref.campaignTitle,
      href: r.runs.length === 1 ? href({ name: 'run', campaign: r.ref.campaign, run: r.runs[0].id, density: null }) : null,
      density: h.density,
      perVcpu: h.perVcpu,
      midpoint,
      partial,
      delta: midpoint === null || !Number.isFinite(top) || midpoint === top ? null : midpoint - top,
      missing:
        midpoint !== null
          ? null
          : !r.replicas.length
            ? 'not run yet'
            : noFailure.some((x) => x.stopped_early)
              ? STOPPED_EARLY
              : 'no failure yet',
      cost: h.cost,
      ranOut: h.ranOut,
      ranOutAt: h.ranOutNote,
    };
  });
}

/** The D60 comparison in one line: specs with a midpoint, highest first, then those without one. */
export function comparisonLine(rows: CompareRow[]): CompareRow[] {
  const withMid = rows.filter((r) => r.midpoint !== null).sort((a, b) => b.midpoint! - a.midpoint!);
  return [...withMid, ...rows.filter((r) => r.midpoint === null)];
}

// ---- the result chart: one row per run, a mark per trial ------------------------------------------------

/** The most a trial's closest SLO is drawn at, as a share of its limit: 2 is twice the limit. */
export const RATIO_CAP = 2;

/**
 * How close a trial came to failing: its worst criterion as a share of that criterion's limit (each step's p50
 * and p95, the whole task's p95, the time to ready). Over 1 fails. A failed task, a browser never ready or a host
 * not clean counts as the cap. Only for drawing: pass and fail come from the dataset.
 */
export function closestSlo(t: TrialSummary): { ratio: number; what: string } {
  let best = { ratio: 0, what: 'time to ready' };
  const consider = (ratio: number, what: string) => {
    if (ratio > best.ratio) best = { ratio, what };
  };
  if (t.ready.all_ready_ms === null) consider(RATIO_CAP, 'time to ready');
  else consider(t.ready.all_ready_ms / t.ready.limit_ms, 'time to ready');
  for (const c of t.checks) consider(c.value_ms / c.target_ms, `${SUBJECT_LABEL[c.subject]} ${c.stat}`);
  if (t.tasks.ok < t.tasks.of) consider(RATIO_CAP, 'a failed task');
  if (!t.clean) consider(RATIO_CAP, 'host not clean');
  return { ratio: Math.min(RATIO_CAP, best.ratio), what: best.what };
}

export interface Mark {
  density: number;
  x: number;
  /** Stacked position among the trials at this density, from 0. */
  k: number;
  passed: boolean;
  /** The trial's closest SLO as a share of its limit, when the run's trials are loaded. */
  ratio: number | null;
  title: string;
  href: string;
}

export interface ChartRow {
  key: string;
  campaign: string;
  campaignTitle: string;
  /** The group the row belongs to (a spec), and whether it's the group's first row. */
  group: string;
  groupLabel: string;
  first: boolean;
  replica: number;
  runId: string;
  runHref: string | null;
  hostVcpus: number;
  marks: Mark[];
  notTested: { density: number; x: number }[];
  /** From the last density that passed to the first that failed, on the chart's x (D60's interval). */
  band: { from: number; to: number; gap: Range | null } | null;
  /** A label where data is deliberately missing: "stopped early", "not run yet". */
  note: string | null;
}

/**
 * Rows for the result chart: every run of each spec, replicas together, including runs not published yet. With the
 * runs' documents, each mark also carries its trial's closest SLO.
 */
export function chartRows(refs: SpecRef[], docs: Map<string, CampaignDoc>, runDocs: Map<string, RunDoc> = new Map()): ChartRow[] {
  const rows: ChartRow[] = [];
  for (const ref of refs) {
    const c = docs.get(ref.campaign)!;
    const runs = c.runs.filter((r) => r.spec === ref.spec.name);
    const n = ref.spec.host.vcpus;
    for (let replica = 1; replica <= c.definition.replicas; replica++) {
      const run = runs.find((r) => r.replica === replica);
      const id = run?.id ?? `${ref.spec.name}-r${replica}`;
      const base = {
        key: `${c.id}/${id}`,
        campaign: c.id,
        campaignTitle: ref.campaignTitle,
        group: `${c.id}/${ref.spec.name}`,
        groupLabel: ref.groupLabel,
        first: replica === 1,
        replica,
        runId: id,
      };
      if (!run) {
        rows.push({ ...base, runHref: null, hostVcpus: n, marks: [], notTested: [], band: null, note: 'not run yet' });
        continue;
      }
      const doc = runDocs.get(`${c.id}/${run.id}`);
      const v = run.host.vcpus;
      const marks: Mark[] = [];
      const notTested: ChartRow['notTested'] = [];
      for (const b of run.by_density) {
        const x = b.density / v;
        if (b.result === 'not_tested') {
          notTested.push({ density: b.density, x });
          continue;
        }
        b.trial_results.forEach((passed, k) => {
          const id = `d${b.density}-t${k + 1}`;
          const t = doc?.trials.find((x) => x.id === id);
          const near = t ? closestSlo(t) : null;
          const how = near ? `, ${near.what} at ${f.num(near.ratio * 100)}% of its limit` : '';
          marks.push({
            density: b.density,
            x,
            k,
            passed,
            ratio: near ? near.ratio : null,
            title: `trial ${k + 1} at density ${b.density}: ${passed ? 'passed' : 'failed'}${how}`,
            href: href({ name: 'trial', campaign: c.id, run: run.id, trial: id, microvm: null }),
          });
        });
      }
      const res = run.result;
      const stopped = run.stopped_early || (res.first_failed === null && notTested.length > 0 && marks.length > 0);
      rows.push({
        ...base,
        runHref: href({ name: 'run', campaign: c.id, run: run.id, density: null }),
        hostVcpus: v,
        marks,
        notTested,
        band:
          res.first_failed === null
            ? null
            : { from: (res.tested_successfully ?? 0) / v, to: res.first_failed / v, gap: res.gap },
        note: stopped ? STOPPED_EARLY : null,
      });
    }
  }
  return rows;
}

/** What a run page is called: its spec as the campaign labels it, and its replica when there are several. */
export function runTitle(c: CampaignDoc, runId: string): string {
  const run = c.runs.find((r) => r.id === runId);
  const ref = run && campaignRefs(c).find((r) => r.spec.name === run.spec);
  if (!run || !ref) return runId;
  return c.definition.replicas > 1 ? `${ref.groupLabel} · replica ${run.replica}` : ref.groupLabel;
}

// ---- a spec's every input (the run page) ----------------------------------------------------------------

/** The site's words for a few schema labels, so the full spec reads like the rest of the pages. */
const SECTION_WORDS: Record<string, string> = { 'Pass criteria': 'Success criteria' };
const LABEL_WORDS: Record<string, string> = {
  'Worker instance type': 'Instance type',
  'Support instance type': 'Instance type',
  'Step median target': 'Each step p50',
  'Step p95 target': 'Each step p95',
  'Task p95 target': 'Whole task p95',
};

/** Every input of a spec, grouped by section in the schema's order; `changed` marks paths that differ from a base. */
export function specSections(spec: Spec, changed: { path: string; base: unknown }[] = []): {
  group: string;
  rows: { path: string; label: string; value: string; base?: string }[];
}[] {
  const out: ReturnType<typeof specSections> = [];
  for (const [path, value] of Object.entries(flatten(spec))) {
    const fd = field(path);
    const group = SECTION_WORDS[fd.group] ?? fd.group;
    let g = out.find((x) => x.group === group);
    if (!g) out.push((g = { group, rows: [] }));
    const ch = changed.find((c) => c.path === path);
    g.rows.push({ path, label: LABEL_WORDS[fd.label] ?? fd.label, value: show(path, value), base: ch ? show(path, ch.base) : undefined });
  }
  // Labels don't restate their section: "MicroVM memory" under MicroVM is "Memory", the hypervisor's own name is
  // "Name". A section of one row named as the section keeps the label; the page shows it once.
  for (const g of out) {
    if (g.rows.length < 2) continue;
    for (const r of g.rows) {
      if (r.label === g.group) r.label = 'Name';
      else if (r.label.startsWith(`${g.group} `)) {
        const rest = r.label.slice(g.group.length + 1);
        r.label = /^[a-z][a-z]/.test(rest) ? rest.charAt(0).toUpperCase() + rest.slice(1) : rest;
      }
    }
  }
  return out;
}

// ---- run: densities, what limited it --------------------------------------------------------------------

export interface DensityRow {
  density: number;
  result: 'passed' | 'failed' | 'not_tested';
  passed: number;
  trials: TrialSummary[];
  /** Every criterion some trial at this density missed: what ("home p50"), the trials' values, in how many trials. */
  misses: { text: string; value: string | null; missed: number; of: number }[];
  verdicts: Verdict[];
  cpuPressure: Range | null;
  memUsed: Range | null;
  memAllocated: number;
  costExecution: Range | null;
}

export function densityRows(run: RunDoc): DensityRow[] {
  return run.by_density.map((d) => {
    const trials = run.trials.filter((t) => t.counts && t.density === d.density).sort((a, b) => a.number! - b.number!);
    const n = trials.length;
    const misses: DensityRow['misses'] = [];
    const notReady = trials.filter((t) => !t.ready.met).length;
    if (notReady) misses.push({ text: 'not all ready', value: null, missed: notReady, of: n });
    const failedTasks = trials.filter((t) => t.tasks.ok < t.tasks.of).length;
    if (failedTasks) misses.push({ text: 'a task failed', value: null, missed: failedTasks, of: n });
    for (const ch of d.checks) {
      if (ch.met === n) continue;
      misses.push({
        text: `${SUBJECT_LABEL[ch.subject]} ${ch.stat}`,
        value: `${f.range(ch.range, (v) => f.vs(v, ch.target_ms))} ms`,
        missed: n - ch.met,
        of: n,
      });
    }
    const dirty = trials.filter((t) => !t.clean).length;
    if (dirty) misses.push({ text: 'host not clean', value: null, missed: dirty, of: n });
    return {
      density: d.density,
      result: d.result,
      passed: d.passed,
      trials,
      misses,
      verdicts: d.verdicts,
      cpuPressure: d.host?.cpu_pressure_pct ?? null,
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
  /** Shares of busy guest CPU, averaged over the trials. */
  guest: { key: GuestGroup; share: number }[] | null;
  rules: { key: RuleKey; values: Range | null; fired: number }[];
}

/** What used the CPU at the density that limited the run (or, if none failed, the highest it passed). */
export function runAttribution(run: RunDoc): RunAttribution | null {
  const density = run.result.first_failed ?? run.result.tested_successfully ?? null;
  if (density === null) return null;
  const trials = run.trials.filter((t) => t.counts && t.density === density && t.attribution);
  if (!trials.length) return null;
  const sum = (k: HostConsumer) => trials.reduce((a, t) => a + t.attribution!.host_cpu_s[k], 0);
  const total = HOST_CONSUMERS.reduce((a, k) => a + sum(k), 0);
  const withGuest = trials.filter((t) => t.attribution!.guest_share);
  const ruleKeys = [...new Set(trials.flatMap((t) => t.attribution!.rules.map((r) => r.key)))];
  const spread = (vs: (number | null)[]): Range | null => {
    const v = vs.filter((x): x is number => x !== null);
    return v.length ? [Math.min(...v), Math.max(...v)] : null;
  };
  return {
    density,
    atFailure: run.result.first_failed === density,
    trials: trials.length,
    verdicts: run.by_density.find((d) => d.density === density)?.verdicts ?? [],
    host: HOST_CONSUMERS.map((key) => ({ key, seconds: sum(key), share: total ? sum(key) / total : 0 })),
    guest: withGuest.length
      ? GUEST_GROUPS.map((key) => ({
          key,
          share: withGuest.reduce((a, t) => a + t.attribution!.guest_share![key], 0) / withGuest.length,
        }))
      : null,
    rules: ruleKeys.map((key) => {
      const rs = trials.map((t) => t.attribution!.rules.find((r) => r.key === key)!).filter(Boolean);
      return { key, values: spread(rs.map((r) => r.value)), fired: rs.filter((r) => r.fired).length };
    }),
  };
}

export interface LimitBar {
  key: RuleKey;
  op: '>=' | '<';
  threshold: number;
  /** Each density shown (the last that passed, then the first that failed): its trials' values, low to high. */
  at: { density: number; values: Range }[];
}

/**
 * What limited a run, as bars: each rule that fired at the density that failed, with its values there and at the
 * last density that passed, against its threshold. The rules that didn't fire, with their values at the failure.
 */
export function limitBars(run: RunDoc, rules: RuleDef[]): { fired: LimitBar[]; others: { key: RuleKey; op: '>=' | '<'; threshold: number; values: Range | null }[] } | null {
  const failed = run.result.first_failed;
  if (failed === null) return null;
  const densities = [run.result.tested_successfully, failed].filter((d): d is number => d !== null);
  const trialsAt = (d: number) => run.trials.filter((t) => t.counts && t.density === d && t.attribution);
  const at = trialsAt(failed);
  if (!at.length) return null;
  const values = (d: number, key: RuleKey): Range | null => {
    const v = trialsAt(d)
      .map((t) => t.attribution!.rules.find((r) => r.key === key)?.value)
      .filter((x): x is number => typeof x === 'number');
    return v.length ? [Math.min(...v), Math.max(...v)] : null;
  };
  const fired: LimitBar[] = [];
  const others: { key: RuleKey; op: '>=' | '<'; threshold: number; values: Range | null }[] = [];
  for (const def of rules) {
    const didFire = at.some((t) => t.attribution!.rules.find((r) => r.key === def.key)?.fired);
    if (didFire) {
      fired.push({
        key: def.key,
        op: def.op,
        threshold: def.threshold,
        at: densities.flatMap((d) => {
          const v = values(d, def.key);
          return v ? [{ density: d, values: v }] : [];
        }),
      });
    } else {
      others.push({ key: def.key, op: def.op, threshold: def.threshold, values: values(failed, def.key) });
    }
  }
  return { fired, others };
}

/** A trial against the five SLOs, in the badges' order: what it measured and whether it met each. */
export function trialSlos(t: TrialSummary): { value: string; met: boolean; note?: string }[] {
  const worst = (pred: (c: TrialSummary['checks'][number]) => boolean) =>
    t.checks.filter(pred).sort((a, b) => b.value_ms / b.target_ms - a.value_ms / a.target_ms)[0];
  const step = (stat: 'p50' | 'p95') => {
    const c = worst((x) => x.subject !== 'task' && x.stat === stat);
    return c
      ? { value: `${f.vs(c.value_ms, c.target_ms)} ms`, met: t.checks.filter((x) => x.subject !== 'task' && x.stat === stat).every((x) => x.met), note: SUBJECT_LABEL[c.subject] }
      : { value: '–', met: true };
  };
  const task = worst((x) => x.subject === 'task' && x.stat === 'p95');
  return [
    {
      value: t.ready.all_ready_ms !== null ? f.seconds(t.ready.all_ready_ms, 1) : `${t.ready.microvms} of ${t.density}`,
      met: t.ready.met,
    },
    { value: `${t.tasks.ok} of ${t.tasks.of}`, met: t.tasks.ok === t.tasks.of },
    step('p50'),
    step('p95'),
    task ? { value: `${f.vs(task.value_ms, task.target_ms)} ms`, met: task.met } : { value: '–', met: true },
  ];
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
  host_cpu_util_pct: 'Host CPU utilization',
  microvm_throttled_fraction: 'MicroVM CPU throttled',
  host_mem_available_fraction: 'Host memory used',
  host_mem_pressure_pct: 'Host memory pressure',
  host_io_pressure_pct: 'Host IO pressure',
  host_steal_pct: 'CPU taken by outer hypervisor',
};

/**
 * The series behind the trial's verdict, with its rule's threshold: the first rule of that verdict that fired (host
 * CPU pressure, when it did), else the first one recorded. When no rule fired, the host CPU series.
 */
export function limitPanels(doc: TrialDoc, summary: TrialSummary, rules: RuleDef[]): SeriesPanel[] {
  const verdict = summary.attribution?.verdicts[0] ?? doc.limit.verdicts[0] ?? 'unknown';
  const lookFor: Verdict = verdict === 'none' || verdict === 'unknown' ? 'host_cpu' : verdict;
  const fired = (k: RuleKey) => summary.attribution?.rules.find((r) => r.key === k)?.fired ?? null;
  const panels: SeriesPanel[] = [];
  const h = doc.series.host;
  const mine = rules.filter((r) => r.verdict === lookFor);
  const first = mine.find((r) => fired(r.key)) ?? null;
  for (const rule of first ? [first, ...mine.filter((r) => r !== first)] : mine) {
    if (panels.length) break;
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
