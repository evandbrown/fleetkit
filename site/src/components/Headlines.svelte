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
  <div class="stat">
    <dt>$ / 1k tasks</dt>
    <dd><span class="v small">{h.cost ?? '–'}</span></dd>
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
    border-radius: 10px;
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
  .n {
    font-size: 0.82rem;
    color: var(--ink-2);
  }
</style>
