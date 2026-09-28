<script lang="ts">
  // Results: the campaigns as cards, newest first, each with its name, the question it answers (one line) and its
  // best spec's headline. The selected one is marked; choosing another opens it.
  import { href } from '../lib/router';
  import { campaignHeadline } from '../lib/shape';
  import type { Index } from '../lib/types';

  let { index, selected }: { index: Index; selected: string } = $props();
  const specs = $derived(index.campaigns.reduce((n, c) => n + c.specs.length, 0));
</script>

<div class="chooser">
  <nav class="cards" aria-label="Results">
    {#each index.campaigns as c (c.id)}
      {@const h = campaignHeadline(c)}
      <a class="card" href={href({ name: 'results', campaign: c.id })} aria-current={c.id === selected ? 'page' : undefined}>
        <span class="name">{c.title}</span>
        <span class="q">{c.question}</span>
        <span class="fig">
          {#if h.density !== null}<strong>{h.density}</strong>{/if}
          {h.label}{#if h.perVcpu !== null}{' · '}<strong class="per">{h.perVcpu}</strong> per vCPU{/if}
        </span>
      </a>
    {/each}
  </nav>
  {#if specs > 1}<a class="quiet compare" href={href({ name: 'compare', specs: null })}>Compare specs</a>{/if}
</div>

<style>
  .chooser {
    margin-top: 20px;
    display: flex;
    flex-direction: column;
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 17rem), 1fr));
    gap: 10px;
  }
  .card {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 12px 14px;
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--bg);
    color: var(--ink);
    text-decoration: none;
  }
  .card:hover {
    border-color: var(--accent);
    text-decoration: none;
  }
  .card[aria-current='page'] {
    border-color: var(--accent);
    box-shadow: inset 0 0 0 1px var(--accent);
    background: var(--highlight);
  }
  .name {
    font-weight: 650;
    font-size: 0.95rem;
  }
  .name,
  .q {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .q {
    font-size: 0.82rem;
    color: var(--ink-2);
  }
  .fig {
    margin-top: 6px;
    font-size: 0.82rem;
    color: var(--ink-2);
    white-space: nowrap;
  }
  strong {
    font-size: 1.25rem;
    font-weight: 650;
    color: var(--ink);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }
  strong.per {
    font-size: 1rem;
  }
  .compare {
    align-self: flex-end;
    margin-top: 8px;
  }
</style>
