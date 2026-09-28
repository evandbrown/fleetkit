// A campaign definition: expand, validate, diff, cost and quota, exactly as experiments/schema/expand.py does them,
// plus the few helpers the experiment builder needs to edit one. Both implementations must pass
// experiments/schema/tests/cases.json (tests/unit/campaign.test.ts runs it here). The schemas, instance types,
// limits and allowed Chromium flags are the same files expand.py reads, bundled at build time.
import specSchema from '../../../experiments/schema/spec.schema.json';
import campaignSchema from '../../../experiments/schema/campaign.schema.json';
import typesDoc from '../../../experiments/schema/instance-types.json';
import limitsDoc from '../../../experiments/schema/limits.json';
import flagsDoc from '../../../experiments/schema/chromium-flags.json';

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Obj = { [k: string]: Json };
type Schema = { [k: string]: any };

export interface InstanceType {
  vcpus: number;
  memory_gib: number;
  metal: boolean;
  usd_per_hour: number;
  estimated: boolean;
}

export const SCHEMAS: Record<string, Schema> = {
  'spec.schema.json': specSchema,
  'campaign.schema.json': campaignSchema,
};
export const TYPES = typesDoc.types as Record<string, InstanceType>;
export const LIMITS = limitsDoc;

/** A value rule in chromium-flags.json: no value, a whole number in a range, listed features, or listed V8 flags. */
interface FlagRule {
  value: 'none' | 'integer' | 'features' | 'v8';
  minimum?: number;
  maximum?: number;
  why: string;
  features?: Record<string, string>;
  v8?: Record<string, FlagRule>;
}
/** The Chromium flags a spec may add (workload.chromium_extra_flags), each with why it is allowed. */
export const FLAGS = flagsDoc as unknown as {
  base: string[];
  start_page: string;
  max_total_chars: number;
  allowed: Record<string, FlagRule>;
  refused: Record<string, string>;
};

const has = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k);
export const isObj = (v: unknown): v is Obj => v !== null && typeof v === 'object' && !Array.isArray(v);
const clone = <T>(v: T): T => (v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T));

// ------------------------------------------------------------------ reading JSON
export class InputError extends Error {}

/** JSON.parse, except a key repeated in one object is an error rather than last-wins (as expand.py). */
export function parseJson(text: string): Json {
  let i = 0;
  const NUM = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
  const ws = () => {
    while (i < text.length && ' \t\n\r'.includes(text[i])) i++;
  };
  const fail = (what: string): never => {
    throw new InputError(`${what} at character ${i + 1}`);
  };
  function str(): string {
    const start = i++;
    while (i < text.length && text[i] !== '"') i += text[i] === '\\' ? 2 : 1;
    if (i >= text.length) fail('text with no closing quote');
    i++;
    try {
      return JSON.parse(text.slice(start, i)) as string;
    } catch {
      return fail('bad text');
    }
  }
  function value(): Json {
    ws();
    const c = text[i];
    if (c === '{') {
      i++;
      const out: Obj = {};
      ws();
      if (text[i] === '}') return i++, out;
      for (;;) {
        ws();
        if (text[i] !== '"') fail('expected a key');
        const k = str();
        if (has(out, k)) throw new InputError(`the key '${k}' appears twice in one object`);
        ws();
        if (text[i++] !== ':') fail("expected ':'");
        Object.defineProperty(out, k, { value: value(), enumerable: true, writable: true, configurable: true });
        ws();
        if (text[i] === ',') i++;
        else if (text[i] === '}') return i++, out;
        else fail("expected ',' or '}'");
      }
    }
    if (c === '[') {
      i++;
      const out: Json[] = [];
      ws();
      if (text[i] === ']') return i++, out;
      for (;;) {
        out.push(value());
        ws();
        if (text[i] === ',') i++;
        else if (text[i] === ']') return i++, out;
        else fail("expected ',' or ']'");
      }
    }
    if (c === '"') return str();
    NUM.lastIndex = i;
    const m = NUM.exec(text);
    if (m) {
      i += m[0].length;
      return Number(m[0]);
    }
    for (const [w, v] of [['true', true], ['false', false], ['null', null]] as const) {
      if (text.startsWith(w, i)) {
        i += w.length;
        return v;
      }
    }
    return fail(i >= text.length ? 'the JSON ends early' : 'not JSON');
  }
  const v = value();
  ws();
  if (i < text.length) fail('more text after the JSON');
  return v;
}

// ------------------------------------------------------------------ small helpers
/** A value in a message, written the way expand.py writes it: [1, 2], true, 1000. */
export function fmt(v: unknown): string {
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (v === null || v === undefined) return 'null';
  if (Array.isArray(v)) return `[${v.map(fmt).join(', ')}]`;
  if (isObj(v)) return `{${Object.entries(v).map(([k, x]) => `${k}: ${fmt(x)}`).join(', ')}}`;
  return String(v);
}

/** JSON equality: true is not 1, key order doesn't matter. */
export function same(a: unknown, b: unknown): boolean {
  if (typeof a === 'boolean' || typeof b === 'boolean') return a === b;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => same(x, b[i]));
  if (isObj(a) && isObj(b)) {
    const ka = Object.keys(a);
    return ka.length === Object.keys(b).length && ka.every((k) => has(b, k) && same(a[k], b[k]));
  }
  return a === b;
}

