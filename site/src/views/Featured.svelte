<script lang="ts">
  // #/results : Results, opened on the featured campaign (D58). The home page, #/, is About (D72).
  import { loadIndex } from '../lib/data';
  import Failed from '../components/Failed.svelte';
  import Campaign from './Campaign.svelte';

  const index = loadIndex();
</script>

{#await index}
  <p class="status">Loading…</p>
{:then i}
  {#if i.featured}
    {#key i.featured}<Campaign id={i.featured} />{/key}
  {:else}
    <h1>No results yet</h1>
  {/if}
{:catch e}
  <Failed error={e} />
{/await}
