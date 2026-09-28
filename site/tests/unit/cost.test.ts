// The topline cost (D102), over the fixtures' index.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { campaignCost, costNote, specCost } from '../../src/lib/cost';
import type { CampaignEntry, Index } from '../../src/lib/types';

const index = JSON.parse(readFileSync(join(__dirname, '../fixtures/data/index.json'), 'utf8')) as Index;
const entry = (id: string) => index.campaigns.find((c) => c.id === id)!;

describe('specCost', () => {
  it('is the median of the replicas’ range middles, with the count most replicas held', () => {
    const s = specCost(entry('nested-sizes-synthetic'), 'm8i-4xlarge');
    // middles 0.20165 and 0.19655; the median of two is their mean
    expect(s.cost).toBeCloseTo(0.1991, 4);
    expect(s.middles).toHaveLength(2);
    expect(s.count).toBe('8');
    expect(s.countN).toBe(8);
    expect(s.hostVcpus).toBe(16);
    expect(s.perVcpu).toBe(0.5);
    expect(s.replicas).toBe(2);
    expect(s.noFleet).toBe(false);
  });

  it('joins the full ranges behind the number, and writes them as a note for a tooltip', () => {
    const s = specCost(entry('nested-sizes-synthetic'), 'm8i-4xlarge');
    expect(s.steady).toEqual([0.1926, 0.2107]);
    expect(s.burst).toEqual([0.206, 0.2121]);
    expect(costNote(s)).toBe('Steady state over 2 replicas: $0.193–0.211 per 1,000 tasks; one burst $0.206–0.212');
    expect(costNote(specCost(entry('cap-baseline-1'), 'baseline'))).toBe('Steady state over one replica: $0.141–0.142 per 1,000 tasks; one burst $0.184–0.201');
  });

  it('keeps the cost from the replicas that have one, and a "≥" count when no replica failed', () => {
    const s = specCost(entry('nested-sizes-synthetic'), 'm8i-2xlarge');
    expect(s.middles).toHaveLength(1);
    expect(s.cost).toBeCloseTo(0.2022, 4);
    expect(s.count).toBe('3'); // 4 and 3 once each: the lower
    const hv = specCost(entry('nested-hv-synthetic'), 'cloud-hypervisor');
    expect(hv.count).toBe('9'); // one replica failed at 10, so no "≥"
  });

  it('reports a spec that ran with no fleet cost, and one that never ran', () => {
    const e: CampaignEntry = {
      ...entry('cap-baseline-1'),
      specs: [
        { name: 'baseline', label: 'b' },
        { name: 'queued', label: 'q' },
      ],
      outcomes: [
        {
          spec: 'baseline',
          midpoint_per_host_vcpu: null,
          replicas: entry('cap-baseline-1').outcomes[0].replicas.map((r) => ({ ...r, first_failed: null, cost_per_1000_tasks: null })),
        },
      ],
    };
    const b = specCost(e, 'baseline');
    expect(b.cost).toBeNull();
    expect(b.noFleet).toBe(true);
    expect(b.count).toBe('≥ 8');
    expect(b.countN).toBe(8);
    expect(b.steady).toBeNull();
    expect(b.burst).toBeNull();
    expect(costNote(b)).toBeNull();
    // With a burst but no steady-state figure, the note gives the burst and says why it stands alone.
    const burst = { ...b, burst: [0.182, 0.19] as [number, number] };
    expect(costNote(burst)).toBe('One burst: $0.182–0.190 per 1,000 tasks. The host was never full, so there is no steady-state figure.');
    const q = specCost(e, 'queued');
    expect(q.ran).toBe(false);
    expect(q.count).toBeNull();
    expect(q.perVcpu).toBeNull();
    expect(q.replicas).toBe(0);
    expect(q.noFleet).toBe(false);
  });
});

describe('campaignCost', () => {
  it('ranks specs cheapest first and crowns a clear winner', () => {
    const c = campaignCost(entry('nested-hv-synthetic'));
    // medians: firecracker 0.1897, firecracker-mmio 0.2015, cloud-hypervisor 0.2017
    expect(c.specs.map((s) => s.spec)).toEqual(['firecracker', 'firecracker-mmio', 'cloud-hypervisor']);
    expect(c.lead?.spec).toBe('firecracker');
    // firecracker's middles 0.18855, 0.1909 both under firecracker-mmio's 0.1992, 0.2037
    expect(c.clear).toBe(true);
    expect(c.costed).toBe(3);
  });

  it('does not crown a winner whose replicas overlap the runner-up\u2019s', () => {
    const sizes = entry('nested-sizes-synthetic');
    // as the fixture stands, m8i-4xlarge's middles 0.20165 and 0.19655 are both under m8i-2xlarge's 0.2022
    expect(campaignCost(sizes).clear).toBe(true);
    // bring the runner-up down to 0.200: the leader's median 0.1991 still wins, but its dearer replica doesn't
    const e: CampaignEntry = {
      ...sizes,
      outcomes: sizes.outcomes.map((o) =>
        o.spec !== 'm8i-2xlarge'
          ? o
          : { ...o, replicas: o.replicas.map((r) => (r.cost_per_1000_tasks ? { ...r, cost_per_1000_tasks: { ...r.cost_per_1000_tasks, steady_state: [0.198, 0.202] } } : r)) },
      ),
    };
    const c = campaignCost(e);
    expect(c.lead?.spec).toBe('m8i-4xlarge');
    expect(c.clear).toBe(false);
  });

  it('leads with the catalog’s featured spec when it has a cost, else the cheapest', () => {
    const e = { ...entry('nested-hv-synthetic'), featured_spec: 'firecracker-mmio' };
    expect(campaignCost(e).lead?.spec).toBe('firecracker-mmio');
    expect(campaignCost({ ...e, featured_spec: 'nowhere' }).lead?.spec).toBe('firecracker');
  });

  it('with one spec the lead stands alone and is clear', () => {
    const c = campaignCost(entry('cap-baseline-1'));
    expect(c.lead?.spec).toBe('baseline');
    expect(c.clear).toBe(true);
    expect(c.specs).toHaveLength(1);
  });
});
