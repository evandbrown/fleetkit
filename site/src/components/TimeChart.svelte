<script lang="ts">
  // One panel of a trial's time series: lines (or stacked layers) against ms from the trial's start, with the
  // task window shaded, the trial's marks as rules, and a rule's threshold as a reference line. A crosshair snaps
  // to the nearest sample and reads every series; the arrow keys move it.
  import * as f from '../lib/format';
  import { areaPath, linear, linePath, nearest, niceDomain } from '../lib/scale';
  import type { Range } from '../lib/types';

  interface Series {
    label: string;
    values: number[];
    color: string;
  }

  let {
    title,
    unit,
    t,
    series,
    many = false,
    stack = false,
    domain,
    window: taskWindow = null,
    threshold = null,
    ceiling = null,
    digits = 1,
    height = 170,
  }: {
    title: string;
    unit: string;
    /** One time axis per series, or one shared by all. */
    t: number[][];
    series: Series[];
    many?: boolean;
    stack?: boolean;
    domain: Range;
    window?: Range | null;
    threshold?: { value: number; op: '>=' | '<'; fired: boolean | null } | null;
    ceiling?: { value: number; label: string } | null;
    digits?: number;
    height?: number;
  } = $props();

  const M = { top: 10, right: 12, bottom: 26, left: 44 };
  let w = $state(0);
  let hover: number | null = $state(null);
  let tipW = $state(0);

  const tOf = (i: number) => t[Math.min(i, t.length - 1)];
  const base = $derived(tOf(0).map((v, i) => ({ v, i })).filter((p) => p.v >= domain[0] && p.v <= domain[1]));

  /** Cumulative sums for stacked layers, on the first series' clock. */
  const stacked = $derived.by(() => {
    if (!stack) return null;
    const n = tOf(0).length;
    const tops: number[][] = [];
    let run = new Array(n).fill(0);
    for (const s of series) {
      run = run.map((v, i) => v + (s.values[i] ?? 0));
      tops.push(run);
    }
    return tops;
  });

  const yMax = $derived.by(() => {
    const vals = stacked ? stacked[stacked.length - 1] : series.flatMap((s) => s.values);
    const extra = [threshold?.value ?? 0, ceiling?.value ?? 0];
    return niceDomain([...vals, ...extra], { count: 4 });
  });

  const x = $derived(linear(domain, [M.left, Math.max(M.left + 1, w - M.right)]));
  const y = $derived(linear(yMax, [height - M.bottom, M.top]));

  const fmt = (v: number) => (unit === '%' ? `${f.num(v, digits)}%` : `${f.num(v, digits)}${unit ? ` ${unit}` : ''}`);
  /** A threshold as written in the spec: 20%, 0.1. */
  const plain = (v: number) => `${f.num(v, v % 1 ? 2 : 0)}${unit === '%' ? '%' : unit ? ` ${unit}` : ''}`;
  const fmtT = (ms: number) => `${f.num(ms / 1000, ms % 1000 === 0 ? 0 : 1)} s`;

  function readAt(sample: number) {
    const at = tOf(0)[sample];
    return series.map((s, k) => {
      const tk = tOf(k);
      const i = tk === tOf(0) ? sample : nearest(tk, at);
      return { label: s.label, color: s.color, value: s.values[i] ?? null };
    });
  }

  function readout(sample: number): string {
    const rows = readAt(sample);
    const at = `${fmtT(tOf(0)[sample])}`;
    if (many) {
      const vals = rows.map((r) => r.value).filter((v) => v !== null) as number[];
      return `${at}: ${vals.length ? f.range([Math.min(...vals), Math.max(...vals)], fmt) : 'not recorded'} across ${f.count(rows.length, 'microVM')}`;
    }
    return `${at}: ${rows.map((r) => `${r.label} ${r.value === null ? 'not recorded' : fmt(r.value)}`).join(', ')}`;
  }

  function move(e: PointerEvent) {
    const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const ms = x.invert(e.clientX - box.left);
    const k = nearest(base.map((p) => p.v), ms);
    hover = k >= 0 ? base[k].i : null;
  }
  function key(e: KeyboardEvent) {
    if (!base.length) return;
    let k = hover === null ? -1 : base.findIndex((p) => p.i === hover);
    const step = e.shiftKey ? 10 : 1;
    if (e.key === 'ArrowRight') k = Math.min(base.length - 1, k + step);
    else if (e.key === 'ArrowLeft') k = Math.max(0, k - step);
    else if (e.key === 'Escape') {
      hover = null;
      return;
    } else return;
    e.preventDefault();
    hover = base[k].i;
  }
</script>

