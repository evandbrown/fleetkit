<script lang="ts" module>
  /** Set when the reader picks a campaign here, so the new page puts focus back on the picker. */
  let refocus = false;
</script>

<script lang="ts">
  // Results: the campaign to show, chosen from a select-style dropdown (Dropdown.svelte), newest first. The closed
  // control names the selected campaign: its title, the question it answers on one line, and its headline figure
  // ("26 microVMs on c8i.4xlarge · 3 specs"). Each option is a card: title, question, and its figure: one spec's
  // density and density per host vCPU, or a small bar per spec of what the campaign compares, in the specs' colours.
  // A campaign judged by other than the standard SLOs says how (D74). Choosing opens the campaign by its address.
  import { onMount } from 'svelte';
  import { specColor } from '../lib/colors';
  import { loadCampaign } from '../lib/data';
  import { href } from '../lib/router';
  import { campaignFigure, campaignHeadline, sloTag, specLabels } from '../lib/shape';
  import type { CampaignEntry, Index } from '../lib/types';
  import Dropdown from './Dropdown.svelte';

  let { index, selected, panel }: { index: Index; selected: string; panel: string } = $props();
  const campaigns = $derived([...index.campaigns].sort((a, b) => (a.started < b.started ? 1 : a.started > b.started ? -1 : 0)));
  const specs = $derived(index.campaigns.reduce((n, c) => n + c.specs.length, 0));

  /**
   * From each campaign's document, once it loads: the specs' short names (the index's labels until then), each
   * spec's host, and a tag if its SLOs aren't the standard ones.
   */
  let names = $state<Record<string, string[]>>({});
  let hosts = $state<Record<string, Record<string, string>>>({});
  let slo = $state<Record<string, string | null>>({});
  const asked = new Set<string>();
  $effect(() => {
    for (const c of index.campaigns) {
      if (asked.has(c.id)) continue;
      asked.add(c.id);
      loadCampaign(c.id)
        .then((d) => {
          const labels = specLabels(d.specs);
          names = {
            ...names,
            [c.id]: c.specs.map((s) => {
              const j = d.specs.findIndex((x) => x.name === s.name);
              return d.specs[j]?.short ?? labels[j] ?? s.label;
            }),
          };
          hosts = { ...hosts, [c.id]: Object.fromEntries(d.specs.map((s) => [s.name, s.host.instance_type])) };
          slo = { ...slo, [c.id]: sloTag(d.specs.map((s) => s.spec.criteria)) };
        })
        .catch(() => {});
    }
  });

  /**
   * The campaign's headline: the density of the spec the catalog features (the same spec About's card leads with),
   * else of its best spec ("8", "3–4"; "≥ 200" when no replica failed), with its unit, that density per host vCPU,
   * and the spec's host once its document loads. Only a label when nothing ran or passed.
   */
  function headline(c: CampaignEntry): { density: string | null; unit: string; perVcpu: string | null; host: string | null } {
    const h = campaignHeadline(c, c.featured_spec ?? null);
    if (h.density === null) return { density: null, unit: h.label, perVcpu: null, host: null };
    const reps = c.outcomes.find((o) => o.spec === h.spec)?.replicas ?? [];
    const noFailure = reps.length > 0 && reps.every((r) => r.first_failed === null);
    return {
      density: `${noFailure ? '≥ ' : ''}${h.density}`,
      unit: h.label,
      perVcpu: h.perVcpu,
      host: (h.spec && hosts[c.id]?.[h.spec]) || null,
    };
  }

  /** The metric over the bars names the unit their figures abbreviate: "0.75 / vCPU" under "microVMs per host vCPU". */
  const metricName = (m: ReturnType<typeof campaignFigure>['metric']) => (m === 'Per vCPU' ? 'microVMs per host vCPU' : m);
  /** A bar's figure says what it counts, compactly: "8 microVMs", "0.75 / vCPU" under "microVMs per host vCPU". */
  const unit = (m: ReturnType<typeof campaignFigure>['metric']) => (m === 'Per vCPU' ? ' / vCPU' : ' microVMs');

  function pick(id: string) {
    refocus = true;
    location.hash = href({ name: 'results', campaign: id });
  }

  let dropdown: { focus(): void } | undefined = $state();
  onMount(() => {
    if (refocus) dropdown?.focus();
    refocus = false;
  });
</script>

