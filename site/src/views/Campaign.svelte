<script lang="ts">
  // Results, with one campaign selected: the chooser, the question, what we tested, the success criteria, then how
  // it performed. The landing page is this view on the featured campaign (D58).
  import { loadCampaign, loadIndex, loadRun } from '../lib/data';
  import { href } from '../lib/router';
  import { campaignRefs, chartRows, compareRows, headline, sameCriteria, specResults } from '../lib/shape';
  import type { RunDoc } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import Chooser from '../components/Chooser.svelte';
  import SpecMatrix from '../components/SpecMatrix.svelte';
  import SloBadges from '../components/SloBadges.svelte';
  import Headlines from '../components/Headlines.svelte';
  import CompareTable from '../components/CompareTable.svelte';
  import ResultChart from '../components/ResultChart.svelte';

  let { id }: { id: string } = $props();
  /** The index, the campaign, and its runs' documents (for each trial's closest SLO on the chart). */
  const data = $derived(
    Promise.all([loadIndex(), loadCampaign(id)]).then(async ([index, c]) => {
      const runs = await Promise.all(c.runs.map((r) => loadRun(c.id, r.id).catch(() => null)));
      return { index, c, runs: new Map(runs.filter((r): r is RunDoc => !!r).map((r) => [`${c.id}/${r.id}`, r])) };
    }),
  );
</script>

{#await data}
  <p class="status">Loading…</p>
{:then { index, c, runs }}
  {@const refs = campaignRefs(c)}
  {@const docs = new Map([[c.id, c]])}
  {@const results = specResults(refs, docs)}
  {@const h = results.length === 1 ? headline(results[0]) : null}
  {@const onlyRun = c.runs.length === 1 ? c.runs[0] : null}
  <Chooser {index} selected={c.id} />
  {#if c.synthetic}<SyntheticBanner />{/if}

  <header class="top">
    <h1>{c.title}</h1>
    <p class="lead">{c.question}</p>
  </header>

  <section>
    <h2>What we tested</h2>
    <SpecMatrix items={refs.map((r) => ({ key: r.spec.name, title: r.groupLabel, why: r.spec.why, spec: r.spec }))} />
  </section>

  <section>
    <h2>Success criteria</h2>
    {#if sameCriteria(c.specs)}
      <SloBadges criteria={c.specs[0].spec.criteria} />
    {:else}
      {#each refs as r (r.spec.name)}<h3>{r.groupLabel}</h3><SloBadges criteria={r.spec.spec.criteria} />{/each}
    {/if}
  </section>

  <section>
    <h2>How it performed</h2>
    {#if h}
      <Headlines
        {h}
        ranOutHref={onlyRun && h.ranOutDensity !== null ? href({ name: 'run', campaign: c.id, run: onlyRun.id, density: h.ranOutDensity }) : null}
      />
    {:else}
      <CompareTable rows={compareRows(results)} />
    {/if}
    <ResultChart rows={chartRows(refs, docs, runs)} caption="Every trial of every run in {c.title}, by density" />
  </section>

  <p class="next"><a class="quiet" href={href({ name: 'builder', from: `campaign:${c.id}` })}>New campaign from this</a></p>
{:catch e}
  <Failed error={e} />
{/await}

<style>
  .top h1 {
    margin-top: 28px;
  }
  .lead {
    margin: 8px 0 0;
    max-width: 80ch;
  }
  .next {
    margin: 40px 0 0;
    padding-top: 16px;
    border-top: 1px solid var(--rule);
    max-width: none;
  }
</style>
