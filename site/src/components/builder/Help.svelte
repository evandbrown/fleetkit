<script module lang="ts">
  // One explanation shows at a time: opening one closes the last.
  let current: (() => void) | null = null;
</script>

<script lang="ts">
  // An ⓘ beside a field's label (D72) that shows one or two plain sentences on hover (of the ⓘ or, through peek, the
  // label itself), on keyboard focus and on tap.
  // Hover shows it until the pointer leaves (it can cross onto the text); a click or tap pins it until a second one,
  // a click elsewhere or Escape. The text stays in the page, hidden, so the field can name it with aria-describedby and a
  // screen reader reads it with the field.
  import { tick } from 'svelte';

  let { id, label, text }: { id: string; label: string; text: string } = $props();

  let open = $state(false);
  let pinned = false;
  let btn: HTMLButtonElement | undefined = $state();
  let tip: HTMLElement | undefined = $state();
  let x = $state(0);
  let y = $state(0);
  let timer: ReturnType<typeof setTimeout> | undefined;

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

  /** A short grace period, so the pointer can cross from the ⓘ onto the text. */
  function leave(e: PointerEvent) {
    if (e.pointerType !== 'mouse' || pinned) return;
    clearTimeout(timer);
    timer = setTimeout(hide, 150);
  }

  /** Below the ⓘ, or above it when there's no room below; never past the window's edges. */
  function place() {
    if (!btn || !tip) return;
    const r = btn.getBoundingClientRect();
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    const vw = document.documentElement.clientWidth;
    x = Math.max(8, Math.min(r.left - 10, vw - w - 8));
    y = r.bottom + 6 + h <= window.innerHeight - 8 || r.top - 6 - h < 8 ? r.bottom + 6 : r.top - 6 - h;
  }

  /** The field's label shows the same explanation on hover, as the ⓘ does. */
  export function peek(e: PointerEvent) {
    if (e.pointerType === 'mouse') show();
  }
  export function unpeek(e: PointerEvent) {
    leave(e);
  }

  /** A click pins what hover showed, and a second click hides it; Enter or Space toggles what focus showed. */
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
      if (!btn?.contains(t) && !tip?.contains(t)) hide();
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

<span class="help">
  <button
    bind:this={btn}
    type="button"
    class="i"
    class:open
    aria-label="About {label}"
    aria-describedby={id}
    aria-expanded={open}
    onpointerenter={(e) => e.pointerType === 'mouse' && show()}
    onpointerleave={leave}
    onfocus={() => btn?.matches(':focus-visible') && show()}
    onblur={hide}
    onclick={click}
  >
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <circle cx="8" cy="8" r="6.9" fill="none" stroke="currentColor" stroke-width="1.3" />
      <circle cx="8" cy="4.9" r="1" fill="currentColor" />
      <path d="M8 7.3v4.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
    </svg>
  </button>
  <span
    bind:this={tip}
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
  .help {
    display: inline-flex;
    vertical-align: middle;
    margin-left: 3px;
  }
  /* :where() in BuilderEditor gives every builder button a border and padding; the ⓘ has neither. */
  .i {
    display: inline-grid;
    place-items: center;
    width: 20px;
    height: 20px;
    margin: -3px 0;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: none;
    color: var(--muted);
    cursor: help;
    line-height: 0;
  }
  .i:hover,
  .i.open {
    color: var(--accent);
    background: var(--highlight);
  }
  .i:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .tip {
    position: fixed;
    z-index: 40;
    width: max-content;
    max-width: min(19rem, calc(100vw - 16px));
    padding: 8px 11px;
    border-radius: var(--radius);
    background: var(--ink);
    color: var(--bg);
    font: 400 0.84rem/1.45 var(--font);
    letter-spacing: normal;
    text-align: left;
    white-space: normal;
    box-shadow: 0 4px 16px rgb(27 26 24 / 0.18);
  }
  .tip[hidden] {
    display: none;
  }
  /* A finger needs a bigger target than the glyph. */
  @media (pointer: coarse) {
    .i {
      width: 30px;
      height: 30px;
      margin: -8px -6px -8px -3px;
    }
  }
</style>
