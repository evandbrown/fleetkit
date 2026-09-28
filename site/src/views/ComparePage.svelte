<script lang="ts">
  // Compare specs (D61): any published specs, from any campaigns, on one scale (density per host vCPU across host
  // sizes, D52): what they tested in a strip, then the charts, then the spec-by-spec table. Specs are grouped under
  // their campaign. When their SLOs differ, one line above the charts says how (D74). The choice lives in the URL.
  import { loadCampaign, loadIndex, loadRun } from '../lib/data';
  import { href } from '../lib/router';
  import { answerRows, campaignRefs, chartRows, latency, sharedBands, sloDifferences, specResults, type SpecRef } from '../lib/shape';
  import type { CampaignDoc, RunDoc } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import SpecMatrix from '../components/SpecMatrix.svelte';
  import TestedStrip from '../components/TestedStrip.svelte';
  import AnswerChart from '../components/AnswerChart.svelte';
  import LatencyChart from '../components/LatencyChart.svelte';
  import ResultChart from '../components/ResultChart.svelte';

  let { specs }: { specs: string[] | null } = $props();

  /** Every published campaign document, in the index's order (newest first). */
  const all = loadIndex().then(async (i) => ({
    index: i,
    docs: new Map((await Promise.all(i.campaigns.map((c) => loadCampaign(c.id)))).map((d) => [d.id, d] as const)),
  }));

  const key = (c: string, s: string) => `${c}/${s}`;

  /** With nothing chosen: the featured campaign's specs, or every spec if there are only a few. */
  function defaults(docs: Map<string, CampaignDoc>, featured: string | null): string[] {
    const every = [...docs.values()].flatMap((d) => d.specs.map((s) => key(d.id, s.name)));
    if (every.length <= 4) return every;
    const f = featured ? docs.get(featured) : undefined;
    return f ? f.specs.map((s) => key(f.id, s.name)) : every.slice(0, 2);
  }

  /** The chosen specs, grouped by campaign in the index's order, each labelled as its campaign labels it. */
  function refsFor(chosen: string[], docs: Map<string, CampaignDoc>): SpecRef[] {
    return [...docs.values()].flatMap((d) => campaignRefs(d).filter((r) => chosen.includes(key(d.id, r.spec.name))));
  }

  /** The runs' documents for the chosen specs, for each trial's closest SLO on the chart. */
  async function runsFor(refs: SpecRef[], docs: Map<string, CampaignDoc>): Promise<Map<string, RunDoc>> {
    const want = refs.flatMap((r) => docs.get(r.campaign)!.runs.filter((x) => x.spec === r.spec.name).map((x) => [r.campaign, x.id] as const));
    const got = await Promise.all(want.map(([c, run]) => loadRun(c, run).catch(() => null)));
    return new Map(got.filter((r): r is RunDoc => !!r).map((r) => [`${r.campaign}/${r.id}`, r]));
  }

  function toggle(chosen: string[], k: string, on: boolean) {
    const next = on ? [...chosen, k] : chosen.filter((x) => x !== k);
    location.hash = href({ name: 'compare', specs: next }).slice(1);
  }
</script>

<p class="crumbs"><a href={href({ name: 'results', campaign: null })}>Results</a> › Compare</p>
<h1>Compare specs</h1>

{#await all}
  <p class="status">Loading…</p>
{:then { index, docs }}
  {@const chosen = specs ? specs.filter((k) => refsFor([k], docs).length) : defaults(docs, index.featured)}
  {@const refs = refsFor(chosen, docs)}
  {@const results = specResults(refs, docs)}
  {@const campaigns = new Set(refs.map((r) => r.campaign)).size > 1}
  {@const sloDiff = sloDifferences(refs)}
  {#if refs.some((r) => docs.get(r.campaign)?.synthetic)}<SyntheticBanner />{/if}

  <fieldset class="picker">
    <legend class="pl">Pick specs to compare</legend>
    <div class="campaign cols" aria-hidden="true"><span class="cname">Campaign</span><span>Specs</span></div>
    {#each index.campaigns as c (c.id)}
      <div class="campaign">
        <span class="cname">{c.title}</span>
        <div class="chips">
          {#each campaignRefs(docs.get(c.id)!) as r (r.spec.name)}
            {@const k = key(c.id, r.spec.name)}
            <label class:on={chosen.includes(k)}>
              <input
                type="checkbox"
                checked={chosen.includes(k)}
                onchange={(e) => toggle(chosen, k, (e.currentTarget as HTMLInputElement).checked)}
              />
              {r.groupLabel}
            </label>
          {/each}
        </div>
      </div>
    {/each}
  </fieldset>

  {#if refs.length === 0}
    <p class="muted">Pick one or more specs.</p>
  {:else}
    <TestedStrip {refs} replicas={0} />
    <section>
      <h2>How it performed</h2>
      {#if sloDiff}<p class="slo-diff" role="note"><strong>SLOs differ:</strong> {sloDiff}</p>{/if}
      <AnswerChart rows={answerRows(results, docs)} {campaigns} />
      {#await runsFor(refs, docs) then runs}
        {@const lat = latency(refs, docs, runs)}
        {@const rows = chartRows(refs, docs, runs)}
        {@const bands = sharedBands(lat, rows)}
        {#if lat.series.some((s) => s.points.length)}
          <h3>Latency by density</h3>
          <LatencyChart data={lat} {bands} />
        {/if}
        <h3>Every trial</h3>
        <ResultChart {rows} {bands} perVcpu={lat.perVcpu} caption="Every trial of the chosen specs' runs, by density" />
      {/await}
    </section>

    <section>
      <h2>What we tested</h2>
      <SpecMatrix
        items={refs.map((r) => ({ key: key(r.campaign, r.spec.name), title: r.groupLabel, spec: r.spec, campaignTitle: r.campaignTitle }))}
        {campaigns}
      />
    </section>
  {/if}
{:catch e}
  <Failed error={e} />
{/await}

<style>
  h3 {
    margin-top: 36px;
  }
  .slo-diff {
    margin: -4px 0 14px;
    padding: 6px 12px;
    max-width: none;
    border-radius: 6px;
    background: var(--highlight);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent);
    font-size: 0.9rem;
    color: var(--ink);
  }
  .slo-diff strong {
    color: var(--accent-ink);
    font-weight: 650;
  }
  .picker {
    border: 0;
    padding: 0;
    margin: 16px 0 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }
  .pl {
    padding: 0;
    margin-bottom: 2px;
    font-weight: 650;
    font-size: 0.95rem;
  }
  .cols {
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--muted);
    margin-bottom: -4px;
  }
  .cols .cname {
    font-size: 0.75rem;
  }
  @media (max-width: 560px) {
    .cols {
      display: none;
    }
  }
  .campaign {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 6px 12px;
  }
  .cname {
    font-weight: 600;
    font-size: 0.9rem;
    min-width: 11rem;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.88rem;
    border: 1px solid var(--rule);
    border-radius: 999px;
    padding: 3px 12px 3px 8px;
    cursor: pointer;
    background: var(--bg);
  }
  label.on {
    border-color: var(--accent);
    background: var(--highlight);
  }
  input {
    accent-color: var(--accent);
    margin: 0;
  }
</style>
