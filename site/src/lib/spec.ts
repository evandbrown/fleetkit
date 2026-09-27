// Reading specs for display. The site never computes a result from a spec; it labels and compares them.
import type { CampaignDoc, Spec, SpecDoc, SpecField } from './types';

/**
 * Where the spec keeps each typed copy, by the SpecDoc field it fills. The site computes only from the typed
 * copies and finds them in a spec only through this map, so this is the one place to update when the input
 * schema (D41) replaces the interim shape in DATA.md.
 */
export const SPEC_PATHS = {
  instance_type: 'host.instance_type',
  hypervisor: 'hypervisor.name',
  'microvm.vcpus': 'microvm.vcpus',
  'microvm.mem_mib': 'microvm.mem_mib',
  'microvm.mem_overhead_mib': 'microvm.mem_overhead_mib',
  densities: 'procedure.densities',
  boundary_trials: 'procedure.boundary_trials',
  'criteria.step_p50_ms': 'criteria.step_p50_ms',
  'criteria.step_p95_ms': 'criteria.step_p95_ms',
  'criteria.task_p95_ms': 'criteria.task_p95_ms',
  'criteria.ready_limit_s': 'criteria.ready_limit_s',
} as const;
export type TypedField = keyof typeof SPEC_PATHS;

/** A typed copy on a SpecDoc, by its field name ("microvm.vcpus"). */
export function typedCopy(s: SpecDoc, field: TypedField): unknown {
  return getPath(s as unknown as Spec, field);
}

/** The value at a dotted path ("host.instance_type"), or undefined. */
export function getPath(spec: Spec, path: string): unknown {
  let v: unknown = spec;
  for (const k of path.split('.')) {
    if (v === null || typeof v !== 'object' || !(k in (v as object))) return undefined;
    v = (v as Record<string, unknown>)[k];
  }
  return v;
}

/** Every leaf path in a spec ("procedure.densities"), arrays treated as values. */
export function leafPaths(spec: Spec, prefix = ''): string[] {
  return Object.entries(spec).flatMap(([k, v]) =>
    v !== null && typeof v === 'object' && !Array.isArray(v)
      ? leafPaths(v as Spec, `${prefix}${k}.`)
      : [`${prefix}${k}`],
  );
}

export function fieldFor(fields: SpecField[], path: string): SpecField {
  return fields.find((f) => f.path === path) ?? { path, label: path, group: 'Other' };
}

/** Paths changed by any spec, in spec_fields order. */
export function differingPaths(doc: CampaignDoc): string[] {
  const changed = new Set(doc.specs.flatMap((s) => s.changes.map((c) => c.path)));
  const ordered = doc.spec_fields.map((f) => f.path).filter((p) => changed.has(p));
  return [...ordered, ...[...changed].filter((p) => !ordered.includes(p))];
}

export function show(value: unknown, unit?: string): string {
  if (value === undefined) return '–';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') return `${value.toLocaleString('en-US')}${unit ? (unit === '%' ? '%' : ` ${unit}`) : ''}`;
  if (value === null) return 'none';
  return String(value);
}

/** The base spec's fields grouped for display, in spec_fields order, then anything unlabelled. */
export function grouped(doc: CampaignDoc, spec: Spec): { group: string; rows: { field: SpecField; value: unknown }[] }[] {
  const paths = leafPaths(spec);
  const ordered = [
    ...doc.spec_fields.map((f) => f.path).filter((p) => paths.includes(p)),
    ...paths.filter((p) => !doc.spec_fields.some((f) => f.path === p)),
  ];
  const out: { group: string; rows: { field: SpecField; value: unknown }[] }[] = [];
  for (const p of ordered) {
    const field = fieldFor(doc.spec_fields, p);
    let g = out.find((x) => x.group === field.group);
    if (!g) out.push((g = { group: field.group, rows: [] }));
    g.rows.push({ field, value: getPath(spec, p) });
  }
  return out;
}
