<script lang="ts">
  // A spec's densities on one line against its worker host: a dot per density, the stretch past the density that
  // takes every host vCPU shaded, and the density that takes all its memory marked when it's in range. The chips
  // above are the accessible list; the line under the scale says the same in words.
  import { densityFit } from './draft';

  let { spec }: { spec: unknown } = $props();

  const fit = $derived(densityFit(spec));
  const list = $derived.by(() => {
    const d = (spec as { densities?: unknown } | null)?.densities;
    return Array.isArray(d) ? d.filter((x): x is number => typeof x === 'number' && x > 0) : [];
  });
  const end = $derived(Math.max(1, ...list, fit ? Math.ceil(fit.cpu) : 0));
  const x = (v: number) => `${Math.min(100, (v / end) * 100)}%`;
  const num = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
  // The tick at "vCPUs full" only when it doesn't crowd the ends.
  const cpuTick = $derived(!!fit && fit.cpu / end > 0.08 && fit.cpu / end < 0.9);
</script>

{#if list.length}
  <div class="scale" aria-hidden="true">
    <div class="track">
      {#if fit && fit.cpu < end}<span class="band" style:left={x(fit.cpu)}></span>{/if}
      {#if fit && fit.mem <= end}<span class="line" style:left={x(fit.mem)}></span>{/if}
      {#each list as d, i (i)}<span class="dot" style:left={x(d)}></span>{/each}
    </div>
    <div class="ticks">
      <span style:left="0%">0</span>
      {#if fit && cpuTick}<span class="cpu" style:left={x(fit.cpu)}>{num(fit.cpu)}</span>{/if}
      <span style:left="100%">{end}</span>
    </div>
  </div>
  {#if fit}
    <p class="cap">
      <span class="key over"></span>vCPUs full at {num(fit.cpu)}<span class="sep">·</span><span
        class="key mem"
        class:off={fit.mem > end}
      ></span>memory full at {num(fit.mem)}
    </p>
  {/if}
{/if}

<style>
  .scale {
    position: relative;
    max-width: 30rem;
    margin: 10px 0 0;
    padding: 0 6px;
  }
  .track {
    position: relative;
    height: 18px;
  }
  .track::before {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    top: 8px;
    height: 2px;
    background: var(--axis);
    border-radius: 1px;
  }
  .band {
    position: absolute;
    top: 2px;
    bottom: 2px;
    right: 0;
    background: repeating-linear-gradient(135deg, var(--axis) 0 1.5px, transparent 1.5px 5px);
    border-left: 1.5px dashed var(--ink-2);
  }
  .line {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 2px;
    margin-left: -1px;
    background: var(--critical);
  }
  .dot {
    position: absolute;
    top: 4px;
    width: 10px;
    height: 10px;
    margin-left: -5px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 0 2px var(--bg);
  }
  .ticks {
    position: relative;
    height: 1.1rem;
    font-size: 0.72rem;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .ticks span {
    position: absolute;
    transform: translateX(-50%);
  }
  .ticks .cpu {
    color: var(--ink-2);
  }
  .cap {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 2px 6px;
    margin: 2px 0 0;
    font-size: 0.8rem;
    color: var(--ink-2);
  }
  .sep {
    color: var(--muted);
    margin: 0 4px;
  }
  .key {
    display: inline-block;
    width: 12px;
    height: 10px;
  }
  .key.over {
    background: repeating-linear-gradient(135deg, var(--axis) 0 1.5px, transparent 1.5px 5px);
    border-left: 1.5px dashed var(--ink-2);
  }
  .key.mem {
    width: 2px;
    background: var(--critical);
  }
  .key.mem.off {
    display: none;
  }
</style>
