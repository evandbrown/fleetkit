// Where a builder draft comes from and where it goes: the campaign files bundled at build time, a published campaign
// or run a link names, pasted JSON, and the draft kept in this browser. Plus the small bits of formatting the
// builder's components share.
import { loadCampaign } from '../../lib/data';
import {
  CAMPAIGN_FIELDS,
  InputError,
  LIMITS,
  TYPES,
  evaluate,
  fieldAt,
  isObj,
  parseJson,
  usd,
  type Evaluation,
  type Obj,
  type Plan,
} from '../../lib/campaign';

// ------------------------------------------------------------------ starts
const FILES = import.meta.glob('../../../../experiments/campaigns/**/*.json', { eager: true, import: 'default' }) as Record<
  string,
  Obj
>;

export interface Start {
  key: string;
  /** What the menu calls it: a plain title, or the file's name when there's none. */
  title: string;
  example: boolean;
  def: Obj;
}

/** Titles for the campaign files, which carry only a name. A published campaign's is its title on Results (site/catalog.json;
 *  a unit test holds them equal). */
export const TITLES: Record<string, string> = {
  'nested-sizes-1': 'Host size',
  'nested-hv-1': 'Hypervisor',
  'nested-mem-1': 'Host memory',
  'hv-host-1': 'Hypervisor and host kind',
  'hv-host-2': 'Metal host',
  'c8i-sizes-1': 'Smaller c8i hosts',
  'shape-cold-1': 'MicroVM shape, cold start',
  'shape-warm-1': 'MicroVM shape, warm start',
  'hv-host-3': 'Metal, nested SLO',
};

/** The campaign files in experiments/campaigns/, campaigns first, then the examples that aren't approved to run. */
export const STARTS: Start[] = Object.entries(FILES)
  .map(([path, def]) => {
    const key = String(def.name);
    return { key, title: TITLES[key] ?? key, example: path.includes('/examples/'), def };
  })
  .sort((a, b) => Number(a.example) - Number(b.example) || a.key.localeCompare(b.key));

const FIRST = 'nested-sizes-1';
export const DEFAULT_START = STARTS.find((s) => s.key === FIRST) ?? STARTS[0];

/** A draft as the builder holds it: the definition, what it started from, and that start's definition for Reset. */
export interface Draft {
  def: Obj;
  label: string;
  source: Obj | null;
}

export const fromStart = (s: Start): Draft => ({ def: structuredClone(s.def), label: s.key, source: s.def });

/** A spec on its own becomes a campaign of one spec that runs it unchanged; the reader names it and asks the question. */
function fromSpec(spec: Obj, label: string, specName = 'base'): Draft {
  const def: Obj = { name: '', question: '', replicas: 2, shutdown_after_minutes: 45, base: spec, specs: { [specName]: {} } };
  return { def, label, source: null };
}

/** Pasted text: a campaign definition, a spec on its own (which becomes a campaign of one spec that runs it
 * unchanged), or a message with either in its first ```json block. */
export function interpret(text: string): Draft {
  const fence = /```(?:json)?\s*\n([\s\S]*?)```/.exec(text);
  let body = fence ? fence[1] : text.trim();
  if (!fence && !body.startsWith('{')) {
    const a = body.indexOf('{');
    const b = body.lastIndexOf('}');
    if (a >= 0 && b > a) body = body.slice(a, b + 1);
  }
  const v = parseJson(body);
  if (!isObj(v)) throw new InputError('this JSON is not an object');
  if ('base' in v || 'specs' in v || 'question' in v) return { def: v, label: 'pasted JSON', source: structuredClone(v) };
  if ('worker_host' in v || 'densities' in v) return fromSpec(v, 'pasted JSON');
  throw new InputError('this is neither a campaign definition nor a spec');
}

/** A campaign file bundled with the site, for a link like #/builder?from=campaign:nested-sizes-1. */
export function bundled(from: string | null): Draft | null {
  const s = from?.startsWith('campaign:') ? STARTS.find((x) => x.key === from.slice('campaign:'.length)) : null;
  return s ? fromStart(s) : null;
}

/**
 * What a "New campaign from this" link asks for (DATA.md, "Builder handoff"): campaign:<campaign> opens that
 * campaign's definition, from the bundled file when there is one, otherwise as published; run:<campaign>/<run>
 * opens a campaign of one spec that runs that run's spec unchanged.
 */
