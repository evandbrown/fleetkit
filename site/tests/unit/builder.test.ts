// The builder's own helpers (components/builder/draft.ts) and its labels: the facts a field shows, where densities
// fill a host, reading pasted JSON, naming the next campaign, shorter messages, and labels short enough for the page.
import { describe, expect, it } from 'vitest';
import { CAMPAIGN_FIELDS, SPEC_FIELDS, TYPES, evaluate } from '../../src/lib/campaign';
import {
  DEFAULT_START,
  STARTS,
  densityFit,
  hostFacts,
  interpret,
  messages,
  nextName,
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

  it('names the next campaign in a series', () => {
    expect(nextName('nested-sizes-1', ['nested-sizes-1', 'nested-sizes-2'])).toBe('nested-sizes-3');
    expect(nextName('fresh', [])).toBe('fresh-2');
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
