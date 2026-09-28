// Reading specs for display, from the one input schema (experiments/schema/spec.schema.json): each field's
// label, unit and tier, in the schema's order. The site never computes a result from a spec; it labels and
// compares them.
import schema from '../../../experiments/schema/spec.schema.json';
import * as f from './format';
import type { Spec } from './types';

export interface Field {
  path: string;
  label: string;
  unit?: string;
  tier: 'basic' | 'advanced' | 'rare';
  /** The section it belongs to: "MicroVM", "Pass criteria". */
  group: string;
}

type Node = {
  $ref?: string;
  type?: string;
  properties?: Record<string, Node>;
  'x-builder'?: { label?: string; unit?: string; tier?: Field['tier'] };
};

function deref(n: Node): Node {
  if (!n.$ref) return n;
  const name = n.$ref.replace('#/$defs/', '');
  return { ...(schema.$defs as Record<string, Node>)[name], ...n, $ref: undefined };
}

function walk(n: Node, path: string, group: string, out: Field[]) {
  const node = deref(n);
  const label = node['x-builder']?.label ?? path;
  if (node.type === 'object' && node.properties) {
    for (const [k, v] of Object.entries(node.properties)) walk(v, path ? `${path}.${k}` : k, label, out);
    return;
  }
  const xb = node['x-builder'] ?? {};
  out.push({ path, label, unit: xb.unit, tier: xb.tier ?? 'rare', group: path.includes('.') ? group : label });
}

/** Every leaf of the spec, in the schema's order. */
export const FIELDS: Field[] = (() => {
  const out: Field[] = [];
  for (const [k, v] of Object.entries(schema.properties as Record<string, Node>)) walk(v, k, k, out);
  return out;
})();

const BY_PATH = new Map(FIELDS.map((x) => [x.path, x]));

/**
 * The method's standard SLOs: the schema's defaults for the pass criteria, read when the site is built. About states
 * these; a campaign may choose its own (D74), and the site names any difference from these.
 */
export const STANDARD_CRITERIA: Spec['criteria'] = (() => {
  const props = (schema.$defs.criteria as { properties: Record<string, { default?: number }> }).properties;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(props)) {
    if (typeof v.default !== 'number') throw new Error(`spec.schema.json: criteria.${k} has no default`);
    out[k] = v.default;
  }
  return out as Spec['criteria'];
})();

export function field(path: string): Field {
  return BY_PATH.get(path) ?? { path, label: path, tier: 'rare', group: 'Other' };
}

/** The value at a dotted path ("worker_host.instance_type"), or undefined. */
export function getPath(obj: unknown, path: string): unknown {
  let v: unknown = obj;
  for (const k of path.split('.')) {
    if (v === null || typeof v !== 'object' || !(k in (v as object))) return undefined;
    v = (v as Record<string, unknown>)[k];
  }
  return v;
}

/** Every leaf of a spec as a dotted path, lists as values. */
export function flatten(obj: object, prefix = ''): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) Object.assign(out, flatten(v as object, `${prefix}${k}.`));
    else out[`${prefix}${k}`] = v;
  }
  return out;
}

const HYPERVISOR: Record<string, string> = { firecracker: 'Firecracker', 'cloud-hypervisor': 'Cloud Hypervisor' };

/** "2 GiB" for whole GiB, else "1,536 MiB". */
export function mib(v: number): string {
  return v % 1024 === 0 ? `${f.num(v / 1024)} GiB` : `${f.num(v)} MiB`;
}

/** A spec value in words, with its unit. */
export function show(path: string, value: unknown): string {
  if (value === undefined) return '–';
  if (path === 'hypervisor.name') return HYPERVISOR[String(value)] ?? String(value);
  if (path === 'hypervisor.virtio_rng') return value ? 'yes' : 'no';
  if (path === 'microvm.memory_mib' && typeof value === 'number') return mib(value);
  if (path === 'densities' && Array.isArray(value)) return value.join(', ');
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  const unit = field(path).unit;
  if (typeof value === 'number') return `${f.num(value, value % 1 ? 2 : 0)}${unit ? ` ${unit}` : ''}`;
  return String(value);
}

export const hypervisorName = (h: string) => HYPERVISOR[h] ?? h;

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** The paths whose values differ between the specs, in the schema's order. */
export function differing(specs: Spec[]): string[] {
  const flat = specs.map((s) => flatten(s));
  const paths = [...new Set(flat.flatMap((x) => Object.keys(x)))];
  const ordered = [...FIELDS.map((x) => x.path).filter((p) => paths.includes(p)), ...paths.filter((p) => !BY_PATH.has(p))];
  return ordered.filter((p) => flat.some((x) => !same(x[p], flat[0][p])));
}
