// The dataset builder's last check: the site's own contract (src/lib/contract.ts) over a built dataset, plus the
// things only a whole dataset can show (every trial document present, every screenshot present, nothing synthetic).
// FLEETKIT_DATA names the dataset; it defaults to public/data.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { checkCampaign, checkIndex, checkRun, checkTrial } from '../src/lib/contract';
import type { CampaignDoc, Index, RunDoc, TrialDoc } from '../src/lib/types';

const DATA = process.env.FLEETKIT_DATA ?? join(__dirname, '../public/data');
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const index = read<Index>('index.json');
const img = (sha8: string) => ['t', 'f'].filter((k) => !existsSync(join(DATA, 'img', `${sha8}.${k}.webp`)));

describe(`dataset at ${DATA}`, () => {
  it('index.json follows the contract and publishes nothing synthetic', () => {
    expect(checkIndex(index)).toEqual([]);
    expect(index.campaigns.filter((c) => c.synthetic)).toEqual([]);
  });

  for (const entry of index.campaigns) {
    describe(entry.id, () => {
      const campaign = read<CampaignDoc>('campaigns', entry.id, 'campaign.json');

      it('campaign.json follows the contract', () => {
        expect(checkCampaign(campaign, entry)).toEqual([]);
      });

      for (const r of campaign.runs) {
        it(`run ${r.id}, its trials and its screenshots follow the contract`, () => {
          const run = read<RunDoc>('campaigns', entry.id, 'runs', `${r.id}.json`);
          expect(checkRun(run, campaign)).toEqual([]);
          const dir = join(DATA, 'campaigns', entry.id, 'runs', r.id);
          const files = existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.json')) : [];
          expect(files.sort()).toEqual(run.trials.map((t) => `${t.id}.json`).sort());
          const missing: string[] = [];
          for (const sha of run.tasks.img) if (sha) missing.push(...img(sha).map((k) => `${sha}.${k}`));
          for (const f of files) {
            const trial = read<TrialDoc>('campaigns', entry.id, 'runs', r.id, f);
            expect(checkTrial(trial, run)).toEqual([]);
            for (const m of trial.microvms) if (m.img) missing.push(...img(m.img).map((k) => `${m.img}.${k}`));
            for (const fr of trial.filmstrip?.frames ?? []) missing.push(...img(fr.img).map((k) => `${fr.img}.${k}`));
          }
          expect(missing).toEqual([]);
        });
      }
    });
  }
});
