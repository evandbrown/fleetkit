<script lang="ts">
  // A spec's SLOs in the What we tested table: one line per target, its name a defined term (Term.svelte), its value,
  // and the standard value in muted text where this spec's differs (D74). The order and wording are sloChips', the
  // same as About's badges and a trial's checks. The standard sits beside the value and wraps under it in a narrow
  // cell, so the list never widens the table.
  import { sloChips, type SloChip } from '../lib/shape';
  import type { Criteria } from '../lib/shape';
  import Term from './Term.svelte';

  let { criteria }: { criteria: Criteria } = $props();

  /** What each target means, in one or two plain sentences. */
  const MEANING: Record<SloChip['key'], string> = {
    ready_timeout_s: 'From starting a microVM to its browser answering. Every browser in the trial must be ready within this.',
    tasks: 'Every task in the trial finishes all five steps. One task that does not fails the trial.',
    step_p50_target_ms: 'The median time of each of the five steps, over every browser in the trial. Each step must be within this.',
    step_p95_target_ms: 'The time 95 of 100 runs of a step finish within, for each of the five steps. The slowest 5% may take longer.',
    task_p95_target_ms: 'The time 95 of 100 whole tasks finish within, from the first step to the last.',
    step_timeout_ms: 'A step that runs longer than this is abandoned, and its task fails.',
    task_timeout_ms: 'A task that runs longer than this is abandoned and counts as failed.',
  };

  const chips = $derived(sloChips(criteria, true));
</script>

<ul class="slo-list" aria-label="SLOs">
  {#each chips as c (c.key)}
    <li>
      <span class="name"><Term text={MEANING[c.key]}>{c.long}</Term></span>
      <span class="value">{c.value}{#if c.standard}{' '}<span class="standard">standard {c.standard.replace(/^≤ /, '')}</span>{/if}</span>
    </li>
  {/each}
</ul>

<style>
  .slo-list {
    list-style: none;
    margin: 0;
    padding: 0;
    max-width: none;
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: 3px 10px;
    align-items: baseline;
    font-weight: 400;
    color: var(--ink);
  }
  li {
    display: contents;
  }
  .name {
    color: var(--ink-2);
    white-space: nowrap;
  }
  .value {
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }
  .standard {
    display: inline-block;
    font-size: 0.82rem;
    font-weight: 400;
    color: var(--muted);
    white-space: nowrap;
  }
</style>
