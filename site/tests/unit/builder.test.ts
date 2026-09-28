// The builder's own helpers (components/builder/draft.ts) and its labels: the facts a field shows, where densities
// fill a host, reading pasted JSON, naming the next campaign, shorter messages, and labels short enough for the page.
import { describe, expect, it } from 'vitest';
import catalog from '../../catalog.json';
import { CAMPAIGN_FIELDS, HELP, LIMITS, SPEC_FIELDS, TYPES, defaults, evaluate, merge, type Obj } from '../../src/lib/campaign';
import {
  ALL_STARTS,
  DEFAULT_START,
  LEAN_CHROMIUM_FLAG,
  NEW_START,
  REVIEW_HELP,
  SPEC_NAME_HELP,
  START_HELP,
  STARTS,
  TITLES,
  WHY_HELP,
  bundled,
  densityFit,
  fromStart,
  hostFacts,
  interpret,
  messages,
  nextName,
  recommendedBase,
  show,
} from '../../src/components/builder/draft';

describe('builder facts', () => {
  it('says what an instance type adds to its size: kind and price, ≈ when estimated', () => {
    expect(hostFacts('m8i.4xlarge')).toBe(`nested · $${TYPES['m8i.4xlarge'].usd_per_hour.toFixed(2)}/h`);
    const estimated = Object.keys(TYPES).find((k) => TYPES[k].estimated);
    if (estimated) expect(hostFacts(estimated)).toContain('≈$');
    expect(hostFacts('no-such-type')).toBe('');
  });

  it('finds the densities that fill a host’s vCPUs and memory', () => {
    const spec = { worker_host: { instance_type: 'm8i.4xlarge' }, microvm: { vcpus: 2, memory_mib: 2048 } };
    expect(densityFit(spec)).toEqual({ cpu: 8, mem: 32 });
    expect(densityFit({ ...spec, microvm: { vcpus: 0, memory_mib: 2048 } })).toBeNull();
    expect(densityFit({ microvm: { vcpus: 2, memory_mib: 2048 } })).toBeNull();
  });

  it('shows values with their units and names', () => {
    expect(show('hypervisor.name', 'cloud-hypervisor')).toBe('Cloud Hypervisor');
    expect(show('microvm.memory_mib', 2048)).toBe('2,048 MiB');
    expect(show('densities', [1, 2, 4])).toBe('1, 2, 4');
    expect(show('hypervisor.virtio_rng', true)).toBe('on');
  });
});

describe('builder labels', () => {
  it('are three words at most', () => {
    for (const f of [...SPEC_FIELDS, ...Object.values(CAMPAIGN_FIELDS)]) {
      expect(f.label.split(/\s+/).length, f.label).toBeLessThanOrEqual(3);
    }
  });
});

describe('builder help', () => {
  const FIELDS = [...SPEC_FIELDS, ...Object.values(CAMPAIGN_FIELDS)];
  const sentences = (t: string) => t.split(/(?<=[.?!])\s+(?=[A-Z0-9])/).length;

  it('explains every field in its own words, not the schema’s', () => {
    for (const f of FIELDS) {
      expect(HELP[f.path], f.path).toBeTruthy();
      expect(f.help).toBe(HELP[f.path]);
    }
    const paths = new Set(FIELDS.map((f) => f.path));
    for (const k of Object.keys(HELP)) expect(paths.has(k), `HELP has ${k}, which is no field`).toBe(true);
  });

  it('says it in one or two plain sentences', () => {
    const all = [...FIELDS.map((f) => [f.path, f.help]), ['start', START_HELP], ['spec name', SPEC_NAME_HELP], ['why', WHY_HELP], ...Object.entries(REVIEW_HELP)];
    for (const [k, t] of all) {
      expect(sentences(t), `${k}: ${t}`).toBeLessThanOrEqual(2);
      expect(t.length, k).toBeLessThanOrEqual(260);
      expect(t, k).toMatch(/[.]$/);
      expect(t, k).not.toMatch(/_ms\b|_s\b|_mib\b/);
    }
  });

  it('gives the p50 target and the densities the meaning a reviewer needs', () => {
    expect(HELP['criteria.step_p50_target_ms']).toMatch(/^In each trial, the median time of each of the five steps must be at or under this, or the trial fails\./);
    expect(HELP.densities).toMatch(/^How many browsers each trial runs at once, one microVM each; the run tests the counts in order and stops at the first that fails\./);
  });

  it('quotes the limits the review checks against', () => {
    expect(REVIEW_HELP.worst).toContain(`$${LIMITS.campaign_worst_case_usd}`);
    expect(REVIEW_HELP.vcpus).toContain(String(LIMITS.vcpu_quota));
  });
});

