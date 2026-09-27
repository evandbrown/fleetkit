<script lang="ts">
  // A screenshot from the dataset: the 320 px thumbnail, linking to the 1,280 px original. Says so plainly when
  // the file isn't in this dataset (the cap-baseline-1 fixture has none).
  import { imgUrl } from '../lib/data';

  let { sha, alt, caption }: { sha: string | null; alt: string; caption: string } = $props();
  let broken = $state(false);
</script>

<figure class="shot">
  {#if sha && !broken}
    <a href={imgUrl(sha, 'f')} target="_blank" rel="noopener">
      <img src={imgUrl(sha, 't')} {alt} width="320" height="200" loading="lazy" onerror={() => (broken = true)} />
    </a>
  {:else}
    <div class="missing" role="img" aria-label="{alt}: {sha ? 'not in this dataset' : 'not recorded'}">
      {sha ? 'Screenshot not in this dataset' : 'No screenshot recorded'}
    </div>
  {/if}
  <figcaption>{caption}</figcaption>
</figure>

<style>
  .shot {
    margin: 0;
    min-width: 0;
  }
  img,
  .missing {
    display: block;
    width: 100%;
    height: auto;
    aspect-ratio: 16 / 10;
    border: 1px solid var(--rule);
    border-radius: 4px;
    background: var(--surface);
  }
  img {
    object-fit: cover;
    object-position: top;
  }
  .missing {
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 8px;
    font-size: 0.8rem;
    color: var(--muted);
  }
  figcaption {
    font-size: 0.8rem;
    color: var(--ink-2);
    margin-top: 4px;
  }
</style>
