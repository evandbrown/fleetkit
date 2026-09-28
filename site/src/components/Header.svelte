<script lang="ts">
  import { area, href, type Route } from '../lib/router';

  let { route }: { route: Route } = $props();
  const current = $derived(area(route));

  const LINKS = [
    { area: 'results', href: href({ name: 'results', campaign: null }), label: 'Results' },
    { area: 'builder', href: href({ name: 'builder', from: null }), label: 'Builder' },
    { area: 'about', href: href({ name: 'about' }), label: 'About' },
  ] as const;
</script>

<header>
  <div class="bar">
    <a class="brand" href="#/"><span class="mark" aria-hidden="true"></span>Fleetkit</a>
    <nav aria-label="Main">
      {#each LINKS as l (l.area)}
        <a href={l.href} aria-current={current === l.area ? 'page' : undefined}>{l.label}</a>
      {/each}
    </nav>
  </div>
</header>

<style>
  header {
    position: sticky;
    top: 0;
    z-index: 10;
    background: rgb(255 255 255 / 0.92);
    backdrop-filter: saturate(1.4) blur(8px);
    border-bottom: 1px solid var(--rule);
  }
  .bar {
    max-width: var(--page);
    margin: 0 auto;
    padding: 0 var(--gutter);
    height: 56px;
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .brand {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-weight: 650;
    letter-spacing: -0.01em;
    color: var(--ink);
    text-decoration: none;
    font-size: 1.05rem;
  }
  .mark {
    width: 14px;
    height: 14px;
    border-radius: 4px;
    background: var(--accent);
    box-shadow: 5px 5px 0 -1px var(--highlight);
  }
  nav {
    margin-left: auto;
    display: flex;
    gap: 4px;
  }
  nav a {
    color: var(--ink-2);
    text-decoration: none;
    font-size: 0.95rem;
    padding: 6px 12px;
    border-radius: 999px;
  }
  nav a:hover {
    color: var(--ink);
    background: var(--surface);
  }
  nav a[aria-current='page'] {
    color: var(--accent-ink);
    background: var(--highlight);
    font-weight: 600;
  }
  @media (max-width: 420px) {
    nav a {
      padding: 6px 9px;
    }
  }
</style>
