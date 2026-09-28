// The published dataset (public/data) against the builder: every campaign's definition uses the current schema's
// field names, so "New campaign from this" opens it with no errors.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { evaluate } from '../../src/lib/campaign';
import { fromLink } from '../../src/components/builder/draft';
import type { CampaignDoc, Index } from '../../src/lib/types';

const DATA = join(__dirname, '../../public/data');
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const index = read<Index>('index.json');

beforeAll(() => {
  // The site fetches ./data/...; here that is public/data.
  vi.stubGlobal('fetch', async (url: string) => {
    const path = url.replace(/^\.\/data\//, '');
    try {
      const body = readFileSync(join(DATA, path), 'utf8');
      return { ok: true, status: 200, json: async () => JSON.parse(body) };
    } catch {
      return { ok: false, status: 404, json: async () => null };
    }
  });
});
afterAll(() => vi.unstubAllGlobals());

describe('the published dataset in the builder', () => {
  it('has at least one campaign', () => {
    expect(index.campaigns.length).toBeGreaterThan(0);
  });

  for (const e of index.campaigns) {
    it(`opens ${e.id} with no errors`, async () => {
      const c = read<CampaignDoc>('campaigns', e.id, 'campaign.json');
      const d = await fromLink(`campaign:${e.id}`);
      const ev = evaluate(d.def);
      expect(ev.errors).toEqual([]);
      expect(ev.valid).toBe(true);
      // The builder resolves the same specs the site shows.
      expect(ev.specs.map(([name, spec]) => [name, spec])).toEqual(c.specs.map((s) => [s.name, s.spec]));
    });

    it(`opens each run of ${e.id} as a new campaign of its spec, waiting only for a name and a question`, async () => {
      const c = read<CampaignDoc>('campaigns', e.id, 'campaign.json');
      for (const r of c.runs) {
        const d = await fromLink(`run:${e.id}/${r.id}`);
        expect(evaluate(d.def).errors.filter((x) => !/^(name|question):/.test(x))).toEqual([]);
      }
    });
  }
});