function join(path: string, key: string | number): string {
  if (typeof key === 'number') return `${path}[${key}]`;
  return path ? `${path}.${key}` : key;
}

/** {"a": {"b": 1}, "c": [1, 2]} -> {"a.b": 1, "c": [1, 2]}; lists are values. */
export function flatten(obj: Obj, prefix = ''): Record<string, Json> {
  const out: Record<string, Json> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (isObj(v)) Object.assign(out, flatten(v, `${prefix}${k}.`));
    else out[`${prefix}${k}`] = v;
  }
  return out;
}

/** {path: default} for the spec fields a spec may leave out, which then stand at their default:
 * procedure.release_after_ready_s, workload.chromium_extra_flags, microvm.console and microvm.memory_pages
 * (expand.py optional_defaults). */
export const OPTIONAL: Record<string, Json> = (() => {
  const s = specSchema as Schema;
  const out: Record<string, Json> = {};
  for (const [k, node] of Object.entries(s.properties as Record<string, Schema>)) {
    const d = s.$defs[k];
    if (d.type !== 'object') {
      if (!s.required.includes(k) && has(d, 'default')) out[k] = d.default;
      continue;
    }
    const req: string[] = s.required.includes(k) ? (node.required ?? []) : [];
    for (const [f, fs] of Object.entries(d.properties as Record<string, Schema>)) {
      if (!req.includes(f) && has(fs, 'default')) out[`${k}.${f}`] = fs.default;
    }
  }
  return out;
})();

/** A spec's leaves, flattened, with every optional field it leaves out at its default (after the rest): what it runs. */
export function filled(spec: Obj): Record<string, Json> {
  const out = flatten(spec);
  for (const [k, v] of Object.entries(OPTIONAL)) if (!has(out, k)) out[k] = clone(v);
  return out;
}

/** A named spec: the base with its changes merged in. Objects merge key by key; lists and values replace. */
export function merge(base: Obj, changes: Obj): Obj {
  const out = clone(base);
  for (const [k, v] of Object.entries(changes)) {
    out[k] = isObj(v) && isObj(out[k]) ? merge(out[k] as Obj, v) : clone(v);
  }
  return out;
}

/** Python's round(x, 2), to the cent. */
const cents = (x: number) => Number(x.toFixed(2));

