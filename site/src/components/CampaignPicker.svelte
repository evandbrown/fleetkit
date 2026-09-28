<script lang="ts" module>
  /** Set when the reader picks a campaign here, so the new page puts focus back on the picker. */
  let refocus = false;
</script>

<script lang="ts">
  // Results: the campaign to show, chosen from a select-style dropdown (Dropdown.svelte), newest first. The closed
  // control names the selected campaign: its title, its question on one line, and its answer as one figure: the
  // lead spec's steady-state cost ("≈ $0.043 per 1,000 tasks", the ≈ small: base.css's .approx) over that spec's name
  // and browsers per host. Each
  // option is a card of four lines whatever its spec count: title, question, one strip with a dot per spec at its
  // browsers per vCPU (a shared scale, the best spec named under its dot), and the lead spec's cost at the right
  // with "best: <spec>" or "no clear winner" under it (cost.ts, D102). A campaign judged by other SLOs than most of
  // its peers carries one muted word, "SLOs", whose tooltip lists them (D74); when every campaign was judged alike
  // the word would say nothing, so none carries it (About states the SLOs). Choosing opens the campaign by its address.
  import { onMount } from 'svelte';
  import { specColor } from '../lib/colors';
  import { campaignCost, costNote, costText, specCost, type SpecCost } from '../lib/cost';
  import { loadCampaign } from '../lib/data';
  import * as f from '../lib/format';
  import { browsers } from '../lib/glossary';
  import { href } from '../lib/router';
  import { linear, niceDomain } from '../lib/scale';
  import { campaignHeadline, sloTag, specLabels } from '../lib/shape';
  import type { CampaignEntry, Index } from '../lib/types';
  import Dropdown from './Dropdown.svelte';
  import Term from './Term.svelte';

  let { index, selected, panel }: { index: Index; selected: string; panel: string } = $props();
  const campaigns = $derived([...index.campaigns].sort((a, b) => (a.started < b.started ? 1 : a.started > b.started ? -1 : 0)));
  const specs = $derived(index.campaigns.reduce((n, c) => n + c.specs.length, 0));

  /**
   * From each campaign's document, once it loads: the specs' short names (the index's labels until then), and a tag
   * if its SLOs aren't the standard ones.
   */
  let names = $state<Record<string, string[]>>({});
  let slo = $state<Record<string, string | null>>({});
  /** The tag most campaigns carry, once every document has loaded; a card shows the word only where it differs. */
  const usual = $derived.by(() => {
    const tags = index.campaigns.map((c) => slo[c.id]);
    if (tags.some((t) => t === undefined)) return undefined;
    const tally = new Map<string | null, number>();
    for (const t of tags) tally.set(t, (tally.get(t) ?? 0) + 1);
    return [...tally.entries()].sort((a, b) => b[1] - a[1])[0][0];
  });
  const sloWord = (id: string): string | null => {
    const t = slo[id];
    if (t === undefined || usual === undefined || t === usual) return null;
    return t ?? 'Judged by the design\u2019s standard SLOs';
  };
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
          slo = { ...slo, [c.id]: sloTag(d.specs.map((s) => s.spec.criteria)) };
        })
        .catch(() => {});
    }
  });

  /** A spec's short name, by its place in the campaign's list. */
  const name = (c: CampaignEntry, spec: string) => {
    const i = c.specs.findIndex((s) => s.name === spec);
    return names[c.id]?.[i] ?? c.specs[i]?.label ?? spec;
  };

  /**
   * The figure a campaign is chosen by. `cost`: the lead spec's steady-state cost, and whether it is a clear winner
   * among several. `count`: no spec has a fleet cost (no host was ever full), so the best spec's browsers per host
   * stand in. `none`: nothing has run or passed, with the label to say so.
   */
  type Figure =
    | { kind: 'cost'; spec: SpecCost; several: boolean; clear: boolean }
    | { kind: 'count'; spec: SpecCost }
    | { kind: 'none'; label: string };
  function figure(c: CampaignEntry): Figure {
    const cc = campaignCost(c);
    if (cc.lead) return { kind: 'cost', spec: cc.lead, several: c.specs.length > 1, clear: cc.clear };
    const h = campaignHeadline(c, c.featured_spec ?? null);
    const s = h.spec ? specCost(c, h.spec) : null;
    if (s?.count) return { kind: 'count', spec: s };
    return { kind: 'none', label: h.label === 'not run yet' ? 'Not run yet' : 'None passed' };
  }
  const figures = $derived(new Map(campaigns.map((c) => [c.id, figure(c)])));

  /** The strip's scale, shared by every card: 0 to a round number at or above every spec's browsers per vCPU. */
  const top = $derived(niceDomain(index.campaigns.flatMap((c) => c.specs.map((s) => specCost(c, s.name).perVcpu ?? 0)), { count: 4 })[1]);
  const x = $derived(linear([0, top], [0, 100]));
  const ticks = $derived(x.ticks(2).filter((t) => t > 0 && t < top));

  interface Dot {
    key: string;
    label: string;
    color: string;
    /** Left edge, in % of the strip. */
    pct: number;
    /** Dots at the same value are stacked a little, so both show. */
    dy: number;
    best: boolean;
    title: string;
  }
  /** A dot per spec that has run and passed, at its browsers per vCPU; the best spec's is larger and named. */
  function dots(c: CampaignEntry): Dot[] {
    const fig = figures.get(c.id);
    const best = fig?.kind === 'cost' && (!fig.several || fig.clear) ? fig.spec.spec : fig?.kind === 'count' ? fig.spec.spec : null;
    const seen = new Map<number, number>();
    return c.specs.flatMap((s, i) => {
      const sc = specCost(c, s.name);
      if (sc.perVcpu === null) return [];
      const n = seen.get(sc.perVcpu) ?? 0;
      seen.set(sc.perVcpu, n + 1);
      const label = name(c, s.name);
      return [
        {
          key: s.name,
          label,
          color: specColor(i),
          pct: x(sc.perVcpu),
          dy: n === 0 ? 0 : n % 2 ? -5 * Math.ceil(n / 2) : 5 * Math.ceil(n / 2),
          best: s.name === best,
          title: `${label}: ${f.ratio(sc.perVcpu)} browsers per vCPU, ${browsers(sc.count ?? '')} on ${sc.hostVcpus} vCPUs`,
        },
      ];
    });
  }

  /** A name under a dot stays inside the strip: centred, or flush with the edge it is near. */
  const align = (pct: number) => (pct < 18 ? 'start' : pct > 82 ? 'end' : 'center');

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
    <span class="label" id="campaign-label">Choose a campaign</span>
    {#if specs > 1}<a class="compare" href={href({ name: 'compare', specs: null })}>Compare specs →</a>{/if}
  </div>
  <Dropdown items={campaigns} {selected} label="Campaign" id="campaign-picker" controls={panel} onselect={pick} bind:this={dropdown}>
    {#snippet button(c: CampaignEntry)}
      {@const fig = figures.get(c.id) ?? figure(c)}
      <span class="sel">
        <span class="text">
          <span class="title">{c.title}</span>
          <span class="q">{c.question}</span>
        </span>
        <span class="fig">
          {#if fig.kind === 'cost'}
            <span class="line" title={costNote(fig.spec) ?? undefined}><strong><span class="approx">≈</span>{' '}{costText(fig.spec.cost!)}</strong> per 1,000 tasks</span>
            <span class="line sub">{name(c, fig.spec.spec)} · {browsers(fig.spec.count ?? '')} per host</span>
          {:else if fig.kind === 'count'}
            <span class="line"><strong>{fig.spec.count}</strong> browsers per host</span>
            <span class="line sub">{name(c, fig.spec.spec)} · no fleet cost</span>
          {:else}
            <span class="line sub">{fig.label}</span>
          {/if}
        </span>
      </span>
    {/snippet}
    {#snippet option(c: CampaignEntry, on: boolean)}
      {@const fig = figures.get(c.id) ?? figure(c)}
      {@const ds = dots(c)}
      <div class="card" class:on>
        <div class="title">
          <span class="t">{c.title}</span>
          {#if sloWord(c.id)}
            <!-- The word shows its tooltip; the tap or click that pins it must not also choose the campaign. -->
            <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
            <span class="slo" onclick={(e) => e.stopPropagation()}><Term text={sloWord(c.id)!}>SLOs</Term></span>
          {/if}
          {#if on}
            <svg class="check" aria-hidden="true" viewBox="0 0 16 16" width="16" height="16">
              <path d="M3 8.5l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          {/if}
        </div>
        <div class="q">{c.question}</div>
        {#if ds.length}
          <div class="strip" aria-hidden="true">
            <span class="end">0</span>
            <span class="scale">
              {#each ticks as t (t)}<i class="tick" style:left="{x(t)}%"></i>{/each}
              {#each ds as d (d.key)}
                <i class="dot" class:best={d.best} style:left="{d.pct}%" style:background={d.color} style:--dy="{d.dy}px" title={d.title}></i>
              {/each}
              {#each ds.filter((d) => d.best) as d (d.key)}
                <span class="name {align(d.pct)}" style:left="{d.pct}%">{d.label}</span>
              {/each}
            </span>
            <span class="end unit">{f.num(top, Number.isInteger(top) ? 0 : 1)} browsers / vCPU</span>
          </div>
        {/if}
        <div class="fig">
          {#if fig.kind === 'cost'}
            <span class="v" class:win={fig.several && fig.clear} title={costNote(fig.spec) ?? undefined}><span class="approx">≈</span>{' '}{costText(fig.spec.cost!)}</span>
            <span class="u">per 1,000 tasks</span>
            {#if fig.several}<span class="w">{fig.clear ? `best: ${name(c, fig.spec.spec)}` : 'no clear winner'}</span>{/if}
          {:else if fig.kind === 'count'}
            <span class="v">{fig.spec.count}</span>
            <span class="u">browsers per host</span>
            <span class="w">no fleet cost</span>
          {:else}
            <span class="w">{fig.label}</span>
          {/if}
        </div>
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
  /* The question on one line, cut at a word, never mid-word. */
  .q {
    font-size: 0.85rem;
    line-height: 1.4;
    color: var(--ink-2);
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 1;
    line-clamp: 1;
    overflow: hidden;
    overflow-wrap: normal;
  }
  .sel .fig {
    flex: none;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    text-align: right;
    font-size: 0.85rem;
    line-height: 1.35;
    color: var(--ink-2);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .sel .fig strong {
    font-size: 1.05rem;
    font-weight: 650;
    color: var(--ink);
  }
  .sel .fig .sub {
    font-size: 0.78rem;
  }
  @media (max-width: 640px) {
    .sel .fig {
      align-items: flex-start;
      text-align: left;
    }
  }

  /* An option: four lines whatever its spec count, the figure at the right beside them. */
  .card {
    display: grid;
    grid-template-columns: minmax(0, 1fr) max-content;
    grid-template-rows: auto auto auto;
    gap: 2px 20px;
    min-width: 0;
  }
  .card .title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
    line-height: 1.35;
    min-width: 0;
  }
  .card .title .t {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .card.on .title {
    color: var(--accent-ink);
  }
  .check {
    flex: none;
    color: var(--accent);
  }
  .slo {
    flex: none;
    font-size: 0.78rem;
    font-weight: 500;
    color: var(--muted);
  }
  .card .q {
    font-size: 0.82rem;
  }

  /* The strip: "0" at the left, the scale between, its top at the right; a dot per spec on the line, the best named. */
  .strip {
    margin-top: 6px;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    height: 32px;
    font-size: 0.68rem;
    line-height: 14px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .end {
    flex: none;
  }
  .scale {
    position: relative;
    flex: 1;
    min-width: 0;
    height: 14px;
  }
  /* the line the dots sit on */
  .scale::before {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    top: 50%;
    height: 1px;
    background: var(--axis);
  }
  .tick {
    position: absolute;
    top: 3px;
    height: 8px;
    width: 1px;
    background: var(--axis);
  }
  .dot {
    position: absolute;
    top: 50%;
    width: 9px;
    height: 9px;
    margin: -4.5px 0 0 -4.5px;
    border-radius: 50%;
    box-shadow: 0 0 0 1.5px var(--bg);
    transform: translateY(var(--dy, 0));
  }
  .dot.best {
    width: 12px;
    height: 12px;
    margin: -6px 0 0 -6px;
    z-index: 1;
  }
  .name {
    position: absolute;
    top: 16px;
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1.2;
    color: var(--ink);
    white-space: nowrap;
  }
  .name.center {
    transform: translateX(-50%);
  }
  .name.end {
    transform: translateX(-100%);
  }

  /* The figure: the number, its unit, and which spec it belongs to. */
  .card .fig {
    grid-column: 2;
    grid-row: 1 / span 3;
    align-self: center;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    text-align: right;
    line-height: 1.25;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .card .fig .v {
    font-size: 1.1rem;
    font-weight: 650;
    color: var(--ink);
  }
  /* A clear winner among several specs. */
  .card .fig .v.win {
    color: var(--accent-ink);
  }
  .card .fig .u {
    font-size: 0.78rem;
    color: var(--ink-2);
  }
  .card .fig .w {
    font-size: 0.78rem;
    color: var(--muted);
    max-width: 12rem;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  /* On a phone the figure goes under the strip, at the left, so the strip keeps its width. */
  @media (max-width: 640px) {
    .card {
      grid-template-columns: minmax(0, 1fr);
    }
    .card .fig {
      grid-column: 1;
      grid-row: auto;
      margin-top: 4px;
      flex-direction: row;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 0 6px;
      text-align: left;
    }
    .card .fig .u + .w::before {
      content: '· ';
    }
  }
</style>
