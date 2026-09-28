<svelte:options namespace="svg" />

<script lang="ts" module>
  export type MarkKind = 'pass' | 'fail' | 'untested';
</script>

<script lang="ts">
  // A trial's result, drawn the same on every page: a filled circle passed, a cross failed, a dashed ring not
  // tested. SVG only, for use inside a chart; Mark.svelte wraps it for text.
  let { kind, cx = 0, cy = 0, size = 12 }: { kind: MarkKind; cx?: number; cy?: number; size?: number } = $props();
  const r = $derived(size / 2);
  const a = $derived(size * 0.38);
</script>

{#if kind === 'pass'}
  <circle class="m-pass" {cx} {cy} {r} />
{:else if kind === 'fail'}
  <path class="m-fail" d="M{cx - a},{cy - a}l{2 * a},{2 * a}M{cx + a},{cy - a}l{-2 * a},{2 * a}" style:stroke-width={Math.max(2, size / 5)} />
{:else}
  <circle class="m-untested" {cx} {cy} r={r - 1} />
{/if}

<style>
  .m-pass {
    fill: var(--good);
    stroke: var(--bg);
    stroke-width: 1.5;
  }
  .m-fail {
    fill: none;
    stroke: var(--critical);
    stroke-linecap: round;
  }
  .m-untested {
    fill: var(--bg);
    stroke: var(--muted);
    stroke-width: 1.3;
    stroke-dasharray: 2 2;
  }
</style>
