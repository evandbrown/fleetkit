<script lang="ts">
  // One trial (TrialPage). Its charts load only when someone opens a trial, so Results and About never download them.
  let props: { campaign: string; run: string; trial: string; microvm: number | null } = $props();
  const page = import('./TrialPage.svelte');
</script>

{#await page}
  <p class="status">Loading…</p>
{:then m}
  <m.default {...props} />
{:catch}
  <p class="status error">This page didn't load. Reload to try again.</p>
{/await}
