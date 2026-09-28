<script lang="ts">
  // Every trial of every run, across densities: one row per run, replicas together under their spec. Each trial is
  // a mark (passed, failed) placed at how close it came to failing, its closest SLO as a share of the limit, so the
  // chart shows the headroom and the climb past the dashed line. The shaded band runs from the last density that
  // passed to the first that failed (D60's interval). A dashed ring is a listed density not tested. On one host size
  // the axis is the density; across host sizes it is density per host vCPU (D52), with one axis for every row. Each
  // mark opens its trial.
  import * as f from '../lib/format';
  import { linear } from '../lib/scale';
  import { RATIO_CAP, type ChartRow } from '../lib/shape';
  import MarkShape from './MarkShape.svelte';

  let { rows, caption }: { rows: ChartRow[]; caption: string } = $props();

  let width = $state(0);
  const phone = $derived(width > 0 && width < 560);

  const groups = $derived(new Set(rows.map((r) => r.group)).size);
  const multiCampaign = $derived(new Set(rows.map((r) => r.campaign)).size > 1);
  const replicas = $derived(rows.length > groups);
  const headroom = $derived(rows.some((r) => r.marks.some((m) => m.ratio !== null)));
  const single = $derived(rows.length === 1);

  const LABEL = $derived(replicas ? (phone ? 28 : 74) : 0);
  const YAX = $derived(headroom ? (phone && !single ? 34 : 40) : 8);
  const LEFT = $derived(LABEL + YAX);
  const HEAD = 24;
  const TOP = $derived(headroom ? 22 : 4);
  const AXIS_H = 44;
  const ROW_H = $derived(headroom ? (single ? 200 : phone ? 76 : 84) : 40);
  const PAD_T = 10;
  const PAD_B = $derived(headroom ? 16 : 8);
  const SIZE = $derived(phone ? (single ? 12 : 9) : 14);
  /** How far apart the trials at one density sit: tight on a phone, where neighbouring densities are close. */
  const JITTER = $derived(phone ? (single ? 0.5 : 0.3) : 0.6);

  const vcpus = $derived([...new Set(rows.map((r) => r.hostVcpus))]);
  const oneHost = $derived(vcpus.length === 1 ? vcpus[0] : null);
  /** On one host size, x is the density itself; otherwise density per host vCPU. */
  const k = $derived(oneHost ?? 1);
  const maxX = $derived(Math.max(0.1, ...rows.flatMap((r) => [...r.marks.map((m) => m.x), ...r.notTested.map((m) => m.x)])) * k);
  const x = $derived(linear([0, maxX * 1.04], [LEFT + 10, Math.max(LEFT + 60, width - 14)]));

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
      const head = groups > 1 && r.first ? y : null;
      if (head !== null) y += HEAD;
      const top = y;
      y += ROW_H;
      const bottom = top + ROW_H - PAD_B;
      const ry = linear([0, RATIO_CAP], [bottom, top + PAD_T]);
      return { row: r, camp, head, top, bottom, mid: (top + bottom) / 2, ry };
    });
  });
  const plotBottom = $derived(layout.length ? layout[layout.length - 1].top + ROW_H : TOP);
  const height = $derived(plotBottom + AXIS_H);

  /** Ticks: every density tested on one host size; round steps of density per host vCPU otherwise. */
  const ticks = $derived.by(() => {
    if (oneHost) {
      const ds = [...new Set(rows.flatMap((r) => [...r.marks.map((m) => m.density), ...r.notTested.map((m) => m.density)]))].sort((a, b) => a - b);
      const out: number[] = [];
      for (const d of ds) if (!out.length || x(d) - x(out[out.length - 1]) >= 18) out.push(d);
      return out;
    }
    const top = x.domain[1];
    const step = [0.05, 0.1, 0.25, 0.5, 1, 2, 5].find((s) => top / s + 1 <= (phone ? 5 : 8)) ?? 10;
    return Array.from({ length: Math.floor(top / step + 1e-9) + 1 }, (_, i) => Math.round(i * step * 100) / 100);
  });
  const tickText = (v: number) => (oneHost ? f.num(v) : v === 0 ? '0' : f.num(v, 2).replace(/0$/, '').replace(/\.0$/, ''));
  const yTicks = $derived(single ? [0, 0.5, 1, 1.5, 2] : [1]);

  /** Where a mark sits: trials at one density spread sideways a little, so none hides another. */
  function place(r: ChartRow, m: ChartRow['marks'][number], L: (typeof layout)[number]) {
    const n = r.marks.filter((o) => o.density === m.density).length;
    const cx = x(m.x * k) + (m.k - (n - 1) / 2) * (m.ratio !== null ? SIZE * JITTER : 0);
    const cy = m.ratio !== null ? L.ry(m.ratio) : L.mid + (m.k - (n - 1) / 2) * (SIZE + 1);
    return { cx, cy };
  }
  const fit = (s: string, room: number) => (s.length * 7.5 <= room ? s : `${s.slice(0, Math.max(4, Math.floor(room / 7.5) - 1))}…`);
</script>

