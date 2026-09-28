<script lang="ts">
  // What we tested, as one table (D93): a column per spec, headed by its colour dot and short name with its why
  // under it, and a row per input (table.ts, which leaves out the rows no spec sets). A row whose value is the same
  // for every spec is written once across the row, in ink-2; one that differs shows a cell per spec, in ink at weight
  // 600. The SLOs row lists each target on its own line (SloList, each a defined term); the Chromium flags row is
  // smaller and quieter, so a long flag doesn't dominate. The only lines are the rules between rows and the border
  // around the table: no chips, no fills. One spec: two columns, label and value, no header. On Compare (`campaigns`) a first header row names each campaign over
  // its group of columns. On a phone, with more than two specs, the table scrolls sideways inside its own box with
  // the row labels held at the left, and its right edge fades until it is scrolled to the end; with one or two it
  // wraps to fit.
  //
  // Props:
  //   refs       the specs, one column each, in order; on Compare a campaign's specs sit together (campaignRefs)
  //   replicas   runs per spec: the campaign definition's replicas, or on Compare each campaign's, by campaign id
  //   campaigns  on Compare: name each campaign over its columns
  //   support    on About: a last row for the support host, so the table is everything fixed about one run
  import { specColor } from '../lib/colors';
  import type { SpecRef } from '../lib/shape';
  import { chromiumFlags } from '../lib/spec';
  import { tableRows, type TableRow } from '../lib/table';
  import Flags from './Flags.svelte';
  import SloList from './SloList.svelte';

  let {
    refs,
    replicas,
    campaigns = false,
    support = false,
  }: { refs: SpecRef[]; replicas: number | Map<string, number>; campaigns?: boolean; support?: boolean } = $props();

  const key = (r: SpecRef) => `${r.campaign}/${r.spec.name}`;
  const counts = $derived(refs.map((r) => (typeof replicas === 'number' ? replicas : (replicas.get(r.campaign) ?? 0))));
  const rows = $derived(tableRows(refs, counts, support).filter((r) => r.shown));

  /** The scrolling box, and whether it is scrolled to its right end; until then a fade at that edge says there is more. */
  let wrap: HTMLElement | undefined = $state();
  let atEnd = $state(true);
  function measure() {
    if (wrap) atEnd = wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 1;
  }
  $effect(() => {
    measure();
    if (!wrap || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(wrap);
    if (wrap.firstElementChild) ro.observe(wrap.firstElementChild);
    return () => ro.disconnect();
  });
  const one = $derived(refs.length === 1);
  const whys = $derived(refs.some((r) => r.spec.why));
  /** Runs of neighbouring specs from the same campaign, for the campaign row. */
  const groups = $derived(
    refs.reduce<{ title: string; n: number }[]>((out, r) => {
      const last = out.at(-1);
      if (last && last.title === r.campaignTitle) last.n++;
      else out.push({ title: r.campaignTitle, n: 1 });
      return out;
    }, []),
  );
</script>

{#snippet cell(row: TableRow, i: number)}
  {@const v = row.values[i]}
  <span class="v" class:flags={row.key === 'chromium'}>
    {#if row.key === 'chromium'}
      <Flags flags={chromiumFlags(refs[i].spec.spec)} />
    {:else if row.key === 'slos'}
      <SloList criteria={refs[i].spec.spec.criteria} />
    {:else}
      <span class="main">{v.main}</span>{#if v.sub}<span class="sub">{v.sub}</span>{/if}
    {/if}
  </span>
{/snippet}

<div class="wrap" class:wide={refs.length > 2} class:more={!atEnd} bind:this={wrap} onscroll={measure}>
  <table class="tested" class:one style:--n={refs.length}>
    <colgroup>
      <col class="labels" />
      {#each refs as r (key(r))}<col />{/each}
    </colgroup>
    {#if !one}
      <thead>
        {#if campaigns}
          <tr class="campaigns">
            <td></td>
            {#each groups as g, i (i)}<th scope="colgroup" colspan={g.n}>{g.title}</th>{/each}
          </tr>
        {/if}
        <tr class="names">
          <td></td>
          {#each refs as r, i (key(r))}
            <th scope="col"><span class="name"><i class="dot" style:background={specColor(i)}></i><span>{r.groupLabel}</span></span></th>
          {/each}
        </tr>
        {#if whys}
          <tr class="whys">
            <td></td>
            {#each refs as r (key(r))}<td class="why">{r.spec.why ?? ''}</td>{/each}
          </tr>
        {/if}
      </thead>
    {/if}
    <tbody>
      {#each rows as row (row.key)}
        <tr>
          <th scope="row">{row.label}</th>
          {#if row.differs}
            {#each refs as r, i (key(r))}<td class="diff">{@render cell(row, i)}</td>{/each}
          {:else}
            <td class="same" colspan={refs.length}>{@render cell(row, 0)}</td>
          {/if}
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  /* A wide table scrolls inside its own box, never the page. The box is a container, so a value written once across
     the row can be sized to the box's width on a phone (below). */
  .wrap {
    margin: 12px 0 0;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    overflow-x: auto;
    container-type: inline-size;
  }
  table {
    width: 100%;
    /* Separate borders, so a sticky label column keeps its rule while the rest scrolls under it. */
    border-collapse: separate;
    border-spacing: 0;
    table-layout: fixed;
    font-size: 0.92rem;
    line-height: 1.45;
  }
  col.labels {
    width: 8rem;
  }
  th,
  td {
    padding: 10px 14px;
    border-bottom: 1px solid var(--rule);
    vertical-align: top;
    text-align: left;
    background: transparent;
  }
  tbody tr:last-child > th,
  tbody tr:last-child > td {
    border-bottom: 0;
  }
  /* This is a definition, not data rows: no hover fill. */
  tbody tr:hover > td {
    background: transparent;
  }
  tbody tr:hover > th {
    background: var(--bg);
  }

  /* The header: the campaign row, the names and their whys sit together, with one rule under the whole. */
  thead > tr > * {
    border-bottom: 0;
  }
  thead > tr:last-child > * {
    border-bottom: 1px solid var(--rule);
    padding-bottom: 10px;
  }
  .campaigns > th {
    padding: 10px 14px 0;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--ink-2);
    white-space: normal;
  }
  .names > th {
    padding: 8px 14px 2px;
    font-weight: 600;
    color: var(--ink);
    white-space: normal;
  }
  /* The dot stays on the name's first line and the name wraps beside it, never under it in a narrow column. */
  .name {
    display: flex;
    align-items: baseline;
    gap: 7px;
  }
  .dot {
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    position: relative;
    top: -1px;
  }
  .whys > td {
    padding: 0 14px 10px;
    font-size: 0.8rem;
    color: var(--ink-2);
    line-height: 1.4;
  }

  /* The rows: a label at the left; a value once across the row where it matches, per spec where it differs. */
  th[scope='row'] {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--ink-2);
    white-space: normal;
    background: var(--bg);
  }
  .v {
    display: block;
    min-width: 0;
  }
  /* The flags, in full but small and quiet: Flags sets its code at 0.82em, so this lands it at 0.8rem. */
  .v.flags {
    font-size: calc(0.8rem / 0.82);
    font-weight: 400;
    color: var(--ink-2);
  }
  td.same {
    color: var(--ink-2);
    font-weight: 400;
  }
  /* One spec: there is nothing to compare, so its values read in ink, not as "the same across specs". */
  .one td.same {
    color: var(--ink);
  }
  td.diff {
    color: var(--ink);
    font-weight: 600;
  }
  .main {
    display: block;
    overflow-wrap: anywhere;
  }
  .sub {
    display: block;
    margin-top: 1px;
    font-size: 0.8rem;
    font-weight: 400;
    color: var(--ink-2);
  }

  @media (max-width: 600px) {
    col.labels {
      width: 6.5rem;
    }
    th,
    td {
      padding: 8px 12px;
    }
    .campaigns > th {
      padding: 8px 12px 0;
    }
    .names > th {
      padding: 6px 12px 2px;
    }
    .whys > td {
      padding: 0 12px 8px;
    }
    /* More than two specs: the table is wider than the phone and scrolls in its box, the row labels held at the left. */
    .wide table {
      min-width: calc(6.5rem + var(--n) * 9.5rem);
    }
    .wide th[scope='row'],
    .wide thead td:first-child {
      position: sticky;
      left: 0;
      z-index: 1;
      background: var(--bg);
    }
    /* A value written once across the row stays in view beside its label while the spec columns scroll. */
    .wide td.same > .v {
      position: sticky;
      left: calc(6.5rem + 12px);
      width: calc(100cqw - 6.5rem - 24px);
    }
    /* Until the box is scrolled to its end, its right edge fades: there are more specs that way. */
    .wide.more {
      -webkit-mask-image: linear-gradient(to right, black calc(100% - 28px), transparent);
      mask-image: linear-gradient(to right, black calc(100% - 28px), transparent);
    }
  }
</style>