export async function fromLink(from: string): Promise<Draft> {
  const quick = bundled(from);
  if (quick) return quick;
  const [kind, ref] = [from.slice(0, from.indexOf(':')), from.slice(from.indexOf(':') + 1)];
  if (kind === 'campaign') {
    const def = structuredClone((await loadCampaign(ref)).definition) as unknown as Obj;
    def.shutdown_after_minutes ??= CAMPAIGN_FIELDS.shutdown_after_minutes.schema.default;
    return { def, label: ref, source: structuredClone(def) };
  }
  const [campaign, run] = ref.split('/');
  const doc = await loadCampaign(campaign);
  const name = doc.runs.find((r) => r.id === run)?.spec;
  const spec = doc.specs.find((s) => s.name === name);
  if (!name || !spec) throw new InputError(`${campaign} has no run ${run}`);
  return fromSpec(structuredClone(spec.spec) as unknown as Obj, `run ${run} of ${campaign}`, name);
}

const DRAFT_KEY = 'fleetkit.builder.draft';

export function loadDraft(): Draft | null {
  try {
    const v = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null');
    if (!isObj(v) || !isObj(v.def) || typeof v.label !== 'string') return null;
    return { def: v.def, label: v.label, source: isObj(v.source) ? v.source : null };
  } catch {
    return null;
  }
}

export function saveDraft(d: Draft): void {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ def: d.def, label: d.label, source: d.source }));
  } catch {
    // storage blocked: the draft lasts until the page closes
  }
}

/** "Compare values of this input": scale densities to the same grid per host vCPU; give the base Cloud
 * Hypervisor's devices so a hypervisor comparison differs only in the hypervisor. */
export interface CompareOptions {
  scale: boolean;
  matchDevices: boolean;
}

// ------------------------------------------------------------------ messages
export interface Msg {
  path: string;
  text: string;
  error: boolean;
}

/** The rule for a campaign or spec name, in fewer words than the schema's message. */
export const NAME_RULE = '2–40 lowercase letters, digits or hyphens, starting with a letter';

/** The long messages campaign.ts shares with expand.py, in fewer words for the page. */
const SHORTER: [RegExp, string][] = [
  [/^a name has 2 to 40 lowercase letters.*$/, NAME_RULE],
  [/^at density (\d+) the microVMs' memory adds up to ([\d.]+) GiB against the worker host's ([\d.]+) GiB, so memory may run out before CPU$/, 'memory full by density $1 ($2 of $3 GiB)'],
  [/^the worst case is (\S+), above the (\S+) limit for one campaign.*$/, 'worst case $1 is over the $2 limit'],
  [/^a run of (\S+) is expected to take about (\d+) minutes, longer than the (\d+)-minute timer$/, '$1 takes ≈$2 min, over the $3-min timer'],
  [/^with 1 replica per spec there is no spread between hosts.*$/, '1 replica: no host-to-host spread to compare against'],
  [/^a run needs (\d+) vCPUs with its support host, more than the quota of (\d+).*$/, 'needs $1 vCPUs, over the $2 quota'],
  [/^expands to the same spec as (\S+); to run a spec again, raise replicas$/, 'same as $1; raise replicas instead'],
];

export function messages(r: Evaluation): Msg[] {
  const shorter = (t: string) => SHORTER.reduce((x, [re, to]) => x.replace(re, to), t);
  const split = (m: string, error: boolean): Msg => {
    const i = m.indexOf(': ');
    return { path: m.slice(0, i), text: shorter(m.slice(i + 2)), error };
  };
  return [...r.errors.map((m) => split(m, true)), ...r.warnings.map((m) => split(m, false))];
}

/** Messages about exactly this path, or one of its list items (densities[3]). */
export const at = (msgs: Msg[], path: string) => msgs.filter((m) => m.path === path || m.path.startsWith(`${path}[`));

/** Messages about this path or anything under it. */
export const under = (msgs: Msg[], path: string) =>
  msgs.filter((m) => m.path === path || m.path.startsWith(`${path}.`) || m.path.startsWith(`${path}[`));

/** The runs and cost, even while the name, question or why still fail: they are replaced by placeholders, which
 * change neither. Null when the specs themselves don't resolve. */
export function estimateOf(def: Obj, r: Evaluation): Plan | null {
  if (r.estimate) return r.estimate;
  const d: Obj = { ...def, name: 'draft', question: 'a question long enough' };
  delete d.why;
  return evaluate(d).estimate;
}

// ------------------------------------------------------------------ words
const HYPERVISOR: Record<string, string> = { firecracker: 'Firecracker', 'cloud-hypervisor': 'Cloud Hypervisor' };
const n = (x: number) => x.toLocaleString('en-US');