describe('builder starts', () => {
  it('opens nested-sizes-1 first, campaigns before examples', () => {
    expect(DEFAULT_START.key).toBe('nested-sizes-1');
    const firstExample = STARTS.findIndex((s) => s.example);
    if (firstExample >= 0) expect(STARTS.slice(firstExample).every((s) => s.example)).toBe(true);
  });

  it('reads a pasted campaign, a pasted spec, and a message with a json block', () => {
    const def = structuredClone(DEFAULT_START.def);
    expect(interpret(JSON.stringify(def)).def).toEqual(def);
    const spec = interpret(JSON.stringify(def.base));
    expect(Object.keys(spec.def.specs as object)).toEqual(['base']);
    expect(spec.source).toBeNull();
    const msg = interpret('Here it is:\n```json\n' + JSON.stringify(def) + '\n```\nThanks');
    expect(msg.def.name).toBe(def.name);
    expect(() => interpret('{"x": 1}')).toThrow(/neither/);
  });

  it('calls a published campaign what Results calls it', () => {
    for (const c of catalog.campaigns) {
      const start = STARTS.find((s) => s.key === c.id);
      if (start) expect(start.title, c.id).toBe(c.title);
    }
    expect(Object.keys(TITLES).filter((k) => !STARTS.some((s) => s.key === k))).toEqual([]);
  });

  it('names the next campaign in a series', () => {
    expect(nextName('nested-sizes-1', ['nested-sizes-1', 'nested-sizes-2'])).toBe('nested-sizes-3');
    expect(nextName('fresh', [])).toBe('fresh-2');
  });
});

// A new campaign starts from the design's default guest (D91), not from the schema's defaults, which keep the meaning
// of the specs recorded before the guest fields existed.
describe('a new campaign', () => {
  const base = NEW_START.def.base as Obj;

  it('starts from the recommended guest on c8i.xlarge, at densities 1 to 8, with the schema’s criteria', () => {
    expect(base.worker_host).toEqual({ instance_type: 'c8i.xlarge' });
    expect(base.hypervisor).toEqual({ name: 'firecracker', virtio_transport: 'pci', virtio_rng: true });
    expect(base.microvm).toEqual({ vcpus: 1, memory_mib: 1024, console: 'quiet-i8042', memory_pages: 'thp' });
    expect(base.workload).toEqual({ chromium_extra_flags: [LEAN_CHROMIUM_FLAG] });
    expect(base.densities).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(base.criteria).toEqual(defaults('criteria'));
    expect(base.procedure).toEqual({ trials_per_density: 1, boundary_trials: 2, settle_s: 10 });
    expect(base.support_host).toEqual({ instance_type: 'm8i.xlarge' });
    expect(recommendedBase()).toEqual(base);
  });

  it('has no name or question yet, one spec that runs the base unchanged, and the schema’s 2 replicas', () => {
    expect(NEW_START.def.name).toBe('');
    expect(NEW_START.def.question).toBe('');
    expect(NEW_START.def.specs).toEqual({ 'tuned-guest': {} });
    expect(NEW_START.def.replicas).toBe(2);
    expect(NEW_START.def.shutdown_after_minutes).toBe(CAMPAIGN_FIELDS.shutdown_after_minutes.schema.default);
  });

  it('is the control of tuned_guest_host_size, unchanged', () => {
    const c = STARTS.find((s) => s.key === 'host16-stack-1')!.def;
    const control = merge(c.base as Obj, (c.specs as Obj)['c8i-xlarge-stack'] as Obj);
    for (const k of ['worker_host', 'hypervisor', 'microvm', 'densities', 'workload']) expect(base[k], k).toEqual(control[k]);
  });

  it('passes every check once named and asked, and plans 2 runs', () => {
    const def = { ...structuredClone(NEW_START.def), name: 'tuned-guest-1', question: 'Does the recommended guest hold on the next host?' };
    const r = evaluate(def);
    expect(r.errors).toEqual([]);
    expect(r.plan!.runs.map((x) => x.run)).toEqual(['tuned-guest-r1', 'tuned-guest-r2']);
  });

  it('heads the Start menu and opens from a link, as a campaign file does', () => {
    expect(ALL_STARTS[0]).toBe(NEW_START);
    expect(ALL_STARTS.slice(1)).toEqual(STARTS);
    expect(bundled('campaign:new')!.def).toEqual(NEW_START.def);
    expect(fromStart(NEW_START).source).toBe(NEW_START.def);
    expect(START_HELP).toMatch(/recommended guest/);
  });

  it('is what the guest fields’ help recommends', () => {
    expect(HELP['microvm.vcpus']).toMatch(/Recommended: 1,/);
    expect(HELP['microvm.memory_mib']).toMatch(/Recommended: 1,024/);
    expect(HELP['microvm.console']).toMatch(/Recommended: quiet-i8042/);
    expect(HELP['microvm.memory_pages']).toMatch(/Recommended: thp/);
    expect(HELP['microvm.memory_pages']).toMatch(/madvise or always/);
    expect(HELP['workload.chromium_extra_flags']).toMatch(/Recommended: the one a new campaign starts with/);
  });
});

describe('builder messages', () => {
  it('shortens the long shared messages and keeps their paths', () => {
    const def = structuredClone(DEFAULT_START.def) as Record<string, unknown>;
    def.shutdown_after_minutes = 21;
    const msgs = messages(evaluate(def));
    const timer = msgs.filter((m) => m.path === 'shutdown_after_minutes');
    expect(timer.length).toBeGreaterThan(0);
    for (const m of timer) expect(m.text).toMatch(/takes ≈\d+ min, over the 21-min timer/);
  });
});
