<script lang="ts">
  // How one spec (or one run) performed, in four figures, each saying what it counts: browsers per host that met
  // every SLO, browsers per vCPU, the cost per 1,000 tasks there as one number (D102, its ranges in a tooltip; the
  // ≈ is base.css's .approx), and what ran out at the first count that failed (a link to it, when there's one run
  // to open).
  import type { Headline } from '../lib/shape';
  import Term from './Term.svelte';

  let { h, ranOutHref = null }: { h: Headline; ranOutHref?: string | null } = $props();

  const COST_TERM = 'Steady state: what a full fleet pays per 1,000 tasks, boot included. Hover a figure for its range and the one burst it was measured from.';
  /** "1.50" → "1.5", "0.38–0.50" → "0.38–0.5": a ratio without its trailing zeros. */
  const trim = (s: string) => s.replace(/(\.\d*?)0+(?=$|–)/g, '$1').replace(/\.(?=$|–)/g, '');
</script>

<dl class="stats">
  <div class="stat">
    <dt>Browsers per host</dt>
    <dd><span class="v">{h.count ?? h.density}</span>{#if h.densityNote}<span class="n">{h.densityNote}</span>{/if}</dd>
  </div>
  <div class="stat">
    <dt>Browsers per vCPU</dt>
    <dd><span class="v">{h.perVcpu === null ? '–' : trim(h.perVcpu)}</span></dd>
  </div>
  <div class="stat">
    <dt><Term text={COST_TERM}>$ / 1k tasks</Term></dt>
    <dd title={h.costNote}>
      {#if h.cost}
        <span class="v small"><span class="approx">≈</span>{' '}{h.cost}</span>
      {:else if h.burst}
        <span class="v small burst"><Term text={h.costNote ?? ''}><span class="approx">≈</span>{' '}{h.burst}</Term></span>
      {:else}
        <span class="v small none">—</span>
      {/if}
    </dd>
  </div>
  <div class="stat">
    <dt>Ran out</dt>
    <dd>
      {#if ranOutHref}
        <a class="out" href={ranOutHref}><span class="v small">{h.ranOut}</span>{#if h.ranOutNote}<span class="n">{h.ranOutNote}</span>{/if}</a>
      {:else}
        <span class="v small">{h.ranOut}</span>{#if h.ranOutNote}<span class="n">{h.ranOutNote}</span>{/if}
      {/if}
    </dd>
  </div>
</dl>

<style>
  .stats {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px 20px;
    padding: 14px 18px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
  }
  @media (max-width: 640px) {
    .stats {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  dt {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
  }
  dd {
    margin: 0;
    display: flex;
    flex-direction: column;
  }
  .out {
    display: flex;
    flex-direction: column;
    color: var(--ink);
  }
  .out:hover .v {
    color: var(--accent-ink);
  }
  .out .n {
    color: var(--accent-ink);
  }
  .v {
    font-size: 2.2rem;
    font-weight: 650;
    line-height: 1.1;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .v.small {
    font-size: clamp(1.1rem, 0.9rem + 1vw, 1.5rem);
    line-height: 1.6;
  }
  /* A burst figure alone: the same glyph, one shade quieter. */
  .v.burst {
    color: var(--muted);
    font-weight: 600;
  }
  .v.none {
    color: var(--muted);
    font-weight: 500;
  }
  .n {
    font-size: 0.82rem;
    color: var(--ink-2);
  }
</style>
