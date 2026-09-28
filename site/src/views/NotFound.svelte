<script lang="ts">
  import { href } from '../lib/router';

  let { path }: { path: string } = $props();
</script>

<h1>Not found</h1>
<p>No page at <code>#{path}</code>. <a href={href({ name: 'results', campaign: null })}>Go to Results</a>.</p>
