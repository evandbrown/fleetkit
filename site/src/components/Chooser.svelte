<script lang="ts" module>
  /** Set when the reader picks a campaign here, so the new page puts focus back on the chooser. */
  let refocus = false;
</script>

<script lang="ts">
  // Results: the campaigns as a labelled set of tabs, newest first, each with its name, the question it answers (one
  // line) and its answer: one spec's density, or a small bar per spec of what the campaign compares, in the specs'
  // colours on the campaign page. A campaign judged by other than the standard SLOs says how (D74). The arrow keys
  // move between campaigns and Enter opens one. On a phone the tabs scroll sideways.
  import { onMount } from 'svelte';
  import { specColor } from '../lib/colors';
  import { loadCampaign } from '../lib/data';
  import { href } from '../lib/router';
  import { campaignFigure, sloTag, specLabels } from '../lib/shape';
  import type { Index } from '../lib/types';

  let { index, selected, panel }: { index: Index; selected: string; panel: string } = $props();
  const specs = $derived(index.campaigns.reduce((n, c) => n + c.specs.length, 0));
  /**
   * From each campaign's document, once it loads: the specs' names as its page shows them (the index's labels until
   * then), and a tag if its SLOs aren't the standard ones.
   */
  let names = $state<Record<string, string[]>>({});
  let slo = $state<Record<string, string | null>>({});
  const asked = new Set<string>();
  $effect(() => {
    for (const c of index.campaigns) {
      if (asked.has(c.id)) continue;
      asked.add(c.id);
      loadCampaign(c.id)
        .then((d) => {
          const labels = specLabels(d.specs);
          names = { ...names, [c.id]: c.specs.map((s) => labels[d.specs.findIndex((x) => x.name === s.name)] ?? s.label) };
          slo = { ...slo, [c.id]: sloTag(d.specs.map((s) => s.spec.criteria)) };
        })
        .catch(() => {});
    }
  });
  let list: HTMLElement | undefined = $state();

  const tabs = () => [...(list?.querySelectorAll<HTMLAnchorElement>('[role="tab"]') ?? [])];

  function key(e: KeyboardEvent) {
    const all = tabs();
    const i = all.indexOf(document.activeElement as HTMLAnchorElement);
    if (i < 0) return;
    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % all.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i - 1 + all.length) % all.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = all.length - 1;
    else if (e.key === ' ') {
      e.preventDefault();
      all[i].click();
      return;
    }
    if (next < 0) return;
    e.preventDefault();
    all[next].focus();
    reveal(all[next]);
  }

  /** Scrolls the tabs sideways (on a phone) so a tab is in view, without moving the page. */
  function reveal(el: HTMLElement) {
    if (!list || list.scrollWidth <= list.clientWidth) return;
    list.scrollLeft = el.offsetLeft - list.offsetLeft - (list.clientWidth - el.offsetWidth) / 2;
  }

  onMount(() => {
    const sel = tabs().find((t) => t.getAttribute('aria-selected') === 'true');
    if (sel) reveal(sel);
    if (refocus && sel) sel.focus({ preventScroll: true });
    refocus = false;
  });
</script>

