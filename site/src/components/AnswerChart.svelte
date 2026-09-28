<script lang="ts">
  // How each spec performed, as one chart with its figures beside it. A row per spec: a bar per run (replica) from 0
  // to the highest density that met every SLO, hatched on to the first density that failed (✕), and a tick at the
  // spec's midpoint; then the highest density, per host vCPU, cost per 1,000 tasks and what ran out. Each bar and
  // replica label opens its run. On one host size the axis is the density; across sizes, density per host vCPU.
  import * as f from '../lib/format';
  import { specColor } from '../lib/colors';
  import { tickText } from '../lib/bands';
  import { niceDomain } from '../lib/scale';
  import { ticks as d3ticks } from 'd3-array';
  import type { AnswerRow } from '../lib/shape';
  import Mark from './Mark.svelte';

  let { rows, campaigns = false }: { rows: AnswerRow[]; campaigns?: boolean } = $props();

  const perVcpu = $derived(new Set(rows.flatMap((r) => r.runs.map((x) => x.hostVcpus))).size > 1);
  const v = (d: number, vcpus: number) => (perVcpu ? d / vcpus : d);
  const top = $derived(
    niceDomain(
      rows.flatMap((r) => r.runs.flatMap((x) => [x.failed, x.passed].filter((d): d is number => d !== null).map((d) => v(d, x.hostVcpus) * 1.04))),
      { count: 6 },
    )[1],
  );
  const pos = (x: number) => `${Math.max(0, Math.min(100, (x / top) * 100))}%`;
  const axis = $derived(d3ticks(0, top, perVcpu ? 5 : 8).filter((t) => t <= top + 1e-9 && (perVcpu || Number.isInteger(t))));
  const midX = (r: AnswerRow) => (r.midpoint === null ? null : perVcpu ? r.midpoint : r.midpoint * r.hostVcpus);
  const replicas = $derived(rows.some((r) => r.runs.length > 1));
  const runLabel = (k: number) => (replicas ? `replica ${k}` : 'run');
</script>

