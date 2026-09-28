// lib/campaign.ts against experiments/schema/tests/cases.json, the cases expand.py also passes, plus the builder's
// own helpers: writing a definition exactly as the campaign files are written, scaling densities, naming specs.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  FLAGS,
  InputError,
  SPEC_FIELDS,
  VARIABLE_SECTIONS,
  checkSpec,
  fieldAt,
  flagProblems,
  defaults,
  evaluate,
  parseJson,
  scaleDensities,
  serialize,
  suggestName,
  validate,
  valueIn,
  SCHEMAS,
  type Json,
  type Obj,
} from '../../src/lib/campaign';

const EXPERIMENTS = join(__dirname, '../../../experiments');
const read = (p: string) => parseJson(readFileSync(join(EXPERIMENTS, p), 'utf8'));

interface Case {
  name: string;
  file?: string;
  from?: string;
  patch?: Json;
  campaign?: Json;
  valid: boolean;
  errors?: string[];
  warnings?: string[];
  runs?: string[];
  differs?: string[];
  expected_usd?: number;
  worst_case_usd?: number;
  waves?: string[][];
  too_big_for_quota?: string[];
  quota?: number;
}
const CASES = (read('schema/tests/cases.json') as unknown as { cases: Case[] }).cases;

/** RFC 7386 JSON merge patch: objects merge, null removes a key, anything else replaces. */
function mergePatch(target: Json | undefined, patch: Json): Json {
  if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) return structuredClone(patch);
  const out: Obj =
    target && typeof target === 'object' && !Array.isArray(target) ? structuredClone(target as Obj) : {};
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) delete out[k];
    else out[k] = mergePatch(out[k], v);
  }
  return out;
}

function campaignOf(c: Case): Json {
  if (c.campaign !== undefined) return c.campaign;
  const doc = read((c.file ?? c.from)!);
  return c.patch !== undefined ? mergePatch(doc, c.patch) : doc;
}

describe('cases.json', () => {
  for (const c of CASES) {
    it(c.name, () => {
      const r = evaluate(campaignOf(c), c.quota);
      expect(r.valid, r.errors.join('\n')).toBe(c.valid);
      if (c.valid) expect(r.errors).toEqual([]);
      for (const want of c.errors ?? []) {
        expect(r.errors.some((e) => e.includes(want)), `no error containing ${want}; got ${r.errors}`).toBe(true);
      }
      if (c.warnings) {
        for (const want of c.warnings) {
          expect(r.warnings.some((w) => w.includes(want)), `no warning containing ${want}; got ${r.warnings}`).toBe(true);
        }
        expect(r.warnings).toHaveLength(c.warnings.length);
      }
      const p = r.plan;
      if (c.runs) expect(p!.runs.map((x) => x.run)).toEqual(c.runs);
      if (c.differs) expect(p!.differs.map((d) => d.field)).toEqual(c.differs);
      if (c.expected_usd !== undefined) expect(p!.expected_usd).toBeCloseTo(c.expected_usd, 2);
      if (c.worst_case_usd !== undefined) expect(p!.worst_case_usd).toBeCloseTo(c.worst_case_usd, 2);
      if (c.waves) expect(p!.waves).toEqual(c.waves);
      if (c.too_big_for_quota) expect(p!.too_big_for_quota).toEqual(c.too_big_for_quota);
    });
  }

  it('a quota of 1,024 fits the metal example in one wave', () => {
    const p = evaluate(read('campaigns/examples/hv-host-1.json'), 1024).plan!;
    expect(p.waves).toHaveLength(1);
    expect(p.too_big_for_quota).toEqual([]);
  });

  it('shows the estimate even when only a cost limit fails', () => {
    const r = evaluate(mergePatch(read('campaigns/nested-sizes-1.json'), { shutdown_after_minutes: 21 }));
    expect(r.valid).toBe(false);
    expect(r.plan).toBeNull();
    expect(r.estimate!.runs).toHaveLength(4);
  });
});

