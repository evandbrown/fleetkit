<script lang="ts">
  // Where a page's evidence is public on GitHub (D73), in one quiet row: the campaign's definition, the page's dataset
  // file, the harness at the commit it ran. Each opens in a new tab. Nothing when there's nothing to link.
  import type { SourceLink } from '../lib/repo';

  let { links }: { links: SourceLink[] } = $props();
</script>

{#if links.length}
  <nav class="source" aria-labelledby="source-label">
    <span class="k" id="source-label">Source</span>
    {#each links as l (l.href)}
      <a href={l.href} target="_blank" rel="noopener">{l.label}<span class="out" aria-hidden="true">↗</span></a>
    {/each}
  </nav>
{/if}

<style>
  .source {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 2px 14px;
    font-size: 0.85rem;
  }
  .k {
    color: var(--muted);
  }
  a {
    color: var(--ink-2);
    white-space: nowrap;
  }
  a:hover {
    color: var(--accent-ink);
  }
  .out {
    margin-left: 2px;
    font-size: 0.75rem;
    color: var(--muted);
  }
</style>
