<script lang="ts">
  // A glossary word. Its definition shows on hover and on keyboard focus (and on tap, which focuses it), and
  // Escape dismisses it. The definition is also the element's accessible description.
  import { tick } from 'svelte';
  import { TERMS } from '../lib/glossary';
  import type { TermKey } from '../lib/types';

  let { k, text }: { k: TermKey; text?: string } = $props();
  const id = $props.id();

  let open = $state(false);
  let anchor: HTMLElement | undefined = $state();
  let tip: HTMLElement | undefined = $state();
  let pos = $state({ left: 0, top: 0 });

  async function show() {
    open = true;
    await tick();
    if (!anchor || !tip) return;
    const a = anchor.getBoundingClientRect();
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    const vw = document.documentElement.clientWidth;
    const left = Math.max(8, Math.min(a.left, vw - w - 8));
    const below = a.bottom + 6;
    const top = below + h > window.innerHeight - 8 && a.top - h - 6 > 8 ? a.top - h - 6 : below;
    pos = { left, top };
  }
  function hide() {
    open = false;
  }
  function key(e: KeyboardEvent) {
    if (e.key === 'Escape' && open) {
      e.stopPropagation();
      hide();
    }
  }
</script>

<svelte:window onscroll={open ? hide : undefined} />

<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_static_element_interactions -- the pointer handlers only mirror focus, which opens the definition from the keyboard -->
<span
  class="term"
  tabindex="0"
  aria-describedby={id}
  bind:this={anchor}
  onmouseenter={show}
  onmouseleave={hide}
  onfocus={show}
  onblur={hide}
  onkeydown={key}
  >{text ?? TERMS[k].label}<span
    class="tip"
    class:open
    role="tooltip"
    {id}
    bind:this={tip}
    style:left="{pos.left}px"
    style:top="{pos.top}px"><strong>{TERMS[k].label}</strong>: {TERMS[k].text}</span
  ></span
>

<style>
  .term {
    text-decoration: underline dotted;
    text-decoration-color: var(--muted);
    text-underline-offset: 3px;
    cursor: help;
    border-radius: 2px;
  }
  .term:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .tip {
    display: none;
    position: fixed;
    z-index: 20;
    width: max-content;
    max-width: min(20rem, calc(100vw - 16px));
    padding: 8px 10px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--bg);
    color: var(--ink);
    box-shadow: 0 2px 10px rgb(0 0 0 / 0.12);
    font-size: 0.85rem;
    font-weight: 400;
    font-style: normal;
    line-height: 1.45;
    text-align: left;
    white-space: normal;
    pointer-events: none;
  }
  .tip.open {
    display: block;
  }
</style>