describe('the schema', () => {
  it('gives every spec field a tier, a label and a valid default, and the defaults make a valid spec', () => {
    for (const f of SPEC_FIELDS) {
      expect(['basic', 'advanced', 'rare']).toContain(f.tier);
      expect(f.label).toBeTruthy();
      expect(validate(f.schema.default, f.schema, f.path)).toEqual([]);
    }
    const spec = defaults();
    expect(Object.keys(spec)).toEqual(Object.keys(SCHEMAS['spec.schema.json'].properties));
    expect(validate(spec, SCHEMAS['spec.schema.json'])).toEqual([]);
    expect(checkSpec(spec)).toEqual([[], []]);
  });

  it('has two optional fields, the wait after ready and the extra Chromium flags, held at their defaults when left out', () => {
    expect(SPEC_FIELDS.filter((f) => f.optional).map((f) => f.path)).toEqual([
      'procedure.release_after_ready_s',
      'workload.chromium_extra_flags',
    ]);
    const flags = fieldAt('workload.chromium_extra_flags')!;
    expect(valueIn({}, flags)).toEqual([]);
    expect(flags.variable).toBe(true);
    expect(flags.help).toContain('chromium-flags.json');
    expect(VARIABLE_SECTIONS).toEqual(['worker_host', 'hypervisor', 'microvm', 'densities', 'workload']);
    const wait = SPEC_FIELDS.find((f) => f.optional)!;
    const base = (read('campaigns/nested-sizes-1.json') as Obj).base as Obj;
    expect((base.procedure as Obj).release_after_ready_s).toBeUndefined();
    expect(valueIn(base, wait)).toBe(0);
    expect(valueIn({ procedure: { release_after_ready_s: 30 } }, wait)).toBe(30);
    const settle = SPEC_FIELDS.find((f) => f.path === 'procedure.settle_s')!;
    expect(valueIn({ procedure: {} }, settle)).toBeUndefined(); // a required field left out stays missing
  });
});

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : n.endsWith('.json') ? [p] : [];
  });
}

describe('writing a definition', () => {
  for (const f of files(join(EXPERIMENTS, 'campaigns'))) {
    it(`writes ${relative(EXPERIMENTS, f)} exactly as the file is written`, () => {
      const text = readFileSync(f, 'utf8');
      expect(serialize(parseJson(text) as Obj)).toBe(text);
    });
  }

  it('puts keys in the schema order, whatever order they were set in', () => {
    const doc = read('campaigns/nested-sizes-1.json') as Obj;
    const shuffled = Object.fromEntries(Object.entries(doc).reverse()) as Obj;
    shuffled.base = Object.fromEntries(Object.entries(doc.base as Obj).reverse());
    expect(serialize(shuffled)).toBe(serialize(doc));
  });
});

describe('reading JSON', () => {
  it('refuses a key repeated in one object', () => {
    expect(() => parseJson('{"specs": {"a": {}, "a": {"densities": [1]}}}')).toThrow(InputError);
    expect(() => parseJson('{"specs": {"a": {}, "a": {"densities": [1]}}}')).toThrow(/appears twice/);
  });
  it('reads what JSON.parse reads', () => {
    const text = '{"a": [1, 2.5, -3e2, true, false, null, "x\\"y\\u00e9"], "b": {}}';
    expect(parseJson(text)).toEqual(JSON.parse(text));
  });
  it('says where it stopped', () => {
    expect(() => parseJson('{"a": 1,}')).toThrow(/expected a key/);
    expect(() => parseJson('{"a": 1} x')).toThrow(/more text/);
  });
});

describe('extra Chromium flags', () => {
  it('allows every listed flag in its simplest form and refuses the base flags', () => {
    for (const [name, rule] of Object.entries(FLAGS.allowed)) {
      const value = { none: '', integer: `=${rule.minimum}`, features: `=${Object.keys(rule.features ?? {})[0]}`, v8: '=--jitless' }[
        rule.value
      ];
      expect(flagProblems(name + value)).toEqual([]);
    }
    for (const b of FLAGS.base) expect(flagProblems(b)[0]).toMatch(/is one of the base flags every run has$/);
  });
  it('says why a refused flag is refused', () => {
    expect(flagProblems('--blink-settings=imagesEnabled=false')).toEqual([
      `--blink-settings is refused: ${FLAGS.refused['--blink-settings']}`,
    ]);
  });
});

describe('builder helpers', () => {
  it('scales densities to the same grid per host vCPU', () => {
    expect(scaleDensities([1, 2, 4, 8, 12, 16], 16, 8)).toEqual([1, 2, 4, 6, 8]);
    expect(scaleDensities([1, 4, 8, 9, 10, 11, 12, 14, 16], 16, 8)).toEqual([1, 2, 4, 5, 6, 7, 8]);
    expect(scaleDensities([1, 8, 16], 16, 192)).toEqual([1, 96, 192]);
    expect(scaleDensities([1, 20, 40], 8, 192)).toEqual([1, 200]);
  });
  it('names a spec after the value it changes', () => {
    expect(suggestName('worker_host.instance_type', 'm8i.2xlarge')).toBe('m8i-2xlarge');
    expect(suggestName('hypervisor.name', 'cloud-hypervisor')).toBe('cloud-hypervisor');
    expect(suggestName('microvm.memory_mib', 4096)).toBe('memory-4096');
    expect(suggestName('microvm.vcpus', 4)).toBe('vcpus-4');
    expect(suggestName('hypervisor.virtio_rng', true)).toBe('rng-on');
  });
});
