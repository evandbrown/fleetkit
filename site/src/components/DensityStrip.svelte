<script lang="ts">
  // One chip per density in the spec: ✓ for each trial that passed, × for each that failed, ○ if not run.
  import type { DensityBrief } from '../lib/types';

  let { briefs, href }: { briefs: DensityBrief[]; href?: (density: number) => string } = $props();

  const times = (glyph: string, n: number) => Array.from({ length: n }, () => glyph).join('');

  function glyphs(b: DensityBrief): string {
    if (b.result === 'not_run') return '○';
    return times('✓', b.passed) + times('×', b.trials - b.passed);
  }
  function title(b: DensityBrief): string {
    if (b.result === 'not_run') return `Density ${b.density}: not run`;
    return `Density ${b.density}: ${b.passed} of ${b.trials} trials passed`;
  }
</script>

<span class="strip">
  {#each briefs as b (b.density)}
    {#if href && b.result !== 'not_run'}
      <a class="chip {b.result}" href={href(b.density)} title={title(b)} aria-label={title(b)}
        ><span class="n">{b.density}</span><span class="g">{glyphs(b)}</span></a
      >
    {:else}
      <span class="chip {b.result}" title={title(b)} role="img" aria-label={title(b)}><span class="n">{b.density}</span><span class="g">{glyphs(b)}</span></span>
    {/if}
  {/each}
</span>

<style>
  .strip {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .chip {
    display: inline-flex;
    gap: 3px;
    align-items: baseline;
    border: 1px solid var(--rule);
    border-radius: 4px;
    padding: 0 5px;
    font-size: 0.82rem;
    text-decoration: none;
    color: var(--ink-2);
    white-space: nowrap;
  }
  .n {
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    color: var(--ink);
  }
  .passed .g {
    color: var(--good);
  }
  .failed .g {
    color: var(--critical);
  }
  .not_run {
    border-style: dashed;
    color: var(--muted);
  }
</style>
