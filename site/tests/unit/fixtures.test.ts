// The fixtures follow DATA.md: every document passes the contract checks.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { checkCampaign, checkIndex, checkRun, checkTrial } from '../../src/lib/contract';
import type { CampaignDoc, Index, RunDoc, TrialDoc } from '../../src/lib/types';

const DATA = join(__dirname, '../fixtures/data');
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const index = read<Index>('index.json');

describe('fixture dataset', () => {
  it('index.json follows the contract', () => {
    expect(checkIndex(index)).toEqual([]);
  });

  for (const entry of index.campaigns) {
    describe(entry.id, () => {
      const campaign = read<CampaignDoc>('campaigns', entry.id, 'campaign.json');

      it('campaign.json follows the contract', () => {
        expect(checkCampaign(campaign, entry)).toEqual([]);
      });

      for (const r of campaign.runs) {
        it(`run ${r.id} and each of its trials follow the contract`, () => {
          const run = read<RunDoc>('campaigns', entry.id, 'runs', `${r.id}.json`);
          expect(checkRun(run, campaign)).toEqual([]);
          const dir = join(DATA, 'campaigns', entry.id, 'runs', r.id);
          const files = existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.json')) : [];
          expect(files.sort()).toEqual(run.trials.map((t) => `${t.id}.json`).sort());
          for (const f of files) expect(checkTrial(read<TrialDoc>('campaigns', entry.id, 'runs', r.id, f), run)).toEqual([]);
        });
      }
    });
  }
});

describe('the contract refuses what breaks a rule', () => {
  const c = read<CampaignDoc>('campaigns', 'nested-sizes-synthetic', 'campaign.json');
  const entry = index.campaigns.find((x) => x.id === c.id)!;

  it('a midpoint that is not the middle of the span', () => {
    const bad = structuredClone(c);
    bad.runs[0].result.midpoint_per_host_vcpu = 0.9;
    expect(checkCampaign(bad).some((x) => x.includes('midpoint'))).toBe(true);
  });
  it("a spec that isn't the base merged with its changes", () => {
    const bad = structuredClone(c);
    bad.specs[1].spec.microvm.vcpus = 4;
    expect(checkCampaign(bad).join('\n')).toMatch(/isn't the base merged/);
  });
  it('a featured campaign that is not listed', () => {
    expect(checkIndex({ ...index, featured: 'nowhere' })).toEqual(['index: featured nowhere is not listed']);
  });
  it('an outcome that does not match its runs', () => {
    const bad = structuredClone(c);
    bad.outcomes[0].replicas[0].tested_successfully = 99;
    expect(checkCampaign(bad, entry).join('\n')).toMatch(/outcome for m8i-4xlarge/);
  });
});

describe('the synthetic campaigns', () => {
  const sizes = index.campaigns.find((c) => c.id === 'nested-sizes-synthetic')!;
  const hv = read<CampaignDoc>('campaigns', 'nested-hv-synthetic', 'campaign.json');

  it('are marked synthetic in every document', () => {
    for (const id of ['nested-sizes-synthetic', 'nested-hv-synthetic']) {
      const dir = join(DATA, 'campaigns', id);
      const campaign = read<CampaignDoc>('campaigns', id, 'campaign.json');
      expect(campaign.synthetic).toBe(true);
      for (const r of campaign.runs) {
        expect(read<RunDoc>('campaigns', id, 'runs', `${r.id}.json`).synthetic).toBe(true);
        for (const f of readdirSync(join(dir, 'runs', r.id))) {
          expect(read<TrialDoc>('campaigns', id, 'runs', r.id, f).synthetic).toBe(true);
        }
      }
    }
  });

  it('feature the newest complete campaign, not the newer partial one', () => {
    expect(index.campaigns[0].id).toBe('nested-hv-synthetic');
    expect(index.campaigns[0].status).toBe('partial');
    expect(index.featured).toBe('nested-sizes-synthetic');
  });

  it('show the replicas disagreeing on the 8-vCPU host (one walked down to 3), with D60 midpoints', () => {
    const o = sizes.outcomes.find((x) => x.spec === 'm8i-2xlarge')!;
    expect(o.replicas.map((r) => r.tested_successfully)).toEqual([4, 3]);
    expect(o.replicas.map((r) => r.midpoint_per_host_vcpu)).toEqual([0.625, 0.4375]);
    expect(o.midpoint_per_host_vcpu).toBe(0.5313);
    expect(sizes.outcomes[0].midpoint_per_host_vcpu).toBe(0.625);
  });

  it('label a run that stopped early and a density not tested, and give those specs no midpoint', () => {
    const ch = hv.runs.find((r) => r.id === 'cloud-hypervisor-r2')!;
    expect(ch.stopped_early).toBe(true);
    expect(ch.result).toMatchObject({ tested_successfully: 9, first_failed: null, midpoint_per_host_vcpu: null });
    const mmio = hv.runs.find((r) => r.id === 'firecracker-mmio-r2')!;
    expect(mmio.result.not_tested).toEqual([10, 11, 12, 14, 16]);
    expect(hv.outcomes.find((o) => o.spec === 'firecracker-mmio')!.midpoint_per_host_vcpu).toBeNull();
    expect(hv.status).toBe('partial');
  });
});

describe('cap-baseline-1, a campaign of one copied from the real build', () => {
  const campaign = read<CampaignDoc>('campaigns', 'cap-baseline-1', 'campaign.json');
  const run = read<RunDoc>('campaigns', 'cap-baseline-1', 'runs', 'baseline-r1.json');

  it('is a reconstructed campaign of one', () => {
    expect(campaign.before_campaigns).toBe(true);
    expect(campaign.reconstructed).toBe(true);
    expect(campaign.specs.map((s) => s.name)).toEqual(['baseline']);
    expect(campaign.runs.map((r) => r.id)).toEqual(['baseline-r1']);
  });

  it('keeps the published result: 8 tested successfully, 12 failed, 9 to 11 and 16 not tested, midpoint 0.625', () => {
    expect(run.result).toMatchObject({ tested_successfully: 8, first_failed: 12, gap: [9, 11], not_tested: [16], per_host_vcpu: 0.5, midpoint_per_host_vcpu: 0.625 });
  });

  it('numbers trials within their density and keeps the run-wide order separately', () => {
    expect(run.trials.map((t) => t.id)).toEqual([
      'warmup', 'd1-t1', 'd2-t1', 'd4-t1', 'd8-t1', 'd12-t1', 'd8-t2', 'd8-t3', 'd12-t2', 'd12-t3', 'illustration',
    ]);
    expect(run.trials.find((t) => t.id === 'd12-t1')?.order).toBe(6);
  });

  it('keeps the home-page medians at 12 and the CPU pressure that separated 8 from 12', () => {
    const d12 = run.by_density.find((d) => d.density === 12)!;
    const home = d12.checks.find((c) => c.subject === 'home' && c.stat === 'p50')!;
    expect(home.range.map(Math.round)).toEqual([1063, 1563]);
    expect(home.met).toBe(0);
    const sep = run.result.limit!.separated_by!;
    expect(sep.rule).toBe('host_cpu_pressure_pct');
    expect(sep.passing.map((v) => Math.round(v * 10) / 10)).toEqual([5.5, 10.6]);
    expect(sep.failing.map((v) => Math.round(v * 10) / 10)).toEqual([34.5, 46.5]);
    expect(run.result.limit!.missed[0]).toBe("the home step's median took 1,063–1,563 ms against 1,000 (3 of 3 trials)");
  });
});
