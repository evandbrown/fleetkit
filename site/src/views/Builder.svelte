<script lang="ts">
  // The experiment builder's page. The editor, with the schemas and campaign files it bundles, loads only when
  // someone opens the builder, so the rest of the site never downloads it.
  const editor = import('./BuilderEditor.svelte');
</script>

<h1>Builder</h1>
<p class="lead">Define a campaign and see what it runs and costs.</p>
{#await editor}
  <p class="status">Loading…</p>
{:then m}
  <m.default />
{:catch}
  <p class="status error">The builder didn't load. Reload to try again.</p>
{/await}

<style>
  .lead {
    margin-top: 0;
  }
</style>