/** A value as the builder shows it. */
export function show(path: string, value: unknown): string {
  if (path === 'workload.chromium_extra_flags') return Array.isArray(value) && value.length ? value.join(' ') : 'none';
  if (value === undefined) return '–';
  if (typeof value === 'boolean') return value ? 'on' : 'off';
  if (Array.isArray(value)) return value.join(', ');
  if (path === 'hypervisor.name' && typeof value === 'string') return HYPERVISOR[value] ?? value;
  const unit = fieldAt(path)?.unit;
  if (typeof value === 'number') return unit && unit !== 'microVMs' ? `${n(value)} ${unit}` : n(value);
  return String(value);
}

/** An option in a select: "m8i.2xlarge · 8 vCPUs, 32 GiB", "Cloud Hypervisor". */
export function optionLabel(path: string, value: unknown): string {
  if (typeof value === 'string' && path.endsWith('instance_type') && TYPES[value]) {
    const t = TYPES[value];
    return `${value} · ${t.vcpus} vCPUs, ${t.memory_gib} GiB${t.metal ? ', metal' : ''}`;
  }
  return show(path, value);
}

/** What follows from an instance type beyond its size (the select shows that): "nested · $0.85/h", "≈" when the
 * price is an estimate. */
export function hostFacts(type: unknown): string {
  if (typeof type !== 'string' || !TYPES[type]) return '';
  const t = TYPES[type];
  return `${t.metal ? 'metal' : 'nested'} · ${t.estimated ? '≈' : ''}$${t.usd_per_hour.toFixed(2)}/h`;
}

/** The densities at which a spec's microVMs take all of its host's vCPUs and all of its memory. */
export function densityFit(spec: unknown): { cpu: number; mem: number } | null {
  if (!isObj(spec) || !isObj(spec.worker_host) || !isObj(spec.microvm)) return null;
  const t = TYPES[String(spec.worker_host.instance_type)];
  const { vcpus, memory_mib } = spec.microvm;
  if (!t || typeof vcpus !== 'number' || typeof memory_mib !== 'number' || vcpus <= 0 || memory_mib <= 0) return null;
  return { cpu: t.vcpus / vcpus, mem: (t.memory_gib * 1024) / memory_mib };
}

/** The next free name in a numbered series: nested-sizes-1 → nested-sizes-2. */
export function nextName(name: string, taken: string[]): string {
  const m = /^(.*?)-(\d+)$/.exec(name);
  const stem = m ? m[1] : name;
  for (let k = m ? Number(m[2]) + 1 : 2; ; k++) if (!taken.includes(`${stem}-${k}`)) return `${stem}-${k}`;
}

// ------------------------------------------------------------------ help
// What the ⓘ beside a label says (D72), for the inputs and figures that aren't schema fields; the schema fields'
// explanations are HELP in lib/campaign.ts. One or two plain sentences each.
const dollars = (x: number) => usd(x).replace(/\.00$/, '');

export const START_HELP =
  'A campaign to edit: one that has run, or an example that hasn’t. Your draft is kept in this browser until you start from another.';

export const SPEC_NAME_HELP =
  'The spec’s name. Its runs are named after it, one per replica: name-r1, name-r2, and so on.';

export const ADD_HELP =
  'Sets one input differently for this spec: the worker host, the hypervisor, the microVM, the densities or the extra Chromium flags. Every other input stays as the base spec has it.';
export const WHY_HELP = 'Optional: one sentence on why this spec is in the campaign, shown beside the spec.';

export const REVIEW_HELP = {
  runs: 'One run for each spec and replica. Each run gets its own worker host and support host and tests every density in its spec.',
  time: 'About how long from launching the hosts until every run has uploaded its results. Runs start together when they fit the vCPU quota, otherwise in waves.',
  expected: 'What the hosts cost at on-demand prices for as long as each run is expected to take.',
  worst: `What the hosts cost if every one runs until its shutdown timer. A campaign must stay under the ${dollars(LIMITS.campaign_worst_case_usd)} limit.`,
  vcpus: `The most vCPUs running at once, worker and support hosts together, against the account’s quota of ${LIMITS.vcpu_quota}. Runs that don’t fit wait for a later wave.`,
  cost: `The worst case against the ${dollars(LIMITS.campaign_worst_case_usd)} limit for one campaign.`,
  changes: 'The inputs that differ between the specs. Everything not listed is the same in every spec.',
  hosts: 'One box per worker host: a row for each spec, a box for each replica.',
} as const;
