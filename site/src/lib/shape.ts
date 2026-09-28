// Shapes the dataset for the views. Pure functions over the documents in DATA.md, so the tests can run them over
// the fixtures. Nothing here recomputes a result: pass, fail and the result come from the builder; these only
// group, pick and label (and compute D60 midpoints for specs from different campaigns, by the same rule).
import * as f from './format';
import { makeBands, type Band } from './bands';
import { meanMidpoint } from './derive';
import { STOPPED_EARLY, SUBJECT_LABEL, trialLabel, VERDICT_SHORT } from './glossary';
import { href } from './router';
import { chromiumFlags, differing, flagsWords, flatten, field, hypervisorName, mib, show, STANDARD_CRITERIA } from './spec';
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
 * size, the extra Chromium flags, then the devices to break a tie ("Firecracker MMIO"). Flags are "extra flags" beside
 * "no extra flags" where the specs add only one set of them, and the flags themselves, cut short where that stays
 * unambiguous ("--disable-features=…"), where they add several. One spec, or specs that differ in nothing named here,
 * get "m8i.4xlarge · Firecracker"; the builder's label is the last resort. Slugs stay in URLs.
 */
export function specLabels(specs: SpecDoc[]): string[] {
  const host = (s: SpecDoc) => s.host.instance_type;
  const hv = (s: SpecDoc) => hypervisorName(s.spec.hypervisor.name);
  const vm = (s: SpecDoc) => `${s.spec.microvm.vcpus} vCPU · ${mib(s.spec.microvm.memory_mib)} microVM`;
  const sets = new Set(specs.map((s) => chromiumFlags(s.spec).join(' ')).filter(Boolean));
  const words = flagsWords(specs.map((s) => s.spec));
  const flags = (s: SpecDoc) => (!chromiumFlags(s.spec).length ? 'no extra flags' : sets.size === 1 ? 'extra flags' : words[specs.indexOf(s)]);
  const parts = [host, hv, vm, flags].filter((p) => new Set(specs.map(p)).size > 1);
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

export type CardRow = 'host' | 'hypervisor' | 'microvm' | 'densities' | 'chromium';

export interface SpecCard {
  host: { value: string; sub: string };
  /** The devices are named only when they differ between the specs shown. */
  hypervisor: { value: string; sub: string | null };
  microvm: string;
  densities: number[];
  /** The extra Chromium flags, in full; none when the spec leaves them out. */
  chromium: string[];
}

/** The microVM as a card shows it: its size, then its guest console and memory pages where they aren't the defaults
 * ("2 vCPU · 2 GiB · quiet console · transparent huge pages"), so specs that differ only there don't read the same. */
function microvmCard(m: Spec['microvm']): string {
  const parts = [`${m.vcpus} vCPU`, mib(m.memory_mib)];
  if (m.console && m.console !== 'verbose') parts.push(m.console === 'quiet-i8042' ? 'quiet console, no keyboard probe' : `${m.console} console`);
  if (m.memory_pages && m.memory_pages !== '4k') parts.push(show('microvm.memory_pages', m.memory_pages));
  return parts.join(' · ');
}

/** A spec's inputs as the card shows them: the host, the hypervisor, the microVM, the densities. */
export function specCard(s: SpecDoc, showDevices = false): SpecCard {
  const h = s.spec.hypervisor;
  return {
    host: { value: s.host.instance_type, sub: `${s.host.vcpus} vCPU · ${f.num(s.host.memory_gib)} GiB · ${s.host.host_kind}` },
    hypervisor: { value: hypervisorName(h.name), sub: showDevices ? `${devices(h)} devices` : null },
    microvm: microvmCard(s.spec.microvm),
    densities: s.spec.densities,
    chromium: chromiumFlags(s.spec),
  };
}

/** Whether the cards show a Chromium flags row: when any spec shown adds flags. Where none does, the row would only
 * say "none" for every spec, as every campaign before the flags existed ran. */
export const showsChromium = (specs: SpecDoc[]) => specs.some((s) => chromiumFlags(s.spec).length > 0);

const ROW_OF: [string, CardRow][] = [
  ['worker_host.', 'host'],
  ['hypervisor.', 'hypervisor'],
  ['microvm.', 'microvm'],
  ['densities', 'densities'],
  ['workload.', 'chromium'],
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
// The SLOs a spec is judged by, as the pages show them, and how they differ from the method's standard ones (the
// input schema's defaults, STANDARD_CRITERIA). A campaign may choose its own targets (D74): the site shows each
// campaign's own, and wherever campaigns sit side by side it names the difference from the standard.

/** Whether every spec is judged by the same criteria. */
export function sameCriteria(specs: SpecDoc[]): boolean {
  return specs.every((s) => JSON.stringify(s.spec.criteria) === JSON.stringify(specs[0].spec.criteria));
}

export type Criteria = Spec['criteria'];
type CriteriaKey = keyof Criteria;

/** 1000 → "1 s", 1500 → "1.5 s", 750 → "750 ms". */
export function sloTime(ms: number): string {
  return ms >= 1000 ? `${f.num(ms / 1000, ms % 1000 === 0 ? 0 : ms % 100 === 0 ? 1 : 2)} s` : `${f.num(ms)} ms`;
}

interface Limit {
  /** In a chip: "Step p50". */
  short: string;
  /** On a badge: "Each step p50". */
  long: string;
  /** In a sentence: "step p50". */
  words: string;
  text: (v: number) => string;
}

const upTo = (v: number) => `≤ ${sloTime(v)}`;
const LIMITS: Record<CriteriaKey, Limit> = {
  ready_timeout_s: { short: 'Ready', long: 'Browser ready', words: 'ready', text: (v) => `≤ ${f.num(v)} s` },
  step_p50_target_ms: { short: 'Step p50', long: 'Each step p50', words: 'step p50', text: upTo },
  step_p95_target_ms: { short: 'Step p95', long: 'Each step p95', words: 'step p95', text: upTo },
  task_p95_target_ms: { short: 'Task p95', long: 'Whole task p95', words: 'task p95', text: upTo },
  step_timeout_ms: { short: 'Step limit', long: 'Step time limit', words: 'step time limit', text: sloTime },
  task_timeout_ms: { short: 'Task limit', long: 'Task time limit', words: 'task time limit', text: sloTime },
};

/** The order a difference is named in: the latency targets first, then ready, then the time limits. */
const DIFF_ORDER: CriteriaKey[] = [
  'step_p50_target_ms',
  'step_p95_target_ms',
  'task_p95_target_ms',
  'ready_timeout_s',
  'step_timeout_ms',
  'task_timeout_ms',
];

export interface SloChip {
  key: CriteriaKey | 'tasks';
  short: string;
  long: string;
  value: string;
  /** The standard value, where this one differs from it. */
  standard: string | null;
}

const chip = (c: Criteria, key: CriteriaKey): SloChip => {
  const l = LIMITS[key];
  const standard = c[key] === STANDARD_CRITERIA[key] ? null : l.text(STANDARD_CRITERIA[key]);
  return { key, short: l.short, long: l.long, value: l.text(c[key]), standard };
};

/**
 * The five SLOs in the order every page shows them (ready, tasks, step p50, step p95, task p95); with `limits`, then
 * each time limit that differs from the standard, the only way those show.
 */
export function sloChips(c: Criteria, limits = false): SloChip[] {
  const five: SloChip[] = [
    chip(c, 'ready_timeout_s'),
    { key: 'tasks', short: 'Tasks', long: 'Tasks succeed', value: '100%', standard: null },
    chip(c, 'step_p50_target_ms'),
    chip(c, 'step_p95_target_ms'),
    chip(c, 'task_p95_target_ms'),
  ];
  if (!limits) return five;
  const other = (['step_timeout_ms', 'task_timeout_ms'] as const).filter((k) => c[k] !== STANDARD_CRITERIA[k]);
  return [...five, ...other.map((k) => chip(c, k))];
}

export interface SloDiff {
  key: CriteriaKey;
  /** "step p50 ≤ 2 s", with no-break spaces so a line never breaks inside it. */
  text: string;
  /** Every criterion is an upper limit, so a higher one is looser. */
  looser: boolean;
}

/** How the criteria differ from the standard, most telling first. None for the standard SLOs. */
export function sloDiffs(c: Criteria): SloDiff[] {
  return DIFF_ORDER.filter((k) => c[k] !== STANDARD_CRITERIA[k]).map((k) => ({
    key: k,
    text: `${LIMITS[k].words} ${LIMITS[k].text(c[k])}`.replaceAll(' ', '\u00a0'),
    looser: c[k] > STANDARD_CRITERIA[k],
  }));
}

/** The differences of several specs' criteria from the standard, each named once, in DIFF_ORDER. */
function mergedDiffs(criteria: Criteria[]): SloDiff[] {
  const seen = new Map<string, SloDiff>();
  for (const c of criteria) for (const d of sloDiffs(c)) if (!seen.has(d.text)) seen.set(d.text, d);
  return [...seen.values()].sort((a, b) => DIFF_ORDER.indexOf(a.key) - DIFF_ORDER.indexOf(b.key));
}

const kind = (ds: SloDiff[]) => (ds.every((d) => d.looser) ? 'looser' : ds.every((d) => !d.looser) ? 'stricter' : 'different');

/**
 * A short tag for a campaign (or spec) judged by other than the standard SLOs, naming the difference: "Looser SLO:
 * step p50 ≤ 2 s", "Looser SLOs: step p50 ≤ 2 s, step p95 ≤ 3 s, ready ≤ 900 s". Null for the standard SLOs.
 */
export function sloTag(criteria: Criteria[]): string | null {
  const ds = mergedDiffs(criteria);
  if (!ds.length) return null;
  const k = kind(ds);
  return `${k[0].toUpperCase()}${k.slice(1)} SLO${ds.length > 1 ? 's' : ''}: ${ds.map((d) => d.text).join(', ')}`;
}

const list = (xs: string[]) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs.at(-1)}`);

/**
 * When the specs shown side by side aren't all judged by the same SLOs, who differs from the standard and how, for a
 * line that starts "SLOs differ:": "Metal host uses step p50 ≤ 2 s, step p95 ≤ 3 s and ready ≤ 900 s; the rest use
 * the standard." A spec is named by its campaign when every spec shown from that campaign is judged alike. Null when
 * every spec shown has the same SLOs.
 */
export function sloDifferences(refs: SpecRef[]): string | null {
  const key = (r: SpecRef) => DIFF_ORDER.map((k) => r.spec.spec.criteria[k]).join();
  const groups = new Map<string, SpecRef[]>();
  for (const r of refs) groups.set(key(r), [...(groups.get(key(r)) ?? []), r]);
  if (groups.size < 2) return null;
  const campaigns = new Set(refs.map((r) => r.campaign)).size > 1;
  const parts: string[] = [];
  let standard = false;
  for (const g of groups.values()) {
    const ds = sloDiffs(g[0].spec.spec.criteria);
    if (!ds.length) {
      standard = true;
      continue;
    }
    const names: string[] = [];
    for (const r of g) {
      const whole = refs.filter((x) => x.campaign === r.campaign).every((x) => key(x) === key(r));
      const name = whole ? r.campaignTitle : campaigns ? `${r.campaignTitle} · ${r.groupLabel}` : r.groupLabel;
      if (!names.includes(name)) names.push(name);
    }
    parts.push(`${list(names)} ${names.length > 1 ? 'use' : 'uses'} ${list(ds.map((d) => d.text))}`);
  }
  return `${parts.join('; ')}${standard ? '; the rest use the standard' : ''}.`;
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
  /** The spec behind the figure (its slug), or null when nothing has run. */
  spec: string | null;
} {
  const score = (o: CampaignEntry['outcomes'][number]) => {
    const per = o.replicas.map((r) => r.per_host_vcpu).filter((x): x is number => x !== null);
    return o.midpoint_per_host_vcpu ?? (per.length ? Math.min(...per) : -1);
  };
  const best = [...e.outcomes].sort((a, b) => score(b) - score(a))[0];
  if (!best || !best.replicas.length) return { density: null, label: 'not run yet', perVcpu: null, spec: null };
  const passed = spread(best.replicas.map((r) => r.tested_successfully), (v) => f.num(v));
  return {
    density: passed,
    label: passed === null ? 'none passed' : passed === '1' ? 'microVM' : 'microVMs',
    perVcpu: spread(best.replicas.map((r) => r.per_host_vcpu), f.ratio),
    spec: best.spec,
  };
}

export interface CardBar {
  key: string;
  label: string;
  /** "9", "8–9", "0.56"; a label when there's no figure ("not run yet"). */
  text: string;
  lo: number;
  hi: number;
}

/**
 * The figure a campaign's card shows: with one spec, its density and density per host vCPU; with several, a small
 * bar per spec of what the campaign compares (the density on one host size, density per host vCPU across sizes).
 * `labels` are the specs' names as the campaign page shows them, in the entry's order.
 */
export function campaignFigure(
  e: CampaignEntry,
  labels: string[] = e.specs.map((s) => s.label),
): { single: ReturnType<typeof campaignHeadline> | null; metric: 'Max density' | 'Per vCPU'; bars: CardBar[]; max: number } {
  const perVcpu = new Set(e.outcomes.flatMap((o) => o.replicas.map((r) => r.host_vcpus))).size > 1;
  const metric = perVcpu ? 'Per vCPU' : 'Max density';
  if (e.specs.length < 2) return { single: campaignHeadline(e), metric, bars: [], max: 0 };
  const bars = e.specs.map((s, i): CardBar => {
    const reps = e.outcomes.find((o) => o.spec === s.name)?.replicas ?? [];
    const vals = reps.map((r) => (perVcpu ? r.per_host_vcpu : r.tested_successfully)).filter((v): v is number => v !== null);
    const fmt = perVcpu ? f.ratio : (v: number) => f.num(v);
    const text = !reps.length ? 'not run yet' : !vals.length ? 'none passed' : f.range([Math.min(...vals), Math.max(...vals)], fmt);
    return { key: s.name, label: labels[i] ?? s.label, text, lo: vals.length ? Math.min(...vals) : 0, hi: vals.length ? Math.max(...vals) : 0 };
  });
  return { single: null, metric, bars, max: Math.max(0, ...bars.map((b) => b.hi)) };
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

/** "8–9" → [8, 9]; "9" → [9, 9]. */
const ends = (s: string) => {
  const n = (s.match(/\d+(\.\d+)?/g) ?? ['0']).map(Number);
  return [Math.min(...n), Math.max(...n)];
};
const listed = (xs: string[]) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);
const EVERY = ['', 'it', 'both', 'all three', 'all four'];

/**
 * The answer in a line or two, from the headlines: what each spec fit (grouped where they fit the same), and what
 * ran out. On one host size the figure is the density; across sizes, density per host vCPU.
 */
export function answerText(results: SpecResult[]): string {
  if (!results.length) return '';
  const hs = results.map(headline);
  if (results.length === 1) {
    const [h, r] = [hs[0], results[0]];
    if (!r.replicas.length) return 'Not run yet.';
    if (h.density === '0') return 'No density met every SLO.';
    const d = h.density.startsWith('≥') ? `At least ${h.density.slice(2)}` : h.density;
    const fit = `${d} ${d === '1' ? 'microVM' : 'microVMs'} met every SLO${h.perVcpu ? `, ${h.perVcpu} per host vCPU` : ''}.`;
    return `${fit} ${r.limits[0] ? `${h.ranOut} ran out at ${r.limits[0].density}.` : 'Nothing ran out.'}`;
  }
  const perVcpu = new Set(results.flatMap((r) => r.replicas.map((x) => x.host_vcpus))).size > 1;
  const groups = new Map<string, string[]>();
  const missing: string[] = [];
  results.forEach((r, i) => {
    const v = perVcpu ? hs[i].perVcpu : hs[i].density;
    if (!r.replicas.length || v === null || hs[i].density === '0') missing.push(r.ref.groupLabel);
    else groups.set(v, [...(groups.get(v) ?? []), r.ref.groupLabel]);
  });
  const unit = perVcpu ? ' microVMs per host vCPU' : ' microVMs';
  // Highest first: by the top of each range, then its bottom.
  const sorted = [...groups].sort((a, b) => ends(b[0])[1] - ends(a[0])[1] || ends(b[0])[0] - ends(a[0])[0]);
  const parts: string[] = [];
  if (sorted.length === 1 && !missing.length) parts.push(`${EVERY[results.length] ?? `all ${results.length}`} fit ${sorted[0][0]}${unit}.`);
  else if (sorted.length > 3) {
    // Many specs: the best, and the range of the rest, so the line stays a line.
    const [[v, labels], ...rest] = sorted;
    const others = rest.reduce((n, [, ls]) => n + ls.length, 0);
    const [lo, hi] = [Math.min(...rest.map(([x]) => ends(x)[0])), Math.max(...rest.map(([x]) => ends(x)[1]))];
    const fmt = (n: number) => (perVcpu ? n.toFixed(2) : `${n}`);
    parts.push(`${listed(labels)} ${labels.length > 1 ? 'fit' : 'fits'} the most, ${v}${unit}; the other ${others} specs ${lo === hi ? fmt(lo) : `${fmt(lo)}–${fmt(hi)}`}.`);
  } else if (sorted.length) {
    parts.push(
      `${sorted.map(([v, labels], i) => (i === 0 ? `${listed(labels)} ${labels.length > 1 ? 'fit' : 'fits'} ${v}${unit}` : `${listed(labels)} ${v}`)).join(', ')}.`,
    );
  }
  if (missing.length) parts.push(`${listed(missing)}: none passed or not run yet.`);
  const outs = [...new Set(results.filter((r) => r.limits.length).map((r) => VERDICT_SHORT[r.limits[0].verdict]))];
  const failing = results.filter((r) => r.limits.length).length;
  if (outs.length === 1) parts.push(`${outs[0]} ran out first${failing === results.length ? ` in ${EVERY[failing] ?? `all ${failing}`}` : ''}.`);
  else if (outs.length > 1 && failing > 3) {
    const n = (o: string) => results.filter((r) => r.limits.length && VERDICT_SHORT[r.limits[0].verdict] === o).length;
    parts.push(`Ran out first: ${outs.map((o) => `${o.toLowerCase()} in ${n(o)}`).join(', ')}.`);
  } else if (outs.length > 1) {
    parts.push(`Ran out: ${results.filter((r) => r.limits.length).map((r) => `${r.ref.groupLabel} ${VERDICT_SHORT[r.limits[0].verdict].toLowerCase()}`).join('; ')}.`);
  }
  // Two sentences at most: the fit, and what ran out.
  return parts.slice(0, 2).join(' ');
}

export interface AnswerRun {
  replica: number;
  runId: string;
  href: string | null;
  /** The highest density that met every SLO, and the first that failed. */
  passed: number | null;
  failed: number | null;
  stoppedEarly: boolean;
  hostVcpus: number;
}

export interface AnswerRow extends CompareRow {
  /** The spec's place in the list, for its colour. */
  series: number;
  runs: AnswerRun[];
  hostVcpus: number;
}

/** The answer chart's rows: each spec's headline figures, and every run (replica) as a bar from 0 to its first failure. */
export function answerRows(results: SpecResult[], docs: Map<string, CampaignDoc>): AnswerRow[] {
  const base = compareRows(results);
  return results.map((r, i) => {
    const c = docs.get(r.ref.campaign)!;
    const n = Math.max(c.definition.replicas, ...r.runs.map((x) => x.replica));
    const runs: AnswerRun[] = Array.from({ length: n }, (_, k) => {
      const run = r.runs.find((x) => x.replica === k + 1);
      const rep = run ? r.replicas.find((x) => x.run === run.id) : undefined;
      return {
        replica: k + 1,
        runId: run?.id ?? `${r.ref.spec.name}-r${k + 1}`,
        href: run ? href({ name: 'run', campaign: c.id, run: run.id, density: null }) : null,
        passed: rep?.tested_successfully ?? run?.result.tested_successfully ?? null,
        failed: rep?.first_failed ?? run?.result.first_failed ?? null,
        stoppedEarly: !!(rep?.stopped_early ?? run?.stopped_early),
        hostVcpus: run?.host.vcpus ?? r.ref.spec.host.vcpus,
      };
    });
    return { ...base[i], series: i, runs, hostVcpus: r.ref.spec.host.vcpus };
  });
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

// ---- latency by density: the slowest step's p50 and the whole task's p95, per trial ---------------------

export interface LatencyPoint {
  key: string;
  /** Which series (spec) the point belongs to, from 0. */
  series: number;
  replica: number;
  density: number;
  /** The density on the chart's x: the density itself on one host size, per host vCPU across sizes. */
  x: number;
  /** The slowest step's p50 and which step it was; null when the trial recorded no step checks. */
  step: { ms: number; target: number; name: StepName } | null;
  task: { ms: number; target: number } | null;
  passed: boolean;
  /** "Firecracker · replica 1 · trial 2 at density 10". */
  title: string;
  href: string;
}

export interface LatencySeries {
  key: string;
  label: string;
  campaignTitle: string;
  points: LatencyPoint[];
}

export interface Latency {
  series: LatencySeries[];
  /** true: x is density per host vCPU (the specs' hosts differ in size); false: x is the density. */
  perVcpu: boolean;
  replicas: number;
  /** The SLO limits drawn: each distinct target across the specs. */
  stepTargets: number[];
  taskTargets: number[];
  /**
   * Whose each limit is, in the same order, when the specs' limits differ (D74): "standard", or the campaigns (or
   * specs) that chose it. Null for each when every spec shares one limit.
   */
  stepTargetWho: (string | null)[];
  taskTargetWho: (string | null)[];
}

/**
 * Every counting trial of each spec's runs as two latencies: its slowest step's p50 and its whole task's p95, each
 * against its SLO. Only densities that were tested have points; nothing is interpolated between them.
 */
export function latency(refs: SpecRef[], docs: Map<string, CampaignDoc>, runDocs: Map<string, RunDoc>): Latency {
  const vcpus = new Set<number>();
  const found: { ref: SpecRef; run: RunDoc }[] = [];
  for (const ref of refs) {
    const c = docs.get(ref.campaign)!;
    for (const entry of c.runs.filter((r) => r.spec === ref.spec.name)) {
      const run = runDocs.get(`${c.id}/${entry.id}`);
      if (!run) continue;
      found.push({ ref, run });
      vcpus.add(run.host.vcpus);
    }
  }
  const perVcpu = vcpus.size > 1;
  const multi = refs.length > 1;
  // Specs from several campaigns (the compare page) carry their campaign's title.
  const campaigns = new Set(refs.map((r) => r.campaign)).size > 1;
  const series: LatencySeries[] = refs.map((ref) => ({
    key: `${ref.campaign}/${ref.spec.name}`,
    label: campaigns ? `${ref.campaignTitle} · ${ref.groupLabel}` : ref.groupLabel,
    campaignTitle: ref.campaignTitle,
    points: [],
  }));
  // The replicas drawn apart: those with runs here (one, on a run's own page).
  const present = new Set(found.map((x) => x.run.replica));
  const replicas = present.size > 1 ? Math.max(...present) : 1;
  for (const { ref, run } of found) {
    const i = refs.indexOf(ref);
    const c = docs.get(ref.campaign)!;
    for (const t of run.trials) {
      if (!t.counts || t.passed === null) continue;
      const steps = t.checks.filter((ch) => ch.subject !== 'task' && ch.stat === 'p50');
      const slow = steps.sort((a, b) => b.value_ms - a.value_ms)[0];
      const task = t.checks.find((ch) => ch.subject === 'task' && ch.stat === 'p95');
      if (!slow && !task) continue;
      const who = [multi ? series[i].label : null, c.definition.replicas > 1 ? `replica ${run.replica}` : null].filter(Boolean);
      series[i].points.push({
        key: `${c.id}/${run.id}/${t.id}`,
        series: i,
        replica: run.replica,
        density: t.density,
        x: perVcpu ? t.density / run.host.vcpus : t.density,
        step: slow ? { ms: slow.value_ms, target: slow.target_ms, name: slow.subject as StepName } : null,
        task: task ? { ms: task.value_ms, target: task.target_ms } : null,
        passed: !!t.passed,
        title: [...who, trialLabel(t)].join(' · '),
        href: href({ name: 'trial', campaign: c.id, run: run.id, trial: t.id, microvm: null }),
      });
    }
  }
  for (const s of series) s.points.sort((a, b) => a.x - b.x || a.replica - b.replica || a.key.localeCompare(b.key));
  type TargetKey = 'step_p50_target_ms' | 'task_p95_target_ms';
  const targets = (k: TargetKey) => [...new Set(refs.map((r) => r.spec.spec.criteria[k]))].sort((a, b) => a - b);
  const who = (k: TargetKey, ts: number[]) =>
    ts.map((t) => {
      if (ts.length < 2) return null;
      if (t === STANDARD_CRITERIA[k]) return 'standard';
      const mine = refs.filter((r) => r.spec.spec.criteria[k] === t);
      const whole = (r: SpecRef) => refs.filter((x) => x.campaign === r.campaign).every((x) => x.spec.spec.criteria[k] === t);
      return [...new Set(mine.map((r) => (whole(r) ? r.campaignTitle : series[refs.indexOf(r)].label)))].join(', ');
    });
  const stepTargets = targets('step_p50_target_ms');
  const taskTargets = targets('task_p95_target_ms');
  return {
    series,
    perVcpu,
    replicas,
    stepTargets,
    taskTargets,
    stepTargetWho: who('step_p50_target_ms', stepTargets),
    taskTargetWho: who('task_p95_target_ms', taskTargets),
  };
}

/**
 * The density bands the latency chart and the every-trial grid share: every density some trial tested, then the
 * listed densities above them as one not-tested band. Per host vCPU when the hosts differ in size, as the latency.
 */
export function sharedBands(lat: Latency, rows: ChartRow[]): Band[] {
  const x = (d: number, v: number) => (lat.perVcpu ? d / v : d);
  return makeBands(
    [...lat.series.flatMap((s) => s.points.map((p) => p.x)), ...rows.flatMap((r) => r.marks.map((m) => x(m.density, r.hostVcpus)))],
    rows.flatMap((r) => r.notTested.map((n) => x(n.density, r.hostVcpus))),
    lat.perVcpu,
  );
}

// ---- at the limit: one trial at each spec's first failing density ---------------------------------------

export interface LimitPick {
  ref: SpecRef;
  run: RunDoc;
  trial: TrialSummary;
  density: number;
  /** true: the spec's first failing density; false: nothing failed, so this is the highest density that passed. */
  failed: boolean;
  /** What ran out there: the run's verdict at its limit, or the trial's own. */
  ranOut: Verdict | null;
  /** In the same run, a trial at the highest density that passed, to set beside the failure. */
  pass: { trial: TrialSummary; density: number } | null;
}

/**
 * The trial that shows where a spec reached its limit: of its runs, the one that failed at the lowest density (the
 * first replica on a tie), and there the first trial that failed. With no failure, the highest density that passed.
 */
export function limitPick(ref: SpecRef, c: CampaignDoc, runDocs: Map<string, RunDoc>): LimitPick | null {
  const runs = c.runs
    .filter((r) => r.spec === ref.spec.name)
    .map((r) => runDocs.get(`${c.id}/${r.id}`))
    .filter((r): r is RunDoc => !!r)
    .sort((a, b) => a.replica - b.replica);
  const failing = runs.filter((r) => r.result.first_failed !== null);
  const failed = failing.length > 0;
  const run = failed
    ? failing.reduce((best, r) => (r.result.first_failed! < best.result.first_failed! ? r : best))
    : runs.filter((r) => r.result.tested_successfully !== null).reduce<RunDoc | null>(
        (best, r) => (!best || r.result.tested_successfully! > best.result.tested_successfully! ? r : best),
        null,
      );
  if (!run) return null;
  const density = failed ? run.result.first_failed! : run.result.tested_successfully!;
  const at = run.trials.filter((t) => t.counts && t.density === density).sort((a, b) => (a.number ?? 0) - (b.number ?? 0));
  const trial = (failed ? at.find((t) => t.passed === false) : undefined) ?? at[0];
  if (!trial) return null;
  const ranOut = failed ? (run.result.limit?.verdicts[0] ?? trial.attribution?.verdicts[0] ?? null) : null;
  const passAt = failed ? run.result.tested_successfully : null;
  const passTrial =
    passAt === null
      ? undefined
      : run.trials
          .filter((t) => t.counts && t.density === passAt && t.passed)
          .sort((a, b) => (a.number ?? 0) - (b.number ?? 0))[0];
  return { ref, run, trial, density, failed, ranOut, pass: passTrial && passAt !== null ? { trial: passTrial, density: passAt } : null };
}

/** The rule behind a verdict at a density: the first of that verdict's rules that fired in any of its trials. */
export function firedRule(trials: TrialSummary[], rules: RuleDef[], verdict: Verdict | null): RuleDef | null {
  if (!verdict) return null;
  const fired = (k: RuleKey) => trials.some((t) => t.attribution?.rules.find((r) => r.key === k)?.fired);
  return rules.find((r) => r.verdict === verdict && fired(r.key)) ?? null;
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
  'Step median target': 'Step p50 target',
  'Step time limit': 'Step timeout',
  'Task time limit': 'Task timeout',
  'Idle before each trial': 'Idle before trial',
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
export function limitPanels(doc: TrialDoc, summary: TrialSummary, rules: RuleDef[], prefer: RuleKey | null = null): SeriesPanel[] {
  const verdict = summary.attribution?.verdicts[0] ?? doc.limit.verdicts[0] ?? 'unknown';
  const preferred = prefer ? (rules.find((r) => r.key === prefer) ?? null) : null;
  const lookFor: Verdict = preferred ? preferred.verdict : verdict === 'none' || verdict === 'unknown' ? 'host_cpu' : verdict;
  const fired = (k: RuleKey) => summary.attribution?.rules.find((r) => r.key === k)?.fired ?? null;
  const panels: SeriesPanel[] = [];
  const h = doc.series.host;
  const mine = rules.filter((r) => r.verdict === lookFor);
  const first = preferred ?? mine.find((r) => fired(r.key)) ?? null;
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

/**
 * Host CPU by process as stacked cores, if the run recorded per-process counters. The counters are cumulative, so
 * one short sample interval can be credited with more CPU than the host has (during boot on a 192-vCPU host, several
 * times more): a sampling artifact. With the host's vCPUs, a sample whose layers add up to more is scaled down to
 * exactly that, each process keeping its share, so the chart never shows more CPU than the host has. Only the
 * plotted copy changes; the published data doesn't.
 */
export function coresStack(doc: TrialDoc, hostVcpus: number | null = null): { t: number[]; layers: { key: HostConsumer; values: number[] }[] } | null {
  const cores = doc.series.host.cores;
  if (!cores) return null;
  const layers = HOST_CONSUMERS.map((key) => ({ key, values: cores[key] }));
  if (hostVcpus === null) return { t: doc.series.host.t_ms, layers };
  const scale = doc.series.host.t_ms.map((_, i) => {
    const total = layers.reduce((a, l) => a + Math.max(0, l.values[i] ?? 0), 0);
    return total > hostVcpus ? hostVcpus / total : 1;
  });
  return { t: doc.series.host.t_ms, layers: layers.map((l) => ({ key: l.key, values: l.values.map((v, i) => v * scale[i]) })) };
}
