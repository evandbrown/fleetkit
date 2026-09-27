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

describe('the synthetic campaign', () => {
  const entry = index.campaigns.find((c) => c.synthetic);

  it('exists, with four runs of two specs', () => {
    expect(entry?.id).toBe('nested-sizes-synthetic');
    expect(entry?.runs).toBe(4);
    expect(entry?.specs.length).toBe(2);
  });

  it('is marked synthetic in every document', () => {
    const dir = join(DATA, 'campaigns', entry!.id);
    const campaign = read<CampaignDoc>('campaigns', entry!.id, 'campaign.json');
    expect(campaign.synthetic).toBe(true);
    for (const r of campaign.runs) {
      expect(read<RunDoc>('campaigns', entry!.id, 'runs', `${r.id}.json`).synthetic).toBe(true);
      for (const f of readdirSync(join(dir, 'runs', r.id))) {
        expect(read<TrialDoc>('campaigns', entry!.id, 'runs', r.id, f).synthetic).toBe(true);
      }
    }
  });

  it('shows the replicas disagreeing on the 8-vCPU host (one walked down to 3)', () => {
    const o = entry!.outcomes.find((x) => x.spec === 'm8i-2xlarge')!;
    expect(o.tested_successfully).toEqual([4, 3]);
    expect(o.per_host_vcpu).toEqual([0.5, 0.375]);
  });
});

describe('cap-baseline-1, a campaign of one derived from the real run', () => {
  const campaign = read<CampaignDoc>('campaigns', 'cap-baseline-1', 'campaign.json');
  const run = read<RunDoc>('campaigns', 'cap-baseline-1', 'runs', 'baseline-r1.json');

  it('is a reconstructed campaign of one', () => {
    expect(campaign.before_campaigns).toBe(true);
    expect(campaign.provenance).toBe('reconstructed');
    expect(campaign.specs.map((s) => s.name)).toEqual(['baseline']);
    expect(campaign.runs.map((r) => r.id)).toEqual(['baseline-r1']);
  });

  it('keeps the published result: density 8 tested successfully, 12 failed, 9 to 11 not tried, 16 not run', () => {
    expect(run.result).toMatchObject({ tested_successfully: 8, first_failed: 12, not_tried: [9, 11], not_run: [16], per_host_vcpu: 0.5 });
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
    const sep = run.result!.limit!.separated_by!;
    expect(sep.rule).toBe('host_cpu_pressure_pct');
    expect(sep.passing.map((v) => Math.round(v * 10) / 10)).toEqual([5.5, 10.6]);
    expect(sep.failing.map((v) => Math.round(v * 10) / 10)).toEqual([34.5, 46.5]);
  });
});