export function usd(x: number): string {
  return `$${x.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// ------------------------------------------------------------------ schema validation
// The subset of JSON Schema 2020-12 the two schemas use, plus x-message: when a subschema that carries one fails,
// its errors are replaced by that one message. Messages start with the field.
const KIND: Record<string, string> = {
  object: 'an object',
  array: 'a list',
  string: 'text',
  boolean: 'true or false',
  number: 'a number',
  integer: 'a whole number',
};
const TYPE_OK: Record<string, (v: unknown) => boolean> = {
  object: isObj,
  array: Array.isArray,
  string: (v) => typeof v === 'string',
  boolean: (v) => typeof v === 'boolean',
  number: (v) => typeof v === 'number',
  integer: (v) => typeof v === 'number' && Number.isInteger(v),
};

function resolve(ref: string, doc: string): [Schema, string] {
  const at = ref.indexOf('#');
  const name = at < 0 ? ref : ref.slice(0, at);
  const frag = at < 0 ? '' : ref.slice(at + 1);
  const d = name || doc;
  let node = SCHEMAS[d];
  for (const part of frag.split('/').filter(Boolean)) node = node[part];
  return [node, d];
}

export function validate(inst: unknown, schema: Schema, path = '', doc = 'spec.schema.json'): string[] {
  const errs: string[] = [];
  v(inst, schema, path, doc, errs);
  return errs;
}

function v(inst: unknown, s: Schema, path: string, doc: string, errs: string[]) {
  if (has(s, 'x-message')) {
    const sub: string[] = [];
    check(inst, s, path, doc, sub);
    if (sub.length) errs.push(`${path || '(top)'}: ${s['x-message']}`);
    return;
  }
  check(inst, s, path, doc, errs);
}

function check(inst: unknown, s: Schema, path: string, doc: string, errs: string[]) {
  const where = path || '(top)';
  if (has(s, '$ref')) {
    const [sub, d] = resolve(s.$ref, doc);
    v(inst, sub, path, d, errs);
  }
  for (const sub of s.allOf ?? []) v(inst, sub, path, doc, errs);
  if (has(s, 'not') && validate(inst, s.not, path, doc).length === 0) errs.push(`${where}: not allowed`);
  if (has(s, 'if') && has(s, 'then') && validate(inst, s.if, path, doc).length === 0) v(inst, s.then, path, doc, errs);
  const t = s.type as string | undefined;
  if (t && !TYPE_OK[t](inst)) {
    errs.push(`${where}: must be ${KIND[t]}`);
    return;
  }
  if (has(s, 'const') && !same(inst, s.const)) errs.push(`${where}: must be ${fmt(s.const)}`);
  if (has(s, 'enum') && !s.enum.some((e: unknown) => same(inst, e))) {
    errs.push(`${where}: must be one of ${s.enum.map(fmt).join(', ')}`);
  }
  if (typeof inst === 'number') {
    if (has(s, 'minimum') && inst < s.minimum) errs.push(`${where}: must be at least ${fmt(s.minimum)}`);
    if (has(s, 'maximum') && inst > s.maximum) errs.push(`${where}: must be at most ${fmt(s.maximum)}`);
    if (has(s, 'exclusiveMinimum') && inst <= s.exclusiveMinimum) {
      errs.push(`${where}: must be more than ${fmt(s.exclusiveMinimum)}`);
    }
    if (has(s, 'multipleOf') && inst % s.multipleOf !== 0) errs.push(`${where}: must be a multiple of ${fmt(s.multipleOf)}`);
  }
  if (typeof inst === 'string') {
    const n = [...inst].length; // code points, as Python counts them
    if (has(s, 'minLength') && n < s.minLength) errs.push(`${where}: must be at least ${s.minLength} characters`);
    if (has(s, 'maxLength') && n > s.maxLength) errs.push(`${where}: must be at most ${s.maxLength} characters`);
    if (has(s, 'pattern') && !new RegExp(s.pattern).test(inst)) errs.push(`${where}: must match ${s.pattern}`);
  }
  if (Array.isArray(inst)) {
    if (has(s, 'minItems') && inst.length < s.minItems) {
      errs.push(`${where}: needs at least ${s.minItems} ${s.minItems === 1 ? 'value' : 'values'}`);
    }
    if (has(s, 'maxItems') && inst.length > s.maxItems) errs.push(`${where}: allows at most ${s.maxItems} values`);
    if (has(s, 'items')) inst.forEach((item, i) => v(item, s.items, join(path, i), doc, errs));
  }
  if (isObj(inst)) {
    for (const k of s.required ?? []) if (!has(inst, k)) errs.push(`${join(path, k)}: required`);
    const n = Object.keys(inst).length;
    if (has(s, 'minProperties') && n < s.minProperties) errs.push(`${where}: needs at least ${s.minProperties}`);
    if (has(s, 'maxProperties') && n > s.maxProperties) errs.push(`${where}: allows at most ${s.maxProperties}`);
    const props: Schema = s.properties ?? {};
    const extra = has(s, 'additionalProperties') ? s.additionalProperties : true;
    for (const [k, x] of Object.entries(inst)) {
      if (has(s, 'propertyNames')) v(k, s.propertyNames, join(path, k), doc, errs);
      if (has(props, k)) v(x, props[k], join(path, k), doc, errs);
      else if (extra === false) errs.push(`${join(path, k)}: not a field`);
      else if (isObj(extra)) v(x, extra, join(path, k), doc, errs);
    }
  }
}

// ------------------------------------------------------------------ extra Chromium flags
/** --name of --name=value. */
export const flagName = (flag: string) => flag.split('=', 1)[0];

function partition(text: string): [string, boolean, string] {
  const i = text.indexOf('=');
  return i < 0 ? [text, false, ''] : [text.slice(0, i), true, text.slice(i + 1)];
}

/** A number is written one way only (no leading zeros), so =2 and =02 can't pass as two different specs. */
function valueProblems(name: string, hasValue: boolean, value: string, rule: FlagRule): string[] {
  if (rule.value === 'none') return hasValue ? [`${name} takes no value`] : [];
  const [lo, hi] = [rule.minimum!, rule.maximum!];
  if (!hasValue || !/^(0|[1-9][0-9]*)$/.test(value) || Number(value) < lo || Number(value) > hi) {
    return [`${name} takes a whole number from ${lo} to ${hi}, as ${name}=N`];
  }
  return [];
}

/** Why one extra Chromium flag isn't allowed (chromium-flags.json); empty when it is (expand.py flag_problems). */
export function flagProblems(flag: string): string[] {
  const [name, eq, value] = partition(flag);
  if (FLAGS.base.some((b) => flagName(b) === name)) return [`${name} is one of the base flags every run has`];
  if (has(FLAGS.refused, name)) return [`${name} is refused: ${FLAGS.refused[name]}`];
  if (!has(FLAGS.allowed, name)) return [`${name} is not on the allowed list in chromium-flags.json`];
  const rule = FLAGS.allowed[name];
  if (rule.value === 'features') {
    if (!eq || !value) return [`${name} takes features, comma-separated, as ${name}=BackForwardCache`];
    const out: string[] = [];
    const seen = new Set<string>();
    const features = rule.features!;
    for (const feat of value.split(',')) {
      if (!has(features, feat)) {
        out.push(`${name}: ${feat || 'an empty name'} is not an allowed feature (allowed: ${Object.keys(features).join(', ')})`);
      } else if (seen.has(feat)) out.push(`${name}: ${feat} appears twice`);
      seen.add(feat);
    }
    return out;
  }
  if (rule.value === 'v8') {
    if (!eq || !value) return [`${name} takes V8 flags, space-separated, as ${name}=--jitless`];
    const out: string[] = [];
    const seen = new Set<string>();
    const v8 = rule.v8!;
    for (const tok of value.split(' ')) {
      const [vname, veq, vval] = partition(tok);
      if (!has(v8, vname)) {
        out.push(`${name}: ${tok || 'an empty flag'} is not an allowed V8 flag (allowed: ${Object.keys(v8).join(', ')})`);
        continue;
      }
      out.push(...valueProblems(vname, veq, vval, v8[vname]).map((p) => `${name}: ${p}`));
      if (seen.has(vname)) out.push(`${name}: ${vname} appears twice`);
      seen.add(vname);
    }
    return out;
  }
  return valueProblems(name, eq, value, rule);
}

/** Errors for workload.chromium_extra_flags, each starting with its field. */
export function flagsProblems(flags: string[]): string[] {
  const errs: string[] = [];
  const seen = new Set<string>();
  flags.forEach((flag, i) => {
    const where = `workload.chromium_extra_flags[${i}]`;
    errs.push(...flagProblems(flag).map((p) => `${where}: ${p}`));
    const name = flagName(flag);
    if (seen.has(name)) errs.push(`${where}: ${name} appears twice; give each flag once`);
    seen.add(name);
  });
  const total = flags.reduce((a, f) => a + [...f].length, 0);
  if (total > FLAGS.max_total_chars) {
    errs.push(`workload.chromium_extra_flags: the flags add up to ${total} characters, more than ${FLAGS.max_total_chars}`);
  }
  return errs;
}

// ------------------------------------------------------------------ rules the schema can't state
/** Errors and warnings for one complete, schema-valid spec; paths are relative to the spec. */
export function checkSpec(spec: any): [string[], string[]] {
  const errs: string[] = [];
  const warns: string[] = [];
  const d: number[] = spec.densities;
  if (d.some((a, i) => i + 1 < d.length && d[i + 1] <= a)) {
    errs.push(`densities: must be in increasing order, each density once, got ${fmt(d)}`);
  }
  const c = spec.criteria;
  if (c.step_p50_target_ms > c.step_p95_target_ms) {
    errs.push('criteria.step_p50_target_ms: must be at most criteria.step_p95_target_ms');
  }
  if (c.step_p95_target_ms >= c.step_timeout_ms) errs.push('criteria.step_p95_target_ms: must be below criteria.step_timeout_ms');
  if (c.step_timeout_ms > c.task_timeout_ms) errs.push('criteria.step_timeout_ms: must be at most criteria.task_timeout_ms');
  if (c.task_p95_target_ms >= c.task_timeout_ms) errs.push('criteria.task_p95_target_ms: must be below criteria.task_timeout_ms');
  errs.push(...flagsProblems(spec.workload?.chromium_extra_flags ?? []));
  const host = TYPES[spec.worker_host.instance_type];
  const top = Math.max(...d);
  const gib = (top * spec.microvm.memory_mib) / 1024;
  if (gib >= host.memory_gib) {
    warns.push(
      `densities: at density ${top} the microVMs' memory adds up to ${fmt(gib)} GiB against the worker ` +
        `host's ${host.memory_gib} GiB, so memory may run out before CPU`,
    );
  }
  return [errs, warns];
}

