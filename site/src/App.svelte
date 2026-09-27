<script lang="ts">
  import { nav } from './lib/nav.svelte';
  import Header from './components/Header.svelte';
  import Home from './views/Home.svelte';
  import Campaigns from './views/Campaigns.svelte';
  import Campaign from './views/Campaign.svelte';
  import Run from './views/Run.svelte';
  import Trial from './views/Trial.svelte';
  import Builder from './views/Builder.svelte';
  import Method from './views/Method.svelte';
  import About from './views/About.svelte';
  import NotFound from './views/NotFound.svelte';

  const r = $derived(nav.route);

  $effect(() => {
    // A new page starts at the top, except the method page, which scrolls to its anchor itself.
    if (r.name !== 'method') window.scrollTo(0, 0);
  });
</script>

<Header route={r} />

<main class="page">
  {#if r.name === 'home'}
    <Home />
  {:else if r.name === 'campaigns'}
    <Campaigns />
  {:else if r.name === 'campaign'}
    {#key r.campaign}<Campaign id={r.campaign} />{/key}
  {:else if r.name === 'run'}
    {#key `${r.campaign}/${r.run}`}<Run campaign={r.campaign} run={r.run} density={r.density} />{/key}
  {:else if r.name === 'trial'}
    {#key `${r.campaign}/${r.run}/${r.trial}`}
      <Trial campaign={r.campaign} run={r.run} trial={r.trial} microvm={r.microvm} />
    {/key}
  {:else if r.name === 'builder'}
    <Builder />
  {:else if r.name === 'method'}
    <Method anchor={r.anchor} />
  {:else if r.name === 'about'}
    <About />
  {:else}
    <NotFound path={r.path} />
  {/if}
</main>

<footer class="page">
  <p class="muted">
    Plain numbers are measured. Costs are modelled from measured times at an assumed price. Verdicts on what limited a
    trial come from rules fixed in the spec before the run.
  </p>
</footer>

<style>
  footer {
    border-top: 1px solid var(--rule);
    padding-top: 12px;
    font-size: 0.85rem;
  }
</style>
