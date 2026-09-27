<script lang="ts">
  import { area, type Route } from '../lib/router';
  import { applyTheme, NEXT_THEME, storedTheme, type Theme } from '../lib/theme';

  let { route }: { route: Route } = $props();
  const current = $derived(area(route));

  let theme: Theme = $state(storedTheme());
  const THEME_LABEL: Record<Theme, string> = { auto: 'Theme: system', light: 'Theme: light', dark: 'Theme: dark' };

  function cycle() {
    theme = NEXT_THEME[theme];
    applyTheme(theme);
  }

  const LINKS = [
    { area: 'campaigns', href: '#/campaigns', label: 'Campaigns' },
    { area: 'builder', href: '#/builder', label: 'Builder' },
    { area: 'method', href: '#/method', label: 'Method' },
    { area: 'about', href: '#/about', label: 'About' },
  ] as const;
</script>

<header>
  <div class="bar">
    <a class="brand" href="#/">Fleetkit</a>
    <nav aria-label="Main">
      {#each LINKS as l (l.area)}
        <a href={l.href} aria-current={current === l.area ? 'page' : undefined}>{l.label}</a>
      {/each}
    </nav>
    <button type="button" class="theme" onclick={cycle} title="Switch theme">{THEME_LABEL[theme]}</button>
  </div>
</header>

<style>
  header {
    border-bottom: 1px solid var(--rule);
    background: var(--bg);
  }
  .bar {
    max-width: var(--page);
    margin: 0 auto;
    padding: 10px var(--gutter);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 20px;
  }
  .brand {
    font-weight: 700;
    color: var(--ink);
    text-decoration: none;
    font-size: 1.05rem;
  }
  nav {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
  }
  /* On a phone: the name and theme on one line, the areas on the next. */
  @media (max-width: 560px) {
    nav {
      order: 3;
      flex-basis: 100%;
    }
  }
  nav a {
    color: var(--ink-2);
    text-decoration: none;
    padding: 4px 0;
    border-bottom: 2px solid transparent;
  }
  nav a[aria-current='page'] {
    color: var(--ink);
    border-bottom-color: var(--accent);
  }
  .theme {
    margin-left: auto;
    font: inherit;
    font-size: 0.85rem;
    color: var(--ink-2);
    background: none;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    padding: 4px 10px;
    cursor: pointer;
  }
</style>