export const hostKind = (instanceType: string): 'metal' | 'nested' => (TYPES[instanceType].metal ? 'metal' : 'nested');

/** Expected length of a run that tests every density: setup, the trials, then upload and teardown. Every trial,
 * labelled ones too, waits settle_s before it and release_after_ready_s (absent: 0) inside it (expand.py run_minutes). */
export function runMinutes(spec: any): number {
  const t = LIMITS.run_time_estimate;
  const p = spec.procedure;
  const d: number[] = spec.densities;
  const wait: number = p.release_after_ready_s ?? 0;
  const trialS = (n: number) => p.settle_s + wait + t.trial_base_s + t.trial_s_per_microvm * n;
  const sum = (xs: number[]) => xs.reduce((a, x) => a + trialS(x), 0);
  const trialsS = t.labelled_trials * trialS(1) + p.trials_per_density * sum(d) + p.boundary_trials * sum(d.slice(-2));
  const kind = hostKind(spec.worker_host.instance_type);
  return Math.ceil(t.setup_minutes[kind] + t.finish_minutes + trialsS / 60);
}

/** First-fit decreasing: groups of runs whose vCPUs fit under the quota together. */
export function packWaves(runs: { run: string; vcpus: number }[], quota: number): { runs: string[]; vcpus: number }[] {
  const waves: { runs: string[]; vcpus: number }[] = [];
  for (const r of [...runs].sort((a, b) => b.vcpus - a.vcpus)) {
    if (r.vcpus > quota) continue;
    const w = waves.find((w) => w.vcpus + r.vcpus <= quota);
    if (w) {
      w.runs.push(r.run);
      w.vcpus += r.vcpus;
    } else waves.push({ runs: [r.run], vcpus: r.vcpus });
  }
  return waves;
}

export interface Differs {
  field: string;
  values: Record<string, Json | undefined>;
}

/** Every field whose value isn't the same in every spec. An optional field a spec leaves out is at its default. */
export function differing(specs: [string, Obj][]): Differs[] {
  const flat = specs.map(([name, spec]) => [name, filled(spec)] as const);
  const keys = [...new Set(flat.flatMap(([, f]) => Object.keys(f)))];
  const out: Differs[] = [];
  for (const k of keys) {
    const vals = flat.map(([, f]) => (has(f, k) ? f[k] : undefined));
    if (vals.slice(1).some((x) => !same(vals[0], x))) {
      out.push({ field: k, values: Object.fromEntries(flat.map(([n, f]) => [n, has(f, k) ? f[k] : undefined])) });
    }
  }
  return out;
}

