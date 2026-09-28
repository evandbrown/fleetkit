<script lang="ts">
  // What we tested. One spec: its host, hypervisor, microVM and densities side by side, and its extra Chromium flags
  // in full under them. Several: one column per spec and one row per input; an input that is the same for every spec
  // is written once across the row, and one that differs is highlighted in each column. The Chromium flags row is
  // there only when some spec adds flags. The header row names each column and gives its why; with no why, and names
  // that only echo the host or hypervisor row, it is left out. A cell doesn't restate its column's name.
  import { differingRows, showsChromium, specCard, type CardRow } from '../lib/shape';
  import type { SpecDoc } from '../lib/types';
  import Flags from './Flags.svelte';

  interface Item {
    key: string;
    title: string;
    why?: string;
    spec: SpecDoc;
    campaignTitle?: string;
  }
  let { items, campaigns = false }: { items: Item[]; campaigns?: boolean } = $props();

  const diff = $derived(differingRows(items.map((i) => i.spec)));
  const devices = $derived(
    new Set(items.map((i) => `${i.spec.spec.hypervisor.virtio_transport}${i.spec.spec.hypervisor.virtio_rng}`)).size > 1,
  );
  const cards = $derived(items.map((i) => specCard(i.spec, devices)));
  const heads = $derived(
    items.some((it) => it.why) ||
      !(['host', 'hypervisor'] as const).some(
        (row) => diff.has(row) && items.every((it, i) => it.title === (row === 'host' ? cards[i].host.value : cards[i].hypervisor.value)),
      ),
  );
  /** A differing cell whose value is its column's name: shown without the name. */
  const echo = (row: CardRow, i: number) =>
    heads &&
    items.length > 1 &&
    diff.has(row) &&
    (row === 'host' ? items[i].title === cards[i].host.value : items[i].title === cards[i].hypervisor.value && !!cards[i].hypervisor.sub);
  const ROWS: { key: CardRow; label: string }[] = $derived([
    { key: 'host', label: 'Host' },
    { key: 'hypervisor', label: 'Hypervisor' },
    { key: 'microvm', label: 'MicroVM' },
    { key: 'densities', label: 'Densities' },
    ...(showsChromium(items.map((i) => i.spec)) ? [{ key: 'chromium' as const, label: 'Chromium flags' }] : []),
  ]);
  /** Runs of neighbouring items from the same campaign, for the header row on Compare. */
  const groups = $derived(
    items.reduce<{ title: string; n: number }[]>((out, it) => {
      const last = out.at(-1);
      if (last && last.title === it.campaignTitle) last.n++;
      else out.push({ title: it.campaignTitle ?? '', n: 1 });
      return out;
    }, []),
  );
</script>

{#snippet value(row: CardRow, i: number)}
  {@const c = cards[i]}
  {#if row === 'host'}
    {#if !echo(row, i)}<strong>{c.host.value}</strong>{/if}<span class="sub">{c.host.sub}</span>
  {:else if row === 'hypervisor'}
    {#if !echo(row, i)}<strong>{c.hypervisor.value}</strong>{/if}{#if c.hypervisor.sub}<span class="sub">{c.hypervisor.sub}</span>{/if}
  {:else if row === 'microvm'}
    <strong>{c.microvm}</strong>
  {:else if row === 'chromium'}
    <strong><Flags flags={c.chromium} /></strong>
  {:else}
    <span class="chips">{#each c.densities as d (d)}<span>{d}</span>{/each}</span>
  {/if}
{/snippet}

{#if items.length === 1}
  <div class="single">
    {#if items[0].why}<p class="why">{items[0].why}</p>{/if}
    <dl>
      {#each ROWS as row (row.key)}
        <div class="in" class:wide={row.key === 'chromium'}><dt>{row.label}</dt><dd>{@render value(row.key, 0)}</dd></div>
      {/each}
    </dl>
  </div>
{:else}
  <div class="matrix" style:--n={items.length}>
    {#if campaigns}
      <div class="corner" aria-hidden="true"></div>
      {#each groups as g, i (i)}<div class="campaign" style:grid-column="span {g.n}">{g.title}</div>{/each}
    {/if}
    {#if heads}
      <div class="corner" aria-hidden="true"></div>
      {#each items as it (it.key)}
        <div class="head">
          <strong>{it.title}</strong>
          {#if it.why}<p class="why">{it.why}</p>{/if}
        </div>
      {/each}
    {/if}
    {#each ROWS as row (row.key)}
      <div class="rl">{row.label}</div>
      {#if diff.has(row.key)}
        {#each items as it, i (it.key)}<div class="cell diff">{@render value(row.key, i)}</div>{/each}
      {:else}
        <div class="cell same">{@render value(row.key, 0)}</div>
      {/if}
    {/each}
  </div>
{/if}

<style>
  .single {
    border: 1px solid var(--rule);
    border-radius: 10px;
    padding: 14px 16px;
  }
  .single dl {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 14px 20px;
  }
  @media (max-width: 760px) {
    .single dl {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .in {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .in.wide {
    grid-column: 1 / -1;
  }
  dt,
  .rl {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
  }
  dd {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }
  .why {
    margin: 0 0 10px;
    font-size: 0.85rem;
    color: var(--ink-2);
  }

  .matrix {
    display: grid;
    grid-template-columns: 6.5rem repeat(var(--n), minmax(0, 1fr));
    column-gap: 8px;
    border-top: 1px solid var(--rule);
  }
  .campaign {
    padding: 10px 0 0;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
  }
  .head {
    padding: 10px 0 8px;
    min-width: 0;
  }
  .head strong {
    font-size: 1rem;
  }
  .head .why {
    margin: 2px 0 0;
    font-size: 0.82rem;
    line-height: 1.45;
  }
  .rl,
  .cell {
    border-top: 1px solid var(--rule);
    padding: 9px 0;
  }
  .cell {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }
  .cell.same {
    grid-column: 2 / -1;
    color: var(--ink-2);
  }
  .cell.same strong {
    font-weight: 500;
  }
  .cell.diff {
    background: var(--highlight);
    padding: 9px 10px;
    border-radius: 6px;
    border-top-color: transparent;
    margin: 3px 0;
  }
  .cell.diff strong {
    font-weight: 700;
    color: var(--ink);
  }

  strong {
    font-weight: 600;
  }
  .sub {
    font-size: 0.82rem;
    color: var(--ink-2);
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .chips span {
    min-width: 1.8em;
    text-align: center;
    font-size: 0.82rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    border: 1px solid var(--rule);
    border-radius: 4px;
    padding: 0 4px;
    background: var(--bg);
  }

  @media (max-width: 600px) {
    .matrix {
      grid-template-columns: repeat(var(--n), minmax(0, 1fr));
    }
    .corner {
      display: none;
    }
    .rl {
      grid-column: 1 / -1;
      padding-bottom: 0;
    }
    .rl + .cell {
      border-top: 0;
    }
    .cell.same {
      grid-column: 1 / -1;
    }
    .cell.diff {
      margin-top: 4px;
    }
  }
</style>
