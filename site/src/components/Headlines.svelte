<script lang="ts">
  // How one spec (or one run) performed, in four figures: the highest density that met every SLO, that density per
  // host vCPU, the cost per 1,000 tasks there, and what ran out at the first density that failed (a link to it, when
  // there's one run to open).
  import type { Headline } from '../lib/shape';

  let { h, ranOutHref = null }: { h: Headline; ranOutHref?: string | null } = $props();
</script>

<dl class="stats">
  <div class="stat">
    <dt>Max density</dt>
    <dd><span class="v">{h.density}</span>{#if h.densityNote}<span class="n">{h.densityNote}</span>{/if}</dd>
  </div>
  <div class="stat">
    <dt>Per vCPU</dt>
    <dd><span class="v">{h.perVcpu ?? '–'}</span></dd>
  </div>
  <!-- Each cost says what it counts, in About's words: the steady-state cost (D81) is what a full fleet pays, with
       what one burst is charged under it. Where the host wasn't full at the result there is no fleet figure, so the
       burst stands alone and says why. -->
  <div class="stat">
    <dt>$ / 1k tasks</dt>
    <dd>
      {#if h.cost}
        <span class="v small">{h.cost} <span class="what">full fleet</span></span><span class="n">{h.burst} one burst</span>
      {:else if h.burst}
        <span class="v small">{h.burst} <span class="what">one burst</span></span><span class="n">host not full, no fleet figure</span>
      {:else}
        <span class="v small">–</span>
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
    /* So "full fleet" can drop under the figure on a phone. */
    white-space: normal;
  }
  /* What a cost figure counts, muted beside it. */
  .what {
    font-size: 0.82rem;
    font-weight: 400;
    letter-spacing: 0;
    color: var(--ink-2);
    white-space: nowrap;
  }
  .n {
    font-size: 0.82rem;
    color: var(--ink-2);
  }
</style>
