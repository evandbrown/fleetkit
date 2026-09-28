<script lang="ts">
  import { nav } from './lib/nav.svelte';
  import Header from './components/Header.svelte';
  import Home from './views/Home.svelte';
  import Campaign from './views/Campaign.svelte';
  import Run from './views/Run.svelte';
  import Trial from './views/Trial.svelte';
  import Compare from './views/Compare.svelte';
  import Builder from './views/Builder.svelte';
  import About from './views/About.svelte';
  import NotFound from './views/NotFound.svelte';

  const r = $derived(nav.route);

  let last: string | null = null;
  $effect(() => {
    // A new page starts at the top, except the compare page while specs are picked (each pick changes its URL).
    const name = r.name;
    if (!(name === 'compare' && last === 'compare')) window.scrollTo(0, 0);
    last = name;
  });
</script>

<Header route={r} />

<main class="page">
  {#if r.name === 'results'}
    {#if r.campaign === null}
      <Home />
    {:else}
      {#key r.campaign}<Campaign id={r.campaign} />{/key}
    {/if}
  {:else if r.name === 'run'}
    {#key `${r.campaign}/${r.run}`}<Run campaign={r.campaign} run={r.run} density={r.density} />{/key}
  {:else if r.name === 'trial'}
    {#key `${r.campaign}/${r.run}/${r.trial}`}
      <Trial campaign={r.campaign} run={r.run} trial={r.trial} microvm={r.microvm} />
    {/key}
  {:else if r.name === 'compare'}
    <Compare specs={r.specs} />
  {:else if r.name === 'builder'}
    <!-- The builder reads its route (from) from nav.route itself: DATA.md, "Builder handoff". -->
    <Builder />
  {:else if r.name === 'about'}
    <About />
  {:else}
    <NotFound path={r.path} />
  {/if}
</main>
