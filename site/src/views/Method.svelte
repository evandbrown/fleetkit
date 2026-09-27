<script lang="ts">
  // docs/method.md, rendered at build time.
  import { tick } from 'svelte';
  import { html } from 'virtual:method';

  let { anchor }: { anchor: string | null } = $props();

  $effect(() => {
    const id = anchor;
    tick().then(() => {
      const el = id ? document.getElementById(id) : null;
      if (el) el.scrollIntoView();
      else window.scrollTo(0, 0);
    });
  });
</script>

<article class="method">
  <!-- eslint-disable-next-line svelte/no-at-html-tags -- our own document, rendered at build time -->
  {@html html}
</article>

<style>
  .method :global(table) {
    min-width: 0;
  }
  .method :global(td),
  .method :global(th) {
    white-space: normal;
  }
</style>