<figure class="chart" bind:clientWidth={width}>
  {#if width > 0}
    <svg {width} {height} role="group" aria-label={caption}>
      {#if headroom}
        <text class="ytitle" x={LABEL} y="12">% of SLO limit</text>
      {/if}
      {#each ticks as t (t)}
        <line class="grid" x1={x(t)} x2={x(t)} y1={TOP} y2={plotBottom} />
        <text class="tick" x={x(t)} y={plotBottom + 16} text-anchor="middle">{tickText(t)}</text>
      {/each}
      <text class="axis" x={x(0)} y={height - 6}>{oneHost ? 'Density (microVMs)' : 'Density per host vCPU'}</text>

      {#each layout as L (L.row.key)}
        {@const row = L.row}
        {#if L.camp !== null}
          <text class="campaign" x="0" y={L.camp + 16}>{fit(row.campaignTitle, width)}</text>
        {/if}
        {#if L.head !== null}
          <text class="group" x="0" y={L.head + 16}>{fit(row.groupLabel, width)}</text>
        {/if}

        {#if row.band}
          <rect class="band" x={x(row.band.from * k)} y={L.top + 2} width={Math.max(2, x(row.band.to * k) - x(row.band.from * k))} height={L.bottom - L.top - 2} rx="3" />
          {#if single && row.band.gap}
            <text class="band-label" x={(x(row.band.from * k) + x(row.band.to * k)) / 2} y={L.ry(RATIO_CAP) + 18} text-anchor="middle">
              {f.range(row.band.gap)} not tested
            </text>
          {/if}
        {/if}

        {#if headroom}
          {#each yTicks as v (v)}
            <line class={v === 1 ? 'slo' : 'hgrid'} x1={LEFT} x2={x.range[1]} y1={L.ry(v)} y2={L.ry(v)} />
            {#if single || L === layout[0]}
              <text class="ytick" x={LEFT - 6} y={L.ry(v) + 4} text-anchor="end">{f.num(v * 100)}%</text>
            {/if}
          {/each}
          {#if L === layout[0]}
            <text class="slo-label" x={x.range[1]} y={L.ry(1) - 5} text-anchor="end">SLO limit</text>
          {/if}
        {/if}
        <line class="track" x1={LEFT} x2={x.range[1]} y1={headroom ? L.bottom : L.mid} y2={headroom ? L.bottom : L.mid} />

        {#if replicas}
          {#if row.runHref}
            <a href={row.runHref} aria-label="Run {row.runId}">
              <text class="label" x="0" y={L.mid + 4}>{phone ? `r${row.replica}` : `replica ${row.replica}`}</text>
            </a>
          {:else}
            <text class="label off" x="0" y={L.mid + 4}>{phone ? `r${row.replica}` : `replica ${row.replica}`}</text>
          {/if}
        {/if}

        {#each row.notTested as n (n.density)}
          <MarkShape kind="untested" cx={x(n.x * k)} cy={headroom ? L.bottom : L.mid} size={10} />
        {/each}
        {#each row.marks as m (`${m.density}-${m.k}`)}
          {@const p = place(row, m, L)}
          <a class="mark" href={m.href} aria-label={m.title}>
            <rect class="hit" x={p.cx - 9} y={p.cy - 9} width="18" height="18" />
            <MarkShape kind={m.passed ? 'pass' : 'fail'} cx={p.cx} cy={p.cy} size={m.passed ? SIZE : SIZE - 1} />
          </a>
        {/each}
        {#if single && row.band && headroom}
          {@const best = row.marks.filter((m) => m.x === row.band!.from && m.ratio !== null)}
          {#if best.length}
            <text class="callout" x={x(row.band.from * k)} y={Math.max(...best.map((m) => place(row, m, L).cy)) + 24} text-anchor="middle">
              {best[0].density} passed
            </text>
          {/if}
        {/if}
        {#if row.note}
          <text class="note" x={x.range[1]} y={headroom ? L.ry(1) - 6 : L.mid - 10} text-anchor="end">{row.note}</text>
        {/if}
      {/each}
    </svg>
  {/if}
  <figcaption>
    <span class="key"><svg width="14" height="14" aria-hidden="true"><MarkShape kind="pass" cx={7} cy={7} size={12} /></svg>passed</span>
    <span class="key"><svg width="14" height="14" aria-hidden="true"><MarkShape kind="fail" cx={7} cy={7} size={11} /></svg>failed</span>
    {#if rows.some((r) => r.notTested.length)}
      <span class="key"><svg width="14" height="14" aria-hidden="true"><MarkShape kind="untested" cx={7} cy={7} size={10} /></svg>not tested</span>
    {/if}
    {#if rows.some((r) => r.band)}<span class="key"><i class="sw band-sw"></i>last pass to first failure</span>{/if}
  </figcaption>
</figure>

<style>
  .chart {
    margin: 16px 0 0;
    min-width: 0;
  }
  svg {
    display: block;
    overflow: visible;
  }
  .grid {
    stroke: var(--grid);
  }
  .hgrid {
    stroke: var(--grid);
  }
  .track {
    stroke: var(--axis);
  }
  .slo {
    stroke: var(--ink-2);
    stroke-dasharray: 5 4;
  }
  .slo-label,
  .ytick,
  .ytitle {
    fill: var(--ink-2);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .ytitle {
    font-size: 12px;
    fill: var(--muted);
  }
  .band {
    fill: var(--surface-2);
    opacity: 0.8;
  }
  .band-label {
    fill: var(--ink-2);
    font-size: 11px;
  }
  .tick {
    fill: var(--ink-2);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
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
  .label {
    fill: var(--ink-2);
    font-size: 12px;
  }
  a:hover .label {
    fill: var(--accent-ink);
  }
  .label.off {
    fill: var(--muted);
  }
  .note {
    fill: var(--ink-2);
    font-size: 11.5px;
  }
  .callout {
    fill: var(--good);
    font-size: 12px;
    font-weight: 650;
  }
  .hit {
    fill: transparent;
  }
  .mark:hover :global(.m-pass),
  .mark:focus-visible :global(.m-pass) {
    stroke: var(--ink);
  }
  .mark:hover :global(.m-fail),
  .mark:focus-visible :global(.m-fail) {
    stroke: var(--ink);
  }
  a:focus-visible {
    outline: 2px solid var(--accent);
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
  .band-sw {
    background: var(--surface-2);
  }
</style>
