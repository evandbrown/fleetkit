<script lang="ts">
  // Every trial, as a grid: a row per run (replicas together under their spec, which is named once above them with
  // its colour dot), a column per density tested, and in each cell a mark per trial there, passed or failed. The
  // shaded span runs from the last density that passed to the first that failed (D60's interval); a dashed cell is a
  // listed density the run didn't reach. The columns are the latency chart's bands (bands.ts), so the two read
  // together. Each mark opens its trial; each replica label ("replica 2"; "r2" on a phone, where the key says what r
  // is), its run.
  import * as f from '../lib/format';
  import { specColor } from '../lib/colors';
  import { bandIndex, bandLayout, makeBands, tickShown, type Band } from '../lib/bands';
  import type { ChartRow } from '../lib/shape';
  import MarkShape from './MarkShape.svelte';

  let {
    rows,
    caption,
    bands: given = null,
    perVcpu: forced = null,
  }: { rows: ChartRow[]; caption: string; bands?: Band[] | null; perVcpu?: boolean | null } = $props();

  let width = $state(0);
  /** The trial pointed at, read out above the grid. */
  let hover: string | null = $state(null);
  const phone = $derived(width > 0 && width < 560);
  const touch = typeof matchMedia !== 'undefined' && matchMedia('(pointer: coarse)').matches;

  const groups = $derived(new Set(rows.map((r) => r.group)).size);
  const multiCampaign = $derived(new Set(rows.map((r) => r.campaign)).size > 1);
  const replicas = $derived(rows.length > groups);
  const perVcpu = $derived(forced ?? new Set(rows.map((r) => r.hostVcpus)).size > 1);
  const xOf = (r: ChartRow, density: number) => (perVcpu ? density / r.hostVcpus : density);
  const bands = $derived(
    given ??
      makeBands(
        rows.flatMap((r) => r.marks.map((m) => xOf(r, m.density))),
        rows.flatMap((r) => r.notTested.map((n) => xOf(r, n.density))),
        perVcpu,
      ),
  );

  const ROW = $derived(phone ? 24 : 26);
  const HEAD = 22;
  const TOP = 2;
  const AXIS_H = 38;
  const SIZE = $derived(phone ? 9 : 10);

  const specIndex = $derived([...new Set(rows.map((r) => r.group))]);
  const L = $derived(bandLayout(bands.length, width, phone));
  const shown = $derived(tickShown(bands, L.bw));
  /** The run's untested gap between its last pass and first failure: the shaded span, and its label ("9–11 not tested"). */
  function gapOf(r: ChartRow) {
    if (!r.band) return null;
    const a = bandIndex(bands, perVcpu ? r.band.from : r.band.from * r.hostVcpus);
    const b = bandIndex(bands, perVcpu ? r.band.to : r.band.to * r.hostVcpus);
    if (a < 0 || b < 0) return null;
    const x = L.center(a);
    const w = L.center(b) - x;
    const text = r.band.gap ? `${f.range(r.band.gap)} not tested` : null;
    // The label sits in the span when it fits, else on its own line under the row.
    return { x, w, text, inside: !!text && text.length * 6.2 + 40 < w };
  }
  const layout = $derived.by(() => {
    let y = TOP;
    let campaign: string | null = null;
    return rows.map((r) => {
      let camp: number | null = null;
      if (multiCampaign && r.campaign !== campaign) {
        camp = y;
        y += HEAD;
      }
      campaign = r.campaign;
      const head = r.first ? y : null;
      if (head !== null) y += HEAD;
      const top = y;
      const gap = gapOf(r);
      y += ROW + (gap?.text && !gap.inside ? 14 : 0);
      return { row: r, camp, head, top, mid: top + ROW / 2, gap };
    });
  });
  const plotBottom = $derived.by(() => {
    const last = layout.at(-1);
    return last ? last.top + ROW + (last.gap?.text && !last.gap.inside ? 14 : 0) : TOP;
  });
  const height = $derived(plotBottom + AXIS_H);

  /** "Cloud Hypervisor · replica 2 · trial 1 at density 10: failed, home p50 at 101% of its limit". */
  const readout = (r: ChartRow, title: string) =>
    [multiCampaign ? r.campaignTitle : null, groups > 1 ? r.groupLabel : null, replicas ? `replica ${r.replica}` : null, title]
      .filter(Boolean)
      .join(' · ');
  const fit = (s: string, room: number) => (s.length * 7.2 <= room ? s : `${s.slice(0, Math.max(4, Math.floor(room / 7.2) - 1))}…`);
  /** A row's label: its replica, even with one, so every row names what it is. */
  const rowLabel = (r: ChartRow) => (phone ? `r${r.replica}` : `replica ${r.replica}`);
