<script lang="ts">
  // Compare specs (ComparePage), loaded only when someone opens it.
  let props: { specs: string[] | null } = $props();
  const page = import('./ComparePage.svelte');
</script>

{#await page}
  <p class="status">Loading…</p>
{:then m}
  <m.default {...props} />
{:catch}
  <p class="status error">This page didn't load. Reload to try again.</p>
{/await}