<section class="chooser" aria-labelledby="campaigns-label">
  <div class="bar">
    <p class="label" id="campaigns-label"><strong>Campaigns</strong><span class="count">{index.campaigns.length}</span></p>
    {#if specs > 1}<a class="compare" href={href({ name: 'compare', specs: null })}>Compare specs →</a>{/if}
  </div>
  <!-- svelte-ignore a11y_interactive_supports_focus -->
  <div class="tabs" role="tablist" aria-labelledby="campaigns-label" tabindex="-1" bind:this={list} onkeydown={key}>
    {#each index.campaigns as c (c.id)}
      {@const fig = campaignFigure(c, names[c.id])}
      {@const on = c.id === selected}
      <a
        class="tab"
        role="tab"
        id="campaign-tab-{c.id}"
        href={href({ name: 'results', campaign: c.id })}
        aria-selected={on}
        aria-controls={on ? panel : undefined}
        tabindex={on ? 0 : -1}
        onclick={() => (refocus = true)}
      >
        <span class="name"><span class="radio" aria-hidden="true"></span><span class="t">{c.title}</span></span>
        <span class="q">{c.question}</span>
        {#if fig.single}
          {@const h = fig.single}
          <span class="fig">
            {#if h.density !== null}<strong>{h.density}</strong>{/if}
            {h.label}{#if h.perVcpu !== null}{' · '}<strong class="per">{h.perVcpu}</strong> per vCPU{/if}
          </span>
        {:else}
          <span class="bars">
            <span class="metric">{fig.metric}</span>
            {#each fig.bars as b, i (b.key)}
              <span class="brow">
                <span class="bl">{b.label}</span>
                <span class="track" aria-hidden="true"
                  ><i style:width="{fig.max ? (b.lo / fig.max) * 100 : 0}%" style:background={specColor(i)}></i
                  >{#if b.hi > b.lo}<i class="spread" style:width="{((b.hi - b.lo) / fig.max) * 100}%" style:--c={specColor(i)}></i>{/if}</span
                >
                <span class="bv">{b.text}</span>
              </span>
            {/each}
          </span>
        {/if}
        {#if slo[c.id]}<span class="slo-tag">{slo[c.id]}</span>{/if}
      </a>
    {/each}
  </div>
</section>

<style>
  .chooser {
    margin-top: 20px;
  }
  .bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 6px 16px;
    margin-bottom: 8px;
  }
  .label {
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 8px;
    max-width: none;
  }
  .label strong {
    font-size: 0.95rem;
    font-weight: 700;
  }
  .count {
    font-size: 0.78rem;
    font-weight: 650;
    color: var(--ink-2);
    background: var(--surface-2);
    border-radius: 999px;
    padding: 0 8px;
    line-height: 1.5;
    font-variant-numeric: tabular-nums;
  }
  .compare {
    font-size: 0.88rem;
    font-weight: 600;
    white-space: nowrap;
  }
  .tabs {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr));
    gap: 10px;
    padding: 2px 2px 4px;
    outline: none;
  }
  @media (max-width: 640px) {
    .tabs {
      display: flex;
      overflow-x: auto;
      scroll-snap-type: x mandatory;
      scrollbar-width: none;
      margin: 0 calc(-1 * var(--gutter));
      padding: 2px var(--gutter) 6px;
      scroll-padding: 0 var(--gutter);
    }
    .tab {
      flex: 0 0 min(80%, 18rem);
      scroll-snap-align: start;
    }
  }
  .tab {
    position: relative;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 9px 12px 10px;
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--surface);
    color: var(--ink);
    text-decoration: none;
    cursor: pointer;
    transition:
      border-color 0.12s,
      background-color 0.12s,
      box-shadow 0.12s;
  }
  .tab:hover {
    background: var(--bg);
    border-color: var(--accent);
    box-shadow: var(--shadow);
    text-decoration: none;
  }
  .tab:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    text-decoration: none;
  }
  .tab[aria-selected='true'] {
    background: var(--highlight);
    border-color: var(--accent);
    box-shadow: inset 0 0 0 1px var(--accent);
  }
  .name {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 650;
    font-size: 0.98rem;
  }
  .tab[aria-selected='true'] .name {
    color: var(--accent-ink);
  }
  .radio {
    flex: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 1.5px solid var(--muted);
    background: var(--bg);
  }
  .tab:hover .radio {
    border-color: var(--accent);
  }
  .tab[aria-selected='true'] .radio {
    border: 4px solid var(--accent);
  }
  .t {
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  /* The question in full where it fits, two lines at most. */
  .q {
    font-size: 0.82rem;
    line-height: 1.4;
    color: var(--ink-2);
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
    min-height: 2.8em;
  }
  .bars {
    margin-top: auto;
    padding-top: 6px;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 44px auto;
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
  .tab[aria-selected='true'] .track {
    background: var(--bg);
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
  .fig {
    margin-top: auto;
    padding-top: 6px;
    font-size: 0.82rem;
    color: var(--ink-2);
    white-space: nowrap;
  }
  strong {
    font-size: 1.25rem;
    font-weight: 650;
    color: var(--ink);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }
  strong.per {
    font-size: 1rem;
  }
  .slo-tag {
    align-self: flex-start;
    margin-top: 6px;
  }
</style>
