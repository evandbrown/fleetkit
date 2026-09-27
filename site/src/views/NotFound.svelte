<script lang="ts">
  let { path }: { path: string } = $props();
</script>

<h1>Not found</h1>
<p>There's no page at <code>#{path}</code>. <a href="#/campaigns">See every campaign</a>.</p>
