<script lang="ts">
  // Results, with one campaign selected. The campaign picker; then the campaign's question and its answer in a line,
  // and four sections of equal standing, each with a heading and one sentence saying what it shows: what it tested
  // (one table, a column per spec, on a full-width band), how each spec performed (one row per spec, with every trial
  // and replica folded under it), latency by browsers per host, and one trial at the limit. Its links (a new campaign
  // from this; its Source on GitHub, D73) close the page.
  // The landing page is this view on the featured campaign (D58).
  import { loadCampaign, loadIndex, loadRun } from '../lib/data';
  import { campaignSource } from '../lib/repo';
  import { href } from '../lib/router';
  import { answerRows, answerText, campaignRefs, chartRows, latency, sharedBands, specResults } from '../lib/shape';
  import type { RunDoc } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import CampaignPicker from '../components/CampaignPicker.svelte';
  import SpecTable from '../components/SpecTable.svelte';
  import AnswerChart from '../components/AnswerChart.svelte';
  import LatencyChart from '../components/LatencyChart.svelte';
  import ResultChart from '../components/ResultChart.svelte';
  import SourceLinks from '../components/SourceLinks.svelte';

  let { id }: { id: string } = $props();
  const index = loadIndex();
  /** The campaign, its runs' documents (each trial's latencies and closest SLO), and the catalog's featured spec. */
  const data = $derived(
    Promise.all([index, loadCampaign(id)]).then(async ([i, c]) => {
      const runs = await Promise.all(c.runs.map((r) => loadRun(c.id, r.id).catch(() => null)));
      return {
        c,
        runs: new Map(runs.filter((r): r is RunDoc => !!r).map((r) => [`${c.id}/${r.id}`, r])),
        featured: i.campaigns.find((e) => e.id === c.id)?.featured_spec ?? null,
      };
    }),
  );
  const PANEL = 'campaign-panel';
  /** At the limit draws a trial with the trial page's charts: they load with it, below the fold. */
  const atLimit = import('../components/AtLimit.svelte');
</script>

{#await index then i}
  <CampaignPicker index={i} selected={id} panel={PANEL} />
{/await}

<!-- The picker's button names this panel through aria-controls. -->
<div class="panel-body" id={PANEL}>
  {#await data}
    <p class="status">Loading…</p>
  {:then { c, runs, featured }}
    {@const refs = campaignRefs(c)}
    {@const docs = new Map([[c.id, c]])}
    {@const results = specResults(refs, docs)}
    {@const lat = latency(refs, docs, runs)}
    {@const rows = chartRows(refs, docs, runs)}
    {@const bands = sharedBands(lat, rows)}
    {#if c.synthetic}<SyntheticBanner />{/if}

    <header class="top">
      <h1>{c.title}</h1>
      <p class="question">{c.question}</p>
      <p class="lead">{c.answer ?? answerText(results)}</p>
    </header>

    <section class="band">
      <h2>What we tested</h2>
      <p class="sub">
        {#if refs.length > 1}
          Each spec is one configuration of host, microVM and guest. Rows that differ between specs are bold.
        {:else}
          The one configuration of host, microVM and guest that ran, and the SLOs it was judged by.
        {/if}
      </p>
      <SpecTable {refs} replicas={c.definition.replicas} />
    </section>

    <section>
      <h2>How it performed</h2>
      <p class="sub">The last browser count that met every SLO and the first that failed, replica by replica, with what 1,000 tasks cost there. A clear winner on cost is highlighted.</p>
      <AnswerChart rows={answerRows(results, docs, featured)} />
      <details class="trials">
        <summary>Every trial and replica <span class="runs">· {c.runs.length} {c.runs.length === 1 ? 'run' : 'runs'}</span></summary>
        <ResultChart {rows} {bands} perVcpu={lat.perVcpu} hint={false} caption="Every trial of every run in {c.title}, by browsers per host" />
      </details>
    </section>

    {#if lat.series.some((s) => s.points.length)}
      <section>
        <h2>Latency by browsers per host</h2>
        <p class="sub">Each trial's slowest step against its SLO, as browsers are added. It shows how close each spec ran to the line before it failed.</p>
        <LatencyChart data={lat} {bands} />
      </section>
    {/if}

    <section>
      <h2>At the limit</h2>
      <p class="sub">
        For one spec at a time, the first trial that failed, shown the way a trial page shows it: which SLO it missed, what ran out on the host, and each microVM's timeline. Last pass puts the last passing count beside it so the jump is visible; this is the evidence behind the headline.
      </p>
      {#await atLimit}
        <p class="status">Loading…</p>
      {:then m}
        <m.default {refs} {c} {runs} />
      {:catch}
        <p class="status error">This chart didn't load. Reload to try again.</p>
      {/await}
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
  summary .runs {
    color: var(--muted);
    font-weight: 400;
  }
  .top h1 {
    margin-top: 20px;
    margin-bottom: 4px;
  }
  /* The question the campaign answers, over its answer. */
  .question {
    margin: 0 0 6px;
    max-width: none;
    color: var(--ink-2);
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
  /* Sections of equal standing: a heading, one sentence, the figure; 72 px between them. */
  section {
    margin-top: 72px;
  }
  .top + section {
    margin-top: 40px;
  }
  section > :global(h2) {
    margin-top: 0;
  }
  /* What we tested is setup, not result: it sits on a band the full width of the window. The band is a pseudo-element
     behind the section, its shadow spread to the window's edges and clipped to the section's height, so it adds no
     width to the page and clips nothing inside the section. */
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
  .trials {
    margin-top: 16px;
    font-size: 0.9rem;
  }
  .trials summary {
    font-weight: 600;
  }
  .next {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px 24px;
    margin: 56px 0 0;
    padding-top: 16px;
    border-top: 1px solid var(--rule);
  }
</style>
