<script lang="ts">
  // The featured card's picture of one run: a bar per browser count for how close its slowest trial came to failing (its
  // closest SLO as a share of that SLO's limit, `closestSlo`), against a dashed line at the limit. Green under the
  // line, red over it; a dashed ring where a density wasn't tested. Pass and fail come from the run's own results,
  // the heights are only for drawing.
  import { closestSlo, RATIO_CAP } from '../../lib/shape';
  import { linear } from '../../lib/scale';
  import type { RunDoc } from '../../lib/types';
  import MarkShape from '../MarkShape.svelte';

  let { run, densities }: { run: RunDoc; densities: number[] } = $props();

  let w = $state(0);
  const H = 96;
  const TOP = 14; // room for the limit's label
  const BASE = H - 16; // the axis, with the densities' labels under it
  const RIGHT = 34; // the limit's label sits past the last bar

  const bars = $derived(
    densities.map((d) => {
      const brief = run.by_density.find((b) => b.density === d) ?? null;
      const trials = run.trials.filter((t) => t.density === d && t.counts);
      const ratio = trials.length ? Math.max(...trials.map((t) => closestSlo(t).ratio)) : null;
      const result = brief?.result ?? 'not_tested';
      return { d, ratio, result };
    }),
  );
  const top = $derived(Math.max(1.3, ...bars.map((b) => (b.ratio ?? 0) * 1.1), 0) || RATIO_CAP);
  const y = $derived(linear([0, Math.min(top, RATIO_CAP * 1.1)], [BASE, TOP]));
  const slot = $derived(bars.length ? (w - RIGHT) / bars.length : 0);
  const bw = $derived(Math.max(4, Math.min(18, slot * 0.58)));
  const cx = (i: number) => slot * i + slot / 2;
</script>

<figure class="headroom" bind:clientWidth={w}>
  {#if w > 0 && bars.length}
    <svg width={w} height={H} viewBox="0 0 {w} {H}" role="img" aria-label="Closest SLO at each browser count, as a share of its limit">
      <line class="axis" x1="0" x2={w - RIGHT} y1={BASE} y2={BASE} />
      <line class="limit" x1="0" x2={w - RIGHT + 4} y1={y(1)} y2={y(1)} />
      <text class="limit-label" x={w - RIGHT + 8} y={y(1)} dominant-baseline="middle">SLO</text>
      {#each bars as b, i (b.d)}
        {#if b.ratio !== null && b.result !== 'not_tested'}
          <rect
            class={b.result}
            x={cx(i) - bw / 2}
            y={Math.min(y(b.ratio), BASE - 1)}
            width={bw}
            height={Math.max(1, BASE - y(b.ratio))}
            rx="2"
          />
        {:else}
          <MarkShape kind="untested" cx={cx(i)} cy={BASE - 5} size={9} />
        {/if}
        <text class="d" x={cx(i)} y={H - 3} text-anchor="middle">{b.d}</text>
      {/each}
    </svg>
  {/if}
  <figcaption>Closest SLO per browser count</figcaption>
</figure>

<style>
  .headroom {
    margin: 12px 0 0;
    width: 100%;
  }
  svg {
    display: block;
    overflow: visible;
  }
  .axis {
    stroke: var(--axis);
    stroke-width: 1;
  }
  .limit {
    stroke: var(--ink-2);
    stroke-width: 1;
    stroke-dasharray: 3 3;
  }
  .limit-label {
    font-size: 11px;
    font-weight: 600;
    fill: var(--ink-2);
  }
  .passed {
    fill: var(--good);
  }
  .failed {
    fill: var(--critical);
  }
  .d {
    font-size: 11px;
    fill: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  figcaption {
    margin-top: 4px;
    font-size: 0.78rem;
    color: var(--muted);
  }
</style>
