<script lang="ts">
  // Extra Chromium flags in full, one to a line, each able to break after its = and after each comma in its value
  // ("--disable-features=A,B,C"), so a long flag wraps at its list rather than mid-name, and never widens the page.
  // None is "none".
  let { flags }: { flags: string[] } = $props();
  /** A flag's name with its =, then the items of its value. */
  const parts = (flag: string) => {
    const eq = flag.indexOf('=');
    return eq < 0 ? { name: flag, items: [] } : { name: flag.slice(0, eq + 1), items: flag.slice(eq + 1).split(',') };
  };
</script>

{#if flags.length}
  <span class="flags">
    {#each flags as flag (flag)}
      {@const p = parts(flag)}
      <code>{p.name}{#each p.items as item, i (i)}{#if i},{/if}<wbr />{item}{/each}</code>
    {/each}
  </span>
{:else}none{/if}

<style>
  .flags {
    display: inline-flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    max-width: 100%;
  }
  code {
    font-size: 0.82em;
    overflow-wrap: anywhere;
  }
</style>
