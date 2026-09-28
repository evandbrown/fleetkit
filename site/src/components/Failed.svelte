<script lang="ts">
  import { DataError } from '../lib/data';
  import { href } from '../lib/router';

  let { error }: { error: unknown } = $props();
  const missing = $derived(error instanceof DataError && error.status === 404);
</script>

<div class="status error" role="alert">
  {#if missing}
    <p>That isn't in this dataset. <a href={href({ name: 'results', campaign: null })}>Back to results</a></p>
  {:else}
    <p>{error instanceof Error ? error.message : String(error)}</p>
    <p><button type="button" onclick={() => location.reload()}>Try again</button></p>
  {/if}
</div>
