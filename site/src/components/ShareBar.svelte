<script lang="ts">
  // A whole split into parts: one bar of stacked segments, and a legend that carries every value, so nothing is
  // read from colour or hover alone. Colours follow the parts' fixed order.
  import * as f from '../lib/format';

  let { title, parts }: { title: string; parts: { label: string; share: number; detail?: string; color?: string }[] } = $props();
  const shown = $derived(parts.map((p, i) => ({ ...p, color: p.color ?? `var(--series-${(i % 8) + 1})` })));
</script>

<figure class="share">
  <figcaption>{title}</figcaption>
  <div class="bar" role="img" aria-label="{title}: {shown.map((p) => `${p.label} ${f.num(p.share * 100)}%`).join(', ')}">
    {#each shown as p (p.label)}
      {#if p.share > 0}
        <span class="seg" style:flex-grow={p.share} style:background={p.color}></span>
      {/if}
    {/each}
  </div>
  <ul class="legend">
    {#each shown as p (p.label)}
      <li>
        <span class="sw" style:background={p.color}></span>
        <span class="v">{p.share > 0 && p.share < 0.005 ? '<1' : f.num(p.share * 100)}%</span>
        {p.label}{#if p.detail}{' · '}<span class="muted nowrap">{p.detail}</span>{/if}
      </li>
    {/each}
  </ul>
</figure>

<style>
  .share {
    margin: 12px 0;
    min-width: 0;
  }
  figcaption {
    font-weight: 600;
    font-size: 0.9rem;
    margin-bottom: 6px;
  }
  .bar {
    display: flex;
    gap: 2px;
    height: 14px;
    border-radius: 4px;
    overflow: hidden;
  }
  .seg {
    flex-basis: 0;
    min-width: 2px;
  }
  .legend {
    list-style: none;
    padding: 0;
    margin: 8px 0 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
    gap: 2px 16px;
    font-size: 0.85rem;
    max-width: none;
  }
  .sw {
    display: inline-block;
    width: 12px;
    height: 10px;
    border-radius: 2px;
    margin-right: 6px;
    vertical-align: -1px;
  }
  .nowrap {
    white-space: nowrap;
  }
  .v {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    display: inline-block;
    min-width: 2.6em;
  }
</style>