<div class="campaign-picker">
  <div class="bar">
    <span class="label" id="campaign-label">Campaign</span>
    {#if specs > 1}<a class="compare" href={href({ name: 'compare', specs: null })}>Compare specs →</a>{/if}
  </div>
  <Dropdown items={campaigns} {selected} label="Campaign" id="campaign-picker" controls={panel} onselect={pick} bind:this={dropdown}>
    {#snippet button(c: CampaignEntry)}
      {@const h = headline(c)}
      <span class="sel">
        <span class="text">
          <span class="title">{c.title}</span>
          <span class="q">{c.question}</span>
        </span>
        <span class="fig">
          <!-- On one line, the spaces written as strings: Svelte trims whitespace at a block's edges and collapses a line break between blocks to a space. -->
          {#if h.density !== null}<strong>{h.density}</strong>{' '}{h.unit}{#if h.host}{' on '}{h.host}{/if}{:else}{h.unit}{/if}{#if c.specs.length > 1}{' · '}{c.specs.length} specs{/if}
        </span>
      </span>
    {/snippet}
    {#snippet option(c: CampaignEntry, on: boolean)}
      {@const fig = campaignFigure(c, names[c.id])}
      <div class="card" class:on>
        <div class="title">
          <span class="t">{c.title}</span>
          {#if on}
            <svg class="check" aria-hidden="true" viewBox="0 0 16 16" width="16" height="16">
              <path d="M3 8.5l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          {/if}
        </div>
        <div class="q">{c.question}</div>
        {#if fig.single}
          {@const h = headline(c)}
          <div class="one">
            {#if h.density !== null}<strong>{h.density}</strong>{' '}{h.unit}{:else}{h.unit}{/if}{#if h.perVcpu !== null}{' · '}<strong>{h.perVcpu}</strong> microVMs per host vCPU{/if}
          </div>
        {:else}
          <div class="bars">
            <span class="metric">{metricName(fig.metric)}</span>
            {#each fig.bars as b, i (b.key)}
              <span class="brow">
                <span class="bl">{b.label}</span>
                <span class="track" aria-hidden="true"
                  ><i style:width="{fig.max ? (b.lo / fig.max) * 100 : 0}%" style:background={specColor(i)}></i
                  >{#if b.hi > b.lo}<i class="spread" style:width="{((b.hi - b.lo) / fig.max) * 100}%" style:--c={specColor(i)}></i>{/if}</span
                >
                <!-- A label ("not run yet", "none passed") has no ends, so no unit. -->
                <span class="bv">{b.text}{#if b.hi > 0}{unit(fig.metric)}{/if}</span>
              </span>
            {/each}
          </div>
        {/if}
        {#if slo[c.id]}<span class="slo-tag">{slo[c.id]}</span>{/if}
      </div>
    {/snippet}
  </Dropdown>
</div>

<style>
  .campaign-picker {
    margin-top: 20px;
  }
  .bar {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 6px 16px;
    margin-bottom: 8px;
  }
  /* The same label style as At the limit's "Spec". */
  .label {
    font-size: 0.88rem;
    font-weight: 700;
    color: var(--ink);
  }
  .compare {
    font-size: 0.88rem;
    font-weight: 600;
    white-space: nowrap;
  }

  /* The closed control: title and question at the left, the figure at the right (under them on a phone). */
  .sel {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 20px;
  }
  .text {
    flex: 1 1 16rem;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .sel .title {
    font-weight: 600;
    font-size: 1rem;
    line-height: 1.35;
  }
  .sel .q {
    font-size: 0.85rem;
    line-height: 1.4;
    color: var(--ink-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .fig {
    flex: none;
    font-size: 0.85rem;
    color: var(--ink-2);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  strong {
    font-weight: 650;
    color: var(--ink);
    font-variant-numeric: tabular-nums;
  }
  .fig strong {
    font-size: 1rem;
  }

  /* An option: a card of the campaign's title, question and figure. */
  .card {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .card .title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-weight: 600;
    line-height: 1.35;
  }
  .card.on .title {
    color: var(--accent-ink);
  }
  .check {
    flex: none;
    color: var(--accent);
  }
  /* The question in full where it fits, two lines at most. */
  .card .q {
    font-size: 0.82rem;
    line-height: 1.4;
    color: var(--ink-2);
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
  }
  .one {
    margin-top: 4px;
    font-size: 0.85rem;
    color: var(--ink-2);
  }
  /* A label column at least 9rem wide, grown to the longest name, so the bars line up from card to card; then the bar
     and its figure right beside it. The grid hugs its content, so the figure never drifts to the card's far edge. */
  .bars {
    margin-top: 6px;
    display: grid;
    grid-template-columns: minmax(9rem, max-content) 120px max-content;
    justify-content: start;
    align-items: center;
    gap: 2px 8px;
    font-size: 0.78rem;
    color: var(--ink-2);
  }
  .metric {
    grid-column: 1 / -1;
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--muted);
  }
  .brow {
    display: contents;
  }
  .bl {
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .track {
    display: flex;
    height: 8px;
    border-radius: 2px;
    background: var(--surface-2);
    overflow: hidden;
  }
  .track i {
    display: block;
    height: 100%;
  }
  .track i.spread {
    background: repeating-linear-gradient(135deg, var(--c) 0 2px, transparent 2px 4px);
  }
  .bv {
    font-weight: 650;
    color: var(--ink);
    font-variant-numeric: tabular-nums;
    text-align: right;
    white-space: nowrap;
  }
  /* On a phone the label takes what the bar and figure leave, and wraps rather than cutting a spec's name short.
     After the rules above, so it overrides them. */
  @media (max-width: 640px) {
    .bars {
      grid-template-columns: minmax(0, 1fr) 84px auto;
    }
    .bl {
      white-space: normal;
    }
  }
  .slo-tag {
    align-self: flex-start;
    margin-top: 6px;
  }
</style>
