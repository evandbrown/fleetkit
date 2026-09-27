<script lang="ts">
  // Prose compiled by the dataset builder: text, glossary terms and anchored numbers. Each number says what
  // kind it is (measured, derived, modelled, assumed, rule) and where it came from, on hover and focus.
  import Term from './Term.svelte';
  import type { Cls, Seg } from '../lib/types';

  let { segs }: { segs: Seg[] } = $props();

  const KIND: Record<Cls, string> = {
    measured: 'Measured',
    derived: 'Derived from measured values',
    modelled: 'Modelled: a measured time at an assumed price',
    assumed: 'Assumed, not measured',
    rule: 'A verdict from rules fixed in the spec',
  };
</script>

{#each segs as s, i (i)}{#if 'v' in s}<span class="v {s.cls}" title="{KIND[s.cls]}. Source: {s.src}">{s.v}</span
    >{:else if 'term' in s}<Term k={s.term} text={s.t} />{:else}{s.t}{/if}{/each}

<style>
  .v {
    font-weight: 600;
  }
  .v.modelled::before {
    content: '≈';
  }
  .v.assumed {
    font-style: italic;
  }
</style>
