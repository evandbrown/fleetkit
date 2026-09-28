<script lang="ts">
  // A trial's latencies against their SLOs: for each step a thick p50 bar and a thin p95 bar in the step's colour,
  // with the p50 and p95 limits as ticks; the whole task on its own row against its own limit. A bar past its limit
  // is outlined in red.
  import * as f from '../lib/format';
  import { SUBJECT_LABEL } from '../lib/glossary';
  import { linear } from '../lib/scale';
  import { STEP_NAMES, type Check, type Spec, type Subject } from '../lib/types';

  let { checks, criteria }: { checks: Check[]; criteria: Spec['criteria'] } = $props();

  let w = $state(0);
  const phone = $derived(w > 0 && w < 560);
  const LABEL = $derived(phone ? 92 : 120);
  const VAL = $derived(phone ? 112 : 120);
  /** Where the p50 column ends; the p95 column ends at the right edge. */
  const P50 = 64;
  const HEAD = 20;
  const ROW = 30;
  const GAP = 14;

  const get = (s: Subject, stat: 'p50' | 'p95') => checks.find((c) => c.subject === s && c.stat === stat) ?? null;
  const steps = $derived(STEP_NAMES.filter((s) => get(s, 'p50') || get(s, 'p95')));
  const task = $derived(get('task', 'p95'));

  const stepMax = $derived(Math.max(criteria.step_p95_target_ms * 1.12, ...checks.filter((c) => c.subject !== 'task').map((c) => c.value_ms * 1.04)));
  const taskMax = $derived(Math.max(criteria.task_p95_target_ms * 1.12, (task?.value_ms ?? 0) * 1.04));
  const xs = $derived(linear([0, stepMax], [LABEL, Math.max(LABEL + 40, w - VAL)]));
  const xt = $derived(linear([0, taskMax], [LABEL, Math.max(LABEL + 40, w - VAL)]));
  const taskTop = $derived(HEAD + steps.length * ROW + GAP + 8);
  const H = $derived(taskTop + (task ? ROW : 0) + 4);
  const sec = (ms: number) => `${f.num(ms / 1000, ms % 1000 ? 1 : 0)} s`;
  const colour = (s: Subject) => (s === 'task' ? 'var(--ink-2)' : `var(--step-${s.replaceAll('_', '-')})`);
</script>

<figure class="steps" bind:clientWidth={w}>
  {#if w > 0}
    <svg width={w} height={H} role="img" aria-label="Each step's p50 and p95 against the limits, and the whole task's p95">
      <text class="lim" x={xs(criteria.step_p50_target_ms) - 4} y="12" text-anchor="end">p50 ≤ {sec(criteria.step_p50_target_ms)}</text>
      <text class="lim" x={xs(criteria.step_p95_target_ms) - 4} y="12" text-anchor="end">p95 ≤ {sec(criteria.step_p95_target_ms)}</text>
      <text class="vh" x={w - P50} y="12" text-anchor="end">p50 (ms)</text>
      <text class="vh" x={w - 4} y="12" text-anchor="end">p95 (ms)</text>
      {#each steps as s, i (s)}
        {@const y = HEAD + i * ROW}
        {@const a = get(s, 'p50')}
        {@const b = get(s, 'p95')}
        <text class="step" x="0" y={y + 15}>{SUBJECT_LABEL[s]}</text>
        <rect class="track" x={LABEL} y={y + 4} width={xs.range[1] - LABEL} height="20" rx="3" />
        {#if a}<rect class="bar" class:over={!a.met} x={LABEL} y={y + 5} width={Math.max(1, xs(a.value_ms) - LABEL)} height="11" rx="2" fill={colour(s)} />{/if}
        {#if b}<rect class="bar" class:over={!b.met} x={LABEL} y={y + 18} width={Math.max(1, xs(b.value_ms) - LABEL)} height="5" rx="1.5" fill={colour(s)} />{/if}
        <text class="v" class:bad={a && !a.met} x={w - P50} y={y + 15} text-anchor="end">{a ? f.vs(a.value_ms, a.target_ms) : '–'}</text>
        <text class="v2" class:bad={b && !b.met} x={w - 4} y={y + 15} text-anchor="end">{b ? f.vs(b.value_ms, b.target_ms) : '–'}</text>
      {/each}
      <line class="limit" x1={xs(criteria.step_p50_target_ms)} x2={xs(criteria.step_p50_target_ms)} y1={HEAD - 2} y2={HEAD + steps.length * ROW} />
      <line class="limit" x1={xs(criteria.step_p95_target_ms)} x2={xs(criteria.step_p95_target_ms)} y1={HEAD - 2} y2={HEAD + steps.length * ROW} />

      {#if task}
        {@const y = taskTop}
        <line class="sep" x1="0" x2={w} y1={y - GAP} y2={y - GAP} />
        <text class="step" x="0" y={y + 20}>{SUBJECT_LABEL.task}</text>
        <rect class="track" x={LABEL} y={y + 6} width={xt.range[1] - LABEL} height="20" rx="3" />
        <rect class="bar" class:over={!task.met} x={LABEL} y={y + 10} width={Math.max(1, xt(task.value_ms) - LABEL)} height="11" rx="2" fill={colour('task')} />
        <line class="limit" x1={xt(criteria.task_p95_target_ms)} x2={xt(criteria.task_p95_target_ms)} y1={y + 4} y2={y + 28} />
        <text class="lim" x={xt(criteria.task_p95_target_ms) - 4} y={y + 2} text-anchor="end">p95 ≤ {sec(criteria.task_p95_target_ms)}</text>
        <text class="v2" class:bad={!task.met} x={w - 4} y={y + 20} text-anchor="end">{f.vs(task.value_ms, task.target_ms)}</text>
      {/if}
    </svg>
  {/if}
  <figcaption>
    <span class="key"><i class="thick"></i>p50</span>
    <span class="key"><i class="thin"></i>p95</span>
    <span class="key"><i class="rule"></i>limit</span>
    <span class="key"><i class="thick over"></i>past its limit</span>
  </figcaption>
</figure>

<style>
  .steps {
    margin: 0;
    min-width: 0;
  }
  svg {
    display: block;
    overflow: visible;
  }
  .track {
    fill: var(--surface);
  }
  .bar.over {
    stroke: var(--critical);
    stroke-width: 2;
  }
  .limit {
    stroke: var(--ink);
    stroke-width: 1.5;
  }
  .sep {
    stroke: var(--rule);
  }
  .step {
    fill: var(--ink);
    font-size: 13px;
  }
  .lim,
  .vh {
    fill: var(--muted);
    font-size: 11px;
  }
  .v,
  .v2 {
    font-size: 12.5px;
    font-variant-numeric: tabular-nums;
    fill: var(--ink);
    font-weight: 600;
  }
  .v2 {
    fill: var(--ink-2);
    font-weight: 400;
  }
  .bad {
    fill: var(--critical);
    font-weight: 700;
  }
  figcaption {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    font-size: 0.82rem;
    color: var(--ink-2);
    margin-top: 6px;
  }
  .key {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .key i {
    display: inline-block;
    width: 16px;
    background: var(--ordinal-2);
    border-radius: 2px;
  }
  .thick {
    height: 9px;
  }
  .thin {
    height: 4px;
  }
  .thick.over {
    background: var(--surface) !important;
    outline: 2px solid var(--critical);
    outline-offset: -2px;
  }
  .key i.rule {
    width: 2px;
    height: 12px;
    background: var(--ink);
  }
</style>
