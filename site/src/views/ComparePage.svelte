<script lang="ts">
  // Compare specs (D61): any published specs, from any campaigns, on one scale (density per host vCPU across host
  // sizes, D52). A two-column picker (campaign · its specs as checkboxes), then the same sections as a campaign page:
  // what they tested in one table, each campaign named over its specs; how each performed, one row per spec, with
  // every trial folded under it; latency by browsers per host. When their SLOs differ, one line above the charts says
  // how (D74). The choice lives in the URL.
  import { loadCampaign, loadIndex, loadRun } from '../lib/data';
  import { href } from '../lib/router';
  import { answerRows, campaignRefs, chartRows, latency, sharedBands, sloDifferences, specResults, type SpecRef } from '../lib/shape';
  import type { CampaignDoc, RunDoc } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import SpecTable from '../components/SpecTable.svelte';
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
    <table>
      <thead>
        <tr class="cols">
          <th class="cname" scope="col">Campaign</th>
          <th scope="col">Specs</th>
        </tr>
      </thead>
      <tbody>
        {#each index.campaigns as c (c.id)}
          <tr class="campaign">
            <th class="cname" scope="row">{c.title}</th>
            <td class="chips">
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
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </fieldset>

  {#if refs.length === 0}
    <p class="muted">Pick one or more specs.</p>
  {:else}
    <section class="band">
      <h2>What we tested</h2>
      <p class="sub">Each spec is one configuration of host, microVM and guest, named under its campaign. Rows that differ between specs are bold.</p>
      <SpecTable {refs} replicas={new Map([...docs.values()].map((d) => [d.id, d.definition.replicas]))} {campaigns} />
    </section>
    <section>
      <h2>How it performed</h2>
      <p class="sub">The last browser count that met every SLO and the first that failed, replica by replica, with what 1,000 tasks cost there. A clear winner on cost is highlighted.</p>
      {#if sloDiff}<p class="slo-diff" role="note"><strong>SLOs differ:</strong> {sloDiff}</p>{/if}
      <AnswerChart rows={answerRows(results, docs)} {campaigns} />
      {#await runsFor(refs, docs) then runs}
        {@const lat = latency(refs, docs, runs)}
        {@const rows = chartRows(refs, docs, runs)}
        {@const bands = sharedBands(lat, rows)}
        <details class="trials">
          <summary>Every trial and replica <span class="runs">· {rows.length} {rows.length === 1 ? 'run' : 'runs'}</span></summary>
          <ResultChart {rows} {bands} perVcpu={lat.perVcpu} hint={false} caption="Every trial of the chosen specs' runs, by browsers per host" />
        </details>
      {/await}
    </section>
    {#await runsFor(refs, docs) then runs}
      {@const lat = latency(refs, docs, runs)}
      {@const rows = chartRows(refs, docs, runs)}
      {@const bands = sharedBands(lat, rows)}
      {#if lat.series.some((s) => s.points.length)}
        <section>
          <h2>Latency by browsers per host</h2>
          <p class="sub">Each trial's slowest step against its SLO, as browsers are added. It shows how close each spec ran to the line before it failed.</p>
          <LatencyChart data={lat} {bands} />
        </section>
      {/if}
    {/await}
  {/if}
{:catch e}
  <Failed error={e} />
{/await}

<style>
  summary .runs {
    color: var(--muted);
    font-weight: 400;
  }
  /* Sections of equal standing, 72 px apart, each a heading and one sentence over its figure. */
  section {
    margin-top: 72px;
  }
  .picker + section {
    margin-top: 48px;
  }
  section > :global(h2) {
    margin-top: 0;
  }
  /* What we tested on a band the full width of the window: see Campaign.svelte. */
  .band {
    position: relative;
    z-index: 0;
    padding: 28px 0 32px;
  }
  .band::before {
    content: '';
    position: absolute;
    inset: 0;
    z-index: -1;
    background: var(--surface);
    box-shadow: 0 0 0 100vmax var(--surface);
    clip-path: inset(0 -100vmax);
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
  .trials {
    margin-top: 16px;
    font-size: 0.9rem;
  }
  .trials summary {
    font-weight: 600;
  }
  /* The picker: a borderless two-column table, a campaign's name beside its specs as checkbox labels. */
  .picker {
    border: 0;
    padding: 0;
    margin: 16px 0 0;
    min-width: 0;
  }
  .pl {
    padding: 0;
    margin-bottom: 4px;
    font-weight: 650;
    font-size: 0.95rem;
  }
  .picker table {
    min-width: 0;
    width: auto;
    font-size: 0.9rem;
  }
  .picker th,
  .picker td {
    border: 0;
    padding: 3px 24px 3px 0;
    vertical-align: baseline;
  }
  .cols th {
    padding-bottom: 6px;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--muted);
  }
  .picker tbody tr:hover > th,
  .picker tbody tr:hover > td {
    background: none;
  }
  .cname {
    font-weight: 600;
    color: var(--ink);
    white-space: nowrap;
  }
  .chips {
    padding-right: 0;
  }
  label {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    margin-right: 18px;
    cursor: pointer;
    color: var(--ink-2);
  }
  label.on {
    color: var(--ink);
  }
  input {
    accent-color: var(--accent);
    margin: 0;
    transform: translateY(1px);
  }
  @media (max-width: 560px) {
    /* The header row has nothing to head once a campaign's name sits over its specs. */
    .cols {
      display: none;
    }
    .campaign {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      padding: 4px 0;
    }
    .picker th,
    .picker td {
      padding: 0;
    }
    .picker th {
      padding-bottom: 2px;
    }
  }
</style>