// ------------------------------------------------------------------ a campaign
export interface Run {
  run: string;
  path: string;
  spec_name: string;
  replica: number;
  worker_instance_type: string;
  support_instance_type: string;
  host_kind: 'metal' | 'nested';
  vcpus: number;
  usd_per_hour: number;
  minutes_expected: number;
  spec: Obj;
}

export interface Plan {
  campaign: string;
  question: string;
  replicas: number;
  shutdown_after_minutes: number;
  runs: Run[];
  differs: Differs[];
  expected_usd: number;
  worst_case_usd: number;
  prices_estimated: boolean;
  vcpu_quota: number;
  vcpus_all_at_once: number;
  waves: string[][];
  wave_vcpus: number[];
  too_big_for_quota: string[];
  minutes_in_waves: number;
}

export interface Evaluation {
  valid: boolean;
  errors: string[];
  warnings: string[];
  specs: [string, Obj][];
  /** Only when the campaign is valid, as in expand.py. */
  plan: Plan | null;
  /** The same plan whenever the specs resolved, even if a cost or time limit then failed: the builder shows it. */
  estimate: Plan | null;
}

export function evaluate(camp: any, quota: number = LIMITS.vcpu_quota): Evaluation {
  const out: Evaluation = { valid: false, errors: [], warnings: [], specs: [], plan: null, estimate: null };
  const errs = out.errors;
  const warns = out.warnings;
  errs.push(...validate(camp, SCHEMAS['campaign.schema.json'], '', 'campaign.schema.json'));
  if (errs.length) return out;
  const base = camp.base as Obj;
  const [baseErrs] = checkSpec(base);
  errs.push(...baseErrs.map((e) => `base.${e}`));
  const specs: [string, Obj][] = [];
  for (const [name, changes] of Object.entries(camp.specs as Record<string, Obj>)) {
    const spec = merge(base, changes);
    const se = validate(spec, SCHEMAS['spec.schema.json']);
    errs.push(...se.map((e) => `specs.${name}.${e}`));
    if (!se.length) {
      const [re, rw] = checkSpec(spec);
      errs.push(...re.filter((e) => !baseErrs.includes(e)).map((e) => `specs.${name}.${e}`));
      warns.push(...rw.map((w) => `specs.${name}.${w}`));
    }
    specs.push([name, spec]);
  }
  for (const k of Object.keys(camp.why ?? {})) {
    if (!has(camp.specs, k)) errs.push(`why.${k}: no spec named ${k}`);
  }
  specs.forEach(([a, sa], i) => {
    for (const [b, sb] of specs.slice(i + 1)) {
      if (same(filled(sa), filled(sb))) {
        errs.push(`specs.${b}: expands to the same spec as ${a}; to run a spec again, raise replicas`);
      }
    }
  });
  if (errs.length) return out;
  out.specs = specs;

  const name: string = camp.name;
  const reps: number = camp.replicas;
  const timer: number = camp.shutdown_after_minutes;
  const runs: Omit<Run, 'spec'>[] = [];
  for (const [s, spec] of specs as [string, any][]) {
    const worker: string = spec.worker_host.instance_type;
    const support: string = spec.support_host.instance_type;
    const minutes = runMinutes(spec);
    for (let k = 1; k <= reps; k++) {
      runs.push({
        run: `${s}-r${k}`,
        path: `results/${name}/${s}-r${k}`,
        spec_name: s,
        replica: k,
        worker_instance_type: worker,
        support_instance_type: support,
        host_kind: hostKind(worker),
        vcpus: TYPES[worker].vcpus + TYPES[support].vcpus,
        usd_per_hour: TYPES[worker].usd_per_hour + TYPES[support].usd_per_hour,
        minutes_expected: minutes,
      });
    }
  }
  const expected = cents(runs.reduce((a, r) => a + (r.usd_per_hour * r.minutes_expected) / 60, 0));
  const worst = cents(runs.reduce((a, r) => a + (r.usd_per_hour * timer) / 60, 0));
  const limit = LIMITS.campaign_worst_case_usd;
  if (worst > limit) {
    errs.push(
      `shutdown_after_minutes: the worst case is ${usd(worst)}, above the ${usd(limit)} limit for one ` +
        'campaign; shorten the timer, or run fewer specs or replicas',
    );
  }
  for (const [s, spec] of specs) {
    const m = runMinutes(spec);
    if (m > timer) {
      errs.push(
        `shutdown_after_minutes: a run of ${s} is expected to take about ${m} minutes, longer than the ` +
          `${timer}-minute timer`,
      );
    }
  }
  if (reps === 1 && specs.length > 1) {
    warns.push('replicas: with 1 replica per spec there is no spread between hosts to judge a difference against');
  }
  const tooBig = new Map<string, number>();
  for (const r of runs) if (r.vcpus > quota && !tooBig.has(r.spec_name)) tooBig.set(r.spec_name, r.vcpus);
  for (const [s, n] of tooBig) {
    warns.push(
      `specs.${s}.worker_host.instance_type: a run needs ${n} vCPUs with its support host, more than ` +
        `the quota of ${quota}; it can't launch until the quota grows`,
    );
  }
  const waves = packWaves(runs, quota);
  const minutes = Object.fromEntries(runs.map((r) => [r.run, r.minutes_expected]));
  const specOf = Object.fromEntries(specs);
  out.estimate = {
    campaign: name,
    question: camp.question,
    replicas: reps,
    shutdown_after_minutes: timer,
    runs: runs.map((r) => ({ ...r, spec: specOf[r.spec_name] })),
    differs: differing(specs),
    expected_usd: expected,
    worst_case_usd: worst,
    prices_estimated: runs.some((r) => TYPES[r.worker_instance_type].estimated || TYPES[r.support_instance_type].estimated),
    vcpu_quota: quota,
    vcpus_all_at_once: runs.reduce((a, r) => a + r.vcpus, 0),
    waves: waves.map((w) => w.runs),
    wave_vcpus: waves.map((w) => w.vcpus),
    too_big_for_quota: runs.filter((r) => r.vcpus > quota).map((r) => r.run),
    minutes_in_waves: waves.reduce((a, w) => a + Math.max(...w.runs.map((x) => minutes[x])), 0),
  };
  if (errs.length) return out;
  out.valid = true;
  out.plan = out.estimate;
  return out;
}

