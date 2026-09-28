// The dataset builder's last check: the site's own contract (src/lib/contract.ts) over a built dataset, plus the
// things only a whole dataset can show (every trial document present, every screenshot present at the sizes rule 10
// gives it and nothing else in img/, nothing synthetic).
// FLEETKIT_DATA names the dataset; it defaults to public/data.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { checkCampaign, checkIndex, checkRun, checkTrial } from '../src/lib/contract';
import type { CampaignDoc, Index, RunDoc, TrialDoc } from '../src/lib/types';

const DATA = process.env.FLEETKIT_DATA ?? join(__dirname, '../public/data');
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const index = read<Index>('index.json');
const IMG = join(DATA, 'img');
/** A trial's screenshots: its final screens and its filmstrip. */
const shots = (t: TrialDoc) => [...t.microvms.flatMap((m) => (m.img ? [m.img] : [])), ...(t.filmstrip?.frames ?? []).map((f) => f.img)];
/** The files a screenshot in this trial needs (rule 10): its thumbnail, and its full-size copy if the trial has them. */
const needs = (t: TrialDoc) => shots(t).flatMap((sha) => [`${sha}.t.webp`, ...(t.full_size_screenshots ? [`${sha}.f.webp`] : [])]);

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
          const want = run.tasks.img.flatMap((sha) => (sha ? [`${sha}.t.webp`] : []));
          for (const f of files) {
            const trial = read<TrialDoc>('campaigns', entry.id, 'runs', r.id, f);
            expect(checkTrial(trial, run)).toEqual([]);
            want.push(...needs(trial));
          }
          expect([...new Set(want)].filter((f) => !existsSync(join(IMG, f)))).toEqual([]);
        });
      }
    });
  }

  it('img/ holds only the screenshots some document names, at full size only where rule 10 gives it', () => {
    const want = new Set<string>();
    for (const entry of index.campaigns) {
      const campaign = read<CampaignDoc>('campaigns', entry.id, 'campaign.json');
      for (const r of campaign.runs) {
        const run = read<RunDoc>('campaigns', entry.id, 'runs', `${r.id}.json`);
        for (const sha of run.tasks.img) if (sha) want.add(`${sha}.t.webp`);
        for (const t of run.trials) for (const f of needs(read<TrialDoc>('campaigns', entry.id, 'runs', r.id, `${t.id}.json`))) want.add(f);
      }
    }
    const files = existsSync(IMG) ? readdirSync(IMG) : [];
    expect(files.filter((f) => !want.has(f))).toEqual([]);
  });
});
