<script lang="ts">
  // Results, with one campaign selected. The campaigns to choose from; then the campaign's answer in a line, what it
  // tested and its SLOs in one strip, and the charts: how each spec did, latency by density with every trial under
  // it on the same columns, and one trial at the limit. The spec-by-spec table of what we tested, then its links
  // (a new campaign from this; its Source on GitHub, D73) close the page.
  // The landing page is this view on the featured campaign (D58).
  import { loadCampaign, loadIndex, loadRun } from '../lib/data';
  import { campaignSource } from '../lib/repo';
  import { href } from '../lib/router';
  import { answerRows, answerText, campaignRefs, chartRows, latency, sharedBands, specResults } from '../lib/shape';
  import type { RunDoc } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import Chooser from '../components/Chooser.svelte';
  import SpecMatrix from '../components/SpecMatrix.svelte';
  import TestedStrip from '../components/TestedStrip.svelte';
  import AnswerChart from '../components/AnswerChart.svelte';
  import LatencyChart from '../components/LatencyChart.svelte';
  import ResultChart from '../components/ResultChart.svelte';
  import SourceLinks from '../components/SourceLinks.svelte';

  let { id }: { id: string } = $props();
  const index = loadIndex();
  /** The campaign and its runs' documents (each trial's latencies and closest SLO). */
  const data = $derived(
    loadCampaign(id).then(async (c) => {
      const runs = await Promise.all(c.runs.map((r) => loadRun(c.id, r.id).catch(() => null)));
      return { c, runs: new Map(runs.filter((r): r is RunDoc => !!r).map((r) => [`${c.id}/${r.id}`, r])) };
    }),
  );
  const PANEL = 'campaign-panel';
  /** At the limit draws a trial with the trial page's charts: they load with it, below the fold. */
  const atLimit = import('../components/AtLimit.svelte');
</script>

{#await index then i}
  <Chooser index={i} selected={id} panel={PANEL} />
{/await}

<div class="panel-body" id={PANEL} role="tabpanel" aria-labelledby="campaign-tab-{id}">
  {#await data}
    <p class="status">Loading…</p>
  {:then { c, runs }}
    {@const refs = campaignRefs(c)}
    {@const docs = new Map([[c.id, c]])}
    {@const results = specResults(refs, docs)}
    {@const lat = latency(refs, docs, runs)}
    {@const rows = chartRows(refs, docs, runs)}
    {@const bands = sharedBands(lat, rows)}
    {#if c.synthetic}<SyntheticBanner />{/if}

    <header class="top">
      <h1>{c.title}</h1>
      <p class="lead">{answerText(results)}</p>
    </header>
    <TestedStrip {refs} replicas={c.definition.replicas} />

    <section>
      <h2>How it performed</h2>
      <AnswerChart rows={answerRows(results, docs)} />

      {#if lat.series.some((s) => s.points.length)}
        <h3>Latency by density</h3>
        <LatencyChart data={lat} {bands} />
      {/if}

      <h3>Every trial</h3>
      <ResultChart {rows} {bands} perVcpu={lat.perVcpu} caption="Every trial of every run in {c.title}, by density" />

      <h3>At the limit</h3>
      {#await atLimit}
        <p class="status">Loading…</p>
      {:then m}
        <m.default {refs} {c} {runs} />
      {:catch}
        <p class="status error">This chart didn't load. Reload to try again.</p>
      {/await}
    </section>

    <section>
      <h2>What we tested</h2>
      <SpecMatrix items={refs.map((r) => ({ key: r.spec.name, title: r.groupLabel, why: r.spec.why, spec: r.spec }))} />
    </section>

    <div class="next">
      <a class="quiet" href={href({ name: 'builder', from: `campaign:${c.id}` })}>New campaign from this</a>
      <SourceLinks links={campaignSource(c)} />
    </div>
  {:catch e}
    <Failed error={e} />
  {/await}
</div>

<style>
  .top h1 {
    margin-top: 20px;
    margin-bottom: 4px;
  }
  .lead {
    margin: 0;
    max-width: none;
    color: var(--ink);
  }
  @media (max-width: 600px) {
    .lead {
      font-size: 1.02rem;
    }
  }
  section > :global(h2) {
    margin-top: 28px;
  }
  h3 {
    margin-top: 32px;
  }
  .next {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px 24px;
    margin: 40px 0 0;
    padding-top: 16px;
    border-top: 1px solid var(--rule);
  }
</style>
