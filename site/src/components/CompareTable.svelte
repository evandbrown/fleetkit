<script lang="ts">
  // How several specs performed. First the comparison in one line of numbers (D60): each spec's midpoint per host
  // vCPU, highest first, with its distance from the highest. Then one row per spec, in the chart's order: the highest
  // density that met every SLO, that density per host vCPU, the cost per 1,000 tasks and what ran out. On a phone
  // each row becomes a card, so no figure is dropped.
  import * as f from '../lib/format';
  import { comparisonLine, type CompareRow } from '../lib/shape';

  let { rows, campaigns = false }: { rows: CompareRow[]; campaigns?: boolean } = $props();
  const two = (v: number) => f.num(v, 2);
  const line = $derived(comparisonLine(rows));
</script>

{#if rows.length > 1}
  <p class="line" aria-label="Midpoint per host vCPU, highest first">
    <span class="lk">Midpoint per vCPU</span>
    {#each line as r (r.key)}
      <span class="item">
        <span class="l">{r.label}</span>
        {#if r.midpoint !== null}
          <strong>{two(r.midpoint)}</strong>{#if r.delta !== null}<span class="d">{r.delta < 0 ? '−' : '+'}{two(Math.abs(r.delta))}</span>{/if}
          {#if r.partial}<span class="p">{r.partial}</span>{/if}
        {:else}
          <span class="na">{r.missing}</span>
        {/if}
      </span>
    {/each}
  </p>
{/if}

<div class="table-wrap">
  <table class="compare">
    <thead>
      <tr>
        <th>Spec</th>
        <th class="num">Max density</th>
        <th class="num">Per vCPU</th>
        <th class="num">$ / 1k tasks</th>
        <th>Ran out</th>
      </tr>
    </thead>
    <tbody>
      {#each rows as r, i (r.key)}
        {#if campaigns && (i === 0 || rows[i - 1].campaign !== r.campaign)}
          <tr class="group"><th colspan="5" scope="colgroup">{r.campaignTitle}</th></tr>
        {/if}
        <tr>
          <th scope="row" class="spec">{#if r.href}<a href={r.href}>{r.label}</a>{:else}{r.label}{/if}</th>
          <td class="num" data-label="Max density"><strong>{r.density}</strong></td>
          <td class="num" data-label="Per vCPU">{r.perVcpu ?? '–'}</td>
          <td class="num" data-label="$ / 1k tasks">{r.cost ?? '–'}</td>
          <td class="out" data-label="Ran out">{r.ranOut}{#if r.ranOutAt}<span class="at">{r.ranOutAt}</span>{/if}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .line {
    max-width: none;
    margin: 0 0 4px;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 6px 22px;
    padding: 12px 16px;
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--surface);
  }
  .lk {
    width: 100%;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
  }
  .item {
    display: inline-flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0 6px;
  }
  .l {
    font-size: 0.9rem;
    color: var(--ink-2);
  }
  .line strong {
    font-size: 1.35rem;
    font-weight: 650;
    letter-spacing: -0.01em;
    font-variant-numeric: tabular-nums;
  }
  .d {
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--ink-2);
    font-variant-numeric: tabular-nums;
  }
  .p,
  .na {
    font-size: 0.8rem;
    color: var(--muted);
  }
  .compare {
    font-size: 0.95rem;
  }
  th.spec {
    font-weight: 600;
    color: var(--ink);
    white-space: normal;
  }
  th.spec a {
    color: var(--ink);
  }
  td strong {
    font-weight: 650;
    font-size: 1.05rem;
  }
  .out {
    white-space: nowrap;
  }
  .at {
    margin-left: 6px;
    font-size: 0.82rem;
    color: var(--ink-2);
  }
  tr.group th {
    padding-top: 16px;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
  }
  tbody tr.group:hover > th {
    background: none;
  }
  /* A phone: each spec a card of labelled figures. */
  @media (max-width: 600px) {
    .compare,
    .compare tbody,
    .compare tr,
    .compare th,
    .compare td {
      display: block;
      min-width: 0;
    }
    .compare thead {
      display: none;
    }
    .compare tbody tr:not(.group) {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px 16px;
      padding: 12px 0;
      border-bottom: 1px solid var(--rule);
    }
    .compare tbody tr:hover > * {
      background: none;
    }
    .compare th,
    .compare td {
      border: 0;
      padding: 0;
      text-align: left;
      white-space: normal;
    }
    .compare th.spec {
      grid-column: 1 / -1;
      font-size: 1rem;
    }
    .compare td::before {
      content: attr(data-label);
      display: block;
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--muted);
    }
    .at {
      display: block;
      margin-left: 0;
    }
  }
</style>