// ------------------------------------------------------------------ for the builder
export interface Field {
  path: string; // within a spec, "microvm.memory_mib", or a campaign field, "replicas"
  section: string; // "microvm"
  label: string;
  unit?: string;
  tier: 'basic' | 'advanced' | 'rare';
  schema: Schema;
  description: string;
  /** What the builder's ⓘ beside the label says: one or two plain sentences for a reader new to the experiment. */
  help: string;
  /** A named spec may change it: the worker host, the hypervisor, the microVM, the densities and the workload's
   * extra Chromium flags. */
  variable: boolean;
  /** A spec may leave it out, and then it is the schema's default: procedure.release_after_ready_s,
   * workload.chromium_extra_flags, microvm.console and microvm.memory_pages, added after the first campaigns ran.
   * Every other field is required. */
  optional: boolean;
}

const CHANGES = campaignSchema.$defs.spec_changes.properties as Record<string, Schema>;
export const VARIABLE_SECTIONS = Object.keys(CHANGES).filter((k) => has(CHANGES[k], '$ref'));

/** Shorter labels than the schema's, where the builder has less room: the glossary word, or the metric's name. */
const SHORT_LABEL: Record<string, string> = {
  'worker_host.instance_type': 'Worker host',
  'support_host.instance_type': 'Support host',
  'criteria.step_p50_target_ms': 'Step p50 target',
  'criteria.step_timeout_ms': 'Step timeout',
  'criteria.task_timeout_ms': 'Task timeout',
  'procedure.settle_s': 'Idle before trial',
  name: 'Name',
};

/** Each field's explanation (D72): the schema's description, rewritten for a reviewer who hasn't read the method.
 * The label stays short; this carries the meaning. One or two sentences each. */
