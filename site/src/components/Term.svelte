<script module lang="ts">
  // One definition shows at a time: opening one closes the last.
  let current: (() => void) | null = null;
  let seq = 0;
</script>

<script lang="ts">
  // A word with its definition: dotted underline, and one or two plain sentences on hover, keyboard focus or tap.
  // The definition stays in the page, hidden, so a screen reader reads it with the word (aria-describedby). It is
  // moved to the end of the body and positioned fixed, so it shows whole even inside a box that scrolls, clips or
  // fades (the What we tested table on a phone) and never under the sticky header or a sticky table cell. Hover
  // shows it until the pointer leaves; a tap or click pins it until a second one, a click elsewhere or Escape.
  import { tick, type Snippet } from 'svelte';

  let { text, children }: { text: string; children: Snippet } = $props();

  const id = `term-${++seq}`;
  let open = $state(false);
  let pinned = false;
  let word: HTMLElement | undefined = $state();
  let tip: HTMLElement | undefined = $state();
  let x = $state(0);
  let y = $state(0);
  let timer: ReturnType<typeof setTimeout> | undefined;

  /** Moves the definition to the end of the body, out of any box that would clip, fade or paint over it. */
  function portal(node: HTMLElement) {
    document.body.appendChild(node);
    return { destroy: () => node.remove() };
  }

  function show() {
    clearTimeout(timer);
    if (open) return;
    if (current && current !== hide) current();
    current = hide;
    open = true;
    tick().then(place);
  }

  function hide() {
    clearTimeout(timer);
    open = false;
    pinned = false;
    if (current === hide) current = null;
  }

  function leave(e: PointerEvent) {
    if (e.pointerType !== 'mouse' || pinned) return;
    clearTimeout(timer);
    timer = setTimeout(hide, 150);
  }

  /**
   * Below the word, or above it when there's no room below and there is above; never past the window's edges, and
   * never over the site's sticky header (the top of the page's usable area is under it).
   */
  function place() {
    if (!word || !tip) return;
    const r = word.getBoundingClientRect();
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    const vw = document.documentElement.clientWidth;
    const top = (document.querySelector('body > #app > header, header.site')?.getBoundingClientRect().bottom ?? 0) + 8;
    x = Math.max(8, Math.min(r.left, vw - w - 8));
    const below = r.bottom + 6;
    const above = r.top - 6 - h;
    y = below + h <= window.innerHeight - 8 || above < top ? below : above;
  }

  function click(e: MouseEvent) {
    if (open && (pinned || e.detail === 0)) return hide();
    show();
    pinned = true;
  }

  $effect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => e.key === 'Escape' && hide();
    const down = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!word?.contains(t) && !tip?.contains(t)) hide();
    };
    document.addEventListener('keydown', key);
    document.addEventListener('pointerdown', down);
    window.addEventListener('scroll', place, { capture: true, passive: true });
    window.addEventListener('resize', place);
    return () => {
      document.removeEventListener('keydown', key);
      document.removeEventListener('pointerdown', down);
      window.removeEventListener('scroll', place, { capture: true });
      window.removeEventListener('resize', place);
    };
  });

  $effect(() => () => {
    clearTimeout(timer);
    if (current === hide) current = null;
  });
</script>

<span class="term-wrap">
  <button
    bind:this={word}
    type="button"
    class="term"
    class:open
    aria-describedby={id}
    aria-expanded={open}
    onpointerenter={(e) => e.pointerType === 'mouse' && show()}
    onpointerleave={leave}
    onfocus={() => word?.matches(':focus-visible') && show()}
    onblur={hide}
    onclick={click}>{@render children()}</button
  >
  <span
    bind:this={tip}
    use:portal
    {id}
    role="tooltip"
    class="tip"
    hidden={!open}
    style:left="{x}px"
    style:top="{y}px"
    onpointerenter={() => clearTimeout(timer)}
    onpointerleave={leave}>{text}</span
  >
</span>

<style>
  .term-wrap {
    display: inline;
  }
  /* The dotted underline is the invitation: a shade darker than muted text, and the accent while the definition shows. */
  .term {
    all: unset;
    cursor: help;
    text-decoration: underline dotted;
    text-decoration-color: var(--ink-2);
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
    text-decoration-skip-ink: none;
  }
  .term:hover,
  .term.open {
    text-decoration-color: var(--accent);
    color: var(--accent-ink);
  }
  .term:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: 2px;
  }
  .tip {
    position: fixed;
    z-index: 30;
    max-width: min(280px, 100vw - 16px);
    padding: 8px 10px;
    border: 1px solid var(--rule);
    border-radius: 8px;
    background: var(--bg);
    box-shadow: 0 4px 16px rgb(0 0 0 / 0.1);
    color: var(--ink);
    font-size: 0.82rem;
    font-weight: 400;
    line-height: 1.4;
    text-align: left;
    white-space: normal;
  }
</style>
