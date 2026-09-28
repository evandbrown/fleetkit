// The SLOs as the pages show them, and how a campaign's differ from the method's standard ones (D74). The standard
// changed on 2026-09-28 (D89, D95): the fixtures, like every campaign published before then, were judged by the
// earlier targets and stand as judged, so they carry a tag.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { campaignRefs, latency, sloChips, sloDifferences, sloDiffs, sloTag, sloTime, type Criteria, type SpecRef } from '../../src/lib/shape';
import { STANDARD_CRITERIA } from '../../src/lib/spec';
import type { CampaignDoc } from '../../src/lib/types';

const DATA = join(__dirname, '../fixtures/data/campaigns');
const read = <T>(...p: string[]): T => JSON.parse(readFileSync(join(DATA, ...p), 'utf8')) as T;
const cap = read<CampaignDoc>('cap-baseline-1', 'campaign.json');
const syn = read<CampaignDoc>('nested-sizes-synthetic', 'campaign.json');
const schema = JSON.parse(readFileSync(join(__dirname, '../../../experiments/schema/spec.schema.json'), 'utf8'));

/** Text as read, no-break spaces as spaces. */
const plain = (s: string | null) => s?.replaceAll(' ', ' ') ?? null;

/** The targets every campaign before 28 September 2026 was judged by, the fixtures among them. */
const EARLIER: Criteria = { ...STANDARD_CRITERIA, step_p50_target_ms: 1000, step_p95_target_ms: 2000, task_p95_target_ms: 5000 };
const TIGHTER = 'Tighter SLOs: step p50 ≤ 1 s, step p95 ≤ 2 s, task p95 ≤ 5 s';
/** The metal campaign's own targets (hv-host-2, D74): the standard step targets, a tighter task target, a longer wait for ready. */
const METAL: Criteria = { ...STANDARD_CRITERIA, task_p95_target_ms: 5000, ready_timeout_s: 900 };
/** Looser than the standard in every way. */
const LOOSER: Criteria = { ...STANDARD_CRITERIA, step_p50_target_ms: 3000, step_p95_target_ms: 4000, ready_timeout_s: 900 };

/** A copy of a campaign whose every spec is judged by `criteria`. */
function judgedBy(c: CampaignDoc, criteria: Criteria, id = `${c.id}-x`, title = `${c.title} x`): CampaignDoc {
  return { ...c, id, title, specs: c.specs.map((s) => ({ ...s, spec: { ...s.spec, criteria } })) };
}

describe('the standard SLOs', () => {
  it('are the input schema defaults, every one of them', () => {
    const props = schema.$defs.criteria.properties as Record<string, { default: number }>;
    expect(Object.keys(STANDARD_CRITERIA).sort()).toEqual(Object.keys(props).sort());
    for (const [k, v] of Object.entries(props)) expect(STANDARD_CRITERIA[k as keyof Criteria]).toBe(v.default);
    // The design's SLOs (D89): step p50 2 s, step p95 3 s, task p95 10 s.
    expect(STANDARD_CRITERIA).toMatchObject({ ready_timeout_s: 180, step_p50_target_ms: 2000, step_p95_target_ms: 3000, task_p95_target_ms: 10000 });
  });

  it('are not what the fixtures were judged by: those used the earlier targets, and stand as judged', () => {
    expect(cap.specs[0].spec.criteria).toEqual(EARLIER);
    expect(syn.specs[0].spec.criteria).toEqual(EARLIER);
  });
});

describe('SLO chips', () => {
  it('are the five, in the order every page shows them, in plain units', () => {
    expect(sloChips(STANDARD_CRITERIA).map((c) => [c.short, c.value])).toEqual([
      ['Ready', '≤ 180 s'],
      ['Tasks', '100%'],
      ['Step p50', '≤ 2 s'],
      ['Step p95', '≤ 3 s'],
      ['Task p95', '≤ 10 s'],
    ]);
    expect(sloChips(STANDARD_CRITERIA).map((c) => c.long)).toEqual(['Browser ready', 'Tasks succeed', 'Each step p50', 'Each step p95', 'Whole task p95']);
    expect(sloChips(STANDARD_CRITERIA).every((c) => c.standard === null)).toBe(true);
    expect([sloTime(750), sloTime(1500), sloTime(1250), sloTime(2000)]).toEqual(['750 ms', '1.5 s', '1.25 s', '2 s']);
  });

  it('give the standard beside each that differs from it', () => {
    expect(sloChips(EARLIER).map((c) => [c.short, c.value, c.standard])).toEqual([
      ['Ready', '≤ 180 s', null],
      ['Tasks', '100%', null],
      ['Step p50', '≤ 1 s', '≤ 2 s'],
      ['Step p95', '≤ 2 s', '≤ 3 s'],
      ['Task p95', '≤ 5 s', '≤ 10 s'],
    ]);
    expect(sloChips(METAL).map((c) => [c.short, c.value, c.standard])).toEqual([
      ['Ready', '≤ 900 s', '≤ 180 s'],
      ['Tasks', '100%', null],
      ['Step p50', '≤ 2 s', null],
      ['Step p95', '≤ 3 s', null],
      ['Task p95', '≤ 5 s', '≤ 10 s'],
    ]);
  });

  it('add a time limit only where it differs, and only when asked', () => {
    expect(sloChips(STANDARD_CRITERIA, true)).toHaveLength(5);
    const slow = { ...STANDARD_CRITERIA, step_timeout_ms: 20000 };
    expect(sloChips(slow)).toHaveLength(5);
    expect(sloChips(slow, true).at(-1)).toMatchObject({ short: 'Step limit', value: '20 s', standard: '10 s' });
  });
});