export const HELP: Record<string, string> = {
  'worker_host.instance_type':
    'The EC2 instance that runs the microVMs: the host being measured. Metal types run the hypervisor on the hardware itself; the others run it nested inside a virtual machine.',
  'hypervisor.name': 'The software on the worker host that starts and runs each microVM: Firecracker or Cloud Hypervisor.',
  'hypervisor.virtio_transport':
    "How each microVM's virtual disk and network devices connect to its guest kernel. Firecracker can use mmio or pci; Cloud Hypervisor needs pci.",
  'hypervisor.virtio_rng':
    'Gives each microVM a virtual device that supplies random numbers from the host. Optional for Firecracker; Cloud Hypervisor always has one.',
  // The guest fields say what the design recommends and why (D91); the recommendation is what a new campaign starts
  // with, not the schema's default, which keeps the meaning of the specs recorded before the field existed.
  'microvm.vcpus':
    'Virtual CPUs given to each microVM, which runs one headless Chrome and one browser task per trial. Recommended: 1, which with 1 GiB of memory cost 15% less per task than 2 vCPUs and 2 GiB, with every task inside its targets.',
  'microvm.memory_mib':
    "Memory given to each microVM, in MiB (1,024 MiB is 1 GiB); if the microVMs' memory adds up to more than the host's, memory runs out before CPU. Recommended: 1,024, since a guest touched at most 0.7 GiB during its task and no task failed for memory.",
  'microvm.console':
    'What the guest kernel prints on its serial console at boot: the whole log (verbose), critical only (quiet), or those and no keyboard probe (quiet-i8042); each character costs host CPU. Recommended: quiet-i8042, about 10% cheaper per task (guest_boot_tuning).',
  'microvm.memory_pages':
    'How the host backs a microVM’s memory: 4k asks for nothing, thp for transparent huge pages, granted if the host’s mode is madvise or always. Recommended: thp, 24–40% cheaper per task on nested hosts (guest_boot_tuning), about 10% on metal (tuned_guest_metal).',
  densities:
    'How many microVMs each trial starts at once; the run tests them in order and stops at the first that fails. Space them closely where you expect the limit: the result is only as precise as the gap between the last pass and the first failure.',
  'criteria.step_p50_target_ms':
    'In each trial, the median time of each of the five steps must be at or under this, or the trial fails. The steps are home, search, open product, add to cart and verify cart.',
  'criteria.step_p95_target_ms':
    'In each trial, the 95th-percentile time of each of the five steps must be at or under this, or the trial fails.',
  'criteria.task_p95_target_ms':
    'In each trial, the 95th-percentile time of the whole five-step task must be at or under this, or the trial fails.',
  'criteria.ready_timeout_s': 'Every microVM must report its browser ready within this many seconds of starting, or the trial fails.',
  'criteria.step_timeout_ms':
    'A step still running after this long fails its task, and a failed task fails the trial. It must be above the step p95 target.',
  'criteria.task_timeout_ms':
    'A task still running after this long fails, and so does its trial. It must be above the task p95 target.',
  'procedure.trials_per_density':
    'How many trials run at each density on the way up. Each trial starts fresh microVMs, and all must pass before the run moves up.',
  'procedure.boundary_trials':
    'Once the run stops, extra trials at the highest density that passed and at the lowest that failed, to check the result holds.',
  'procedure.settle_s': "Seconds the worker host sits idle before each trial, so the last trial's cleanup can't slow the next.",
  'procedure.release_after_ready_s':
    'Seconds each trial waits, once every microVM is ready, before starting their tasks together. 0 starts them at once, while browsers may still be settling; a wait tests a warm pool, and no task’s time includes it.',
  'support_host.instance_type':
    'The EC2 instance that serves the test shopping site and collects telemetry, one per run. It grows with the worker host so it is never what runs out.',
  'workload.chromium_extra_flags':
    'Chromium flags added to the ones every run has, one per line; only those in chromium-flags.json, which change how Chromium uses memory and CPU, not what a task does. Recommended: the one a new campaign starts with, 8–10% cheaper per task (chromium_no_preload).',
  name: 'The campaign’s short name; its results are saved under results/<name>. 2–40 lowercase letters, digits or hyphens, starting with a letter.',
  question: 'The one question this campaign’s runs answer together, in a line. It heads the campaign on Results.',
  replicas:
    'How many runs each spec gets, each on its own worker host. Two or more show how much a result varies from host to host.',
  shutdown_after_minutes:
    'Every host shuts itself down this many minutes after it boots, whatever else happens. The worst-case cost assumes every host runs this long.',
};

/** Every field of a spec, in schema order, from its x-builder annotations. */
export const SPEC_FIELDS: Field[] = Object.keys(specSchema.properties).flatMap((section) => {
  const node = (specSchema.$defs as Record<string, Schema>)[section];
  const required: string[] = ((specSchema.properties as Record<string, Schema>)[section].required as string[]) ?? [];
  const make = (path: string, f: Schema): Field => ({
    path,
    section,
    label: SHORT_LABEL[path] ?? f['x-builder'].label,
    unit: f['x-builder'].unit,
    tier: f['x-builder'].tier,
    schema: f,
    description: f.description,
    help: HELP[path] ?? f.description,
    variable: VARIABLE_SECTIONS.includes(section),
    optional: path.includes('.') ? !required.includes(path.slice(section.length + 1)) : !specSchema.required.includes(section),
  });
  return node.type === 'object'
    ? Object.entries(node.properties as Record<string, Schema>).map(([k, f]) => make(`${section}.${k}`, f))
    : [make(section, node)];
});

/** The campaign's own fields: name, question, replicas, the shutdown timer. */
export const CAMPAIGN_FIELDS: Record<string, Field> = Object.fromEntries(
  ['name', 'question', 'replicas', 'shutdown_after_minutes'].map((k) => {
    const f: Schema = (campaignSchema.properties as Record<string, Schema>)[k];
    const pattern = k === 'name' ? campaignSchema.$defs.name : {};
    return [
      k,
      {
        path: k,
        section: 'campaign',
        label: SHORT_LABEL[k] ?? f['x-builder'].label,
        unit: f['x-builder'].unit,
        tier: f['x-builder'].tier,
        schema: { ...pattern, ...f },
        description: f.description,
        help: HELP[k] ?? f.description,
        variable: false,
        optional: false,
      },
    ];
  }),
);

export const fieldAt = (path: string): Field | undefined => SPEC_FIELDS.find((f) => f.path === path);

/** A field's value in a spec: what it holds, or for an optional field it leaves out, the default that stands for it. */
export function valueIn(spec: unknown, f: Field): Json | undefined {
  const v = getPath(spec, f.path);
  return v === undefined && f.optional ? (f.schema.default as Json) : v;
}

/** A spec, or one section of it, with every field at the schema's default. */
export function defaults(section?: string): Obj {
  const out: Obj = {};
  for (const f of SPEC_FIELDS) if (!section || f.section === section) setPath(out, f.path, clone(f.schema.default));
  return section ? ((out[section] as Obj) ?? {}) : out;
}

/** The value at a dotted path, or undefined. */
export function getPath(obj: unknown, path: string): Json | undefined {
  let v: unknown = obj;
  for (const k of path.split('.')) {
    if (!isObj(v) || !has(v, k)) return undefined;
    v = v[k];
  }
  return v as Json;
}