</script>

<figure class="chart" bind:clientWidth={width}>
  <p class="hint" class:reading={hover !== null}>
    {#if hover}
      <span aria-hidden="true">{hover}</span>
    {:else}
      {touch ? 'Tap' : 'Click'} a mark to open its trial
    {/if}
  </p>
  {#if width > 0}
    <svg {width} {height} role="group" aria-label={caption}>
      {#each bands as b, i (b.label)}
        {#if b.untested}
          <rect class="untested" x={L.start(i) + 2} y={TOP} width={L.bw - 4} height={plotBottom - TOP} rx="4" />
        {:else if i > 0}
          <line class="sep" x1={L.start(i)} x2={L.start(i)} y1={TOP} y2={plotBottom} />
        {/if}
        {#if shown(i)}<text class="tick" class:off={b.untested} x={L.center(i)} y={plotBottom + 15} text-anchor="middle">{b.label}</text>{/if}
      {/each}
      <text class="axis" x={L.left} y={height - 4}>{perVcpu ? 'Density per host vCPU' : 'Density (microVMs)'}</text>

      {#each layout as R (R.row.key)}
        {@const row = R.row}
        {#if R.camp !== null}
          <text class="campaign" x="0" y={R.camp + 15}>{fit(row.campaignTitle, width)}</text>
        {/if}
        {#if R.head !== null}
          <circle cx="5" cy={R.head + 11} r="4.5" fill={specColor(specIndex.indexOf(row.group))} />
          <text class="group" x="15" y={R.head + 15}>{fit(row.groupLabel, width - 15)}</text>
        {/if}
        <line class="track" x1={L.left} x2={L.right} y1={R.top + ROW + (R.gap?.text && !R.gap.inside ? 14 : 0)} y2={R.top + ROW + (R.gap?.text && !R.gap.inside ? 14 : 0)} />

        {#if R.gap}
          <rect class="gap" x={R.gap.x} y={R.top + 3} width={Math.max(2, R.gap.w)} height={ROW - 6} rx="3" />
          {#if R.gap.text}
            <text class="gap-label" x={R.gap.x + R.gap.w / 2} y={R.gap.inside ? R.mid + 4 : R.top + ROW + 9} text-anchor="middle">{R.gap.text}</text>
          {/if}
        {/if}

        {#if row.runHref}
          <a class="run" href={row.runHref} aria-label="Run {row.runId}">
            <text class="label" x="0" y={R.mid + 4}>{rowLabel(row)}</text>
          </a>
        {:else}
          <text class="label off" x="0" y={R.mid + 4}>{rowLabel(row)}</text>
        {/if}

        {#each row.notTested as n (n.density)}
          {@const i = bandIndex(bands, xOf(row, n.density))}
          {#if i >= 0 && !bands[i].untested}
            <rect class="cell-off" x={L.center(i) - Math.min(18, L.bw * 0.3)} y={R.top + 6} width={Math.min(36, L.bw * 0.6)} height={ROW - 12} rx="3" />
          {/if}
        {/each}
        {#each row.marks as m (`${m.density}-${m.k}`)}
          {@const i = bandIndex(bands, xOf(row, m.density))}
          {@const n = row.marks.filter((o) => o.density === m.density).length}
          {@const step = Math.min(SIZE + 3, (L.bw * 0.8) / Math.max(1, n))}
          {@const cx = L.center(i) + (m.k - (n - 1) / 2) * step}
          <a
            class="mark"
            href={m.href}
            aria-label={m.title}
            onpointerenter={() => (hover = readout(row, m.title))}
            onpointerleave={() => (hover = null)}
            onfocus={() => (hover = readout(row, m.title))}
            onblur={() => (hover = null)}
          >
            <rect class="hit" x={cx - step / 2} y={R.top} width={step} height={ROW} />
            <circle class="halo" {cx} cy={R.mid} r={SIZE / 2 + 4} />
            <g class="shape"><MarkShape kind={m.passed ? 'pass' : 'fail'} {cx} cy={R.mid} size={m.passed ? SIZE : SIZE - 1} /></g>
          </a>
        {/each}
        {#if row.note}
          <text class="note" x={L.right - 4} y={R.mid + 4} text-anchor="end">{row.note}</text>
        {/if}
      {/each}
    </svg>
  {/if}
  <figcaption>
    <span class="key"><svg width="14" height="14" aria-hidden="true"><MarkShape kind="pass" cx={7} cy={7} size={11} /></svg>passed</span>
    <span class="key"><svg width="14" height="14" aria-hidden="true"><MarkShape kind="fail" cx={7} cy={7} size={10} /></svg>failed</span>
    {#if rows.some((r) => r.band)}<span class="key"><i class="sw gap-sw"></i>last pass to first failure</span>{/if}
    {#if bands.some((b) => b.untested) || rows.some((r) => r.notTested.length)}<span class="key"><i class="sw off-sw"></i>not tested</span>{/if}
    {#if phone}<span class="key">r = replica</span>{/if}
  </figcaption>
</figure>

<style>
  .chart {
    margin: 8px 0 0;
    min-width: 0;
  }
  svg {
    display: block;
    overflow: visible;
  }
  .sep {
    stroke: var(--grid);
    stroke-dasharray: 2 3;
  }
  .track {
    stroke: var(--grid);
  }
  .untested,
  .cell-off {
    fill: var(--surface);
    stroke: var(--rule);
    stroke-dasharray: 3 3;
  }
  .gap {
    fill: var(--surface-2);
  }
  .gap-label {
    fill: var(--ink-2);
    font-size: 11px;
  }
  .tick {
    fill: var(--ink-2);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .tick.off {
    fill: var(--muted);
  }
  .axis {
    fill: var(--muted);
    font-size: 12px;
  }
  .campaign {
    fill: var(--muted);
    font-size: 12px;
    font-weight: 600;
  }
  .group {
    fill: var(--ink);
    font-size: 13px;
    font-weight: 650;
  }
  /* Each replica label is a link to its run, and looks like one. */
  .label {
    fill: var(--accent-ink);
    font-size: 12px;
    text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--accent) 45%, transparent);
  }
  .run:hover .label,
  .run:focus-visible .label {
    fill: var(--accent);
  }
  .label.off {
    fill: var(--muted);
    text-decoration: none;
  }
  .note {
    fill: var(--ink-2);
    font-size: 11.5px;
    paint-order: stroke;
    stroke: var(--bg);
    stroke-width: 4px;
  }
  .mark {
    cursor: pointer;
  }
  .hit {
    fill: transparent;
  }
  .halo {
    fill: var(--highlight);
    stroke: var(--accent);
    stroke-width: 1.5;
    opacity: 0;
    transition: opacity 0.12s;
  }
  .shape {
    transform-box: fill-box;
    transform-origin: center;
    transition: transform 0.12s;
  }
  .mark:hover .halo,
  .mark:focus-visible .halo {
    opacity: 1;
  }
  .mark:hover .shape,
  .mark:focus-visible .shape {
    transform: scale(1.25);
  }
  a:focus-visible {
    outline: none;
  }
  a.run:focus-visible .label {
    outline: 2px solid var(--accent);
  }
  /* One line, always: a readout that wrapped would move the marks under the pointer. */
  .hint {
    height: 1.5em;
    display: flex;
    align-items: center;
    white-space: nowrap;
    overflow: hidden;
    margin: 0 0 4px;
    font-size: 0.85rem;
    color: var(--muted);
    max-width: none;
  }
  .hint.reading span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .hint.reading {
    color: var(--ink-2);
    font-variant-numeric: tabular-nums;
  }
  figcaption {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    align-items: center;
    font-size: 0.82rem;
    color: var(--ink-2);
    margin-top: 4px;
  }
  .key {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    white-space: nowrap;
  }
  .sw {
    display: inline-block;
    width: 14px;
    height: 10px;
    border-radius: 2px;
  }
  .gap-sw {
    background: var(--surface-2);
  }
  .off-sw {
    background: var(--surface);
    border: 1px dashed var(--axis);
  }
</style>
