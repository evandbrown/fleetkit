<script lang="ts">
  // A trial's result inline with text: the same shapes as the charts (MarkShape).
  import MarkShape, { type MarkKind } from './MarkShape.svelte';

  let { kind, size = 12 }: { kind: MarkKind; size?: number } = $props();
</script>

<svg class="mark" width={size + 2} height={size + 2} viewBox="-1 -1 {size + 2} {size + 2}" aria-hidden="true"
  ><MarkShape {kind} cx={size / 2} cy={size / 2} {size} /></svg
>

<style>
  .mark {
    display: inline-block;
    vertical-align: -2px;
    flex: none;
  }
</style>