<figure class="tc">
  <figcaption>{title}{unit && unit !== '%' ? `, ${unit}` : ''}</figcaption>
  <div class="plot" bind:clientWidth={w}>
    <div
      class="focus"
      role="slider"
      tabindex="0"
      style:min-height="{height}px"
      aria-label="{title} over the trial. Arrow keys move through the samples; Shift moves faster."
      aria-valuemin={domain[0]}
      aria-valuemax={domain[1]}
      aria-valuenow={hover === null ? domain[0] : tOf(0)[hover]}
      aria-valuetext={hover === null ? 'no time selected' : readout(hover)}
      onkeydown={key}
      onfocus={() => {
        if (hover === null && base.length) hover = base[0].i;
      }}
      onblur={() => (hover = null)}
    >
      {#if w > 0}
        <svg width={w} {height} role="img" aria-label="{title} over the trial" onpointermove={move} onpointerleave={() => (hover = null)}>
          {#if taskWindow}
            <rect class="window" x={x(taskWindow[0])} y={M.top} width={Math.max(1, x(taskWindow[1]) - x(taskWindow[0]))} height={height - M.top - M.bottom} />
          {/if}
          {#each y.ticks(4) as v (v)}
            <line class="grid" x1={M.left} x2={w - M.right} y1={y(v)} y2={y(v)} />
            <text class="tick" x={M.left - 6} y={y(v)} dy="0.32em" text-anchor="end">{unit === '%' ? `${f.num(v)}%` : f.num(v, v % 1 ? 1 : 0)}</text>
          {/each}
          <line class="axis" x1={M.left} x2={w - M.right} y1={height - M.bottom} y2={height - M.bottom} />
          {#each x.ticks(Math.max(2, Math.floor(w / 80))) as v (v)}
            <text class="tick" x={x(v)} y={height - M.bottom + 15} text-anchor="middle">{fmtT(v)}</text>
          {/each}

          {#if stacked}
            {#each series as s, k (s.label)}
              {@const lo = k === 0 ? tOf(0).map(() => 0) : stacked[k - 1]}
              {@const idx = base.map((p) => p.i)}
              <path
                d={areaPath(idx.map((i) => x(tOf(0)[i])), idx.map((i) => y(lo[i])), idx.map((i) => y(stacked[k][i])))}
                fill={s.color}
                stroke="var(--bg)"
                stroke-width="1"
              />
            {/each}
          {:else}
            {#each series as s, k (s.label)}
              {@const tk = tOf(k)}
              <path
                d={linePath(s.values.map((v, i) => ({ x: x(tk[i]), y: tk[i] < domain[0] || tk[i] > domain[1] ? null : y(v) })))}
                fill="none"
                stroke={s.color}
                stroke-width={many ? 1.25 : 2}
                stroke-opacity={many ? 0.55 : 1}
                stroke-linejoin="round"
              />
            {/each}
          {/if}

          {#if ceiling}
            <line class="ref" x1={M.left} x2={w - M.right} y1={y(ceiling.value)} y2={y(ceiling.value)} />
            <text class="ref-label" x={w - M.right} y={y(ceiling.value) - 4} text-anchor="end">{ceiling.label}</text>
          {/if}
          {#if threshold}
            <line class="ref" x1={M.left} x2={w - M.right} y1={y(threshold.value)} y2={y(threshold.value)} />
            {@const nearTop = y(threshold.value) - M.top < 18}
            <text class="ref-label" x={w - M.right} y={y(threshold.value) + (nearTop ? 13 : -4)} text-anchor="end">
              rule: {threshold.op === '>=' ? 'at least' : 'below'} {plain(threshold.value)}{threshold.fired === null ? '' : threshold.fired ? ' (fired)' : ' (did not fire)'}
            </text>
          {/if}
          {#if hover !== null}
            <line class="crosshair" x1={x(tOf(0)[hover])} x2={x(tOf(0)[hover])} y1={M.top} y2={height - M.bottom} />
          {/if}
        </svg>
      {/if}
    </div>
    {#if hover !== null && w > 0}
      {@const px = x(tOf(0)[hover])}
      {@const rows = readAt(hover)}
      <div class="tip" bind:offsetWidth={tipW} style:left="{Math.max(0, px + 12 + tipW > w ? px - 12 - tipW : px + 12)}px" aria-hidden="true">
        <div class="tip-head">{fmtT(tOf(0)[hover])} from the trial's start</div>
        {#if many}
          {@const vals = rows.map((r) => r.value).filter((v) => v !== null) as number[]}
          <div class="tip-row">
            <span class="tip-v">{vals.length ? f.range([Math.min(...vals), Math.max(...vals)], fmt) : '–'}</span>
            <span class="tip-l">across {f.count(rows.length, 'microVM')}</span>
          </div>
        {:else}
          {#each [...rows].reverse() as r (r.label)}
            <div class="tip-row">
              <svg width="12" height="10" aria-hidden="true"><rect x="0" y="1" width="12" height="8" rx="2" fill={r.color} /></svg>
              <span class="tip-v">{r.value === null ? '–' : fmt(r.value)}</span>
              <span class="tip-l">{r.label}</span>
            </div>
          {/each}
        {/if}
      </div>
    {/if}
  </div>
</figure>

<style>
  .tc {
    margin: 0;
    min-width: 0;
  }
  figcaption {
    font-weight: 600;
    font-size: 0.9rem;
    margin: 4px 0 2px;
  }
  .plot {
    position: relative;
  }
  .focus {
    border-radius: 4px;
  }
  .focus:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  svg {
    display: block;
  }
  .window {
    fill: var(--surface-2);
    opacity: 0.7;
  }
  .grid {
    stroke: var(--grid);
  }
  .axis {
    stroke: var(--axis);
  }
  .tick {
    fill: var(--muted);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .ref {
    stroke: var(--ink-2);
    stroke-dasharray: 5 4;
  }
  .ref-label {
    fill: var(--ink-2);
    font-size: 11px;
    paint-order: stroke;
    stroke: var(--bg);
    stroke-width: 3px;
  }
  .crosshair {
    stroke: var(--ink-2);
    opacity: 0.6;
  }
  .tip {
    position: absolute;
    top: 6px;
    z-index: 5;
    width: max-content;
    max-width: min(20rem, 100%);
    background: var(--bg);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    box-shadow: 0 2px 10px rgb(0 0 0 / 0.12);
    padding: 6px 9px;
    font-size: 0.78rem;
    pointer-events: none;
  }
  .tip-head {
    font-weight: 600;
    margin-bottom: 3px;
  }
  .tip-row {
    display: grid;
    grid-template-columns: auto auto 1fr;
    gap: 6px;
    align-items: baseline;
  }
  .tip-v {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .tip-l {
    color: var(--ink-2);
  }
</style>
