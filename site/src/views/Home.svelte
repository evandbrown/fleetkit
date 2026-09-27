<script lang="ts">
  // #/ opens the most recent campaign (D49).
  import { loadIndex } from '../lib/data';
  import { href } from '../lib/router';
  import Failed from '../components/Failed.svelte';

  const index = loadIndex().then((i) => {
    if (i.latest) location.replace(href({ name: 'campaign', campaign: i.latest }));
    return i;
  });
</script>

{#await index}
  <p class="status">Loading…</p>
{:then i}
  {#if !i.latest}
    <h1>No campaigns yet</h1>
    <p>Nothing has been published. Read <a href="#/method">how the experiments run</a> in the meantime.</p>
  {/if}
{:catch e}
  <Failed error={e} />
{/await}
