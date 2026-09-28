<script lang="ts">
  // The success criteria as one row of badges, read from a spec: the same badges on About, Results and a trial. With
  // a trial's results, each badge shows what the trial measured against its limit, with a pass or fail mark when the
  // trial is judged, and none when it isn't (a warm-up or the illustration).
  import { num } from '../lib/format';
  import type { Spec } from '../lib/types';
  import Mark from './Mark.svelte';

  let {
    criteria,
    results = null,
    judged = true,
  }: {
    criteria: Spec['criteria'];
    results?: { value: string; met: boolean; note?: string }[] | null;
    judged?: boolean;
  } = $props();

  /** 1000 → "1 s", 1500 → "1.5 s", 750 → "750 ms". */
  function time(ms: number): string {
    return ms >= 1000 ? `${num(ms / 1000, ms % 1000 === 0 ? 0 : ms % 100 === 0 ? 1 : 2)} s` : `${num(ms)} ms`;
  }

  const badges = $derived([
    { value: `≤ ${num(criteria.ready_timeout_s)} s`, label: 'Browser ready' },
    { value: '100%', label: 'Tasks succeed' },
    { value: `≤ ${time(criteria.step_p50_target_ms)}`, label: 'Each step p50' },
    { value: `≤ ${time(criteria.step_p95_target_ms)}`, label: 'Each step p95' },
    { value: `≤ ${time(criteria.task_p95_target_ms)}`, label: 'Whole task p95' },
  ]);
</script>

<ul class="slos" class:results={!!results} aria-label="Success criteria">
  {#each badges as b, i (b.label)}
    {@const r = results?.[i]}
    {#if r}
      <li class:bad={judged && !r.met}>
        <span class="value">{#if judged}<Mark kind={r.met ? 'pass' : 'fail'} size={11} />{/if}{r.value}</span>
        <span class="label">{b.label} {b.value}{#if r.note}<span class="note">{' · '}{r.note}</span>{/if}</span>
      </li>
    {:else}
      <li><span class="value">{b.value}</span><span class="label">{b.label}</span></li>
    {/if}
  {/each}
</ul>

<style>
  .slos {
    list-style: none;
    margin: 0;
    padding: 0;
    max-width: none;
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 150px));
    gap: 8px;
  }
  .slos.results {
    grid-template-columns: repeat(5, minmax(0, 170px));
  }
  li {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    padding: 10px 12px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--surface);
  }
  .value {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 1.1rem;
    font-weight: 650;
    letter-spacing: -0.01em;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .bad .value {
    color: var(--critical);
  }
  .label {
    font-size: 0.78rem;
    color: var(--ink-2);
  }
  .note {
    color: var(--muted);
  }
  /* A phone: the five criteria still one row, smaller; a trial's measured values wrap. */
  @media (max-width: 600px) {
    .slos {
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 4px;
    }
    .slos:not(.results) li {
      padding: 7px 5px;
    }
    .slos:not(.results) .value {
      font-size: 0.85rem;
    }
    .slos:not(.results) .label {
      font-size: 0.68rem;
      line-height: 1.3;
    }
    .slos.results {
      grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
      gap: 6px;
    }
  }
</style>
