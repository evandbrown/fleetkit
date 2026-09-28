// D73: each campaign, run and trial page links what is public on GitHub: the definition, the page's dataset file,
// and the harness at the commit the run used. The dataset holds paths and commits; src/lib/repo.ts makes the links.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { checkCampaign, checkRun } from '../../src/lib/contract';
import { campaignSource, runSource, trialSource } from '../../src/lib/repo';
import type { CampaignDoc, Index, RunDoc } from '../../src/lib/types';

const FIXTURES = join(__dirname, '../fixtures/data');
const PUBLISHED = join(__dirname, '../../public/data');
const read = <T>(root: string, ...p: string[]): T => JSON.parse(readFileSync(join(root, ...p), 'utf8')) as T;
const GH = 'https://github.com/evandbrown/fleetkit';
const CAP_COMMIT = '652f26d88cda86a453e31e29f9def2e771dd4971';
const OTHER_COMMIT = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

describe('source links', () => {
  const cap = read<CampaignDoc>(FIXTURES, 'campaigns', 'cap-baseline-1', 'campaign.json');
  const capRun = read<RunDoc>(FIXTURES, 'campaigns', 'cap-baseline-1', 'runs', 'baseline-r1.json');

  it('give a campaign its definition, its campaign.json and its harness commit', () => {
    expect(campaignSource(cap)).toEqual([
      { label: 'Definition', href: `${GH}/blob/main/docs/capacity-experiment.md` },
      { label: 'Data', href: `${GH}/blob/main/site/public/data/campaigns/cap-baseline-1/campaign.json` },
      { label: 'Harness @ 652f26d', href: `${GH}/tree/${CAP_COMMIT}` },
    ]);
  });

  it("give a run and a trial their own document and the run's commit", () => {
    expect(runSource(cap, capRun).map((l) => l.href)).toEqual([
      `${GH}/blob/main/docs/capacity-experiment.md`,
      `${GH}/blob/main/site/public/data/campaigns/cap-baseline-1/runs/baseline-r1.json`,
      `${GH}/tree/${CAP_COMMIT}`,
    ]);
    expect(trialSource(cap, capRun, 'd8-t2')[1]).toEqual({
      label: 'Data',
      href: `${GH}/blob/main/site/public/data/campaigns/cap-baseline-1/runs/baseline-r1/d8-t2.json`,
    });
  });

  it('link each distinct harness commit of a campaign once, in run order, and skip runs that recorded none', () => {
    const c = structuredClone(cap);
    const [r] = c.runs;
    c.runs = [r, { ...r, id: 'b-r1', harness_commit: OTHER_COMMIT }, { ...r, id: 'c-r1', harness_commit: null }, { ...r, id: 'd-r1' }];
    expect(campaignSource(c).map((l) => l.label)).toEqual(['Definition', 'Data', 'Harness @ 652f26d', 'Harness @ bbbbbbb']);
  });

  it('leave out a definition the repository does not have', () => {
    expect(campaignSource({ ...cap, definition_path: null }).map((l) => l.label)).toEqual(['Data', 'Harness @ 652f26d']);
  });

  it('are never made for synthetic data, which is not on GitHub', () => {
    const syn = read<CampaignDoc>(FIXTURES, 'campaigns', 'nested-sizes-synthetic', 'campaign.json');
    const run = read<RunDoc>(FIXTURES, 'campaigns', 'nested-sizes-synthetic', 'runs', `${syn.runs[0].id}.json`);
    expect(campaignSource(syn)).toEqual([]);
    expect(runSource(syn, run)).toEqual([]);
  });

  it('link every published campaign to its definition file and every run to a full commit', () => {
    const index = read<Index>(PUBLISHED, 'index.json');
    for (const e of index.campaigns) {
      const c = read<CampaignDoc>(PUBLISHED, 'campaigns', e.id, 'campaign.json');
      const labels = campaignSource(c).map((l) => l.label);
      expect(labels.slice(0, 2)).toEqual(['Definition', 'Data']);
      expect(labels.slice(2).length).toBeGreaterThan(0);
      if (!c.reconstructed) expect(c.definition_path).toBe(`experiments/campaigns/${c.id}.json`);
      for (const r of c.runs) expect(r.harness_commit).toMatch(/^[0-9a-f]{40}$/);
    }
  });
});

describe('the contract checks the links (rule 9)', () => {
  const cap = read<CampaignDoc>(FIXTURES, 'campaigns', 'cap-baseline-1', 'campaign.json');
  const run = read<RunDoc>(FIXTURES, 'campaigns', 'cap-baseline-1', 'runs', 'baseline-r1.json');
  const syn = read<CampaignDoc>(FIXTURES, 'campaigns', 'nested-sizes-synthetic', 'campaign.json');

  it('refuses a short or dirty harness commit', () => {
    for (const bad of ['652f26d', `${CAP_COMMIT}-dirty`]) {
      const c = structuredClone(cap);
      c.runs[0].harness_commit = bad;
      expect(checkCampaign(c).join('\n')).toMatch(/harness_commit should be a full commit id or null/);
    }
  });
  it('refuses a missing field, which must be null instead', () => {
    const c = structuredClone(cap) as Partial<CampaignDoc>;
    delete c.definition_path;
    expect(checkCampaign(c as CampaignDoc).join('\n')).toMatch(/definition_path is missing/);
  });
  it("refuses a definition path that isn't the campaign's", () => {
    expect(checkCampaign({ ...cap, definition_path: 'experiments/campaigns/other.json' }).join('\n')).toMatch(
      /definition_path should be docs\/capacity-experiment\.md/,
    );
  });
  it('refuses links in synthetic data', () => {
    const c = structuredClone(syn);
    c.definition_path = `experiments/campaigns/${c.id}.json`;
    c.runs[0].harness_commit = CAP_COMMIT;
    const p = checkCampaign(c).join('\n');
    expect(p).toMatch(/definition_path should be null/);
    expect(p).toMatch(/harness_commit should be null in a synthetic campaign/);
  });
  it("refuses a run document whose commit differs from its campaign's entry", () => {
    expect(checkRun({ ...run, harness_commit: OTHER_COMMIT }, cap)).toContain("run cap-baseline-1/baseline-r1: harness_commit differs from the campaign's entry");
  });
});