<div class="answer" class:replicas>
  <div class="head" aria-hidden="true">
    <span>Spec</span>
    <span class="chartcol">Densities that met every SLO</span>
    <span class="num">Max density</span>
    <span class="num">Per vCPU</span>
    <span class="num">$ / 1k tasks</span>
    <span>Ran out</span>
  </div>
  <ol aria-label="How each spec performed">
    {#each rows as r, i (r.key)}
      {#if campaigns && (i === 0 || rows[i - 1].campaign !== r.campaign)}
        <li class="group" aria-hidden="true">{r.campaignTitle}</li>
      {/if}
      {@const mid = midX(r)}
      <li class="spec" style:--c={specColor(r.series)}>
        <p class="name"><i class="dot" aria-hidden="true"></i>{r.label}</p>
        <div class="chartcol bars">
          {#each r.runs as run (run.runId)}
            {@const p = run.passed === null ? null : v(run.passed, run.hostVcpus)}
            {@const x = run.failed === null ? null : v(run.failed, run.hostVcpus)}
            {@const aria = `${r.label}, ${runLabel(run.replica)}: ${run.passed === null ? 'none passed' : `passed up to ${run.passed}`}${run.failed === null ? (run.stoppedEarly ? ', stopped early' : '') : `, failed at ${run.failed}`}`}
            <svelte:element this={run.href ? 'a' : 'div'} class="run" href={run.href} aria-label={run.href ? `${aria}. Open the run` : aria}>
              <span class="rl" data-r={run.replica}>{runLabel(run.replica)}</span>
              <span class="track">
                {#if run.href === null}
                  <span class="none">not run yet</span>
                {:else}
                  {#if p !== null}<span class="pass" style:width={pos(p)}><b>{run.passed}</b></span>{/if}
                  {#if x !== null}
                    <span class="gap" style:left={pos(p ?? 0)} style:width="calc({pos(x)} - {pos(p ?? 0)})"></span>
                    <span class="x" style:left={pos(x)}><Mark kind="fail" size={11} /></span>
                  {/if}
                  {#if x === null && run.stoppedEarly}
                    <span class="x" style:left={pos(p ?? 0)}><Mark kind="untested" size={10} /></span>
                  {/if}
                  {#if p === null}<span class="val" style:left="0">none passed</span>{/if}
                  {#if x === null && run.stoppedEarly}<span class="val note" style:left={pos(p ?? 0)}>stopped early</span>{/if}
                {/if}
              </span>
            </svelte:element>
          {/each}
          {#if mid !== null}
            <span class="mid" style:left="calc(var(--rl) + (100% - var(--rl)) * {Math.min(1, mid / top)})" title="Midpoint {f.num(mid, perVcpu ? 2 : 1)}{r.partial ? ` (${r.partial})` : ''}"></span>
          {/if}
        </div>
        <dl class="figs">
          <div class="num"><dt>Max density</dt><dd><strong>{r.density}</strong></dd></div>
          <div class="num"><dt>Per vCPU</dt><dd>{r.perVcpu ?? '–'}</dd></div>
          <div class="num"><dt>$ / 1k tasks</dt><dd>{r.cost ?? '–'}</dd></div>
          <div class="out"><dt>Ran out</dt><dd>{r.ranOut}{#if r.ranOutAt}<span class="at">{r.ranOutAt}</span>{/if}</dd></div>
        </dl>
      </li>
    {/each}
  </ol>
  <div class="axis" aria-hidden="true">
    <span></span>
    <span class="chartcol ticks">
      {#each axis as t (t)}<span class="t" style:left="calc(var(--rl) + (100% - var(--rl)) * {t / top})">{tickText(t, perVcpu)}</span>{/each}
      <span class="title">{perVcpu ? 'Density per host vCPU' : 'Density (microVMs)'}</span>
    </span>
  </div>
  <p class="key" aria-hidden="true">
    <span><i class="sw" style:background={specColor(0)}></i>met every SLO, up to the density shown</span>
    <span><i class="sw gap-sw"></i>last pass to first failure</span>
    <span><Mark kind="fail" size={10} />first failure</span>
    <span><i class="sw mid-sw"></i>midpoint</span>
    {#if rows.some((r) => r.runs.some((x) => x.failed === null && x.stoppedEarly))}<span><Mark kind="untested" size={10} />stopped early</span>{/if}
    <!-- On a phone the row labels shorten to r1, r2…; the key says what r is. -->
    {#if replicas}<span class="rkey">r = replica</span>{/if}
  </p>
</div>

<style>
  .answer {
    --rl: 0px;
    margin: 4px 0 0;
    font-size: 0.92rem;
  }
  .answer.replicas {
    --rl: 64px;
  }
  .head,
  .spec,
  .axis {
    display: grid;
    grid-template-columns: 11.5rem minmax(0, 1fr) 5.5rem 4.5rem 7.5rem 7.5rem;
    column-gap: 16px;
    align-items: center;
  }
  .head {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
    padding-bottom: 6px;
    border-bottom: 1px solid var(--rule);
  }
  .head .chartcol {
    padding-left: var(--rl);
  }
  .num {
    text-align: right;
  }
  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    max-width: none;
  }
  .group {
    padding: 12px 0 0;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
  }
  .spec {
    padding: 10px 0;
    border-bottom: 1px solid var(--rule);
  }
  .name {
    margin: 0;
    font-weight: 600;
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }
  .dot {
    flex: none;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--c);
    transform: translateY(1px);
  }
  .bars {
    position: relative;
    overflow-x: clip;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .run {
    display: grid;
    grid-template-columns: var(--rl) minmax(0, 1fr);
    align-items: center;
    height: 20px;
    color: var(--ink);
    text-decoration: none;
    border-radius: 4px;
  }
  a.run:hover .track,
  a.run:focus-visible .track {
    background: var(--highlight);
  }
  a.run:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .rl {
    font-size: 0.8rem;
    color: var(--accent-ink);
    text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--accent) 45%, transparent);
    text-underline-offset: 2px;
    white-space: nowrap;
  }
  div.run .rl {
    color: var(--muted);
    text-decoration: none;
  }
  .answer:not(.replicas) .rl {
    display: none;
  }
  .answer:not(.replicas) .run {
    grid-template-columns: minmax(0, 1fr);
  }
  .track {
    position: relative;
    height: 20px;
    border-radius: 3px;
  }
  .pass {
    position: absolute;
    left: 0;
    top: 3px;
    height: 14px;
    border-radius: 2px 0 0 2px;
    background: var(--c);
    display: flex;
    justify-content: flex-end;
    align-items: center;
    overflow: hidden;
  }
  .pass b {
    padding-right: 4px;
    font-size: 0.7rem;
    font-weight: 700;
    line-height: 1;
    color: #fff;
    font-variant-numeric: tabular-nums;
  }
  .gap {
    position: absolute;
    top: 3px;
    height: 14px;
    background: repeating-linear-gradient(135deg, var(--axis) 0 2px, transparent 2px 5px);
  }
  .x {
    position: absolute;
    top: 3px;
    transform: translateX(-50%);
    line-height: 0;
  }
  .val {
    position: absolute;
    top: 0;
    margin-left: 12px;
    font-size: 0.8rem;
    font-weight: 400;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    line-height: 20px;
  }
  .note,
  .none {
    font-weight: 400;
    color: var(--muted);
    font-size: 0.78rem;
  }
  .none {
    line-height: 20px;
  }
  .mid {
    position: absolute;
    top: -3px;
    bottom: -3px;
    width: 0;
    border-left: 2px solid var(--ink);
    pointer-events: none;
  }
  .figs {
    display: contents;
  }
  .figs dt {
    display: none;
  }
  .figs dd {
    margin: 0;
    font-variant-numeric: tabular-nums;
  }
  .figs strong {
    font-size: 1.15rem;
    font-weight: 650;
  }
  .out dd {
    display: flex;
    flex-direction: column;
    line-height: 1.3;
  }
  .at {
    font-size: 0.8rem;
    color: var(--ink-2);
  }
  .axis {
    padding-top: 4px;
  }
  .ticks {
    position: relative;
    height: 34px;
  }
  .t {
    position: absolute;
    top: 0;
    transform: translateX(-50%);
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .title {
    position: absolute;
    left: var(--rl);
    bottom: 0;
    font-size: 12px;
    color: var(--muted);
  }
  .key {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    margin: 6px 0 0;
    font-size: 0.82rem;
    color: var(--ink-2);
    max-width: none;
  }
  .key span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .sw {
    display: inline-block;
    width: 16px;
    height: 10px;
    border-radius: 2px;
  }
  .gap-sw {
    background: repeating-linear-gradient(135deg, var(--axis) 0 2px, transparent 2px 5px);
    border: 1px solid var(--axis);
  }
  .mid-sw {
    width: 2px;
    height: 14px;
    background: var(--ink);
    border-radius: 0;
  }
  .rkey {
    display: none;
  }

  /* Narrower: the figures move under the bars, one line of labelled numbers. */
  @media (max-width: 760px) {
    .head {
      display: none;
    }
    .spec {
      grid-template-columns: repeat(4, minmax(0, auto));
      justify-content: start;
      row-gap: 6px;
      column-gap: 18px;
    }
    .name,
    .bars {
      grid-column: 1 / -1;
    }
    .figs dt {
      display: block;
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--muted);
    }
    .figs .num {
      text-align: left;
    }
    .figs > div {
      align-self: start;
    }
    .figs strong {
      font-size: 1rem;
    }
    .axis {
      grid-template-columns: minmax(0, 1fr);
    }
    .axis > span:first-child {
      display: none;
    }
  }
  @media (max-width: 480px) {
    .answer.replicas {
      --rl: 28px;
    }
    .note {
      display: none;
    }
    .rl {
      font-size: 0;
    }
    .rl::before {
      content: 'r' attr(data-r);
      font-size: 0.8rem;
    }
    .rkey {
      display: inline-flex;
    }
  }
</style>
