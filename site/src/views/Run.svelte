<script lang="ts">
  // One run (RunPage), loaded only when someone opens a run.
  let props: { campaign: string; run: string; density: number | null } = $props();
  const page = import('./RunPage.svelte');
</script>

{#await page}
  <p class="status">Loading…</p>
{:then m}
  <m.default {...props} />
{:catch}
  <p class="status error">This page didn't load. Reload to try again.</p>
{/await}