/** Sets a dotted path, making the objects on the way. */
export function setPath(obj: Obj, path: string, value: Json): void {
  const keys = path.split('.');
  let o = obj;
  for (const k of keys.slice(0, -1)) {
    if (!isObj(o[k])) o[k] = {};
    o = o[k] as Obj;
  }
  o[keys[keys.length - 1]] = value;
}

/** Removes a dotted path, and any object it leaves empty. */
export function deletePath(obj: Obj, path: string): void {
  const keys = path.split('.');
  const parent = keys.length > 1 ? getPath(obj, keys.slice(0, -1).join('.')) : obj;
  if (!isObj(parent)) return;
  delete parent[keys[keys.length - 1]];
  if (keys.length > 1 && Object.keys(parent).length === 0) deletePath(obj, keys.slice(0, -1).join('.'));
}

/** A spec's densities on another host, at the same densities per host vCPU: each scaled by the ratio of host vCPUs,
 * rounded half up, 1 kept as 1, duplicates dropped, at most 200. */
export function scaleDensities(d: number[], fromVcpus: number, toVcpus: number): number[] {
  const scaled = d.map((n) => (n === 1 ? 1 : Math.min(200, Math.max(1, Math.floor((n * toVcpus) / fromVcpus + 0.5)))));
  return [...new Set(scaled)].sort((a, b) => a - b);
}

/** A spec name for a spec that changes one input to this value: m8i-2xlarge, cloud-hypervisor, memory-4096. */
export function suggestName(path: string, value: unknown): string {
  if (path === 'hypervisor.virtio_rng') return value ? 'rng-on' : 'rng-off';
  const slug = String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (/^[a-z]/.test(slug)) return slug.slice(0, 40).replace(/-+$/, '');
  const word = path.split('.').pop()!.split('_')[0];
  return `${word}-${slug}`.slice(0, 40).replace(/-+$/, '');
}

/** A name not already in use: the name itself, or name-2, name-3, ... */
export function freeName(name: string, taken: string[]): string {
  if (!taken.includes(name)) return name;
  for (let k = 2; ; k++) if (!taken.includes(`${name}-${k}`)) return `${name}-${k}`;
}

// ------------------------------------------------------------------ writing a definition
const CAMPAIGN_ORDER = Object.keys(campaignSchema.properties);

/** Keys in schema order, anything unknown after them in the order written. */
function ordered(obj: Obj, order: string[]): Obj {
  const keys = [...order.filter((k) => has(obj, k)), ...Object.keys(obj).filter((k) => !order.includes(k))];
  return Object.fromEntries(keys.map((k) => [k, obj[k]]));
}

function orderedSpec(spec: Obj): Obj {
  const out = ordered(spec, Object.keys(specSchema.properties));
  for (const [k, x] of Object.entries(out)) {
    const node = (specSchema.$defs as Record<string, Schema>)[k];
    if (isObj(x) && node?.properties) out[k] = ordered(x, Object.keys(node.properties));
  }
  return out;
}

/** The definition with its keys in the canonical order: the schema's, specs as written, why following specs. */
export function canonical(camp: Obj): Obj {
  const out = ordered(camp, CAMPAIGN_ORDER);
  if (isObj(out.base)) out.base = orderedSpec(out.base);
  if (isObj(out.specs)) {
    out.specs = Object.fromEntries(Object.entries(out.specs).map(([k, x]) => [k, isObj(x) ? orderedSpec(x) : x]));
  }
  if (isObj(out.why) && isObj(out.specs)) out.why = ordered(out.why, Object.keys(out.specs));
  return out;
}

const WIDTH = 100;
const scalar = (x: unknown) => x === null || typeof x !== 'object';
const flatValue = (x: unknown) => scalar(x) || (Array.isArray(x) && x.every(scalar));

/** JSON written the way the campaign files are: an object of plain values on one line when it fits, lists of plain
 * values always on one line, everything else one key per line. */
export function pretty(value: Json, indent = '', lead = 0): string {
  if (Array.isArray(value) && value.every(scalar)) return `[${value.map((x) => JSON.stringify(x)).join(', ')}]`;
  if (scalar(value)) return JSON.stringify(value);
  const inner = indent + '  ';
  if (Array.isArray(value)) return `[\n${value.map((x) => inner + pretty(x, inner, inner.length)).join(',\n')}\n${indent}]`;
  const entries = Object.entries(value as Obj);
  if (!entries.length) return '{}';
  if (indent && entries.every(([, x]) => flatValue(x))) {
    const line = `{ ${entries.map(([k, x]) => `${JSON.stringify(k)}: ${pretty(x)}`).join(', ')} }`;
    if (lead + line.length + 1 <= WIDTH) return line;
  }
  const rows = entries.map(([k, x]) => {
    const key = `${inner}${JSON.stringify(k)}: `;
    return key + pretty(x, inner, key.length);
  });
  return `{\n${rows.join(',\n')}\n${indent}}`;
}

/** The definition to copy: canonical key order, formatted like the files in experiments/campaigns/. */
export function serialize(camp: Obj): string {
  return pretty(canonical(camp)) + '\n';
}