describe('a campaign with SLOs of its own', () => {
  it('names each difference, the latency targets first', () => {
    expect(sloDiffs(STANDARD_CRITERIA)).toEqual([]);
    expect(sloDiffs(EARLIER).map((d) => [plain(d.text), d.looser])).toEqual([
      ['step p50 ≤ 1 s', false],
      ['step p95 ≤ 2 s', false],
      ['task p95 ≤ 5 s', false],
    ]);
    expect(sloDiffs(METAL).map((d) => [plain(d.text), d.looser])).toEqual([
      ['task p95 ≤ 5 s', false],
      ['ready ≤ 900 s', true],
    ]);
  });

  it('gets a short tag that says which way and names the difference', () => {
    expect(plain(sloTag([STANDARD_CRITERIA, STANDARD_CRITERIA]))).toBeNull();
    // Every campaign judged before the standard changed.
    expect(plain(sloTag([EARLIER]))).toBe(TIGHTER);
    // A line breaks between differences, never inside one.
    expect(sloTag([EARLIER])).toContain('step p95 ≤ 2 s, task');
    expect(plain(sloTag([LOOSER]))).toBe('Looser SLOs: step p50 ≤ 3 s, step p95 ≤ 4 s, ready ≤ 900 s');
    expect(plain(sloTag([{ ...STANDARD_CRITERIA, step_p50_target_ms: 3000 }]))).toBe('Looser SLO: step p50 ≤ 3 s');
    expect(plain(sloTag([{ ...STANDARD_CRITERIA, task_p95_target_ms: 4000 }]))).toBe('Tighter SLO: task p95 ≤ 4 s');
    // Tighter in one way and looser in another: the metal campaign. Each group is named, so the reader sees which
    // way each target went.
    expect(plain(sloTag([METAL]))).toBe('Tighter SLO: task p95 ≤ 5 s · looser: ready ≤ 900 s');
    expect(plain(sloTag([{ ...EARLIER, ready_timeout_s: 900 }]))).toBe('Tighter SLOs: step p50 ≤ 1 s, step p95 ≤ 2 s, task p95 ≤ 5 s · looser: ready ≤ 900 s');
    // Across a campaign's specs, each difference once.
    expect(plain(sloTag([EARLIER, EARLIER, STANDARD_CRITERIA]))).toBe(TIGHTER);
  });
});

describe('specs compared side by side', () => {
  // The fixtures as they are (the earlier targets), one campaign judged by the standard, and one by the metal targets.
  const std = judgedBy(syn, STANDARD_CRITERIA, 'std', 'Standard');
  const metal = judgedBy(cap, METAL, 'hv-host-x', 'Metal host');
  const both: SpecRef[] = [...campaignRefs(std), ...campaignRefs(metal)];

  it('say nothing when every spec shown has the same SLOs, standard or not', () => {
    expect(plain(sloDifferences(campaignRefs(syn)))).toBeNull();
    expect(plain(sloDifferences(campaignRefs(std)))).toBeNull();
    expect(plain(sloDifferences(campaignRefs(metal)))).toBeNull();
    expect(plain(sloDifferences([...campaignRefs(syn), ...campaignRefs(cap)]))).toBeNull();
  });

  it('name the campaign that differs from the standard, and how', () => {
    expect(plain(sloDifferences(both))).toBe('Metal host uses task p95 ≤ 5 s and ready ≤ 900 s; the rest use the standard.');
    expect(plain(sloDifferences([...campaignRefs(std), ...campaignRefs(syn)]))).toBe(
      'Synthetic host sizes uses step p50 ≤ 1 s, step p95 ≤ 2 s and task p95 ≤ 5 s; the rest use the standard.',
    );
  });

  it('name a spec when its campaign is split, and every group when none is standard', () => {
    const split = { ...std, specs: [std.specs[0], { ...std.specs[1], spec: { ...std.specs[1].spec, criteria: METAL } }] };
    expect(plain(sloDifferences(campaignRefs(split)))).toBe('m8i.2xlarge uses task p95 ≤ 5 s and ready ≤ 900 s; the rest use the standard.');
    expect(plain(sloDifferences([...campaignRefs(syn), ...campaignRefs(metal)]))).toBe(
      'Synthetic host sizes uses step p50 ≤ 1 s, step p95 ≤ 2 s and task p95 ≤ 5 s; Metal host uses task p95 ≤ 5 s and ready ≤ 900 s.',
    );
  });

  it("name whose each latency limit is on the chart, and nothing when they share one", () => {
    const docs = new Map([syn, metal].map((d) => [d.id, d] as const));
    const lat = latency([...campaignRefs(syn), ...campaignRefs(metal)], docs, new Map());
    // The fixture's step limit is its own; the metal campaign's is the standard. Both hold the task to 5 s.
    expect([lat.stepTargets, lat.stepTargetWho]).toEqual([[1000, 2000], ['Synthetic host sizes', 'standard']]);
    expect([lat.taskTargets, lat.taskTargetWho]).toEqual([[5000], [null]]);
    const alone = latency(campaignRefs(syn), docs, new Map());
    expect(alone.stepTargetWho).toEqual([null]);
  });
});
